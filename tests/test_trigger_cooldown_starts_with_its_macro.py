"""A trigger's cooldown starts when its macro starts, never on a fire the
macro refused.

The trigger stamped its cooldown before the macro's own guards had their
say, so a fire the macro turned away (its own Cooldown, its Overlap set to
Skip under a trigger set to Allow, a queued run that waited too long) started
the trigger's cooldown with nothing run, and the next real event was dropped
as ``trigger.skipped cooldown``. The stamp is now written by the macro engine's
start callback, the moment the run takes its slot, as the macro stamps its
own cooldown; a refused fire leaves the stamp where it was and records the
macro's reason on the trigger's last run, which the trigger card reads.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from openavc.core import macro_engine as macro_engine_module
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_text import skip_reason
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine

GOOD_STEP = {"action": "state.set", "key": "var.ran", "value": True}
WAIT_STEP = {
    "action": "wait_until",
    "condition": {"key": "var.never", "operator": "eq", "value": True},
    "timeout": None,
}
STAMP_KEY = "system.trigger.trg.last_fired"


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def macros(core):
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


@pytest.fixture
def triggers(core, macros):
    state, events = core
    return TriggerEngine(state, events, macros)


def _macro(*steps, **extra) -> dict:
    return {"id": "m", "name": "m", "steps": list(steps), **extra}


def _with_trigger(macro: dict, **trigger) -> dict:
    return {
        **macro,
        "triggers": [{
            "id": "trg", "type": "schedule", "enabled": True,
            "cooldown_seconds": 30, **trigger,
        }],
    }


def _load(macros, triggers, macro: dict, **trigger) -> None:
    project = [_with_trigger(macro, **trigger)]
    macros.load_macros(project)
    triggers.load_triggers(project)


async def _fire(triggers) -> None:
    """The automation path a schedule, state change or event reaches."""
    await triggers._fire_macro(triggers._triggers["trg"], {})


def _status(triggers) -> dict:
    return next(t for t in triggers.list_triggers() if t["id"] == "trg")


def _completed(events) -> list[dict]:
    seen: list[dict] = []
    events.on("trigger.completed", lambda _e, p: seen.append(p))
    return seen


def _assert_no_cooldown_started(triggers, state) -> None:
    assert triggers._triggers["trg"].last_fired == 0
    assert "trg" not in triggers._last_fired_monotonic
    assert state.get(STAMP_KEY) is None
    assert _status(triggers)["last_fired"] is None


async def _cooldown_skips_next_fire(triggers, events) -> bool:
    """Whether the trigger's own cooldown turns the next fire away."""
    skipped: list[str] = []
    events.on("trigger.skipped", lambda _e, p: skipped.append(p["code"]))
    triggers._initiate_fire(triggers._triggers["trg"], {})
    await asyncio.sleep(0.05)
    return "cooldown" in skipped


# ---------------------------------------------------------------------------
# The three refusals the macro makes: no cooldown, the macro's reason recorded
# ---------------------------------------------------------------------------

async def test_a_fire_the_macros_cooldown_refuses_starts_no_cooldown(
    triggers, macros, core,
):
    state, events = core
    _load(macros, triggers, _macro(GOOD_STEP, cooldown_seconds=60), overlap="allow")
    assert await macros.execute("m") == "completed"   # the macro's cooldown is running
    state.set("var.ran", None)
    seen = _completed(events)

    await _fire(triggers)

    _assert_no_cooldown_started(triggers, state)
    assert state.get("var.ran") is None
    reason = skip_reason("macro_cooldown", seconds=60)
    status = _status(triggers)
    assert status["last_outcome"] == "skipped"
    assert status["last_reason"] == reason
    assert seen[-1]["outcome"] == "skipped"
    assert seen[-1]["code"] == reason["code"]
    assert seen[-1]["message"] == reason["message"]
    assert not await _cooldown_skips_next_fire(triggers, events)


async def test_a_fire_the_macros_skip_overlap_refuses_starts_no_cooldown(
    triggers, macros, core,
):
    state, events = core
    _load(macros, triggers, _macro(WAIT_STEP, overlap="skip"), overlap="allow")
    running = macros.start("m")
    await asyncio.sleep(0.05)
    try:
        await _fire(triggers)

        _assert_no_cooldown_started(triggers, state)
        assert _status(triggers)["last_reason"] == skip_reason("macro_overlap")
        assert not await _cooldown_skips_next_fire(triggers, events)
    finally:
        await macros.cancel("m")
        await asyncio.gather(running, return_exceptions=True)


