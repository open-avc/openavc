"""Regression tests: every project ingress door runs the format-migration chain.

PUT /project (the Programmer's import flow saves through it) validated raw
bodies against the current schema only, so a legitimately older export was
rejected with a 422 even though a disk load of the same file would migrate it
fine. open_from_library validated the stored copy before migrating — it
worked only because current validators happen to accept old shapes, healed by
the later disk reload.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.rest import router, set_engine


def _old_format_body() -> dict:
    """A 0.1.0-format project: connection fields still live in device.config
    and there is no connections table."""
    return {
        "openavc_version": "0.1.0",
        "project": {"id": "old_room", "name": "Old Room"},
        "devices": [
            {
                "id": "proj1",
                "driver": "acme_projector",
                "name": "Projector",
                "config": {"host": "10.0.0.5", "port": 4352},
                "enabled": True,
            }
        ],
        "variables": [],
        "macros": [],
        "ui": {"pages": []},
        "scripts": [],
    }


@pytest.fixture
def mock_engine(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "project.avc").write_text("{}", encoding="utf-8")

    engine = MagicMock()
    engine.project_path = project_dir / "project.avc"
    engine._project_revision = 0
    engine.apply_project = AsyncMock(return_value=1)
    engine.reload_project = AsyncMock()
    engine.broadcast_ws = AsyncMock()
    return engine


@pytest.fixture
def client(mock_engine):
    app = FastAPI()
    app.include_router(router)
    set_engine(mock_engine)
    yield TestClient(app, raise_server_exceptions=False)
    set_engine(None)


class TestPutProjectMigrates:
    def test_old_format_body_is_migrated_before_apply(self, client, mock_engine):
        resp = client.put("/api/project", json=_old_format_body())
        assert resp.status_code == 200

        project = mock_engine.apply_project.call_args.args[0]
        assert project.openavc_version == "0.7.0"
        # 0.1.0 -> 0.2.0 moves connection fields into the connections table.
        assert project.connections["proj1"]["host"] == "10.0.0.5"
        assert project.connections["proj1"]["port"] == 4352
        assert "host" not in project.devices[0].config

    def test_current_format_body_passes_through(self, client, mock_engine):
        body = {
            "openavc_version": "0.7.0",
            "project": {"id": "room", "name": "Room"},
            "devices": [],
            "connections": {},
        }
        resp = client.put("/api/project", json=body)
        assert resp.status_code == 200
        project = mock_engine.apply_project.call_args.args[0]
        assert project.openavc_version == "0.7.0"

    def test_non_object_body_is_422(self, client):
        resp = client.put("/api/project", json=[1, 2, 3])
        assert resp.status_code == 422

    def test_invalid_project_is_422(self, client):
        resp = client.put("/api/project", json={"openavc_version": "0.7.0"})
        assert resp.status_code == 422  # missing required `project` section


class TestOpenFromLibraryDoor:
    def _seed_library(self, lib_dir, project_id: str, data: dict) -> None:
        d = lib_dir / project_id
        d.mkdir(parents=True)
        (d / "project.avc").write_text(json.dumps(data), encoding="utf-8")

    def test_open_writes_already_migrated_file(self, tmp_path):
        """The active file must land migrated at write time — not rely on the
        follow-up disk reload to heal the stored format."""
        import server.core.project_library as plib
        from server.core.project_library import open_from_library

        lib_dir = tmp_path / "saved_projects"
        lib_dir.mkdir()
        self._seed_library(lib_dir, "legacy", _old_format_body())

        active = tmp_path / "active" / "project.avc"
        active.parent.mkdir(parents=True)
        with patch.object(plib, "config") as mock_config:
            mock_config.SAVED_PROJECTS_DIR = lib_dir
            open_from_library("legacy", active, active.parent / "scripts",
                              "legacy", "Legacy")

        written = json.loads(active.read_text(encoding="utf-8"))
        assert written["openavc_version"] == "0.7.0"
        assert written["connections"]["proj1"]["host"] == "10.0.0.5"
        assert "host" not in written["devices"][0]["config"]

    def test_route_returns_422_for_invalid_stored_project(
        self, client, mock_engine, tmp_path
    ):
        """A stored project that fails validation even after migration must
        surface as a friendly 422, not a raw 500 (the route used to catch
        only FileNotFoundError)."""
        import server.core.project_library as plib

        lib_dir = tmp_path / "saved_projects"
        lib_dir.mkdir()
        bad = _old_format_body()
        # Dotted plugin ids are rejected by ProjectConfig regardless of format.
        bad["plugins"] = {"a.b": {"enabled": True, "config": {}}}
        self._seed_library(lib_dir, "broken", bad)

        with patch.object(plib, "config") as mock_config:
            mock_config.SAVED_PROJECTS_DIR = lib_dir
            resp = client.post(
                "/api/project/open-from-library",
                json={"library_id": "broken", "project_name": "Broken"},
            )
        assert resp.status_code == 422
