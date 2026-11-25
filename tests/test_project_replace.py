"""One replace for every door that puts a whole other project in place of the
running one: a restore, opening a copy from the library, a blank project.

Each checks what is coming in before anything is written, keeps a backup of
the project it replaces, and swaps every file the project is made of in one
go, so none of the previous project's saved values (``state.json``) or its
startup notice (``recovery.json``) carries into the new one. A replace whose
reload fails puts the project before it back, files and all, and values go
on being saved whatever happens. A power cut part way through the swap
leaves a marker that the next start reads first: it puts back the backup the
replace took.

The engine is real and so is its variables stage; the rest of the reconcile
(devices, plugins, scripts) is left out, so what is proved is the files, the
saved values and the persister.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openavc.core import backup_manager, project_recovery, project_swap
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.project_reconcile import RECONCILE_STAGES

_VARIABLES = next(stage for stage in RECONCILE_STAGES if stage.forward == "_follow_variables")

LEVEL = {"id": "level", "type": "number", "default": 0, "persist": True}
MODE = {"id": "mode", "type": "string", "default": "off", "persist": True}
SCENE = {"id": "scene", "type": "string", "default": "off"}


def _write_project(project_file: Path, name: str, variables: object) -> None:
    project_file.write_text(json.dumps({
        "openavc_version": CURRENT_VERSION,
        "project": {"id": name.lower(), "name": name},
        "variables": variables,
    }), encoding="utf-8")


def _write_files(folder: Path, script: str, logo: bytes) -> None:
    (folder / "scripts").mkdir(exist_ok=True)
    (folder / "scripts" / "main.py").write_text(script, encoding="utf-8")
    (folder / "assets").mkdir(exist_ok=True)
    (folder / "assets" / "logo.png").write_bytes(logo)


def _saved(folder: Path) -> dict | None:
    state_file = folder / "state.json"
    return json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else None


def _left_a_notice(folder: Path) -> None:
    project_recovery.record(
        folder, outcome=project_recovery.RESTORED, reason=project_recovery.UNREADABLE,
        backup="backups/older.zip", cutoff="2026-01-01T00:00:00+00:00",
    )


def _engine(folder: Path) -> Engine:
    """A real engine on ``folder``'s project, its variables seeded from
    state.json and its persister watching them, as a start leaves them. Its
    reconcile is the variables stage alone."""
    engine = Engine(str(folder / "project.avc"))
    engine.project = load_project(engine.project_path)
    engine._start_variables()
    project_recovery.publish(engine.state, folder)

    async def variables_only(run) -> None:
        run.entered.append(_VARIABLES)
        await engine._follow_variables(run)

    engine._reconcile = variables_only
    engine.broadcast_ws = AsyncMock()
    return engine


@pytest.fixture
def hall(tmp_path: Path) -> Engine:
    """The Hall running: two saved values, a script, a logo, and the notice a
    start left about a backup it had to load."""
    _write_project(tmp_path / "project.avc", "Hall", [LEVEL, MODE, SCENE])
    _write_files(tmp_path, "# the hall's script\n", b"PNG-hall")
    (tmp_path / "state.json").write_text(json.dumps({"var.level": 7, "var.mode": "on"}), encoding="utf-8")
    _left_a_notice(tmp_path)
    engine = _engine(tmp_path)
    engine.state.set("var.scene", "movie", source="ui")
    yield engine
    engine.persister.stop()


def _client(engine: Engine, monkeypatch) -> httpx.AsyncClient:
    from openavc.api import _engine as engine_slot
    from openavc.api.errors import (
        StructuredApiError,
        add_project_change_handler,
        structured_api_error_handler,
    )
    from openavc.api.routes import project as project_routes

    monkeypatch.setattr(engine_slot, "_engine", engine)
    monkeypatch.setattr(project_routes, "_get_engine", lambda: engine)
    app = FastAPI()
    app.include_router(project_routes.router, prefix="/api")
    add_project_change_handler(app)
    app.add_exception_handler(StructuredApiError, structured_api_error_handler)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _saves_values(engine: Engine) -> bool:
    engine.state.set("var.level", 42, source="ui")
    engine.persister.flush()
    return (_saved(engine.project_path.parent) or {}).get("var.level") == 42


def _hall_as_it_was(engine: Engine) -> None:
    folder = engine.project_path.parent
    assert load_project(engine.project_path).project.name == "Hall"
    assert engine.project.project.name == "Hall"
    assert (folder / "scripts" / "main.py").read_text(encoding="utf-8") == "# the hall's script\n"
    assert (folder / "assets" / "logo.png").read_bytes() == b"PNG-hall"
    assert _saved(folder) == {"var.level": 7, "var.mode": "on"}
    assert project_recovery.read(folder) is not None
    assert not (folder / project_swap.WORK_DIR).exists()
    assert project_swap.read_marker(folder) is None


def _library_lobby(tmp_path: Path, monkeypatch) -> str:
    """A Lobby saved in the library: the same saved variable id as the
    Hall's, its own script and logo."""
    import openavc.core.project_library as library

    monkeypatch.setattr(library, "_lib_dir", lambda: tmp_path / "library")
    saved = library.import_project(json.dumps({
        "openavc_version": CURRENT_VERSION,
        "project": {"id": "lobby", "name": "Lobby"},
        "variables": [LEVEL, SCENE],
    }).encode(), "lobby.avc", "lobby")
    _write_files(tmp_path / "library" / saved["id"], "# the lobby's script\n", b"PNG-lobby")
    return saved["id"]


