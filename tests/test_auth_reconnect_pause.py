"""Permanent faults stop auto-reconnect instead of retrying for an hour.

A rejected login can't heal by retrying — the same password just fails
again — and devices with brute-force lockouts block the offending source IP
after a handful of failures, locking the legitimate user out too. So an
``auth_failed`` classification must stop the reconnect machinery (one
attempt per user action) instead of feeding it.

The same holds, less urgently, for the other faults only a human can clear:
a rejected host key, an untrusted certificate, invalid connection settings,
or a missing client binary. Those get a couple of attempts (a device that
was mid-reboot can present one briefly) and then stop. Network faults —
unreachable, refused, no response — keep the normal retry loop, because
those do heal on their own.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from server.core.device_manager import (
    _DRIVER_REGISTRY,
    _MAX_PERMANENT_FAULT_ATTEMPTS,
    DeviceManager,
)
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver, ConnectionFaultError


class _AuthRejectDriver(BaseDriver):
    """Connect always fails like a device rejecting the login."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_auth", "name": "Acme Auth Widget", "transport": "tcp",
        "state_variables": {}, "commands": {},
    }

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.connect_attempts = 0

    async def connect(self) -> None:
        self.connect_attempts += 1
        raise ConnectionFaultError("Login rejected", code="auth_failed")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _UnreachableThenAuthDriver(_AuthRejectDriver):
    """Unreachable for the first N attempts, then rejects the login —
    a device that comes back online with changed credentials."""

    unreachable_attempts = 2

    async def connect(self) -> None:
        self.connect_attempts += 1
        if self.connect_attempts <= self.unreachable_attempts:
            raise ConnectionError("connection refused")
        raise ConnectionFaultError("Login rejected", code="auth_failed")


class _UnreachableDriver(_AuthRejectDriver):
    async def connect(self) -> None:
        self.connect_attempts += 1
        raise ConnectionError("connection refused")


class _PermanentFaultDriver(_AuthRejectDriver):
    """Fails every attempt with a fault only a human can clear.

    ``fault`` is set per-subclass/per-test: either a typed code the driver
    declares itself, or a raw message the shared classifier resolves (the
    TLS case, which no driver may declare directly).
    """

    fault: Any = ConnectionFaultError("bad settings", code="invalid_config")

    async def connect(self) -> None:
        self.connect_attempts += 1
        raise self.fault


@pytest.fixture
def dm():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    yield manager


def _register(driver_id: str, cls):
    _DRIVER_REGISTRY[driver_id] = cls


def _unregister(driver_id: str):
    _DRIVER_REGISTRY.pop(driver_id, None)


async def test_add_device_auth_failure_does_not_start_reconnect(dm):
    _register("acme_auth", _AuthRejectDriver)
    try:
        await dm.add_device({
            "id": "d1", "driver": "acme_auth", "name": "D1",
            "config": {"host": "192.0.2.1"},
        })
        driver = dm._devices["d1"]
        assert driver.connect_attempts == 1  # the one try (may carry defaults)
        assert "d1" not in dm._reconnect_tasks  # no retry loop started
        assert dm.state.get("device.d1.offline_reason") == "auth_failed"
        assert dm.state.get("device.d1.reconnect_failed") is True
    finally:
        _unregister("acme_auth")


async def test_non_auth_failure_still_starts_reconnect(dm):
    _register("acme_dead", _UnreachableDriver)
    try:
        await dm.add_device({
            "id": "d1", "driver": "acme_dead", "name": "D1",
            "config": {"host": "192.0.2.1"},
        })
        assert dm.state.get("device.d1.offline_reason") == "connection_refused"
        assert "d1" in dm._reconnect_tasks  # normal failures keep retrying
    finally:
        await dm._cancel_reconnect("d1")
        _unregister("acme_dead")


async def test_reconnect_loop_stops_when_auth_failure_appears(dm):
    """An unreachable device that comes back rejecting the login: the loop
    stops on the attempt that discovers the auth failure instead of burning
    the remaining attempts as failed logins."""
    driver = _UnreachableThenAuthDriver(
        "d1", {"host": "192.0.2.1"}, dm.state, dm.events
    )
    dm._devices["d1"] = driver
    dm._device_configs["d1"] = {"id": "d1", "driver": "acme_auth", "config": {}}

    with patch("server.core.device_manager.asyncio.sleep", new=AsyncMock()):
        await dm._reconnect_loop("d1", max_attempts=10)

    # 2 unreachable attempts + the 1 that discovered the rejection — not 10.
    assert driver.connect_attempts == 3
    assert dm.state.get("device.d1.offline_reason") == "auth_failed"
    assert dm.state.get("device.d1.reconnect_failed") is True


