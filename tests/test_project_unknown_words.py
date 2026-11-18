"""A project a later release saved loads on this one.

Two things a later release can put in a project would otherwise stop it
loading here, and with a rollback or a fleet push to an older system that
project would be set aside for a backup:

- a word in a fixed-choice field this release does not know (a trigger type,
  an overlap, an orientation, a picture size, a page's contents). Each such
  field declares its words and what a word it does not know reads as
  (``project_loader.OneOf``); the word is kept as written, so the project goes
  back to the release that saved it unchanged, and the load says what it does
  here. A save or an AI edit that adds such a word is refused; one the running
  project already holds is kept.
- a value past a bound, which an earlier release took and saved into a project
  stamped with the current format (it skips the format step that brings it
  inside). Every load brings each bounded value inside today's bound, whatever
  the project's version, and says so; a save still refuses one.
"""

from __future__ import annotations

import copy
import json
import typing
from typing import Literal
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openavc.core import project_update
from openavc.core.engine import Engine
from openavc.core.project_loader import (
    DriverDependency,
    Layout,
    MacroConfig,
    MacroStep,
    OneOf,
    PageBackground,
    PluginDependency,
    ProjectConfig,
    TriggerConfig,
    UIPage,
    load_project,
    reads_as,
    save_project,
)
from openavc.core.project_migration import CURRENT_VERSION, migrate_project
from openavc.core.project_update import CHANGED, FOUND, MigrationNotes
from openavc.core.project_values_gen import _walk

UNKNOWN = "from_a_later_version"

#: Each fixed-choice field and what a word it does not know reads as.
CHOICES = {
    (TriggerConfig, "type"): None,
    (TriggerConfig, "overlap"): "skip",
    (MacroConfig, "overlap"): "allow",
    (MacroStep, "on_timeout"): "fail",
    (Layout, "orientation"): None,
    (PageBackground, "image_size"): "cover",
    (UIPage, "render_mode"): "elements",
    (DriverDependency, "source"): "unknown",
    (PluginDependency, "source"): "unknown",
}


def _project(version: str = CURRENT_VERSION, word: str = UNKNOWN) -> dict:
    """A project holding ``word`` in every fixed-choice field."""
    return {
        "openavc_version": version,
        "project": {"id": "p", "name": "P"},
        "driver_dependencies": [{"driver_id": "acme_widget", "source": word}],
        "plugin_dependencies": [{"plugin_id": "acme_tool", "source": word}],
        "macros": [{
            "id": "warmup", "name": "Warm Up", "overlap": word,
            "steps": [{"action": "wait_until", "condition": {"key": "var.ready"},
                       "timeout": 5, "on_timeout": word}],
            "triggers": [
                {"id": "t1", "type": word},
                {"id": "t2", "type": "startup", "overlap": word},
            ],
        }],
        "ui": {"pages": [{
            "id": "main", "name": "Main", "render_mode": word,
            "background": {"image": "assets://bg.png", "image_size": word},
            "elements": [],
            "layouts": [
                {"id": "landscape", "orientation": "landscape", "primary": True},
                {"id": "odd", "orientation": word},
            ],
        }]},
    }


def _literals(annotation) -> bool:
    if typing.get_origin(annotation) is Literal:
        return True
    return any(_literals(arg) for arg in typing.get_args(annotation))


def _declared_choices() -> dict:
    found = {}
    for model in _walk((ProjectConfig,)):
        for field, info in model.model_fields.items():
            for rule in info.metadata:
                if isinstance(rule, OneOf) and field in vars(model).get("__annotations__", {}):
                    found[(model, field)] = rule
    return found


# --- the schema ---------------------------------------------------------------


def test_no_project_field_refuses_a_word_it_does_not_know():
    refusing = [
        f"{model.__name__}.{field}"
        for model in _walk((ProjectConfig,))
        for field, info in model.model_fields.items()
        if _literals(info.annotation)
    ]
    assert refusing == []


def test_every_fixed_choice_field_declares_what_a_word_it_does_not_know_reads_as():
    assert set(_declared_choices()) == set(CHOICES)
    for (model, field), fallback in CHOICES.items():
        assert reads_as(model, field, UNKNOWN) == fallback, f"{model.__name__}.{field}"


