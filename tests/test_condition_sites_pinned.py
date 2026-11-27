"""What each place that judges a condition does with every spelling of its
operator, and with a condition that has groups where a macro reads one key.

The places: a macro step's Skip If, a Conditional step, a Wait Until step, a
trigger's guard on each of its three paths (the fire, the re-check after a
delay, the re-check after a queue wait) and a State Change trigger's own
operator on both of its paths (the change, the re-check after a delay). Then
every sentence the validator writes about a condition, place by place, and the
spellings it takes at each.

A condition a place cannot read is never acted on: a macro step fails before
it acts, and a trigger whose guard or own operator cannot be read does not
fire, on every path, and is skipped as ``invalid_condition``, which its
last-run record keeps for the trigger card. These tests hold each answer.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

import openavc.core.trigger_engine as te
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_text import skip_reason
from openavc.core.macro_validation import macro_issues
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine
from tests.helpers import wait_for_condition

#: An operator left out of the condition altogether.
ABSENT = object()

#: Spellings a macro step or a guard reads, each meaning "equal".
EQUALS = ["eq", "==", "equals", "equal", ABSENT]
#: Spellings neither reads: a word nobody knows, a canonical name in the wrong
#: case, a blank one, an explicit null and one that is not text at all.
UNREADABLE = ["contains", "EQ", "", None, ["eq"]]


def _id(op: Any) -> str:
    return "absent" if op is ABSENT else repr(op)


def _condition(op: Any, key: str = "var.level", value: Any = 5) -> dict[str, Any]:
    cond: dict[str, Any] = {"key": key, "value": value}
    if op is not ABSENT:
        cond["operator"] = op
    return cond


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    state.set("var.level", 5, source="test")
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


def _record(events: EventBus, pattern: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def on(_event: str, payload: dict[str, Any]) -> None:
        seen.append(dict(payload or {}))

    events.on(pattern, on)
    return seen


def _ran(key: str) -> dict[str, Any]:
    return {"action": "state.set", "key": key, "value": True}


async def _run(macro_engine: MacroEngine, steps: list[dict[str, Any]]) -> str:
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": steps}])
    return await macro_engine.execute("m")


def _unknown(op: Any) -> str:
    return (
        f"Unknown condition operator: '{op}'. "
        "Valid operators: eq, ne, gt, lt, gte, lte, truthy, falsy"
    )


# --- a macro's Skip If ----------------------------------------------------------


@pytest.mark.parametrize("op", EQUALS, ids=_id)
async def test_skip_if_skips_its_step_on_every_spelling_of_equal(op, macro_engine, core):
    state, events = core
    errors = _record(events, "macro.step_error.*")
    outcome = await _run(macro_engine, [
        {**_ran("var.ran"), "skip_if": _condition(op)}, _ran("var.after"),
    ])
    assert (outcome, state.get("var.ran"), state.get("var.after"), errors) == (
        "completed", None, True, [],
    )


@pytest.mark.parametrize("op", UNREADABLE, ids=_id)
async def test_skip_if_it_cannot_read_fails_its_step_before_it_acts(op, macro_engine, core):
    state, events = core
    errors = _record(events, "macro.step_error.*")
    outcome = await _run(macro_engine, [
        {**_ran("var.ran"), "skip_if": _condition(op)}, _ran("var.after"),
    ])
    assert (outcome, state.get("var.ran"), state.get("var.after")) == ("failed", None, True)
    assert [(e["step_index"], e["error"]) for e in errors] == [(0, _unknown(op))]


# --- a Conditional step ---------------------------------------------------------


def _conditional(condition: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "conditional", "condition": condition,
        "then_steps": [_ran("var.then")], "else_steps": [_ran("var.else")],
    }


@pytest.mark.parametrize("op", EQUALS, ids=_id)
async def test_a_conditional_takes_then_on_every_spelling_of_equal(op, macro_engine, core):
    state, _ = core
    assert await _run(macro_engine, [_conditional(_condition(op))]) == "completed"
    assert (state.get("var.then"), state.get("var.else")) == (True, None)


@pytest.mark.parametrize("op", UNREADABLE, ids=_id)
async def test_a_conditional_it_cannot_read_runs_neither_branch_and_fails(op, macro_engine, core):
    state, events = core
    errors = _record(events, "macro.step_error.*")
    assert await _run(macro_engine, [_conditional(_condition(op))]) == "failed"
    assert (state.get("var.then"), state.get("var.else")) == (None, None)
    assert [(e["step_index"], e["error"]) for e in errors] == [(0, _unknown(op))]


# --- a Wait Until step ----------------------------------------------------------


def _wait(condition: dict[str, Any]) -> dict[str, Any]:
    return {"action": "wait_until", "condition": condition, "timeout": 1, "on_timeout": "fail"}


@pytest.mark.parametrize("op", EQUALS, ids=_id)
async def test_a_wait_until_already_met_on_every_spelling_of_equal_does_not_wait(
    op, macro_engine, core,
):
    state, events = core
    progress = _record(events, "macro.progress.*")
    assert await _run(macro_engine, [_wait(_condition(op)), _ran("var.after")]) == "completed"
    waits = [p for p in progress if "condition_key" in p]
    assert [(p["status"], p["waited_seconds"]) for p in waits] == [("satisfied", 0.0)]
    assert state.get("var.after") is True


@pytest.mark.parametrize("op", UNREADABLE, ids=_id)
async def test_a_wait_until_it_cannot_read_fails_its_step_without_waiting(op, macro_engine, core):
    state, events = core
    progress = _record(events, "macro.progress.*")
    errors = _record(events, "macro.step_error.*")
    assert await _run(macro_engine, [_wait(_condition(op)), _ran("var.after")]) == "failed"
    assert [p for p in progress if "condition_key" in p] == []
    assert [(e["step_index"], e["error"]) for e in errors] == [(0, _unknown(op))]
    assert state.get("var.after") is True


# --- a condition with groups, where a macro reads one key -----------------------


_GROUP = [{"key": "var.level", "operator": "eq", "value": 5}]
_FALSE_GROUP = [{"key": "var.level", "operator": "eq", "value": 6}]


@pytest.mark.parametrize("group", ["any", "all"])
async def test_a_macro_condition_with_groups_and_no_key_reads_as_not_met(
    group, macro_engine, core,
):
    """It reads the empty key, a value that has not arrived, against no value."""
    state, events = core
    errors = _record(events, "macro.step_error.*")
    assert await _run(macro_engine, [
        {**_ran("var.ran"), "skip_if": {group: _GROUP}}, _conditional({group: _GROUP}),
    ]) == "completed"
    assert (state.get("var.ran"), state.get("var.then"), state.get("var.else")) == (
        True, None, True,
    )
    assert errors == []


@pytest.mark.parametrize("group", ["any", "all"])
async def test_a_macro_condition_with_a_key_beside_a_group_follows_the_key(
    group, macro_engine, core,
):
    state, _ = core
    beside = {**_condition("eq"), group: _FALSE_GROUP}
    assert await _run(macro_engine, [
        {**_ran("var.ran"), "skip_if": beside}, _conditional(beside),
    ]) == "completed"
    assert (state.get("var.ran"), state.get("var.then"), state.get("var.else")) == (
        None, True, None,
    )


# --- a trigger's guard ----------------------------------------------------------


def _one_macro(trigger: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": "m", "name": "M", "triggers": [{"enabled": True, **trigger}]}]


def _guarded(condition: dict[str, Any], **extra: Any) -> list[dict[str, Any]]:
    return _one_macro({
        "id": "t", "type": "event", "event_pattern": "go", "conditions": [condition], **extra,
    })


async def _fire_guarded(trigger_engine, macro_engine, core, condition) -> tuple[list, list]:
    _, events = core
    skipped = _record(events, "trigger.skipped")
    fired = _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_guarded(condition))
    await trigger_engine.start()
    await events.emit("go", {})
    await _an_outcome(skipped, fired)
    await trigger_engine.stop()
    return skipped, fired


async def _an_outcome(skipped: list, fired: list) -> None:
    """Until the trigger has fired or been skipped."""
    await wait_for_condition(lambda: bool(skipped or fired), timeout=2, interval=0.01)


def _skip(code: str, **about: Any) -> dict[str, Any]:
    return {"trigger_id": "t", "reason": code, **skip_reason(code, **about)}


@pytest.mark.parametrize("op", EQUALS, ids=_id)
async def test_a_guard_holds_on_every_spelling_of_equal(op, trigger_engine, macro_engine, core):
    skipped, fired = await _fire_guarded(trigger_engine, macro_engine, core, _condition(op))
    assert (skipped, len(fired)) == ([], 1)


@pytest.mark.parametrize("op", UNREADABLE, ids=_id)
async def test_a_guard_it_cannot_read_is_skipped_as_invalid(
    op, trigger_engine, macro_engine, core,
):
    skipped, fired = await _fire_guarded(trigger_engine, macro_engine, core, _condition(op))
    assert (skipped, fired) == ([_skip("invalid_condition", operator=op)], [])


async def test_a_guard_it_cannot_read_is_named_behind_one_that_is_not_met(
    trigger_engine, macro_engine, core,
):
    """Whatever its place among the guards: the trigger cannot fire until it
    is fixed, so it is said even while an earlier guard does not hold."""
    _, events = core
    skipped = _record(events, "trigger.skipped")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_one_macro({
        "id": "t", "type": "event", "event_pattern": "go",
        "conditions": [_condition("eq", value=6), _condition("contains")],
    }))
    await trigger_engine.start()
    await events.emit("go", {})
    await wait_for_condition(lambda: bool(skipped), timeout=2, interval=0.01)
    await trigger_engine.stop()
    assert skipped == [_skip("invalid_condition", operator="contains")]


@pytest.mark.parametrize("group", ["any", "all"])
async def test_a_guard_with_groups_and_no_key_is_not_met(
    group, trigger_engine, macro_engine, core,
):
    skipped, fired = await _fire_guarded(trigger_engine, macro_engine, core, {group: _GROUP})
    assert (skipped, fired) == ([_skip("conditions_not_met")], [])


@pytest.mark.parametrize("group", ["any", "all"])
async def test_a_guard_with_a_key_beside_a_group_follows_the_key(
    group, trigger_engine, macro_engine, core,
):
    beside = {**_condition("eq"), group: _FALSE_GROUP}
    skipped, fired = await _fire_guarded(trigger_engine, macro_engine, core, beside)
    assert (skipped, len(fired)) == ([], 1)


@pytest.mark.parametrize("op", UNREADABLE, ids=_id)
async def test_a_guard_it_cannot_read_after_a_delay_is_skipped_as_invalid(
    op, trigger_engine, macro_engine, core,
):
    _, events = core
    skipped = _record(events, "trigger.skipped")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_guarded(_condition(op), delay_seconds=1))
    trigger_engine._running = True
    await trigger_engine._delayed_fire(trigger_engine._triggers["t"], 0, {"event": "go"})
    assert skipped == [_skip("invalid_condition", operator=op)]


@pytest.mark.parametrize("op", UNREADABLE, ids=_id)
async def test_a_guard_it_cannot_read_after_a_queue_wait_is_skipped_as_invalid(
    op, trigger_engine, macro_engine, core, monkeypatch,
):
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    _, events = core
    skipped = _record(events, "trigger.skipped")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_guarded(_condition(op), overlap="queue"))
    trigger_engine._running = True
    running = {"v": True}
    macro_engine.is_macro_running = lambda _mid: running["v"]
    task = asyncio.create_task(
        trigger_engine._queued_fire(trigger_engine._triggers["t"], {"event": "go"}),
    )
    await asyncio.sleep(0.03)
    running["v"] = False
    await asyncio.wait_for(task, 1)
    assert skipped == [_skip("invalid_condition", operator=op)]


# --- a State Change trigger's own operator --------------------------------------


#: Each fires on a change to the value 5: "equal" spelled three ways, and the
#: trigger's own "any" (every change), which a blank, a null and a left-out
#: operator also read as.
STATE_FIRES = ["eq", "==", "equals", "any", "", None, ABSENT]
#: Each is skipped as an invalid condition.
STATE_UNREADABLE = ["contains", "EQ", ["eq"]]


def _state_trigger(op: Any, **extra: Any) -> list[dict[str, Any]]:
    trigger: dict[str, Any] = {
        "id": "t", "type": "state_change", "state_key": "var.level", "state_value": 5, **extra,
    }
    if op is not ABSENT:
        trigger["state_operator"] = op
    return _one_macro(trigger)


async def _change(trigger_engine, macro_engine, core, op) -> tuple[list, list]:
    state, events = core
    skipped = _record(events, "trigger.skipped")
    fired = _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    state.set("var.level", 4, source="test")
    trigger_engine.load_triggers(_state_trigger(op))
    await trigger_engine.start()
    state.set("var.level", 5, source="test")
    await _an_outcome(skipped, fired)
    await trigger_engine.stop()
    return skipped, fired


@pytest.mark.parametrize("op", STATE_FIRES, ids=_id)
async def test_a_state_trigger_fires_on_its_change_for_each_spelling_it_reads(
    op, trigger_engine, macro_engine, core,
):
    skipped, fired = await _change(trigger_engine, macro_engine, core, op)
    assert (skipped, len(fired)) == ([], 1)


@pytest.mark.parametrize("op", STATE_UNREADABLE, ids=_id)
async def test_a_state_trigger_it_cannot_read_is_skipped_as_invalid(
    op, trigger_engine, macro_engine, core,
):
    skipped, fired = await _change(trigger_engine, macro_engine, core, op)
    assert (skipped, fired) == ([_skip("invalid_condition", operator=op)], [])


async def test_a_state_trigger_ignores_a_change_its_operator_does_not_match(
    trigger_engine, macro_engine, core,
):
    state, events = core
    skipped = _record(events, "trigger.skipped")
    fired = _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_state_trigger("eq"))
    await trigger_engine.start()
    state.set("var.level", 6, source="test")
    await asyncio.sleep(0.05)
    await trigger_engine.stop()
    assert (skipped, fired) == ([], [])


async def _after_delay(trigger_engine, macro_engine, core, op) -> tuple[list, list]:
    _, events = core
    skipped = _record(events, "trigger.skipped")
    fired = _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_state_trigger(op, delay_seconds=1))
    trigger_engine._running = True
    await trigger_engine._delayed_fire(trigger_engine._triggers["t"], 0, {"key": "var.level"})
    await _an_outcome(skipped, fired)
    return skipped, fired


@pytest.mark.parametrize("op", STATE_FIRES, ids=_id)
async def test_a_delayed_state_trigger_still_matching_fires(op, trigger_engine, macro_engine, core):
    skipped, fired = await _after_delay(trigger_engine, macro_engine, core, op)
    assert (skipped, len(fired)) == ([], 1)


@pytest.mark.parametrize("op", STATE_UNREADABLE, ids=_id)
async def test_a_delayed_state_trigger_it_cannot_read_is_skipped_as_invalid(
    op, trigger_engine, macro_engine, core,
):
    skipped, fired = await _after_delay(trigger_engine, macro_engine, core, op)
    assert (skipped, fired) == ([_skip("invalid_condition", operator=op)], [])


async def test_a_delayed_state_trigger_whose_value_reverted_is_skipped(
    trigger_engine, macro_engine, core,
):
    state, _ = core
    state.set("var.level", 6, source="test")
    skipped, fired = await _after_delay(trigger_engine, macro_engine, core, "eq")
    assert (skipped, fired) == ([_skip("state_reverted")], [])


# --- what the validator says, place by place ------------------------------------


def _step_issues(step: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(i["message"], i.get("missing")) for i in macro_issues([step], [])]


def _trigger_issues(trigger: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    return [(i["path"], i["message"], i.get("missing")) for i in macro_issues([], [trigger])]


_SKIP_IF_STEP = {"action": "delay", "seconds": 1}
_WAIT_STEP = {"action": "wait_until", "timeout": 1}


@pytest.mark.parametrize("op", EQUALS, ids=_id)
def test_the_validator_takes_every_spelling_of_equal_at_every_place(op):
    cond = _condition(op)
    assert _step_issues({**_SKIP_IF_STEP, "skip_if": cond}) == []
    assert _step_issues({"action": "conditional", "condition": cond}) == []
    assert _step_issues({**_WAIT_STEP, "condition": cond}) == []
    assert _trigger_issues({"id": "t", "type": "startup", "conditions": [cond]}) == []


@pytest.mark.parametrize("op", UNREADABLE, ids=_id)
def test_the_validator_names_an_operator_it_cannot_read_at_each_place(op):
    cond = _condition(op)
    assert _step_issues({**_SKIP_IF_STEP, "skip_if": cond}) == [
        (f"skip_if operator '{op}' is not valid", None),
    ]
    assert _step_issues({"action": "conditional", "condition": cond}) == [
        (f"condition operator '{op}' is not valid", None),
    ]
    assert _step_issues({**_WAIT_STEP, "condition": cond}) == [
        (f"wait_until condition operator '{op}' is not valid", None),
    ]
    assert _trigger_issues({"id": "t", "type": "startup", "conditions": [cond]}) == [
        ("triggers[0].conditions[0]", f"operator '{op}' is not valid", None),
    ]


def test_the_validator_names_a_condition_with_no_key_at_each_place():
    cond = {"operator": "eq", "value": 5}
    assert _step_issues({**_SKIP_IF_STEP, "skip_if": cond}) == [
        ("skip_if requires 'key'", "skip_if.key"),
    ]
    assert _step_issues({"action": "conditional", "condition": cond}) == [
        ("conditional condition requires 'key'", "condition.key"),
    ]
    assert _step_issues({**_WAIT_STEP, "condition": cond}) == [
        ("wait_until condition requires 'key'", "condition.key"),
    ]
    assert _trigger_issues({"id": "t", "type": "startup", "conditions": [cond]}) == [
        ("triggers[0].conditions[0]", "missing 'key'", "conditions.key"),
    ]


@pytest.mark.parametrize("group", ["any", "all"])
def test_the_validator_reads_a_condition_with_groups_and_no_key_as_one_with_no_key(group):
    cond = {group: _GROUP}
    assert _step_issues({**_SKIP_IF_STEP, "skip_if": cond}) == [
        ("skip_if requires 'key'", "skip_if.key"),
    ]
    assert _trigger_issues({"id": "t", "type": "startup", "conditions": [cond]}) == [
        ("triggers[0].conditions[0]", "missing 'key'", "conditions.key"),
    ]


def test_the_validator_says_a_condition_s_missing_key_before_its_operator():
    assert _step_issues({**_SKIP_IF_STEP, "skip_if": {"operator": "contains"}}) == [
        ("skip_if requires 'key'", "skip_if.key"),
        ("skip_if operator 'contains' is not valid", None),
    ]


@pytest.mark.parametrize("op", STATE_FIRES, ids=_id)
def test_the_validator_takes_each_state_operator_the_trigger_reads(op):
    trigger = _state_trigger(op)[0]["triggers"][0]
    assert _trigger_issues(trigger) == []


@pytest.mark.parametrize("op", STATE_UNREADABLE, ids=_id)
def test_the_validator_names_a_state_operator_the_trigger_cannot_read(op):
    trigger = _state_trigger(op)[0]["triggers"][0]
    assert _trigger_issues(trigger) == [(
        "triggers[0]",
        f"state_operator '{op}' is not valid. Use: any, eq, ne, gt, lt, gte, lte, truthy, falsy",
        None,
    )]


def test_the_validator_names_a_state_trigger_with_no_key_before_its_operator():
    trigger = {"id": "t", "type": "state_change", "state_operator": "contains"}
    assert _trigger_issues(trigger) == [
        ("triggers[0]", "state_change trigger requires 'state_key'", "state_key"),
        ("triggers[0]", "state_operator 'contains' is not valid. "
         "Use: any, eq, ne, gt, lt, gte, lte, truthy, falsy", None),
    ]


# --- what the trigger card reads ------------------------------------------------


#: Each path a fire can meet a condition it cannot read on.
UNREADABLE_PATHS = [
    "a guard at the fire",
    "a guard after its delay",
    "a guard after a queue wait",
    "a state trigger's own operator at its change",
    "a state trigger's own operator after its delay",
]


async def _unreadable_on(path: str, trigger_engine, macro_engine, monkeypatch) -> None:
    """A fire on ``path`` whose condition names ``contains``, run to its end."""
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.01)
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    macro_engine.is_macro_running = lambda _mid: False
    guarded = _guarded(_condition("contains"), delay_seconds=1, overlap="queue")
    state_trigger = _state_trigger("contains", delay_seconds=1)
    trigger_engine.load_triggers(state_trigger if "state trigger" in path else guarded)
    trigger_engine._running = True
    ts = trigger_engine._triggers["t"]
    if path == "a guard at the fire":
        await trigger_engine._execute_trigger(ts, {"event": "go"})
    elif path == "a guard after a queue wait":
        await asyncio.wait_for(trigger_engine._queued_fire(ts, {"event": "go"}), 1)
    elif path.endswith("at its change"):
        trigger_engine._on_state_change(ts, "var.level", 4, 5, "test")
    else:
        await trigger_engine._delayed_fire(ts, 0, {"event": "go", "key": "var.level"})


@pytest.mark.parametrize("path", UNREADABLE_PATHS)
async def test_a_condition_it_cannot_read_is_the_trigger_s_last_run(
    path, trigger_engine, macro_engine, core, monkeypatch,
):
    """Written to the record the trigger card reads, and announced as
    ``trigger.completed``, so the card says why while the trigger cannot
    fire. It never started its macro, so its last fire does not move."""
    _, events = core
    completed = _record(events, "trigger.completed")
    await _unreadable_on(path, trigger_engine, macro_engine, monkeypatch)
    await wait_for_condition(lambda: bool(completed), timeout=2, interval=0.01)
    said = skip_reason("invalid_condition", operator="contains")
    (listed,) = trigger_engine.list_triggers()
    assert (listed["last_outcome"], listed["last_reason"], listed["last_fired"]) == (
        "skipped", said, None,
    )
    assert [(c["trigger_id"], c["outcome"], c["code"], c["message"]) for c in completed] == [
        ("t", "skipped", said["code"], said["message"]),
    ]


async def test_a_guard_not_met_leaves_the_last_run_as_it_was(
    trigger_engine, macro_engine, core,
):
    """A guard that does not hold is the trigger working: its last run stays
    the last time its macro ran."""
    _, events = core
    completed = _record(events, "trigger.completed")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_guarded(_condition("eq", value=6)))
    trigger_engine._running = True
    await trigger_engine._execute_trigger(trigger_engine._triggers["t"], {"event": "go"})
    await asyncio.sleep(0.02)
    (listed,) = trigger_engine.list_triggers()
    assert (listed["last_outcome"], completed) == (None, [])
