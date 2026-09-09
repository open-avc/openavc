"""Deleting a script, and asking first what still reaches into one.

``check_references(type="script", ...)`` accepted `script` as a type, had no
branch for it, and fell through to an empty result — so the assistant asked the
question it is told to ask before a delete and was told, every time, that
nothing referenced the script. That is the Q-168 failure one type over: an
empty impact reads as an all-clear rather than as an unanswered question.

Two ways into a script and only two, both covered here: a control calls a
function in it, and an event it handles is emitted. The first has a wrinkle
worth pinning — a hand-authored binding names only the function, and which
script that reaches is the runtime's question, so the walk asks the script
engine rather than guessing.

The AI's ``get_script_health`` is tested here too, beside the walk, because it
is the other half of the same blindness: the platform knew a script was failing
and the one surface being asked "why does this button do nothing" could not see
it.

Every script, page and event here is invented.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, save_project
from openavc.core.script_references import (
    as_reference_report,
    find_references_to_script,
)

LIGHTS_SOURCE = '''
"""The script the project reaches into."""

import openavc


@openavc.on_event("custom.room.ready")
def on_ready(event):
    pass


@openavc.on_event("custom.nobody.says.this")
def on_never(event):
    pass


def lights_on(level=100):
    pass
'''

OTHER_SOURCE = '''
def lights_on():
    """The same name in a second script, which is the whole ambiguity."""
'''


def _seed() -> dict:
    return {
        "project": {"id": "screfs", "name": "Script References"},
        "macros": [
            {
                "id": "arrive", "name": "Arrive",
                "steps": [{"action": "event.emit", "event": "custom.room.ready"}],
            },
        ],
        "scripts": [
            {"id": "lights", "file": "lights.py"},
            {"id": "other", "file": "other.py"},
        ],
        "ui": {
            "pages": [
                {"id": "home", "name": "Home", "elements": [
                    # Written by the Builder: it recorded which script it meant.
                    {"id": "on_btn", "type": "button", "label": "Lights On",
                     "bindings": {"do.press": [{"action": "script.call",
                                                "script": "lights",
                                                "function": "lights_on"}]}},
                    # Hand-authored: the name has to stand on its own.
                    {"id": "bare_btn", "type": "button", "label": "Bare",
                     "bindings": {"do.press": {"action": "script.call",
                                               "function": "lights_on"}}},
                    {"id": "unrelated", "type": "button", "label": "Other",
                     "bindings": {"do.press": [{"action": "macro", "macro": "arrive"}]}},
                ]},
            ],
            "master_elements": [
                {"id": "nav", "type": "button", "label": "Home", "pages": "*",
                 "bindings": {"do.press": [{"action": "script.call",
                                            "script": "lights",
                                            "function": "go_home"}]}},
            ],
        },
    }


@pytest.fixture
async def refs_engine(tmp_path):
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig.model_validate(_seed())
    save_project(project_path, engine.project)

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "lights.py").write_text(LIGHTS_SOURCE, encoding="utf-8")
    (scripts_dir / "other.py").write_text(OTHER_SOURCE, encoding="utf-8")

    rest.set_engine(engine)
    ws.set_engine(engine)
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    try:
        yield engine, handler
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


def _sources(engine) -> dict[str, str]:
    from openavc.core.event_references import project_script_sources

    return project_script_sources(engine.project, engine.project_path.parent / "scripts")


def _defines(mapping: dict[str, set[str]]):
    """A stand-in for the script engine's own answer, so the walk can be
    exercised without loading modules."""
    return lambda script_id, name: name in mapping.get(script_id, set())


# --- The walk --------------------------------------------------------------


async def test_a_binding_that_names_the_script_is_a_reference(refs_engine) -> None:
    engine, _handler = refs_engine
    refs = find_references_to_script(engine.project, "lights")
    called = [r.where["element_id"] for r in refs if r.kind == "binding"]
    assert called == ["on_btn", "nav"]


async def test_a_master_element_counts(refs_engine) -> None:
    """It carries its own bindings and is drawn on every page, so a script it
    calls is reached everywhere — and a walk that read only pages[].elements
    would not see it at all."""
    engine, _handler = refs_engine
    refs = find_references_to_script(engine.project, "lights")
    master = [r for r in refs if r.where.get("element_id") == "nav"]
    assert master and master[0].message == 'UI master element "Home": go_home()'


async def test_a_bare_function_name_is_resolved_the_way_a_press_resolves_it(
    refs_engine,
) -> None:
    """`bare_btn` names only `lights_on`, which BOTH scripts define. Which one
    it reaches is not this module's guess — it is what the engine answers."""
    engine, _handler = refs_engine

    to_lights = find_references_to_script(
        engine.project, "lights",
        defines_function=_defines({"lights": {"lights_on"}, "other": {"lights_on"}}),
    )
    to_other = find_references_to_script(
        engine.project, "other",
        defines_function=_defines({"lights": {"lights_on"}, "other": {"lights_on"}}),
    )

    assert "bare_btn" in [r.where.get("element_id") for r in to_lights]
    assert "bare_btn" in [r.where.get("element_id") for r in to_other]


async def test_a_bare_name_no_script_defines_reaches_nothing(refs_engine) -> None:
    engine, _handler = refs_engine
    refs = find_references_to_script(
        engine.project, "other", defines_function=_defines({"other": {"something_else"}}),
    )
    assert refs == []


async def test_an_event_the_project_emits_is_a_reference(refs_engine) -> None:
    engine, _handler = refs_engine
    refs = find_references_to_script(engine.project, "lights", sources=_sources(engine))
    events = [r.where["event"] for r in refs if r.kind == "event"]
    assert events == ["custom.room.ready"]