def test_a_known_word_reads_as_itself_and_a_blank_one_as_the_default():
    for (model, field), rule in _declared_choices().items():
        for word in rule.words:
            assert reads_as(model, field, word) == word
        default = model.model_fields[field].default
        if isinstance(default, str):
            assert reads_as(model, field, default) == default
    assert reads_as(MacroStep, "on_timeout", None) == "fail"
    assert reads_as(TriggerConfig, "overlap", None) == "skip"
    assert reads_as(MacroConfig, "overlap", None) == "allow"


def test_a_word_is_still_text():
    with pytest.raises(ValueError):
        TriggerConfig(id="t", type=5)


def test_a_project_holding_words_this_release_does_not_know_loads_and_is_saved_as_written(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project()), encoding="utf-8")

    project = load_project(path)
    macro = project.macros[0]
    page = project.ui.pages[0]
    assert macro.overlap == UNKNOWN
    assert macro.steps[0].on_timeout == UNKNOWN
    assert [t.type for t in macro.triggers] == [UNKNOWN, "startup"]
    assert macro.triggers[1].overlap == UNKNOWN
    assert page.render_mode == UNKNOWN
    assert page.background.image_size == UNKNOWN
    assert page.layouts[1].orientation == UNKNOWN

    save_project(path, project)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["macros"][0]["overlap"] == UNKNOWN
    assert written["macros"][0]["triggers"][0]["type"] == UNKNOWN
    assert written["ui"]["pages"][0]["layouts"][1]["orientation"] == UNKNOWN


# --- what the load says -------------------------------------------------------


def _said(data: dict) -> list[tuple[str, str, str]]:
    notes = MigrationNotes()
    migrate_project(data, notes)
    return [(n.kind, n.location, n.message) for n in notes.notes]


def test_the_load_says_what_each_word_it_does_not_know_does_here():
    w = f"'{UNKNOWN}'"
    assert _said(_project()) == [
        (FOUND, "Macros > Warm Up",
         f"Overlap {w} is not one this version of OpenAVC knows, so it works as Allow."),
        (FOUND, "Macros > Warm Up > step 1",
         f"On timeout {w} is not one this version of OpenAVC knows, so a timeout fails the macro."),
        (FOUND, "Macros > Warm Up > t1",
         f"Trigger type {w} is not one this version of OpenAVC knows, so the trigger never fires."),
        (FOUND, "Macros > Warm Up > t2",
         f"Overlap {w} is not one this version of OpenAVC knows, so it works as Skip."),
        (FOUND, "Main > page",
         f"Contents {w} is not one this version of OpenAVC knows, so the page draws the controls placed on it."),
        (FOUND, "Main > page",
         f"Background picture size {w} is not one this version of OpenAVC knows, so the picture is drawn as Cover."),
        (FOUND, "Main > page",
         f'Orientation {w} of layout "odd" is not one this version of OpenAVC knows, '
         f"so no screen is matched to that layout."),
    ]


def test_a_project_with_only_words_this_release_knows_says_nothing():
    data = _project(word="queue")
    data["macros"][0]["steps"][0]["on_timeout"] = "continue"
    data["macros"][0]["triggers"][0]["type"] = "startup"
    data["ui"]["pages"][0]["render_mode"] = "elements"
    data["ui"]["pages"][0]["background"]["image_size"] = "contain"
    data["ui"]["pages"][0]["layouts"][1]["orientation"] = "portrait"
    data["driver_dependencies"][0]["source"] = "community"
    data["plugin_dependencies"][0]["source"] = "community"
    assert _said(data) == []


def test_a_dismissed_note_the_next_load_finds_again_is_not_raised_again(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project()), encoding="utf-8")
    load_project(path, keep_notes=True)
    assert project_update.read(tmp_path) is not None

    engine_state = _State()
    assert project_update.dismiss(engine_state, tmp_path) is True
    load_project(path, keep_notes=True)

    assert project_update.read(tmp_path) is None
    assert engine_state.values[project_update.KEY_AT] == ""


def test_a_new_note_after_a_dismiss_is_raised_with_only_itself(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project()), encoding="utf-8")
    load_project(path, keep_notes=True)
    project_update.dismiss(_State(), tmp_path)

    data = json.loads(path.read_text(encoding="utf-8"))
    data["ui"]["pages"][0]["elements"] = [{"id": "level", "type": "slider", "display_decimals": 50}]
    path.write_text(json.dumps(data), encoding="utf-8")
    load_project(path, keep_notes=True)

    notes = project_update.read(tmp_path)["notes"]
    assert [(n["kind"], n["location"]) for n in notes] == [(CHANGED, "Main > level")]


