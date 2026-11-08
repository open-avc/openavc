"""What the macro engine does today on paths no other test runs.

Each test records current behaviour so a restructure of the engine can be
checked against it: the queue wait that gives up and starts anyway, a start
made with no current task, a wait_until run outside any macro, the wording a
failed step is logged with, and the sentence each step is announced with.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import openavc.core.macro_engine as macro_engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore


@pytest.fixture
def state() -> StateStore:
    return StateStore()


@pytest.fixture
def events() -> EventBus:
    return EventBus()


@pytest.fixture
def devices() -> MagicMock:
    d = MagicMock()
    d.send_command = AsyncMock()
    d.command_goes_out = MagicMock(return_value=True)
    d.get_driver = MagicMock(return_value=None)
    return d


@pytest.fixture
def engine(state: StateStore, events: EventBus, devices: MagicMock) -> MacroEngine:
    return MacroEngine(state, events, devices)


def _record(events: EventBus, pattern: str) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def on(event: str, payload: dict[str, Any]) -> None:
        seen.append((event, dict(payload or {})))

    events.on(pattern, on)
    return seen


# --- overlap: queue gives up and starts anyway ---------------------------------


async def test_a_queued_start_gives_up_and_runs_beside_the_first(
    engine: MacroEngine, events: EventBus, monkeypatch, caplog,
) -> None:
    monkeypatch.setattr(macro_engine, "_QUEUE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(macro_engine, "_QUEUE_MAX_WAIT_SECONDS", 0.06)
    started = _record(events, "macro.started.*")
    engine.load_macros([{
        "id": "slow", "name": "Slow", "overlap": "queue",
        "steps": [{"action": "delay", "seconds": 0.5}],
    }])

    first = asyncio.create_task(engine.execute("slow"))
    await asyncio.sleep(0.02)
    with caplog.at_level(logging.WARNING, logger="openavc.core.macro_engine"):
        second = asyncio.create_task(engine.execute("slow"))
        await asyncio.sleep(0.2)
        # The second gave up waiting and started while the first still runs.
        assert len(started) == 2
        assert not first.done()
        assert len(engine._running["slow"]) == 2
    assert any(
        "overlap=queue: gave up after" in r.getMessage()
        and "starting anyway" in r.getMessage()
        and r.name == "openavc.core.macro_engine"
        for r in caplog.records
    )
    assert await asyncio.gather(first, second) == ["completed", "completed"]
    assert engine.is_macro_running("slow") is False


# --- a start with no current task -----------------------------------------------


async def test_a_start_with_no_task_honours_the_cooldown_and_registers_nothing(
    engine: MacroEngine, events: EventBus, state: StateStore, monkeypatch,
) -> None:
    engine.load_macros([{
        "id": "cool", "name": "Cool", "cooldown_seconds": 30,
        "steps": [{"action": "state.set", "key": "var.seen_running", "value": "$var.probe"}],
    }])
    skipped = _record(events, "macro.skipped.*")
    monkeypatch.setattr(macro_engine.asyncio, "current_task", lambda *a, **k: None)

    # Running, the macro is not counted: nothing was registered for it.
    observed: list[bool] = []
    state.subscribe("var.seen_running", lambda *_a: observed.append(engine.is_macro_running("cool")))
    assert await engine.execute("cool") == "completed"
    assert observed == [False]
    assert engine._running == {}
    # The cooldown was stamped all the same, so the next start is refused.
    assert await engine.execute("cool") == "skipped"
    assert skipped[-1][1]["reason"] == "cooldown (30s) not elapsed"


async def test_a_start_with_no_task_still_preempts_its_cancel_group(
    engine: MacroEngine, events: EventBus, monkeypatch, caplog,
) -> None:
    engine.load_macros([
        {"id": "on", "name": "On", "cancel_group": "power",
         "steps": [{"action": "delay", "seconds": 5}]},
        {"id": "off", "name": "Off", "cancel_group": "power",
         "steps": [{"action": "state.set", "key": "var.off", "value": True}]},
    ])
    running = asyncio.create_task(engine.execute("on"))
    await asyncio.sleep(0.02)
    assert engine.is_macro_running("on")

    monkeypatch.setattr(macro_engine.asyncio, "current_task", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger="openavc.core.macro_engine"):
        assert await engine.execute("off") == "completed"
    assert await running == "cancelled"
    assert any(
        r.getMessage() == "Preempting macro 'on' (cancel_group 'power')"
        for r in caplog.records
    )


async def test_a_start_with_no_task_is_skipped_behind_a_running_skip_macro(
    engine: MacroEngine, events: EventBus, monkeypatch,
) -> None:
    engine.load_macros([{
        "id": "solo", "name": "Solo", "overlap": "skip",
        "steps": [{"action": "delay", "seconds": 5}],
    }])
    skipped = _record(events, "macro.skipped.*")
    running = asyncio.create_task(engine.execute("solo"))
    await asyncio.sleep(0.02)

    monkeypatch.setattr(macro_engine.asyncio, "current_task", lambda *a, **k: None)
    assert await engine.execute("solo") == "skipped"
    assert skipped == [(
        "macro.skipped.solo",
        {"macro_id": "solo", "name": "Solo",
         "reason": "overlap=skip and an instance is already running"},
    )]
    monkeypatch.undo()
    running.cancel()
    assert await running == "cancelled"


# --- wait_until outside a macro, and the re-check after subscribing ---------------


async def test_a_wait_until_outside_a_macro_says_nothing_and_still_waits(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    progress = _record(events, "macro.progress.*")
    state.set("var.ready", False)
    step = {"action": "wait_until", "timeout": 2,
            "condition": {"key": "var.ready", "operator": "eq", "value": True}}

    run = asyncio.create_task(engine.execute_steps([step]))
    await asyncio.sleep(0.05)
    assert not run.done()
    state.set("var.ready", True)
    assert await asyncio.wait_for(run, 1) == 0
    assert progress == []


async def test_a_wait_until_outside_a_macro_counts_its_timeout(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    progress = _record(events, "macro.progress.*")
    errors = _record(events, "macro.step_error.*")
    step = {"action": "wait_until", "timeout": 0.05,
            "condition": {"key": "var.never", "operator": "eq", "value": True}}

    assert await engine.execute_steps([step]) == 1
    assert progress == [] and errors == []


async def test_a_wait_until_that_is_already_met_outside_a_macro_returns_at_once(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    progress = _record(events, "macro.progress.*")
    state.set("var.ready", True)
    step = {"action": "wait_until", "timeout": None,
            "condition": {"key": "var.ready", "operator": "eq", "value": True}}

    assert await asyncio.wait_for(engine.execute_steps([step]), 1) == 0
    assert progress == []


async def test_a_value_that_lands_before_the_subscription_is_still_seen(
    engine: MacroEngine, events: EventBus, state: StateStore, monkeypatch,
) -> None:
    """The condition turns true after the first check and before the listener
    exists, so no change notification ever reaches it: only the second check
    after subscribing can end the wait."""
    state.set("var.ready", False)
    real_subscribe = state.subscribe

    def subscribe_after_the_change(pattern: str, callback: Any) -> str:
        state.set("var.ready", True)
        return real_subscribe(pattern, callback)

    monkeypatch.setattr(state, "subscribe", subscribe_after_the_change)
    progress = _record(events, "macro.progress.*")
    engine.load_macros([{
        "id": "w", "name": "W",
        "steps": [{"action": "wait_until", "timeout": None,
                   "condition": {"key": "var.ready", "operator": "eq", "value": True}}],
    }])

    assert await asyncio.wait_for(engine.execute("w"), 1) == "completed"
    statuses = [p.get("status") for _e, p in progress if p.get("action") == "wait_until"]
    assert statuses == ["running", "waiting", "satisfied"]


async def test_a_wait_until_ignores_the_trigger_context(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    """A wait_until reads its condition from the state store only: a
    ``trigger.`` key is never satisfied by the firing trigger's context."""
    engine.load_macros([{
        "id": "w", "name": "W",
        "steps": [{"action": "wait_until", "timeout": 0.05, "on_timeout": "fail",
                   "condition": {"key": "trigger.value", "operator": "eq", "value": 1}}],
    }])
    assert await engine.execute("w", {"value": 1}) == "failed"


