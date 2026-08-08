"""Tests for the bridge-emit / bridge-learn seam on BaseDriver.

Covers the "downstream device routes its command through a live bridge
instance" path that the IR bridge uses (and that the serial bridge's
prepare_bridge_port already established). Invented devices only (core-test
rule): a fake emitting bridge and a fake IR device, no real hardware or vendor
protocol.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _FakeEmittingBridge(BaseDriver):
    """A bridge that advertises an IR port and records what it's asked to emit."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "fake_ir_bridge",
        "name": "Fake IR Bridge",
        "category": "utility",
        "transport": "tcp",
        "bridge": {"ports": [{"id": "ir:1", "kind": "ir", "label": "IR 1"}]},
        "state_variables": {},
        "commands": {},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.emitted: list[tuple[str, str, dict]] = []

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def bridge_emit(self, port_id: str, kind: str, payload: dict) -> Any:
        self.emitted.append((port_id, kind, payload))
        return {"status": "ok"}


class _FakeIRDevice(BaseDriver):
    """An IR device: no transport of its own, emits via the bridge."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "fake_ir_device",
        "name": "Fake IR Device",
        "category": "display",
        "transport": "bridge",
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return await self.emit_via_bridge("ir", {"pronto": command, "repeat": 1})


def _mk(cls, config):
    return cls("dev", config, StateStore(), EventBus())


# --- bridge-routed connect: no socket; liveness mirrors the bound bridge ---


def test_bridge_routed_connect_opens_no_socket_and_mirrors_bridge_state():
    # Bridge offline at connect time -> the device comes up offline (no socket,
    # but flagged bridge-routed so the manager can flip it later).
    dev = _mk(_FakeIRDevice, {"transport": "bridge", "bridge": "b", "bridge_port": "ir:1"})
    asyncio.run(dev.connect())
    assert dev._bridge_routed is True
    assert dev.transport is None
    assert dev._connected is False
    assert dev.state.get("device.dev.connected") is False

    # Bridge already online -> the device seeds online from the bridge's state.
    dev2 = _mk(_FakeIRDevice, {"transport": "bridge", "bridge": "b", "bridge_port": "ir:1"})
    dev2.state.set("device.b.connected", True)
    asyncio.run(dev2.connect())
    assert dev2._connected is True
    assert dev2.transport is None
    assert dev2.state.get("device.dev.connected") is True
    # The connected property tolerates the no-transport case for a bridge device.
    assert dev2.connected is True


# --- emit_via_bridge routing ---


def test_emit_requires_a_binding():
    dev = _mk(_FakeIRDevice, {"transport": "bridge"})
    with pytest.raises(ConnectionError, match="not bound"):
        asyncio.run(dev.send_command("PWR"))


def test_emit_requires_a_router():
    dev = _mk(_FakeIRDevice, {"bridge": "b", "bridge_port": "ir:1"})
    # bound but no router injected (device manager didn't wire it)
    with pytest.raises(ConnectionError, match="routing unavailable"):
        asyncio.run(dev.send_command("PWR"))


def test_emit_forwards_to_router_with_binding_and_payload():
    dev = _mk(_FakeIRDevice, {"bridge": "itach", "bridge_port": "ir:2"})
    calls: list[tuple] = []

    async def router(bridge_id, port_id, kind, payload):
        calls.append((bridge_id, port_id, kind, payload))
        return {"status": "ok"}

    dev._bridge_router = router
    asyncio.run(dev.send_command("0000 006D ..."))
    assert calls == [("itach", "ir:2", "ir", {"pronto": "0000 006D ...", "repeat": 1})]


# --- capability defaults ---


def test_non_emitting_bridge_raises_and_cannot_learn():
    dev = _mk(_FakeIRDevice, {})  # not a bridge, no override
    with pytest.raises(NotImplementedError):
        asyncio.run(dev.bridge_emit("ir:1", "ir", {}))
    assert dev.can_learn is False
    with pytest.raises(NotImplementedError):
        asyncio.run(dev.bridge_learn_start())


def test_emitting_bridge_records_payload_and_is_a_bridge():
    bridge = _mk(_FakeEmittingBridge, {"host": "192.0.2.9"})
    assert bridge.is_bridge is True
    result = asyncio.run(bridge.bridge_emit("ir:1", "ir", {"pronto": "X", "repeat": 3}))
    assert result == {"status": "ok"}
    assert bridge.emitted == [("ir:1", "ir", {"pronto": "X", "repeat": 3})]


# --- device-manager router (the injected _bridge_router) ---


def test_device_manager_router_reaches_live_bridge_and_guards():
    from openavc.core.device_manager import DeviceManager

    dm = DeviceManager(StateStore(), EventBus())
    bridge = _mk(_FakeEmittingBridge, {})
    bridge._connected = True
    dm._devices["itach"] = bridge

    res = asyncio.run(
        dm._route_bridge_command("itach", "ir:1", "ir", {"pronto": "X", "repeat": 1})
    )
    assert res == {"status": "ok"}
    assert bridge.emitted[-1] == ("ir:1", "ir", {"pronto": "X", "repeat": 1})

    # Unknown bridge id.
    with pytest.raises(ConnectionError, match="not available"):
        asyncio.run(dm._route_bridge_command("nope", "ir:1", "ir", {}))

    # A non-bridge device is rejected too.
    plain = _mk(_FakeIRDevice, {})
    dm._devices["plain"] = plain
    with pytest.raises(ConnectionError, match="not available"):
        asyncio.run(dm._route_bridge_command("plain", "ir:1", "ir", {}))

    # Offline bridge surfaces as a command failure, not a silent no-op.
    bridge._connected = False
    with pytest.raises(ConnectionError, match="offline"):
        asyncio.run(dm._route_bridge_command("itach", "ir:1", "ir", {}))
