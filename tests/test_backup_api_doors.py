"""The three backup doors name a backup the same way.

``POST /api/backups/create`` used to answer with a bare ``backup_….zip`` while
``GET /api/backups`` reported the same file as ``backups/backup_….zip`` and
``POST /api/backups/{filename}/restore`` resolved its argument against the
project directory — so the create response was the one form restore could not
find, and feeding one door's answer into the next always 404'd.

These tests pin the round trip rather than the string: whatever shape the
identifier takes, every door has to agree on it.

The restore door also has to leave the backup it reads where it was, and the
running system saving variable values afterwards, whatever the restore did.
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest
from openavc.core.backup_manager import MAX_BACKUPS, backup_id, create_backup
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.main import app


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "project.avc").write_text(
        json.dumps({
            "project": {"id": "test", "name": "Test Room"},
            "openavc_version": "0.4.0",
            "devices": [],
            "variables": [],
            "macros": [],
            "ui": {"pages": []},
        }),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def client(project_dir: Path):
    """TestClient over an engine whose project lives in a real tmp directory,
    so the backup files the routes create are really on disk."""
    engine = MagicMock()
    engine.project_path = project_dir / "project.avc"
    engine.persister = None
    engine.reload_project = AsyncMock()
    engine.reload_persisted_state = MagicMock()
    engine.logins_needed = MagicMock(return_value=[])
    rest.set_engine(engine)
    yield TestClient(app)
    rest.set_engine(None)


def test_create_returns_the_name_list_reports(client: TestClient):
    created = client.post("/api/backups/create", json={"reason": "Manual backup"})
    assert created.status_code == 200
    filename = created.json()["filename"]

    listed = [b["filename"] for b in client.get("/api/backups").json()["backups"]]
    assert filename in listed


def test_create_response_restores(client: TestClient):
    """The whole point: the create answer goes straight back into restore."""
    filename = client.post(
        "/api/backups/create", json={"reason": "Manual backup"}
    ).json()["filename"]

    restored = client.post(f"/api/backups/{filename}/restore")

    assert restored.status_code == 200, restored.json()
    assert restored.json() == {
        "status": "restored", "filename": filename, "needs_login": [], "plugins_need_login": [],
    }


def test_listed_name_restores(client: TestClient):
    """The other direction of the same rule, so a fix to one door can't drift
    from the other."""
    client.post("/api/backups/create", json={"reason": "Manual backup"})
    filename = client.get("/api/backups").json()["backups"][0]["filename"]

    assert client.post(f"/api/backups/{filename}/restore").status_code == 200


def test_legacy_backup_is_named_and_restored_the_same_way(
    client: TestClient, project_dir: Path
):
    """A legacy ``.avc.bak`` sits beside the project file rather than in
    ``backups/``, so its identifier is a bare name — and restore takes that
    unchanged too."""
    (project_dir / "project.20260406.avc.bak").write_text(
        (project_dir / "project.avc").read_text(encoding="utf-8"), encoding="utf-8"
    )

    listed = client.get("/api/backups").json()["backups"]
    legacy = [b for b in listed if b["format"] == "legacy"]
    assert [b["filename"] for b in legacy] == ["project.20260406.avc.bak"]

    assert client.post(f"/api/backups/{legacy[0]['filename']}/restore").status_code == 200


def test_the_doors_serve_a_project_file_with_its_own_name(tmp_path: Path):
    """``OPENAVC_PROJECT`` can name any file. Create used to answer 404 "No
    project to back up" for one not called ``project.avc``, and restore wrote
    ``project.avc`` beside it and answered "restored" with nothing changed."""
    hall = tmp_path / "main-hall.avc"
    hall.write_text(json.dumps({"project": {"id": "hall", "name": "Main Hall"}}), encoding="utf-8")
    engine = MagicMock()
    engine.project_path = hall
    engine.persister = None
    engine.reload_project = AsyncMock()
    engine.reload_persisted_state = MagicMock()
    engine.logins_needed = MagicMock(return_value=[])
    rest.set_engine(engine)
    try:
        client = TestClient(app)
        created = client.post("/api/backups/create", json={"reason": "Manual backup"})
        assert created.status_code == 200, created.json()
        filename = created.json()["filename"]
        assert filename in [b["filename"] for b in client.get("/api/backups").json()["backups"]]

        hall.write_text(json.dumps({"project": {"id": "hall", "name": "Changed"}}), encoding="utf-8")
        assert client.post(f"/api/backups/{filename}/restore").status_code == 200

        assert json.loads(hall.read_text(encoding="utf-8"))["project"]["name"] == "Main Hall"
        assert not (tmp_path / "project.avc").exists()
    finally:
        rest.set_engine(None)


# ── restoring: the chosen backup and the saved values ──


def _write_project(project_file: Path, name: str, variables: object = None) -> None:
    """A project named ``name`` holding one variable whose value is saved."""
    if variables is None:
        variables = [{"id": "level", "type": "number", "default": 0, "persist": True}]
    project_file.write_text(json.dumps({
        "openavc_version": "0.4.0",
        "project": {"id": "room", "name": name},
        "devices": [],
        "variables": variables,
        "macros": [],
        "ui": {"pages": []},
    }), encoding="utf-8")


def _running_engine(project_dir: Path) -> Engine:
    """A real engine on the project in ``project_dir``, its variables seeded
    and its persister watching them as a start leaves them. Only the reload is
    stood in for: it reads the project file again, as the real one does first,
    without reconnecting anything."""
    project_file = project_dir / "project.avc"
    engine = Engine(str(project_file))
    engine.project = load_project(project_file)
    engine._start_variables()

    async def reload_project() -> None:
        engine.project = load_project(engine.project_path)

    engine.reload_project = reload_project
    return engine


def _saves_values(engine: Engine) -> bool:
    """Whether a change to a saved variable reaches state.json."""
    engine.state.set("var.level", 42, source="ui")
    engine.persister.flush()
    state_file = engine.project_path.parent / "state.json"
    if not state_file.is_file():
        return False
    return json.loads(state_file.read_text(encoding="utf-8")).get("var.level") == 42


def _project_name(project_file: Path) -> str:
    return json.loads(project_file.read_text(encoding="utf-8"))["project"]["name"]


@pytest.fixture(params=["directly", "through a link"])
def restore_dir(request, tmp_path: Path) -> Path:
    """The project directory, reached as it is or through a link to it, as a
    data directory on a mounted or linked volume is: the door resolves the
    backup it was handed while the rotation lists the directory as named."""
    if request.param == "directly":
        return tmp_path
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("this system makes no directory links")
    return link


def test_the_oldest_of_a_full_list_of_backups_restores_and_is_kept(restore_dir: Path):
    """The "Before restore" backup the door takes first used to rotate out the
    oldest backup, which is the one chosen: the restore then failed, the
    backup was gone and values were no longer saved."""
    tmp_path = restore_dir
    project_file = tmp_path / "project.avc"
    taken = []
    for n in range(MAX_BACKUPS):
        _write_project(project_file, f"Version {n}")
        path = create_backup(project_file, "Manual backup")
        # The rotation goes by modification time: older ones first, whatever
        # the clock's resolution.
        os.utime(path, (1_000_000 + n, 1_000_000 + n))
        taken.append(path)
    oldest = taken[0]
    _write_project(project_file, "Current")
    engine = _running_engine(tmp_path)
    rest.set_engine(engine)
    try:
        restored = TestClient(app).post(f"/api/backups/{backup_id(tmp_path, oldest)}/restore")

        assert restored.status_code == 200, restored.text
        assert _project_name(project_file) == "Version 0"
        assert oldest.exists()
        # Nothing the person did not choose went either: the list holds every
        # backup it held, and the one taken before the restore.
        assert all(path.exists() for path in taken)
        assert len(list((tmp_path / "backups").glob("backup_*.zip"))) == MAX_BACKUPS + 1
        assert _saves_values(engine)
    finally:
        engine.persister.stop()
        rest.set_engine(None)


def test_a_restore_that_fails_leaves_values_being_saved(tmp_path: Path):
    """The persister is stopped for the restore; a restore that fails used to
    leave it stopped, so no variable value was saved until a restart."""
    project_file = tmp_path / "project.avc"
    _write_project(project_file, "Will not load", variables="not a list")
    broken = create_backup(project_file, "Manual backup")
    _write_project(project_file, "Current")
    engine = _running_engine(tmp_path)
    rest.set_engine(engine)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        restored = client.post(f"/api/backups/{backup_id(tmp_path, broken)}/restore")

        assert restored.status_code != 200
        assert _saves_values(engine)
    finally:
        engine.persister.stop()
        rest.set_engine(None)
