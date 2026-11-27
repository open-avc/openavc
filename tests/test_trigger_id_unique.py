"""A trigger's id names one trigger in the whole project.

The trigger engine keeps one trigger per id (``TriggerEngine.load_triggers``):
the last one it loads, in the macros' order and then each macro's triggers'.
Every earlier trigger with that id never fires, and its card in the editor
reads the other one's runs. Nothing about the project file stops two
triggers sharing an id, so the macro lint marks each one that never fires,
naming the trigger that takes its place, and the AI's macro tools refuse a
trigger whose id another trigger already has, whichever of the two the
engine would keep.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.rest import router, set_engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_validation import macro_issues, validate_macro
from openavc.core.project_loader import MacroConfig, ProjectConfig, ProjectMeta, TriggerConfig
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine


def _trigger(tid: str, **fields) -> dict:
    return {"id": tid, "type": "event", "event_pattern": "custom.acme_widget_ready", **fields}


def _project(*macros: dict) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="acme_room", name="Acme Room"),
        macros=[MacroConfig(**m) for m in macros],
    )


@pytest.fixture
def client():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    engine = MagicMock()
    engine.macros = MacroEngine(state, events, MagicMock())
    app = FastAPI()
    app.include_router(router)
    set_engine(engine)
    yield TestClient(app, raise_server_exceptions=False)
    set_engine(None)


def _lint(http, *macros) -> dict:
    resp = http.post("/api/macros/validate", json={"macros": list(macros)})
    assert resp.status_code == 200, resp.text
    return resp.json()["macros"]


def _same_id_issues(result: dict, macro_id: str) -> list[dict]:
    return [i for i in result.get(macro_id, {}).get("issues", []) if "same_id_as" in i]


# --- The lint: each trigger that never fires, and the one that takes its place


def test_the_lint_marks_the_earlier_of_two_triggers_in_one_macro(client):
    result = _lint(client, {
        "id": "widget_on", "steps": [],
        "triggers": [_trigger("t1"), _trigger("t1", event_pattern="custom.other")],
    })
    issues = _same_id_issues(result, "widget_on")
    assert [(i["scope"], i["index"]) for i in issues] == [("trigger", 0)]
    assert issues[0]["same_id_as"] == {"index": 1}
    assert "triggers[1]" in issues[0]["message"]


def test_the_lint_reads_every_macro_posted_together(client):
    """A trigger in an earlier macro loses its id to one in a later macro:
    the earlier one is marked, naming the macro that took it."""
    result = _lint(
        client,
        {"id": "widget_on", "steps": [], "triggers": [_trigger("t1")]},
        {"id": "widget_off", "steps": [], "triggers": [_trigger("other"), _trigger("t1")]},
    )
    issues = _same_id_issues(result, "widget_on")
    assert [(i["scope"], i["index"]) for i in issues] == [("trigger", 0)]
    assert issues[0]["same_id_as"] == {"macro": "widget_off", "index": 1}
    assert "widget_off" in issues[0]["message"]
    assert _same_id_issues(result, "widget_off") == []


def test_triggers_with_ids_of_their_own_are_not_marked(client):
    result = _lint(
        client,
        {"id": "widget_on", "steps": [], "triggers": [_trigger("t1"), _trigger("t2")]},
        {"id": "widget_off", "steps": [], "triggers": [_trigger("t3")]},
    )
    assert _same_id_issues(result, "widget_on") == []
    assert _same_id_issues(result, "widget_off") == []


@pytest.mark.parametrize("macros", [
    # Two in one macro.
    [{"id": "a", "name": "A", "triggers": [_trigger("t1"), _trigger("t1")]}],
    # Across two macros, three holders.
    [
        {"id": "a", "name": "A", "triggers": [_trigger("t1")]},
        {"id": "b", "name": "B", "triggers": [_trigger("t1"), _trigger("t2")]},
        {"id": "c", "name": "C", "triggers": [_trigger("t1")]},
    ],
    # The one kept is turned off: the engine still keeps it.
    [
        {"id": "a", "name": "A", "triggers": [_trigger("t1")]},
        {"id": "b", "name": "B", "triggers": [_trigger("t1", enabled=False)]},
    ],
])
def test_the_one_left_unmarked_is_the_one_the_trigger_engine_keeps(client, macros):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    engine = TriggerEngine(state, events, MagicMock())
    engine.load_triggers(macros)

    result = _lint(client, *({"id": m["id"], "steps": [], "triggers": m["triggers"]} for m in macros))
    for macro in macros:
        marked = {i["index"] for i in _same_id_issues(result, macro["id"])}
        for index, trigger in enumerate(macro["triggers"]):
            kept = engine._triggers[trigger["id"]]
            is_kept = kept.macro_id == macro["id"] and kept.trigger is trigger
            assert (index not in marked) == is_kept, (macro["id"], index)


def test_a_trigger_with_no_id_is_no_holder(client):
    """The engine loads no trigger without an id, so none of them takes
    another's place."""
    result = _lint(client, {
        "id": "widget_on", "steps": [],
        "triggers": [_trigger(""), _trigger(""), "not a trigger"],
    })
    assert _same_id_issues(result, "widget_on") == []


