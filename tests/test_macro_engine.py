"""Tests for MacroEngine."""

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
    # Mock send_command so we don't need real devices
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


async def test_state_set_step(macro_engine, core):
    state, _ = core
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "state.set", "key": "var.x", "value": 42}
        ],
    }])
    await macro_engine.execute("test")
    assert state.get("var.x") == 42


async def test_device_command_step(macro_engine):
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "device.command", "device": "proj1", "command": "power_on", "params": {}}
        ],
    }])
    await macro_engine.execute("test")
    macro_engine.devices.send_command.assert_called_once_with("proj1", "power_on", {})


async def test_event_emit_step(macro_engine, core):
    _, events = core
    received = []
    events.on("custom.test_event", lambda e, p: received.append(p))

    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "event.emit", "event": "custom.test_event", "payload": {"msg": "hi"}}
        ],
    }])
    await macro_engine.execute("test")
    assert len(received) == 1
    assert received[0]["msg"] == "hi"


async def test_nested_macro(macro_engine, core):
    state, _ = core
    macro_engine.load_macros([
        {
            "id": "inner",
            "name": "Inner",
            "steps": [{"action": "state.set", "key": "var.inner_ran", "value": True}],
        },
        {
            "id": "outer",
            "name": "Outer",
            "steps": [
                {"action": "state.set", "key": "var.outer_ran", "value": True},
                {"action": "macro", "macro": "inner"},
            ],
        },
    ])
    await macro_engine.execute("outer")
    assert state.get("var.outer_ran") is True
    assert state.get("var.inner_ran") is True


async def test_error_continues_to_next_step(macro_engine, core):
    state, _ = core
    macro_engine.devices.send_command = AsyncMock(side_effect=Exception("boom"))

    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "device.command", "device": "proj1", "command": "power_on"},
            {"action": "state.set", "key": "var.after_error", "value": True},
        ],
    }])
    await macro_engine.execute("test")
    # Second step should still run
    assert state.get("var.after_error") is True


async def test_unknown_macro(macro_engine):
    # Should log error but not crash
    await macro_engine.execute("nonexistent")


async def test_multi_step_sequence(macro_engine, core):
    state, _ = core
    macro_engine.load_macros([{
        "id": "seq",
        "name": "Sequence",
        "steps": [
            {"action": "state.set", "key": "var.a", "value": 1},
            {"action": "state.set", "key": "var.b", "value": 2},
            {"action": "state.set", "key": "var.c", "value": 3},
        ],
    }])
    await macro_engine.execute("seq")
    assert state.get("var.a") == 1
    assert state.get("var.b") == 2
    assert state.get("var.c") == 3


# --- Device group command tests ---


async def test_group_command_all_devices(macro_engine, core):
    """Group command sends to all devices in the group."""
    state, _ = core
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

    await macro_engine.execute("test")
    assert macro_engine.devices.send_command.call_count == 0
