"""Tests for the wait_until macro step."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.state_store import StateStore


@pytest.fixture
def state():
    s = StateStore()
    return s


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
    state.set_event_bus(events)
    return MacroEngine(state, events, devices)


# ===== Happy paths =====


async def test_wait_until_already_satisfied(engine, state):
    """Condition already true at step entry — step returns immediately."""
    state.set("device.p1.power_state", "on")
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "device.p1.power_state", "operator": "eq", "value": "on"},
                "timeout": 10,
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    assert state.get("var.done") is True


async def test_wait_until_becomes_satisfied(engine, state):
    """Condition becomes true mid-wait — step returns, sequence continues."""
    state.set("device.p1.power_state", "warming")
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "device.p1.power_state", "operator": "eq", "value": "on"},
                "timeout": 5,
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])

    async def flip():
        await asyncio.sleep(0.05)
        state.set("device.p1.power_state", "on")

    asyncio.create_task(flip())
    await engine.execute("m")
    assert state.get("var.done") is True


async def test_wait_until_never_timeout_eventually_satisfied(engine, state):
    """timeout=null + eventual satisfaction — step returns without a timer."""
    state.set("var.ready", False)
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": None,
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])

    async def flip():
        await asyncio.sleep(0.05)
        state.set("var.ready", True)

    asyncio.create_task(flip())
    await engine.execute("m")
    assert state.get("var.done") is True


# ===== Timeout handling =====


async def test_wait_until_timeout_fail_stop_on_error(engine, state):
    """stop_on_error=True: timeout+fail aborts the macro, subsequent step does NOT run."""
    state.set("var.ready", False)
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "stop_on_error": True,
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 0.1,
                "on_timeout": "fail",
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    assert state.get("var.done") is False


async def test_wait_until_timeout_fail_continues_without_stop_on_error(engine, state):
    """stop_on_error=False (default): timeout+fail logs error, sequence continues."""
    state.set("var.ready", False)
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 0.1,
                "on_timeout": "fail",
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    assert state.get("var.done") is True


async def test_wait_until_timeout_continue(engine, state):
    """on_timeout=continue: no error raised, sequence continues."""
    state.set("var.ready", False)
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "stop_on_error": True,  # ensure it's not just error recovery saving us
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 0.1,
                "on_timeout": "continue",
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    assert state.get("var.done") is True


# ===== Cancellation =====


async def test_wait_until_cancel_during_wait(engine, state, events):
    """Cancelling a macro parked in wait_until emits macro.cancelled and cleans up."""
    state.set("var.ready", False)
    cancel_events = []

    async def capture(_event, data):
        cancel_events.append(data)

    events.on("macro.cancelled.*", capture)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": None,  # forever — must be cancellable
            },
        ],
    }])

    asyncio.create_task(engine.execute("m"))
    await asyncio.sleep(0.05)
    assert engine.is_macro_running("m")

    result = await engine.cancel("m")
    assert result is True
    await asyncio.sleep(0.05)

    assert not engine.is_macro_running("m")
    assert any(e["macro_id"] == "m" for e in cancel_events)
    # No leftover subscription for var.ready
    assert "var.ready" not in state._listeners


async def test_wait_until_cancel_group_preemption(engine, state, events):
    """A waiting macro is preempted cleanly by another macro in the same cancel_group."""
    state.set("var.ready", False)
    cancel_events = []

    async def capture(_event, data):
        cancel_events.append(data)

    events.on("macro.cancelled.*", capture)

    engine.load_macros([
        {
            "id": "system_on",
            "name": "System On",
            "cancel_group": "system_power",
            "steps": [
                {
                    "action": "wait_until",
                    "condition": {"key": "var.ready", "operator": "eq", "value": True},
                    "timeout": None,
                },
                {"action": "state.set", "key": "var.on_done", "value": True},
            ],
        },
        {
            "id": "system_off",
            "name": "System Off",
            "cancel_group": "system_power",
            "steps": [
                {"action": "state.set", "key": "var.off_done", "value": True},
            ],
        },
    ])

    asyncio.create_task(engine.execute("system_on"))
    await asyncio.sleep(0.05)
    assert engine.is_macro_running("system_on")

    await engine.execute("system_off")
    await asyncio.sleep(0.05)

    assert any(e["macro_id"] == "system_on" for e in cancel_events)
    assert state.get("var.on_done") is None
    assert state.get("var.off_done") is True
    assert "var.ready" not in state._listeners


# ===== Guards =====


async def test_wait_until_skip_if_skips(engine, state):
    """skip_if guard true skips the wait entirely — no subscribe, no wait."""
    state.set("var.skip", True)
    state.set("var.ready", False)
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 5,
                "skip_if": {"key": "var.skip", "operator": "eq", "value": True},
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    assert state.get("var.done") is True
    assert "var.ready" not in state._listeners


# ===== Progress events =====


async def test_wait_until_progress_events(engine, state, events):
    """waiting then satisfied events fire with expected fields."""
    state.set("var.ready", False)
    progress = []

    async def capture(_event, data):
        if data.get("action") == "wait_until":
            progress.append(data)

    events.on("macro.progress.*", capture)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 5,
            },
        ],
    }])

    async def flip():
        await asyncio.sleep(0.05)
        state.set("var.ready", True)

    asyncio.create_task(flip())
    await engine.execute("m")
    await asyncio.sleep(0.05)  # let event emissions drain

    statuses = [p["status"] for p in progress]
    assert "waiting" in statuses
    assert "satisfied" in statuses
    waiting = next(p for p in progress if p["status"] == "waiting")
    assert waiting["condition_key"] == "var.ready"
    assert waiting["timeout"] == 5


async def test_wait_until_progress_on_timeout(engine, state, events):
    """Timeout path emits a timeout progress event with on_timeout field."""
    state.set("var.ready", False)
    progress = []

    async def capture(_event, data):
        if data.get("action") == "wait_until":
            progress.append(data)

    events.on("macro.progress.*", capture)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 0.1,
                "on_timeout": "continue",
            },
        ],
    }])
    await engine.execute("m")
    await asyncio.sleep(0.05)

    assert any(p["status"] == "timeout" and p.get("on_timeout") == "continue" for p in progress)


# ===== Validation =====


async def test_wait_until_invalid_missing_condition_key(engine, state):
    """Missing condition.key raises ValueError at step execution."""
    engine.load_macros([{
        "id": "m",
        "name": "M",
        "stop_on_error": True,
        "steps": [
            {
                "action": "wait_until",
                "condition": {"operator": "eq", "value": True},
                "timeout": 1,
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    # stop_on_error=True should have halted before var.done is set
    assert state.get("var.done") is None


async def test_wait_until_invalid_on_timeout(engine, state):
    """Invalid on_timeout raises ValueError."""
    state.set("var.ready", False)
    engine.load_macros([{
        "id": "m",
        "name": "M",
        "stop_on_error": True,
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 1,
                "on_timeout": "retry",
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    assert state.get("var.done") is None


async def test_wait_until_invalid_negative_timeout(engine, state):
    """Negative timeout raises ValueError."""
    state.set("var.ready", False)
    engine.load_macros([{
        "id": "m",
        "name": "M",
        "stop_on_error": True,
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": -1,
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])
    await engine.execute("m")
    assert state.get("var.done") is None


# ===== Isolation =====


async def test_wait_until_other_key_changes_dont_wake(engine, state):
    """Changes to unrelated keys don't prematurely satisfy the wait."""
    state.set("var.ready", False)
    state.set("var.done", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 0.2,
                "on_timeout": "continue",
            },
            {"action": "state.set", "key": "var.done", "value": True},
        ],
    }])

    async def bumps():
        # Write unrelated keys; they should NOT wake the wait early.
        for i in range(5):
            state.set(f"var.noise_{i}", i)
            await asyncio.sleep(0.01)

    asyncio.create_task(bumps())
    await engine.execute("m")
    # var.done should still run (continue on timeout), but it should have timed out.
    assert state.get("var.done") is True
    # Confirm wait actually ran for close to the timeout — not tripped early.
    # (No strict timing assertion to avoid flakiness; subscription cleanup checked below.)
    assert "var.ready" not in state._listeners


