"""Tests for server.updater — update system components."""

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from server.updater.checker import UpdateChecker, ReleaseInfo, parse_semver, is_newer
from server.updater.platform import (
    DeploymentType,
    detect_deployment_type,
    can_self_update,
    update_instructions,
)
from server.updater.backup import create_backup, list_backups, cleanup_old_backups
from server.updater.rollback import (
    write_pending_marker,
    read_pending_marker,
    increment_marker_attempts,
    clear_pending_marker,
    check_rollback_needed,
)
from server.updater.manager import UpdateManager


# --- Semver Parsing ---


class TestParseSemver:
    def test_simple(self):
        assert parse_semver("1.2.3") == (1, 2, 3, "")

    def test_with_v_prefix(self):
        assert parse_semver("v1.2.3") == (1, 2, 3, "")

    def test_with_prerelease(self):
        assert parse_semver("1.0.0-beta.1") == (1, 0, 0, "beta.1")

    def test_with_v_and_prerelease(self):
        assert parse_semver("v2.1.0-rc.2") == (2, 1, 0, "rc.2")

    def test_invalid(self):
        assert parse_semver("not-a-version") == (0, 0, 0, "")


class TestIsNewer:
    def test_major_bump(self):
        assert is_newer("2.0.0", "1.0.0") is True

    def test_minor_bump(self):
        assert is_newer("1.1.0", "1.0.0") is True

    def test_patch_bump(self):
        assert is_newer("1.0.1", "1.0.0") is True

    def test_same_version(self):
        assert is_newer("1.0.0", "1.0.0") is False

    def test_older_version(self):
        assert is_newer("0.9.0", "1.0.0") is False

    def test_stable_newer_than_prerelease(self):
        assert is_newer("1.0.0", "1.0.0-beta.1") is True

    def test_prerelease_not_newer_than_stable(self):
        assert is_newer("1.0.0-beta.1", "1.0.0") is False

    def test_newer_prerelease(self):
        assert is_newer("1.0.0-beta.2", "1.0.0-beta.1") is True

    def test_v_prefix_handled(self):
        assert is_newer("v1.1.0", "v1.0.0") is True


# --- Update Checker ---


