"""Tests for project library — saved project file management."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server.core.project_library import (
    sanitize_id,
    list_projects,
    get_project,
    save_to_library,
    delete_project,
    update_project_meta,
    duplicate_project,
    create_blank_project,
    replace_scripts,
    ensure_starter_projects,
    _project_meta,
)


# --- Fixtures ---


@pytest.fixture
def tmp_lib(tmp_path):
    """Patch SAVED_PROJECTS_DIR to a temp directory for isolation."""
    lib_dir = tmp_path / "saved_projects"
    lib_dir.mkdir()
    with patch("server.core.project_library.config") as mock_config:
        mock_config.SAVED_PROJECTS_DIR = lib_dir
        yield lib_dir


@pytest.fixture
def sample_project_data():
    """Minimal valid project data dict."""
    return {
        "openavc_version": "0.4.0",
        "project": {
            "id": "test_room",
            "name": "Test Room",
            "description": "A test project",
            "created": "2025-01-01T00:00:00",
            "modified": "2025-01-01T00:00:00",
        },
        "devices": [
            {
                "id": "proj1",
                "driver": "pjlink_class1",
                "name": "Projector",
                "config": {},
                "enabled": True,
            }
        ],
        "variables": [],
        "macros": [
            {"id": "on", "name": "System On", "steps": []},
        ],
        "ui": {
            "pages": [
                {"id": "main", "name": "Main", "elements": []},
                {"id": "audio", "name": "Audio", "elements": []},
            ],
        },
        "scripts": [],
    }


@pytest.fixture
def sample_project_config():
    """A minimal ProjectConfig object."""
    return create_blank_project("test_room", "Test Room")


def _seed_project(lib_dir: Path, project_id: str, data: dict, scripts: dict[str, str] | None = None):
    """Helper to write a project directly into the library dir."""
    project_dir = lib_dir / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.avc").write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    if scripts:
        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        for fname, source in scripts.items():
            (scripts_dir / fname).write_text(source, encoding="utf-8")


# --- sanitize_id tests ---


class TestSanitizeId:
    def test_clean_id_unchanged(self):
        assert sanitize_id("my_project") == "my_project"

    def test_spaces_replaced(self):
        assert sanitize_id("my project") == "my_project"

    def test_special_chars_replaced(self):
        assert sanitize_id("my@project!name") == "my_project_name"

    def test_multiple_underscores_collapsed(self):
        assert sanitize_id("my___project") == "my_project"

    def test_leading_trailing_stripped(self):
        assert sanitize_id("  _hello_ ") == "hello"

    def test_empty_string_returns_untitled(self):
        assert sanitize_id("") == "untitled"

    def test_all_special_returns_untitled(self):
        assert sanitize_id("@#$%^") == "untitled"

    def test_hyphens_preserved(self):
        assert sanitize_id("my-project") == "my-project"

    def test_alphanumeric_preserved(self):
        assert sanitize_id("Room101") == "Room101"


# --- list_projects tests ---


class TestListProjects:
    def test_empty_library(self, tmp_lib):
        result = list_projects()
        assert result == []

    def test_lists_valid_projects(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        result = list_projects()
        assert len(result) == 1
        assert result[0]["id"] == "room_a"
        assert result[0]["name"] == "Test Room"
        assert result[0]["device_count"] == 1
        assert result[0]["page_count"] == 2
        assert result[0]["macro_count"] == 1

    def test_lists_multiple_projects(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        data_b = dict(sample_project_data)
        data_b = json.loads(json.dumps(sample_project_data))
        data_b["project"]["name"] = "Room B"
        _seed_project(tmp_lib, "room_b", data_b)
        result = list_projects()
        assert len(result) == 2
        ids = [p["id"] for p in result]
        assert "room_a" in ids
        assert "room_b" in ids

    def test_skips_non_directories(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        # Create a stray file in lib dir
        (tmp_lib / "stray_file.txt").write_text("hello")
        result = list_projects()
        assert len(result) == 1

    def test_skips_dirs_without_project_avc(self, tmp_lib):
        (tmp_lib / "empty_dir").mkdir()
        result = list_projects()
        assert len(result) == 0

    def test_handles_corrupt_json_gracefully(self, tmp_lib):
        project_dir = tmp_lib / "corrupt"
        project_dir.mkdir()
        (project_dir / "project.avc").write_text("not valid json{{{", encoding="utf-8")
        result = list_projects()
        assert len(result) == 1
        assert result[0]["id"] == "corrupt"
        assert result[0]["name"] == "corrupt"  # Falls back to dir name
        assert result[0]["device_count"] == 0

    def test_counts_script_files(self, tmp_lib, sample_project_data):
        _seed_project(
            tmp_lib, "scripted", sample_project_data,
            scripts={"startup.py": "print('hello')", "shutdown.py": "print('bye')"}
        )
        result = list_projects()
        assert result[0]["script_count"] == 2


# --- get_project tests ---


class TestGetProject:
    def test_get_existing_project(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        data, scripts = get_project("room_a")
        assert data["project"]["name"] == "Test Room"
        assert scripts == {}

    def test_get_project_with_scripts(self, tmp_lib, sample_project_data):
        _seed_project(
            tmp_lib, "room_a", sample_project_data,
            scripts={"main.py": "# main script"}
        )
        data, scripts = get_project("room_a")
        assert "main.py" in scripts
        assert scripts["main.py"] == "# main script"

    def test_get_missing_project_raises(self, tmp_lib):
        with pytest.raises(FileNotFoundError, match="not found"):
            get_project("nonexistent")

    def test_sanitizes_project_id(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "my_project", sample_project_data)
        # Passing an ID with special chars should still work if it sanitizes to the right thing
        data, _ = get_project("my project")
        assert data["project"]["name"] == "Test Room"


# --- save_to_library tests ---


class TestSaveToLibrary:
    def test_save_new_project(self, tmp_lib, sample_project_config):
        scripts_dir = tmp_lib / "_tmp_scripts"
        scripts_dir.mkdir()
        (scripts_dir / "test.py").write_text("# test", encoding="utf-8")

        save_to_library("new_room", sample_project_config, scripts_dir, "New Room", "A new room")

        # Verify it was written
        avc_path = tmp_lib / "new_room" / "project.avc"
        assert avc_path.exists()
        data = json.loads(avc_path.read_text(encoding="utf-8"))
        assert data["project"]["name"] == "New Room"
        assert data["project"]["description"] == "A new room"
        assert data["project"]["id"] == "new_room"

        # Verify scripts were copied
        assert (tmp_lib / "new_room" / "scripts" / "test.py").exists()

    def test_save_duplicate_raises(self, tmp_lib, sample_project_config):
        scripts_dir = tmp_lib / "_tmp_scripts"
        scripts_dir.mkdir()

        save_to_library("dup_test", sample_project_config, scripts_dir, "Room", "")
        with pytest.raises(ValueError, match="already exists"):
            save_to_library("dup_test", sample_project_config, scripts_dir, "Room", "")


# --- delete_project tests ---


class TestDeleteProject:
    def test_delete_existing(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "to_delete", sample_project_data)
        result = delete_project("to_delete")
        assert result is True
        assert not (tmp_lib / "to_delete").exists()

    def test_delete_nonexistent_returns_false(self, tmp_lib):
        result = delete_project("nope")
        assert result is False

    def test_delete_removes_scripts_too(self, tmp_lib, sample_project_data):
        _seed_project(
            tmp_lib, "scripted", sample_project_data,
            scripts={"test.py": "# code"}
        )
        assert (tmp_lib / "scripted" / "scripts" / "test.py").exists()
        delete_project("scripted")
        assert not (tmp_lib / "scripted").exists()


# --- update_project_meta tests ---


class TestUpdateProjectMeta:
    def test_update_name(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        update_project_meta("room_a", name="Updated Name", description=None)
        data, _ = get_project("room_a")
        assert data["project"]["name"] == "Updated Name"
        assert data["project"]["description"] == "A test project"  # unchanged

    def test_update_description(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        update_project_meta("room_a", name=None, description="New desc")
        data, _ = get_project("room_a")
        assert data["project"]["name"] == "Test Room"  # unchanged
        assert data["project"]["description"] == "New desc"

    def test_update_both(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        update_project_meta("room_a", name="New Name", description="New desc")
        data, _ = get_project("room_a")
        assert data["project"]["name"] == "New Name"
        assert data["project"]["description"] == "New desc"

    def test_update_sets_modified_timestamp(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "room_a", sample_project_data)
        update_project_meta("room_a", name="Updated", description=None)
        data, _ = get_project("room_a")
        # modified should be updated (different from original)
        assert data["project"]["modified"] != "2025-01-01T00:00:00"

    def test_update_missing_project_raises(self, tmp_lib):
        with pytest.raises(FileNotFoundError, match="not found"):
            update_project_meta("nonexistent", name="Foo", description=None)


# --- duplicate_project tests ---


class TestDuplicateProject:
    def test_duplicate_creates_copy(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "original", sample_project_data)
        duplicate_project("original", "copy", "Copy of Room")
        data, _ = get_project("copy")
        assert data["project"]["name"] == "Copy of Room"
        assert data["project"]["id"] == "copy"

    def test_duplicate_with_scripts(self, tmp_lib, sample_project_data):
        _seed_project(
            tmp_lib, "original", sample_project_data,
            scripts={"startup.py": "# boot"}
        )
        duplicate_project("original", "copy", "Copy")
        _, scripts = get_project("copy")
        assert "startup.py" in scripts
        assert scripts["startup.py"] == "# boot"

    def test_duplicate_to_existing_raises(self, tmp_lib, sample_project_data):
        _seed_project(tmp_lib, "original", sample_project_data)
        _seed_project(tmp_lib, "existing", sample_project_data)
        with pytest.raises(ValueError, match="already exists"):
            duplicate_project("original", "existing", "Dup")


# --- create_blank_project tests ---


class TestCreateBlankProject:
    def test_creates_valid_project(self):
        project = create_blank_project("lobby", "Lobby")
        assert project.project.id == "lobby"
        assert project.project.name == "Lobby"
        assert project.openavc_version == "0.4.0"
        assert len(project.devices) == 0
        assert len(project.macros) == 0
        assert len(project.ui.pages) == 1
        assert project.ui.pages[0].id == "main"

    def test_sets_timestamps(self):
        project = create_blank_project("lobby", "Lobby")
        assert project.project.created != ""
        assert project.project.modified != ""


# --- replace_scripts tests ---


class TestReplaceScripts:
    def test_writes_scripts(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        replace_scripts(scripts_dir, {"a.py": "# a", "b.py": "# b"})
        assert (scripts_dir / "a.py").read_text(encoding="utf-8") == "# a"
        assert (scripts_dir / "b.py").read_text(encoding="utf-8") == "# b"

    def test_clears_old_scripts(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "old.py").write_text("# old")
        replace_scripts(scripts_dir, {"new.py": "# new"})
        assert not (scripts_dir / "old.py").exists()
        assert (scripts_dir / "new.py").exists()

    def test_creates_dir_if_missing(self, tmp_path):
        scripts_dir = tmp_path / "nonexistent" / "scripts"
        replace_scripts(scripts_dir, {"test.py": "# test"})
        assert (scripts_dir / "test.py").exists()


# --- ensure_starter_projects tests ---


class TestEnsureStarterProjects:
    def test_seeds_avc_files(self, tmp_lib, sample_project_data):
        """Seeding from .avc template files."""
        seed_dir = tmp_lib.parent / "templates"
        seed_dir.mkdir()
        (seed_dir / "starter.avc").write_text(
            json.dumps(sample_project_data), encoding="utf-8"
        )

        with patch("server.core.project_library._SEED_DIR", seed_dir):
            ensure_starter_projects()

        assert (tmp_lib / "starter" / "project.avc").exists()
        # Marker should be created
        assert (tmp_lib / ".seeded").exists()

    def test_does_not_reseed(self, tmp_lib, sample_project_data):
        """If marker exists, don't seed again."""
        seed_dir = tmp_lib.parent / "templates"
        seed_dir.mkdir()
        (seed_dir / "starter.avc").write_text(
            json.dumps(sample_project_data), encoding="utf-8"
        )

        # Create marker
        (tmp_lib / ".seeded").touch()

        with patch("server.core.project_library._SEED_DIR", seed_dir):
            ensure_starter_projects()

        # Should NOT have seeded
        assert not (tmp_lib / "starter").exists()

    def test_no_templates_dir_is_noop(self, tmp_lib):
        """If templates dir doesn't exist, nothing happens."""
        with patch("server.core.project_library._SEED_DIR", tmp_lib / "nonexistent"):
            ensure_starter_projects()
        # No marker either
        assert not (tmp_lib / ".seeded").exists()

    def test_seeds_with_scripts_dir(self, tmp_lib, sample_project_data):
        """Seeds .avc file + matching .scripts/ directory."""
        seed_dir = tmp_lib.parent / "templates"
        seed_dir.mkdir()
        (seed_dir / "starter.avc").write_text(
            json.dumps(sample_project_data), encoding="utf-8"
        )
        scripts_seed = seed_dir / "starter.scripts"
        scripts_seed.mkdir()
        (scripts_seed / "boot.py").write_text("# boot script", encoding="utf-8")

        with patch("server.core.project_library._SEED_DIR", seed_dir):
            ensure_starter_projects()

        assert (tmp_lib / "starter" / "scripts" / "boot.py").exists()


# --- _project_meta tests ---


class TestProjectMeta:
    def test_extracts_metadata(self, sample_project_data):
        meta = _project_meta("room_a", sample_project_data)
        assert meta["id"] == "room_a"
        assert meta["name"] == "Test Room"
        assert meta["device_count"] == 1
        assert meta["page_count"] == 2
        assert meta["macro_count"] == 1
        assert meta["description"] == "A test project"

    def test_handles_missing_fields(self):
        data = {"project": {"name": "Minimal"}}
        meta = _project_meta("minimal", data)
        assert meta["name"] == "Minimal"
        assert meta["device_count"] == 0
        assert meta["page_count"] == 0
        assert meta["macro_count"] == 0

    def test_extracts_driver_deps(self):
        data = {
            "project": {"name": "Test"},
            "driver_dependencies": [
                {"driver_id": "pjlink_class1", "driver_name": "PJLink"},
                {"driver_id": "samsung_mdc", "driver_name": "Samsung MDC"},
            ],
        }
        meta = _project_meta("test", data)
        assert "pjlink_class1" in meta["required_drivers"]
        assert "samsung_mdc" in meta["required_drivers"]
