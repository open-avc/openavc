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


# --- what a deliberate-break pass on the moved code found nothing else checks ---------


async def test_a_conditional_reports_the_value_it_compared(
    engine: MacroEngine, events: EventBus, state: StateStore,
) -> None:
    state.set("var.mode", 3)
    progress = _record(events, "macro.progress.*")
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "conditional", "condition": {"key": "var.mode", "operator": "gt", "value": 2}},
    ]}])
    await engine.execute("m")
    evaluated = [p for _e, p in progress if p.get("status") == "evaluated"]
    assert evaluated == [{
        "macro_id": "m", "action": "conditional", "condition_result": True,
        "branch": "then", "condition_key": "var.mode", "condition_operator": "gt",
        "condition_value": 2, "actual_value": 3, "status": "evaluated",
    }]


async def test_a_help_request_defaults_to_a_warning(engine: MacroEngine) -> None:
    help_requests = MagicMock()
    help_requests.raise_request = AsyncMock()
    engine._help = help_requests
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "help.request", "message": "Lamp out"},
    ]}])
    assert await engine.execute("m") == "completed"
    help_requests.raise_request.assert_awaited_once_with(
        message="Lamp out", severity="warning", cooldown=None,
    )


async def test_a_plugin_step_is_handed_the_run_context(engine: MacroEngine) -> None:
    seen: list[dict[str, Any]] = []

    async def blink(params: dict[str, Any], context: dict[str, Any]) -> None:
        seen.append({"params": params, "context": context})

    engine.register_plugin_action("acme.blink", blink, "acme_plugin")
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "acme.blink", "params": {"times": 3}},
    ]}])
    await engine.execute("m", {"event": "custom.go", "data": 1})
    assert seen == [{"params": {"times": 3}, "context": {"event": "custom.go", "data": 1}}]


async def test_a_branch_of_a_run_with_no_context_gets_its_own_empty_context(
    engine: MacroEngine, state: StateStore,
) -> None:
    """A plugin writing into the context inside a branch does not reach the
    steps after the branch when the run started with no trigger context."""
    async def write(_params: dict[str, Any], context: dict[str, Any]) -> None:
        context["seen"] = 1

    engine.register_plugin_action("acme.write", write, "acme_plugin")
    state.set("var.a", 1)
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "conditional", "condition": {"key": "var.a", "value": 1},
         "then_steps": [{"action": "acme.write"}]},
        {"action": "state.set", "key": "var.after", "value": "$trigger.seen"},
    ]}])
    await engine.execute("m")
    assert state.get("var.after") is None


async def test_a_wait_until_already_met_says_so_once_and_never_subscribes(
    engine: MacroEngine, events: EventBus, state: StateStore, monkeypatch,
) -> None:
    state.set("var.ready", True)
    subscribed: list[str] = []
    real_subscribe = state.subscribe
    monkeypatch.setattr(
        state, "subscribe", lambda p, cb: subscribed.append(p) or real_subscribe(p, cb),
    )
    progress = _record(events, "macro.progress.*")
    engine.load_macros([{"id": "w", "name": "W", "steps": [
        {"action": "wait_until", "timeout": 5,
         "condition": {"key": "var.ready", "operator": "eq", "value": True}},
    ]}])
    assert await engine.execute("w") == "completed"
    assert subscribed == []
    frames = [p for _e, p in progress if p.get("status") != "running"]
    assert frames == [{
        "macro_id": "w", "action": "wait_until", "condition_key": "var.ready",
        "condition_operator": "eq", "condition_value": True,
        "status": "satisfied", "waited_seconds": 0.0,
    }]


@pytest.mark.parametrize("timeout", [-1, "5", True and "x"])
async def test_a_wait_until_refuses_a_timeout_that_is_not_a_duration(
    engine: MacroEngine, events: EventBus, timeout: Any,
) -> None:
    errors = _record(events, "macro.step_error.*")
    engine.load_macros([{"id": "w", "name": "W", "steps": [
        {"action": "wait_until", "timeout": timeout, "condition": {"key": "var.x", "value": 1}},
    ]}])
    assert await engine.execute("w") == "failed"
    assert [p["error"] for _e, p in errors] == [
        f"wait_until 'timeout' must be a non-negative number or null, got {timeout!r}"
    ]


async def test_a_missing_member_of_an_unnamed_group_names_the_group_by_its_id(
    state: StateStore, events: EventBus, devices: MagicMock,
) -> None:
    engine = MacroEngine(state, events, devices, project_device_ids=lambda: {"acme1"})
    engine.load_groups([{"id": "wall", "device_ids": ["ghost"]}])
    progress = _record(events, "macro.progress.*")
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "group.command", "group": "wall", "command": "power_on"},
    ]}])
    await engine.execute("m")
    results = [p["device_results"] for _e, p in progress if p.get("status") == "group_complete"]
    assert results[0][0]["message"] == (
        "Device 'ghost' is listed in group 'wall' but is not in this project. "
        "Remove it from the group, or add the device back."
    )