async def test_a_fire_whose_queued_run_timed_out_starts_no_cooldown(
    triggers, macros, core, monkeypatch,
):
    state, events = core
    monkeypatch.setattr(macro_engine_module, "_QUEUE_MAX_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(macro_engine_module, "_QUEUE_POLL_SECONDS", 0.02)
    _load(macros, triggers, _macro(WAIT_STEP, overlap="queue"), overlap="allow")
    running = macros.start("m")
    await asyncio.sleep(0.05)
    try:
        await _fire(triggers)

        _assert_no_cooldown_started(triggers, state)
        assert _status(triggers)["last_reason"] == skip_reason(
            "macro_queue_timeout", seconds=0.1,
        )
    finally:
        await macros.cancel("m")
        await asyncio.gather(running, return_exceptions=True)


# ---------------------------------------------------------------------------
# A run that starts stamps it, at the start, and carries no reason
# ---------------------------------------------------------------------------

async def test_a_run_that_starts_starts_the_cooldown_as_it_starts(
    triggers, macros, core,
):
    """Stamped when the run takes its slot, so the cooldown holds while the
    macro is still running, as before."""
    state, events = core
    _load(macros, triggers, _macro(WAIT_STEP))
    fire = asyncio.create_task(_fire(triggers))
    await asyncio.sleep(0.05)
    try:
        assert triggers._triggers["trg"].last_fired > 0
        assert "trg" in triggers._last_fired_monotonic
        assert state.get(STAMP_KEY) == triggers._triggers["trg"].last_fired
    finally:
        await macros.cancel("m")
        await fire
    assert _status(triggers)["last_reason"] is None


async def test_a_clean_run_after_a_refusal_clears_the_reason(triggers, macros, core):
    state, events = core
    _load(macros, triggers, _macro(GOOD_STEP, cooldown_seconds=60), overlap="allow")
    await macros.execute("m")
    await _fire(triggers)
    assert _status(triggers)["last_reason"] is not None

    _load(macros, triggers, _macro(GOOD_STEP), overlap="allow")   # cooldown taken off
    seen = _completed(events)
    await _fire(triggers)

    status = _status(triggers)
    assert status["last_outcome"] == "completed"
    assert status["last_reason"] is None
    assert "code" not in seen[-1]
    assert status["last_fired"] is not None


async def test_the_record_keeps_its_own_time(triggers, macros, core):
    """A refused fire moves no stamp, so the card cannot date the last run
    from ``last_fired``: the record says when it was written."""
    state, events = core
    _load(macros, triggers, _macro(GOOD_STEP, cooldown_seconds=60), overlap="allow")
    await macros.execute("m")
    assert _status(triggers)["last_run_at"] is None

    await _fire(triggers)

    status = _status(triggers)
    assert status["last_fired"] is None
    assert isinstance(status["last_run_at"], float)


async def test_the_reason_survives_a_project_edit(triggers, macros, core):
    _load(macros, triggers, _macro(GOOD_STEP, cooldown_seconds=60), overlap="allow")
    await macros.execute("m")
    await _fire(triggers)

    triggers.load_triggers([_with_trigger(_macro(GOOD_STEP), overlap="allow")])

    assert _status(triggers)["last_reason"] == skip_reason("macro_cooldown", seconds=60)


# ---------------------------------------------------------------------------
# A deleted trigger takes its stamp with it
# ---------------------------------------------------------------------------

async def test_a_trigger_that_survives_an_edit_keeps_its_cooldown(triggers, macros, core):
    state, events = core
    _load(macros, triggers, _macro(GOOD_STEP))
    await _fire(triggers)

    _load(macros, triggers, _macro(GOOD_STEP))

    assert state.get(STAMP_KEY) == triggers._triggers["trg"].last_fired > 0
    assert await _cooldown_skips_next_fire(triggers, events)


async def test_a_reused_id_does_not_inherit_the_deleted_triggers_cooldown(
    triggers, macros, core,
):
    """A trigger deleted and later added again under the same id is a new
    trigger: it does not wait out the old one's cooldown, and the old one's
    stamp is gone from the state as soon as the old one is."""
    state, events = core
    _load(macros, triggers, _macro(GOOD_STEP))
    await _fire(triggers)

    macros.load_macros([_macro(GOOD_STEP)])
    triggers.load_triggers([_macro(GOOD_STEP)])        # the trigger deleted
    assert STAMP_KEY not in state.snapshot()

    _load(macros, triggers, _macro(GOOD_STEP))         # another, under its id

    _assert_no_cooldown_started(triggers, state)
    assert not await _cooldown_skips_next_fire(triggers, events)


async def test_only_the_deleted_triggers_stamp_goes(triggers, macros, core):
    """Each id is judged on its own, a dotted one included, and a key the
    trigger engine does not write is left alone."""
    state, _events = core
    both = {
        **_macro(GOOD_STEP),
        "triggers": [
            {"id": "trg", "type": "schedule", "cooldown_seconds": 30},
            {"id": "a.b", "type": "schedule", "cooldown_seconds": 30},
        ],
    }
    macros.load_macros([both])
    triggers.load_triggers([both])
    for trigger_id in ("trg", "a.b"):
        await triggers._fire_macro(triggers._triggers[trigger_id], {})
    state.set("system.trigger_note", "kept", source="system")

    triggers.load_triggers([{**both, "triggers": both["triggers"][1:]}])

    assert STAMP_KEY not in state.snapshot()
    assert state.get("system.trigger.a.b.last_fired") == triggers._triggers["a.b"].last_fired
    assert state.get("system.trigger_note") == "kept"


# ---------------------------------------------------------------------------
# The start callback, at the macro engine (MacroEngine.starts)
# ---------------------------------------------------------------------------

async def test_a_run_that_starts_calls_its_callback_once_as_it_starts(macros, core):
    _state, events = core
    order: list[str] = []
    events.on("macro.started.m", lambda _e, _p: order.append("started"))
    macros.load_macros([_macro(GOOD_STEP)])

    with macros.starts.on_next_start(lambda: order.append("on_start")):
        assert await macros.execute("m") == "completed"

    assert order == ["on_start", "started"]


async def test_a_refused_start_drops_its_callback_and_keeps_its_reason(macros, core):
    macros.load_macros([_macro(GOOD_STEP, cooldown_seconds=60)])
    await macros.execute("m")
    called: list[bool] = []

    with macros.starts.on_next_start(lambda: called.append(True)):
        assert await macros.execute("m") == "skipped"
    macros.load_macros([_macro(GOOD_STEP)])
    assert await macros.execute("m") == "completed"   # a later start, same task

    assert called == []
    assert macros.starts.take_refusal() == skip_reason("macro_cooldown", seconds=60)
    assert macros.starts.take_refusal() is None


async def test_a_callback_no_start_took_is_not_left_behind(macros, core):
    """A macro that does not exist raises before any start: the callback goes
    with the block, so the task's next start stamps nothing."""
    macros.load_macros([_macro(GOOD_STEP)])
    called: list[bool] = []

    with pytest.raises(ValueError):
        with macros.starts.on_next_start(lambda: called.append(True)):
            await macros.execute("ghost")
    assert await macros.execute("m") == "completed"

    assert called == []


async def test_a_sub_macro_never_calls_the_callback(macros, core):
    macros.load_macros([
        _macro({"action": "macro", "macro": "sub"}),
        {"id": "sub", "name": "sub", "steps": [GOOD_STEP]},
    ])
    called: list[bool] = []

    with macros.starts.on_next_start(lambda: called.append(True)):
        assert await macros.execute("m") == "completed"

    assert called == [True]


async def test_a_callback_that_raises_does_not_stop_the_run(macros, core):
    state, _events = core
    macros.load_macros([_macro(GOOD_STEP)])

    def broken() -> None:
        raise RuntimeError("stamp failed")

    with macros.starts.on_next_start(broken):
        assert await macros.execute("m") == "completed"
    assert state.get("var.ran") is True


async def test_a_refused_sub_macro_is_not_the_triggers_reason(triggers, macros, core):
    """A Run Macro step whose macro refuses files its reason under the same
    task; the trigger's run started, so its last run carries no reason."""
    sub = {"id": "sub", "name": "sub", "steps": [GOOD_STEP], "cooldown_seconds": 60}
    project = [_with_trigger(_macro({"action": "macro", "macro": "sub"})), sub]
    macros.load_macros(project)
    triggers.load_triggers(project)
    await macros.execute("sub")   # the sub-macro's cooldown is running

    await _fire(triggers)

    status = _status(triggers)
    assert status["last_outcome"] != "skipped"
    assert status["last_reason"] is None
    assert status["last_fired"] is not None

async def test_a_fire_waiting_in_its_macros_queue_has_started_no_cooldown(
    triggers, macros, core, monkeypatch,
):
    """The cooldown counts from a start, so while one fire waits in the
    macro's queue a second is not turned away by it: both wait, and both
    runs start, one after the other."""
    state, events = core
    monkeypatch.setattr(macro_engine_module, "_QUEUE_POLL_SECONDS", 0.02)
    gated = {
        "action": "wait_until",
        "condition": {"key": "var.go", "operator": "eq", "value": True},
        "timeout": None,
    }
    macro = _with_trigger(
        _macro(gated, {"action": "state.set", "key": "var.go", "value": False}, overlap="queue"),
        type="event", event_pattern="custom.go", overlap="allow",
    )
    macros.load_macros([macro])
    triggers.load_triggers([macro])
    await triggers.start(fire_startup=False)
    starts: list[str] = []
    skipped: list[str] = []
    events.on("macro.started.m", lambda e, _p: starts.append(e))
    events.on("trigger.skipped", lambda _e, p: skipped.append(p["code"]))
    elsewhere = macros.start("m")
    await asyncio.sleep(0.05)
    try:
        await events.emit("custom.go", {})
        await asyncio.sleep(0.05)
        await events.emit("custom.go", {})
        await asyncio.sleep(0.05)
        # Let each run through in turn, until the three have started (a hang
        # guard, not a deadline).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 20
        while len(starts) < 3 and loop.time() < deadline:
            state.set("var.go", True)
            await asyncio.sleep(0.02)
        assert len(starts) == 3
        assert skipped == []
    finally:
        await triggers.stop()
        await macros.cancel_all()
        await asyncio.gather(elsewhere, return_exceptions=True)
