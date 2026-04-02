"""Tests for device groups and group.command macro steps."""

from unittest.mock import AsyncMock

import pytest

from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.state_store import StateStore


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def macro_engine(core):
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    engine = MacroEngine(state, events, devices)
    return engine


async def test_group_command_all_devices(macro_engine, core):
    """Group command sends to all devices in the group."""
    state, _ = core
    # Mark devices as connected
    state.set("device.proj1.connected", True)
    state.set("device.proj2.connected", True)
    state.set("device.proj3.connected", True)

    macro_engine.load_groups([{
        "id": "projectors",
        "device_ids": ["proj1", "proj2", "proj3"],
    }])
    macro_engine.load_macros([{
        "id": "power_on_all",
        "name": "Power On All",
        "steps": [
            {"action": "group.command", "group": "projectors", "command": "power_on", "params": {}}
        ],
    }])

    await macro_engine.execute("power_on_all")

    assert macro_engine.devices.send_command.call_count == 3
    called_devices = {call.args[0] for call in macro_engine.devices.send_command.call_args_list}
    assert called_devices == {"proj1", "proj2", "proj3"}
    for call in macro_engine.devices.send_command.call_args_list:
        assert call.args[1] == "power_on"


async def test_group_command_concurrent(macro_engine, core):
    """Commands execute concurrently (all sent via asyncio.gather)."""
    state, _ = core
    state.set("device.d1.connected", True)
    state.set("device.d2.connected", True)

    macro_engine.load_groups([{
        "id": "displays",
        "device_ids": ["d1", "d2"],
    }])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "displays", "command": "input_select", "params": {"input": "hdmi1"}}
        ],
    }])

    await macro_engine.execute("test")

    # Both commands were sent (concurrently via gather)
    assert macro_engine.devices.send_command.call_count == 2
    for call in macro_engine.devices.send_command.call_args_list:
        assert call.args[1] == "input_select"
        assert call.args[2] == {"input": "hdmi1"}


async def test_group_command_partial_offline(macro_engine, core):
    """Offline devices are skipped, online devices still get commands."""
    state, _ = core
    state.set("device.proj1.connected", True)
    state.set("device.proj2.connected", False)
    state.set("device.proj3.connected", True)

    macro_engine.load_groups([{
        "id": "projectors",
        "device_ids": ["proj1", "proj2", "proj3"],
    }])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "projectors", "command": "power_on"}
        ],
    }])

    await macro_engine.execute("test")

    # Only 2 online devices received the command
    assert macro_engine.devices.send_command.call_count == 2
    called_devices = {call.args[0] for call in macro_engine.devices.send_command.call_args_list}
    assert called_devices == {"proj1", "proj3"}


async def test_group_command_empty_group(macro_engine, core):
    """Empty group is a no-op (no error, no commands sent)."""
    macro_engine.load_groups([{
        "id": "empty",
        "device_ids": [],
    }])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "empty", "command": "power_on"}
        ],
    }])

    await macro_engine.execute("test")
    assert macro_engine.devices.send_command.call_count == 0


async def test_group_command_unknown_group(macro_engine, core):
    """Unknown group logs error but doesn't crash."""
    macro_engine.load_groups([])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "nonexistent", "command": "power_on"}
        ],
    }])

    # Should not raise
    await macro_engine.execute("test")
    assert macro_engine.devices.send_command.call_count == 0