async def test_a_command_field_on_another_step_is_not_named_in_its_failure(
    engine: MacroEngine, caplog,
) -> None:
    lines = await _failure_line(
        engine, [{"action": "macro", "macro": "ghost", "command": "power_on"}], caplog,
    )
    assert lines == ["Macro step failed: step 1/1, macro, calling 'ghost' — Macro 'ghost' not found"]


async def test_a_step_error_frame_counts_its_own_macro_in_the_chain(
    engine: MacroEngine, events: EventBus,
) -> None:
    errors = _record(events, "macro.step_error.*")
    engine.load_macros([
        {"id": "outer", "name": "Outer", "steps": [{"action": "macro", "macro": "inner"}]},
        {"id": "inner", "name": "Inner", "steps": [{"action": "acme.nothing"}]},
    ])
    await engine.execute("outer")
    assert [(e, p["call_chain"]) for e, p in errors] == [
        ("macro.step_error.inner", ["inner", "outer"]),
    ]
    await engine.execute("inner")
    assert errors[-1][1]["call_chain"] == ["inner"]


async def test_a_failed_step_with_no_device_is_worded_for_the_panel(
    engine: MacroEngine, events: EventBus,
) -> None:
    from openavc.api.error_messages import friendly_error
    from openavc.reach_devices import AdapterUnavailable

    async def broken(_params: dict[str, Any], _context: dict[str, Any]) -> None:
        raise AdapterUnavailable("en9")

    engine.register_plugin_action("acme.blink", broken, "acme_plugin")
    errors = _record(events, "macro.step_error.*")
    engine.load_macros([{"id": "m", "name": "M", "steps": [{"action": "acme.blink"}]}])
    await engine.execute("m")
    exc = AdapterUnavailable("en9")
    assert friendly_error(exc, at_panel=True) != friendly_error(exc, at_panel=False)
    assert errors[0][1]["message"] == friendly_error(exc, at_panel=True)


async def test_a_handler_of_a_skipped_run_may_start_that_macro_again(
    engine: MacroEngine, events: EventBus,
) -> None:
    """The skipped run's call chain is gone before its skip is announced, so a
    handler that starts the same macro is not refused as a circular call."""
    engine.load_macros([{
        "id": "solo", "name": "Solo", "overlap": "skip",
        "steps": [{"action": "delay", "seconds": 0.2}],
    }])
    refusals: list[str] = []

    async def again(_event: str, _payload: dict[str, Any]) -> None:
        try:
            engine.start("solo")
        except ValueError as exc:
            refusals.append(str(exc))

    events.on("macro.skipped.solo", again)
    running = asyncio.create_task(engine.execute("solo"))
    await asyncio.sleep(0.02)
    assert await engine.execute("solo") == "skipped"
    assert refusals == []
    await running
    await asyncio.sleep(0.25)


async def test_a_step_error_from_a_bare_step_list_names_its_macro_as_the_chain(
    engine: MacroEngine, events: EventBus,
) -> None:
    errors = _record(events, "macro.step_error.*")
    assert await engine.execute_steps([{"action": "acme.nothing"}], macro_id="bare") == 1
    assert errors[0][1]["call_chain"] == ["bare"]


async def test_a_skipped_run_leaves_the_callers_chain_as_it_was(
    engine: MacroEngine,
) -> None:
    from openavc.core.macro_engine import active_call_chain

    engine.load_macros([{
        "id": "solo", "name": "Solo", "overlap": "skip",
        "steps": [{"action": "delay", "seconds": 0.2}],
    }])
    running = asyncio.create_task(engine.execute("solo"))
    await asyncio.sleep(0.02)
    assert await engine.execute("solo") == "skipped"
    assert active_call_chain() == frozenset()
    await running


async def test_a_patched_step_method_is_the_one_that_runs(
    engine: MacroEngine, monkeypatch,
) -> None:
    patched = AsyncMock(return_value=None)
    monkeypatch.setattr(engine, "_delay", patched)
    engine.load_macros([{"id": "m", "name": "M", "steps": [{"action": "delay", "seconds": 30}]}])
    assert await asyncio.wait_for(engine.execute("m"), 1) == "completed"
    patched.assert_awaited_once()


async def test_a_group_step_sends_through_the_engines_current_devices(
    engine: MacroEngine, devices: MagicMock,
) -> None:
    replacement = MagicMock()
    replacement.send_command = AsyncMock()
    replacement.command_goes_out = MagicMock(return_value=True)
    replacement.get_driver = MagicMock(return_value=None)
    engine.devices = replacement
    engine.load_groups([{"id": "wall", "name": "Wall", "device_ids": ["acme1"]}])
    engine.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "group.command", "group": "wall", "command": "power_on"},
        {"action": "device.command", "device": "acme2", "command": "power_on"},
    ]}])
    assert await engine.execute("m") == "completed"
    assert [c.args[0] for c in replacement.send_command.call_args_list] == ["acme1", "acme2"]
    devices.send_command.assert_not_called()