# --- the context a failed step is logged with -------------------------------------


async def _failure_line(
    engine: MacroEngine, steps: list[dict[str, Any]], caplog,
) -> list[str]:
    engine.load_macros([{"id": "m", "name": "M", "steps": steps}])
    with caplog.at_level(logging.ERROR, logger="openavc.core.macro_engine"):
        await engine.execute("m")
    return [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("Macro step failed: ")
    ]


async def test_a_failed_group_step_names_the_command_and_the_group(engine, caplog) -> None:
    lines = await _failure_line(
        engine, [{"action": "group.command", "group": "wall", "command": "power_on"}], caplog,
    )
    assert lines == [
        "Macro step failed: step 1/1, group.command, command 'power_on', on group 'wall'"
        " — Device group 'wall' not found. Check that the group exists in your project."
    ]


async def test_a_failed_group_step_without_a_command_or_group_says_only_the_action(
    engine, caplog,
) -> None:
    lines = await _failure_line(engine, [{"action": "group.command"}], caplog)
    assert lines == [
        "Macro step failed: step 1/1, group.command"
        " — Device group '' not found. Check that the group exists in your project."
    ]


async def test_a_failed_call_names_the_macro_it_called(engine, caplog) -> None:
    lines = await _failure_line(
        engine,
        [{"action": "delay", "seconds": 0}, {"action": "macro", "macro": "ghost"}],
        caplog,
    )
    assert lines == ["Macro step failed: step 2/2, macro, calling 'ghost' — Macro 'ghost' not found"]


