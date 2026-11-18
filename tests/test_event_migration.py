"""The 0.18.0 step: a project's own `system.` events become `custom.` ones.

The Macro editor used to suggest `system.room_occupied`, `system.room_vacant`
and `system.panic` for a project's own events. From 0.18.0 a project cannot
send a `system.` name, so a project that took a suggestion would have its step
refused and its listener left hearing nothing. Opening it renames what it
sends, and the Event triggers that could only hear it, so the wiring keeps
working, and the Programmer's notice says what moved. Every name, page and
control here is invented.
"""

from __future__ import annotations

import copy
import json

import pytest

from openavc.core.event_bus import EventBus, check_project_emit
from openavc.core.project_loader import load_project
from openavc.core.project_migration import CURRENT_VERSION, migrate_project
from openavc.core.project_update import CHANGED, FOUND, MigrationNotes


def _project(**parts) -> dict:
    data = {
        "openavc_version": "0.17.0",
        "project": {"id": "acme", "name": "Acme"},
        "devices": [], "variables": [], "macros": [],
        "ui": {"settings": {}, "pages": [{"id": "main", "name": "Main", "elements": []}]},
    }
    data.update(parts)
    return data


def _emit(event: str) -> dict:
    return {"action": "event.emit", "event": event}


def _migrate(data: dict) -> tuple[dict, MigrationNotes]:
    notes = MigrationNotes()
    out, migrated = migrate_project(data, notes)
    assert migrated
    assert out["openavc_version"] == CURRENT_VERSION == "0.18.0"
    return out, notes


def test_a_macro_s_steps_are_renamed_branches_included():
    data = _project(macros=[{
        "id": "occupancy", "name": "Occupancy",
        "steps": [
            _emit("system.room_occupied"),
            {"action": "conditional", "condition": {"key": "var.alarm"},
             "then_steps": [_emit("system.panic")],
             "else_steps": [_emit("custom.all_clear")]},
        ],
    }])

    out, notes = _migrate(data)

    steps = out["macros"][0]["steps"]
    assert steps[0]["event"] == "custom.room_occupied"
    assert steps[1]["then_steps"][0]["event"] == "custom.panic"
    assert steps[1]["else_steps"][0]["event"] == "custom.all_clear"
    assert [(n.kind, n.location, n.message) for n in notes.notes] == [
        (CHANGED, "Macros > Occupancy", (
            "Step 1 (Emit Event) now sends 'custom.room_occupied' in place of "
            "'system.room_occupied': names starting with 'system.' are OpenAVC's own. "
            "A Node-RED flow or another system listening for the old name needs the new one."
        )),
        (CHANGED, "Macros > Occupancy", (
            "Step 2, Then step 1 (Emit Event) now sends 'custom.panic' in place of "
            "'system.panic': names starting with 'system.' are OpenAVC's own. "
            "A Node-RED flow or another system listening for the old name needs the new one."
        )),
    ]


@pytest.mark.parametrize("pattern,renamed", [
    ("system.panic", "custom.panic"),
    ("system.room_*", "custom.room_*"),
    ("System.Panic", "custom.Panic"),
    ("system.*", None),
    ("system.started", None),
    ("system.project.*", None),
    ("custom.panic", None),
])
def test_an_event_trigger_only_a_project_could_hear_is_renamed(pattern, renamed):
    """A pattern that also hears the platform keeps hearing it."""
    data = _project(macros=[{
        "id": "alarm", "name": "Alarm", "steps": [],
        "triggers": [{"id": "trg_1", "type": "event", "event_pattern": pattern}],
    }])

    out, notes = _migrate(data)

    assert out["macros"][0]["triggers"][0]["event_pattern"] == (renamed or pattern)
    if renamed:
        assert [(n.kind, n.location, n.message) for n in notes.notes] == [(
            CHANGED, "Macros > Alarm > trg_1",
            f"Listens for '{renamed}' in place of '{pattern}': a project's own events "
            f"are named under 'custom.'.",
        )]
    else:
        assert not notes


def test_a_trigger_that_also_hears_the_platform_is_left_and_says_what_it_no_longer_hears():
    data = _project(macros=[
        {"id": "panic", "name": "Panic", "steps": [_emit("system.panic")]},
        {"id": "log", "name": "Log everything", "steps": [],
         "triggers": [{"id": "trg_all", "type": "event", "event_pattern": "system.*"}]},
    ])

    out, notes = _migrate(data)

    assert out["macros"][1]["triggers"][0]["event_pattern"] == "system.*"
    assert [(n.kind, n.location, n.message) for n in notes.notes][1:] == [(
        FOUND, "Macros > Log everything > trg_all",
        "Listens for 'system.*', which no longer hears this project's 'system.panic': it is "
        "sent as 'custom.panic' now. Add an Event trigger for 'custom.panic' if this macro "
        "should still run on it.",
    )]


