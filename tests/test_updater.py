"""Tests for server.updater — update system components."""

import logging
import os
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from server.updater.checker import UpdateChecker, ReleaseInfo, parse_semver, is_newer, is_valid_semver
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
    can_rollback,
    _macos_previous_bundle,
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

    def test_is_valid_semver_distinguishes_miss_from_zero(self):
        # parse_semver returns (0,0,0,"") for both a real 0.0.0 and a regex miss;
        # is_valid_semver tells them apart.
        assert is_valid_semver("0.0.0") is True
        assert is_valid_semver("v1.2.3-rc.2") is True
        assert is_valid_semver("v0.13") is False
        assert is_valid_semver("not-a-version") is False


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

    def test_alphanumeric_prerelease_outranks_numeric(self):
        # Semver rule 11.4.3: a numeric identifier has LOWER precedence than an
        # alphanumeric one at the same position. Raw string compare got this wrong
        # ('-' < '9' in ASCII, so it reported the alphanumeric as older).
        assert is_newer("1.0.0--a", "1.0.0-9") is True
        assert is_newer("1.0.0-9", "1.0.0--a") is False

    def test_numeric_prerelease_identifiers_compare_numerically(self):
        # beta.10 > beta.9 even though "10" < "9" lexically.
        assert is_newer("1.0.0-beta.10", "1.0.0-beta.9") is True

    def test_longer_prerelease_outranks_shorter_when_prefix_equal(self):
        # Semver rule 11.4.4: rc.1 has higher precedence than rc.
        assert is_newer("1.0.0-rc.1", "1.0.0-rc") is True
        assert is_newer("1.0.0-rc", "1.0.0-rc.1") is False


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

    async def test_check_warns_on_unparseable_tag(self, checker, caplog):
        # An unparseable tag (v0.13, missing patch) must not silently hide the
        # valid newer release, and must be surfaced at WARNING.
        releases = [
            {
                "tag_name": "v0.13",
                "prerelease": False,
                "draft": False,
                "body": "Mistyped tag",
                "published_at": "2026-04-01T00:00:00Z",
                "assets": [],
            },
            {
                "tag_name": "v0.2.0",
                "prerelease": False,
                "draft": False,
                "body": "New features!",
                "published_at": "2026-03-01T00:00:00Z",
                "assets": [],
            },
        ]

        with patch("server.updater.checker.httpx.AsyncClient") as mock_client:
            mock_client.return_value = self._make_mock_client(releases)
            with caplog.at_level(logging.WARNING, logger="server.updater.checker"):
                result = await checker.check("stable")

        assert result is not None
        assert result.version == "0.2.0"
        assert any("v0.13" in r.message and r.levelno == logging.WARNING for r in caplog.records)

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
        assert can_self_update(DeploymentType.MACOS_APP) is True
        assert can_self_update(DeploymentType.ANDROID_APPLIANCE) is True

    def test_cannot_self_update_docker(self):
        assert can_self_update(DeploymentType.DOCKER) is False
        assert can_self_update(DeploymentType.GIT_DEV) is False

    def test_is_macos_app_detects_frozen_bundle(self, monkeypatch):
        import sys
        import server.updater.platform as platform_mod
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        inside = Path("/Applications/OpenAVC.app/Contents/MacOS/_internal")
        assert platform_mod._is_macos_app(inside) is True

    def test_is_macos_app_false_when_not_frozen(self, monkeypatch):
        import sys
        import server.updater.platform as platform_mod
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        # A source/dev run on macOS is not an .app install.
        inside = Path("/Applications/OpenAVC.app/Contents/MacOS/_internal")
        assert platform_mod._is_macos_app(inside) is False

    def test_is_macos_app_false_off_darwin(self, monkeypatch):
        import sys
        import server.updater.platform as platform_mod
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert platform_mod._is_macos_app(Path("/x/OpenAVC.app/y")) is False

    def test_detects_macos_app_end_to_end(self, tmp_path, monkeypatch):
        import sys
        import server.updater.platform as platform_mod
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(platform_mod, "_is_docker", lambda: False)
        monkeypatch.setattr(platform_mod, "_DEPLOYMENT_MARKER", tmp_path / "absent")
        app_dir = Path("/Applications/OpenAVC.app/Contents/MacOS/_internal")
        assert detect_deployment_type(app_dir) == DeploymentType.MACOS_APP

    def test_explicit_marker_wins(self, tmp_path, monkeypatch):
        """A provisioning-time /etc/openavc-deployment marker beats every
        filesystem heuristic."""
        import server.updater.platform as platform_mod
        marker = tmp_path / "openavc-deployment"
        marker.write_text("android_appliance\n")
        monkeypatch.setattr(platform_mod, "_DEPLOYMENT_MARKER", marker)
        app_dir = Path(__file__).resolve().parent.parent  # a git checkout
        assert detect_deployment_type(app_dir) == DeploymentType.ANDROID_APPLIANCE

    def test_invalid_marker_ignored(self, tmp_path, monkeypatch):
        import server.updater.platform as platform_mod
        marker = tmp_path / "openavc-deployment"
        marker.write_text("not-a-real-type")
        monkeypatch.setattr(platform_mod, "_DEPLOYMENT_MARKER", marker)
        app_dir = Path(__file__).resolve().parent.parent
        assert detect_deployment_type(app_dir) == DeploymentType.GIT_DEV

    def test_missing_marker_falls_through(self, tmp_path, monkeypatch):
        import server.updater.platform as platform_mod
        monkeypatch.setattr(
            platform_mod, "_DEPLOYMENT_MARKER", tmp_path / "absent"
        )
        app_dir = Path(__file__).resolve().parent.parent
        assert detect_deployment_type(app_dir) == DeploymentType.GIT_DEV

    def test_update_instructions_docker(self):
        msg = update_instructions(DeploymentType.DOCKER, "1.0.0")
        assert "docker compose pull" in msg

    def test_update_instructions_git(self):
        msg = update_instructions(DeploymentType.GIT_DEV, "1.0.0")
        assert "git pull" in msg


