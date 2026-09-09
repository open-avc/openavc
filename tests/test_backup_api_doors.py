"""The three backup doors name a backup the same way.

``POST /api/backups/create`` used to answer with a bare ``backup_….zip`` while
``GET /api/backups`` reported the same file as ``backups/backup_….zip`` and
``POST /api/backups/{filename}/restore`` resolved its argument against the
project directory — so the create response was the one form restore could not
find, and feeding one door's answer into the next always 404'd.

These tests pin the round trip rather than the string: whatever shape the
identifier takes, every door has to agree on it.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest
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
    assert restored.json() == {"status": "restored", "filename": filename}


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
