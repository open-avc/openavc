"""The reconnect loop's less travelled exits and turns, pinned by what they do.

Each test drives ``DeviceManager._reconnect_loop`` (or the offline-reason
classification it calls) down one path the broader reconnect suites do not
reach: an attempt that removes its own device, a device asleep on purpose, a
retry a person asked for, a poll that will not stop, a simulation that starts
mid-wait, a held connection refused a login, a drop while queued settings are
applied, and an attempt a newer connect ended. What is asserted is the
outcome: the attempts made, the waits taken, the state published.

An invented device (``acme_widget``) throughout, per the core-tests rule.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

import pytest

from openavc.core import device_sleep
from openavc.core.connection_fault import (
    AUTH_FAILED,
    CONNECTION_REFUSED,
    NO_SIMULATOR,
    ConnectionFault,
)
from openavc.core.device_manager import DeviceManager
from openavc.core.device_sleep import SleepBook
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectEndedError

SLEEP = "openavc.core.device_manager.asyncio.sleep"


class _AcmeDriver(BaseDriver):
    """Connect fails like a refused port unless a test says otherwise."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
        "state_variables": {}, "commands": {},
    }

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.connect_attempts = 0

    async def connect(self) -> None:
        self.connect_attempts += 1
        raise ConnectionError("connection refused")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    def come_up(self) -> None:
        self._connected = True
        self.set_state("connected", True)


@pytest.fixture
def dm():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state, events)


def _place(dm: DeviceManager, cls: type[_AcmeDriver]) -> _AcmeDriver:
    driver = cls("acme1", {"host": "192.0.2.1", "port": 4001}, dm.state, dm.events)
    dm._devices["acme1"] = driver
    dm._device_configs["acme1"] = {
        "id": "acme1", "driver": "acme_widget", "config": {"host": "192.0.2.1"},
    }
    return driver


def _stop_after(driver: _AcmeDriver, attempts: int, waits: list[float] | None = None):
    """A stand-in for the loop's wait: records each delay, and cancels the
    loop at the wait after ``attempts`` connects."""

    async def wait(delay: float) -> None:
        if waits is not None:
            waits.append(delay)
        if driver.connect_attempts >= attempts:
            raise asyncio.CancelledError

    return wait


async def test_an_attempt_that_removes_its_device_ends_the_loop_without_another_wait(dm):
    class _RemovedWhileDialing(_AcmeDriver):
        async def connect(self) -> None:
            self.connect_attempts += 1
            dm._devices.pop("acme1", None)
            raise ConnectionError("connection refused")

    driver = _place(dm, _RemovedWhileDialing)
    waits: list[float] = []
    with patch(SLEEP, side_effect=_stop_after(driver, 99, waits)):
        await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 1
    assert len(waits) == 1  # the wait before the one attempt, none after it
    assert dm.state.get("device.acme1.offline_reason") == CONNECTION_REFUSED
    assert dm.state.get("device.acme1.reconnect_failed") is None


async def test_a_loop_for_a_device_that_is_not_there_does_nothing(dm):
    waits: list[float] = []

    async def wait(delay: float) -> None:
        waits.append(delay)

    with patch(SLEEP, side_effect=wait):
        await dm._reconnect_loop("ghost")

    assert waits == []
    assert dm.state.get("device.ghost.reconnect_attempt") is None
    assert dm.state.get("device.ghost.offline_reason") is None


async def test_a_device_asleep_on_purpose_is_retried_without_a_word(dm, tmp_path, caplog):
    dm._sleep = SleepBook(lambda: tmp_path / device_sleep.RECORD_NAME)
    dm._sleep.arm("acme1", "power_on", "Power On", "acme_widget")
    dm._sleep.mark_asleep("acme1")
    driver = _place(dm, _AcmeDriver)

    with caplog.at_level(logging.INFO, logger="openavc"):
        with patch(SLEEP, side_effect=_stop_after(driver, 4)):
            await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 4
    assert dm.state.get("device.acme1.reconnect_attempt") is None
    assert dm.state.get("device.acme1.asleep") is True
    assert dm.state.get("device.acme1.offline_reason") is None
    said = [r.getMessage() for r in caplog.records]
    assert not [m for m in said if "Reconnect attempt" in m or "Reconnect failed" in m]


