"""Every producer of a macro run hands over the fields the table lists.

``value_resolver.FIRING_CONTEXTS`` lists what each producer (a trigger by its
type, a control, Fire now) puts in a run's firing context; the IDE's picker,
the trigger editor and docs/macros-and-triggers.md list fields from it. Each
producer is fired here through its real path and the context it hands the
macro is compared with its row, so a field a producer starts sending that the
table lacks, or one the table lists and nothing sends, fails here. A sweep of
the code finds every call that hands a macro a context, so a new door is one
this file knows about. The docs' table names the same fields.

Every device, control and event here is invented.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import openavc.core.trigger_engine as te
from openavc.core.device_manager import DeviceManager
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.project_loader import load_project
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine
from openavc.core.value_resolver import FIRING_CONTEXTS

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "macros-and-triggers.md"

#: A payload an event trigger's event carries, beside the fields it adds.
EVENT_PAYLOAD = {"source": "hdmi1", "level": 4}

Captured = list[dict[str, Any] | None]


def _table_fields(producer: str) -> set[str]:
    return {field.name for field in FIRING_CONTEXTS[producer].fields}


def _engines() -> tuple[StateStore, EventBus, MacroEngine, TriggerEngine, Captured]:
    """A trigger engine whose macro runs are captured rather than executed."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    macros = MacroEngine(state, events, devices)
    macros.load_macros([{"id": "m", "name": "M", "steps": []}])
    captured: Captured = []

    async def execute(_macro_id, context=None, **_kwargs):
        captured.append(context)
        return "completed"

    macros.execute = execute
    return state, events, macros, TriggerEngine(state, events, macros), captured


def _load(triggers: TriggerEngine, trigger: dict[str, Any]) -> None:
    triggers.load_triggers([{
        "id": "m", "name": "M",
        "triggers": [{"id": "t", "enabled": True, **trigger}],
    }])


async def _first_run(captured: Captured) -> dict[str, Any] | None:
    for _ in range(2000):  # up to 10 s on a slow runner
        if captured:
            return captured[0]
        await asyncio.sleep(0.005)
    raise AssertionError("the producer never ran the macro")


async def _state_change(monkeypatch, tmp_path) -> dict[str, Any] | None:
    state, _events, _macros, triggers, captured = _engines()
    _load(triggers, {"type": "state_change", "state_key": "var.level", "state_operator": "any"})
    await triggers.start()
    try:
        state.set("var.level", 5)
        return await _first_run(captured)
    finally:
        await triggers.stop()


async def _event(monkeypatch, tmp_path) -> dict[str, Any] | None:
    _state, events, _macros, triggers, captured = _engines()
    _load(triggers, {"type": "event", "event_pattern": "custom.source_changed"})
    await triggers.start()
    try:
        await events.emit("custom.source_changed", dict(EVENT_PAYLOAD))
        return await _first_run(captured)
    finally:
        await triggers.stop()


async def _schedule(monkeypatch, tmp_path) -> dict[str, Any] | None:
    _state, _events, _macros, triggers, captured = _engines()
    _load(triggers, {"type": "schedule", "cron": "0 18 * * *"})
    monkeypatch.setattr(te, "_CRON_POLL_INTERVAL", 0.005)
    monkeypatch.setattr(triggers, "_schedule_prev_due", lambda _cron, now, _last: (now, True))
    await triggers.start()
    try:
        return await _first_run(captured)
    finally:
        await triggers.stop()


async def _startup(monkeypatch, tmp_path) -> dict[str, Any] | None:
    _state, _events, _macros, triggers, captured = _engines()
    _load(triggers, {"type": "startup", "delay_seconds": 0})
    await triggers.start()
    try:
        return await _first_run(captured)
    finally:
        await triggers.stop()


async def _fire_now(monkeypatch, tmp_path) -> dict[str, Any] | None:
    _state, _events, _macros, triggers, captured = _engines()
    _load(triggers, {"type": "state_change", "state_key": "var.level", "state_operator": "any"})
    await triggers.test_trigger("t")
    return await _first_run(captured)


async def _press(monkeypatch, tmp_path) -> dict[str, Any] | None:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.14.0",
        "project": {"id": "p", "name": "P", "description": ""},
        "devices": [],
        "macros": [{"id": "m", "name": "M", "steps": []}],
        "ui": {"settings": {}, "pages": [{
            "id": "main", "name": "Main", "layouts": [],
            "elements": [{
                "id": "vol", "type": "slider", "label": "Volume", "tag": "main",
                "bindings": {"do": {"change": [{"action": "macro", "macro": "m"}]}},
            }],
        }]},
    }), encoding="utf-8")
    engine = Engine(path)
    engine.project = load_project(engine.project_path)
    engine.macros.load_macros([m.model_dump() for m in engine.project.macros])
    captured: Captured = []

    async def execute(_macro_id, context=None, **_kwargs):
        captured.append(context)

    engine.macros.execute = execute
    await engine.handle_ui_event("change", "vol", {"value": 42})
    return await _first_run(captured)


