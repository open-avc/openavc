"""Custom themes remain usable when a project is moved or restored."""

import io
import json
import sys
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api import themes
from openavc.api.routes import project as project_routes
from openavc.core import backup_manager, project_library as library, theme_tree
from openavc.core.project_loader import save_project


@pytest.fixture
def room(tmp_path, monkeypatch):
    active = tmp_path / "active"
    active.mkdir()
    project = library.create_blank_project("studio", "Studio")
    project.ui.settings.theme_id = "studio-green"
    engine = SimpleNamespace(project=project, project_path=active / "project.avc")
    save_project(engine.project_path, project)
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", tmp_path / "library")
    monkeypatch.setattr(project_routes, "_get_engine", lambda: engine)
    monkeypatch.setattr(themes, "_engine", engine)
    app = FastAPI()
    app.include_router(project_routes.router, prefix="/api")
    app.include_router(themes.router)
    app.include_router(themes.open_router)
    client = TestClient(app)
    theme = {
        "id": "studio-green", "name": "Studio Green", "version": "1.0.0",
        "variables": {"bg_color": "#17241f", "accent_color": "#8ab493"},
        "element_defaults": {"slider": {"thumb_size": 60}},
    }
    assert client.post("/api/themes", json=theme).status_code == 200
    alternative = {**theme, "id": "studio-blue", "name": "Studio Blue"}
    assert client.post("/api/themes", json=alternative).status_code == 200
    return client, engine, theme


@pytest.mark.parametrize("export_source", ["active", "library"])
def test_save_replace_duplicate_export_import_open_preserves_theme(room, tmp_path, export_source):
    client, engine, theme = room
    response = client.post("/api/library", json={"id": "saved", "name": "Saved"})
    assert response.status_code == 200, response.text
    saved_theme = library._lib_dir() / "saved/themes/studio-green.json"
    assert json.loads(saved_theme.read_text()) == theme

    # Replacing a saved room carries the edited theme, not an older copy.
    theme["variables"]["accent_color"] = "#aabbcc"
    assert client.put("/api/themes/studio-green", json=theme).status_code == 200
    response = client.put("/api/library/saved", json={"name": "Saved"})
    assert response.status_code == 200, response.text
    assert json.loads(saved_theme.read_text()) == theme
    library.duplicate_project("saved", "duplicate", "Duplicate")

    if export_source == "active":
        content, _, _ = library.export_active_project(engine.project_path)
    else:
        content, _, _ = library.export_project("duplicate")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert json.loads(archive.read("themes/studio-green.json")) == theme
        assert "themes/studio-blue.json" in archive.namelist()

    library.import_project(content, "room.zip", override_id="imported")
    # A fresh active project has no access to the source's custom themes.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    engine.project_path = fresh / "project.avc"
    engine.project = library.open_from_library(
        "imported", engine.project_path, fresh / "scripts", "fresh", "Fresh",
    )
    assert engine.project.ui.settings.theme_id == theme["id"]
    loaded = client.get("/api/themes/studio-green")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json() == {**theme, "_source": "custom"}
    assert client.get("/api/themes/studio-blue").status_code == 200

    # Opening another project replaces the theme tree instead of leaking the
    # previous room's custom themes into the next room's Theme Studio.
    blank = library.create_blank_project("blank", "Blank")
    library.save_to_library("blank", blank, fresh / "missing", "Blank", "")
    library.open_from_library("blank", engine.project_path, fresh / "scripts", "blank", "Blank")
    assert client.get("/api/themes/studio-green").status_code == 404


def test_seeded_bundle_keeps_custom_themes(room, tmp_path):
    client, engine, theme = room
    content, _, _ = library.export_active_project(engine.project_path)
    bundle = tmp_path / "starter.zip"
    bundle.write_bytes(content)
    library._seed_zip_to_library(bundle, "starter", library._lib_dir())
    library.open_from_library(
        "starter", engine.project_path, engine.project_path.parent / "scripts", "test", "Test",
    )
    assert client.get("/api/themes/studio-green").json() == {**theme, "_source": "custom"}


def test_backup_restores_theme_and_removes_newer_themes(room):
    client, engine, theme = room
    active = engine.project_path.parent
    backup = backup_manager.create_backup(active, "Before theme edits")
    assert backup is not None
    changed = {**theme, "variables": {"bg_color": "#ffffff"}}
    assert client.put("/api/themes/studio-green", json=changed).status_code == 200
    assert client.post("/api/themes", json={**theme, "id": "later-theme"}).status_code == 200
    backup_manager.restore_from_backup(backup, active)
    assert client.get("/api/themes/studio-green").json() == {**theme, "_source": "custom"}
    assert client.get("/api/themes/later-theme").status_code == 404


@pytest.mark.parametrize("name", [
    "themes/../escape.json", "themes//escape.json", "themes/nested/escape.json",
    "themes/..\\escape.json", "themes/escape.py", "themes/.hidden.json",
])
def test_import_does_not_flatten_or_write_unsafe_theme_members(room, tmp_path, name):
    _, engine, theme = room
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("project.avc", engine.project_path.read_bytes())
        archive.writestr("themes/studio-green.json", json.dumps(theme))
        archive.writestr(name, "unwanted")
    library.import_project(stream.getvalue(), "room.zip", override_id="safe")
    imported = library._lib_dir() / "safe"
    assert sorted(p.name for p in (imported / "themes").iterdir()) == ["studio-green.json"]
    assert not (imported / "escape.json").exists()
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege on Windows")
def test_theme_exports_do_not_follow_symlinks_or_include_unrelated_files(tmp_path):
    source = tmp_path / "themes"
    source.mkdir()
    (source / "room.json").write_text("{}")
    (source / ".DS_Store").write_text("noise")
    (source / "notes.txt").write_text("notes")
    outside = tmp_path / "outside.json"
    outside.write_text("private")
    (source / "linked.json").symlink_to(outside)
    assert [name for name, _ in theme_tree.zip_entries(source)] == ["themes/room.json"]