async def test_manual_reconnect_tries_once_then_pauses_again(dm):
    _register("acme_auth", _AuthRejectDriver)
    try:
        await dm.add_device({
            "id": "d1", "driver": "acme_auth", "name": "D1",
            "config": {"host": "192.0.2.1"},
        })
        driver = dm._devices["d1"]
        assert driver.connect_attempts == 1

        # The Reconnect button forces exactly one more attempt, then holds.
        await dm.reconnect_device("d1")
        await asyncio.sleep(0)  # let any (wrongly) spawned loop task start
        assert driver.connect_attempts == 2
        assert "d1" not in dm._reconnect_tasks
        assert dm.state.get("device.d1.reconnect_failed") is True
    finally:
        _unregister("acme_auth")


@pytest.mark.parametrize(
    "fault, expected_code",
    [
        (
            ConnectionFaultError("bad baud rate", code="invalid_config"),
            "invalid_config",
        ),
        (
            ConnectionFaultError("host key changed", code="host_key_rejected"),
            "host_key_rejected",
        ),
        (
            ConnectionFaultError("no ssh on PATH", code="client_missing"),
            "client_missing",
        ),
        # tls_cert_untrusted isn't a code a driver may declare — it comes out
        # of the shared classifier's string matching, so drive it that way.
        (
            ConnectionError("certificate verify failed: self-signed certificate"),
            "tls_cert_untrusted",
        ),
    ],
)
async def test_reconnect_loop_stops_early_on_permanent_fault(
    dm, fault, expected_code
):
    """Retrying can't clear these, so the loop stops after a couple of
    attempts rather than grinding through the full 120."""
    driver = _PermanentFaultDriver("d1", {"host": "192.0.2.1"}, dm.state, dm.events)
    driver.fault = fault
    dm._devices["d1"] = driver
    dm._device_configs["d1"] = {"id": "d1", "driver": "acme_perm", "config": {}}

    with patch("server.core.device_manager.asyncio.sleep", new=AsyncMock()):
        await dm._reconnect_loop("d1", max_attempts=50)

    assert driver.connect_attempts == _MAX_PERMANENT_FAULT_ATTEMPTS
    assert dm.state.get("device.d1.offline_reason") == expected_code
    assert dm.state.get("device.d1.reconnect_failed") is True


async def test_network_faults_still_use_all_attempts(dm):
    """The early stop must not leak into faults that do heal on their own."""
    driver = _UnreachableDriver("d1", {"host": "192.0.2.1"}, dm.state, dm.events)
    dm._devices["d1"] = driver
    dm._device_configs["d1"] = {"id": "d1", "driver": "acme_dead", "config": {}}

    with patch("server.core.device_manager.asyncio.sleep", new=AsyncMock()):
        await dm._reconnect_loop("d1", max_attempts=5)

    assert driver.connect_attempts == 5
    assert dm.state.get("device.d1.offline_reason") == "connection_refused"


async def test_transient_permanent_fault_does_not_stop_a_recovering_device(dm):
    """A device that presents a permanent-looking fault once (mid-reboot) and
    then just needs the network to come back keeps retrying — the early-stop
    counter only fires on consecutive permanent classifications."""

    class _FlipFlopDriver(_AuthRejectDriver):
        async def connect(self) -> None:
            self.connect_attempts += 1
            if self.connect_attempts % 2 == 1:
                raise ConnectionFaultError("bad settings", code="invalid_config")
            raise ConnectionError("connection refused")

    driver = _FlipFlopDriver("d1", {"host": "192.0.2.1"}, dm.state, dm.events)
    dm._devices["d1"] = driver
    dm._device_configs["d1"] = {"id": "d1", "driver": "acme_flip", "config": {}}

    with patch("server.core.device_manager.asyncio.sleep", new=AsyncMock()):
        await dm._reconnect_loop("d1", max_attempts=6)

    assert driver.connect_attempts == 6
