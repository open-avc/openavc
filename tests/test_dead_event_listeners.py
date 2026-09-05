"""A handler waiting for an event nothing in the project ever sends.

The other half of the dead-control failure, and the half nothing used to
catch. A `do` binding naming an action the runtime cannot dispatch is warned
at the write door and logged at runtime; a script carrying
`@on_event("custom.select_source")` for an event no macro, control or other
script emits produces no error, no warning and no log line. The handler below
it can be perfectly correct -- which is exactly why the hunt goes to the
projector, the cable and the network. That shape shipped in a starter template
for months.

Two properties matter more than any single case here:

1. **It only speaks where it can be sure.** `custom.` is the one namespace
   closed to the project: a plugin's emit is auto-prefixed, a peer instance's
   is prefixed. An OUTSIDE system may emit into it (the WebSocket's
   `event.emit`, `POST /api/events`), which is why the sentence says the
   handler runs only if something outside emits it, never that it never runs.
   Everywhere else the emitter set is open, so a warning would eventually fire
   on a working handler and teach people to stop reading warnings. The
   out-of-scope cases below are as load-bearing as the in-scope ones.
2. **Silence is the safe answer.** An emit whose name is built at runtime, a
   source that does not parse -- every unreadable thing counts as "could be
   this one", never as "nothing emits it". This check may not be the reason
   somebody deletes a handler that works.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.auth import require_claimed_auth
from openavc.api.rest import router, set_engine
from openavc.core.event_references import (
    dead_listeners,
    event_listeners,
    project_emitters,
    script_emitters,
)
from openavc.core.project_loader import ProjectConfig, ProjectMeta, ScriptConfig

# The shape the starter template shipped: the routing logic was right and the
# event never arrived.
DEAD_HANDLER = '''
from openavc import on_event, devices

@on_event("custom.select_source")
async def route_source(event):
    await devices.switcher.route(input=event.payload["input"])
'''


def _events(sources, project=None) -> list[str]:
    """Just the event names warned about, across every script."""
    return [
        issue["event"]
        for issues in dead_listeners(sources, project).values()
        for issue in issues
    ]


def _project(macros=None, elements=None, masters=None) -> dict:
    return {
        "macros": macros or [],
        "ui": {
            "pages": [{"id": "main", "elements": elements or []}],
            "master_elements": masters or [],
        },
    }


def _button(do: dict) -> dict:
    return {"id": "b1", "type": "button", "bindings": {"do": do}}


# --- The failure this exists for -------------------------------------------


def test_a_handler_nothing_emits_is_reported_where_the_pattern_is_written():
    issues = dead_listeners({"router": DEAD_HANDLER}, _project())

    assert list(issues) == ["router"]
    (issue,) = issues["router"]
    assert issue["event"] == "custom.select_source"
    # The decorator's line, not the function's: the handler is fine and the
    # pattern is what is wrong.
    assert DEAD_HANDLER.splitlines()[issue["line"] - 1].strip() == (
        '@on_event("custom.select_source")'
    )
    assert "runs only if an outside system emits it" in issue["message"]
    assert "never runs" not in issue["message"]


def test_a_script_with_nothing_wrong_is_absent_rather_than_empty():
    project = _project(macros=[
        {"id": "m", "steps": [{"action": "event.emit", "event": "custom.select_source"}]}
    ])
    assert dead_listeners({"router": DEAD_HANDLER}, project) == {}


# --- Every door that can emit ----------------------------------------------


def test_a_macro_step_emits_it():
    project = _project(macros=[
        {"id": "m", "steps": [{"action": "event.emit", "event": "custom.select_source"}]}
    ])
    assert _events({"router": DEAD_HANDLER}, project) == []


def test_a_macro_step_inside_a_conditional_branch_emits_it():
    """A conditional's branches are steps too, and the walk has to go in.

    A room's source selection is exactly where an emit ends up nested: emit
    one event if the lectern PC is awake and another if it is not.
    """
    project = _project(macros=[{
        "id": "m",
        "steps": [{
            "action": "conditional",
            "condition": {"key": "var.mode", "operator": "eq", "value": "pc"},
            "then_steps": [{"action": "event.emit", "event": "custom.select_source"}],
            "else_steps": [{"action": "event.emit", "event": "custom.select_laptop"}],
        }],
    }])
    assert _events({"router": DEAD_HANDLER}, project) == []


def test_a_control_binding_emits_it():
    project = _project(elements=[
        _button({"tap": [{"action": "event.emit", "event": "custom.select_source"}]})
    ])
    assert _events({"router": DEAD_HANDLER}, project) == []


def test_a_toggles_off_action_emits_it():
    """The half of a toggle that turns the room off is not a lesser case."""
    project = _project(elements=[_button({"tap": [{
        "action": "state.set",
        "key": "var.on",
        "off_action": {"action": "event.emit", "event": "custom.select_source"},
    }]})])
    assert _events({"router": DEAD_HANDLER}, project) == []


def test_a_value_maps_entry_emits_it():
    project = _project(elements=[_button({"change": [{
        "action": "value_map",
        "map": {"1": {"action": "event.emit", "event": "custom.select_source"}},
    }]})])
    assert _events({"router": DEAD_HANDLER}, project) == []


def test_a_matrix_destinations_own_route_override_emits_it():
    """A destination's route list runs INSTEAD of the element's do.route."""
    project = _project(elements=[{
        "id": "m1",
        "type": "matrix",
        "bindings": {"do": {}},
        "matrix_config": {
            "sources": [{"value": 1, "label": "PC"}],
            "destinations": [{
                "value": 1,
                "label": "Stream",
                "route_key": "device.enc.state",
                "route": [{"action": "event.emit", "event": "custom.select_source"}],
            }],
        },
    }])
    assert _events({"router": DEAD_HANDLER}, project) == []


def test_a_master_element_emits_it():
    """A master draws on every page and carries bindings like any element."""
    project = _project(masters=[
        {"id": "help", "type": "button", "pages": "*",
         "bindings": {"do": {"tap": [{"action": "event.emit", "event": "custom.select_source"}]}}}
    ])
    assert _events({"router": DEAD_HANDLER}, project) == []


def test_another_script_emits_it():
    other = '''
from openavc import events

async def pick(name):
    await events.emit("custom.select_source", {"input": name})
'''
    assert _events({"router": DEAD_HANDLER, "picker": other}, _project()) == []


def test_a_script_that_emits_to_itself_is_a_normal_shape():
    both = DEAD_HANDLER + '''
async def later():
    await events.emit("custom.select_source", {"input": 1})
'''
    assert _events({"router": both}, _project()) == []


# --- Where it deliberately says nothing ------------------------------------


@pytest.mark.parametrize("pattern", [
    "device.projector.power",   # a driver's lifecycle
    "ui.tap.source_button",     # a panel
    "plugin.calendar.started",  # whatever is installed
    "isc.lobby.scene",          # another instance
    "cloud.command",            # the cloud
    "*",                        # a script watching everything
])
def test_only_the_custom_namespace_is_judged(pattern):
    """Everywhere else the emitter set is open, so there is nothing to know.

    A warning that fires on a working handler is worse than no warning: it
    teaches people that the warnings are noise, and then the real one is noise
    too.
    """
    source = f'''
from openavc import on_event

@on_event("{pattern}")
async def handler(event):
    pass
'''
    assert dead_listeners({"s": source}, _project()) == {}


def test_an_emit_built_at_runtime_silences_the_check():
    """`events.emit(name)` could be any name at all, so nothing is claimed."""
    dynamic = '''
from openavc import events

async def go(name):
    await events.emit(name, {})
'''
    assert _events({"router": DEAD_HANDLER, "dyn": dynamic}, _project()) == []


def test_a_listener_pattern_built_at_runtime_is_not_judged():
    source = '''
from openavc import on_event

PREFIX = "custom."

@on_event(PREFIX + "select_source")
async def handler(event):
    pass
'''
    assert dead_listeners({"s": source}, _project()) == {}


def test_a_source_that_does_not_parse_says_nothing_and_is_not_read_as_silent():
    """Mid-edit is not evidence. It reports nothing about the broken script,
    and does not conclude that the broken script emits nothing either."""
    broken = "def oops(:\n"
    assert event_listeners(broken) == []
    assert script_emitters(broken) == ["*"]
    assert _events({"router": DEAD_HANDLER, "broken": broken}, _project()) == []


# --- Globs, both ways round ------------------------------------------------


def test_a_wildcard_handler_is_reached_by_a_literal_emit():
    source = '''
from openavc import on_event

@on_event("custom.source.*")
async def handler(event):
    pass
'''
    project = _project(macros=[
        {"id": "m", "steps": [{"action": "event.emit", "event": "custom.source.hdmi1"}]}
    ])
    assert _events({"s": source}, project) == []
    # ...and with nothing under that family emitted, it is still reported.
    assert _events({"s": source}, _project()) == ["custom.source.*"]


def test_a_literal_handler_is_reached_by_an_f_string_emit():
    """An f-string keeps what it can: the family it emits into is readable."""
    source = '''
from openavc import on_event

@on_event("custom.source.hdmi1")
async def handler(event):
    pass
'''
    emitter = '''
from openavc import events

async def pick(port):
    await events.emit(f"custom.source.{port}", {})
'''
    assert _events({"s": source, "e": emitter}, _project()) == []
    # The f-string's literal half still limits what it can answer for.
    other = '''
from openavc import on_event

@on_event("custom.lights.on")
async def handler(event):
    pass
'''
    assert _events({"s": other, "e": emitter}, _project()) == ["custom.lights.on"]


# --- The pieces on their own -----------------------------------------------


def test_project_emitters_reads_both_halves_of_a_project():
    project = _project(
        macros=[{"id": "m", "steps": [{"action": "event.emit", "event": "custom.a"}]}],
        elements=[_button({"tap": [{"action": "event.emit", "event": "custom.b"}]})],
    )
    assert project_emitters(project) == {"custom.a", "custom.b"}


def test_a_step_or_action_that_is_not_an_emit_contributes_nothing():
    project = _project(
        macros=[{"id": "m", "steps": [{"action": "macro", "macro": "custom.a"}]}],
        elements=[_button({"tap": [{"action": "script.call", "function": "custom.b"}]})],
    )
    assert project_emitters(project) == set()


# --- The door the editor knocks on -----------------------------------------


@pytest.fixture
def client(tmp_path):
    """The app over a project directory with two scripts on disk."""
    project_dir = tmp_path / "project"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "scripts" / "router.py").write_text(DEAD_HANDLER, encoding="utf-8")
    (project_dir / "scripts" / "picker.py").write_text(
        "from openavc import events\n"
        "async def pick():\n"
        '    await events.emit("custom.select_source", {})\n',
        encoding="utf-8",
    )
    project_json = project_dir / "project.avc"
    project_json.write_text(json.dumps({
        "openavc_version": "0.1.0",
        "project": {"id": "t", "name": "T"},
    }), encoding="utf-8")

    engine = MagicMock()
    engine.project_path = project_json
    engine.project = ProjectConfig(
        project=ProjectMeta(id="t", name="T"),
        scripts=[
            ScriptConfig(id="router", file="router.py"),
            ScriptConfig(id="picker", file="picker.py"),
        ],
    )
    engine._project_revision = 0
    engine.apply_project_edit = AsyncMock(return_value=1)
    engine.reload_project = AsyncMock()

    app = FastAPI()
    app.include_router(router)
    # The save below is gated on the instance being claimed, which is a
    # separate rule about editing code at all and not this check's business.
    app.dependency_overrides[require_claimed_auth] = lambda: None
    set_engine(engine)
    yield TestClient(app, raise_server_exceptions=False), project_dir
    set_engine(None)


def _lint(http, *scripts) -> dict:
    resp = http.post("/api/scripts/validate", json={"scripts": list(scripts)})
    assert resp.status_code == 200, resp.text
    return resp.json()["scripts"]


def test_an_emit_in_a_script_the_editor_did_not_post_still_counts(client):
    """The whole point of reading the rest from disk.

    The editor asks about the script that is open. The emit that saves its
    handler is as likely to be in the script next to it, and a warning that
    depends on which tab you have open is worse than none.
    """
    http, _ = client
    result = _lint(http, {"id": "router", "source": DEAD_HANDLER})
    assert result["router"]["issues"] == []


def test_the_posted_source_is_what_is_checked_not_the_saved_copy(client):
    """Unsaved edits are the state the mark has to describe."""
    http, project_dir = client
    # Delete the emit from the OTHER script, in the editor only.
    result = _lint(
        http,
        {"id": "router", "source": DEAD_HANDLER},
        {"id": "picker", "source": "async def pick():\n    pass\n"},
    )
    assert [i["event"] for i in result["router"]["issues"]] == ["custom.select_source"]
    # Nothing was written: the file on disk still emits it.
    assert "custom.select_source" in (project_dir / "scripts" / "picker.py").read_text()


def test_every_posted_script_gets_an_answer_even_a_clean_one(client):
    http, _ = client
    result = _lint(http, {"id": "picker", "source": "x = 1\n"})
    assert result == {"picker": {"issues": []}}


def test_a_body_that_is_not_a_script_list_is_refused(client):
    http, _ = client
    assert http.post("/api/scripts/validate", json={"scripts": "all"}).status_code == 400
    assert http.post("/api/scripts/validate", json={}).status_code == 400
    assert http.post(
        "/api/scripts/validate", json={"scripts": [{"source": "x = 1"}]}
    ).status_code == 400


def test_it_refuses_nothing_a_flagged_script_still_saves(client):
    """The same policy the macro lint holds to: show it, block nothing.

    A handler written before the macro that will fire it is a normal state for
    somebody halfway through building a room.
    """
    http, project_dir = client
    result = _lint(http, {"id": "router", "source": DEAD_HANDLER},
                   {"id": "picker", "source": "x = 1\n"})
    assert result["router"]["issues"], "expected the dead handler to be reported"

    resp = http.put("/api/scripts/router/source", json={"source": DEAD_HANDLER})
    assert resp.status_code == 200, resp.text
    assert (project_dir / "scripts" / "router.py").read_text() == DEAD_HANDLER


def test_a_script_named_without_a_source_is_checked_as_it_is_on_disk(client):
    """How the list gets a mark on every row while one file is open."""
    http, _ = client
    # Both scripts, only the open one's text -- and the open one's edit is what
    # kills the emit the other script's handler depends on.
    result = _lint(http, {"id": "router"}, {"id": "picker", "source": "x = 1\n"})
    assert [i["event"] for i in result["router"]["issues"]] == ["custom.select_source"]
    assert result["picker"]["issues"] == []


def test_a_script_named_with_no_saved_copy_at_all_is_simply_clean(client):
    http, _ = client
    assert _lint(http, {"id": "never_saved"}) == {"never_saved": {"issues": []}}


# --- The other door that writes a script ------------------------------------


@pytest.fixture
def ai(tmp_path):
    """The AI tool handler over a project with one script and no emitters."""
    from openavc.api import rest
    from openavc.cloud.ai_tool_handler import AIToolHandler
    from openavc.core.project_loader import MacroConfig

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "router.py").write_text("x = 1\n", encoding="utf-8")

    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        scripts=[ScriptConfig(id="router", file="router.py")],
        macros=[MacroConfig(id="lights", name="Lights", steps=[
            {"action": "event.emit", "event": "custom.lights_on"},
        ])],
    )
    engine.project_path = tmp_path / "project.avc"
    engine.scripts = None
    rest.set_engine(engine)
    try:
        yield AIToolHandler(MagicMock(), MagicMock(), MagicMock()), engine
    finally:
        rest.set_engine(None)


async def test_the_ai_is_told_in_the_reply_it_is_already_reading(ai):
    """A generator finds out now, not a round trip later, and not never."""
    handler, _ = ai
    result = await handler._update_script_source(
        {"script_id": "router", "source": DEAD_HANDLER}
    )
    assert result["status"] == "saved"
    assert result["warnings"] == [
        'Nothing in this project emits "custom.select_source", so this handler runs '
        "only if an outside system emits it over the API. To fire it from here, emit "
        "it from a macro's Emit Event step, a control's Emit Event action, or "
        "events.emit() in another script."
    ]
    assert "warnings, not failures" in result["warning_note"]


async def test_the_write_still_lands_it_warns_and_does_not_refuse(ai, tmp_path):
    handler, _ = ai
    await handler._update_script_source({"script_id": "router", "source": DEAD_HANDLER})
    assert (tmp_path / "scripts" / "router.py").read_text(encoding="utf-8") == DEAD_HANDLER


async def test_a_handler_the_project_does_emit_for_is_not_warned_about(ai):
    handler, _ = ai
    good = DEAD_HANDLER.replace("custom.select_source", "custom.lights_on")
    result = await handler._update_script_source({"script_id": "router", "source": good})
    assert "warnings" not in result


async def test_a_created_script_is_read_the_same_way(ai):
    handler, engine = ai
    engine.apply_project_edit = AsyncMock(return_value=1)
    result = await handler._create_script(
        {"id": "second", "file": "second.py", "source": DEAD_HANDLER}
    )
    assert result["status"] == "created"
    assert [w.split('"')[1] for w in result["warnings"]] == ["custom.select_source"]
