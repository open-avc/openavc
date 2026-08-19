"""The lint door the macro editor knocks on, and the two lines it must not cross.

A macro can be saved half-built. The IDE says "Saved", and in the room that
step quietly does nothing -- and because the save was clean there is no reason
to ever open that macro again, so the hunt goes to the projector, the cable and
the network. ``POST /api/macros/validate`` is the door that makes it visible.

Two properties matter more than any single rule here, and neither is visible
from inside one call:

1. **It is the same rules, once.** The refusal string the cloud AI's macro
   tools raise and the placed records the editor draws come out of one
   traversal in ``core/macro_validation``. A second copy is what Q-053 removed
   and what §109 refuses to reintroduce, so the parity test below compares them
   finding for finding rather than trusting the shared call.
2. **Nothing it reports blocks anything.** Half-built is a normal state while
   somebody is editing, so a macro this endpoint flags still saves through
   ``PUT /api/project`` with a 200. If that ever becomes a 422, the fix was
   turned into the thing §109 explicitly did not want.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.rest import router, set_engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_validation import macro_issues, validate_macro
from openavc.core.state_store import StateStore


@pytest.fixture
def client(tmp_path):
    """The app with a real macro engine behind it.

    Real rather than mocked because the plugin-action list is the one thing
    the browser could not work out for itself, and a MagicMock would answer
    that question with a MagicMock.
    """
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)

    engine = MagicMock()
    engine.macros = MacroEngine(state, events, MagicMock())
    engine.project_path = tmp_path / "project.avc"
    engine._project_revision = 0
    engine.apply_project = AsyncMock(return_value=1)
    engine.broadcast_ws = AsyncMock()

    app = FastAPI()
    app.include_router(router)
    set_engine(engine)
    yield TestClient(app, raise_server_exceptions=False), engine
    set_engine(None)


def _lint(http, *macros) -> dict:
    resp = http.post("/api/macros/validate", json={"macros": list(macros)})
    assert resp.status_code == 200, resp.text
    return resp.json()["macros"]


# --- What it reports, and where it puts it ---------------------------------


def test_an_incomplete_step_is_reported_against_its_own_row(client):
    """The delay somebody added and never filled in."""
    http, _ = client
    result = _lint(http, {
        "id": "macro_start",
        "steps": [
            {"action": "device.command", "device": "proj", "command": "power_on"},
            {"action": "delay"},
        ],
    })
    issues = result["macro_start"]["issues"]
    assert issues == [{
        "scope": "step",
        "index": 1,
        "path": "steps[1]",
        "message": "delay step requires 'seconds'",
    }]


def test_a_device_command_with_no_command_chosen_is_reported(client):
    """§109's own example: the device is picked, the command never was."""
    http, _ = client
    issues = _lint(http, {
        "id": "macro_start",
        "steps": [{"action": "device.command", "device": "proj"}],
    })["macro_start"]["issues"]
    assert [i["message"] for i in issues] == ["device.command step requires 'command'"]


def test_a_problem_inside_a_conditional_marks_the_step_holding_it(client):
    """The row somebody has to open is the conditional, not the branch."""
    http, _ = client
    issues = _lint(http, {
        "id": "macro_start",
        "steps": [{
            "action": "conditional",
            "condition": {"key": "var.mode", "operator": "eq", "value": "present"},
            "then_steps": [{"action": "delay"}],
        }],
    })["macro_start"]["issues"]
    assert len(issues) == 1
    assert issues[0]["scope"] == "step"
    assert issues[0]["index"] == 0
    # The full location still rides along, because the sentence alone would
    # not say which of the two branches it is about.
    assert issues[0]["path"] == "steps[0].then_steps[0]"


