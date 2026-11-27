"""Every place on the server that judges a condition reads a value that names a key.

``power == $var.wanted`` compares with what ``var.wanted`` holds at a macro's
Skip If, a Conditional and a Wait Until, at a trigger's guard and at a State
Change trigger's own test, on the change and after a delay. A ``$trigger.``
value follows the place's rule for the firing context, as its key does. A key
with no value makes no decision. A Wait Until wakes when the key its value
names changes, not only its own. The comparison itself is the corpus's
(``tests/test_condition_references.py``).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine
from tests.helpers import wait_for_condition


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    state.set("var.power", "on", source="test")
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


def _ran(key: str) -> dict[str, Any]:
    return {"action": "state.set", "key": key, "value": True}


def _wanted(op: str = "eq", value: Any = "$var.wanted", key: str = "var.power") -> dict[str, Any]:
    return {"key": key, "operator": op, "value": value}


async def _run(macro_engine: MacroEngine, steps: list[dict[str, Any]], **kwargs: Any) -> str:
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": steps}])
    return await macro_engine.execute("m", **kwargs)


def _record(events: EventBus, pattern: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def on(_event: str, payload: dict[str, Any]) -> None:
        seen.append(dict(payload or {}))

    events.on(pattern, on)
    return seen


# --- a macro's steps --------------------------------------------------------------


@pytest.mark.parametrize(("wanted", "skipped"), [("ON", True), ("off", False)])
async def test_skip_if_compares_with_what_the_key_its_value_names_holds(
    wanted, skipped, macro_engine, core,
):
    state, _ = core
    state.set("var.wanted", wanted, source="test")
    await _run(macro_engine, [{**_ran("var.ran"), "skip_if": _wanted()}])
    assert (state.get("var.ran") is None) is skipped


async def test_skip_if_makes_no_decision_on_a_key_with_no_value(macro_engine, core):
    """Not-equal to a key that has no value does not hold, so the step runs."""
    state, _ = core
    await _run(macro_engine, [{**_ran("var.ran"), "skip_if": _wanted("ne")}])
    assert state.get("var.ran") is True


@pytest.mark.parametrize(("wanted", "branch"), [("on", "var.then"), ("off", "var.else")])
async def test_a_conditional_takes_the_branch_the_key_its_value_names_decides(
    wanted, branch, macro_engine, core,
):
    state, _ = core
    state.set("var.wanted", wanted, source="test")
    await _run(macro_engine, [{
        "action": "conditional", "condition": _wanted(),
        "then_steps": [_ran("var.then")], "else_steps": [_ran("var.else")],
    }])
    assert [k for k in ("var.then", "var.else") if state.get(k)] == [branch]


async def test_a_conditional_reads_a_trigger_value_from_the_run_s_firing_context(macro_engine, core):
    state, _ = core
    await _run(macro_engine, [{
        "action": "conditional", "condition": _wanted(value="$trigger.new_value"),
        "then_steps": [_ran("var.then")], "else_steps": [_ran("var.else")],
    }], context={"new_value": "ON"})
    assert (state.get("var.then"), state.get("var.else")) == (True, None)


async def test_a_wait_until_wakes_when_only_the_key_its_value_names_changes(macro_engine, core):
    state, _ = core
    state.set("var.wanted", "off", source="test")
    run = asyncio.create_task(_run(macro_engine, [
        {"action": "wait_until", "condition": _wanted(), "timeout": 2, "on_timeout": "fail"},
        _ran("var.after"),
    ]))
    await asyncio.sleep(0.05)
    state.set("var.wanted", "on", source="test")
    assert await asyncio.wait_for(run, timeout=1) == "completed"
    assert state.get("var.after") is True


async def test_a_wait_until_already_met_by_the_key_its_value_names_does_not_wait(macro_engine, core):
    state, events = core
    state.set("var.wanted", "on", source="test")
    progress = _record(events, "macro.progress.*")
    await _run(macro_engine, [{"action": "wait_until", "condition": _wanted(), "timeout": 1}])
    waits = [p for p in progress if p.get("action") == "wait_until" and "condition_key" in p]
    assert [p["status"] for p in waits] == ["satisfied"]


async def test_a_wait_until_watches_both_keys_and_lets_go_of_each_when_it_ends(
    macro_engine, core, monkeypatch,
):
    state, _ = core
    state.set("var.wanted", "off", source="test")
    watched: dict[str, str] = {}
    released: list[str] = []
    subscribe, unsubscribe = state.subscribe, state.unsubscribe

    def _subscribe(pattern: str, callback: Any) -> str:
        sub_id = subscribe(pattern, callback)
        watched[sub_id] = pattern
        return sub_id

    def _unsubscribe(sub_id: str) -> None:
        released.append(sub_id)
        unsubscribe(sub_id)

    monkeypatch.setattr(state, "subscribe", _subscribe)
    monkeypatch.setattr(state, "unsubscribe", _unsubscribe)
    outcome = await _run(macro_engine, [
        {"action": "wait_until", "condition": _wanted(), "timeout": 0.05, "on_timeout": "continue"},
    ])
    assert outcome == "completed"
    assert sorted(watched.values()) == ["var.power", "var.wanted"]
    assert sorted(released) == sorted(watched)


# --- a trigger -------------------------------------------------------------------


def _one_macro(trigger: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": "m", "name": "M", "triggers": [{"enabled": True, "id": "t", **trigger}]}]


async def _outcome(skipped: list, fired: list) -> None:
    await wait_for_condition(lambda: bool(skipped or fired), timeout=2, interval=0.01)


@pytest.mark.parametrize(("wanted", "fires"), [("on", True), ("off", False)])
async def test_a_guard_compares_with_what_the_key_its_value_names_holds(
    wanted, fires, trigger_engine, macro_engine, core,
):
    state, events = core
    state.set("var.wanted", wanted, source="test")
    skipped, fired = _record(events, "trigger.skipped"), _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_one_macro({
        "type": "event", "event_pattern": "go", "conditions": [_wanted()],
    }))
    await trigger_engine.start()
    await events.emit("go", {})
    await _outcome(skipped, fired)
    await trigger_engine.stop()
    assert (len(fired), len(skipped)) == ((1, 0) if fires else (0, 1))


@pytest.mark.parametrize(("wanted", "fires"), [("on", True), ("off", False)])
async def test_a_guard_compares_with_a_trigger_value_from_the_event_that_fired_it(
    wanted, fires, trigger_engine, macro_engine, core,
):
    state, events = core
    state.set("var.power", "on", source="test")
    skipped, fired = _record(events, "trigger.skipped"), _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_one_macro({
        "type": "event", "event_pattern": "go",
        "conditions": [{"key": "var.power", "operator": "eq", "value": "$trigger.wanted"}],
    }))
    await trigger_engine.start()
    await events.emit("go", {"wanted": wanted})
    await _outcome(skipped, fired)
    await trigger_engine.stop()
    assert (len(fired), len(skipped)) == ((1, 0) if fires else (0, 1))


def _state_trigger(**extra: Any) -> list[dict[str, Any]]:
    return _one_macro({
        "type": "state_change", "state_key": "var.level",
        "state_operator": "gte", "state_value": "$var.limit", **extra,
    })


@pytest.mark.parametrize(("level", "fires"), [(50, True), (30, False)])
async def test_a_state_trigger_tests_its_change_against_the_key_its_value_names(
    level, fires, trigger_engine, macro_engine, core,
):
    state, events = core
    state.set("var.limit", 40, source="test")
    state.set("var.level", 0, source="test")
    fired = _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_state_trigger())
    await trigger_engine.start()
    state.set("var.level", level, source="test")
    if fires:
        await wait_for_condition(lambda: bool(fired), timeout=2, interval=0.01)
    else:
        await asyncio.sleep(0.05)
    await trigger_engine.stop()
    assert bool(fired) is fires


async def test_a_delayed_state_trigger_checks_again_against_the_key_its_value_names(
    trigger_engine, macro_engine, core,
):
    """The limit rose during the delay, past the level: the change no longer holds."""
    state, events = core
    state.set("var.level", 50, source="test")
    state.set("var.limit", 60, source="test")
    skipped, fired = _record(events, "trigger.skipped"), _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_state_trigger(delay_seconds=1))
    trigger_engine._running = True
    await trigger_engine._delayed_fire(trigger_engine._triggers["t"], 0, {"key": "var.level"})
    await _outcome(skipped, fired)
    assert ([s["reason"] for s in skipped], fired) == (["state_reverted"], [])