async def test_a_handler_nothing_emits_is_not_a_reference(refs_engine) -> None:
    """It is a dead listener, which is a different finding on a different door.
    Reporting it here would say the script is depended on by something that
    never speaks to it."""
    engine, _handler = refs_engine
    refs = find_references_to_script(engine.project, "lights", sources=_sources(engine))
    assert "custom.nobody.says.this" not in [r.where.get("event") for r in refs]


async def test_a_script_nothing_points_at_reports_nothing(refs_engine) -> None:
    engine, _handler = refs_engine
    assert as_reference_report(
        find_references_to_script(engine.project, "other", sources=_sources(engine))
    ) == {}


# --- The AI door -----------------------------------------------------------


async def test_check_references_no_longer_answers_nothing(refs_engine) -> None:
    """The finding itself: this used to be `{}` for every script in every
    project."""
    _engine, handler = refs_engine
    refs = (await handler._check_references({"type": "script", "id": "lights"}))["referenced_by"]

    assert [b["element_id"] for b in refs["bindings"]] == ["on_btn", "nav"]
    assert [e["event"] for e in refs["events"]] == ["custom.room.ready"]


async def test_a_bare_name_resolves_through_the_real_script_engine(refs_engine) -> None:
    """The stand-in above proves the walk; this proves the wiring.

    Both scripts really load here, so `bare_btn`'s lone `lights_on` is resolved
    by ``ScriptEngine.find_callable`` — the same call the press makes — and both
    scripts are reported, because either one of them may be what runs.
    """
    engine, handler = refs_engine
    from openavc.core.event_bus import EventBus
    from openavc.core.script_engine import ScriptEngine
    from openavc.core.state_store import StateStore

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    scripts = ScriptEngine(state, events, MagicMock(), engine.project_path.parent)
    scripts.install()
    try:
        scripts.load_scripts([s.model_dump() for s in engine.project.scripts])
        engine.scripts = scripts

        assert {sid for sid, _ in scripts.find_callable("lights_on")} == {"lights", "other"}
        for script_id in ("lights", "other"):
            refs = (await handler._check_references(
                {"type": "script", "id": script_id}
            ))["referenced_by"]
            assert "bare_btn" in [b["element_id"] for b in refs["bindings"]]
    finally:
        scripts.unload_all()


async def test_delete_script_hands_back_the_same_impact(refs_engine) -> None:
    """The other three delete tools report impact; this one answered
    `{"status": "deleted"}` and nothing else."""
    _engine, handler = refs_engine
    result = await handler._delete_script({"script_id": "lights"})
    assert result["status"] == "deleted"
    assert [b["element_id"] for b in result["impact"]["bindings"]] == ["on_btn", "nav"]


async def test_deleting_an_unreferenced_script_carries_no_impact_key(refs_engine) -> None:
    _engine, handler = refs_engine
    assert "impact" not in await handler._delete_script({"script_id": "other"})


# --- Script health ---------------------------------------------------------


class _FakeScripts:
    """The three stores, as the engine exposes them."""

    def __init__(self, errors=None, abandoned=None, runtime=None):
        self._errors = errors or {}
        self._abandoned = abandoned or {}
        self._runtime = runtime or {}

    def get_load_errors(self) -> dict[str, str]:
        return dict(self._errors)

    def get_abandoned_loads(self) -> dict[str, Any]:
        return dict(self._abandoned)

    def get_runtime_errors(self) -> dict[str, Any]:
        return dict(self._runtime)

    def find_callable(self, name: str, script: str = "") -> list:
        return []


async def test_script_health_reports_all_three_ways_a_script_can_fail(
    refs_engine,
) -> None:
    engine, handler = refs_engine
    engine.scripts = _FakeScripts(
        errors={"lights": "SyntaxError: invalid syntax (lights.py, line 3)"},
        abandoned={"slow": {"attempts": 2, "running": True, "since": "2026-09-09T12:00:00Z"}},
        runtime={"other": {"count": 7, "handler": "on_ready", "error": "KeyError: 'level'"}},
    )

    health = await handler._get_script_health({})

    assert health["errors"]["lights"].startswith("SyntaxError")
    assert health["abandoned"]["slow"]["running"] is True
    assert health["runtime"]["other"]["count"] == 7


async def test_script_health_narrows_to_one_script(refs_engine) -> None:
    engine, handler = refs_engine
    engine.scripts = _FakeScripts(
        errors={"lights": "boom"},
        runtime={"other": {"count": 1}},
    )

    health = await handler._get_script_health({"script_id": "lights"})

    assert health == {"errors": {"lights": "boom"}}


async def test_the_tool_is_reachable_by_name_and_reads_nothing(refs_engine) -> None:
    """Through the dispatch table rather than the method, because a tool that
    is written and not wired behaves exactly like one that was never written --
    and it belongs in the read-only set, which is what lets it be asked while
    the project is being changed."""
    engine, handler = refs_engine
    engine.scripts = _FakeScripts(errors={"lights": "boom"})

    assert "get_script_health" in handler._READ_ONLY_TOOLS
    result = await handler._tools["get_script_health"]({})
    assert result["errors"] == {"lights": "boom"}


async def test_a_healthy_project_answers_empty_rather_than_erroring(refs_engine) -> None:
    """"Nothing is broken" is the answer to the question, not a failure to
    answer it."""
    engine, handler = refs_engine
    engine.scripts = _FakeScripts()

    assert await handler._get_script_health({}) == {
        "errors": {}, "abandoned": {}, "runtime": {},
    }
