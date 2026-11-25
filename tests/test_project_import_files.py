"""An import of a project file on its own, and the stamp that says which
project a file is.

A project file imported on its own brings no scripts, images, custom
controls, themes or saved values. When it is the project already running
(the same stamp, or for a file saved before stamps, the same id and the
same ``created`` stamp, one no release shipped), the files beside the
running project stay. When it is another project they are set aside in the
backup the import takes, and the answer says which. A cloud push, which
brings no files either, keeps them and the running project's stamp; so does
an edit. A project is stamped when it is made, and a project file loaded
without one is stamped once.

The engine is real and so is its variables stage; the rest of the reconcile
(devices, plugins, scripts) is left out.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openavc.core import backup_manager, project_identity
from openavc.core.device_logins import SAVED_LOGIN, DeviceLogins
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.project_reconcile import RECONCILE_STAGES

_VARIABLES = next(stage for stage in RECONCILE_STAGES if stage.forward == "_follow_variables")

LEVEL = {"id": "level", "type": "number", "default": 0, "persist": True}
MODE = {"id": "mode", "type": "string", "default": "off", "persist": True}

HALL_STAMP = "4f7c2a10-hall"
HALL_CREATED = "2025-03-01T10:00:00"
STARTER_CREATED = "2026-01-01T00:00:00.000000"

#: Each file beside the Hall's project file, by where it sits.
HALL_FILES = {
    "scripts/main.py": b"# the hall's script\n",
    "assets/logo.png": b"PNG-hall",
    "ui/meter/index.html": b"<p>the hall's meter</p>",
    "themes/hall.json": b'{"id": "hall", "name": "Hall"}',
}
HALL_VALUES = {"var.level": 7, "var.mode": "on"}


def _document(project_id: str, name: str, *, stamp: str | None, created: str, variables=None) -> dict:
    meta = {"id": project_id, "name": name, "created": created}
    if stamp is not None:
        meta["uuid"] = stamp
    return {
        "openavc_version": CURRENT_VERSION,
        "project": meta,
        "variables": [LEVEL, MODE] if variables is None else variables,
    }


def _saved(folder: Path) -> dict | None:
    state_file = folder / "state.json"
    return json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else None


def _files(folder: Path) -> dict[str, bytes]:
    return {name: (folder / name).read_bytes() for name in HALL_FILES if (folder / name).is_file()}


def _on_disk(engine: Engine) -> dict:
    return json.loads(engine.project_path.read_text(encoding="utf-8"))


def _hall(folder: Path, *, created: str = HALL_CREATED, stamp: str | None = HALL_STAMP) -> Engine:
    """The Hall running from ``folder``: two saved values and a file in each
    of its folders. Its reconcile is the variables stage alone."""
    (folder / "project.avc").write_text(json.dumps(
        _document("hall", "Hall", stamp=stamp, created=created),
    ), encoding="utf-8")
    for name, data in HALL_FILES.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    (folder / "state.json").write_text(json.dumps(HALL_VALUES), encoding="utf-8")
    engine = Engine(str(folder / "project.avc"))
    engine.project = load_project(engine.project_path)
    engine._start_variables()

    async def variables_only(run) -> None:
        run.entered.append(_VARIABLES)
        await engine._follow_variables(run)

    engine._reconcile = variables_only
    engine.broadcast_ws = AsyncMock()
    return engine


@pytest.fixture
def data(tmp_path: Path, monkeypatch) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    return folder


@pytest.fixture
def room(tmp_path: Path, data: Path) -> Path:
    folder = tmp_path / "room"
    folder.mkdir()
    return folder


@pytest.fixture
def hall(room: Path):
    engine = _hall(room)
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


async def _import(engine: Engine, document: dict, monkeypatch, **headers: str) -> httpx.Response:
    async with _client(engine, monkeypatch) as client:
        return await client.put("/api/project?mode=import", json=document, headers=headers)


def _backup(engine: Engine, reason: str) -> zipfile.ZipFile:
    [kept] = [b for b in backup_manager.list_backups(engine.project_path) if b.reason == reason]
    return zipfile.ZipFile(engine.project_path.parent / kept.filename)


def _hall_running(engine: Engine) -> None:
    folder = engine.project_path.parent
    assert engine.project.project.name == "Hall"
    assert load_project(engine.project_path).project.name == "Hall"
    assert _files(folder) == HALL_FILES
    assert _saved(folder) == HALL_VALUES


# --- Another project ------------------------------------------------------------


async def test_importing_another_project_sets_the_previous_files_aside_in_its_backup(hall, monkeypatch):
    folder = hall.project_path.parent

    resp = await _import(hall, _document(
        "lobby", "Lobby", stamp="9a1d-lobby", created="2025-06-01T09:00:00", variables=[LEVEL],
    ), monkeypatch)

    assert resp.status_code == 200, resp.text
    answer = resp.json()
    assert answer["previous_files"] == "set_aside"
    assert answer["notice"] == (
        "The previous project's scripts, images, custom controls, themes and saved values "
        "were set aside in the backup taken before the import (Before importing 'Lobby')."
    )
    assert hall.project.project.name == "Lobby"
    assert _files(folder) == {}
    assert list((folder / "scripts").glob("*.py")) == []
    assert (_saved(folder) or {}).get("var.level") in (None, 0)
    assert hall.state.get("var.level") == 0
    with _backup(hall, "Before importing 'Lobby'") as kept:
        names = set(kept.namelist())
        assert set(HALL_FILES) <= names
        assert json.loads(kept.read("state.json")) == HALL_VALUES
    assert hall.project.project.uuid == "9a1d-lobby"
    assert _on_disk(hall)["project"]["uuid"] == "9a1d-lobby"


async def test_the_same_id_with_another_stamp_is_another_project(hall, monkeypatch):
    """Two rooms built from one template share the id."""
    resp = await _import(hall, _document(
        "hall", "Hall", stamp="7e2b-another-hall", created=HALL_CREATED,
    ), monkeypatch)

    assert resp.status_code == 200, resp.text
    assert resp.json()["previous_files"] == "set_aside"
    assert _files(hall.project_path.parent) == {}


async def test_another_project_with_no_stamp_is_given_one_of_its_own(hall, monkeypatch):
    resp = await _import(hall, _document(
        "lobby", "Lobby", stamp=None, created="2025-06-01T09:00:00",
    ), monkeypatch)

    assert resp.status_code == 200, resp.text
    stamp = hall.project.project.uuid
    assert stamp and stamp != HALL_STAMP
    assert _on_disk(hall)["project"]["uuid"] == stamp


# --- The same project -----------------------------------------------------------


async def test_importing_the_same_project_keeps_its_files_and_saved_values(hall, monkeypatch):
    folder = hall.project_path.parent

    resp = await _import(hall, _document(
        "hall", "Hall (edited)", stamp=HALL_STAMP, created=HALL_CREATED,
    ), monkeypatch)

    assert resp.status_code == 200, resp.text
    answer = resp.json()
    assert answer["previous_files"] == "kept"
    assert answer["notice"] == (
        "It is the same project, so its scripts, images, custom controls, themes "
        "and saved values were kept."
    )
    assert hall.project.project.name == "Hall (edited)"
    assert _files(folder) == HALL_FILES
    assert _saved(folder) == HALL_VALUES
    assert hall.state.get("var.level") == 7
    assert [b.reason for b in backup_manager.list_backups(hall.project_path)] == ["Before importing 'Hall (edited)'"]


async def test_the_same_project_starts_its_unsaved_values_at_their_defaults(room, monkeypatch):
    """The project is loaded as at a start: its saved values come back from
    state.json, and a value it does not save starts at its default."""
    scene = {"id": "scene", "type": "string", "default": "off"}
    engine = _hall(room)
    try:
        engine.state.set("var.scene", "movie", source="ui")
        resp = await _import(engine, _document(
            "hall", "Hall", stamp=HALL_STAMP, created=HALL_CREATED, variables=[LEVEL, MODE, scene],
        ), monkeypatch)

        assert resp.status_code == 200, resp.text
        assert engine.state.get("var.level") == 7
        assert engine.state.get("var.scene") == "off"
    finally:
        engine.persister.stop()


@pytest.mark.parametrize("running_created,incoming_id,incoming_created,kept", [
    (HALL_CREATED, "hall", HALL_CREATED, True),
    (HALL_CREATED, "lobby", HALL_CREATED, False),
    (HALL_CREATED, "hall", "2025-06-01T09:00:00", False),
    # Every room built from the starter or a template carries these.
    (STARTER_CREATED, "hall", STARTER_CREATED, False),
    ("2026-04-06T00:00:00", "hall", "2026-04-06T00:00:00", False),
    ("", "hall", "", False),
], ids=["own-created", "another-id", "another-created", "starter", "template", "none"])
async def test_a_file_saved_before_stamps_is_judged_by_its_id_and_created_stamp(
    room, monkeypatch, running_created, incoming_id, incoming_created, kept,
):
    engine = _hall(room, created=running_created)
    try:
        resp = await _import(engine, _document(
            incoming_id, "Hall", stamp=None, created=incoming_created,
        ), monkeypatch)

        assert resp.status_code == 200, resp.text
        assert resp.json()["previous_files"] == ("kept" if kept else "set_aside")
        assert (_files(room) == HALL_FILES) is kept
        if kept:
            # Still the project it was, so a file saved since with the stamp
            # is that project too.
            assert engine.project.project.uuid == HALL_STAMP
            assert _on_disk(engine)["project"]["uuid"] == HALL_STAMP
    finally:
        engine.persister.stop()


# --- What an import refuses or undoes ---------------------------------------------


async def test_an_import_against_a_stale_revision_changes_nothing_and_takes_no_backup(hall, monkeypatch):
    resp = await _import(hall, _document(
        "lobby", "Lobby", stamp="9a1d-lobby", created="2025-06-01T09:00:00",
    ), monkeypatch, **{"If-Match": '"not-this-revision"'})

    assert resp.status_code == 409, resp.text
    _hall_running(hall)
    assert backup_manager.list_backups(hall.project_path) == []


async def test_an_import_whose_load_fails_puts_the_previous_project_and_its_files_back(hall, monkeypatch):
    reconcile = hall._reconcile

    async def lobby_fails(run) -> None:
        await reconcile(run)
        if hall.project.project.name == "Lobby":
            raise RuntimeError("a part of the Lobby would not start")

    hall._reconcile = lobby_fails

    resp = await _import(hall, _document(
        "lobby", "Lobby", stamp="9a1d-lobby", created="2025-06-01T09:00:00",
    ), monkeypatch)

    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == "not_applied"
    _hall_running(hall)
    assert hall.project.project.uuid == HALL_STAMP


async def test_an_imported_login_in_the_clear_is_in_no_file_once_imported(hall, data, monkeypatch):
    document = _document("lobby", "Lobby", stamp="9a1d-lobby", created="2025-06-01T09:00:00")
    document["devices"] = [{"id": "proj1", "driver": "acme_widget", "name": "Projector", "enabled": False}]
    document["connections"] = {"proj1": {"host": "192.0.2.10", "port": 5500, "password": "Hunter2-Acme"}}

    resp = await _import(hall, document, monkeypatch)

    assert resp.status_code == 200, resp.text
    folder = hall.project_path.parent
    assert _on_disk(hall)["connections"]["proj1"]["password"] == SAVED_LOGIN
    assert DeviceLogins(data).login("proj1", "acme_widget", "password") == "Hunter2-Acme"
    assert "Hunter2-Acme" not in hall.project_path.read_text(encoding="utf-8")
    crash_copy = hall.project_path.with_suffix(".avc.bak")
    assert not crash_copy.exists() or "Hunter2-Acme" not in crash_copy.read_text(encoding="utf-8")
    assert not any("Hunter2-Acme" in p.read_text(encoding="utf-8", errors="ignore")
                   for p in folder.glob("*.avc*"))


# --- The doors that keep the files ------------------------------------------------


@pytest.mark.parametrize("pushed_stamp", [None, "5c3e-from-another-system"])
async def test_a_cloud_push_keeps_the_files_and_the_running_stamp(hall, pushed_stamp):
    from openavc.core.project_loader import ProjectConfig
    from openavc.core.project_update import MigrationNotes

    pushed = ProjectConfig(**_document("hall", "Hall (pushed)", stamp=pushed_stamp, created=HALL_CREATED))

    await hall.replace_project(pushed, MigrationNotes())

    assert hall.project.project.name == "Hall (pushed)"
    assert _files(hall.project_path.parent) == HALL_FILES
    assert hall.project.project.uuid == HALL_STAMP
    assert _on_disk(hall)["project"]["uuid"] == HALL_STAMP


async def test_an_edit_that_leaves_the_stamp_out_keeps_it(hall, monkeypatch):
    async with _client(hall, monkeypatch) as client:
        document = (await client.get("/api/project")).json()
        del document["project"]["uuid"]
        document["project"]["name"] = "Hall (renamed)"
        resp = await client.put("/api/project", json=document)

    assert resp.status_code == 200, resp.text
    assert hall.project.project.uuid == HALL_STAMP
    assert _on_disk(hall)["project"]["uuid"] == HALL_STAMP


# --- Where a stamp is made --------------------------------------------------------


def test_a_project_file_loaded_without_a_stamp_is_given_one_once(tmp_path):
    project_file = tmp_path / "project.avc"
    project_file.write_text(json.dumps(_document("hall", "Hall", stamp=None, created=HALL_CREATED)), encoding="utf-8")

    first = load_project(project_file)
    again = load_project(project_file)

    assert first.project.uuid
    assert json.loads(project_file.read_text(encoding="utf-8"))["project"]["uuid"] == first.project.uuid
    assert again.project.uuid == first.project.uuid


def test_a_stamp_that_cannot_be_written_never_stops_the_project_loading(tmp_path, monkeypatch):
    """A start that cannot write the stamp (equipment this server refuses,
    a file it cannot write) loads the project as before, never a backup or
    the Recovery Project in its place."""
    from openavc.core import project_recovery

    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"device_id": "local", "identity": "unit-b", "integration": "acme_amplifier",
                                    "name": "Built-in amplifier", "connection": {}}))
    monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(manifest))
    document = _document("hall", "Hall", stamp=None, created=HALL_CREATED)
    document["devices"] = [{"id": "amplifier", "name": "Amplifier", "enabled": False,
                            "driver": "openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232",
                            "config": {"integration": "acme_switcher"}}]
    project_file = tmp_path / "project.avc"
    project_file.write_text(json.dumps(document), encoding="utf-8")
    before = project_file.read_bytes()

    project = project_recovery.load_or_recover(project_file)

    assert project.project.name == "Hall"
    assert project_file.read_bytes() == before
    assert project_recovery.read(tmp_path) is None


async def test_a_reload_of_the_project_file_without_its_stamp_keeps_the_running_one(hall):
    """A hand edit that drops the stamp is still the same project: the
    files beside it stayed."""
    document = json.loads(hall.project_path.read_text(encoding="utf-8"))
    del document["project"]["uuid"]
    document["project"]["name"] = "Hall (edited by hand)"
    hall.project_path.write_text(json.dumps(document), encoding="utf-8")

    await hall.reload_project()

    assert hall.project.project.name == "Hall (edited by hand)"
    assert hall.project.project.uuid == HALL_STAMP
    assert _on_disk(hall)["project"]["uuid"] == HALL_STAMP


async def test_restoring_a_backup_from_before_stamps_keeps_the_running_stamp(hall, monkeypatch):
    folder = hall.project_path.parent
    document = json.loads(hall.project_path.read_text(encoding="utf-8"))
    del document["project"]["uuid"]
    old = folder / "old"
    old.mkdir()
    (old / "project.avc").write_text(json.dumps(document), encoding="utf-8")
    kept = backup_manager.create_backup(old / "project.avc", "Manual backup")
    backups = folder / "backups"
    backups.mkdir(exist_ok=True)
    restored = backups / kept.name
    kept.rename(restored)

    async with _client(hall, monkeypatch) as client:
        resp = await client.post(f"/api/backups/{backup_manager.backup_id(folder, restored)}/restore")

    assert resp.status_code == 200, resp.text
    assert hall.project.project.uuid == HALL_STAMP
    assert _on_disk(hall)["project"]["uuid"] == HALL_STAMP


def test_two_starters_a_first_start_copies_in_are_two_projects(tmp_path, monkeypatch):
    from openavc.core import project_recovery

    seed = tmp_path / "seed.avc"
    seed.write_text(json.dumps(_document("New_Room", "New Room", stamp=None, created=STARTER_CREATED)), encoding="utf-8")
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: seed)

    stamps = set()
    for room_name in ("a", "b"):
        (tmp_path / room_name).mkdir()
        project = project_recovery.load_or_recover(tmp_path / room_name / "project.avc")
        stamps.add(project.project.uuid)

    assert len(stamps) == 2 and "" not in stamps
    assert "uuid" not in json.loads(seed.read_text(encoding="utf-8"))["project"]


def test_a_blank_project_the_recovery_project_and_a_library_copy_are_each_stamped(tmp_path, monkeypatch):
    import openavc.core.project_library as library
    from openavc.core import project_recovery

    blanks = {library.create_blank_project("room", "Room").project.uuid for _ in range(2)}
    assert len(blanks) == 2 and "" not in blanks

    (tmp_path / "empty").mkdir()
    recovered = project_recovery._start_empty(tmp_path / "empty" / "project.avc", project_recovery.UNREADABLE)
    assert recovered.project.uuid

    monkeypatch.setattr(library, "_lib_dir", lambda: tmp_path / "library")
    saved = library.import_project(json.dumps(
        _document("lobby", "Lobby", stamp="9a1d-lobby", created="2025-06-01T09:00:00"),
    ).encode(), "lobby.avc", "lobby")
    library.duplicate_project(saved["id"], "lobby_copy", "Lobby Copy")
    copy, _ = library.get_project("lobby_copy")
    assert copy["project"]["uuid"] not in ("", "9a1d-lobby")

    opened = library.prepare_open(saved["id"], "lobby", "Lobby")
    assert opened.project.project.uuid not in ("", "9a1d-lobby", copy["project"]["uuid"])


def test_no_shipped_project_carries_a_stamp_and_each_created_stamp_proves_nothing():
    """The starter and the templates are copied into many rooms: a stamp in
    one would make every room built from it the same project, and the
    ``created`` stamp each carries is one of those that prove nothing."""
    import openavc

    package = Path(openavc.__file__).parent
    shipped = [package / "seed" / "default" / "project.avc", *sorted((package / "templates").glob("*.avc"))]
    assert len(shipped) > 1
    for path in shipped:
        meta = json.loads(path.read_text(encoding="utf-8"))["project"]
        assert "uuid" not in meta, path.name
        assert meta.get("created", "") in project_identity.SHIPPED_CREATED | {""}, path.name