async def test_a_failed_call_with_no_macro_named_says_only_the_action(engine, caplog) -> None:
    lines = await _failure_line(engine, [{"action": "macro"}], caplog)
    assert lines == ["Macro step failed: step 1/1, macro — Macro '' not found"]


async def test_a_failed_plugin_step_names_the_plugin(engine, caplog) -> None:
    async def broken(_params: dict[str, Any], _context: dict[str, Any]) -> None:
        raise RuntimeError("lamp out")

    engine.register_plugin_action("acme.blink", broken, "acme_plugin", "Blink the lamp")
    lines = await _failure_line(engine, [{"action": "acme.blink"}], caplog)
    assert lines == ["Macro step failed: step 1/1, acme.blink, plugin 'acme_plugin' — lamp out"]


async def test_an_unknown_step_names_only_its_action(engine, caplog) -> None:
    lines = await _failure_line(engine, [{"action": "acme.nothing"}], caplog)
    assert lines == [
        "Macro step failed: step 1/1, acme.nothing — Unknown macro action: 'acme.nothing'"
    ]


async def test_a_failed_device_step_names_the_device_by_its_name(
    engine, state, devices, caplog,
) -> None:
    devices.send_command.side_effect = RuntimeError("no answer")
    state.set("device.acme1.name", "Lobby Widget")
    lines = await _failure_line(
        engine,
        [{"action": "device.command", "device": "acme1", "command": "power_on"},
         {"action": "device.command", "device": "acme2", "command": "power_on"},
         {"action": "device.command"}],
        caplog,
    )
    assert lines == [
        "Macro step failed: step 1/3, device.command, command 'power_on', on 'Lobby Widget' — no answer",
        "Macro step failed: step 2/3, device.command, command 'power_on', on 'acme2' — no answer",
        "Macro step failed: step 3/3, device.command — no answer",
    ]


async def test_a_step_that_fails_under_stop_on_error_ends_the_run_in_those_words(
    engine, events,
) -> None:
    errors = _record(events, "macro.error.*")
    engine.load_macros([{
        "id": "m", "name": "M", "stop_on_error": True,
        "steps": [{"action": "group.command", "group": "wall", "command": "power_on"},
                  {"action": "state.set", "key": "var.after", "value": 1}],
    }])
    assert await engine.execute("m") == "failed"
    assert errors == [("macro.error.m", {
        "macro_id": "m", "name": "M",
        "error": "step 1/2, group.command, command 'power_on', on group 'wall': "
                 "Device group 'wall' not found. Check that the group exists in your project.",
    })]


# --- the sentence each step is announced with ---------------------------------------


_DESCRIBED = [
    ({"action": "device.command", "device": "acme1", "command": "power_on"},
     "Sending power_on to acme1"),
    ({"action": "device.command"}, "Sending ? to ?"),
    ({"action": "group.command", "group": "wall", "command": "power_on"},
     "Sending power_on to group wall"),
    ({"action": "group.command"}, "Sending ? to group ?"),
    ({"action": "delay", "seconds": 0}, "Waiting 0 seconds"),
    ({"action": "delay"}, "Waiting 0 seconds"),
    ({"action": "state.set", "key": "var.x", "value": 1}, "Setting var.x"),
    ({"action": "state.set"}, "Setting ?"),
    ({"action": "macro", "macro": "ghost"}, "Running macro ghost"),
    ({"action": "macro"}, "Running macro ?"),
    ({"action": "event.emit", "event": "custom.go"}, "Emitting custom.go"),
    ({"action": "event.emit"}, "Emitting ?"),
    ({"action": "conditional", "condition": {"key": "var.mode", "value": 1}},
     "Checking var.mode"),
    ({"action": "conditional", "condition": None}, "Checking ?"),
    ({"action": "wait_until", "timeout": 0, "on_timeout": "continue",
      "condition": {"key": "var.ready", "value": True}},
     "Waiting for var.ready (0s)"),
    ({"action": "wait_until", "timeout": None, "condition": None},
     "Waiting for ? (no timeout)"),
    ({"action": "help.request", "message": "x"}, "Asking for help"),
    ({"action": "help.clear"}, "Clearing the help request"),
    ({"action": "ui.navigate", "page": "$back"}, "Going back"),
    ({"action": "ui.navigate", "page": "$dismiss"}, "Dismissing overlay"),
    ({"action": "ui.navigate", "page": "lobby"}, "Navigating panel to lobby"),
    ({"action": "ui.navigate"}, "Navigating panel to ?"),
    ({"action": "acme.blink"}, "Blink the lamp"),
    ({"action": "acme.nothing"}, "acme.nothing"),
    ({"action": "delay", "seconds": 0, "description": "Let the lamp warm"},
     "Let the lamp warm"),
]


