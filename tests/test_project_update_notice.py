"""What loading a project an earlier release saved changed in it, said once.

The format chain repairs a saved value a new rule refuses; each repair, and
each value it found and left for Validate, is a note. The load that ran the
chain keeps the notes beside the project until somebody dismisses them, and
publishes system.project_update_at so the Programmer shows them.
"""

import json
import math

from openavc.core import project_update
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.project_migration import CURRENT_VERSION, migrate_project
from openavc.core.project_update import CHANGED, FOUND, MigrationNotes


def _v016(**ui_settings) -> dict:
    """A 0.16.0 project holding one of each value the 0.17.0 step repairs."""
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "p", "name": "P"},
        "monitors": [{"key": "var.level", "display_decimals": 40}],
        "macros": [{
            "id": "warmup", "name": "Warm Up", "steps": [], "cooldown_seconds": -5,
            "triggers": [{"id": "t1", "type": "startup", "enabled": True, "delay_seconds": math.inf}],
        }],
        "ui": {
            "settings": ui_settings,
            "pages": [{
                "id": "main", "name": "Main",
                "elements": [{"id": "volume", "type": "slider", "display_decimals": 50}],
                "layouts": [{"id": "landscape", "placements": {
                    "volume": {"x": math.nan, "y": 10, "w": -20, "h": 30},
                }}],
            }, {
                "id": "help", "name": "Help", "type": "overlay", "elements": [],
                "overlay": {"width": 2, "height": 50},
            }],
            "master_elements": [{"id": "clock", "type": "label", "display_decimals": 99}],
        },
    }


def _notes(data: dict) -> MigrationNotes:
    notes = MigrationNotes()
    migrate_project(data, notes)
    return notes


def _said(notes: MigrationNotes) -> list[tuple[str, str, str]]:
    return [(n.kind, n.location, n.message) for n in notes.notes]


# --- What the 0.17.0 step says ---


def test_every_value_the_step_rewrites_is_said_where_it_is():
    notes = _notes(_v016(page_transition_duration=99999, idle_timeout_seconds=-5))

    assert _said(notes) == [
        (CHANGED, "Main > volume", "Shown decimals changed from 50 to 20. A readout shows 0 to 20."),
        (CHANGED, "Master Elements > clock", "Shown decimals changed from 99 to 20. A readout shows 0 to 20."),
        (CHANGED, "Monitored Readings > var.level", "Shown decimals changed from 40 to 20. A readout shows 0 to 20."),
        (CHANGED, "Panel Settings", "Transition Duration changed from 99999 to 1000 ms, the range Panel Settings offers."),
        (CHANGED, "Panel Settings", "Idle Timeout changed from -5 to 0 seconds, the range Panel Settings offers."),
        (CHANGED, "Macros > Warm Up", "Cooldown changed from -5 to 0 seconds, which is how it ran."),
        (CHANGED, "Macros > Warm Up > t1",
         "Trigger turned off: its Delay never ended, so it never fired. Set a Delay and turn it on to use it."),
        (CHANGED, "Main > volume", 'X in layout "landscape" was not a number, so it is now 0%.'),
        (FOUND, "Main > volume", 'Width in layout "landscape" is -20%, so the control does not draw.'),
        (CHANGED, "Help > page", "Width changed from 2% to 5%, the range the UI Builder offers."),
    ]


def test_a_note_names_the_page_and_control_it_is_on():
    notes = _notes(_v016())
    slider = notes.notes[0]
    assert (slider.page_id, slider.element_id) == ("main", "volume")
    master = notes.notes[1]
    assert (master.page_id, master.element_id) == ("", "clock")
    overlay = [n for n in notes.notes if n.location == "Help > page"][0]
    assert (overlay.page_id, overlay.element_id) == ("help", "")


def test_a_lock_pin_note_never_holds_a_digit_of_it():
    for pin, says in (
        ("12345678", "changed to its first six digits"),
        ("98 76", "changed to its first six digits"),
        ("letmein", "cleared"),
    ):
        notes = _notes(_v016(lock_code=pin))
        pin_notes = [n for n in notes.notes if "Lock Code" in n.message]
        assert len(pin_notes) == 1, pin
        assert says in pin_notes[0].message
        assert not any(c.isdigit() for c in pin_notes[0].message), pin_notes[0].message


def test_a_project_the_step_has_nothing_to_repair_in_says_nothing():
    clean = {
        "openavc_version": "0.16.0",
        "project": {"id": "p", "name": "P"},
        "ui": {"settings": {"lock_code": "1234", "page_transition_duration": 300}, "pages": [{
            "id": "main", "name": "Main", "elements": [{"id": "l", "type": "label", "display_decimals": 2}],
        }]},
    }
    assert _notes(clean).notes == []


def test_a_step_that_only_reshapes_the_file_says_nothing():
    """The chain from the first format reshapes everything and repairs nothing."""
    assert _notes({"project": {"id": "p", "name": "P"}}).notes == []


def test_a_current_project_is_not_migrated_and_says_nothing():
    notes = MigrationNotes()
    _, migrated = migrate_project({"openavc_version": CURRENT_VERSION, "project": {"id": "p", "name": "P"}}, notes)
    assert migrated is False and notes.notes == []


# --- The record a load keeps ---


def _write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The file writer put a NaN or Infinity down bare, as json.dumps does.
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_load_keeps_its_notes_beside_the_project_and_a_second_load_keeps_them(tmp_path):
    path = tmp_path / "project.avc"
    _write(path, _v016(page_transition_duration=99999))

    load_project(path, keep_notes=True)
    first = project_update.read(tmp_path)
    assert first is not None
    assert any("Transition Duration" in n["message"] for n in first["notes"])
    # The file is now current, so the next load has nothing to migrate: the
    # record is the only place the notes are.
    assert json.loads(path.read_text())["openavc_version"] == CURRENT_VERSION

    load_project(path, keep_notes=True)
    assert project_update.read(tmp_path) == first


