"""Why a trigger fire or a macro run did not go ahead: one table of reasons.

``SKIP_REASONS`` in ``core/macro_text.py`` holds every code a
``trigger.skipped`` or ``macro.skipped`` names, each with its sentence. These
tests hold the two engines to it: every exit that stops a fire or a run sends
one of those codes and its sentence, and no exit stops one without saying why.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import openavc.core.macro_engine as me
import openavc.core.trigger_engine as te
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_text import SKIP_REASONS, skip_reason
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine

CORE = Path(te.__file__).parent


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
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


def _record(events: EventBus, pattern: str) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def on(event: str, payload: dict[str, Any]) -> None:
        seen.append((event, dict(payload or {})))

    events.on(pattern, on)
    return seen


def _trigger_skip(trigger_id: str, code: str, message: str) -> dict[str, Any]:
    return {"trigger_id": trigger_id, "reason": code, "code": code, "message": message}


def _one_macro(trigger: dict[str, Any], macro_id: str = "m") -> list[dict[str, Any]]:
    return [{"id": macro_id, "name": "M", "triggers": [{"enabled": True, **trigger}]}]


# --- the table ------------------------------------------------------------------


def test_a_reason_is_its_code_and_its_sentence() -> None:
    assert skip_reason("cooldown") == {
        "code": "cooldown",
        "message": "The trigger's Cooldown had not passed since it last fired.",
    }
    assert skip_reason("macro_cooldown", seconds=60.0) == {
        "code": "macro_cooldown",
        "message": "The macro's Cooldown (60 seconds) had not passed since it last started.",
    }


@pytest.mark.parametrize(("seconds", "said"), [
    (1, "1 second"), (1.0, "1 second"), (0.06, "0.06 seconds"), (30, "30 seconds"),
    (2592000, "2,592,000 seconds"), (90.25, "90.25 seconds"),
])
def test_a_number_of_seconds_reads_as_a_person_says_it(seconds, said) -> None:
    assert skip_reason("macro_cooldown", seconds=seconds)["message"] == (
        f"The macro's Cooldown ({said}) had not passed since it last started."
    )


def test_a_code_the_table_does_not_hold_is_refused() -> None:
    with pytest.raises(KeyError):
        skip_reason("busy")


def test_every_sentence_is_one_a_person_can_read() -> None:
    for code, sentence in SKIP_REASONS.items():
        assert code == code.lower() and " " not in code, code
        assert sentence[0].isupper() and sentence.endswith("."), code
        assert "—" not in sentence, code
        assert "=" not in sentence, code


# --- the trigger exits that said nothing ----------------------------------------


async def test_a_state_trigger_whose_operator_nobody_knows_says_so(
    trigger_engine, core,
) -> None:
    state, events = core
    skipped = _record(events, "trigger.skipped")
    trigger_engine.load_triggers(_one_macro({
        "id": "t", "type": "state_change", "state_key": "var.level",
        "state_operator": "about", "state_value": 5,
    }))
    await trigger_engine.start()
    state.set("var.level", 5, source="test")
    await asyncio.sleep(0.02)
    assert skipped == [("trigger.skipped", _trigger_skip(
        "t", "invalid_condition",
        "A condition on the trigger uses 'about', an operator this system does not know.",
    ))]
    await trigger_engine.stop()


async def test_a_change_that_does_not_match_is_no_fire_and_no_skip(
    trigger_engine, macro_engine, core,
) -> None:
    """A State Change trigger fires when its key matches; any other change to
    the key is not a fire, so it is not a skipped one either."""
    state, events = core
    skipped = _record(events, "trigger.skipped")
    fired = _record(events, "trigger.fired")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_one_macro({
        "id": "t", "type": "state_change", "state_key": "var.power",
        "state_operator": "eq", "state_value": "on",
    }))
    await trigger_engine.start()
    state.set("var.power", "off", source="test")
    await asyncio.sleep(0.02)
    assert skipped == [] and fired == []
    state.set("var.power", "on", source="test")
    await asyncio.sleep(0.05)
    assert skipped == [] and len(fired) == 1
    await trigger_engine.stop()


async def test_a_delayed_state_trigger_whose_operator_nobody_knows_says_so(
    trigger_engine, core,
) -> None:
    state, events = core
    skipped = _record(events, "trigger.skipped")
    trigger_engine.load_triggers(_one_macro({
        "id": "t", "type": "state_change", "state_key": "var.level",
        "state_operator": "about", "state_value": 5, "delay_seconds": 1,
    }))
    trigger_engine._running = True
    ts = trigger_engine._triggers["t"]
    await trigger_engine._delayed_fire(ts, 0, {"key": "var.level"})
    assert skipped == [("trigger.skipped", _trigger_skip(
        "t", "invalid_condition",
        "A condition on the trigger uses 'about', an operator this system does not know.",
    ))]


def _queue_trigger(**extra: Any) -> list[dict[str, Any]]:
    return _one_macro({"id": "t", "type": "event", "event_pattern": "go", "overlap": "queue", **extra})


async def test_a_queued_fire_that_waited_too_long_says_so(
    trigger_engine, macro_engine, core, monkeypatch,
) -> None:
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    monkeypatch.setattr(te, "_QUEUE_MAX_WAIT_SECONDS", 0.06)
    state, events = core
    skipped = _record(events, "trigger.skipped")
    macro_engine.is_macro_running = lambda _mid: True
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger())
    await trigger_engine.start()
    await events.emit("go", {})
    await asyncio.sleep(0.2)
    assert skipped == [("trigger.skipped", _trigger_skip(
        "t", "queue_timeout",
        "The macro was still running after 0.06 seconds, so the trigger's queued fire was dropped.",
    ))]
    await trigger_engine.stop()


async def test_a_queued_fire_whose_trigger_was_turned_off_says_so(
    trigger_engine, macro_engine, core, monkeypatch,
) -> None:
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    state, events = core
    skipped = _record(events, "trigger.skipped")
    macro_engine.is_macro_running = lambda _mid: True
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger())
    await trigger_engine.start()
    await events.emit("go", {})
    await asyncio.sleep(0.05)
    trigger_engine._triggers["t"].trigger["enabled"] = False
    await asyncio.sleep(0.1)
    assert skipped == [("trigger.skipped", _trigger_skip(
        "t", "trigger_disabled",
        "The trigger was turned off while its fire waited in the queue.",
    ))]
    await trigger_engine.stop()


async def test_a_queued_fire_whose_conditions_went_false_says_so(
    trigger_engine, macro_engine, core, monkeypatch,
) -> None:
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    state, events = core
    state.set("var.guard", True, source="test")
    skipped = _record(events, "trigger.skipped")
    running = {"v": True}
    macro_engine.is_macro_running = lambda _mid: running["v"]
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_queue_trigger(
        conditions=[{"key": "var.guard", "operator": "eq", "value": True}],
    ))
    await trigger_engine.start()
    await events.emit("go", {})
    await asyncio.sleep(0.05)
    state.set("var.guard", False, source="test")
    running["v"] = False
    await asyncio.sleep(0.1)
    assert skipped == [("trigger.skipped", _trigger_skip(
        "t", "guard_not_met",
        "The trigger's conditions were no longer met when its wait ended.",
    ))]
    await trigger_engine.stop()


async def test_a_skip_that_already_had_a_code_now_carries_its_sentence(
    trigger_engine, macro_engine, core,
) -> None:
    state, events = core
    skipped = _record(events, "trigger.skipped")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    trigger_engine.load_triggers(_one_macro({
        "id": "t", "type": "event", "event_pattern": "go", "cooldown_seconds": 60,
    }))
    await trigger_engine.start()
    await events.emit("go", {})
    await asyncio.sleep(0.05)
    await events.emit("go", {})
    await asyncio.sleep(0.02)
    assert skipped == [("trigger.skipped", _trigger_skip(
        "t", "cooldown", "The trigger's Cooldown had not passed since it last fired.",
    ))]
    await trigger_engine.stop()


# --- the macro's own refusals ---------------------------------------------------


def _macro_skip(macro_id: str, name: str, code: str, message: str) -> dict[str, Any]:
    # `reason` is the sentence, as it has always been on this event.
    return {"macro_id": macro_id, "name": name, "reason": message, "code": code, "message": message}


async def test_a_macro_in_its_cooldown_says_so(macro_engine, core) -> None:
    _state, events = core
    skipped = _record(events, "macro.skipped.*")
    macro_engine.load_macros([{"id": "m", "name": "M", "cooldown_seconds": 30, "steps": []}])
    assert await macro_engine.execute("m") == "completed"
    assert await macro_engine.execute("m") == "skipped"
    assert skipped == [("macro.skipped.m", _macro_skip(
        "m", "M", "macro_cooldown",
        "The macro's Cooldown (30 seconds) had not passed since it last started.",
    ))]


async def test_a_macro_already_running_under_skip_says_so(macro_engine, core) -> None:
    _state, events = core
    skipped = _record(events, "macro.skipped.*")
    macro_engine.load_macros([{
        "id": "m", "name": "M", "overlap": "skip", "steps": [{"action": "delay", "seconds": 5}],
    }])
    running = asyncio.create_task(macro_engine.execute("m"))
    await asyncio.sleep(0.02)
    assert await macro_engine.execute("m") == "skipped"
    assert skipped == [("macro.skipped.m", _macro_skip(
        "m", "M", "macro_overlap",
        "The macro was already running, and the macro's Overlap is set to Skip.",
    ))]
    assert await macro_engine.cancel("m") is True
    assert await running == "cancelled"


async def test_a_queued_macro_start_that_waited_too_long_says_so(
    macro_engine, core, monkeypatch,
) -> None:
    monkeypatch.setattr(me, "_QUEUE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(me, "_QUEUE_MAX_WAIT_SECONDS", 0.06)
    _state, events = core
    skipped = _record(events, "macro.skipped.*")
    macro_engine.load_macros([{
        "id": "m", "name": "M", "overlap": "queue", "steps": [{"action": "delay", "seconds": 0.5}],
    }])
    first = asyncio.create_task(macro_engine.execute("m"))
    await asyncio.sleep(0.02)
    assert await macro_engine.execute("m") == "skipped"
    assert skipped == [("macro.skipped.m", _macro_skip(
        "m", "M", "macro_queue_timeout",
        "The macro was still running after 0.06 seconds, so this queued run was dropped.",
    ))]
    assert await first == "completed"


async def test_a_macro_started_while_the_system_stops_says_so(macro_engine, core) -> None:
    _state, events = core
    skipped = _record(events, "macro.skipped.*")
    macro_engine.load_macros([{"id": "m", "name": "M", "steps": []}])
    macro_engine.close()
    assert await macro_engine.execute("m") == "skipped"
    assert skipped == [("macro.skipped.m", _macro_skip(
        "m", "M", "stopping", "The system is stopping.",
    ))]


# --- every exit names a code in the table ---------------------------------------

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# Where the code sits in a call that names one: `skip_reason(code, ...)` and
# `self._skipped(trigger_id, code, ...)`.
_CODE_POSITION = {"skip_reason": 0, "_skipped": 1}

_SKIP_EVENTS = ("trigger.skipped", "macro.skipped")


def _module_tree(module) -> ast.Module:
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _functions(tree: ast.AST) -> list[ast.AST]:
    return [n for n in ast.walk(tree) if isinstance(n, _FUNCTION_TYPES)]


def _called_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _code_argument(call: ast.Call) -> ast.expr | None:
    """The argument that names a reason's code, when ``call`` is such a call."""
    position = _CODE_POSITION.get(_called_name(call))
    if position is None or len(call.args) <= position:
        return None
    return call.args[position]