def test_a_value_rewritten_again_after_a_dismiss_is_said_again(tmp_path):
    """A backup from before a repair, restored: the load rewrites the value
    again, and that is news even though the same note was dismissed."""
    path = tmp_path / "project.avc"
    data = _past_bounds()
    path.write_text(json.dumps(data), encoding="utf-8")
    load_project(path, keep_notes=True)
    project_update.dismiss(_State(), tmp_path)

    path.write_text(json.dumps(data), encoding="utf-8")
    load_project(path, keep_notes=True)

    notes = project_update.read(tmp_path)["notes"]
    assert ("Main > volume", "Shown decimals changed from 50 to 20. A readout shows 0 to 20.") in [
        (n["location"], n["message"]) for n in notes
    ]


@pytest.mark.asyncio
async def test_a_project_pushed_from_the_cloud_with_a_later_word_and_a_value_past_a_bound_is_applied(
    tmp_path, monkeypatch,
):
    from openavc.cloud.command_handler import CommandHandler
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    engine, _ = await _edit_rig(tmp_path, monkeypatch, _plain())
    sent = []

    class _Agent:
        async def send_message(self, msg_type, payload):
            sent.append(payload)

    state, events = StateStore(), EventBus()
    handler = CommandHandler(
        _Agent(), DeviceManager(state, events), events,
        apply_fn=engine.replace_project, project_path=str(engine.project_path),
    )
    pushed = _past_bounds()
    pushed["macros"][0]["triggers"].append({"id": "t9", "type": UNKNOWN})

    await handler._handle_config_push({"project_json": pushed, "mode": "full_replace"}, "req", "tech@x.com")

    assert sent[-1]["success"] is True, sent[-1]
    assert [t.type for t in engine.project.macros[0].triggers] == ["startup", UNKNOWN]
    assert engine.project.ui.pages[0].elements[0].display_decimals == 20
    said = [(n["kind"], n["location"]) for n in project_update.read(tmp_path)["notes"]]
    assert (FOUND, "Macros > Warm Up > t9") in said
    assert (CHANGED, "Main > volume") in said


class _State:
    def __init__(self):
        self.values = {}

    def set(self, key, value, source=None):
        self.values[key] = value


# --- what each word reads as at run time ---------------------------------------


@pytest.mark.asyncio
async def test_a_trigger_overlap_this_release_does_not_know_skips_while_its_macro_runs():
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.core.trigger_engine import TriggerEngine

    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    macros = AsyncMock()
    macros.is_macro_running = lambda macro_id: True
    engine = TriggerEngine(state, events, macros)
    engine.load_triggers([{"id": "m", "name": "M", "triggers": [
        {"id": "t", "type": "event", "event_pattern": "custom.go", "overlap": UNKNOWN},
    ]}])
    skipped = []
    events.on("trigger.skipped", lambda evt, payload: skipped.append(payload))
    fired = []
    engine._fire_macro = AsyncMock(side_effect=lambda ts, ctx: fired.append(ts))

    await engine._execute_trigger(engine._triggers["t"], {})

    assert fired == []
    assert skipped == [{"trigger_id": "t", "reason": "macro_running"}]


@pytest.mark.asyncio
async def test_a_trigger_of_a_type_this_release_does_not_know_never_fires():
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.core.trigger_engine import TriggerEngine

    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    engine = TriggerEngine(state, events, AsyncMock())
    engine.load_triggers([{"id": "m", "name": "M", "triggers": [
        {"id": "t", "type": UNKNOWN, "event_pattern": "custom.go", "state_key": "var.x"},
    ]}])
    engine._initiate_fire = AsyncMock()
    await engine.start(fire_startup=True)
    await events.emit("custom.go", {})
    state.set("var.x", 1)
    await engine.stop()
    engine._initiate_fire.assert_not_called()