async def _open_lobby(engine: Engine, library_id: str, monkeypatch) -> httpx.Response:
    async with _client(engine, monkeypatch) as client:
        return await client.post(
            "/api/project/open-from-library",
            json={"library_id": library_id, "project_name": "Lobby", "project_id": "lobby"},
        )


# --- A restore -------------------------------------------------------------------


async def test_a_restore_keeps_the_values_its_backup_saved_when_it_saves_fewer(tmp_path, monkeypatch):
    """The reload's variables stage rewrote state.json from the running
    values once a saved variable left, before the restored file was read
    back, so the backup's values were lost to the ones it replaced."""
    project_file = tmp_path / "project.avc"
    _write_project(project_file, "Hall", [LEVEL])
    (tmp_path / "state.json").write_text(json.dumps({"var.level": 3}), encoding="utf-8")
    kept = backup_manager.create_backup(project_file, "Manual backup")
    _write_project(project_file, "Hall", [LEVEL, MODE])
    (tmp_path / "state.json").write_text(json.dumps({"var.level": 7, "var.mode": "on"}), encoding="utf-8")
    engine = _engine(tmp_path)
    try:
        async with _client(engine, monkeypatch) as client:
            resp = await client.post(f"/api/backups/{backup_manager.backup_id(tmp_path, kept)}/restore")

        assert resp.status_code == 200, resp.text
        assert engine.state.get("var.level") == 3
        assert _saved(tmp_path) == {"var.level": 3}
        assert _saves_values(engine)
    finally:
        engine.persister.stop()


async def test_a_restore_takes_away_the_notice_about_the_project_it_replaces(hall, monkeypatch):
    folder = hall.project_path.parent
    kept = backup_manager.create_backup(hall.project_path, "Manual backup")

    async with _client(hall, monkeypatch) as client:
        resp = await client.post(f"/api/backups/{backup_manager.backup_id(folder, kept)}/restore")

    assert resp.status_code == 200, resp.text
    assert project_recovery.read(folder) is None
    assert hall.state.get(project_recovery.KEY_STATE) == ""


async def test_a_backup_that_is_not_a_valid_project_is_refused_with_nothing_changed(hall, monkeypatch):
    folder = hall.project_path.parent
    broken = tmp_backup = folder / "backups"
    tmp_backup.mkdir(exist_ok=True)
    broken = tmp_backup / "backup_20260101_000000_manual_backup.zip"
    import zipfile

    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr("project.avc", json.dumps({
            "openavc_version": CURRENT_VERSION,
            "project": {"id": "old", "name": "Old"},
            "variables": "not a list",
        }))
        zf.writestr("scripts/other.py", "# another script\n")

    async with _client(hall, monkeypatch) as client:
        resp = await client.post(f"/api/backups/{backup_manager.backup_id(folder, broken)}/restore")

    assert resp.status_code == 422, resp.text
    assert "variables" in resp.json()["detail"]
    _hall_as_it_was(hall)
    # Refused before the backup a restore takes first: nothing to go back to.
    assert [b.reason for b in backup_manager.list_backups(hall.project_path)] == ["Backup"]
    assert _saves_values(hall)


