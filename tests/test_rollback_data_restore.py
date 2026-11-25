"""What an automatic rollback does when the projects cannot be put back.

Before an update is rolled back, the projects are put back from the backup
taken before it (``updater/backup.restore_user_data``), so the earlier
version opens files it can read. A backup whose files could not all be
written back stops the rollback: the earlier version is not put back on what
the failed one left, the start page says why, and the next start tries again,
so a retry must never lose what an earlier attempt set aside. A backup that
is missing or cannot be read changes nothing, so the rollback goes on with
the projects as they are, as a rollback by hand does, and the log says so.
"""

from __future__ import annotations

import errno
import logging
import os
import zipfile
from pathlib import Path

import pytest

from openavc.updater.backup import (
    BackupNotReadable,
    RestoreFailed,
    create_backup,
    restore_user_data,
)
from openavc.updater.rollback import RollbackStopped, restore_pre_update_data, write_pending_marker
from openavc.utils.paths import is_link
from tests.helpers import folder_link_kinds, make_folder_link

OLD = '{"openavc_version": "0.7.0"}'
NEW = '{"openavc_version": "0.8.0"}'


def _room(folder: Path) -> Path:
    (folder / "scripts").mkdir(parents=True)
    (folder / "project.avc").write_text(OLD, encoding="utf-8")
    (folder / "scripts" / "lights.py").write_text("# lights", encoding="utf-8")
    return folder


def _updated(folder: Path) -> None:
    """What the failed update left: the project and a script rewritten."""
    (folder / "project.avc").write_text(NEW, encoding="utf-8")
    (folder / "scripts" / "lights.py").write_text("# lights, 0.8.0", encoding="utf-8")


