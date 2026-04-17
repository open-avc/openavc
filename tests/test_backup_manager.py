"""Tests for backup manager — create, list, restore, rotate."""

import json
import zipfile
from pathlib import Path

import pytest

from server.core.backup_manager import (
    cleanup_backups,
    create_backup,
    list_backups,
    restore_from_backup,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a project directory with a project.avc and sample scripts/assets."""
    project_data = {
        "project": {"id": "test", "name": "Test Project"},
        "openavc_version": "0.4.0",
        "devices": [],
        "variables": [],
        "macros": [],
        "ui": {"pages": []},
    }
    (tmp_path / "project.avc").write_text(json.dumps(project_data), encoding="utf-8")

    # Scripts
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "startup.py").write_text("print('hello')", encoding="utf-8")
    (scripts_dir / "shutdown.py").write_text("print('bye')", encoding="utf-8")

    # Assets
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "logo.png").write_bytes(b"\x89PNG fake image data")
    (assets_dir / "bg.jpg").write_bytes(b"\xff\xd8\xff fake jpeg")

    return tmp_path


@pytest.fixture
def empty_project_dir(tmp_path: Path) -> Path:
    """Project directory with no project.avc."""
    return tmp_path


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------

class TestCreateBackup:
    def test_creates_zip(self, project_dir: Path):
        result = create_backup(project_dir, "Manual backup")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".zip"
        assert "manual_backup" in result.name

    def test_zip_contains_project_file(self, project_dir: Path):
        result = create_backup(project_dir, "Test")
        assert result is not None
        with zipfile.ZipFile(result, "r") as zf:
            assert "project.avc" in zf.namelist()
            data = json.loads(zf.read("project.avc"))
            assert data["project"]["name"] == "Test Project"

    def test_zip_contains_metadata(self, project_dir: Path):
        result = create_backup(project_dir, "My reason")
        assert result is not None
        with zipfile.ZipFile(result, "r") as zf:
            assert "backup_meta.json" in zf.namelist()
            meta = json.loads(zf.read("backup_meta.json"))
            assert meta["reason"] == "My reason"
            assert meta["project_name"] == "Test Project"
            assert "timestamp" in meta

    def test_zip_contains_scripts(self, project_dir: Path):
        result = create_backup(project_dir, "Test")
        assert result is not None
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "scripts/startup.py" in names
            assert "scripts/shutdown.py" in names

    def test_zip_contains_assets(self, project_dir: Path):
        result = create_backup(project_dir, "Test")
        assert result is not None
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "assets/logo.png" in names
            assert "assets/bg.jpg" in names

    def test_no_project_file_returns_none(self, empty_project_dir: Path):
        result = create_backup(empty_project_dir, "Nothing to back up")
        assert result is None

    def test_reason_slug_in_filename(self, project_dir: Path):
        result = create_backup(project_dir, "AI configuration change")
        assert result is not None
        assert "ai_configuration_change" in result.name

    def test_special_chars_in_reason(self, project_dir: Path):
        result = create_backup(project_dir, "User's backup! @#$%")
        assert result is not None
        # Should create a valid filename
        assert result.exists()

    def test_creates_backups_dir(self, project_dir: Path):
        # Remove backups dir if it exists
        backups_dir = project_dir / "backups"
        if backups_dir.exists():
            import shutil
            shutil.rmtree(backups_dir)

        result = create_backup(project_dir, "Test")
        assert result is not None
        assert (project_dir / "backups").is_dir()

    def test_no_scripts_dir_ok(self, tmp_path: Path):
        project_data = {"project": {"id": "t", "name": "T"}}
        (tmp_path / "project.avc").write_text(json.dumps(project_data), encoding="utf-8")
        result = create_backup(tmp_path, "Test")
        assert result is not None

    def test_corrupt_project_file_still_creates_backup(self, tmp_path: Path):
        (tmp_path / "project.avc").write_text("not valid json", encoding="utf-8")
        result = create_backup(tmp_path, "Test")
        assert result is not None
        # project_name will be empty but backup still created
        with zipfile.ZipFile(result, "r") as zf:
            meta = json.loads(zf.read("backup_meta.json"))
            assert meta["project_name"] == ""


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------

class TestListBackups:
    def test_lists_zip_backups(self, project_dir: Path):
        create_backup(project_dir, "First")
        create_backup(project_dir, "Second")

        results = list_backups(project_dir)
        assert len(results) == 2
        assert all(b.format == "zip" for b in results)

    def test_newest_first(self, project_dir: Path):
        create_backup(project_dir, "Old")
        create_backup(project_dir, "New")

        results = list_backups(project_dir)
        assert len(results) >= 2
        # Newest should be first
        assert results[0].reason == "New"
        assert results[1].reason == "Old"

    def test_empty_dir(self, empty_project_dir: Path):
        results = list_backups(empty_project_dir)
        assert results == []

    def test_includes_metadata(self, project_dir: Path):
        create_backup(project_dir, "Test backup")
        results = list_backups(project_dir)
        assert len(results) == 1
        b = results[0]
        assert b.reason == "Test backup"
        assert b.project_name == "Test Project"
        assert b.size_bytes > 0
        assert b.format == "zip"
        assert b.filename.startswith("backups/")

    def test_ignores_corrupted_zips(self, project_dir: Path):
        create_backup(project_dir, "Good")
        # Create a corrupted zip
        bad_path = project_dir / "backups" / "backup_99999999_999999_bad.zip"
        bad_path.write_bytes(b"not a zip file")

        results = list_backups(project_dir)
        # Should only list the good backup
        assert len(results) == 1
        assert results[0].reason == "Good"


# ---------------------------------------------------------------------------
# restore_from_backup
# ---------------------------------------------------------------------------

class TestRestoreFromBackup:
    def test_restore_project_file(self, project_dir: Path):
        # Create backup
        backup_path = create_backup(project_dir, "Before changes")
        assert backup_path is not None

        # Modify the project
        new_data = {"project": {"id": "test", "name": "MODIFIED"}, "devices": []}
        (project_dir / "project.avc").write_text(json.dumps(new_data), encoding="utf-8")

        # Restore
        restore_from_backup(backup_path, project_dir)

        # Verify original project restored
        restored = json.loads((project_dir / "project.avc").read_text(encoding="utf-8"))
        assert restored["project"]["name"] == "Test Project"

    def test_restore_scripts(self, project_dir: Path):
        backup_path = create_backup(project_dir, "Test")
        assert backup_path is not None

        # Delete a script and add a new one
        (project_dir / "scripts" / "startup.py").unlink()
        (project_dir / "scripts" / "new_script.py").write_text("new", encoding="utf-8")

        # Restore
        restore_from_backup(backup_path, project_dir)

        # Original scripts restored, new script removed
        scripts = list((project_dir / "scripts").glob("*.py"))
        script_names = {s.name for s in scripts}
        assert "startup.py" in script_names
        assert "shutdown.py" in script_names
        assert "new_script.py" not in script_names

    def test_restore_assets(self, project_dir: Path):
        backup_path = create_backup(project_dir, "Test")
        assert backup_path is not None

        # Verify assets exist after restore
        restore_from_backup(backup_path, project_dir)
        assert (project_dir / "assets" / "logo.png").exists()
        assert (project_dir / "assets" / "bg.jpg").exists()

    def test_missing_backup_raises(self, project_dir: Path):
        fake_path = project_dir / "backups" / "nonexistent.zip"
        with pytest.raises(FileNotFoundError):
            restore_from_backup(fake_path, project_dir)

    def test_zip_without_project_avc_raises(self, project_dir: Path):
        bad_zip = project_dir / "backups" / "bad_backup.zip"
        (project_dir / "backups").mkdir(exist_ok=True)
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("random.txt", "no project file here")

        with pytest.raises(ValueError, match="does not contain project.avc"):
            restore_from_backup(bad_zip, project_dir)

    def test_unrecognized_format_raises(self, project_dir: Path):
        bad_file = project_dir / "backup.tar.gz"
        bad_file.write_bytes(b"fake")
        with pytest.raises(ValueError, match="Unrecognized backup format"):
            restore_from_backup(bad_file, project_dir)

    def test_legacy_backup_restore(self, project_dir: Path):
        # Create a legacy .avc.bak file
        legacy_data = {"project": {"id": "test", "name": "Legacy"}}
        legacy_path = project_dir / "project.20240101_120000.avc.bak"
        legacy_path.write_text(json.dumps(legacy_data), encoding="utf-8")

        restore_from_backup(legacy_path, project_dir)
        restored = json.loads((project_dir / "project.avc").read_text(encoding="utf-8"))
        assert restored["project"]["name"] == "Legacy"


# ---------------------------------------------------------------------------
# cleanup_backups (rotation)
# ---------------------------------------------------------------------------

class TestCleanupBackups:
    def test_removes_oldest_beyond_limit(self, project_dir: Path):
        backup_dir = project_dir / "backups"
        backup_dir.mkdir(exist_ok=True)

        # Create 5 backups
        for i in range(5):
            path = backup_dir / f"backup_2024010{i}_120000_test.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("project.avc", "{}")

        removed = cleanup_backups(backup_dir, keep=3)
        assert removed == 2
        remaining = list(backup_dir.glob("backup_*.zip"))
        assert len(remaining) == 3

    def test_no_removal_when_under_limit(self, project_dir: Path):
        backup_dir = project_dir / "backups"
        backup_dir.mkdir(exist_ok=True)

        for i in range(2):
            path = backup_dir / f"backup_2024010{i}_120000_test.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("project.avc", "{}")

        removed = cleanup_backups(backup_dir, keep=5)
        assert removed == 0

    def test_empty_dir(self, project_dir: Path):
        backup_dir = project_dir / "backups"
        backup_dir.mkdir(exist_ok=True)
        removed = cleanup_backups(backup_dir, keep=3)
        assert removed == 0

    def test_auto_rotation_on_create(self, project_dir: Path):
        # Create more than max_backups
        for i in range(5):
            create_backup(project_dir, f"Backup {i}", max_backups=3)

        backups = list((project_dir / "backups").glob("backup_*.zip"))
        assert len(backups) == 3