async def test_a_legacy_backup_replaces_the_project_file_and_keeps_the_rest(hall, monkeypatch):
    """A ``.avc.bak`` holds the project file alone, from before backups held
    the scripts and files beside it."""
    folder = hall.project_path.parent
    _write_project(folder / "project.20250101.avc.bak", "Hall Before", [LEVEL, MODE])

    async with _client(hall, monkeypatch) as client:
        resp = await client.post("/api/backups/project.20250101.avc.bak/restore")

    assert resp.status_code == 200, resp.text
    assert hall.project.project.name == "Hall Before"
    assert (folder / "scripts" / "main.py").read_text(encoding="utf-8") == "# the hall's script\n"
    assert (folder / "assets" / "logo.png").read_bytes() == b"PNG-hall"
    assert hall.state.get("var.level") == 7


# --- Opening another project, a blank one ----------------------------------------


async def test_opening_another_project_brings_none_of_the_previous_values_or_notice(hall, tmp_path, monkeypatch):
    library_id = _library_lobby(tmp_path, monkeypatch)

    resp = await _open_lobby(hall, library_id, monkeypatch)

    assert resp.status_code == 200, resp.text
    folder = hall.project_path.parent
    assert hall.project.project.name == "Lobby"
    assert (folder / "scripts" / "main.py").read_text(encoding="utf-8") == "# the lobby's script\n"
    assert (folder / "assets" / "logo.png").read_bytes() == b"PNG-lobby"
    assert hall.state.get("var.level") == 0
    # Not saved, so not in state.json: the Hall's value went with the Hall.
    assert hall.state.get("var.scene") == "off"
    assert (_saved(folder) or {}).get("var.level") in (None, 0)
    assert project_recovery.read(folder) is None
    assert hall.state.get(project_recovery.KEY_STATE) == ""
    assert not (folder / project_swap.WORK_DIR).exists()
    assert project_swap.read_marker(folder) is None
    assert _saves_values(hall)


async def test_a_blank_project_brings_none_of_the_previous_values_or_notice(hall, monkeypatch):
    async with _client(hall, monkeypatch) as client:
        resp = await client.post("/api/project/create-blank", json={"project_name": "Blank"})

    assert resp.status_code == 200, resp.text
    folder = hall.project_path.parent
    assert hall.project.project.name == "Blank"
    assert list((folder / "scripts").glob("*.py")) == []
    assert not (folder / "assets" / "logo.png").exists()
    assert _saved(folder) in (None, {})
    assert project_recovery.read(folder) is None
    assert [b.reason for b in backup_manager.list_backups(hall.project_path)] == ["Before creating blank project"]


async def test_a_replace_whose_reload_fails_puts_the_previous_project_back(hall, tmp_path, monkeypatch):
    library_id = _library_lobby(tmp_path, monkeypatch)
    reconcile = hall._reconcile

    async def lobby_fails(run) -> None:
        await reconcile(run)
        if hall.project.project.name == "Lobby":
            raise RuntimeError("a part of the Lobby would not start")

    hall._reconcile = lobby_fails

    resp = await _open_lobby(hall, library_id, monkeypatch)

    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == "not_applied"
    _hall_as_it_was(hall)
    assert hall.state.get("var.level") == 7
    assert hall.state.get("var.scene") == "movie"
    assert _saves_values(hall)


async def test_a_value_the_opened_project_sets_as_it_starts_is_kept(hall, tmp_path, monkeypatch):
    """Its variables are seeded from what it brings before its triggers
    run, as at a start, so a value a startup trigger sets stays and is
    saved."""
    library_id = _library_lobby(tmp_path, monkeypatch)
    reconcile = hall._reconcile

    async def a_startup_trigger_sets_the_level(run) -> None:
        await reconcile(run)
        if hall.project.project.name == "Lobby":
            hall.state.set("var.level", 5, source="trigger")

    hall._reconcile = a_startup_trigger_sets_the_level

    resp = await _open_lobby(hall, library_id, monkeypatch)

    assert resp.status_code == 200, resp.text
    assert hall.state.get("var.level") == 5
    assert _saved(hall.project_path.parent) == {"var.level": 5}