def test_a_trigger_problem_is_scoped_to_the_trigger_list(client):
    """Same lint, other editor: a bad cron and an operator name nothing knows."""
    http, _ = client
    issues = _lint(http, {
        "id": "macro_start",
        "steps": [],
        "triggers": [
            {"id": "t1", "type": "schedule", "cron": "0 8 * *"},
            {"id": "t2", "type": "state_change", "state_key": "var.mode",
             "state_operator": "is"},
        ],
    })["macro_start"]["issues"]
    assert [(i["scope"], i["index"]) for i in issues] == [("trigger", 0), ("trigger", 1)]
    assert "5 or 6 fields" in issues[0]["message"]
    assert "'is' is not valid" in issues[1]["message"]


def test_a_complete_macro_reports_nothing(client):
    """The badge has to be off for everything that works, or it is noise."""
    http, _ = client
    issues = _lint(http, {
        "id": "macro_start",
        "steps": [
            {"action": "device.command", "device": "proj", "command": "power_on"},
            {"action": "delay", "seconds": 2},
            {"action": "state.set", "key": "var.mode", "value": "present"},
            {"action": "help.request"},
        ],
        "triggers": [{"id": "t1", "type": "schedule", "cron": "0 8 * * 1-5"}],
    })["macro_start"]["issues"]
    assert issues == []


def test_every_macro_posted_comes_back_keyed_by_its_id(client):
    """One call marks the whole list -- a project's worth of macros must not
    be a project's worth of requests."""
    http, _ = client
    result = _lint(
        http,
        {"id": "macro_a", "steps": [{"action": "delay"}]},
        {"id": "macro_b", "steps": [{"action": "delay", "seconds": 1}]},
        {"id": "macro_c", "steps": [{"action": "macro"}]},
    )
    assert set(result) == {"macro_a", "macro_b", "macro_c"}
    assert len(result["macro_a"]["issues"]) == 1
    assert result["macro_b"]["issues"] == []
    assert len(result["macro_c"]["issues"]) == 1


def test_a_macro_with_no_steps_yet_is_not_a_problem(client):
    """Somebody who just clicked + has an empty macro, not a broken one."""
    http, _ = client
    assert _lint(http, {"id": "macro_new", "steps": []})["macro_new"]["issues"] == []


# --- The plugin-action case, which is why this is not in the browser --------


def test_a_plugin_action_the_engine_has_loaded_is_not_flagged(client):
    """A lint that flags a working step is worse than no lint at all.

    Only the running engine knows which plugin actions are registered, so a
    copy of these rules in TypeScript would mark every plugin step red.
    """
    http, engine = client

    async def handler(params, context):
        return None

    step = {"action": "audio_player.play", "params": {"file": "chime.wav"}}

    before = _lint(http, {"id": "macro_a", "steps": [step]})["macro_a"]["issues"]
    assert len(before) == 1, "an unknown action is flagged when nothing registers it"

    engine.macros.register_plugin_action("audio_player.play", handler, "audio_player")
    after = _lint(http, {"id": "macro_a", "steps": [step]})["macro_a"]["issues"]
    assert after == []


def test_an_action_nothing_dispatches_is_flagged_with_the_usable_names(client):
    """A typo'd action is the case that runs and does nothing at all."""
    http, _ = client
    issues = _lint(http, {
        "id": "macro_a",
        "steps": [{"action": "device.comand", "device": "proj", "command": "on"}],
    })["macro_a"]["issues"]
    assert len(issues) == 1
    assert "'device.comand' is not valid" in issues[0]["message"]
    assert "device.command" in issues[0]["message"]


# --- The envelope ----------------------------------------------------------


@pytest.mark.parametrize("body", [
    {},
    {"macros": {"macro_a": {}}},
    [],
    {"macros": ["macro_a"]},
    {"macros": [{"steps": []}]},
    {"macros": [{"id": "", "steps": []}]},
])
def test_a_malformed_request_is_refused_in_words(client, body):
    """The envelope is ours, so a bad one is a bug to say out loud -- unlike
    the macros inside it, where being half-built is the normal case."""
    http, _ = client
    resp = http.post("/api/macros/validate", json=body)
    assert resp.status_code == 400
    assert isinstance(resp.json()["detail"], str)


def test_the_lint_writes_nothing(client):
    """It is asked while somebody is mid-edit, on macros that are not saved."""
    http, engine = client
    _lint(http, {"id": "macro_a", "steps": [{"action": "delay"}]})
    engine.apply_project.assert_not_called()
    assert engine._project_revision == 0


