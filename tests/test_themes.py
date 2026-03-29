"""Tests for Theme API endpoints."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.core.engine import Engine
from server.main import app
from server.api import rest, themes as themes_api


TEST_PROJECT = {
    "project": {"id": "theme_test", "name": "Theme Test Room"},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {"pages": [], "settings": {"theme_id": "dark-default"}},
}


@pytest.fixture
async def client():
    """Start engine with a test project, yield TestClient."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT, f)
        tmp_path = f.name

    engine = Engine(tmp_path)

    from server.core.project_loader import load_project
    engine.project = load_project(tmp_path)
    engine._running = True

    rest.set_engine(engine)
    themes_api.set_engine(engine)

    yield TestClient(app)

    rest.set_engine(None)
    themes_api.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


async def test_list_themes(client):
    resp = client.get("/api/themes")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 6  # At least 6 built-in themes
    # Each theme has required fields
    for t in data:
        assert "id" in t
        assert "name" in t


async def test_get_builtin_theme(client):
    resp = client.get("/api/themes/dark-default")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "dark-default"
    assert "variables" in data
    assert "panel_bg" in data["variables"]


async def test_get_theme_not_found(client):
    resp = client.get("/api/themes/nonexistent-theme-xyz")
    assert resp.status_code == 404


async def test_create_custom_theme(client):
    import uuid
    theme_id = f"test-custom-{uuid.uuid4().hex[:8]}"
    theme = {
        "name": "Test Custom",
        "id": theme_id,
        "version": "1.0.0",
        "variables": {
            "panel_bg": "#000000",
            "panel_text": "#ffffff",
            "accent": "#ff0000",
        },
    }
    resp = client.post("/api/themes", json=theme)
    assert resp.status_code == 200

    # Verify it appears in list
    resp = client.get("/api/themes")
    ids = [t["id"] for t in resp.json()]
    assert theme_id in ids

    # Cleanup
    client.delete(f"/api/themes/{theme_id}")


async def test_create_theme_invalid_id(client):
    theme = {
        "name": "Bad ID",
        "id": "BAD ID WITH SPACES!",
        "version": "1.0.0",
        "variables": {},
    }
    resp = client.post("/api/themes", json=theme)
    assert resp.status_code == 400


async def test_create_theme_missing_fields(client):
    theme = {"name": "Missing Fields"}  # Missing id, version, variables
    resp = client.post("/api/themes", json=theme)
    assert resp.status_code == 400


async def test_update_custom_theme(client):
    import uuid
    theme_id = f"updatable-{uuid.uuid4().hex[:8]}"
    theme = {
        "name": "Updatable",
        "id": theme_id,
        "version": "1.0.0",
        "variables": {"panel_bg": "#111111"},
    }
    client.post("/api/themes", json=theme)

    # Update
    theme["variables"]["panel_bg"] = "#222222"
    resp = client.put(f"/api/themes/{theme_id}", json=theme)
    assert resp.status_code == 200

    # Verify
    resp = client.get(f"/api/themes/{theme_id}")
    assert resp.json()["variables"]["panel_bg"] == "#222222"

    # Cleanup
    client.delete(f"/api/themes/{theme_id}")


async def test_delete_custom_theme(client):
    import uuid
    theme_id = f"deletable-{uuid.uuid4().hex[:8]}"
    theme = {
        "name": "Deletable",
        "id": theme_id,
        "version": "1.0.0",
        "variables": {},
    }
    client.post("/api/themes", json=theme)

    # Delete
    resp = client.delete(f"/api/themes/{theme_id}")
    assert resp.status_code == 200

    # Verify gone
    resp = client.get(f"/api/themes/{theme_id}")
    assert resp.status_code == 404


async def test_delete_builtin_theme(client):
    resp = client.delete("/api/themes/dark-default")
    assert resp.status_code == 403


async def test_export_theme(client):
    resp = client.get("/api/themes/dark-default/export")
    assert resp.status_code == 200
    data = resp.json()
    # Export returns the theme JSON (may be wrapped or direct)
    theme = data.get("theme", data)
    assert "id" in theme
    assert "variables" in theme