def _full_disk_at(monkeypatch, wanted, times: int = 1) -> None:
    """Writing a file into place fails as a full disk does wherever
    ``wanted(destination)`` holds, the first ``times`` times."""
    real = os.replace
    left = [times]

    def replace(src, dst, *args, **kwargs):
        if left[0] and wanted(Path(dst)):
            left[0] -= 1
            raise OSError(errno.ENOSPC, "No space left on device", str(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)


def _named(name: str):
    return lambda dst: dst.name == name


# --- a file that could not be written back -------------------------------


def test_an_external_project_file_that_cannot_be_written_back_fails_the_restore(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    room = _room(tmp_path / "room")
    backup = create_backup(data, "0.7.0", project_path=room / "project.avc")
    _updated(room)
    _full_disk_at(monkeypatch, _named("project.avc"))

    with pytest.raises(RestoreFailed) as failed:
        restore_user_data(data, backup, project_path=room / "project.avc")

    assert failed.value.cause.errno == errno.ENOSPC
    # The rest of what it holds is still written back.
    assert (room / "scripts" / "lights.py").read_text(encoding="utf-8") == "# lights"


def test_a_projects_folder_that_cannot_be_swapped_fails_the_restore_and_is_left_as_it_was(
    tmp_path, monkeypatch,
):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0")
    _updated(room)
    _full_disk_at(monkeypatch, lambda dst: dst == data / "projects")

    with pytest.raises(RestoreFailed):
        restore_user_data(data, backup)

    assert (room / "project.avc").read_text(encoding="utf-8") == NEW
    assert not (data / "projects.restore-tmp").exists()


def test_a_backup_that_cannot_be_opened_is_not_readable(tmp_path):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = data / "backups" / "pre-update-v0.7.0-x.zip"
    backup.parent.mkdir()
    backup.write_bytes(b"not a zip")

    with pytest.raises(BackupNotReadable):
        restore_user_data(data, backup)

    assert (room / "project.avc").read_text(encoding="utf-8") == OLD


def test_a_backup_with_a_damaged_file_in_it_changes_nothing(tmp_path):
    """Read through before anything is written, so the files ahead of the
    damaged one are not put back either."""
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = tmp_path / "pre-update-v0.7.0-x.zip"
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("projects/default/project.avc", '{"openavc_version": "0.6.0"}')
        zf.writestr("projects/default/scripts/lights.py", "# lights, intact")
    backup.write_bytes(backup.read_bytes().replace(b"intact", b"broken"))

    with pytest.raises(BackupNotReadable):
        restore_user_data(data, backup)

    assert (room / "project.avc").read_text(encoding="utf-8") == OLD
    assert not (data / "projects.pre-rollback").exists()


def test_a_damaged_file_the_restore_never_writes_does_not_stop_it(tmp_path):
    """The community drivers and plugins are carried, never put back."""
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = tmp_path / "pre-update-v0.7.0-x.zip"
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("projects/default/project.avc", '{"openavc_version": "0.6.0"}')
        zf.writestr("driver_repo/acme_widget.avcdriver", "# acme, intact")
    backup.write_bytes(backup.read_bytes().replace(b"intact", b"broken"))

    assert restore_user_data(data, backup) is True

    assert (room / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.6.0"}'


def test_a_backup_with_no_projects_in_it_restores_nothing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    backup = tmp_path / "pre-update-v0.7.0-x.zip"
    with zipfile.ZipFile(backup, "w") as zf:
        zf.writestr("system.json", "{}")

    assert restore_user_data(data, backup) is False


# --- the next start tries again, and loses nothing the first attempt kept --


def test_a_retry_keeps_the_projects_folder_the_first_attempt_set_aside(tmp_path, monkeypatch):
    """The projects folder was swapped, then the external project failed:
    the second attempt must not set the restored folder aside over the one
    the failed update left."""
    data = tmp_path / "data"
    default = _room(data / "projects" / "default")
    room = _room(tmp_path / "room")
    backup = create_backup(data, "0.7.0", project_path=room / "project.avc")
    _updated(default)
    _updated(room)
    _full_disk_at(monkeypatch, lambda dst: dst == room / "project.avc")

    with pytest.raises(RestoreFailed):
        restore_user_data(data, backup, project_path=room / "project.avc")
    assert restore_user_data(data, backup, project_path=room / "project.avc") is True

    assert (default / "project.avc").read_text(encoding="utf-8") == OLD
    assert (room / "project.avc").read_text(encoding="utf-8") == OLD
    aside = data / "projects.pre-rollback" / "default"
    assert (aside / "project.avc").read_text(encoding="utf-8") == NEW
    assert (aside / "scripts" / "lights.py").read_text(encoding="utf-8") == "# lights, 0.8.0"


def test_a_retry_puts_back_a_projects_folder_a_failed_swap_could_not(tmp_path, monkeypatch):
    """The swap failed and so did putting the folder back: the only copy of
    the projects the failed update left is the one set aside."""
    data = tmp_path / "data"
    default = _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0")
    _updated(default)
    _full_disk_at(monkeypatch, lambda dst: dst == data / "projects", times=2)

    with pytest.raises(RestoreFailed):
        restore_user_data(data, backup)
    assert not (data / "projects").exists()
    assert restore_user_data(data, backup) is True

    assert (default / "project.avc").read_text(encoding="utf-8") == OLD
    aside = data / "projects.pre-rollback" / "default"
    assert (aside / "project.avc").read_text(encoding="utf-8") == NEW


def test_an_earlier_rollback_s_folder_is_still_cleared(tmp_path):
    """What a rollback of an earlier update set aside makes way for this one."""
    data = tmp_path / "data"
    default = _room(data / "projects" / "default")
    first = create_backup(data, "0.6.0")
    _updated(default)
    assert restore_user_data(data, first) is True
    second = create_backup(data, "0.7.0")
    (default / "project.avc").write_text('{"openavc_version": "0.9.0"}', encoding="utf-8")

    assert restore_user_data(data, second) is True

    aside = data / "projects.pre-rollback" / "default"
    assert (aside / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.9.0"}'


@pytest.mark.parametrize("kind", folder_link_kinds())
def test_a_retry_through_a_linked_projects_folder_keeps_what_it_replaced(tmp_path, kind, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    elsewhere = tmp_path / "elsewhere"
    default = _room(elsewhere / "default")
    make_folder_link(kind, data / "projects", elsewhere)
    backup = create_backup(data, "0.7.0")
    _updated(default)
    _full_disk_at(monkeypatch, _named("lights.py"))

    with pytest.raises(RestoreFailed):
        restore_user_data(data, backup)
    assert restore_user_data(data, backup) is True

    assert is_link(data / "projects")
    assert (default / "project.avc").read_text(encoding="utf-8") == OLD
    assert (default / "scripts" / "lights.py").read_text(encoding="utf-8") == "# lights"
    aside = data / "projects.pre-rollback" / "default"
    assert (aside / "project.avc").read_text(encoding="utf-8") == NEW
    assert (aside / "scripts" / "lights.py").read_text(encoding="utf-8") == "# lights, 0.8.0"


@pytest.mark.parametrize("kind", folder_link_kinds())
def test_a_retry_writes_through_a_project_link_an_earlier_attempt_put_back(tmp_path, kind, monkeypatch):
    data = tmp_path / "data"
    (data / "projects").mkdir(parents=True)
    room = _room(tmp_path / "elsewhere" / "room")
    make_folder_link(kind, data / "projects" / "default", room)
    backup = create_backup(data, "0.7.0")
    _updated(room)
    _full_disk_at(monkeypatch, _named("lights.py"))

    with pytest.raises(RestoreFailed):
        restore_user_data(data, backup)
    assert restore_user_data(data, backup) is True

    assert is_link(data / "projects" / "default")
    assert (room / "project.avc").read_text(encoding="utf-8") == OLD
    assert (room / "scripts" / "lights.py").read_text(encoding="utf-8") == "# lights"
    aside = data / "projects.pre-rollback" / "default"
    # Written back by the first attempt, so only the first kept what it replaced.
    assert (aside / "project.avc").read_text(encoding="utf-8") == NEW
    assert (aside / "scripts" / "lights.py").read_text(encoding="utf-8") == "# lights, 0.8.0"


# --- what the rollback does with each -------------------------------------


def _pending(data: Path, backup: Path | None) -> None:
    write_pending_marker(data, "0.7.0", "0.8.0", backup_path=backup)


def test_a_rollback_whose_projects_could_not_be_put_back_stops_and_says_why(tmp_path, monkeypatch):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0")
    _pending(data, backup)
    _updated(room)
    _full_disk_at(monkeypatch, lambda dst: dst == data / "projects")

    with pytest.raises(RollbackStopped) as stopped:
        restore_pre_update_data(data)

    assert str(stopped.value) == (
        "v0.8.0 did not start, and going back to v0.7.0 stopped because the projects could "
        "not be put back as they were before the update. "
        "The disk is full. Free up disk space and try again."
    )


def test_a_failure_with_no_words_points_at_the_server_log(tmp_path, monkeypatch):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0")
    _pending(data, backup)
    _updated(room)
    real = os.replace

    def refused(src, dst, *args, **kwargs):
        if Path(dst) == data / "projects":
            raise PermissionError(errno.EACCES, "Permission denied", str(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refused)

    with pytest.raises(RollbackStopped) as stopped:
        restore_pre_update_data(data)

    assert str(stopped.value).endswith("before the update. Check the server log for the reason.")
    assert str(data) not in str(stopped.value)


def test_a_missing_backup_goes_back_with_the_projects_as_they_are(tmp_path, caplog):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    _pending(data, data / "backups" / "pre-update-v0.7.0-gone.zip")
    _updated(room)

    with caplog.at_level(logging.WARNING, logger="openavc.updater.rollback"):
        assert restore_pre_update_data(data) is False

    assert (room / "project.avc").read_text(encoding="utf-8") == NEW
    assert any(
        "were not put back" in r.getMessage() and "missing" in r.getMessage() for r in caplog.records
    )


def test_a_backup_that_cannot_be_opened_goes_back_with_the_projects_as_they_are(tmp_path, caplog):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = data / "backups" / "pre-update-v0.7.0-x.zip"
    backup.parent.mkdir()
    backup.write_bytes(b"not a zip")
    _pending(data, backup)

    with caplog.at_level(logging.WARNING, logger="openavc.updater.rollback"):
        assert restore_pre_update_data(data) is False

    assert (room / "project.avc").read_text(encoding="utf-8") == OLD
    assert any(
        "were not put back" in r.getMessage() and "could not be read" in r.getMessage()
        for r in caplog.records
    )
