"""Tests for DeviceManager."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from server.core.device_manager import DeviceManager, _DRIVER_REGISTRY
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver

needs_pjlink = pytest.mark.skipif(
    "pjlink_class1" not in _DRIVER_REGISTRY,
    reason="pjlink_class1 driver not installed",
)


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


@needs_pjlink
async def test_add_device(dm, core, pjlink_sim):
    state, _ = core
    await dm.add_device({
        "id": "proj1",
        "driver": "pjlink_class1",
        "name": "Test Projector",
        "config": {"host": "127.0.0.1", "port": pjlink_sim.port, "poll_interval": 0},
    })
    assert state.get("device.proj1.connected") is True
    assert state.get("device.proj1.name") == "Test Projector"
    await dm.disconnect_all()


@needs_pjlink
async def test_send_command(dm, core, pjlink_sim):
    state, _ = core
    await dm.add_device({
        "id": "proj1",
        "driver": "pjlink_class1",
        "name": "Test Projector",
        "config": {"host": "127.0.0.1", "port": pjlink_sim.port, "poll_interval": 0},
    })
    await dm.send_command("proj1", "power_on")
    await asyncio.sleep(0.1)
    # No assert on state since poll isn't running, just verify no exception
    await dm.disconnect_all()


async def test_send_command_unknown_device(dm):
    with pytest.raises(ValueError, match="not found"):
        await dm.send_command("nonexistent", "power_on")


@needs_pjlink
async def test_list_devices(dm, core, pjlink_sim):
    await dm.add_device({
        "id": "proj1",
        "driver": "pjlink_class1",
        "name": "Test Projector",
        "config": {"host": "127.0.0.1", "port": pjlink_sim.port, "poll_interval": 0},
    })
    devices = dm.list_devices()
    assert len(devices) == 1
    assert devices[0]["id"] == "proj1"
    assert devices[0]["connected"] is True
    await dm.disconnect_all()


@needs_pjlink
async def test_remove_device(dm, core, pjlink_sim):
    state, _ = core
    await dm.add_device({
        "id": "proj1",
        "driver": "pjlink_class1",
        "name": "Test Projector",
        "config": {"host": "127.0.0.1", "port": pjlink_sim.port, "poll_interval": 0},
    })
    assert len(dm.list_devices()) == 1
    await dm.remove_device("proj1")
    assert len(dm.list_devices()) == 0


async def test_unknown_driver(dm):
    await dm.add_device({
        "id": "bad_device",
        "driver": "totally_fake_driver",
        "name": "Bad Device",
        "config": {},
    })
    # Should track as orphaned (visible but not connected)
    devices = dm.list_devices()
    assert len(devices) == 1
    assert devices[0]["id"] == "bad_device"
    assert devices[0]["orphaned"] is True
    assert "totally_fake_driver" in devices[0]["orphan_reason"]


@needs_pjlink
async def test_get_device_info(dm, core, pjlink_sim):
    await dm.add_device({
        "id": "proj1",
        "driver": "pjlink_class1",
        "name": "Test Projector",
        "config": {"host": "127.0.0.1", "port": pjlink_sim.port, "poll_interval": 0},
    })
    info = dm.get_device_info("proj1")
    assert info["id"] == "proj1"
    assert info["driver"] == "pjlink_class1"
    assert "power_on" in info["commands"]
    await dm.disconnect_all()


# ---------------------------------------------------------------------------
# Mock driver for reconnection tests
# ---------------------------------------------------------------------------

class MockDriver(BaseDriver):
    """A minimal mock driver for testing reconnection logic."""

    DRIVER_INFO = {
        "id": "mock_driver",
        "name": "Mock Driver",
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
        self.connect_fail_count = 0  # Fail this many times before succeeding

    async def connect(self):
        self.connect_calls += 1
        if self.connect_calls <= self.connect_fail_count:
            raise ConnectionError(f"Mock connection failed (attempt {self.connect_calls})")
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self):
        self._connected = False
        self.state.set(f"device.{self.device_id}.connected", False, source="driver")

    async def send_command(self, command: str, params: dict | None = None):
        pass

    async def stop_polling(self):
        pass


# Register the mock driver
_DRIVER_REGISTRY["mock_driver"] = MockDriver


# ---------------------------------------------------------------------------
# Reconnection tests
# ---------------------------------------------------------------------------

async def test_reconnect_loop_success_on_first_attempt(dm, core):
    """Reconnect loop succeeds immediately when connect works."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    driver._connected = False
    dm._devices["test_dev"] = driver

    await dm._reconnect_loop("test_dev", max_attempts=3)
    assert driver._connected is True
    assert driver.connect_calls == 1