class TestMacosRollbackBundle:
    def test_previous_bundle_resolves_app_sibling(self):
        inside = Path("/Applications/OpenAVC.app/Contents/MacOS/_internal")
        assert _macos_previous_bundle(inside) == Path(
            "/Applications/OpenAVC.app.previous"
        )

    def test_previous_bundle_none_outside_app(self):
        assert _macos_previous_bundle(Path("/opt/openavc")) is None

    def test_can_rollback_macos_true_when_previous_exists(self, tmp_path, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "platform", "darwin")
        bundle = tmp_path / "OpenAVC.app"
        (bundle / "Contents" / "MacOS" / "_internal").mkdir(parents=True)
        (tmp_path / "OpenAVC.app.previous").mkdir()
        app_dir = bundle / "Contents" / "MacOS" / "_internal"
        assert can_rollback(app_dir) is True

    def test_can_rollback_macos_false_without_previous(self, tmp_path, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "platform", "darwin")
        bundle = tmp_path / "OpenAVC.app"
        app_dir = bundle / "Contents" / "MacOS" / "_internal"
        app_dir.mkdir(parents=True)
        assert can_rollback(app_dir) is False


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

    def test_create_backup_excludes_user_backups(self, tmp_path):
        # User backups live at {project_dir}/backups/ inside the projects
        # tree; embedding them re-compresses up to 15 nested backup zips per
        # update for zero restore value.
        projects_dir = tmp_path / "projects" / "default"
        projects_dir.mkdir(parents=True)
        (projects_dir / "project.avc").write_text('{"name": "test"}')
        user_backups = projects_dir / "backups"
        user_backups.mkdir()
        (user_backups / "backup-20260101T000000Z.zip").write_bytes(b"fake-zip")

        backup_path = create_backup(tmp_path, "0.1.0")

        with zipfile.ZipFile(backup_path) as zf:
            names = zf.namelist()
        assert "projects/default/project.avc" in names
        assert not any("backups/" in n for n in names)

    def test_list_backups(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        older = backup_dir / "pre-update-v0.1.0-20260101T000000Z.zip"
        newer = backup_dir / "pre-update-v0.2.0-20260201T000000Z.zip"
        older.write_bytes(b"fake")
        newer.write_bytes(b"fake")
        # Set explicit mtimes so the newest-first assertion is deterministic:
        # list_backups orders by mtime, and Windows' coarse mtime resolution can
        # otherwise tie two writes made in the same tick (flaky order).
        os.utime(older, (1_600_000_000, 1_600_000_000))
        os.utime(newer, (1_700_000_000, 1_700_000_000))

        backups = list_backups(tmp_path)
        assert len(backups) == 2
        # Newest first (by mtime)
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

    def test_list_backups_newest_first_across_version_bump(self, tmp_path):
        # v0.13.0 is chronologically newer than v0.9.0, but sorts *before* it
        # lexicographically ('1' < '9'). A filename sort would report the older
        # v0.9.0 as newest; an mtime sort must report v0.13.0 first.
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        old = backup_dir / "pre-update-v0.9.0-20260101T000000Z.zip"
        new = backup_dir / "pre-update-v0.13.0-20260601T000000Z.zip"
        old.write_bytes(b"fake")
        new.write_bytes(b"fake")
        os.utime(old, (1_600_000_000, 1_600_000_000))
        os.utime(new, (1_700_000_000, 1_700_000_000))

        backups = list_backups(tmp_path)
        assert "v0.13.0" in backups[0]["name"]

    def test_cleanup_keeps_chronologically_newest_across_version_bump(self, tmp_path):
        # keep=1 must retain the mtime-newest backup even when its version string
        # sorts lexicographically below an older one.
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        old = backup_dir / "pre-update-v0.9.0-20260101T000000Z.zip"
        new = backup_dir / "pre-update-v0.13.0-20260601T000000Z.zip"
        old.write_bytes(b"fake")
        new.write_bytes(b"fake")
        os.utime(old, (1_600_000_000, 1_600_000_000))
        os.utime(new, (1_700_000_000, 1_700_000_000))

        removed = cleanup_old_backups(tmp_path, keep=1)
        assert removed == 1
        assert new.exists()
        assert not old.exists()


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

        from server.version import __version__
        assert status["current_version"] == __version__
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

    async def test_apply_update_clears_marker_on_apply_failure(self, tmp_path):
        """A58: when the apply step raises, the pending-update marker must NOT
        survive on disk — otherwise the next manual restart triggers a false
        rollback against a from_version the user never moved away from.
        """
        mock_state = MagicMock()
        mock_state.set = MagicMock()
        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)

        # Force the manager to think it's a self-updating deployment.
        mgr._deployment_type = DeploymentType.LINUX_PACKAGE

        mgr._checker._last_check_result = ReleaseInfo(
            version="1.0.0",
            tag="v1.0.0",
            prerelease=False,
            changelog="",
            published_at="2026-06-01T00:00:00Z",
        )

        artifact = tmp_path / "openavc-1.0.0.tar.gz"
        artifact.write_bytes(b"fake")

        with patch(
            "server.updater.backup.create_backup",
            return_value=tmp_path / "backup.zip",
        ), patch(
            "server.updater.backup.cleanup_old_backups",
        ), patch.object(
            mgr, "_download_update", new_callable=AsyncMock, return_value=artifact
        ), patch.object(
            mgr, "_apply_linux", side_effect=OSError("disk full")
        ):
            result = await mgr.apply_update()

        assert result["success"] is False
        assert "disk full" in result["error"]
        # Marker must be cleared so a manual restart doesn't roll back stale.
        assert not (tmp_path / "pending-update").exists(), (
            "pending-update marker leaked after apply failure"
        )

    async def test_apply_cloud_update_clears_marker_on_apply_failure(self, tmp_path):
        """A58: same invariant for apply_cloud_update().

        Passes a valid checksum so execution reaches the apply step — the
        fail-closed verification (C4) rejects a missing checksum earlier, which
        would otherwise short-circuit this test before it exercises the marker
        cleanup on apply failure.
        """
        import hashlib

        mock_state = MagicMock()
        mock_state.set = MagicMock()
        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)

        mgr._deployment_type = DeploymentType.LINUX_PACKAGE

        artifact = tmp_path / "openavc-9.9.9.tar.gz"
        artifact.write_bytes(b"fake")
        good_checksum = hashlib.sha256(b"fake").hexdigest()

        with patch(
            "server.updater.backup.create_backup",
            return_value=tmp_path / "backup.zip",
        ), patch(
            "server.updater.backup.cleanup_old_backups",
        ), patch.object(
            mgr,
            "_download_artifact",
            new_callable=AsyncMock,
            return_value=artifact,
        ), patch.object(
            mgr, "_apply_linux", side_effect=OSError("read-only filesystem")
        ):
            result = await mgr.apply_cloud_update(
                target_version="9.9.9",
                update_url="https://example.com/openavc-9.9.9.tar.gz",
                checksum_sha256=good_checksum,
            )

        assert result["success"] is False
        assert not (tmp_path / "pending-update").exists(), (
            "pending-update marker leaked after cloud apply failure"
        )

    # --- A63: cloud staged update -----------------------------------------

    def test_stage_update_persists_to_disk(self, tmp_path):
        """stage_update writes target_version + update_url to staged-update.json."""
        mock_state = MagicMock()
        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)
        mgr.stage_update(
            "0.6.0", "https://example.com/x.tar.gz", checksum_sha256="abc"
        )
        staged = mgr.get_staged_update()
        assert staged is not None
        assert staged["target_version"] == "0.6.0"
        assert staged["update_url"] == "https://example.com/x.tar.gz"
        assert staged["checksum_sha256"] == "abc"
        mock_state.set.assert_any_call(
            "system.update_staged_version", "0.6.0", source="system"
        )

    def test_get_staged_update_returns_none_when_absent(self, tmp_path):
        mgr = UpdateManager(state_store=None, data_dir=tmp_path)
        assert mgr.get_staged_update() is None

    def test_get_staged_update_ignores_malformed_payload(self, tmp_path):
        mgr = UpdateManager(state_store=None, data_dir=tmp_path)
        (tmp_path / "staged-update.json").write_text("::: not json :::")
        assert mgr.get_staged_update() is None

    def test_clear_staged_update_removes_file(self, tmp_path):
        mock_state = MagicMock()
        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)
        mgr.stage_update("0.6.0", "https://example.com/x.tar.gz")
        assert (tmp_path / "staged-update.json").exists()
        mgr.clear_staged_update()
        assert not (tmp_path / "staged-update.json").exists()
        assert mgr.get_staged_update() is None

    async def test_apply_update_consumes_staged_update(self, tmp_path):
        """apply_update routes to apply_cloud_update with staged params and clears."""
        mock_state = MagicMock()
        mock_state.get = MagicMock(return_value="")
        mgr = UpdateManager(state_store=mock_state, data_dir=tmp_path)
        mgr.stage_update(
            "9.9.9", "https://example.com/openavc-9.9.9.tar.gz", "sha256hex"
        )

        captured: dict = {}

        async def fake_apply_cloud(target_version, update_url, checksum_sha256=None):
            captured["target_version"] = target_version
            captured["update_url"] = update_url
            captured["checksum_sha256"] = checksum_sha256
            return {"success": True, "message": "ok"}

        with patch.object(mgr, "apply_cloud_update", new=fake_apply_cloud):
            result = await mgr.apply_update()

        assert result["success"] is True
        assert captured["target_version"] == "9.9.9"
        assert captured["update_url"] == "https://example.com/openavc-9.9.9.tar.gz"
        assert captured["checksum_sha256"] == "sha256hex"
        # Staged marker is consumed.
        assert mgr.get_staged_update() is None