async def test_an_awake_device_says_its_first_attempts(dm, caplog):
    driver = _place(dm, _AcmeDriver)

    with caplog.at_level(logging.INFO, logger="openavc"):
        with patch(SLEEP, side_effect=_stop_after(driver, 4)):
            await dm._reconnect_loop("acme1")

    # The first three attempts are said; the fourth goes quietly.
    said = [r.getMessage() for r in caplog.records]
    assert len([m for m in said if "Reconnect attempt" in m]) == 3
    assert len([m for m in said if "Reconnect failed" in m]) == 3
    assert dm.state.get("device.acme1.reconnect_attempt") == 3
    assert driver.connect_attempts == 4


async def test_an_immediate_retry_dials_before_any_wait(dm):
    waits_at_dial: list[int] = []
    waits: list[float] = []

    class _Counting(_AcmeDriver):
        async def connect(self) -> None:
            waits_at_dial.append(len(waits))
            await super().connect()

    driver = _place(dm, _Counting)
    with patch(SLEEP, side_effect=_stop_after(driver, 3, waits)):
        await dm._reconnect_loop("acme1", immediate=True)

    assert waits_at_dial == [0, 1, 2]  # the first dial waited for nothing
    assert dm.state.get("device.acme1.reconnect_attempt") == 3


async def test_a_retry_nobody_asked_for_waits_before_its_first_dial(dm):
    waits_at_dial: list[int] = []
    waits: list[float] = []

    class _Counting(_AcmeDriver):
        async def connect(self) -> None:
            waits_at_dial.append(len(waits))
            await super().connect()

    driver = _place(dm, _Counting)
    with patch(SLEEP, side_effect=_stop_after(driver, 2, waits)):
        await dm._reconnect_loop("acme1")

    assert waits_at_dial == [1, 2]


async def test_a_poll_that_will_not_stop_does_not_stop_the_attempt(dm):
    class _StubbornPoll(_AcmeDriver):
        async def stop_polling(self) -> None:
            raise RuntimeError("poll task wedged")

        async def connect(self) -> None:
            self.connect_attempts += 1
            self.come_up()

    driver = _place(dm, _StubbornPoll)
    dm.state.set("device.acme1.offline_reason", CONNECTION_REFUSED)
    with patch(SLEEP, side_effect=_stop_after(driver, 99)):
        await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 1
    assert dm.state.get("device.acme1.offline_reason") is None
    assert dm.state.get("device.acme1.reconnect_attempt") is None


async def test_a_simulation_that_starts_during_the_wait_ends_the_loop_undialed(dm):
    driver = _place(dm, _AcmeDriver)
    gap: dict[str, str | None] = {"acme1": None}
    dm.unsimulated_driver = lambda device_id: gap.get(device_id)

    async def wait(delay: float) -> None:
        gap["acme1"] = "acme_widget"  # simulation started while it waited

    with patch(SLEEP, side_effect=wait):
        await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 0
    assert dm.state.get("device.acme1.offline_reason") == NO_SIMULATOR
    assert dm.state.get("device.acme1.reconnect_failed") is None


async def test_a_simulation_already_running_ends_the_loop_before_it_waits(dm):
    driver = _place(dm, _AcmeDriver)
    dm.unsimulated_driver = lambda device_id: "acme_widget"
    waits: list[float] = []

    async def wait(delay: float) -> None:
        waits.append(delay)

    with patch(SLEEP, side_effect=wait):
        await dm._reconnect_loop("acme1")

    assert waits == []
    assert driver.connect_attempts == 0
    assert dm.state.get("device.acme1.offline_reason") == NO_SIMULATOR
    assert dm.state.get("device.acme1.reconnect_attempt") is None


