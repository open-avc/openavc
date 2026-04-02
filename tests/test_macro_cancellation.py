"""Tests for macro cancellation and cancel_group preemption."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.state_store import StateStore


@pytest.fixture
def state():
    return StateStore()


@pytest.fixture
def events():
    return EventBus()


@pytest.fixture
def devices():
    d = MagicMock()
    d.send_command = AsyncMock()
    return d


@pytest.fixture
def engine(state, events, devices):
    return MacroEngine(state, events, devices)


# ===== cancel() =====


@pytest.mark.asyncio
async def test_cancel_running_macro(engine, events):
    """Cancel a macro that has a delay, verify it stops."""
    cancel_events = []

    async def capture(event, data):
        cancel_events.append(data)

    events.on("macro.cancelled.*", capture)

    engine.load_macros([{
        "id": "slow_macro",
        "name": "Slow Macro",
        "steps": [
            {"action": "delay", "seconds": 10},
            {"action": "state.set", "key": "var.should_not_set", "value": True},
        ],
    }])

    # Start macro in background
    asyncio.create_task(engine.execute("slow_macro"))
    await asyncio.sleep(0.05)  # Let it start

    assert engine.is_macro_running("slow_macro")
    result = await engine.cancel("slow_macro")
    assert result is True

    # Wait for task to finish
    await asyncio.sleep(0.05)

    assert not engine.is_macro_running("slow_macro")
    assert len(cancel_events) == 1
    assert cancel_events[0]["macro_id"] == "slow_macro"


@pytest.mark.asyncio
async def test_cancel_not_running(engine):
    """Cancel a macro that isn't running returns False."""
    result = await engine.cancel("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_cleanup(engine, state):
    """Cancelled macro is removed from _running and _call_stack."""
    engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "delay", "seconds": 10}],
    }])

    asyncio.create_task(engine.execute("test_macro"))
    await asyncio.sleep(0.05)

    await engine.cancel("test_macro")
    await asyncio.sleep(0.05)

    assert "test_macro" not in engine._running
    assert "test_macro" not in engine._call_stack


# ===== cancel_group preemption =====


@pytest.mark.asyncio
async def test_cancel_group_preemption(engine, events, state):
    """Start macro A in group X, then macro B in group X. A should be cancelled."""
    cancel_events = []
    complete_events = []

    async def on_cancel(event, data):
        cancel_events.append(data)

    async def on_complete(event, data):
        complete_events.append(data)

    events.on("macro.cancelled.*", on_cancel)
    events.on("macro.completed.*", on_complete)

    engine.load_macros([
        {
            "id": "system_on",
            "name": "System On",
            "cancel_group": "system_power",
            "steps": [
                {"action": "delay", "seconds": 10},
                {"action": "state.set", "key": "var.system_on_done", "value": True},
            ],
        },
        {
            "id": "system_off",
            "name": "System Off",
            "cancel_group": "system_power",
            "steps": [
                {"action": "state.set", "key": "var.system_off_done", "value": True},
            ],
        },
    ])

    # Start system_on (has 10s delay)
    asyncio.create_task(engine.execute("system_on"))
    await asyncio.sleep(0.05)

    # Start system_off (same cancel_group) - should preempt system_on
    await engine.execute("system_off")
    await asyncio.sleep(0.05)

    # system_on should have been cancelled
    assert any(e["macro_id"] == "system_on" for e in cancel_events)

    # system_off should have completed
    assert any(e["macro_id"] == "system_off" for e in complete_events)

    # system_on's state.set should NOT have run
    assert state.get("var.system_on_done") is None

    # system_off's state.set SHOULD have run
    assert state.get("var.system_off_done") is True


@pytest.mark.asyncio
async def test_cancel_group_different_groups(engine, state):
    """Macros in different cancel groups don't interfere."""
    engine.load_macros([
        {
            "id": "macro_a",
            "name": "Macro A",
            "cancel_group": "group_a",
            "steps": [
                {"action": "delay", "seconds": 10},
                {"action": "state.set", "key": "var.a_done", "value": True},
            ],
        },
        {
            "id": "macro_b",
            "name": "Macro B",
            "cancel_group": "group_b",
            "steps": [
                {"action": "state.set", "key": "var.b_done", "value": True},
            ],
        },
    ])

    asyncio.create_task(engine.execute("macro_a"))
    await asyncio.sleep(0.05)

    # macro_b is in a different group, should NOT cancel macro_a
    await engine.execute("macro_b")
    await asyncio.sleep(0.05)

    assert state.get("var.b_done") is True
    # macro_a should still be running
    assert engine.is_macro_running("macro_a")

    # Clean up
    await engine.cancel("macro_a")
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_cancel_group_no_group(engine, state):
    """Macros without cancel_group are never preempted."""
    engine.load_macros([
        {
            "id": "macro_a",
            "name": "Macro A",
            "steps": [
                {"action": "delay", "seconds": 10},
                {"action": "state.set", "key": "var.a_done", "value": True},
            ],
        },
        {
            "id": "macro_b",
            "name": "Macro B",
            "steps": [
                {"action": "state.set", "key": "var.b_done", "value": True},
            ],
        },
    ])

    asyncio.create_task(engine.execute("macro_a"))
    await asyncio.sleep(0.05)

    await engine.execute("macro_b")
    await asyncio.sleep(0.05)

    assert state.get("var.b_done") is True
    assert engine.is_macro_running("macro_a")

    await engine.cancel("macro_a")
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_cancel_all(engine, state):
    """cancel_all stops all running macros."""
    engine.load_macros([
        {"id": "m1", "name": "M1", "steps": [{"action": "delay", "seconds": 10}]},
        {"id": "m2", "name": "M2", "steps": [{"action": "delay", "seconds": 10}]},
    ])

    asyncio.create_task(engine.execute("m1"))
    asyncio.create_task(engine.execute("m2"))
    await asyncio.sleep(0.05)

    assert engine.is_macro_running("m1")
    assert engine.is_macro_running("m2")

    await engine.cancel_all()
    await asyncio.sleep(0.05)

    assert not engine.is_macro_running("m1")
    assert not engine.is_macro_running("m2")


# ===== Project loader: cancel_group field =====


def test_macro_config_cancel_group():
    from server.core.project_loader import MacroConfig
    macro = MacroConfig(id="test", name="Test", cancel_group="system_power")
    assert macro.cancel_group == "system_power"


def test_macro_config_cancel_group_default():
    from server.core.project_loader import MacroConfig
    macro = MacroConfig(id="test", name="Test")
    assert macro.cancel_group is None