@pytest.mark.parametrize("step,expected", _DESCRIBED)
async def test_each_step_is_announced_in_its_own_words(
    engine: MacroEngine, events: EventBus, step: dict[str, Any], expected: str,
) -> None:
    async def blink(_params: dict[str, Any], _context: dict[str, Any]) -> None:
        return None

    engine.register_plugin_action("acme.blink", blink, "acme_plugin", "Blink the lamp")
    progress = _record(events, "macro.progress.*")
    engine.load_macros([{"id": "m", "name": "M", "steps": [step]}])
    await engine.execute("m")
    announced = [p["description"] for _e, p in progress if p.get("status") == "running"]
    assert announced == [expected]


async def test_a_plugin_action_with_no_label_is_announced_by_its_type(
    engine: MacroEngine, events: EventBus,
) -> None:
    async def blink(_params: dict[str, Any], _context: dict[str, Any]) -> None:
        return None

    engine.register_plugin_action("acme.blink", blink, "acme_plugin")
    progress = _record(events, "macro.progress.*")
    engine.load_macros([{"id": "m", "name": "M", "steps": [{"action": "acme.blink"}]}])
    await engine.execute("m")
    assert [p["description"] for _e, p in progress] == ["acme.blink"]


# --- where a nested step sits ---------------------------------------------------------


async def test_a_nested_step_reports_its_whole_path(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    state.set("var.a", 1)
    state.set("var.b", 0)
    progress = _record(events, "macro.progress.*")
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "delay", "seconds": 0},
        {"action": "conditional", "condition": {"key": "var.a", "value": 1},
         "then_steps": [
             {"action": "conditional", "condition": {"key": "var.b", "value": 1},
              "then_steps": [{"action": "delay", "seconds": 0}],
              "else_steps": [{"action": "delay", "seconds": 0},
                             {"action": "state.set", "key": "var.c", "value": 1}]},
         ],
         "else_steps": [{"action": "delay", "seconds": 0}]},
    ]}])
    assert await engine.execute("m") == "completed"
    running = [
        (p["step_path"], p["step_index"], p["total_steps"], p["action"])
        for _e, p in progress if p.get("status") == "running"
    ]
    assert running == [
        ([0], 0, 2, "delay"),
        ([1], 1, 2, "conditional"),
        ([1, "then", 0], 0, 1, "conditional"),
        ([1, "then", 0, "else", 0], 0, 2, "delay"),
        ([1, "then", 0, "else", 1], 1, 2, "state.set"),
    ]
    branches = [p["branch"] for _e, p in progress if p.get("status") == "evaluated"]
    assert branches == ["then", "else"]


async def test_a_failure_deep_in_a_branch_counts_toward_the_run(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    state.set("var.a", 1)
    errors = _record(events, "macro.step_error.*")
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "conditional", "condition": {"key": "var.a", "value": 1},
         "then_steps": [{"action": "acme.nothing"}, {"action": "acme.nothing"}]},
        {"action": "acme.nothing"},
    ]}])
    assert await engine.execute("m") == "failed"
    assert [(p["step_index"], p["total_steps"]) for _e, p in errors] == [(0, 2), (1, 2), (1, 2)]
    assert engine.is_macro_running("m") is False


async def test_a_branch_past_the_nesting_limit_fails_at_the_limit(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    state.set("var.a", 1)
    errors = _record(events, "macro.step_error.*")
    leaf: dict[str, Any] = {"action": "state.set", "key": "var.leaf", "value": 1}
    for _ in range(6):
        leaf = {"action": "conditional", "condition": {"key": "var.a", "value": 1},
                "then_steps": [leaf]}
    engine.load_macros([{"id": "m", "name": "M", "steps": [leaf]}])
    assert await engine.execute("m") == "failed"
    assert state.get("var.leaf") is None
    assert [p["error"] for _e, p in errors] == [
        "Conditional nesting depth limit (5) exceeded"
    ]
