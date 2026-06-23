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
    """Cancelled macro is removed from _running."""
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


# ===== L-100: cancel()/cancel_all() surface tasks that ignore cancellation =====


async def _stubborn_invocation():
    """A macro task that swallows the first cancellation and keeps running,
    simulating a step that doesn't honour CancelledError within the grace
    period (tight loop, or an except that re-awaits)."""
    try:
        await asyncio.sleep(10)
    except asyncio.CancelledError:
        # Ignore the cancel and keep going — this is what leaves AV output
        # in flight past a "cancelled" result.
        await asyncio.sleep(10)


@pytest.mark.asyncio
async def test_cancel_warns_when_task_does_not_stop(engine, caplog, monkeypatch):
    """cancel() must not report success silently when a task ignores the
    cancellation — it inspects the still-pending set and logs a warning."""
    import logging

    from server.core import macro_engine as me

    monkeypatch.setattr(me, "_CANCEL_GRACE_SECONDS", 0.1)

    task = asyncio.create_task(_stubborn_invocation())
    await asyncio.sleep(0.05)  # let it reach its first await before cancel
    engine._running["stubborn"] = {task}

    with caplog.at_level(logging.WARNING):
        result = await engine.cancel("stubborn")

    assert result is True
    assert any(
        "did not stop within" in r.message and "stubborn" in r.message
        for r in caplog.records
    ), f"expected a warning about the unstopped invocation; got {caplog.records}"

    # Hard-stop the stubborn task so it doesn't leak into other tests.
    task.cancel()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_cancel_all_warns_when_task_does_not_stop(engine, caplog, monkeypatch):
    """cancel_all() (shutdown path) must surface tasks still running past the
    grace period rather than swallowing the asyncio.wait result."""
    import logging

    from server.core import macro_engine as me

    monkeypatch.setattr(me, "_CANCEL_GRACE_SECONDS", 0.1)

    task = asyncio.create_task(_stubborn_invocation())
    await asyncio.sleep(0.05)  # let it reach its first await before cancel
    engine._running["stubborn"] = {task}

    with caplog.at_level(logging.WARNING):
        await engine.cancel_all()

    assert any(
        "did not stop within" in r.message for r in caplog.records
    ), f"expected a shutdown warning about the unstopped task; got {caplog.records}"

    task.cancel()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_cancel_no_warning_when_task_stops_cleanly(engine, caplog, monkeypatch):
    """A well-behaved macro that honours cancellation produces no warning."""
    import logging

    from server.core import macro_engine as me

    monkeypatch.setattr(me, "_CANCEL_GRACE_SECONDS", 0.5)

    engine.load_macros([{
        "id": "clean",
        "name": "Clean",
        "steps": [{"action": "delay", "seconds": 10}],
    }])
    asyncio.create_task(engine.execute("clean"))
    await asyncio.sleep(0.05)

    with caplog.at_level(logging.WARNING):
        result = await engine.cancel("clean")

    assert result is True
    assert not any("did not stop within" in r.message for r in caplog.records)


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


# ===== A49 / A51 / A53 — concurrency =====


@pytest.mark.asyncio
async def test_true_concurrent_cancel_group_start(engine, state):
    """A49 + A53: Two macros in the same cancel_group started in a single
    event-loop tick (asyncio.gather) must NOT both cancel each other.
    Exactly one wins and runs to completion.
    """
    engine.load_macros([
        {
            "id": "system_on",
            "name": "System On",
            "cancel_group": "system_power",
            "steps": [
                {"action": "delay", "seconds": 0.1},
                {"action": "state.set", "key": "var.system_on_done", "value": True},
            ],
        },
        {
            "id": "system_off",
            "name": "System Off",
            "cancel_group": "system_power",
            "steps": [
                {"action": "delay", "seconds": 0.1},
                {"action": "state.set", "key": "var.system_off_done", "value": True},
            ],
        },
    ])

    # True concurrent start — both coroutines enter execute() before either
    # reaches its register-and-preempt section.
    await asyncio.gather(
        engine.execute("system_on"),
        engine.execute("system_off"),
        return_exceptions=True,
    )

    # Exactly one terminal state.set must have fired. Pre-fix, both
    # macros could cancel each other and neither's state.set would run
    # (assertion would fail on "no terminal step ran").
    on_done = state.get("var.system_on_done")
    off_done = state.get("var.system_off_done")
    assert (on_done is True) ^ (off_done is True), (
        f"Expected exactly one terminal state.set; got on={on_done}, off={off_done}"
    )