def test_the_macro_lint_reads_the_trigger_words_from_the_schema():
    from openavc.core.macro_validation import VALID_OVERLAP_MODES, VALID_TRIGGER_TYPES, macro_issues

    assert VALID_TRIGGER_TYPES == frozenset(_declared_choices()[(TriggerConfig, "type")].words)
    assert VALID_OVERLAP_MODES == frozenset(_declared_choices()[(TriggerConfig, "overlap")].words)
    issues = macro_issues([], [{"id": "t", "type": UNKNOWN}])
    assert [i["message"] for i in issues] == [
        f"trigger type '{UNKNOWN}' is not one this version of OpenAVC knows, so the trigger never "
        f"fires. Use: schedule, state_change, event, startup"
    ]


# --- a value past a bound, whatever the project's version ----------------------


def _past_bounds(version: str = CURRENT_VERSION) -> dict:
    """A project stamped ``version`` that an earlier release saved with a value
    past each bound it did not have."""
    return {
        "openavc_version": version,
        "project": {"id": "p", "name": "P"},
        "monitors": [{"key": "var.level", "display_decimals": 40}],
        "macros": [{
            "id": "warmup", "name": "Warm Up", "steps": [], "cooldown_seconds": -5,
            "triggers": [{"id": "t1", "type": "startup", "debounce_seconds": -1}],
        }],
        "ui": {
            "settings": {"page_transition_duration": 99999, "lock_code": "1234567"},
            "pages": [{
                "id": "main", "name": "Main",
                "elements": [{"id": "volume", "type": "slider", "display_decimals": 50}],
                "layouts": [{"id": "landscape", "placements": {"volume": {"x": 10, "y": 10, "w": 20, "h": 30}}}],
            }, {
                "id": "help", "name": "Help", "page_type": "overlay", "elements": [],
                "overlay": {"width": 2, "height": 50},
            }],
        },
    }


def test_a_current_project_an_earlier_release_saved_past_a_bound_loads_brought_inside(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_past_bounds()), encoding="utf-8")

    project = load_project(path, keep_notes=True)

    assert project.ui.pages[0].elements[0].display_decimals == 20
    assert project.monitors[0].display_decimals == 20
    assert project.ui.settings.page_transition_duration == 1000
    assert project.ui.settings.lock_code == "123456"
    assert project.macros[0].cooldown_seconds == 0
    assert project.macros[0].triggers[0].debounce_seconds == 0
    assert project.ui.pages[1].overlay.width == 5
    # Written back, so the next load has nothing to repair.
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["ui"]["pages"][0]["elements"][0]["display_decimals"] == 20
    notes = project_update.read(tmp_path)["notes"]
    assert ("Main > volume", "Shown decimals changed from 50 to 20. A readout shows 0 to 20.") in [
        (n["location"], n["message"]) for n in notes
    ]
    assert all(n["kind"] == CHANGED for n in notes)


def test_every_load_repairs_with_the_bounds_the_schema_declares_today():
    from openavc.core.project_loader import (
        MonitorConfig,
        OverlayConfig,
        UIElement,
        UISettings,
    )
    from openavc.core.saved_values import todays_bounds

    def ends(model, field):
        rules = model.model_fields[field].metadata
        return (next(r.ge for r in rules if hasattr(r, "ge")), next(r.le for r in rules if hasattr(r, "le")))

    bounds = todays_bounds()
    assert bounds.decimals == ends(UIElement, "display_decimals") == ends(MonitorConfig, "display_decimals")
    assert bounds.overlay_box == ends(OverlayConfig, "width") == ends(OverlayConfig, "height")
    assert bounds.panel_settings == {
        f: ends(UISettings, f) for f in ("page_transition_duration", "element_stagger_ms", "idle_timeout_seconds")
    }
    assert bounds.pin_length == next(
        r.max_length for r in UISettings.model_fields["lock_code"].metadata if hasattr(r, "max_length")
    )
    assert bounds.seconds_min == next(
        r.ge for r in TriggerConfig.model_fields["delay_seconds"].metadata if hasattr(r, "ge")
    )


def test_a_project_inside_every_bound_is_not_rewritten():
    data = _past_bounds()
    data["monitors"][0]["display_decimals"] = 3
    data["macros"][0]["cooldown_seconds"] = 1
    data["macros"][0]["triggers"][0]["debounce_seconds"] = 0
    data["ui"]["settings"] = {"page_transition_duration": 300, "lock_code": "1234"}
    data["ui"]["pages"][0]["elements"][0]["display_decimals"] = 2
    data["ui"]["pages"][1]["overlay"]["width"] = 50
    before = copy.deepcopy(data)
    _, migrated = migrate_project(data, MigrationNotes())
    assert migrated is False
    assert data == before


