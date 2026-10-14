"""Macro-level overlap and cooldown guards, enforced at the engine chokepoint.

These exercise the guard directly through ``MacroEngine.execute()`` — the path a
script (``macros.execute()``), REST, the AI tool, a UI press, or another macro
takes. That path has no trigger in front of it, so before the engine grew its
own guard it ran with no throttle at all. Each test that asserts a skip/queue is
red against that pre-guard behaviour (the second run would proceed).
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

import openavc.core.macro_engine as macro_engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore


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


def _count_starts(events, macro_id):
    """Subscribe a counter to a macro's start event; return a mutable holder.

    A skipped/blocked invocation returns before ``macro.started`` is emitted,
    so the count is exactly the number of accepted starts."""
    holder = {"n": 0}

    async def _on_start(event, data):
        holder["n"] += 1

    events.on(f"macro.started.{macro_id}", _on_start)
    return holder


@pytest.mark.asyncio
async def test_overlap_skip_drops_concurrent_run(engine, events):
    """overlap=skip: a second run while one is in flight is dropped."""
    starts = _count_starts(events, "sys_on")
    engine.load_macros([{
        "id": "sys_on",
        "name": "System On",
        "overlap": "skip",
        "steps": [
            {"action": "delay", "seconds": 0.2},
            {"action": "state.set", "key": "var.count", "value": 1},
        ],
    }])

    first = asyncio.create_task(engine.execute("sys_on"))
    await asyncio.sleep(0.05)  # let the first run reach its delay
    assert engine.is_macro_running("sys_on")

    # Second call goes straight through execute() like a script would. It must
    # return immediately (not block for the first to finish) and not start.
    await asyncio.wait_for(engine.execute("sys_on"), timeout=0.1)
    assert starts["n"] == 1
    assert engine.is_macro_running("sys_on")  # the first is still going

    await first
    assert starts["n"] == 1


@pytest.mark.asyncio
async def test_overlap_allow_is_the_default_and_permits_concurrency(engine, events):
    """No overlap field = allow = historic behaviour: concurrent runs stack."""
    starts = _count_starts(events, "burst")
    engine.load_macros([{
        "id": "burst",
        "name": "Burst",
        "steps": [{"action": "delay", "seconds": 0.2}],
    }])

    first = asyncio.create_task(engine.execute("burst"))
    second = asyncio.create_task(engine.execute("burst"))
    await asyncio.sleep(0.05)

    assert starts["n"] == 2  # both accepted, running concurrently
    assert len(engine._running.get("burst", set())) == 2

    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_cooldown_drops_rapid_second_run(engine, events):
    """cooldown_seconds: a run within the window after a start is dropped."""
    starts = _count_starts(events, "quick")
    engine.load_macros([{
        "id": "quick",
        "name": "Quick",
        "cooldown_seconds": 100,
        "steps": [{"action": "state.set", "key": "var.x", "value": 1}],
    }])

    await engine.execute("quick")   # accepted, records the cooldown start
    await engine.execute("quick")   # within the window -> dropped
    assert starts["n"] == 1


@pytest.mark.asyncio
async def test_cooldown_allows_again_after_window(engine, events):
    """A cooldown clears once its window elapses."""
    starts = _count_starts(events, "quick")
    engine.load_macros([{
        "id": "quick",
        "name": "Quick",
        "cooldown_seconds": 0.05,
        "steps": [{"action": "state.set", "key": "var.x", "value": 1}],
    }])

    await engine.execute("quick")
    await asyncio.sleep(0.1)        # let the cooldown elapse
    await engine.execute("quick")
    assert starts["n"] == 2


@pytest.mark.asyncio
async def test_overlap_queue_serializes_runs(engine, events, monkeypatch):
    """overlap=queue: a second run waits for the first, then proceeds."""
    monkeypatch.setattr(macro_engine, "_QUEUE_POLL_SECONDS", 0.02)
    starts = _count_starts(events, "seq")
    engine.load_macros([{
        "id": "seq",
        "name": "Sequential",
        "overlap": "queue",
        "steps": [{"action": "delay", "seconds": 0.15}],
    }])

    first = asyncio.create_task(engine.execute("seq"))
    await asyncio.sleep(0.03)
    second = asyncio.create_task(engine.execute("seq"))
    await asyncio.sleep(0.05)

    # Second is still queued behind the running first.
    assert starts["n"] == 1

    await asyncio.gather(first, second)
    assert starts["n"] == 2  # second ran after the first finished


@pytest.mark.asyncio
async def test_cooldown_not_updated_when_blocked(engine, events):
    """A blocked start must not slide the cooldown baseline forward."""
    starts = _count_starts(events, "quick")
    engine.load_macros([{
        "id": "quick",
        "name": "Quick",
        "cooldown_seconds": 0.15,
        "steps": [{"action": "state.set", "key": "var.x", "value": 1}],
    }])

    await engine.execute("quick")   # t0: accepted
    await asyncio.sleep(0.08)
    await engine.execute("quick")   # blocked (0.08 < 0.15), baseline stays t0
    await asyncio.sleep(0.1)        # now 0.18 since t0 -> window elapsed
    await engine.execute("quick")   # accepted again
    assert starts["n"] == 2


# --- A run that has ended no longer counts as running ------------------------


async def _end_a_run(engine, monkeypatch, end):
    """Run ``acme_warm_up`` to the given end: completed, cancelled or error."""
    engine.load_macros([{
        "id": "acme_warm_up",
        "name": "Acme Warm Up",
        "steps": [{"action": "delay", "seconds": 5 if end == "cancelled" else 0}],
    }])
    if end == "error":
        monkeypatch.setattr(
            engine, "execute_steps", AsyncMock(side_effect=RuntimeError("acme fault")),
        )
    run = asyncio.create_task(engine.execute("acme_warm_up"))
    if end == "cancelled":
        await asyncio.sleep(0.05)
        assert await engine.cancel("acme_warm_up")
    return await run


@pytest.mark.asyncio
@pytest.mark.parametrize("end, outcome", [
    ("completed", "completed"), ("cancelled", "cancelled"), ("error", "failed"),
])
async def test_whoever_hears_a_run_end_sees_it_stopped(engine, events, monkeypatch, end, outcome):
    """The end is announced after the run stops counting as running, so a
    handler on that event, and anything that runs while one yields, sees the
    macro stopped."""
    seen = []

    async def on_end(event, data):
        seen.append(engine.is_macro_running("acme_warm_up"))
        await asyncio.sleep(0)
        seen.append(engine.is_macro_running("acme_warm_up"))

    events.on(f"macro.{end}.acme_warm_up", on_end)
    assert await _end_a_run(engine, monkeypatch, end) == outcome
    assert seen == [False, False]


@pytest.mark.asyncio
async def test_a_macro_started_again_when_it_ends_is_not_turned_away(engine, events):
    """overlap=skip guards a run in flight, not one that has finished: a
    handler on a run's own end (an event trigger, a script) that starts it
    again gets a run."""
    starts = _count_starts(events, "acme_cycle")
    engine.load_macros([{
        "id": "acme_cycle",
        "name": "Acme Cycle",
        "overlap": "skip",
        "steps": [{"action": "state.set", "key": "var.acme_x", "value": 1}],
    }])
    asked, again = [], []

    async def start_again(event, data):
        if asked:  # once: the second run's own end must not start a third
            return
        asked.append(True)
        again.append(await engine.execute("acme_cycle"))

    events.on("macro.completed.acme_cycle", start_again)
    assert await engine.execute("acme_cycle") == "completed"
    assert again == ["completed"]
    assert starts["n"] == 2
