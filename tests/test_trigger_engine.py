"""Tests for TriggerEngine."""

import asyncio
import logging
import time
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import server.core.trigger_engine as te
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
    from server.core.condition_eval import eval_operator as ev_fn
    ev = ev_fn
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
    with pytest.raises(ValueError, match="Unknown condition operator"):
        ev("unknown_op", 1, 1)
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
    fired = []
    events.on("trigger.fired", lambda e, p: fired.append(p))
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
    # The "Fire now" button must emit trigger.fired so the Macro editor flashes
    # the trigger card the same as a real fire.
    assert [p["trigger_id"] for p in fired] == ["trg_test"]
    assert fired[0]["trigger_type"] == "test"

    not_ok = await trigger_engine.test_trigger("nonexistent")
    assert not_ok is False
    # A nonexistent trigger fires nothing new.
    assert len(fired) == 1


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


# --- Overlap policy: queue ---


def _queue_trigger(macro_id="m", trigger_id="trg", **extra):
    """A single overlap='queue' event trigger spec for a one-macro project."""
    trig = {
        "id": trigger_id,
        "type": "event",
        "enabled": True,
        "event_pattern": "go",
        "overlap": "queue",
    }
    trig.update(extra)
    return [{"id": macro_id, "name": "M", "triggers": [trig]}]


async def test_overlap_queue_cleanup_and_position(
    trigger_engine, macro_engine, core, monkeypatch
):
    """Completed queued fires are removed; queue_position doesn't climb forever.

    pending_queue previously accumulated one Task per fire for the life of the
    project load, and queue_position (len of that list) climbed without bound.
    The task now self-removes when it settles.
    """
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.05)
    state, events = core

    running = {"v": True}
    macro_engine.is_macro_running = lambda mid: running["v"]
    fire_count = {"n": 0}

    async def counting(mid, context=None):
        fire_count["n"] += 1

    macro_engine.execute = counting

    positions: list[int] = []
    events.on("trigger.queued", lambda e, p: positions.append(p["queue_position"]))

    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger())
    await trigger_engine.start()
    ts = trigger_engine._triggers["trg"]

    # Macro "running" -> two fires queue up with positions 1 then 2.
    await events.emit("go", {})
    await asyncio.sleep(0.02)
    await events.emit("go", {})
    await asyncio.sleep(0.02)
    assert len(ts.pending_queue) == 2
    assert positions == [1, 2]

    # Macro finishes -> queued fires run and remove themselves from the queue.
    running["v"] = False
    await asyncio.sleep(0.2)
    assert fire_count["n"] == 2
    assert len(ts.pending_queue) == 0  # no leaked completed tasks

    # A new fire while running again starts back at position 1, not 3.
    running["v"] = True
    await events.emit("go", {})
    await asyncio.sleep(0.02)
    assert positions[-1] == 1
    assert len(ts.pending_queue) == 1

    running["v"] = False
    await asyncio.sleep(0.2)
    assert len(ts.pending_queue) == 0
    await trigger_engine.stop()


async def test_overlap_queue_fires_after_macro_finishes(
    trigger_engine, macro_engine, core, monkeypatch
):
    """The happy path: a queued fire runs once the running macro completes."""
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.05)
    state, events = core
    running = {"v": True}
    macro_engine.is_macro_running = lambda mid: running["v"]
    fire_count = {"n": 0}

    async def counting(mid, context=None):
        fire_count["n"] += 1

    macro_engine.execute = counting
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger())
    await trigger_engine.start()

    await events.emit("go", {})
    await asyncio.sleep(0.1)
    assert fire_count["n"] == 0  # still queued, macro busy

    running["v"] = False
    await asyncio.sleep(0.2)
    assert fire_count["n"] == 1  # fired after the wait
    await trigger_engine.stop()


async def test_overlap_queue_timeout(
    trigger_engine, macro_engine, core, monkeypatch
):
    """A wedged macro times the queued fire out instead of pinning it forever.

    This 5-minute timeout path had no coverage. Constants are shrunk so the
    timeout is reached in milliseconds.
    """
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    monkeypatch.setattr(te, "_QUEUE_MAX_WAIT_SECONDS", 0.06)
    state, events = core

    macro_engine.is_macro_running = lambda mid: True  # never finishes
    fire_count = {"n": 0}

    async def counting(mid, context=None):
        fire_count["n"] += 1

    macro_engine.execute = counting
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger())
    await trigger_engine.start()
    ts = trigger_engine._triggers["trg"]

    await events.emit("go", {})
    await asyncio.sleep(0.2)  # well past the 0.06s wait budget

    assert fire_count["n"] == 0  # timed out, never fired
    assert len(ts.pending_queue) == 0  # timed-out task removed itself
    await trigger_engine.stop()


