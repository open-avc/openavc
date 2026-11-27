"""What each condition site reads for the key on a condition's left side.

One corpus of keys, each with the state and the firing context it is judged
under and the value each kind of site reads for it, run through every site
that judges a condition: a step's Skip If, a Conditional step (and the value
its run reports), a Wait Until step, a trigger's guard at the fire and again
after a delay or a queue wait, and a state-change trigger's own test, at the
change and again after its delay. The same corpus runs through
``value_resolver.read_operand``, the one reader the sites ask, so a site that
stops asking it, or asks it the wrong way, fails here.

The sites differ in where a ``trigger.<field>`` key is read from:

- ``context``: Skip If, Conditional and a trigger's guard read the firing
  context: a guard checked again after a wait reads the one it fired with.
- ``empty``: a Wait Until is handed no firing context, so the field is a value
  that has not arrived.

A state-change trigger's own test after its delay reads the watched key in the
state (``test_a_delayed_state_trigger_judges_the_state_after_its_delay``).

Each site's judgement of a probe is checked against ``eval_operator`` applied
to the reading the corpus expects, so the probes tell the four readings apart
(a value from state, one from the context, False, and nothing).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.condition_eval import eval_operator
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_text import skip_reason
from openavc.core.state_store import StateStore
import openavc.core.trigger_engine as trigger_engine_module
from openavc.core.trigger_engine import TriggerEngine
from openavc.core.value_resolver import read_operand

FROM_STATE = "from_state"
FROM_CONTEXT = "from_context"

#: (id, left key, state, firing context or None for a run with none,
#:  the reading per kind of site).
CASES: list[tuple[str, str, dict[str, Any], dict[str, Any] | None, dict[str, Any]]] = [
    (
        "a state key", "var.level",
        {"var.level": FROM_STATE}, {"level": FROM_CONTEXT},
        {"context": FROM_STATE, "empty": FROM_STATE},
    ),
    (
        "a trigger field, with a state key of the same name", "trigger.value",
        {"trigger.value": FROM_STATE}, {"value": FROM_CONTEXT},
        {"context": FROM_CONTEXT, "empty": None},
    ),
    (
        "a trigger field the context lacks", "trigger.value",
        {}, {"other": FROM_CONTEXT},
        {"context": None, "empty": None},
    ),
    (
        "a trigger field on a run with no context", "trigger.value",
        {"trigger.value": FROM_STATE}, None,
        {"context": None, "empty": None},
    ),
    (
        "a trigger field holding False", "trigger.value",
        {}, {"value": False},
        {"context": False, "empty": None},
    ),
    (
        "a state key with no value", "var.missing",
        {}, {"missing": FROM_CONTEXT},
        {"context": None, "empty": None},
    ),
    (
        "a state key holding None", "var.none",
        {"var.none": None}, {},
        {"context": None, "empty": None},
    ),
    (
        "a state key holding False", "var.off",
        {"var.off": False}, {},
        {"context": False, "empty": False},
    ),
    (
        "a $ on the left is a key, not a reference", "$var.level",
        {"var.level": FROM_STATE}, {"level": FROM_CONTEXT},
        {"context": None, "empty": None},
    ),
]
CASE_IDS = [c[0] for c in CASES]

#: Each probe's judgement differs between the four readings the corpus uses.
PROBES: list[dict[str, Any]] = [
    {"operator": "eq", "value": FROM_STATE},
    {"operator": "eq", "value": FROM_CONTEXT},
    {"operator": "eq", "value": False},
    {"operator": "falsy"},
]


def _state(values: dict[str, Any]) -> StateStore:
    state = StateStore()
    for key, value in values.items():
        # A script may store a key outside the documented namespaces, so a
        # state key named ``trigger.value`` is reachable.
        state.set(key, value, source="test")
    return state


def _macros(state: StateStore, events: EventBus) -> MacroEngine:
    devices = MagicMock()
    devices.send_command = AsyncMock()
    devices.command_goes_out = MagicMock(return_value=True)
    devices.get_driver = MagicMock(return_value=None)
    return MacroEngine(state, events, devices)


def _expected(reading: Any, probe: dict[str, Any]) -> bool:
    return eval_operator(probe["operator"], reading, probe.get("value"))


async def _run_macro(
    steps: list[dict[str, Any]], values: dict[str, Any], context: dict[str, Any] | None,
) -> tuple[StateStore, list[dict[str, Any]]]:
    state = _state(values)
    events = EventBus()
    progress: list[dict[str, Any]] = []

    async def on_progress(_event: str, payload: dict[str, Any]) -> None:
        progress.append(dict(payload or {}))

    events.on("macro.progress.*", on_progress)
    engine = _macros(state, events)
    engine.load_macros([{"id": "m", "name": "M", "steps": steps}])
    if context is None:
        await asyncio.wait_for(engine.execute("m"), 2)
    else:
        await asyncio.wait_for(engine.execute("m", context), 2)
    return state, progress


# --- the reader ----------------------------------------------------------------


@pytest.mark.parametrize(("_id", "key", "values", "context", "reads"), CASES, ids=CASE_IDS)
def test_the_reader_answers_each_kind_of_site(_id, key, values, context, reads) -> None:
    state = _state(values)
    assert read_operand(key, state=state, context=context) == reads["context"]
    assert read_operand(key, state=state) == reads["empty"]


def test_the_reader_logs_nothing_for_a_key_with_no_value(caplog) -> None:
    """A Wait Until reads its key on every change, so a missing key must not
    warn the way a missing ``$`` reference in a parameter does."""
    with caplog.at_level("DEBUG", logger="openavc.core.value_resolver"):
        assert read_operand("var.missing", state=StateStore()) is None
    assert caplog.records == []


# --- Skip If -------------------------------------------------------------------


@pytest.mark.parametrize(("_id", "key", "values", "context", "reads"), CASES, ids=CASE_IDS)
async def test_skip_if_reads_the_firing_context(_id, key, values, context, reads) -> None:
    for probe in PROBES:
        state, _ = await _run_macro(
            [{"action": "state.set", "key": "var.ran", "value": True,
              "skip_if": {"key": key, **probe}}],
            values, context,
        )
        skipped = state.get("var.ran") is None
        assert skipped == _expected(reads["context"], probe), probe


# --- Conditional ---------------------------------------------------------------


@pytest.mark.parametrize(("_id", "key", "values", "context", "reads"), CASES, ids=CASE_IDS)
async def test_a_conditional_reads_the_firing_context(_id, key, values, context, reads) -> None:
    for probe in PROBES:
        state, progress = await _run_macro(
            [{"action": "conditional", "condition": {"key": key, **probe},
              "then_steps": [{"action": "state.set", "key": "var.branch", "value": "then"}],
              "else_steps": [{"action": "state.set", "key": "var.branch", "value": "else"}]}],
            values, context,
        )
        took_then = state.get("var.branch") == "then"
        assert took_then == _expected(reads["context"], probe), probe
        # The run reports the value it compared.
        [frame] = [
            p for p in progress
            if p.get("action") == "conditional" and p.get("status") == "evaluated"
        ]
        assert frame["actual_value"] == reads["context"]
        assert type(frame["actual_value"]) is type(reads["context"])


# --- Wait Until ----------------------------------------------------------------


@pytest.mark.parametrize(("_id", "key", "values", "context", "reads"), CASES, ids=CASE_IDS)
async def test_a_wait_until_is_handed_no_firing_context(_id, key, values, context, reads) -> None:
    for probe in PROBES:
        _state_after, progress = await _run_macro(
            [{"action": "wait_until", "timeout": 0.01, "on_timeout": "continue",
              "condition": {"key": key, **probe}}],
            values, context,
        )
        statuses = [p.get("status") for p in progress if p.get("action") == "wait_until"]
        if _expected(reads["empty"], probe):
            assert "satisfied" in statuses and "waiting" not in statuses, (probe, statuses)
        else:
            # Neither the first check, the change listener nor the check after
            # subscribing found it met.
            assert statuses[-1] == "timeout" and "satisfied" not in statuses, (probe, statuses)


# --- a trigger's guard ---------------------------------------------------------


#: The paths a fire checks its guards on, each with the reason a guard not met
#: there is skipped with.
GUARD_PATHS = {
    "at the fire": "conditions_not_met",
    "after its delay": "guard_not_met",
    "after a queue wait": "guard_not_met",
}


async def _guard_lets_it_fire(
    path: str, key: str, probe: dict[str, Any], values: dict[str, Any],
    context: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    """Whether an event trigger guarded by ``key`` and ``probe`` runs its macro
    when ``custom.go`` fires with ``context`` as its payload, checked on
    ``path``, and the reasons any fire was skipped with."""
    state = _state(values)
    events = EventBus()
    state.set_event_bus(events)
    macros = _macros(state, events)
    macros.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "state.set", "key": "var.ran", "value": True},
    ]}])
    trigger_engine = TriggerEngine(state, events, macros)
    trigger_engine.load_triggers([{"id": "m", "name": "M", "triggers": [{
        "id": "t", "type": "event", "enabled": True, "event_pattern": "custom.go",
        "conditions": [{"key": key, **probe}], "delay_seconds": 1, "overlap": "queue",
    }]}])
    skipped: list[str] = []
    done = asyncio.Event()

    async def on_end(event: str, payload: dict[str, Any]) -> None:
        if event == "trigger.skipped":
            skipped.append(payload["reason"])
        done.set()

    events.on("trigger.completed", on_end)
    events.on("trigger.skipped", on_end)
    ts = trigger_engine._triggers["t"]
    # What the event trigger hands its macro when custom.go fires.
    fired_with = {"event": "custom.go", **(context or {})}
    await trigger_engine.start(fire_startup=False)
    try:
        if path == "at the fire":
            await trigger_engine._execute_trigger(ts, fired_with)
        elif path == "after its delay":
            await trigger_engine._delayed_fire(ts, 0, fired_with)
        else:
            macros.is_macro_running = lambda _macro_id: False
            await asyncio.wait_for(trigger_engine._queued_fire(ts, fired_with), 2)
        await asyncio.wait_for(done.wait(), 2)
    finally:
        await trigger_engine.stop()
    return state.get("var.ran") is True, skipped


@pytest.mark.parametrize("path", GUARD_PATHS)
@pytest.mark.parametrize(("_id", "key", "values", "context", "reads"), CASES, ids=CASE_IDS)
async def test_a_guard_reads_the_firing_context(
    path, _id, key, values, context, reads, monkeypatch,
) -> None:
    monkeypatch.setattr(trigger_engine_module, "_QUEUE_POLL_INTERVAL", 0.01)
    for probe in PROBES:
        fired, skipped = await _guard_lets_it_fire(path, key, probe, values, context)
        holds = _expected(reads["context"], probe)
        assert (fired, skipped) == (holds, [] if holds else [GUARD_PATHS[path]]), probe


async def test_a_guard_fired_by_an_event_reads_the_event_s_payload() -> None:
    """The whole path from the event: the payload's field is the guard's key."""
    for level, fires in ((5, True), (4, False)):
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        macros = _macros(state, events)
        macros.load_macros([{"id": "m", "name": "M", "steps": [
            {"action": "state.set", "key": "var.ran", "value": True},
        ]}])
        trigger_engine = TriggerEngine(state, events, macros)
        trigger_engine.load_triggers([{"id": "m", "name": "M", "triggers": [{
            "id": "t", "type": "event", "enabled": True, "event_pattern": "custom.go",
            "conditions": [{"key": "trigger.level", "operator": "gte", "value": 5}],
        }]}])
        done = asyncio.Event()

        async def on_end(_event: str, _payload: dict[str, Any]) -> None:
            done.set()

        events.on("trigger.completed", on_end)
        events.on("trigger.skipped", on_end)
        await trigger_engine.start(fire_startup=False)
        try:
            await events.emit("custom.go", {"level": level})
            await asyncio.wait_for(done.wait(), 2)
        finally:
            await trigger_engine.stop()
        assert (state.get("var.ran") is True) == fires, level


# --- a state-change trigger's own test -----------------------------------------


def _state_trigger(
    target: Any, delay: float = 0,
) -> tuple[StateStore, EventBus, TriggerEngine, list[dict[str, Any]]]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    macros = _macros(state, events)
    macros.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "state.set", "key": "var.ran", "value": True},
    ]}])
    trigger_engine = TriggerEngine(state, events, macros)
    trigger_engine.load_triggers([{"id": "m", "name": "M", "triggers": [{
        "id": "t", "type": "state_change", "enabled": True, "state_key": "var.watched",
        "state_operator": "eq", "state_value": target, "delay_seconds": delay,
    }]}])
    skipped: list[dict[str, Any]] = []

    async def on_skip(_event: str, payload: dict[str, Any]) -> None:
        skipped.append(dict(payload or {}))

    events.on("trigger.skipped", on_skip)
    return state, events, trigger_engine, skipped


async def _settle(state: StateStore, seconds: float = 0.3) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while state.get("var.ran") is None and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)


@pytest.mark.parametrize(("new_value", "in_state", "fires"), [
    ("on", "off", True),
    ("off", "on", False),
], ids=["the new value matches, the state does not", "the state matches, the new value does not"])
async def test_a_state_trigger_judges_the_new_value_at_the_change(new_value, in_state, fires) -> None:
    """At the change it judges the value the change carries, ``trigger.new_value``,
    not what the state holds by the time the listener runs."""
    state, _events, trigger_engine, _skipped = _state_trigger("on")
    # Written before the trigger listens, so this write fires nothing.
    state.set("var.watched", in_state, source="test")
    await trigger_engine.start(fire_startup=False)
    try:
        # The callback the store calls, with a change whose value the state
        # has already moved past.
        trigger_engine._on_state_change(
            trigger_engine._triggers["t"], "var.watched", None, new_value, "test",
        )
        await _settle(state)
        assert (state.get("var.ran") is True) == fires
    finally:
        await trigger_engine.stop()


@pytest.mark.parametrize(("then", "fires"), [
    ("on", True),
    ("off", False),
], ids=["the state still matches after the delay", "the state moved during the delay"])
async def test_a_delayed_state_trigger_judges_the_state_after_its_delay(then, fires) -> None:
    """After its delay it judges the watched key's value in the state, not the
    change it was scheduled by."""
    state, _events, trigger_engine, skipped = _state_trigger("on", delay=0.05)
    await trigger_engine.start(fire_startup=False)
    try:
        state.set("var.watched", "on", source="test")
        state.set("var.watched", then, source="test")
        await _settle(state)
        assert (state.get("var.ran") is True) == fires
        reverted = {"trigger_id": "t", "reason": "state_reverted", **skip_reason("state_reverted")}
        assert (skipped == [reverted]) == (not fires)
    finally:
        await trigger_engine.stop()


@pytest.mark.parametrize(("guarded", "fires"), [
    (5, True),
    (7, False),
], ids=["the guard holds on the change it fired on", "the guard holds only on the state now"])
async def test_a_delayed_state_trigger_s_guard_reads_the_change_it_fired_on(guarded, fires) -> None:
    """After its delay the trigger's own test reads the watched key in the
    state, and its guard reads the values from the moment it fired."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    macros = _macros(state, events)
    macros.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "state.set", "key": "var.ran", "value": True},
    ]}])
    trigger_engine = TriggerEngine(state, events, macros)
    trigger_engine.load_triggers([{"id": "m", "name": "M", "triggers": [{
        "id": "t", "type": "state_change", "enabled": True, "state_key": "var.watched",
        "state_operator": "gte", "state_value": 5, "delay_seconds": 1,
        "conditions": [{"key": "trigger.new_value", "operator": "eq", "value": guarded}],
    }]}])
    skipped: list[str] = []

    async def on_skip(_event: str, payload: dict[str, Any]) -> None:
        skipped.append(payload["reason"])

    events.on("trigger.skipped", on_skip)
    # By the end of the delay the key has moved on from the change it fired on.
    state.set("var.watched", 7, source="test")
    trigger_engine._running = True
    await trigger_engine._delayed_fire(
        trigger_engine._triggers["t"], 0, {"key": "var.watched", "old_value": 1, "new_value": 5},
    )
    assert (state.get("var.ran") is True, skipped) == (fires, [] if fires else ["guard_not_met"])