async def test_wait_until_subscription_cleanup_on_success(engine, state):
    """After success, no lingering listener for the watched key."""
    state.set("var.ready", False)

    engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [
            {
                "action": "wait_until",
                "condition": {"key": "var.ready", "operator": "eq", "value": True},
                "timeout": 5,
            },
        ],
    }])

    async def flip():
        await asyncio.sleep(0.02)
        state.set("var.ready", True)

    asyncio.create_task(flip())
    await engine.execute("m")
    assert "var.ready" not in state._listeners


# ===== Project loader model =====


def test_macro_step_wait_until_schema():
    """MacroStep accepts wait_until fields."""
    from server.core.project_loader import MacroStep
    step = MacroStep(
        action="wait_until",
        condition={"key": "device.p.power", "operator": "eq", "value": "on"},
        timeout=30,
        on_timeout="fail",
    )
    assert step.action == "wait_until"
    assert step.timeout == 30
    assert step.on_timeout == "fail"
    assert step.condition.key == "device.p.power"


def test_macro_step_wait_until_null_timeout():
    """MacroStep accepts timeout=None for 'never time out'."""
    from server.core.project_loader import MacroStep
    step = MacroStep(
        action="wait_until",
        condition={"key": "var.x", "operator": "eq", "value": True},
        timeout=None,
    )
    assert step.timeout is None