def test_a_platform_event_a_project_sends_is_left_and_noted():
    """It has no name of its own to move to; it is refused now, and the note
    says so where the integrator reads it."""
    data = _project(macros=[{
        "id": "fake", "name": "Fake start", "steps": [_emit("system.started")],
    }])

    out, notes = _migrate(data)

    assert out["macros"][0]["steps"][0]["event"] == "system.started"
    assert check_project_emit("system.started")
    assert [(n.kind, n.message) for n in notes.notes] == [(FOUND, (
        "Step 1 (Emit Event) sends 'system.started', which only OpenAVC sends, so it is "
        "no longer sent. Remove it, or name this project's own event under 'custom.'."
    ))]


def test_every_place_a_control_keeps_an_action_is_renamed():
    toggle = {
        "id": "btn_alarm", "type": "button",
        "bindings": {"do": {"press": [{
            **_emit("system.panic"), "mode": "toggle",
            "off_action": _emit("system.all_clear"),
            "hold_action": _emit("system.hold"),
        }]}},
    }
    single = {
        "id": "btn_occupied", "type": "button",
        "bindings": {"do": {"press": _emit("system.room_occupied")}},
    }
    mapped = {
        "id": "sel_mode", "type": "select",
        "bindings": {"do": {"change": [{
            "action": "value_map", "map": {"a": _emit("system.mode_a"), "b": _emit("custom.mode_b")},
        }]}},
    }
    matrix = {
        "id": "mx", "type": "matrix",
        "matrix_config": {"destinations": [
            {"id": "stream", "label": "Stream", "route": [_emit("system.stream_start")]},
            {"id": "out1", "label": "Out 1"},
        ]},
    }
    master = {
        "id": "mst_vacant", "type": "button",
        "bindings": {"do": {"press": [_emit("system.room_vacant")]}},
    }
    data = _project()
    data["ui"]["pages"][0]["elements"] = [toggle, single, mapped, matrix]
    data["ui"]["master_elements"] = [master]

    out, notes = _migrate(data)

    elements = out["ui"]["pages"][0]["elements"]
    press = elements[0]["bindings"]["do"]["press"][0]
    assert (press["event"], press["off_action"]["event"], press["hold_action"]["event"]) == (
        "custom.panic", "custom.all_clear", "custom.hold",
    )
    assert elements[1]["bindings"]["do"]["press"]["event"] == "custom.room_occupied"
    value_map = elements[2]["bindings"]["do"]["change"][0]["map"]
    assert (value_map["a"]["event"], value_map["b"]["event"]) == ("custom.mode_a", "custom.mode_b")
    assert elements[3]["matrix_config"]["destinations"][0]["route"][0]["event"] == "custom.stream_start"
    assert out["ui"]["master_elements"][0]["bindings"]["do"]["press"][0]["event"] == "custom.room_vacant"
    assert {(n.location, n.page_id, n.element_id) for n in notes.notes} == {
        ("Main > btn_alarm", "main", "btn_alarm"),
        ("Main > btn_occupied", "main", "btn_occupied"),
        ("Main > sel_mode", "main", "sel_mode"),
        ("Main > mx", "main", "mx"),
        ("Master Elements > mst_vacant", "", "mst_vacant"),
    }
    assert len(notes.notes) == 7
    assert all(n.kind == CHANGED for n in notes.notes)


def test_a_project_with_nothing_to_rename_says_nothing():
    data = _project(macros=[{
        "id": "ready", "name": "Ready", "steps": [_emit("custom.ready")],
        "triggers": [{"id": "t", "type": "event", "event_pattern": "system.started"}],
    }])
    before = copy.deepcopy(data)

    out, notes = _migrate(data)

    before["openavc_version"] = "0.18.0"
    assert out == before
    assert not notes


@pytest.mark.asyncio
async def test_an_opened_project_s_button_and_trigger_still_meet(tmp_path):
    """The wiring the Macro editor's suggestion made: a button sends Room
    occupied, a macro listens for it. After the load, the button's event is
    sent and the macro's trigger hears it."""
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project(
        macros=[{
            "id": "occupied", "name": "Occupied", "steps": [],
            "triggers": [{"id": "t", "type": "event", "event_pattern": "system.room_occupied"}],
        }],
        ui={"settings": {}, "pages": [{"id": "main", "name": "Main", "elements": [{
            "id": "btn", "type": "button",
            "bindings": {"do": {"press": [_emit("system.room_occupied")]}},
        }]}]},
    )), encoding="utf-8")

    project = load_project(path)

    sent = project.ui.pages[0].elements[0].bindings["do"]["press"][0]["event"]
    pattern = project.macros[0].triggers[0].event_pattern
    assert check_project_emit(sent) is None
    bus = EventBus()
    heard: list[str] = []
    bus.on(pattern, lambda name, _p: heard.append(name))
    await bus.emit(sent)
    assert heard == ["custom.room_occupied"]