# --- the doors that edit the running project -----------------------------------


async def _edit_rig(tmp_path, monkeypatch, project: dict):
    from openavc.api.routes import project as routes

    engine = Engine(str(tmp_path / "project.avc"))
    engine.project = ProjectConfig(**project)
    save_project(engine.project_path, engine.project)
    monkeypatch.setattr(engine, "_reconcile", AsyncMock())
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    from openavc.api.errors import add_project_change_handler

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    add_project_change_handler(app)
    return engine, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _plain() -> dict:
    data = _project(word="queue")
    data["macros"][0]["steps"][0]["on_timeout"] = "continue"
    data["macros"][0]["triggers"][0]["type"] = "startup"
    data["ui"]["pages"][0]["render_mode"] = "elements"
    data["ui"]["pages"][0]["background"]["image_size"] = "contain"
    data["ui"]["pages"][0]["layouts"][1]["orientation"] = "portrait"
    for dep in data["driver_dependencies"] + data["plugin_dependencies"]:
        dep["source"] = "unknown"
    return data


@pytest.mark.asyncio
async def test_a_save_refuses_a_word_this_release_does_not_know_and_the_running_project_does_not_hold(
    tmp_path, monkeypatch,
):
    engine, client = await _edit_rig(tmp_path, monkeypatch, _plain())
    body = engine.project.model_dump(mode="json")
    body["macros"][0]["triggers"][0]["type"] = "webhook"
    async with client:
        resp = await client.put("/api/project", json=body)
    assert resp.status_code == 422
    assert "Trigger type 'webhook' is not one this version of OpenAVC knows" in resp.json()["detail"]
    assert engine.project.macros[0].triggers[0].type == "startup"


def test_the_server_answers_an_unknown_word_refusal_with_a_422():
    from openavc.api.errors import project_change_handler
    from openavc.core.saved_values import UnknownWordError
    from openavc.main import app

    assert app.exception_handlers[UnknownWordError] is project_change_handler


@pytest.mark.asyncio
async def test_a_save_keeps_a_word_the_running_project_already_holds(tmp_path, monkeypatch):
    engine, client = await _edit_rig(tmp_path, monkeypatch, _project())
    body = engine.project.model_dump(mode="json")
    body["project"]["name"] = "Renamed"
    body["macros"][0]["triggers"].append({"id": "t3", "type": UNKNOWN})
    async with client:
        resp = await client.put("/api/project", json=body)
    assert resp.status_code == 200, resp.text
    assert engine.project.project.name == "Renamed"
    assert [t.type for t in engine.project.macros[0].triggers] == [UNKNOWN, "startup", UNKNOWN]


@pytest.mark.asyncio
async def test_a_save_still_refuses_a_value_past_a_bound(tmp_path, monkeypatch):
    engine, client = await _edit_rig(tmp_path, monkeypatch, _plain())
    body = engine.project.model_dump(mode="json")
    body["ui"]["settings"]["page_transition_duration"] = 99999
    async with client:
        resp = await client.put("/api/project", json=body)
    assert resp.status_code == 422
    assert "page_transition_duration" in resp.text


@pytest.mark.asyncio
async def test_an_import_takes_a_word_this_release_does_not_know_and_says_so(tmp_path, monkeypatch):
    engine, client = await _edit_rig(tmp_path, monkeypatch, _plain())
    async with client:
        resp = await client.put("/api/project?mode=import", json=_project())
        assert resp.status_code == 200, resp.text
        notes = (await client.get("/api/project/update-notice")).json()["notes"]
    assert engine.project.macros[0].triggers[0].type == UNKNOWN
    assert any("Trigger type" in n["message"] for n in notes)


@pytest.mark.asyncio
async def test_an_edit_that_adds_a_word_this_release_does_not_know_is_refused(tmp_path, monkeypatch):
    from openavc.cloud.tools import apply_tool_edit

    engine, _ = await _edit_rig(tmp_path, monkeypatch, _plain())

    def mutate(project):
        project.ui.pages[0].background.image_size = "tile"

    result = await apply_tool_edit(engine, mutate)

    assert result == {"error": (
        "Background picture size 'tile' is not one this version of OpenAVC knows. "
        "Use: cover, contain, stretch"
    )}
    assert engine.project.ui.pages[0].background.image_size == "contain"