# --- The two properties the whole thing rests on ---------------------------


_CORPUS: list[tuple[list, list]] = [
    ([], []),
    ([{"action": "delay"}], []),
    ([{"action": "delay", "seconds": -1}], []),
    ([{"action": "nonsense"}], []),
    ([{"action": ""}], []),
    ([{}], []),
    (["not a step"], []),
    ([{"action": "device.command"}], []),
    ([{"action": "group.command", "group": "displays"}], []),
    ([{"action": "state.set", "key": "var.a", "skip_if": {"key": "", "operator": "eq"}}], []),
    ([{"action": "ui.navigate"}], []),
    ([{"action": "event.emit"}], []),
    ([{"action": "wait_until", "condition": {"key": "device.p.power", "operator": "nope"}}], []),
    ([{"action": "wait_until", "condition": {"key": "device.p.power"}, "timeout": "soon"}], []),
    ([{"action": "conditional", "condition": {"operator": "eq"},
       "then_steps": [{"action": "delay"}], "else_steps": [{"action": "macro"}]}], []),
    ([{"action": "delay", "seconds": 1}], [{"id": "t", "type": "nonsense"}]),
    ([], [{"id": "t", "type": "schedule"}]),
    ([], [{"id": "t", "type": "schedule", "cron": "* * *"}]),
    ([], [{"id": "t", "type": "state_change"}]),
    ([], [{"id": "t", "type": "event"}]),
    ([], [{"id": "t", "type": "event", "event_pattern": "x", "overlap": "sometimes"}]),
    ([], [{"id": "t", "type": "startup", "conditions": [{"key": "", "operator": "nope"}]}]),
    ([], ["not a trigger"]),
    ([{"action": "delay"}], [{"id": "t", "type": "schedule", "cron": "bad"}]),
]


@pytest.mark.parametrize("steps,triggers", _CORPUS)
def test_the_refusal_and_the_placed_records_are_the_same_findings(steps, triggers):
    """One traversal, rendered two ways.

    ``validate_macro`` is what refuses a macro the AI generates;
    ``macro_issues`` is what marks a macro somebody built by hand. §109's whole
    point is that those are the same rules, so this reassembles the records
    back into the string and demands they match exactly -- a rule added to one
    and not the other shows up here rather than in a room.
    """
    placed = macro_issues(steps, triggers)
    rebuilt = "; ".join(
        f"{i['path']}: {i['message']}" if i["path"] else i["message"] for i in placed
    )
    refusal = validate_macro(steps, triggers)
    if not placed:
        assert refusal is None
    else:
        assert refusal == "Macro validation failed: " + rebuilt


def test_the_corpus_actually_trips_the_rules():
    """Parity over a corpus that finds nothing passes for free."""
    flagged = [(s, t) for s, t in _CORPUS if macro_issues(s, t)]
    assert len(flagged) >= len(_CORPUS) - 2
    kinds = {i["message"] for s, t in _CORPUS for i in macro_issues(s, t)}
    assert len(kinds) >= 12, sorted(kinds)


def test_a_macro_the_lint_flags_still_saves(client):
    """The line §109 drew: show it, refuse nothing.

    A half-built step is what editing looks like. If this ever returns 422 the
    lint has been wired into the save path, which is the outcome Q-053
    deliberately did not choose.
    """
    http, _ = client
    flagged = {
        "id": "macro_start",
        "name": "Start",
        "steps": [{"action": "delay"}, {"action": "device.command", "device": "proj"}],
        "triggers": [{"id": "t1", "type": "schedule", "cron": "not a cron"}],
    }
    assert _lint(http, flagged)["macro_start"]["issues"], "precondition: the lint flags it"

    resp = http.put("/api/project", json={
        "openavc_version": "0.11.0",
        "project": {"id": "room", "name": "Room"},
        "devices": [],
        "macros": [flagged],
    })
    assert resp.status_code == 200, resp.text
