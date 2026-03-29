"""Tests for DeviceManager."""

import asyncio

import pytest

from server.core.device_manager import DeviceManager, _DRIVER_REGISTRY
from server.core.event_bus import EventBus
from server.core.state_store import StateStore

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