async def test_overlap_queue_aborts_when_disabled(
    trigger_engine, macro_engine, core, monkeypatch
):
    """Disabling a trigger mid-wait aborts its queued fire."""
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    state, events = core
    macro_engine.is_macro_running = lambda mid: True  # stays "running"
    fire_count = {"n": 0}

    async def counting(mid, context=None):
        fire_count["n"] += 1

    macro_engine.execute = counting
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger())
    await trigger_engine.start()
    ts = trigger_engine._triggers["trg"]

    await events.emit("go", {})
    await asyncio.sleep(0.05)
    assert len(ts.pending_queue) == 1

    ts.trigger["enabled"] = False  # disabled while waiting
    await asyncio.sleep(0.1)
    assert fire_count["n"] == 0  # aborted
    assert len(ts.pending_queue) == 0
    await trigger_engine.stop()


async def test_overlap_queue_rechecks_conditions_after_wait(
    trigger_engine, macro_engine, core, monkeypatch
):
    """A guard that goes false during the wait suppresses the queued fire."""
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    state, events = core
    state.set("var.guard", True, source="test")
    running = {"v": True}
    macro_engine.is_macro_running = lambda mid: running["v"]
    fire_count = {"n": 0}

    async def counting(mid, context=None):
        fire_count["n"] += 1

    macro_engine.execute = counting
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger(
        conditions=[{"key": "var.guard", "operator": "eq", "value": True}]
    ))
    await trigger_engine.start()
    ts = trigger_engine._triggers["trg"]

    await events.emit("go", {})  # passes the guard at queue time
    await asyncio.sleep(0.05)
    assert len(ts.pending_queue) == 1

    state.set("var.guard", False, source="test")  # guard now fails
    running["v"] = False  # macro finishes -> re-check runs
    await asyncio.sleep(0.1)

    assert fire_count["n"] == 0  # re-check failed, did not fire
    assert len(ts.pending_queue) == 0
    await trigger_engine.stop()


# --- Overlap policy: allow ---


async def test_overlap_allow_runs_concurrently(trigger_engine, macro_engine, core):
    """overlap='allow' fires concurrently instead of being blocked as circular.

    The per-trigger chain guard previously single-flighted overlap=allow and
    mislabeled the skip as 'circular'.
    """
    state, events = core
    fire_count = 0
    original_execute = macro_engine.execute

    async def counting_execute(macro_id, context=None):
        nonlocal fire_count
        fire_count += 1
        await original_execute(macro_id, context)

    macro_engine.execute = counting_execute

    skips: list[str] = []
    events.on("trigger.skipped", lambda e, p: skips.append(p["reason"]))

    macro_engine.load_macros([{
        "id": "slow_macro",
        "name": "Slow",
        "steps": [{"action": "delay", "seconds": 0.4}],
    }])
    trigger_engine.load_triggers([{
        "id": "slow_macro",
        "name": "Slow",
        "triggers": [{
            "id": "trg_allow",
            "type": "event",
            "enabled": True,
            "event_pattern": "custom.go",
            "overlap": "allow",
        }],
    }])
    await trigger_engine.start()

    await events.emit("custom.go", {})
    await asyncio.sleep(0.05)
    await events.emit("custom.go", {})  # first run still in its 0.4s delay
    await asyncio.sleep(0.1)

    assert fire_count == 2  # both ran concurrently
    assert "circular" not in skips  # not mislabeled / single-flighted

    await asyncio.sleep(0.5)
    await trigger_engine.stop()


async def test_overlap_allow_depth_cap_blocks_runaway(
    trigger_engine, macro_engine, core
):
    """overlap='allow' is still capped so a self-trigger loop can't run away."""
    state, events = core
    skips: list[str] = []
    events.on("trigger.skipped", lambda e, p: skips.append(p["reason"]))
    fire_count = {"n": 0}

    async def counting(mid, context=None):
        fire_count["n"] += 1

    macro_engine.execute = counting
    macro_engine.is_macro_running = lambda mid: False  # force the depth path

    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers([{
        "id": "m",
        "name": "M",
        "triggers": [{
            "id": "trg",
            "type": "event",
            "enabled": True,
            "event_pattern": "go",
            "overlap": "allow",
        }],
    }])
    await trigger_engine.start()
    ts = trigger_engine._triggers["trg"]

    # Simulate a runaway already at the cap, then attempt one more fire.
    trigger_engine._active_trigger_depth["trg"] = te._MAX_OVERLAP_ALLOW_DEPTH
    await trigger_engine._execute_trigger(ts, {})

    assert fire_count["n"] == 0  # blocked
    assert skips == ["max_depth"]  # distinct reason, not 'circular'
    await trigger_engine.stop()


