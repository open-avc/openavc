"""Tests for the driver test panel's production-conflict protection (A81).

When the test panel is about to open a competing TCP session against a
host:port that a production device already owns, the platform exposes:

- ``GET /driver-test-conflicts`` — pre-flight check that lists matching
  production devices so the UI can warn the user.
- ``POST /devices/{id}/pause`` and ``/resume`` — cleanly disconnect the
  production driver before the test, then reconnect it after.

These tests cover the DeviceManager methods and the route helpers using
the same in-memory MockDriver pattern used elsewhere.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from server.core.device_manager import DeviceManager, _DRIVER_REGISTRY
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver


class MockTCPDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "mock_a81_tcp",
        "name": "Mock TCP",
        "manufacturer": "Test",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"host": "127.0.0.1", "port": 9999},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.fail_next_connect = False

    async def connect(self):
        self.connect_calls += 1
        if self.fail_next_connect:
            self.fail_next_connect = False
            raise ConnectionError("simulated connect failure")
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self):
        self.disconnect_calls += 1
        self._connected = False
        self.state.set(f"device.{self.device_id}.connected", False, source="driver")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def stop_polling(self):
        return None


_DRIVER_REGISTRY["mock_a81_tcp"] = MockTCPDriver


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def dm(core):
    state, events = core
    return DeviceManager(state, events)


# ---------------------------------------------------------------------------
# pause_device / resume_device
# ---------------------------------------------------------------------------


async def test_pause_device_disconnects_and_sets_paused_flag(dm, core):
    state, _ = core
    driver = MockTCPDriver("dev1", {}, state, events=core[1])
    driver._connected = True
    dm._devices["dev1"] = driver

    await dm.pause_device("dev1")

    assert driver.disconnect_calls == 1
    assert state.get("device.dev1.paused") is True
    assert state.get("device.dev1.connected") is False
    assert "dev1" in dm._intentional_disconnect


async def test_pause_device_suppresses_auto_reconnect(dm, core):
    """Pausing must add the device to _intentional_disconnect BEFORE the
    disconnect, so the disconnected event handler doesn't kick off a
    reconnect_loop the user didn't ask for."""
    _, events = core
    driver = MockTCPDriver("dev1", {}, *core)
    driver._connected = True
    dm._devices["dev1"] = driver

    await dm.pause_device("dev1")
    # Emitting the disconnect event after pause must NOT trigger a reconnect
    # task.
    await events.emit("device.disconnected.dev1", {})
    # Give any spurious reconnect task a chance to start.
    await asyncio.sleep(0)
    assert "dev1" not in dm._reconnect_tasks


async def test_resume_device_reconnects_and_clears_paused_flag(dm, core):
    state, _ = core
    driver = MockTCPDriver("dev1", {}, *core)
    driver._connected = False
    dm._devices["dev1"] = driver
    dm._intentional_disconnect.add("dev1")
    state.set("device.dev1.paused", True, source="device_manager")

    await dm.resume_device("dev1")

    assert driver.connect_calls == 1
    assert state.get("device.dev1.paused") is False
    assert state.get("device.dev1.connected") is True
    assert "dev1" not in dm._intentional_disconnect


async def test_resume_device_falls_back_to_reconnect_loop_on_failure(dm, core):
    """If the immediate reconnect fails, the normal exponential-backoff
    reconnect loop should take over rather than leaving the device stranded."""
    state, _ = core
    driver = MockTCPDriver("dev1", {}, *core)
    driver._connected = False
    driver.fail_next_connect = True
    dm._devices["dev1"] = driver

    # Patch _start_reconnect (sync method, schedules a task) so we can
    # detect it was called without actually spinning up a long-running task.
    dm._start_reconnect = MagicMock()

    await dm.resume_device("dev1")

    assert driver.connect_calls == 1
    assert state.get("device.dev1.connected") is False
    dm._start_reconnect.assert_called_once_with("dev1")


async def test_pause_unknown_device_raises(dm):
    with pytest.raises(ValueError, match="not found"):
        await dm.pause_device("nope")