async def test_a_swap_that_stops_part_way_leaves_nothing_for_the_next_start_to_undo(hall, tmp_path, monkeypatch):
    """A rename refused part way (a file held open) puts back the ones made
    and removes the marker, so the next start does not put the backup back
    over what was saved since."""
    library_id = _library_lobby(tmp_path, monkeypatch)
    rename = project_swap._rename_on_disk
    calls = {"n": 0}

    def refused_once(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise PermissionError(13, "The file is in use")
        rename(src, dst)

    monkeypatch.setattr(project_swap, "_rename_on_disk", refused_once)

    resp = await _open_lobby(hall, library_id, monkeypatch)

    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == "not_applied"
    _hall_as_it_was(hall)
    assert _saves_values(hall)
    hall.persister.stop()
    assert project_recovery.load_or_recover(hall.project_path).project.name == "Hall"
    assert _saved(hall.project_path.parent)["var.level"] == 42


async def test_the_marker_names_the_backup_and_is_on_disk_before_the_first_rename(hall, tmp_path, monkeypatch):
    library_id = _library_lobby(tmp_path, monkeypatch)
    folder = hall.project_path.parent
    seen: list[dict | None] = []
    rename = project_swap._rename_on_disk

    def watched(src: Path, dst: Path) -> None:
        seen.append(project_swap.read_marker(folder))
        rename(src, dst)

    monkeypatch.setattr(project_swap, "_rename_on_disk", watched)

    resp = await _open_lobby(hall, library_id, monkeypatch)

    assert resp.status_code == 200, resp.text
    taken = [b.filename for b in backup_manager.list_backups(hall.project_path)]
    assert seen and all(marker == {"backup": taken[0]} for marker in seen)
    assert project_swap.read_marker(folder) is None


# --- The next start --------------------------------------------------------------


def _cut_off_mid_swap(folder: Path) -> str:
    """The Hall as a power cut leaves it part way through opening the Lobby:
    the backup taken and the marker naming it on disk, the project file and
    the scripts swapped, the logo and the saved values not yet."""
    taken = backup_manager.create_backup(folder / "project.avc", "Before opening 'Lobby'")
    backup = backup_manager.backup_id(folder, taken)
    project_swap.write_marker(folder, backup)
    aside = folder / project_swap.WORK_DIR / "old"
    aside.mkdir(parents=True)
    (folder / "scripts").rename(aside / "scripts")
    (folder / "state.json").rename(aside / "state.json")
    _write_project(folder / "project.avc", "Lobby", [LEVEL])
    (folder / "scripts").mkdir()
    (folder / "scripts" / "main.py").write_text("# the lobby's script\n", encoding="utf-8")
    return backup


def test_a_start_after_a_replace_cut_off_puts_back_the_backup_it_took(tmp_path):
    _write_project(tmp_path / "project.avc", "Hall", [LEVEL, MODE])
    _write_files(tmp_path, "# the hall's script\n", b"PNG-hall")
    (tmp_path / "state.json").write_text(json.dumps({"var.level": 7, "var.mode": "on"}), encoding="utf-8")
    backup = _cut_off_mid_swap(tmp_path)

    project = project_recovery.load_or_recover(tmp_path / "project.avc")

    assert project.project.name == "Hall"
    assert (tmp_path / "scripts" / "main.py").read_text(encoding="utf-8") == "# the hall's script\n"
    assert (tmp_path / "assets" / "logo.png").read_bytes() == b"PNG-hall"
    assert _saved(tmp_path) == {"var.level": 7, "var.mode": "on"}
    assert project_swap.read_marker(tmp_path) is None
    assert not (tmp_path / project_swap.WORK_DIR).exists()
    notice = project_recovery.read(tmp_path)
    assert notice is not None
    assert (notice["outcome"], notice["reason"], notice["backup"]) == (
        project_recovery.RESTORED, project_recovery.INTERRUPTED, backup,
    )
    assert notice["cutoff"]


def test_a_start_clears_what_a_finished_replace_left_behind(tmp_path):
    _write_project(tmp_path / "project.avc", "Lobby", [LEVEL])
    leftover = tmp_path / project_swap.WORK_DIR / "old" / "scripts"
    leftover.mkdir(parents=True)
    (leftover / "main.py").write_text("# the hall's script\n", encoding="utf-8")

    project = project_recovery.load_or_recover(tmp_path / "project.avc")

    assert project.project.name == "Lobby"
    assert not (tmp_path / project_swap.WORK_DIR).exists()
    assert project_recovery.read(tmp_path) is None
