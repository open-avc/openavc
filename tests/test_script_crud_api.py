"""Tests for script CRUD REST API endpoints."""

import json
from unittest.mock import MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from server.api.rest import router, set_engine
from server.core.project_loader import ProjectConfig, ScriptConfig, ProjectMeta


@pytest.fixture
def mock_engine(tmp_path):
    """Create a mock engine with a temporary project directory."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir()

    # Write a test script
    (scripts_dir / "test_script.py").write_text('print("hello")', encoding="utf-8")

    # Write project.avc
    project_json = project_dir / "project.avc"
    project_data = {
        "openavc_version": "0.1.0",
        "project": {
            "id": "test",
            "name": "Test Project",
        },
        "scripts": [
            {
                "id": "test_script",
                "file": "test_script.py",
                "enabled": True,
                "description": "A test script",
            }
        ],
    }
    project_json.write_text(json.dumps(project_data), encoding="utf-8")

    engine = MagicMock()
    engine.project_path = project_json
    engine.project = ProjectConfig(
        project=ProjectMeta(id="test", name="Test Project"),
        scripts=[
            ScriptConfig(
                id="test_script",
                file="test_script.py",
                enabled=True,
                description="A test script",
            )
        ],
    )
    engine.scripts = MagicMock()
    engine.scripts.reload_scripts = MagicMock(return_value=2)
    engine.reload_project = AsyncMock()

    return engine


@pytest.fixture
def client(mock_engine):
    """Create a test client with the mock engine."""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    set_engine(mock_engine)
    yield TestClient(app)
    set_engine(None)


def test_get_script_source(client):
    resp = client.get("/api/scripts/test_script/source")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "test_script"
    assert data["file"] == "test_script.py"
    assert 'print("hello")' in data["source"]


def test_get_script_source_not_found(client):
    resp = client.get("/api/scripts/nonexistent/source")
    assert resp.status_code == 404


def test_save_script_source(client, mock_engine):
    resp = client.put(
        "/api/scripts/test_script/source",
        json={"source": "# updated\nprint('world')"},
    )
    assert resp.status_code == 200
    # Verify file was written
    scripts_dir = mock_engine.project_path.parent / "scripts"
    content = (scripts_dir / "test_script.py").read_text(encoding="utf-8")
    assert "# updated" in content


def test_create_script(client, mock_engine):
    resp = client.post(
        "/api/scripts",
        json={
            "id": "new_script",
            "file": "new_script.py",
            "description": "New script",
            "source": "# new script\n",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["id"] == "new_script"
    # Verify file was created
    scripts_dir = mock_engine.project_path.parent / "scripts"
    assert (scripts_dir / "new_script.py").exists()


def test_create_script_duplicate(client):
    resp = client.post(
        "/api/scripts",
        json={
            "id": "test_script",
            "file": "test_script2.py",
            "source": "",
        },
    )
    assert resp.status_code == 409


def test_delete_script(client, mock_engine):
    resp = client.delete("/api/scripts/test_script")
    assert resp.status_code == 200
    # Verify file was deleted
    scripts_dir = mock_engine.project_path.parent / "scripts"
    assert not (scripts_dir / "test_script.py").exists()


def test_delete_script_not_found(client):
    resp = client.delete("/api/scripts/nonexistent")
    assert resp.status_code == 404


def test_reload_scripts(client, mock_engine):
    resp = client.post("/api/scripts/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reloaded"
    assert data["handlers"] == 2


def test_get_recent_logs(client):
    resp = client.get("/api/logs/recent?count=10")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
