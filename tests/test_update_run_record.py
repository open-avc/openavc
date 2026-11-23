"""The update run record (updater/update_run.py): the newest update or rollback,
kept in update-run.json and written where the staged record, the history row
and the in-progress flag are written.

Two halves. The next start reads back what the released updater leaves on disk
(tests/fixtures/update_records_released.json: a staged record and each newest
history row it writes), and what this version writes. And each way an update
or rollback goes writes the run: the target, where it came from, its step and
its end agree with the staged record and the history row written beside it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openavc.updater import update_run
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType

SHAPES = json.loads(
    (Path(__file__).parent / "fixtures" / "update_records_released.json").read_text(encoding="utf-8")
)
ROWS = SHAPES["rows"]

# An installed system, whose service starts the version an update installs.
pytestmark = pytest.mark.usefixtures("under_its_service")
URL = "https://github.com/open-avc/openavc/releases/download/v0.42.0/openavc-0.42.0-linux-x86_64.tar.gz"


def _write(data_dir: Path, name: str, data) -> None:
    (data_dir / name).write_text(json.dumps(data), encoding="utf-8")


def _start(data_dir: Path, running: str) -> UpdateManager:
    """An UpdateManager made the way a server start makes it, on ``running``."""
    with patch("openavc.updater.manager.__version__", running):
        mgr = UpdateManager(state_store=None, data_dir=data_dir)
    mgr._deployment_type = DeploymentType.LINUX_PACKAGE
    return mgr


def _run_file(data_dir: Path) -> dict | None:
    path = data_dir / update_run.RUN_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _history(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "update-history.json").read_text(encoding="utf-8"))


# ── What the released updater leaves, read back by the next start ──


def test_a_staged_record_is_the_run_waiting(tmp_path):
    _write(tmp_path, "staged-update.json", SHAPES["staged"])
    run = _start(tmp_path, "0.41.0-rc1").run_record.run

    assert (run.target, run.source, run.step, run.end) == ("0.41.0", "staged", "waiting", "pending")
    assert run.update_url == SHAPES["staged"]["update_url"]
    assert run.checksum_sha256 == SHAPES["staged"]["checksum_sha256"]
    assert run.rollback is False
    assert _run_file(tmp_path)["target"] == "0.41.0"


def test_a_staged_record_without_a_checksum_waits_without_one(tmp_path):
    _write(tmp_path, "staged-update.json", SHAPES["staged_without_checksum"])
    run = _start(tmp_path, "0.41.0-rc1").run_record.run
    assert run.step == "waiting" and run.checksum_sha256 is None


@pytest.mark.parametrize(
    ("row", "running", "ended"),
    [
        ("pending", "0.41.0", ("success", "")),
        ("pending", "0.41.0-rc1", ("failed", "Update did not apply: expected v0.41.0, running v0.41.0-rc1")),
        ("failed", "0.41.0-rc1", ("failed", ROWS["failed"]["error"])),
        ("cancelled", "0.41.0-rc1", ("failed", "Update cancelled")),
        ("confirmed", "0.41.0", ("success", "")),
        ("confirmed_by_0_40_0", "0.40.0", ("success", "")),
        ("did_not_apply", "0.41.0-rc1", ("failed", ROWS["did_not_apply"]["error"])),
        ("reverted", "0.41.0-rc1", ("rolled_back", ROWS["reverted"]["error"])),
    ],
)
def test_the_newest_history_row_is_the_last_run(tmp_path, row, running, ended):
    end, error = ended
    _write(tmp_path, "update-history.json", [ROWS[row], ROWS["confirmed_by_0_40_0"]])
    run = _start(tmp_path, running).run_record.run

    assert (run.from_version, run.target) == (ROWS[row]["from_version"], ROWS[row]["to_version"])
    assert (run.source, run.step, run.end, run.error) == ("unrecorded", "", end, error)
    assert run.started == ROWS[row]["timestamp"]
    # The end is the history's, and the file says the same from here on.
    assert _history(tmp_path)[0]["status"] == end
    assert _run_file(tmp_path)["end"] == end


def test_a_manual_rollback_row_is_a_rollback_run(tmp_path):
    _write(tmp_path, "update-history.json", [ROWS["manual_rollback"]])
    run = _start(tmp_path, "0.40.0").run_record.run
    assert (run.target, run.from_version, run.rollback, run.end) == ("0.40.0", "0.41.0-rc1", True, "success")


def test_a_staged_record_waits_in_place_of_the_last_run(tmp_path):
    _write(tmp_path, "update-history.json", [ROWS["confirmed_by_0_40_0"]])
    _write(tmp_path, "staged-update.json", SHAPES["staged"])
    run = _start(tmp_path, "0.40.0").run_record.run
    assert (run.target, run.step) == ("0.41.0", "waiting")


def test_with_nothing_on_disk_there_is_no_run_and_no_file(tmp_path):
    assert _start(tmp_path, "0.41.0").run_record.run is None
    assert not (tmp_path / update_run.RUN_FILE).exists()


@pytest.mark.parametrize(
    "content", [b"{", b"[]", b'{"format": 99, "target": "9.0.0"}', b'{"format": 1}', b"\xff\xfe{"],
)
def test_a_run_file_this_version_cannot_read_gives_way_to_the_history(tmp_path, content):
    (tmp_path / update_run.RUN_FILE).write_bytes(content)
    _write(tmp_path, "update-history.json", [ROWS["confirmed"]])
    run = _start(tmp_path, "0.41.0").run_record.run
    assert (run.target, run.end) == ("0.41.0", "success")
    assert _run_file(tmp_path)["format"] == update_run.FORMAT


# ── Each way an update goes writes the run ──


def _artifact_download(content: bytes):
    async def fake(url, filename, data_dir):
        path = data_dir / "update-cache" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path
    return fake


async def _apply_cloud(mgr: UpdateManager, tmp_path: Path, checksum: str | None = None):
    content = b"release"
    digest = checksum or hashlib.sha256(content).hexdigest()
    fake = _artifact_download(content)

    async def download(url, filename):
        return await fake(url, filename, mgr._data_dir)

    with patch("openavc.updater.manager.__version__", "0.41.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.updater.rollback.write_pending_marker"), \
         patch.object(mgr, "_download_artifact", side_effect=download), \
         patch.object(mgr, "_download_sidecar_sig"), \
         patch.object(mgr, "_end_process"):
        return await mgr.apply_cloud_update("0.42.0", URL, digest)


@pytest.mark.asyncio
async def test_a_pushed_update_runs_to_its_exit(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    result = await _apply_cloud(mgr, tmp_path)

    assert result["success"] is True
    run = _run_file(tmp_path)
    assert (run["target"], run["from_version"], run["source"]) == ("0.42.0", "0.41.0", "pushed")
    assert (run["step"], run["end"], run["checksum_sha256"]) == ("restarting", "pending", hashlib.sha256(b"release").hexdigest())
    row = _history(tmp_path)[0]
    assert (row["from_version"], row["to_version"], row["status"]) == ("0.41.0", "0.42.0", "pending")


@pytest.mark.asyncio
async def test_a_failed_update_ends_failed_with_the_history_s_sentence(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    result = await _apply_cloud(mgr, tmp_path, checksum="00" * 32)

    assert result["success"] is False
    run = _run_file(tmp_path)
    assert (run["step"], run["end"]) == ("verifying", "failed")
    assert run["error"] == result["error"] == _history(tmp_path)[0]["error"]


@pytest.mark.asyncio
async def test_a_cancelled_update_ends_failed(tmp_path):
    mgr = _start(tmp_path, "0.41.0")

    async def cancelled(url, filename):
        raise asyncio.CancelledError

    with patch("openavc.updater.manager.__version__", "0.41.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch.object(mgr, "_download_artifact", side_effect=cancelled), \
         pytest.raises(asyncio.CancelledError):
        await mgr.apply_cloud_update("0.42.0", URL, "ab" * 32)

    run = _run_file(tmp_path)
    assert (run["step"], run["end"], run["error"]) == ("downloading", "failed", "Update cancelled")


@pytest.mark.asyncio
async def test_a_checked_release_runs_as_checked(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    mgr._checker = MagicMock(last_result=MagicMock(version="0.42.0"))
    with patch("openavc.updater.manager.__version__", "0.41.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.updater.rollback.write_pending_marker"), \
         patch.object(mgr, "_download_update", return_value=tmp_path / "a.tar.gz"), \
         patch.object(mgr, "_end_process"):
        result = await mgr.apply_update()

    assert result["success"] is True
    run = _run_file(tmp_path)
    assert (run["target"], run["source"], run["step"], run["end"]) == ("0.42.0", "checked", "restarting", "pending")


def test_staging_writes_the_run_waiting_and_clearing_drops_it(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    with patch("openavc.updater.manager.__version__", "0.41.0"):
        mgr.stage_update("0.42.0", URL, "ab" * 32, rollback=False)
        run = _run_file(tmp_path)
        assert (run["target"], run["source"], run["step"], run["update_url"]) == ("0.42.0", "staged", "waiting", URL)
        assert mgr.get_staged_update()["target_version"] == run["target"]

        mgr.clear_staged_update()
    assert _run_file(tmp_path) is None


def test_a_staged_record_dropped_as_no_longer_newer_drops_the_run(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    with patch("openavc.updater.manager.__version__", "0.41.0"):
        mgr.stage_update("0.42.0", URL, "ab" * 32)
    with patch("openavc.updater.manager.__version__", "0.42.0"):
        assert mgr.get_staged_update() is None
    assert _run_file(tmp_path) is None


@pytest.mark.asyncio
async def test_installing_the_staged_target_is_the_staged_run_going_ahead(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    digest = hashlib.sha256(b"release").hexdigest()
    with patch("openavc.updater.manager.__version__", "0.41.0"):
        mgr.stage_update("0.42.0", URL, digest)
    content = _artifact_download(b"release")

    async def download(url, filename):
        return await content(url, filename, mgr._data_dir)

    with patch("openavc.updater.manager.__version__", "0.41.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.updater.rollback.write_pending_marker"), \
         patch.object(mgr, "_download_artifact", side_effect=download), \
         patch.object(mgr, "_download_sidecar_sig"), \
         patch.object(mgr, "_end_process"):
        result = await mgr.apply_update()

    assert result["success"] is True
    assert not (tmp_path / "staged-update.json").exists()
    run = _run_file(tmp_path)
    assert (run["target"], run["source"], run["step"], run["from_version"]) == ("0.42.0", "staged", "restarting", "0.41.0")


@pytest.mark.asyncio
async def test_a_second_try_at_the_staged_target_is_still_the_staged_run(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    with patch("openavc.updater.manager.__version__", "0.41.0"):
        mgr.stage_update("0.42.0", URL, "ab" * 32)

    async def blip(url, filename):
        raise RuntimeError("network blip")

    for _ in range(2):
        with patch("openavc.updater.manager.__version__", "0.41.0"), \
             patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
             patch("openavc.updater.backup.cleanup_old_backups"), \
             patch.object(mgr, "_download_artifact", side_effect=blip):
            assert (await mgr.apply_update())["success"] is False
        run = _run_file(tmp_path)
        assert (run["source"], run["end"]) == ("staged", "failed")

    # The same files read back the same way at the next start.
    assert _start(tmp_path, "0.41.0").run_record.run.source == "staged"


@pytest.mark.asyncio
async def test_staging_while_an_update_runs_leaves_the_run_in_flight(tmp_path):
    mgr = _start(tmp_path, "0.41.0")
    staged_midway = {}

    async def download(url, filename):
        with patch("openavc.updater.manager.__version__", "0.41.0"):
            mgr.stage_update("0.43.0", URL, "ab" * 32)
        staged_midway.update(_run_file(tmp_path))
        raise RuntimeError("network blip")

    with patch("openavc.updater.manager.__version__", "0.41.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch.object(mgr, "_download_artifact", side_effect=download):
        await mgr.apply_cloud_update("0.42.0", URL, "ab" * 32)

    assert (staged_midway["target"], staged_midway["step"]) == ("0.42.0", "downloading")
    assert (_run_file(tmp_path)["target"], _run_file(tmp_path)["end"]) == ("0.42.0", "failed")


@pytest.mark.asyncio
async def test_a_push_that_applies_drops_the_staged_target(tmp_path):
    """A staged target is gone once another update applies, a newer one too,
    so the next start reads back the push, ended as its history row."""
    mgr = _start(tmp_path, "0.41.0")
    with patch("openavc.updater.manager.__version__", "0.41.0"):
        mgr.stage_update("0.43.0", URL, "ab" * 32)
    await _apply_cloud(mgr, tmp_path)
    assert (_run_file(tmp_path)["target"], _run_file(tmp_path)["source"]) == ("0.42.0", "pushed")
    assert not (tmp_path / "staged-update.json").exists()

    run = _start(tmp_path, "0.42.0").run_record.run
    assert (run.target, run.source, run.step, run.end) == ("0.42.0", "pushed", "restarting", "success")
    assert (_history(tmp_path)[0]["to_version"], _history(tmp_path)[0]["status"]) == ("0.42.0", "success")


async def _roll_back(mgr: UpdateManager, tmp_path: Path, *, works: bool):
    with patch("openavc.updater.rollback.can_rollback", return_value=True), \
         patch("openavc.updater.rollback.perform_rollback", return_value=works), \
         patch("openavc.updater.rollback.rollback_target_version", return_value="0.41.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.system_config.APP_DIR", tmp_path), \
         patch("openavc.updater.manager.__version__", "0.42.0"), \
         patch.object(mgr, "_end_process"):
        return await mgr.rollback()


@pytest.mark.asyncio
async def test_a_rollback_runs_to_the_previous_release(tmp_path):
    mgr = _start(tmp_path, "0.42.0")
    assert (await _roll_back(mgr, tmp_path, works=True))["success"] is True

    run = _run_file(tmp_path)
    assert (run["target"], run["from_version"], run["source"], run["rollback"]) == ("0.41.0", "0.42.0", "previous", True)
    assert (run["step"], run["end"]) == ("restarting", "pending")


@pytest.mark.asyncio
async def test_a_rollback_that_fails_ends_failed(tmp_path):
    mgr = _start(tmp_path, "0.42.0")
    assert (await _roll_back(mgr, tmp_path, works=False))["success"] is False

    run = _run_file(tmp_path)
    assert (run["source"], run["step"], run["end"], run["error"]) == ("previous", "applying", "failed", "Rollback failed")


# ── What this version writes, read back by the next start ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "end"), [("0.42.0", "success"), ("0.41.0", "failed")],
)
async def test_the_next_start_ends_the_run_as_the_history_ends_its_row(tmp_path, running, end):
    await _apply_cloud(_start(tmp_path, "0.41.0"), tmp_path)

    run = _start(tmp_path, running).run_record.run
    assert (run.source, run.step, run.end) == ("pushed", "restarting", end)
    assert run.error == _history(tmp_path)[0]["error"]
    assert _run_file(tmp_path)["end"] == end


@pytest.mark.asyncio
async def test_a_failed_rollback_is_still_the_last_run_at_the_next_start(tmp_path):
    _write(tmp_path, "update-history.json", [ROWS["confirmed"]])
    await _roll_back(_start(tmp_path, "0.42.0"), tmp_path, works=False)

    run = _start(tmp_path, "0.42.0").run_record.run
    assert (run.source, run.end, run.error) == ("previous", "failed", "Rollback failed")


def test_a_staged_run_still_waits_at_the_next_start(tmp_path):
    with patch("openavc.updater.manager.__version__", "0.41.0"):
        _start(tmp_path, "0.41.0").stage_update("0.42.0", URL, "ab" * 32, rollback=True)
    written = _run_file(tmp_path)

    run = _start(tmp_path, "0.41.0").run_record.run
    assert (run.step, run.rollback, run.started) == ("waiting", True, written["started"])


@pytest.mark.asyncio
async def test_a_run_an_earlier_release_ran_after_this_one_is_read_from_its_row(tmp_path):
    """Rolled back to a release that writes no run file, which then updated:
    its newer row is the last run, not the file this version left."""
    await _roll_back(_start(tmp_path, "0.42.0"), tmp_path, works=True)
    later = dict(ROWS["pending"], from_version="0.41.0", to_version="0.42.0", timestamp="2099-01-01T00:00:00+00:00")
    _write(tmp_path, "update-history.json", [later, *_history(tmp_path)])

    run = _start(tmp_path, "0.42.0").run_record.run
    assert (run.source, run.from_version, run.target, run.end) == ("unrecorded", "0.41.0", "0.42.0", "success")


def test_a_waiting_run_whose_staged_record_is_gone_gives_way_to_the_history(tmp_path):
    with patch("openavc.updater.manager.__version__", "0.41.0"):
        _start(tmp_path, "0.41.0").stage_update("0.42.0", URL, "ab" * 32)
    (tmp_path / "staged-update.json").unlink()
    _write(tmp_path, "update-history.json", [dict(ROWS["pending"], from_version="0.41.0", to_version="0.42.0")])

    run = _start(tmp_path, "0.42.0").run_record.run
    assert (run.source, run.target, run.end) == ("unrecorded", "0.42.0", "success")


def test_a_system_whose_firmware_owns_updates_keeps_no_run(tmp_path, monkeypatch):
    from openavc.updater.host import get_firmware_host

    host = tmp_path / "firmware-host.json"
    host.write_text(json.dumps({"schema": 1, "update_owner": "firmware", "firmware_version": "fixture-1.2"}))
    monkeypatch.setenv("OPENAVC_FIRMWARE_HOST", str(host))
    get_firmware_host.cache_clear()
    try:
        _write(tmp_path, "staged-update.json", SHAPES["staged"])
        assert _start(tmp_path, "0.41.0-rc1").run_record.run is None
    finally:
        get_firmware_host.cache_clear()
    assert not (tmp_path / update_run.RUN_FILE).exists()