#: How each producer in the table is fired here.
PRODUCERS: dict[str, Callable[..., Awaitable[dict[str, Any] | None]]] = {
    "state_change": _state_change,
    "event": _event,
    "schedule": _schedule,
    "startup": _startup,
    "press": _press,
    "fire_now": _fire_now,
}


def test_every_producer_in_the_table_is_fired_here() -> None:
    assert set(PRODUCERS) == set(FIRING_CONTEXTS)


@pytest.mark.parametrize("producer", sorted(PRODUCERS))
async def test_a_producer_hands_over_the_fields_its_row_lists(producer, monkeypatch, tmp_path) -> None:
    context = await PRODUCERS[producer](monkeypatch, tmp_path)
    sent = set(context or {})
    if FIRING_CONTEXTS[producer].carries_payload:
        assert EVENT_PAYLOAD.items() <= (context or {}).items(), (
            f"{producer}: the event's own payload does not reach the macro"
        )
        sent -= set(EVENT_PAYLOAD)
    assert sent == _table_fields(producer), (
        f"{producer} hands its macro {sorted(sent)}, and FIRING_CONTEXTS in "
        f"openavc/core/value_resolver.py lists {sorted(_table_fields(producer))}: "
        f"list the field there and run python -m openavc.core.firing_context_gen"
    )


def test_a_field_has_one_description_wherever_it_appears() -> None:
    """The picker lists a field once, with its description, whichever
    producer hands it over."""
    seen: dict[str, str] = {}
    for context in FIRING_CONTEXTS.values():
        for field in context.fields:
            assert seen.setdefault(field.name, field.description) == field.description, field.name


# --- Every door that hands a macro a context -----------------------------------

#: Each call that hands a macro run a context, by file and the function it is
#: in, and where that context comes from.
KNOWN_DOORS = {
    # Each trigger type's context, built where its trigger fires.
    ("openavc/core/trigger_engine.py", "_fire_macro"),
    # A control's macro action.
    ("openavc/core/ui_events.py", "execute_action"),
    # A Run Macro step hands on its own run's context.
    ("openavc/core/macro_steps.py", "_call_macro"),
    # The engine's own entry points hand on the context they were given.
    ("openavc/core/macro_engine.py", "start"),
    ("openavc/core/macro_engine.py", "execute_detached"),
}

_MACRO_RUNS = {"execute", "execute_detached", "start"}

#: Where a receiver other than ``<...>macros`` is the macro engine: ``self`` in
#: the engine and its steps, the script API's macros proxy's ``self._engine``.
_MACRO_ENGINE_RECEIVERS = {
    ("openavc/core/macro_engine.py", "self"),
    ("openavc/core/macro_steps.py", "self"),
    ("openavc/core/script_api.py", "self._engine"),
}


def _doors_handing_a_context() -> set[tuple[str, str]]:
    found = set()
    for path in sorted((REPO / "openavc").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in _MACRO_RUNS:
                    continue
                receiver = ast.unparse(node.func.value)
                on_macros = (
                    receiver.endswith("macros") or (rel, receiver) in _MACRO_ENGINE_RECEIVERS
                )
                hands_context = len(node.args) >= 2 or any(
                    k.arg == "context" for k in node.keywords
                )
                if on_macros and hands_context:
                    found.add((rel, func.name))
    return found


def test_every_door_that_hands_a_macro_a_context_is_known() -> None:
    found = _doors_handing_a_context()
    assert found == KNOWN_DOORS, (
        f"new: {sorted(found - KNOWN_DOORS)}; gone: {sorted(KNOWN_DOORS - found)}. "
        f"A new door that hands a macro a context lists its fields in "
        f"FIRING_CONTEXTS (openavc/core/value_resolver.py) and is fired in PRODUCERS here"
    )


# --- The docs ------------------------------------------------------------------

_DOC_ROW = re.compile(r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<reads>[^|]+?)\s*\|$")


def _doc_rows() -> dict[str, str]:
    """The rows of the docs' "What ran the macro" table, by its first cell."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index("| What ran the macro |")
    rows = {}
    for line in text[start:].splitlines()[2:]:
        match = _DOC_ROW.match(line)
        if not match:
            break
        rows[match["name"]] = match["reads"]
    return rows


def test_the_docs_table_lists_every_producer_and_its_fields() -> None:
    rows = _doc_rows()
    assert set(rows) == {context.name for context in FIRING_CONTEXTS.values()}
    for context in FIRING_CONTEXTS.values():
        reads = rows[context.name]
        named = set(re.findall(r"`\$trigger\.([a-z_]+)`", reads))
        assert named == {field.name for field in context.fields}, context.name
        assert ("every field of the event" in reads) == context.carries_payload, context.name