def test_a_load_that_does_not_keep_notes_leaves_no_record(tmp_path):
    path = tmp_path / "project.avc"
    _write(path, _v016(page_transition_duration=99999))
    load_project(path)
    assert project_update.read(tmp_path) is None


def test_the_first_boot_seed_and_the_starters_leave_no_notice(tmp_path):
    """A fresh install never says "This project was updated"."""
    from openavc.core.project_recovery import load_or_recover
    from openavc.system_config import get_seed_project_path

    path = tmp_path / "projects" / "default" / "project.avc"
    load_or_recover(path)
    assert path.exists() or get_seed_project_path() is None
    assert project_update.read(path.parent) is None

    from pathlib import Path

    import openavc.core.project_library as library
    templates = Path(library.__file__).parent.parent / "templates"
    for template in sorted(templates.glob("*.avc")):
        assert _notes(json.loads(template.read_text(encoding="utf-8"))).notes == [], template.name


def test_notes_from_a_later_load_join_the_ones_nobody_dismissed(tmp_path):
    first, second = MigrationNotes(), MigrationNotes()
    first.changed("One.", "Panel Settings")
    second.changed("Two.", "Panel Settings")
    project_update.record(tmp_path, first)
    project_update.record(tmp_path, second)
    assert [n["message"] for n in project_update.read(tmp_path)["notes"]] == ["One.", "Two."]


def test_a_project_that_replaces_the_running_one_replaces_its_notice(tmp_path):
    first, empty = MigrationNotes(), MigrationNotes()
    first.changed("One.", "Panel Settings")
    project_update.record(tmp_path, first)
    project_update.replace(tmp_path, empty)
    assert project_update.read(tmp_path) is None


def test_a_damaged_record_is_no_record(tmp_path):
    (tmp_path / project_update.MARKER_NAME).write_text("{ not json", encoding="utf-8")
    assert project_update.read(tmp_path) is None
    (tmp_path / project_update.MARKER_NAME).write_text(
        json.dumps({"notes": [{"kind": "something_else", "message": "x"}]}), encoding="utf-8"
    )
    assert project_update.read(tmp_path) is None


# --- The doors ---


def test_the_boot_publishes_the_record_and_the_dismiss_route_clears_it(tmp_path):
    from fastapi.testclient import TestClient

    from openavc.api import rest
    from openavc.main import app

    path = tmp_path / "projects" / "default" / "project.avc"
    _write(path, _v016(page_transition_duration=99999))
    eng = Engine(str(path))
    eng.project = eng._load_project_safe()
    project_update.publish(eng.state, path.parent)

    rest.set_engine(eng)
    try:
        client = TestClient(app)
        assert client.get("/api/state").json()["state"][project_update.KEY_AT] != ""
        notice = client.get("/api/project/update-notice").json()
        # What the boot's migration said, each note whole.
        expected = [m for _, _, m in _said(_notes(_v016(page_transition_duration=99999)))]
        assert [n["message"] for n in notice["notes"]] == expected
        assert notice["notes"][0]["page_id"] == "main" and notice["notes"][0]["element_id"] == "volume"

        resp = client.post("/api/project/update-notice/dismiss")
        assert resp.json() == {"status": "dismissed", "cleared": True}
        assert client.get("/api/state").json()["state"][project_update.KEY_AT] == ""
        assert client.get("/api/project/update-notice").json() == {"at": "", "notes": []}
        assert client.post("/api/project/update-notice/dismiss").json()["cleared"] is False
    finally:
        rest.set_engine(None)


def test_a_library_import_keeps_its_notes_until_the_copy_is_opened(tmp_path, monkeypatch):
    import openavc.core.project_library as library

    lib = tmp_path / "library"
    monkeypatch.setattr(library, "_lib_dir", lambda: lib)
    result = library.import_project(
        json.dumps(_v016(page_transition_duration=99999)).encode(), "old.avc", "old"
    )
    assert project_update.read(lib / result["id"]) is not None

    running = tmp_path / "running" / "project.avc"
    running.parent.mkdir()
    notes = MigrationNotes()
    library.open_from_library(result["id"], running, running.parent / "scripts", "old", "Old", update_notes=notes)
    assert any("Transition Duration" in n.message for n in notes.notes)
    # Handed over: the copy no longer holds them.
    assert project_update.read(lib / result["id"]) is None


async def test_an_import_brings_its_notice_and_the_next_import_replaces_it(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI

    from openavc.api.routes import project as routes
    from openavc.core.project_loader import ProjectConfig, save_project

    engine = Engine(str(tmp_path / "project.avc"))
    engine.project = ProjectConfig(project={"id": "before", "name": "Before"})
    save_project(engine.project_path, engine.project)
    monkeypatch.setattr(engine, "_reconcile", AsyncMock())
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    old = json.loads(json.dumps(_v016(page_transition_duration=99999)).replace("NaN", "0").replace("Infinity", "5"))
    current = {"openavc_version": CURRENT_VERSION, "project": {"id": "next", "name": "Next"}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/api/project?mode=import", json=old)).status_code == 200
        notes = (await client.get("/api/project/update-notice")).json()["notes"]
        assert any(n["message"].startswith("Transition Duration changed from 99999 to 1000 ms") for n in notes)
        assert engine.state.get(project_update.KEY_AT) != ""

        assert (await client.put("/api/project?mode=import", json=current)).status_code == 200
        assert (await client.get("/api/project/update-notice")).json()["notes"] == []
        assert engine.state.get(project_update.KEY_AT) == ""
    await engine.stop()