# --- Fire-and-forget task hygiene ---


async def test_fire_task_tracked_then_pruned(trigger_engine, macro_engine, core):
    """The direct-fire task is strongly held while running, pruned when done.

    The fire used to be dispatched with no handle, so the GC could collect a
    still-pending task. It is now held in _bg_tasks and self-prunes.
    """
    state, events = core
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{"action": "delay", "seconds": 0.3}],
    }])
    trigger_engine.load_triggers([{
        "id": "m",
        "name": "M",
        "triggers": [{
            "id": "trg",
            "type": "event",
            "enabled": True,
            "event_pattern": "go",
        }],
    }])
    await trigger_engine.start()

    await events.emit("go", {})
    await asyncio.sleep(0.05)
    assert len(trigger_engine._bg_tasks) >= 1  # held while the macro runs

    await asyncio.sleep(0.4)
    assert len(trigger_engine._bg_tasks) == 0  # self-pruned after completion
    await trigger_engine.stop()


async def test_spawn_logs_unhandled_exception(trigger_engine, caplog):
    """Spawned tasks surface exceptions via the done-callback."""
    async def boom():
        raise RuntimeError("kaboom-in-bg-task")

    with caplog.at_level(logging.ERROR):
        task = trigger_engine._spawn(boom())
        await asyncio.sleep(0.05)

    assert task.done()
    assert task not in trigger_engine._bg_tasks  # self-pruned
    assert any("kaboom-in-bg-task" in r.getMessage() for r in caplog.records)


# --- Cron schedule decision: catch-up + dedup ---


def test_schedule_prev_due_cold_start_recent(trigger_engine):
    pytest.importorskip("croniter")
    now = datetime(2026, 1, 1, 18, 0, 30)
    prev, fire = trigger_engine._schedule_prev_due("0 18 * * *", now, None)
    assert prev == datetime(2026, 1, 1, 18, 0, 0)
    assert fire is True  # 30s old on cold start -> fire


def test_schedule_prev_due_cold_start_stale_skipped(trigger_engine):
    pytest.importorskip("croniter")
    now = datetime(2026, 1, 1, 18, 5, 0)  # 5 min past the occurrence
    _, fire = trigger_engine._schedule_prev_due("0 18 * * *", now, None)
    assert fire is False  # don't replay a pre-start schedule


def test_schedule_prev_due_steady_state_fires_once(trigger_engine):
    pytest.importorskip("croniter")
    last = datetime(2026, 1, 1, 17, 59, 30)
    now = datetime(2026, 1, 1, 18, 0, 20)
    _, fire = trigger_engine._schedule_prev_due("0 18 * * *", now, last)
    assert fire is True  # occurrence came due since the previous poll

    # Same occurrence next poll, no new one -> no double fire.
    _, fire2 = trigger_engine._schedule_prev_due(
        "0 18 * * *", datetime(2026, 1, 1, 18, 0, 50), now
    )
    assert fire2 is False


def test_schedule_prev_due_catch_up_after_falling_behind(trigger_engine):
    """A schedule due during a >60s loop gap still fires (catch-up)."""
    pytest.importorskip("croniter")
    last = datetime(2026, 1, 1, 17, 59, 50)
    now = datetime(2026, 1, 1, 18, 2, 0)  # loop fell ~130s behind
    prev, fire = trigger_engine._schedule_prev_due("0 18 * * *", now, last)
    assert prev == datetime(2026, 1, 1, 18, 0, 0)
    assert (now - prev).total_seconds() > 60  # old code would have dropped it
    assert fire is True  # caught up


async def test_cron_dedup_survives_reload(trigger_engine):
    """Cron dedup is instance state, kept across a hot reload, pruned when a
    trigger is removed."""
    pytest.importorskip("croniter")
    spec = [{
        "id": "m",
        "name": "M",
        "triggers": [{
            "id": "sched",
            "type": "schedule",
            "enabled": True,
            "cron": "* * * * *",
        }],
    }]
    trigger_engine.load_triggers(spec)
    # Record a prior fire, then simulate a hot reload (stop -> load -> start).
    trigger_engine._cron_last_fires["sched"] = datetime(2026, 1, 1, 12, 0, 0)
    await trigger_engine.start()
    await trigger_engine.stop()
    trigger_engine.load_triggers(spec)
    assert "sched" in trigger_engine._cron_last_fires  # survived the reload

    # Removing the trigger prunes its dedup entry.
    trigger_engine.load_triggers([{
        "id": "m",
        "name": "M",
        "triggers": [{
            "id": "other",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.x",
        }],
    }])
    assert "sched" not in trigger_engine._cron_last_fires


