"""The backup an update or a rollback takes before it changes anything
(updater/backup.py ``back_up_first``): one that cannot be made stops the
run, an update and a rollback alike, with a sentence that names the reason
and the remedy and never a path; trimming the old backups after it is
best-effort, so a trim that fails never stops a run whose backup was made.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from openavc.updater import backup
from tests.test_update_exit import LINUX, _manager

pytestmark = pytest.mark.usefixtures("under_its_service")

URL = "https://github.com/open-avc/openavc/releases/download/v9.9.9/a.tar.gz"
NOT_BACKED_UP = "the projects and settings could not be backed up first."
DISK_FULL = f"{NOT_BACKED_UP} The disk is full. Free up disk space and try again."
SEE_THE_LOG = f"{NOT_BACKED_UP} The Programmer's Log has the details."


def _full_disk(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device", "/data/backups/.pre-update.zip.tmp")


def _refused(*_args, **_kwargs):
    raise PermissionError(errno.EACCES, "Permission denied", "/data/projects/default/project.avc")


async def _update(mgr, backup, trim=None):
    with patch("openavc.updater.backup.create_backup", backup), \
            patch("openavc.updater.backup.cleanup_old_backups", trim or (lambda *_a, **_k: 0)):
        return await mgr.apply_update()


async def _cloud_update(mgr, backup, trim=None):
    with patch("openavc.updater.backup.create_backup", backup), \
            patch("openavc.updater.backup.cleanup_old_backups", trim or (lambda *_a, **_k: 0)), \
            patch.object(mgr, "_consumes_signed_tarball", return_value=False):
        return await mgr.apply_cloud_update("9.9.9", URL, "ab" * 32)


class _Rollback:
    """A rollback to 9.8.0 whose swap is recorded, never run."""

    def __init__(self) -> None:
        self.swapped = False

    def _swap(self, *_args, **_kwargs) -> bool:
        self.swapped = True
        return True

    async def __call__(self, mgr, backup, trim=None):
        with patch("openavc.updater.backup.create_backup", backup), \
                patch("openavc.updater.backup.cleanup_old_backups", trim or (lambda *_a, **_k: 0)), \
                patch("openavc.updater.rollback.can_rollback", return_value=True), \
                patch("openavc.updater.rollback.rollback_target_version", return_value="9.8.0"), \
                patch("openavc.updater.rollback.perform_rollback", self._swap):
            return await mgr.rollback()


UPDATES = {"update": _update, "cloud update": _cloud_update}


def _error_set(mgr) -> str | None:
    for call in mgr._state.set.call_args_list:
        if call.args[0] == "system.update_error" and call.args[1]:
            return call.args[1]
    return None


# ── A backup that cannot be made stops the run ──


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(UPDATES))
async def test_an_update_whose_backup_fails_stops_and_says_why(tmp_path, which):
    mgr = _manager(tmp_path, LINUX)

    answer = await UPDATES[which](mgr, _full_disk)

    assert answer == {"success": False, "error": f"Update failed: {DISK_FULL}"}
    mgr._download_update.assert_not_called()
    mgr._download_artifact.assert_not_called()
    mgr._apply_linux.assert_not_called()
    assert (mgr.run_record.run.end, mgr.run_record.run.error) == ("failed", f"Update failed: {DISK_FULL}")
    assert not mgr.update_in_progress
    assert _error_set(mgr) == f"Update failed: {DISK_FULL}"
    assert not (tmp_path / "pending-update").exists()


@pytest.mark.asyncio
async def test_a_rollback_whose_backup_fails_stops_and_says_why(tmp_path):
    mgr = _manager(tmp_path, LINUX)
    mgr.stage_update("9.10.0", URL, "ab" * 32)
    rollback = _Rollback()

    answer = await rollback(mgr, _full_disk)

    assert answer == {"success": False, "error": f"Rollback failed: {DISK_FULL}"}
    assert not rollback.swapped
    assert mgr.get_staged_update() is not None, "a rollback that did not run dropped the staged target"
    assert (mgr.run_record.run.end, mgr.run_record.run.error) == ("failed", f"Rollback failed: {DISK_FULL}")
    assert not mgr.update_in_progress
    assert mgr.busy_refusal() is None
    assert _error_set(mgr) == f"Rollback failed: {DISK_FULL}"
    assert (mgr._history[0]["status"], mgr._history[0]["error"]) == ("failed", f"Rollback failed: {DISK_FULL}")


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(UPDATES) + ["rollback"])
async def test_a_backup_failure_with_no_worded_reason_names_the_log_and_no_path(tmp_path, which):
    mgr = _manager(tmp_path, LINUX)
    door = UPDATES.get(which) or _Rollback()

    answer = await door(mgr, _refused)

    kind = "Rollback" if which == "rollback" else "Update"
    assert answer == {"success": False, "error": f"{kind} failed: {SEE_THE_LOG}"}
    assert "/data" not in answer["error"]


# ── Trimming the old backups never stops a run ──


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(UPDATES))
async def test_an_update_whose_old_backups_cannot_be_trimmed_still_installs(tmp_path, which):
    mgr = _manager(tmp_path, LINUX)

    answer = await UPDATES[which](mgr, lambda *_a, **_k: tmp_path / "b.zip", trim=_refused)

    assert answer == {"success": True, "message": "Update to v9.9.9 started"}
    mgr._apply_linux.assert_called_once()


@pytest.mark.asyncio
async def test_a_rollback_whose_old_backups_cannot_be_trimmed_still_rolls_back(tmp_path):
    mgr = _manager(tmp_path, LINUX)
    rollback = _Rollback()

    answer = await rollback(mgr, lambda *_a, **_k: tmp_path / "b.zip", trim=_refused)

    assert answer["success"] is True
    assert rollback.swapped


@pytest.mark.asyncio
async def test_the_marker_records_the_backup_the_update_made(tmp_path):
    """The automatic rollback restores user data from the backup the marker
    names, so the backup handed back is the one written."""
    mgr = _manager(tmp_path, LINUX)
    made = tmp_path / "backups" / "pre-update-v0.1.0-made.zip"

    with patch("openavc.updater.rollback.write_pending_marker") as marker:
        assert (await _update(mgr, lambda *_a, **_k: made))["success"] is True

    assert marker.call_args.kwargs["backup_path"] == made


def test_the_backup_rule_hands_back_the_path_it_made(tmp_path):
    data = tmp_path / "data"
    (data / "projects" / "default").mkdir(parents=True)
    (data / "projects" / "default" / "project.avc").write_text("{}", encoding="utf-8")

    made = backup.back_up_first(data, "0.1.0")

    assert made.is_file() and made.parent == data / "backups"
    assert made.name.startswith("pre-update-v0.1.0-")


def test_the_backup_rule_trims_the_old_backups_to_five(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    for n in range(6):
        old = backups / f"pre-update-v0.0.{n}-old.zip"
        old.write_bytes(b"")
        # Older than the one about to be made.
        os.utime(old, (1_000_000 + n, 1_000_000 + n))

    made = backup.back_up_first(tmp_path, "0.1.0")

    kept = sorted(p.name for p in backups.glob("pre-update-*.zip"))
    assert len(kept) == 5 and made.name in kept
    assert not (backups / "pre-update-v0.0.0-old.zip").exists()
    assert not (backups / "pre-update-v0.0.1-old.zip").exists()


def test_a_failed_backup_leaves_no_path_in_its_sentence():
    with patch("openavc.updater.backup.create_backup", _full_disk), \
            pytest.raises(backup.BackupFailed) as raised:
        backup.back_up_first(Path("/nowhere"), "0.1.0")

    assert str(raised.value) == DISK_FULL
