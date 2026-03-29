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


async def test_delay_step(macro_engine):
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "delay", "seconds": 0.1}
        ],
    }])
    import time
    start = time.time()
    await macro_engine.execute("test")
    elapsed = time.time() - start
    assert elapsed >= 0.08  # Allow a little tolerance


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