async def test_resume_unknown_device_raises(dm):
    with pytest.raises(ValueError, match="not found"):
        await dm.resume_device("nope")


# ---------------------------------------------------------------------------
# /driver-test-conflicts endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def conflict_engine():
    """Build a minimal engine stub with two TCP devices for conflict checks."""
    from types import SimpleNamespace

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)

    devices = [
        SimpleNamespace(
            id="proj_main",
            driver="mock_a81_tcp",
            name="Main Projector",
            config={"host": "10.0.0.50", "port": 4352},
            enabled=True,
        ),
        SimpleNamespace(
            id="proj_disabled",
            driver="mock_a81_tcp",
            name="Spare (Disabled)",
            config={"host": "10.0.0.50", "port": 4352},
            enabled=False,
        ),
        SimpleNamespace(
            id="display1",
            driver="mock_a81_tcp",
            name="Hallway Display",
            config={"host": "10.0.0.99", "port": 23},
            enabled=True,
        ),
    ]
    project = SimpleNamespace(
        devices=devices,
        connections={},
    )
    return SimpleNamespace(project=project, state=state, devices=None)


async def test_check_conflict_returns_matching_device(monkeypatch, conflict_engine):
    from server.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: conflict_engine)
    conflict_engine.state.set("device.proj_main.connected", True, source="test")

    result = await drivers_routes.check_connection_conflict(
        host="10.0.0.50", port="4352", transport="tcp"
    )
    assert len(result["conflicts"]) == 1
    c = result["conflicts"][0]
    assert c["device_id"] == "proj_main"
    assert c["device_name"] == "Main Projector"
    assert c["connected"] is True
    assert c["paused"] is False


async def test_check_conflict_skips_disabled_devices(monkeypatch, conflict_engine):
    from server.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: conflict_engine)
    result = await drivers_routes.check_connection_conflict(
        host="10.0.0.50", port="4352", transport="tcp"
    )
    # The disabled spare must not appear.
    ids = [c["device_id"] for c in result["conflicts"]]
    assert "proj_disabled" not in ids


async def test_check_conflict_no_match(monkeypatch, conflict_engine):
    from server.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: conflict_engine)
    result = await drivers_routes.check_connection_conflict(
        host="10.0.0.50", port="9999", transport="tcp"
    )
    assert result["conflicts"] == []


async def test_check_conflict_non_tcp_returns_empty(monkeypatch, conflict_engine):
    """Single-session poaching is a TCP issue; HTTP/UDP/serial return [] so
    the UI doesn't surface false-alarm warnings."""
    from server.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: conflict_engine)
    for transport in ("http", "udp", "serial", "osc"):
        result = await drivers_routes.check_connection_conflict(
            host="10.0.0.50", port="4352", transport=transport
        )
        assert result["conflicts"] == [], f"transport={transport}"


async def test_check_conflict_invalid_port(monkeypatch, conflict_engine):
    from server.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: conflict_engine)
    result = await drivers_routes.check_connection_conflict(
        host="10.0.0.50", port="not-a-port", transport="tcp"
    )
    assert result["conflicts"] == []


async def test_check_conflict_honors_connection_overrides(monkeypatch, conflict_engine):
    """connections[device_id] overrides device.config — the conflict check
    must compare against the merged effective host:port."""
    from server.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: conflict_engine)
    conflict_engine.project.connections["proj_main"] = {
        "host": "10.0.0.77",
        "port": 4352,
    }
    # Match against the override, not the bare config.
    result = await drivers_routes.check_connection_conflict(
        host="10.0.0.77", port="4352", transport="tcp"
    )
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["device_id"] == "proj_main"


async def test_check_conflict_surfaces_paused_state(monkeypatch, conflict_engine):
    from server.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: conflict_engine)
    conflict_engine.state.set("device.proj_main.paused", True, source="test")

    result = await drivers_routes.check_connection_conflict(
        host="10.0.0.50", port="4352", transport="tcp"
    )
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["paused"] is True