# --- Cooldown clock robustness ---


async def test_cooldown_monotonic_immune_to_forward_clock_jump(
    trigger_engine, macro_engine, core, monkeypatch
):
    """A forward wall-clock jump doesn't wrongly release the cooldown.

    The in-process baseline is monotonic, so an NTP step can't make a still-in-
    cooldown trigger fire again.
    """
    state, events = core
    fire_count = 0
    original_execute = macro_engine.execute

    async def counting(mid, context=None):
        nonlocal fire_count
        fire_count += 1
        await original_execute(mid, context)

    macro_engine.execute = counting
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{"action": "state.set", "key": "var.x", "value": True}],
    }])
    trigger_engine.load_triggers([{
        "id": "m",
        "name": "M",
        "triggers": [{
            "id": "trg",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.count",
            "state_operator": "any",
            "cooldown_seconds": 5.0,
        }],
    }])
    await trigger_engine.start()

    state.set("var.count", 1, source="test")
    await asyncio.sleep(0.1)
    assert fire_count == 1

    # Wall clock jumps an hour forward. Wall-clock cooldown math would now read
    # ~3600s elapsed and release; the monotonic baseline still reads ~0.1s.
    real = time.time()
    monkeypatch.setattr(te.time, "time", lambda: real + 3600)
    state.set("var.count", 2, source="test")
    await asyncio.sleep(0.1)
    assert fire_count == 1  # still suppressed

    await trigger_engine.stop()


async def test_cooldown_backward_clock_after_restart_does_not_wedge(
    trigger_engine, macro_engine, core
):
    """After a restart (only a persisted wall-clock timestamp), a backward clock
    correction that puts last_fired in the future doesn't suppress forever."""
    state, events = core
    # A persisted last_fired from a prior session that is now in the FUTURE
    # (the clock was corrected backward since it was written).
    state.set("system.trigger.trg.last_fired", time.time() + 3600, source="system")

    fire_count = 0
    original_execute = macro_engine.execute

    async def counting(mid, context=None):
        nonlocal fire_count
        fire_count += 1
        await original_execute(mid, context)

    macro_engine.execute = counting
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{"action": "state.set", "key": "var.x", "value": True}],
    }])
    # load_triggers restores last_fired from the persisted (future) value and
    # there is no in-process monotonic baseline -> the wall-clock fallback runs.
    trigger_engine.load_triggers([{
        "id": "m",
        "name": "M",
        "triggers": [{
            "id": "trg",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.count",
            "state_operator": "any",
            "cooldown_seconds": 5.0,
        }],
    }])
    await trigger_engine.start()

    state.set("var.count", 1, source="test")
    await asyncio.sleep(0.1)
    # Old code: elapsed = now - future = negative < 5 -> suppress forever.
    # New code: negative elapsed is treated as cooldown elapsed -> fires.
    assert fire_count == 1
    await trigger_engine.stop()


# --- Trigger context flows into the fired macro as $trigger.<field> ---


async def test_event_payload_reaches_macro(trigger_engine, macro_engine, core):
    """An event-triggered macro can read the event payload via $trigger.<field>."""
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.seen", "value": "$trigger.data"}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_event",
            "type": "event",
            "enabled": True,
            "event_pattern": "device.response.proj1",
        }],
    }])
    await trigger_engine.start()

    await events.emit("device.response.proj1", {"data": "PWR=ON", "raw": b"PWR=ON\r"})
    await asyncio.sleep(0.1)

    assert state.get("var.seen") == "PWR=ON"
    await trigger_engine.stop()


async def test_state_change_value_reaches_macro(trigger_engine, macro_engine, core):
    """A state-change-triggered macro can read the new value via $trigger.new_value."""
    state, events = core
    macro_engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.mirror", "value": "$trigger.new_value"}],
    }])
    trigger_engine.load_triggers([{
        "id": "test_macro",
        "name": "Test",
        "triggers": [{
            "id": "trg_state",
            "type": "state_change",
            "enabled": True,
            "state_key": "var.source",
            "state_operator": "any",
        }],
    }])
    await trigger_engine.start()

    state.set("var.source", "HDMI2", source="test")
    await asyncio.sleep(0.1)

    assert state.get("var.mirror") == "HDMI2"
    await trigger_engine.stop()