class TestUpdateChecker:
    @pytest.fixture
    def checker(self):
        return UpdateChecker(current_version="0.1.0")

    def _make_mock_client(self, releases):
        """Create a mocked httpx.AsyncClient that returns the given releases."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = releases
        mock_response.raise_for_status = MagicMock()

        instance = AsyncMock()
        instance.get.return_value = mock_response
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        return instance

    async def test_check_no_updates(self, checker):
        releases = [
            {
                "tag_name": "v0.1.0",
                "prerelease": False,
                "draft": False,
                "body": "Initial release",
                "published_at": "2026-01-01T00:00:00Z",
                "assets": [],
            }
        ]

        with patch("server.updater.checker.httpx.AsyncClient") as mock_client:
            mock_client.return_value = self._make_mock_client(releases)
            result = await checker.check("stable")

        assert result is None

    async def test_check_update_available(self, checker):
        releases = [
            {
                "tag_name": "v0.2.0",
                "prerelease": False,
                "draft": False,
                "body": "New features!",
                "published_at": "2026-03-01T00:00:00Z",
                "assets": [
                    {
                        "name": "openavc-0.2.0-linux-amd64.tar.gz",
                        "browser_download_url": "https://example.com/release.tar.gz",
                        "size": 50000000,
                    }
                ],
            },
            {
                "tag_name": "v0.1.0",
                "prerelease": False,
                "draft": False,
                "body": "Initial release",
                "published_at": "2026-01-01T00:00:00Z",
                "assets": [],
            },
        ]

        with patch("server.updater.checker.httpx.AsyncClient") as mock_client:
            mock_client.return_value = self._make_mock_client(releases)
            result = await checker.check("stable")

        assert result is not None
        assert result.version == "0.2.0"
        assert result.changelog == "New features!"
        assert len(result.assets) == 1

    async def test_check_skips_prerelease_on_stable(self, checker):
        releases = [
            {
                "tag_name": "v0.3.0-beta.1",
                "prerelease": True,
                "draft": False,
                "body": "Beta",
                "published_at": "2026-03-01T00:00:00Z",
                "assets": [],
            },
        ]

        with patch("server.updater.checker.httpx.AsyncClient") as mock_client:
            mock_client.return_value = self._make_mock_client(releases)
            result = await checker.check("stable")

        assert result is None

    async def test_check_includes_prerelease_on_beta(self, checker):
        releases = [
            {
                "tag_name": "v0.3.0-beta.1",
                "prerelease": True,
                "draft": False,
                "body": "Beta",
                "published_at": "2026-03-01T00:00:00Z",
                "assets": [],
            },
        ]

        with patch("server.updater.checker.httpx.AsyncClient") as mock_client:
            mock_client.return_value = self._make_mock_client(releases)
            result = await checker.check("beta")

        assert result is not None
        assert result.version == "0.3.0-beta.1"

    async def test_check_skips_drafts(self, checker):
        releases = [
            {
                "tag_name": "v1.0.0",
                "prerelease": False,
                "draft": True,
                "body": "Draft",
                "published_at": "2026-03-01T00:00:00Z",
                "assets": [],
            },
        ]

        with patch("server.updater.checker.httpx.AsyncClient") as mock_client:
            mock_client.return_value = self._make_mock_client(releases)
            result = await checker.check("stable")

        assert result is None

    async def test_check_network_error(self, checker):
        import httpx

        with patch("server.updater.checker.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get.side_effect = httpx.ConnectError("Connection refused")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await checker.check("stable")

        assert result is None
        assert "Network error" in checker.last_error


# --- Platform Detection ---


class TestPlatformDetection:
    def test_detects_git_dev(self):
        # We're running from a git checkout
        app_dir = Path(__file__).resolve().parent.parent
        dtype = detect_deployment_type(app_dir)
        assert dtype == DeploymentType.GIT_DEV

    def test_can_self_update_installer(self):
        assert can_self_update(DeploymentType.WINDOWS_INSTALLER) is True
        assert can_self_update(DeploymentType.LINUX_PACKAGE) is True

    def test_cannot_self_update_docker(self):
        assert can_self_update(DeploymentType.DOCKER) is False
        assert can_self_update(DeploymentType.GIT_DEV) is False

    def test_update_instructions_docker(self):
        msg = update_instructions(DeploymentType.DOCKER, "1.0.0")
        assert "docker compose pull" in msg

    def test_update_instructions_git(self):
        msg = update_instructions(DeploymentType.GIT_DEV, "1.0.0")
        assert "git pull" in msg


# --- Backup ---


class TestBackup:
    def test_create_backup(self, tmp_path):
        # Create some fake data
        projects_dir = tmp_path / "projects" / "default"
        projects_dir.mkdir(parents=True)
        (projects_dir / "project.avc").write_text('{"name": "test"}')
        (tmp_path / "system.json").write_text('{"network": {}}')

        backup_path = create_backup(tmp_path, "0.1.0")

        assert backup_path.exists()
        assert backup_path.suffix == ".zip"
        assert "pre-update-v0.1.0" in backup_path.name

        # Verify contents
        with zipfile.ZipFile(backup_path) as zf:
            names = zf.namelist()
            assert any("project.avc" in n for n in names)
            assert "system.json" in names

    def test_list_backups(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "pre-update-v0.1.0-20260101T000000Z.zip").write_bytes(b"fake")
        (backup_dir / "pre-update-v0.2.0-20260201T000000Z.zip").write_bytes(b"fake")

        backups = list_backups(tmp_path)
        assert len(backups) == 2
        # Newest first
        assert "v0.2.0" in backups[0]["name"]

    def test_cleanup_old_backups(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for i in range(7):
            (backup_dir / f"pre-update-v0.{i}.0-20260{i+1}01T000000Z.zip").write_bytes(b"fake")

        removed = cleanup_old_backups(tmp_path, keep=3)
        assert removed == 4
        remaining = list(backup_dir.glob("pre-update-*.zip"))
        assert len(remaining) == 3


# --- Rollback ---


class TestRollback:
    def test_pending_marker_lifecycle(self, tmp_path):
        # No marker initially
        assert read_pending_marker(tmp_path) is None

        # Write marker
        write_pending_marker(tmp_path, "0.1.0", "0.2.0")
        marker = read_pending_marker(tmp_path)
        assert marker is not None
        assert marker["from_version"] == "0.1.0"
        assert marker["to_version"] == "0.2.0"
        assert marker["attempts"] == 0

        # Increment
        count = increment_marker_attempts(tmp_path)
        assert count == 1

        # Clear
        clear_pending_marker(tmp_path)
        assert read_pending_marker(tmp_path) is None

    def test_check_rollback_first_attempt(self, tmp_path):
        write_pending_marker(tmp_path, "0.1.0", "0.2.0")
        # First check increments to 1, no rollback needed yet
        assert check_rollback_needed(tmp_path) is False

    def test_check_rollback_second_attempt(self, tmp_path):
        write_pending_marker(tmp_path, "0.1.0", "0.2.0")
        # Simulate first failed start
        increment_marker_attempts(tmp_path)
        # Second check (attempt 2) triggers rollback
        assert check_rollback_needed(tmp_path) is True

    def test_no_marker_no_rollback(self, tmp_path):
        assert check_rollback_needed(tmp_path) is False


# --- Update Manager ---


class TestUpdateManager:
    def test_get_status(self, tmp_path):
        mock_state = MagicMock()
        mock_state.get.side_effect = lambda key, default="": {
            "system.update_available": "",
            "system.update_status": "idle",
            "system.update_progress": 0,
            "system.update_error": "",
        }.get(key, default)

        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)
        status = mgr.get_status()

        assert status["current_version"] == "0.1.0"
        assert status["deployment_type"] == "git_dev"
        assert status["can_self_update"] is False

    def test_get_history_empty(self, tmp_path):
        mgr = UpdateManager(state_store=None, data_dir=tmp_path)
        assert mgr.get_history() == []

    async def test_check_for_updates_no_update(self, tmp_path):
        mock_state = MagicMock()
        mock_state.set = MagicMock()

        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)

        with patch.object(mgr._checker, "check", new_callable=AsyncMock, return_value=None):
            mgr._checker._last_check_error = ""
            result = await mgr.check_for_updates(channel="stable")

        assert result["update_available"] is False

    async def test_apply_without_check_fails(self, tmp_path):
        mgr = UpdateManager(state_store=None, data_dir=tmp_path)
        result = await mgr.apply_update()
        assert result["success"] is False
        assert "No update available" in result["error"]

    async def test_apply_on_git_dev_fails(self, tmp_path):
        mock_state = MagicMock()
        mock_state.set = MagicMock()

        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)
        # Simulate a found update
        mgr._checker._last_check_result = ReleaseInfo(
            version="1.0.0",
            tag="v1.0.0",
            prerelease=False,
            changelog="Big update",
            published_at="2026-06-01T00:00:00Z",
        )

        result = await mgr.apply_update()
        assert result["success"] is False
        assert "does not support self-update" in result["error"]
