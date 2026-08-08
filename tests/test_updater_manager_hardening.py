"""Regression tests for the update-manager hardening bug-fix batch.

Covers the maintenance-window / cloud-policy trust boundary, rollback bookkeeping,
history reconciliation, download/backup cleanup, and URL/filename sanitization
fixed in the bug-fix campaign (openavc/updater/manager.py + backup.py + rollback.py).
"""
from __future__ import annotations

import asyncio
import json
import zipfile
from datetime import time as dt_time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType


def _bare_manager(tmp_path, deployment=DeploymentType.LINUX_PACKAGE):
    """Build an UpdateManager without running __init__ (no disk/network at import)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    mgr = UpdateManager.__new__(UpdateManager)
    mgr._data_dir = data_dir
    mgr._project_path = data_dir / "project.avc"
    mgr._state = MagicMock()
    mgr._checker = MagicMock()
    mgr._update_in_progress = False
    mgr._history = []
    mgr._maintenance_task = None
    mgr._auto_check_task = None
    mgr._deployment_type = deployment
    return mgr


# ── M-012: maintenance-window time parsing rejects malformed/out-of-range ──

@pytest.mark.parametrize("value,expected", [
    ("02:30", dt_time(2, 30)),
    ("00:00", dt_time(0, 0)),
    ("23:59", dt_time(23, 59)),
    ("25:99", None),     # out of range — must not raise out of dt_time()
    ("2:3:4", None),     # wrong shape
    ("notatime", None),
    ("", None),
    (None, None),
    (230, None),         # non-string
])
def test_parse_window_time(value, expected):
    assert UpdateManager._parse_window_time(value) == expected


# ── H-009: a bad/hostile timezone resolves to None (UTC), never raises ──

@pytest.mark.parametrize("tz", ["", "Not/AZone", "\x00", "x" * 5000, None, 123])
def test_resolve_tz_bad_values(tz):
    assert UpdateManager._resolve_tz(tz) is None


def test_resolve_tz_valid():
    tzinfo = UpdateManager._resolve_tz("America/New_York")
    assert tzinfo is not None
    assert getattr(tzinfo, "key", None) == "America/New_York"


# ── H-008: the maintenance window enforces its end (not just the start) ──

def test_in_window_same_day(tmp_path):
    mgr = _bare_manager(tmp_path)
    start, end = dt_time(2, 0), dt_time(3, 0)
    with patch("openavc.updater.manager.datetime") as mock_dt:
        # 02:30 UTC -> inside
        mock_dt.now.return_value = _utc(2, 30)
        assert mgr._in_window(start, end, None) is True
        # 04:00 UTC -> past the end, outside (the old code returned True here)
        mock_dt.now.return_value = _utc(4, 0)
        assert mgr._in_window(start, end, None) is False
        # 01:00 UTC -> before the start
        mock_dt.now.return_value = _utc(1, 0)
        assert mgr._in_window(start, end, None) is False


def test_in_window_overnight(tmp_path):
    mgr = _bare_manager(tmp_path)
    start, end = dt_time(23, 0), dt_time(2, 0)
    with patch("openavc.updater.manager.datetime") as mock_dt:
        mock_dt.now.return_value = _utc(23, 30)
        assert mgr._in_window(start, end, None) is True   # after start
        mock_dt.now.return_value = _utc(1, 0)
        assert mgr._in_window(start, end, None) is True   # before end, next day
        mock_dt.now.return_value = _utc(12, 0)
        assert mgr._in_window(start, end, None) is False  # midday, outside


def _utc(hour, minute):
    from datetime import datetime, timezone
    d = datetime(2026, 6, 1, hour, minute, tzinfo=timezone.utc)
    return d


# ── H-014 / M-013: cloud policy is validated; reconfigure tears down cleanly ──

@pytest.mark.asyncio
async def test_apply_policy_garbage_does_not_schedule(tmp_path):
    mgr = _bare_manager(tmp_path)
    with patch("openavc.updater.manager.can_self_update", return_value=True):
        # unknown policy -> manual, no task
        await mgr.apply_update_policy({"policy": "destroy_everything"})
        assert mgr._maintenance_task is None
        # auto but malformed window -> no task, no crash
        await mgr.apply_update_policy({
            "policy": "auto",
            "maintenance_window_start": "99:99",
            "maintenance_window_end": "nope",
            "maintenance_window_tz": "Bad/Zone",
        })
        assert mgr._maintenance_task is None
        # not a dict-ish payload at all
        await mgr.apply_update_policy({})
        assert mgr._maintenance_task is None


@pytest.mark.asyncio
async def test_apply_policy_valid_schedules_one_task_and_reconfigures(tmp_path):
    mgr = _bare_manager(tmp_path)

    async def _never(*_a, **_k):
        await asyncio.sleep(3600)
    mgr._maintenance_window_loop = _never

    with patch("openavc.updater.manager.can_self_update", return_value=True):
        policy = {
            "policy": "auto",
            "maintenance_window_start": "02:00",
            "maintenance_window_end": "03:00",
        }
        await mgr.apply_update_policy(policy)
        task1 = mgr._maintenance_task
        assert task1 is not None and not task1.done()

        # Reconfiguring must cancel + await the old loop, leaving exactly one.
        await mgr.apply_update_policy(policy)
        task2 = mgr._maintenance_task
        assert task1.done()          # old loop torn down (M-013)
        assert task2 is not task1 and not task2.done()

        # Switching to manual cancels it entirely.
        await mgr.apply_update_policy({"policy": "manual"})
        assert mgr._maintenance_task is None
        assert task2.done()


# ── H-013 / L-010: history reconciliation ──

def _write_history(mgr, entry):
    (mgr._data_dir / "update-history.json").write_text(json.dumps([entry]), encoding="utf-8")


def test_history_marks_success_despite_tag_pyproject_skew(tmp_path):
    mgr = _bare_manager(tmp_path)
    # Pending update FROM 0.13.0 TO release tag 0.15.0; running pyproject is 0.14.0.
    _write_history(mgr, {"from_version": "0.13.0", "to_version": "0.15.0", "status": "pending"})
    with patch("openavc.updater.manager.__version__", "0.14.0"):
        mgr._load_history()
    assert mgr._history[0]["status"] == "success"  # version changed -> applied (H-013)


def test_history_marks_failed_when_version_unchanged(tmp_path):
    mgr = _bare_manager(tmp_path)
    _write_history(mgr, {"from_version": "0.13.0", "to_version": "0.14.0", "status": "pending"})
    with patch("openavc.updater.manager.__version__", "0.13.0"):
        mgr._load_history()
    assert mgr._history[0]["status"] == "failed"  # still on from_version -> really failed


def test_history_corrupt_file_logs_and_resets(tmp_path, caplog):
    mgr = _bare_manager(tmp_path)
    (mgr._data_dir / "update-history.json").write_text("{not json", encoding="utf-8")
    import logging
    with caplog.at_level(logging.WARNING):
        mgr._load_history()
    assert mgr._history == []
    assert any("update history" in r.message for r in caplog.records)  # L-010: not silent


# ── H-010 / L-015: rollback clears the staged record and rotates backups ──

@pytest.mark.asyncio
async def test_rollback_clears_staged_update(tmp_path):
    mgr = _bare_manager(tmp_path)
    mgr.stage_update("9.9.9", "https://x/u.tar.gz", "deadbeef")
    assert mgr.get_staged_update() is not None

    cleanup = MagicMock()
    with patch("openavc.updater.rollback.can_rollback", return_value=True), \
         patch("openavc.updater.rollback.perform_rollback", return_value=True), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups", cleanup), \
         patch("openavc.system_config.APP_DIR", tmp_path), \
         patch.object(mgr, "_restart_process"), \
         patch.object(mgr, "_save_history"):
        result = await mgr.rollback()

    assert result["success"] is True
    assert mgr.get_staged_update() is None      # H-010
    cleanup.assert_called_once()                # L-015: pre-rollback backup rotated


# ── H-011: rollback_version reflects the real target, not history ──

def test_get_status_rollback_version_from_real_target(tmp_path):
    mgr = _bare_manager(tmp_path)
    # A prior rollback success entry would mislead a history-based derivation.
    mgr._history = [{"from_version": "0.14.0", "to_version": "rollback", "status": "success"}]
    with patch("openavc.updater.manager.can_self_update", return_value=True), \
         patch("openavc.updater.rollback.can_rollback", return_value=True), \
         patch("openavc.updater.rollback.rollback_target_version", return_value="0.12.0") as rtv, \
         patch("openavc.system_config.APP_DIR", tmp_path):
        status = mgr.get_status()
    rtv.assert_called_once()
    assert status["rollback_version"] == "0.12.0"  # not "0.14.0" from history


# ── H-012 / L-013: failed/cancelled apply cleans markers + records failure ──

def test_cleanup_failed_apply_clears_linux_instruction(tmp_path):
    mgr = _bare_manager(tmp_path)
    (mgr._data_dir / "apply-update.json").write_text("{}", encoding="utf-8")
    with patch("openavc.updater.rollback.clear_pending_marker"), \
         patch("openavc.updater.manager.__version__", "0.13.0"), \
         patch.object(mgr, "_save_history"):
        mgr._cleanup_failed_apply("0.14.0", "boom")
    assert not (mgr._data_dir / "apply-update.json").exists()      # H-012
    assert mgr._history[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_cancelled_apply_records_failed_and_propagates(tmp_path):
    mgr = _bare_manager(tmp_path)
    mgr._checker.last_result = MagicMock(version="1.0.0")

    async def _cancel(_release):
        raise asyncio.CancelledError()
    mgr._download_update = _cancel

    with patch("openavc.updater.manager.can_self_update", return_value=True), \
         patch("openavc.updater.manager.__version__", "0.9.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.updater.rollback.clear_pending_marker"), \
         patch.object(mgr, "_save_history"):
        with pytest.raises(asyncio.CancelledError):
            await mgr.apply_update()

    assert mgr._update_in_progress is False                 # finally ran
    assert mgr._history and mgr._history[0]["status"] == "failed"  # L-013


# ── L-011: a transient cloud apply failure keeps the staged record ──

@pytest.mark.asyncio
async def test_staged_update_kept_on_apply_failure(tmp_path):
    mgr = _bare_manager(tmp_path)
    mgr.stage_update("2.0.0", "https://x/u.tar.gz", "abc123")

    async def _fail(*_a, **_k):
        return {"success": False, "error": "network blip"}
    mgr.apply_cloud_update = _fail
    with patch("openavc.updater.manager.can_self_update", return_value=True):
        result = await mgr.apply_update()
    assert result["success"] is False
    assert mgr.get_staged_update() is not None   # retained for retry (L-011)


# ── L-012: a mid-stream download error removes the partial artifact ──

@pytest.mark.asyncio
async def test_download_removes_partial_on_error(tmp_path, monkeypatch):
    mgr = _bare_manager(tmp_path)

    class _FailStream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def raise_for_status(self):
            pass
        headers = {"content-length": "1000"}
        async def aiter_bytes(self, chunk_size=0):
            yield b"partial"
            raise OSError("connection reset")

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def stream(self, method, url):
            return _FailStream()

    monkeypatch.setattr("openavc.updater.manager.httpx.AsyncClient", _Client)
    with pytest.raises(OSError):
        await mgr._download_artifact("https://x/a.tar.gz", "a.tar.gz")
    assert not (mgr._data_dir / "update-cache" / "a.tar.gz").exists()  # L-012


# ── L-014: a mid-write backup failure leaves no countable .zip ──

def test_backup_atomic_on_write_failure(tmp_path, monkeypatch):
    from openavc.updater import backup
    data_dir = tmp_path / "data"
    (data_dir / "projects").mkdir(parents=True)
    (data_dir / "projects" / "p.avc").write_text("{}", encoding="utf-8")

    def boom(self, *a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(zipfile.ZipFile, "write", boom)

    with pytest.raises(OSError):
        backup.create_backup(data_dir, "1.0.0")
    backups = list((data_dir / "backups").glob("pre-update-*.zip"))
    tmps = list((data_dir / "backups").glob("*.tmp"))
    assert backups == []  # no truncated archive counts as a restore slot (L-014)
    assert tmps == []     # temp cleaned up


# ── M-014: a concurrent check doesn't clobber an in-flight apply's status ──

@pytest.mark.asyncio
async def test_check_does_not_touch_status_during_apply(tmp_path):
    mgr = _bare_manager(tmp_path)
    mgr._update_in_progress = True
    mgr._checker.check = AsyncMock(return_value=None)
    mgr._checker.current_version = "1.0.0"
    mgr._checker.last_error = "some error"
    sets = []
    mgr._state.set = lambda k, v, source="system": sets.append((k, v))

    with patch("openavc.system_config.get_system_config"):
        await mgr.check_for_updates(channel="stable")

    keys = {k for k, _ in sets}
    assert "system.update_status" not in keys  # M-014: apply's status preserved
    assert "system.update_error" not in keys


# ── M-015: a string/zero auto-check interval is coerced, not fatal ──

@pytest.mark.asyncio
async def test_auto_check_interval_coerced(tmp_path):
    mgr = _bare_manager(tmp_path)
    captured = {}

    def _capture(coro):
        # Don't actually run the periodic loop; just confirm scheduling happened.
        coro.close()
        captured["scheduled"] = True
        return MagicMock(done=lambda: False)

    cfg = MagicMock()
    cfg.get.side_effect = lambda section, key, default=None: {
        ("updates", "check_enabled"): True,
        ("updates", "auto_check_interval_hours"): "not-a-number",
    }.get((section, key), default)

    with patch("openavc.system_config.get_system_config", return_value=cfg), \
         patch("openavc.updater.manager.asyncio.create_task", _capture):
        await mgr.start_auto_check()
    assert captured.get("scheduled") is True  # didn't raise on the bad interval (M-015)


# ── M-016: a non-HTTPS cloud update URL is refused before download ──

@pytest.mark.asyncio
async def test_apply_cloud_update_rejects_non_https(tmp_path):
    mgr = _bare_manager(tmp_path)
    with patch("openavc.updater.manager.can_self_update", return_value=True):
        result = await mgr.apply_cloud_update("1.0.0", "http://evil/u.tar.gz", "abc")
    assert result["success"] is False
    assert "HTTPS" in result["error"] or "https" in result["error"]


# ── M-017: URL-derived filenames are sanitized ──

@pytest.mark.parametrize("url_path,version,deployment,expected", [
    ("/releases/openavc-1.0.0-linux-amd64.tar.gz", "1.0.0", DeploymentType.LINUX_PACKAGE,
     "openavc-1.0.0-linux-amd64.tar.gz"),
    ("/a/b/../../etc/passwd", "1.0.0", DeploymentType.LINUX_PACKAGE, "passwd"),  # basename stripped, plain token
    ("/x/..", "1.0.0", DeploymentType.LINUX_PACKAGE, "update-1.0.0.tar.gz"),    # ".." rejected
    ("/weird name;rm -rf", "1.0.0", DeploymentType.LINUX_PACKAGE, "update-1.0.0.tar.gz"),
    ("", "2.0.0-rc.1", DeploymentType.WINDOWS_INSTALLER, "update-2.0.0-rc.1.exe"),
    ("/x", "../../evil", DeploymentType.WINDOWS_INSTALLER, "x"),  # base ok; version only used in fallback
])
def test_safe_artifact_filename(tmp_path, url_path, version, deployment, expected):
    mgr = _bare_manager(tmp_path, deployment=deployment)
    assert mgr._safe_artifact_filename(url_path, version) == expected


# ── Cloud-staged update surfaced: get_status field + state re-seed on boot ──

def test_get_status_includes_staged_version(tmp_path):
    mgr = _bare_manager(tmp_path)
    with patch("openavc.updater.manager.can_self_update", return_value=True), \
         patch("openavc.updater.rollback.can_rollback", return_value=False), \
         patch("openavc.system_config.APP_DIR", tmp_path):
        assert mgr.get_status()["staged_version"] == ""

    mgr.stage_update("9.9.9", "https://x/u.tar.gz", "deadbeef")
    with patch("openavc.updater.manager.can_self_update", return_value=True), \
         patch("openavc.updater.rollback.can_rollback", return_value=False), \
         patch("openavc.system_config.APP_DIR", tmp_path):
        assert mgr.get_status()["staged_version"] == "9.9.9"


def test_init_reseeds_staged_state_key(tmp_path):
    """A staged update persists on disk; the state key must come back after
    a server restart or the IDE loses sight of the staged update."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "staged-update.json").write_text(
        json.dumps({
            "target_version": "9.9.9",
            "update_url": "https://x/u.tar.gz",
            "checksum_sha256": "deadbeef",
        }),
        encoding="utf-8",
    )
    state = MagicMock()
    UpdateManager(
        state_store=state, data_dir=data_dir, project_path=data_dir / "p.avc",
    )
    state.set.assert_any_call("system.update_staged_version", "9.9.9", source="system")