async def test_reconnect_loop_retries_on_failure(dm, core):
    """Reconnect loop retries after connect failure."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    driver._connected = False
    driver.connect_fail_count = 2  # Fail twice, succeed on third
    dm._devices["test_dev"] = driver

    # Patch sleep to make test fast
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await dm._reconnect_loop("test_dev", max_attempts=5)

    assert driver._connected is True
    assert driver.connect_calls == 3  # 2 failures + 1 success


async def test_reconnect_loop_gives_up_after_max_attempts(dm, core):
    """Reconnect loop gives up after max_attempts failures."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    driver._connected = False
    driver.connect_fail_count = 999  # Always fail
    dm._devices["test_dev"] = driver

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await dm._reconnect_loop("test_dev", max_attempts=3)

    assert driver._connected is False
    assert driver.connect_calls == 3
    # Should set reconnect_failed state
    assert state.get("device.test_dev.reconnect_failed") is True


async def test_reconnect_loop_stops_if_device_removed(dm, core):
    """Reconnect loop stops if device is removed during reconnection."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    driver._connected = False
    driver.connect_fail_count = 999
    dm._devices["test_dev"] = driver

    call_count = 0
    original_sleep = asyncio.sleep

    async def mock_sleep(delay):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            # Simulate device removal during reconnect
            del dm._devices["test_dev"]

    with patch("asyncio.sleep", side_effect=mock_sleep):
        await dm._reconnect_loop("test_dev", max_attempts=10)

    # Should have stopped after device was removed
    assert driver.connect_calls < 10


async def test_reconnect_loop_exponential_backoff(dm, core):
    """Reconnect loop uses exponential backoff delays."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    driver._connected = False
    driver.connect_fail_count = 5
    dm._devices["test_dev"] = driver

    sleep_delays = []

    async def mock_sleep(delay):
        sleep_delays.append(delay)

    with patch("asyncio.sleep", side_effect=mock_sleep):
        await dm._reconnect_loop("test_dev", max_attempts=6)

    # Expected delays: 2, 4, 8, 16, 30, 30 (capped)
    assert sleep_delays[0] == 2
    assert sleep_delays[1] == 4
    assert sleep_delays[2] == 8
    assert sleep_delays[3] == 16
    assert sleep_delays[4] == 30  # Capped at max


async def test_start_reconnect_creates_task(dm, core):
    """_start_reconnect creates a background task."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    dm._devices["test_dev"] = driver

    dm._start_reconnect("test_dev")
    assert "test_dev" in dm._reconnect_tasks

    # Cancel and clean up
    dm._cancel_reconnect("test_dev")
    assert "test_dev" not in dm._reconnect_tasks


async def test_start_reconnect_idempotent(dm, core):
    """Calling _start_reconnect twice doesn't create duplicate tasks."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    dm._devices["test_dev"] = driver

    dm._start_reconnect("test_dev")
    task1 = dm._reconnect_tasks.get("test_dev")

    dm._start_reconnect("test_dev")
    task2 = dm._reconnect_tasks.get("test_dev")

    assert task1 is task2  # Same task, not replaced

    dm._cancel_reconnect("test_dev")


async def test_cancel_reconnect(dm, core):
    """_cancel_reconnect cancels a running reconnect task."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    driver.connect_fail_count = 999
    dm._devices["test_dev"] = driver

    dm._start_reconnect("test_dev")
    assert "test_dev" in dm._reconnect_tasks

    dm._cancel_reconnect("test_dev")
    assert "test_dev" not in dm._reconnect_tasks


async def test_on_device_disconnected_triggers_reconnect(dm, core):
    """Transport disconnect event triggers auto-reconnect."""
    state, events = core
    driver = MockDriver("test_dev", {}, state, events)
    driver._connected = False
    dm._devices["test_dev"] = driver
    dm._device_configs["test_dev"] = {"id": "test_dev", "driver": "mock_driver", "enabled": True}
    state.set("device.test_dev.enabled", True, source="config")

    # Emit disconnect event
    await events.emit("device.disconnected.test_dev", {"device_id": "test_dev"})

    # Should have started a reconnect task
    await asyncio.sleep(0.05)  # Let event handler run
    assert "test_dev" in dm._reconnect_tasks

    dm._cancel_reconnect("test_dev")