@pytest.mark.asyncio
async def test_repeated_concurrent_same_macro_id_all_tracked(engine, state):
    """A51 + A53: The same macro fired N times concurrently leaves every
    invocation individually trackable. cancel(macro_id) must stop all of
    them; none can become an orphan that runs to completion past cancel.
    """
    engine.load_macros([{
        "id": "spammed",
        "name": "Spammed",
        "steps": [
            {"action": "delay", "seconds": 10},
            {"action": "state.set", "key": "var.spammed_done", "value": True},
        ],
    }])

    # Fire 10 concurrent invocations of the same macro_id.
    tasks = [asyncio.create_task(engine.execute("spammed")) for _ in range(10)]
    await asyncio.sleep(0.05)  # let them all register

    assert engine.is_macro_running("spammed")
    # All 10 must be tracked, not just the most recent (overwrite bug).
    assert len(engine._running["spammed"]) == 10

    # One cancel() call must stop every invocation.
    result = await engine.cancel("spammed")
    assert result is True
    await asyncio.sleep(0.05)

    assert not engine.is_macro_running("spammed")
    for t in tasks:
        assert t.done()
    # Terminal state must NOT have run for any invocation.
    assert state.get("var.spammed_done") is None


@pytest.mark.asyncio
async def test_cancel_group_drains_before_new_macro_proceeds(engine, state, devices):
    """A50: preempted macros must finish unwinding their in-flight
    transport awaits before the new macro starts sending bytes. The drain
    is implemented via asyncio.wait, not a single sleep(0) yield.
    """
    send_log: list[str] = []

    async def slow_send(device_id, command, params=None):
        # Long, awaitable send: gives the cancel a real window to land
        # mid-flight.
        send_log.append(f"start:{command}")
        try:
            await asyncio.sleep(0.2)
            send_log.append(f"end:{command}")
        except asyncio.CancelledError:
            send_log.append(f"cancelled:{command}")
            raise

    devices.send_command = slow_send

    engine.load_macros([
        {
            "id": "system_on",
            "name": "System On",
            "cancel_group": "system_power",
            "steps": [
                {"action": "device.command", "device_id": "proj", "command": "power_on"},
                {"action": "state.set", "key": "var.system_on_terminal", "value": True},
            ],
        },
        {
            "id": "system_off",
            "name": "System Off",
            "cancel_group": "system_power",
            "steps": [
                {"action": "device.command", "device_id": "proj", "command": "power_off"},
                {"action": "state.set", "key": "var.system_off_terminal", "value": True},
            ],
        },
    ])

    asyncio.create_task(engine.execute("system_on"))
    # Let system_on's slow_send start ('start:power_on' logged, mid-await).
    await asyncio.sleep(0.05)
    assert "start:power_on" in send_log
    assert "end:power_on" not in send_log

    # Now preempt with system_off. system_on's send must finish unwinding
    # (cancelled:power_on logged) BEFORE system_off's send fires.
    await engine.execute("system_off")
    await asyncio.sleep(0.05)

    # Find ordering of "cancelled:power_on" vs "start:power_off".
    assert "cancelled:power_on" in send_log
    assert "start:power_off" in send_log
    cancel_idx = send_log.index("cancelled:power_on")
    new_start_idx = send_log.index("start:power_off")
    assert cancel_idx < new_start_idx, (
        f"new send started before old send finished unwinding: {send_log}"
    )