def test_init_without_staged_file_does_not_touch_key(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state = MagicMock()
    UpdateManager(
        state_store=state, data_dir=data_dir, project_path=data_dir / "p.avc",
    )
    staged_calls = [
        c for c in state.set.call_args_list
        if c.args and c.args[0] == "system.update_staged_version"
    ]
    assert staged_calls == []


# ── Rollback history records the real target version + rollback flag ──

@pytest.mark.asyncio
async def test_rollback_history_records_target_and_flag(tmp_path):
    mgr = _bare_manager(tmp_path)
    with patch("openavc.updater.rollback.can_rollback", return_value=True), \
         patch("openavc.updater.rollback.perform_rollback", return_value=True), \
         patch("openavc.updater.rollback.rollback_target_version", return_value="0.12.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups", MagicMock()), \
         patch("openavc.system_config.APP_DIR", tmp_path), \
         patch("openavc.updater.manager.__version__", "0.13.0"), \
         patch.object(mgr, "_restart_process"), \
         patch.object(mgr, "_save_history"):
        result = await mgr.rollback()

    assert result["success"] is True
    entry = mgr._history[0]
    assert entry["rollback"] is True
    assert entry["from_version"] == "0.13.0"
    assert entry["to_version"] == "0.12.0"  # the real target, not "rollback"


def test_update_history_entries_record_rollback_false(tmp_path):
    mgr = _bare_manager(tmp_path)
    with patch.object(mgr, "_save_history"):
        mgr._add_history_entry("0.13.0", "0.14.0", "pending")
    assert mgr._history[0]["rollback"] is False