class _HeldThenClosed(_AcmeDriver):
    """Connects held (the device stopped answering last time), and the held
    connection closes before the device answers, keeping ``closed_with``."""

    closed_with: ConnectionFault | None = None

    async def connect(self) -> None:
        self.connect_attempts += 1
        self._held = True

    async def _wait_for_answer(self) -> bool:
        self._held = False
        self._last_fault = self.closed_with
        return False


async def test_a_held_connection_refused_a_login_pauses_the_retry(dm):
    class _Refused(_HeldThenClosed):
        closed_with = ConnectionFault(AUTH_FAILED, "Login rejected")

    driver = _place(dm, _Refused)
    driver._dropped_for_silence = True
    with patch(SLEEP, side_effect=_stop_after(driver, 99)):
        await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 1
    assert driver._hold_next_connect is True  # the drop for silence held it
    assert dm.state.get("device.acme1.offline_reason") == AUTH_FAILED
    assert dm.state.get("device.acme1.reconnect_failed") is True


async def test_a_held_connection_closed_for_another_reason_tries_again(dm):
    class _Refused(_HeldThenClosed):
        closed_with = ConnectionFault(CONNECTION_REFUSED, "Refused")

    driver = _place(dm, _Refused)
    with patch(SLEEP, side_effect=_stop_after(driver, 3)):
        await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 3
    assert driver._hold_next_connect is False  # never dropped for silence
    assert dm.state.get("device.acme1.offline_reason") == CONNECTION_REFUSED
    assert dm.state.get("device.acme1.reconnect_failed") is None


async def test_a_drop_while_queued_settings_are_applied_tries_again(dm, monkeypatch):
    class _UpThenDropped(_AcmeDriver):
        async def connect(self) -> None:
            self.connect_attempts += 1
            self.come_up()

    driver = _place(dm, _UpThenDropped)

    async def dropped_while_applying(device_id: str) -> None:
        driver._connected = False
        driver.set_state("connected", False)
        driver._stash_fault(CONNECTION_REFUSED, "Refused")

    monkeypatch.setattr(dm, "_apply_pending_settings", dropped_while_applying)
    with patch(SLEEP, side_effect=_stop_after(driver, 3)):
        await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 3
    assert dm.state.get("device.acme1.offline_reason") == CONNECTION_REFUSED
    assert dm.state.get("device.acme1.reconnect_failed") is None


async def test_an_attempt_a_newer_connect_ended_leaves_the_device_to_it(dm):
    class _Ended(_AcmeDriver):
        async def connect(self) -> None:
            self.connect_attempts += 1
            raise ConnectEndedError("ended by a newer connect")

    driver = _place(dm, _Ended)
    dm.state.set("device.acme1.offline_reason", CONNECTION_REFUSED)
    with patch(SLEEP, side_effect=_stop_after(driver, 99)):
        await dm._reconnect_loop("acme1")

    assert driver.connect_attempts == 1
    # Left as it was: the connect that ended this one owns the device now.
    assert dm.state.get("device.acme1.offline_reason") == CONNECTION_REFUSED
    assert dm.state.get("device.acme1.reconnect_failed") is None


def test_the_live_transport_error_outranks_the_one_kept_from_teardown(dm):
    class _Link:
        last_error = "Connection refused"

    class _Driver:
        DRIVER_INFO: dict[str, Any] = {"id": "acme_widget", "transport": "tcp"}
        config = {"host": "192.0.2.1", "port": 4001}
        transport = _Link()
        last_transport_error = "operation timed out"
        last_fault = None

    code = dm._set_offline_reason("acme1", _Driver())

    assert code == CONNECTION_REFUSED
    assert dm.state.get("device.acme1.offline_reason") == CONNECTION_REFUSED