def _is_text(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _codes_named(module, helper: str) -> set[str]:
    """Every code the module names, each a literal; only ``helper`` itself may
    pass one through."""
    tree = _module_tree(module)
    helper_node = next(fn for fn in _functions(tree) if fn.name == helper)
    inside_helper = {id(n) for n in ast.walk(helper_node)}
    codes: set[str] = set()
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        arg = _code_argument(call)
        if _is_text(arg):
            codes.add(arg.value)
            continue
        assert arg is None or id(call) in inside_helper, (
            f"{module.__name__}:{call.lineno} names a reason that is not a literal code"
        )
    return codes


def test_every_code_an_engine_names_is_in_the_table_and_every_row_is_used() -> None:
    named = _codes_named(te, "_skipped") | _codes_named(me, "_report_skipped")
    assert named - set(SKIP_REASONS) == set()
    assert set(SKIP_REASONS) - named == set()


def _names_a_skip_event(node: ast.AST) -> bool:
    return _is_text(node) and node.value.startswith(_SKIP_EVENTS)


def _event_names(module) -> set[tuple[str, str]]:
    """(function, text) for each string in the module naming a skip event."""
    return {
        (fn.name, node.value)
        for fn in _functions(_module_tree(module))
        for node in ast.walk(fn)
        if _names_a_skip_event(node)
    }


def test_each_engine_sends_its_skip_from_one_place() -> None:
    assert _event_names(te) == {("_skipped", "trigger.skipped")}
    assert _event_names(me) == {("_report_skipped", "macro.skipped.")}


# The trigger's fire path, from a listener to the macro engine. Every `return`
# in these hands the fire on (`trigger.pending`, `trigger.queued`), says why it
# stopped (`_skipped`), or is one of the exits below, which are no fire at all.
# The check reads `return` statements in these functions only: a new function
# on the path joins this list, and an exit that ends by falling off the end of
# one is out of its sight.
_FIRE_PATH = (
    "_on_state_change", "_on_event", "_fire_startup", "_initiate_fire",
    "_debounced_fire", "_schedule_fire", "_delayed_fire", "_execute_trigger",
    "_queued_fire",
)

# (function, a word in the `if` that guards the return): why it is no fire.
_NO_FIRE = {
    # The engine is stopping or stopped; stop() ends what is pending.
    ("*", "self._running"),
    # A trigger turned off has no listener; nothing reaches here for it.
    ("_initiate_fire", "enabled"),
    # A State Change trigger fires when its key matches, so a change that does
    # not match is not a fire.
    ("_on_state_change", "matched"),
}

# What a statement before a `return` says when the fire went on or was refused.
_SAYS_WHY = ("self._skipped(", '"trigger.pending"', '"trigger.queued"')


def _blocks_inside(stmt: ast.stmt, guard: ast.AST | None):
    """(statements, guard) for each block inside ``stmt``: an `if`'s blocks are
    guarded by it, a handler's by the handler, the rest by ``guard``."""
    inner = stmt if isinstance(stmt, ast.If) else guard
    for field in ("body", "orelse", "finalbody"):
        block = getattr(stmt, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            yield block, inner
    for handler in getattr(stmt, "handlers", None) or []:
        yield handler.body, handler


def _returns_with_their_block(stmts: list[ast.stmt], guard: ast.AST | None = None):
    """Each `return` in ``stmts``, the statements before it in its block, and the
    `if` (or `except`) it sits in."""
    for i, stmt in enumerate(stmts):
        if isinstance(stmt, ast.Return):
            yield stmt, stmts[:i], guard
        for block, inner in _blocks_inside(stmt, guard):
            yield from _returns_with_their_block(block, inner)


def _text(source: str, node: ast.AST | None) -> str:
    return (ast.get_source_segment(source, node) if node is not None else None) or ""


def _says_why(stmts: list[ast.stmt], source: str) -> bool:
    return any(mark in _text(source, stmt) for stmt in stmts for mark in _SAYS_WHY)


def _is_no_fire(name: str, guard: ast.AST | None, source: str) -> bool:
    if isinstance(guard, ast.ExceptHandler):
        return "CancelledError" in _text(source, guard.type)
    test = _text(source, guard.test) if isinstance(guard, ast.If) else ""
    return any(f in (name, "*") and word in test for f, word in _NO_FIRE)


def _fire_path(source: str) -> dict[str, ast.AST]:
    engine = next(
        n for n in ast.parse(source).body
        if isinstance(n, ast.ClassDef) and n.name == TriggerEngine.__name__
    )
    return {n.name: n for n in engine.body if isinstance(n, _FUNCTION_TYPES) and n.name in _FIRE_PATH}


def test_every_exit_on_a_triggers_fire_path_says_why_it_stopped() -> None:
    source = Path(te.__file__).read_text(encoding="utf-8")
    functions = _fire_path(source)
    assert set(functions) == set(_FIRE_PATH)
    silent = [
        f"{name}: {_text(source, ret)} (line {ret.lineno})"
        for name, fn in functions.items()
        for ret, before, guard in _returns_with_their_block(fn.body)
        if not (_says_why(before, source) or _is_no_fire(name, guard, source))
    ]
    assert silent == []
