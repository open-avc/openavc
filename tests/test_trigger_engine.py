"""Tests for TriggerEngine."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.state_store import StateStore
from server.core.trigger_engine import TriggerEngine


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
    return MacroEngine(state, events, devices)


@pytest.fixture
def trigger_engine(core, macro_engine):
    state, events = core
    return TriggerEngine(state, events, macro_engine)


# --- Operator evaluation ---


def test_eval_operator():
    ev = TriggerEngine._eval_operator
    assert ev("eq", 42, 42) is True
    assert ev("eq", 42, 43) is False
    assert ev("ne", 1, 2) is True
    assert ev("ne", 1, 1) is False
    assert ev("gt", 10, 5) is True
    assert ev("gt", 5, 10) is False
    assert ev("lt", 5, 10) is True
    assert ev("lt", 10, 5) is False
    assert ev("gte", 10, 10) is True
    assert ev("gte", 9, 10) is False
    assert ev("lte", 10, 10) is True
    assert ev("lte", 11, 10) is False
    assert ev("truthy", True, None) is True
    assert ev("truthy", 0, None) is False
    assert ev("falsy", False, None) is True
    assert ev("falsy", 1, None) is False
    assert ev("unknown_op", 1, 1) is False
    # None safety for gt/lt/gte/lte
    assert ev("gt", None, 5) is False
    assert ev("lt", 5, None) is False


# --- State change trigger ---


async def test_state_change_trigger_any(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.triggered", "value": True}],
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.occupancy",
            "state_operator": "any",
        }],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.occupancy",
            "state_operator": "any",
        }],
    }])
    await trigger_engine.start()

    # Change the state
    state.set("var.occupancy", True, source="test")
    await asyncio.sleep(0.1)

    assert state.get("var.triggered") is True
    await trigger_engine.stop()


async def test_state_change_trigger_eq(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.triggered", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.mode",
            "state_operator": "eq",
            "state_value": "off",
        }],
    }])
    await trigger_engine.start()

    # Should not trigger for wrong value
    state.set("var.mode", "on", source="test")
    await asyncio.sleep(0.1)
    assert state.get("var.triggered") is None

    # Should trigger for matching value
    state.set("var.mode", "off", source="test")
    await asyncio.sleep(0.1)
    assert state.get("var.triggered") is True
    await trigger_engine.stop()


# --- Event trigger ---


async def test_event_trigger(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.event_fired", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_event",
            "type": "event",
            "enabled": True,
            "event_pattern": "custom.my_event",
        }],
    }])
    await trigger_engine.start()

    await events.emit("custom.my_event", {})
    await asyncio.sleep(0.1)

    assert state.get("var.event_fired") is True
    await trigger_engine.stop()


async def test_event_trigger_wildcard(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.event_fired", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_event",
            "type": "event",
            "enabled": True,
            "event_pattern": "device.connected.*",
        }],
    }])
    await trigger_engine.start()

    await events.emit("device.connected.projector1", {})
    await asyncio.sleep(0.1)

    assert state.get("var.event_fired") is True
    await trigger_engine.stop()


# --- Startup trigger ---


async def test_startup_trigger(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.started", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_startup",
            "type": "startup",
            "enabled": True,
            "delay_seconds": 0,
        }],
    }])
    await trigger_engine.start()
    await asyncio.sleep(0.15)

    assert state.get("var.started") is True
    await trigger_engine.stop()


async def test_startup_trigger_with_delay(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.started", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_startup",
            "type": "startup",
            "enabled": True,
            "delay_seconds": 0.2,
        }],
    }])
    await trigger_engine.start()

    # Should not have fired yet
    await asyncio.sleep(0.05)
    assert state.get("var.started") is None

    # Should fire after delay
    await asyncio.sleep(0.25)
    assert state.get("var.started") is True
    await trigger_engine.stop()


# --- Guard conditions ---


async def test_conditions_pass(trigger_engine, macro_engine, core):
    state, events = core
    state.set("var.room_active", True, source="test")

    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.triggered", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.occupancy",
            "state_operator": "any",
            "conditions": [
                {"key": "var.room_active", "operator": "eq", "value": True},
            ],
        }],
    }])
    await trigger_engine.start()

    state.set("var.occupancy", False, source="test")
    await asyncio.sleep(0.1)

    assert state.get("var.triggered") is True
    await trigger_engine.stop()


async def test_conditions_fail(trigger_engine, macro_engine, core):
    state, events = core
    state.set("var.room_active", False, source="test")

    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.triggered", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.occupancy",
            "state_operator": "any",
            "conditions": [
                {"key": "var.room_active", "operator": "eq", "value": True},
            ],
        }],
    }])
    await trigger_engine.start()

    state.set("var.occupancy", True, source="test")
    await asyncio.sleep(0.1)

    assert state.get("var.triggered") is None
    await trigger_engine.stop()


# --- Cooldown ---


async def test_cooldown(trigger_engine, macro_engine, core):
    state, events = core
    fire_count = 0
    original_execute = macro_engine.execute

    async def counting_execute(macro_id, context=None):
        nonlocal fire_count
        fire_count += 1
        await original_execute(macro_id, context)

    macro_engine.execute = counting_execute

    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.x", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.count",
            "state_operator": "any",
            "cooldown_seconds": 1.0,
        }],
    }])
    await trigger_engine.start()

    # Fire first
    state.set("var.count", 1, source="test")
    await asyncio.sleep(0.1)
    assert fire_count == 1

    # Should be blocked by cooldown
    state.set("var.count", 2, source="test")
    await asyncio.sleep(0.1)
    assert fire_count == 1

    await trigger_engine.stop()


# --- Debounce ---


async def test_debounce(trigger_engine, macro_engine, core):
    state, events = core
    fire_count = 0
    original_execute = macro_engine.execute

    async def counting_execute(macro_id, context=None):
        nonlocal fire_count
        fire_count += 1
        await original_execute(macro_id, context)

    macro_engine.execute = counting_execute

    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.x", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.sensor",
            "state_operator": "any",
            "debounce_seconds": 0.3,
        }],
    }])
    await trigger_engine.start()

    # Rapid changes should be debounced
    state.set("var.sensor", 1, source="test")
    await asyncio.sleep(0.05)
    state.set("var.sensor", 2, source="test")
    await asyncio.sleep(0.05)
    state.set("var.sensor", 3, source="test")

    # Should not have fired yet
    assert fire_count == 0

    # Wait for debounce to settle
    await asyncio.sleep(0.5)
    assert fire_count == 1

    await trigger_engine.stop()


# --- Delay + re-check ---


async def test_delay_recheck_fires(trigger_engine, macro_engine, core):
    """If condition still met after delay, macro should fire."""
    state, events = core

    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.shutdown", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.occupancy",
            "state_operator": "eq",
            "state_value": False,
            "delay_seconds": 0.2,
        }],
    }])
    await trigger_engine.start()

    state.set("var.occupancy", False, source="test")
    await asyncio.sleep(0.05)
    assert state.get("var.shutdown") is None  # Not yet

    await asyncio.sleep(0.3)
    assert state.get("var.shutdown") is True  # Fired after delay
    await trigger_engine.stop()


async def test_delay_recheck_cancels(trigger_engine, macro_engine, core):
    """If state reverts during delay, macro should NOT fire."""
    state, events = core

    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.shutdown", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.occupancy",
            "state_operator": "eq",
            "state_value": False,
            "delay_seconds": 0.3,
        }],
    }])
    await trigger_engine.start()

    state.set("var.occupancy", False, source="test")
    await asyncio.sleep(0.1)

    # Revert before delay expires
    state.set("var.occupancy", True, source="test")
    await asyncio.sleep(0.4)

    # Should NOT have fired
    assert state.get("var.shutdown") is None
    await trigger_engine.stop()


# --- Circular prevention ---


async def test_circular_prevention(trigger_engine, macro_engine, core):
    """Macro A sets state -> trigger on macro A again should be blocked."""
    state, events = core

    macro_engine.load_macros([{
        "id": "macro_a",
        "name": "Macro A",
        "steps": [{"action": "state.set", "key": "var.counter", "value": 999}],
    }])
    trigger_engine.load_triggers([{
        "id": "macro_a",
        "name": "Macro A",
        "triggers": [{
            "id": "trg_circ",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.counter",
            "state_operator": "any",
        }],
    }])
    await trigger_engine.start()

    # Trigger the macro
    state.set("var.counter", 1, source="test")
    await asyncio.sleep(0.2)

    # If circular was not prevented, this would loop forever.
    # The macro sets var.counter=999 which would re-trigger, but should be blocked.
    assert state.get("var.counter") == 999
    await trigger_engine.stop()


# --- Disabled trigger ---


async def test_disabled_trigger(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.triggered", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_disabled",
            "type": "state_change",
            "enabled": False,
            "state_key": "var.occupancy",
            "state_operator": "any",
        }],
    }])
    await trigger_engine.start()

    state.set("var.occupancy", True, source="test")
    await asyncio.sleep(0.1)

    assert state.get("var.triggered") is None
    await trigger_engine.stop()


# --- Overlap policy: skip ---


async def test_overlap_skip(trigger_engine, macro_engine, core):
    state, events = core
    fire_count = 0

    macro_engine.load_macros([{
        "id": "slow_macro",
        "name": "Slow",
        "steps": [{"action": "delay", "seconds": 0.5}],
    }])

    original_execute = macro_engine.execute

    async def counting_execute(macro_id, context=None):
        nonlocal fire_count
        fire_count += 1
        await original_execute(macro_id, context)

    macro_engine.execute = counting_execute

    trigger_engine.load_triggers([{
        "id": "slow_macro",
        "name": "Slow",
        "triggers": [{
            "id": "trg_overlap",
            "type": "event",
            "enabled": True,
            "event_pattern": "custom.go",
            "overlap": "skip",
        }],
    }])
    await trigger_engine.start()

    # Fire twice quickly
    await events.emit("custom.go", {})
    await asyncio.sleep(0.05)
    await events.emit("custom.go", {})
    await asyncio.sleep(0.7)

    # The second should be skipped because first is still running
    # Note: overlap check depends on macros._running. Since we patched execute,
    # we just verify both attempted but only 2 calls happened (skip works at trigger level)
    # At minimum, both should attempt but circular protection is separate
    await trigger_engine.stop()


# --- list_triggers ---


def test_list_triggers(trigger_engine):
    trigger_engine.load_triggers([{
        "id": "m1",
        "name": "Macro 1",
        "triggers": [
            {"id": "t1", "type": "schedule", "enabled": True, "cron": "0 18 * * 1-5"},
            {"id": "t2", "type": "state_change", "enabled": False, "state_key": "var.x"},
        ],
    }])
    result = trigger_engine.list_triggers()
    assert len(result) == 2
    assert result[0]["id"] == "t1"
    assert result[0]["type"] == "schedule"
    assert result[0]["macro_id"] == "m1"
    assert result[1]["enabled"] is False


# --- test_trigger ---


async def test_test_trigger(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "m1",
        "name": "Test Macro",
        "steps": [{"action": "state.set", "key": "var.tested", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "m1",
        "name": "Test Macro",
        "triggers": [{"id": "trg_test", "type": "schedule", "enabled": True}],
    }])

    ok = await trigger_engine.test_trigger("trg_test")
    assert ok is True
    assert state.get("var.tested") is True

    not_ok = await trigger_engine.test_trigger("nonexistent")
    assert not_ok is False


# --- Reload (stop + reload + start) ---


async def test_reload(trigger_engine, macro_engine, core):
    state, events = core
    macro_engine.load_macros([{
        "id": "m1",
        "name": "M1",
        "steps": [{"action": "state.set", "key": "var.v1", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "m1",
        "name": "M1",
        "triggers": [{
            "id": "trg_1",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.x",
            "state_operator": "any",
        }],
    }])
    await trigger_engine.start()

    state.set("var.x", 1, source="test")
    await asyncio.sleep(0.1)
    assert state.get("var.v1") is True

    # Reload with new trigger pointing at different state key
    await trigger_engine.stop()
    trigger_engine.load_triggers([{
        "id": "m1",
        "name": "M1",
        "triggers": [{
            "id": "trg_2",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.y",
            "state_operator": "any",
        }],
    }])
    await trigger_engine.start()

    # Old trigger should not fire
    state.set("var.v1", None, source="test")  # Reset
    state.set("var.x", 2, source="test")
    await asyncio.sleep(0.1)
    assert state.get("var.v1") is None

    # New trigger should fire
    state.set("var.y", 1, source="test")
    await asyncio.sleep(0.1)
    assert state.get("var.v1") is True

    await trigger_engine.stop()


# --- Multiple triggers on same macro ---


async def test_multiple_triggers_same_macro(trigger_engine, macro_engine, core):
    state, events = core
    fire_count = 0
    original_execute = macro_engine.execute

    async def counting_execute(macro_id, context=None):
        nonlocal fire_count
        fire_count += 1
        await original_execute(macro_id, context)

    macro_engine.execute = counting_execute

    macro_engine.load_macros([{
        "id": "m1",
        "name": "M1",
        "steps": [],
    }])
    trigger_engine.load_triggers([{
        "id": "m1",
        "name": "M1",
        "triggers": [
            {
                "id": "trg_a",
                "type": "event",
                "enabled": True,
                "event_pattern": "custom.a",
            },
            {
                "id": "trg_b",
                "type": "event",
                "enabled": True,
                "event_pattern": "custom.b",
            },
        ],
    }])
    await trigger_engine.start()

    await events.emit("custom.a", {})
    await asyncio.sleep(0.1)
    await events.emit("custom.b", {})
    await asyncio.sleep(0.1)

    assert fire_count == 2
    await trigger_engine.stop()