def test_the_same_id_finding_is_one_the_editor_counts_as_wont_run():
    issues = macro_issues([], [_trigger("t1"), _trigger("t1")])
    same = [i for i in issues if "same_id_as" in i]
    assert len(same) == 1 and not same[0].get("warning")


# --- The AI's doors: refused, whichever trigger the engine would keep --------


def test_a_macro_reusing_another_macros_trigger_id_is_refused():
    """The new macro goes last, so the engine would keep ITS trigger and the
    existing one would stop firing: refused all the same."""
    project = _project({"id": "widget_on", "name": "Widget On", "triggers": [_trigger("t1")]})
    refusal = validate_macro([], [_trigger("t1")], project, macro_id="widget_off")
    assert refusal is not None
    assert "triggers[0]" in refusal and "'t1'" in refusal and "widget_on" in refusal


def test_an_update_resending_its_own_trigger_ids_is_not_refused():
    project = _project(
        {"id": "widget_on", "name": "Widget On", "triggers": [_trigger("t1"), _trigger("t2")]},
        {"id": "widget_off", "name": "Widget Off", "triggers": [_trigger("t3")]},
    )
    assert validate_macro([], [_trigger("t1"), _trigger("t2")], project, macro_id="widget_on") is None


def test_an_update_taking_another_macros_trigger_id_is_refused():
    project = _project(
        {"id": "widget_on", "name": "Widget On", "triggers": [_trigger("t1")]},
        {"id": "widget_off", "name": "Widget Off", "triggers": [_trigger("t3")]},
    )
    refusal = validate_macro([], [_trigger("t3")], project, macro_id="widget_on")
    assert refusal is not None and "widget_off" in refusal


def test_two_triggers_sharing_an_id_in_one_macro_are_refused():
    refusal = validate_macro([], [_trigger("t1"), _trigger("t1")])
    assert refusal is not None and "triggers[0]" in refusal


def test_the_project_has_no_say_on_a_trigger_id_it_does_not_hold():
    project = _project({"id": "widget_on", "name": "Widget On", "triggers": [_trigger("t1")]})
    assert validate_macro([], [_trigger("t2")], project, macro_id="widget_off") is None


def test_the_models_carry_the_id_the_check_reads():
    """The AI's door reads the project's models, not dicts."""
    project = _project({"id": "widget_on", "name": "Widget On", "triggers": [_trigger("t1")]})
    assert isinstance(project.macros[0].triggers[0], TriggerConfig)
    assert validate_macro([], [_trigger("t1")], project, macro_id="widget_off") is not None


# --- The save stays shape-only ----------------------------------------------


def test_a_project_with_two_triggers_sharing_an_id_still_loads():
    """A hand-edited or imported file opens, and the lint says which trigger
    never fires."""
    project = _project(
        {"id": "widget_on", "name": "Widget On", "triggers": [_trigger("t1")]},
        {"id": "widget_off", "name": "Widget Off", "triggers": [_trigger("t1")]},
    )
    assert [m.triggers[0].id for m in project.macros] == ["t1", "t1"]

