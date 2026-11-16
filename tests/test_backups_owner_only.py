"""The backups the platform writes on its own into the data folder's
``backups/`` are readable by the server's account only.

Two kinds live there: the pre-update zip (the projects tree, so on a paired
system the cloud key in ``cloud.json``) and the Project Library's zip taken
before its logins moved (each copy as it was, logins and all). Each is
written owner-only (0600) whatever the umask, in a folder made owner-only
(0700) even when it already existed, and a start makes the files an earlier
version wrote there under the umask owner-only too.

POSIX only: on Windows the data folder's ACL is the installer's to set.
"""

from __future__ import annotations

import json
import os
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openavc.core import engine_lifecycle
from openavc.core import project_library as library
from openavc.core.device_logins import SAVED_LOGIN, DeviceLogins
from openavc.updater import backup as updater_backup
from openavc.updater.backup import create_backup

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


@pytest.fixture
def umask_022():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _paired_data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    project = data / "projects" / "default"
    project.mkdir(parents=True)
    (project / "project.avc").write_text('{"project": {"id": "p"}}', encoding="utf-8")
    cloud = project / "cloud.json"
    cloud.write_text(json.dumps({"enabled": True, "system_key": "k" * 64}), encoding="utf-8")
    cloud.chmod(0o600)
    (data / "system.json").write_text("{}", encoding="utf-8")
    return data


def test_the_pre_update_backup_is_written_owner_only(tmp_path, umask_022):
    data = _paired_data_dir(tmp_path)
    (data / "backups").mkdir(mode=0o755)

    backup = create_backup(data, "0.1.0")

    with zipfile.ZipFile(backup) as zf:
        assert "projects/default/cloud.json" in zf.namelist()
    assert _mode(backup) == 0o600
    assert _mode(data / "backups") == 0o700
    assert [p.name for p in (data / "backups").iterdir()] == [backup.name]


def test_a_backups_folder_that_cannot_be_made_owner_only_never_stops_the_update(
    tmp_path, umask_022, monkeypatch, caplog,
):
    data = _paired_data_dir(tmp_path)
    real_chmod = os.chmod

    def refused(path, mode, *args, **kwargs):
        if Path(path) == data / "backups":
            raise PermissionError("not the owner")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", refused)
    backup = create_backup(data, "0.1.0")

    assert backup.exists()
    assert _mode(backup) == 0o600
    assert any("not the owner" in r.getMessage() for r in caplog.records)


def test_the_library_backup_before_moving_logins_is_written_owner_only(
    tmp_path, umask_022, monkeypatch,
):
    data = tmp_path / "data"
    lib = data / "saved_projects"
    (lib / "room_b").mkdir(parents=True)
    (data / "backups").mkdir(mode=0o755)
    (lib / "room_b" / "project.avc").write_text(json.dumps({
        "project": {"id": "room_b", "name": "Room B"},
        "isc": {"auth_key": "mesh-key-room-b"},
    }), encoding="utf-8")
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", lib)

    def no_rewrite(path, text):  # the copy keeps its logins, so its zip is kept
        raise OSError("read-only")

    monkeypatch.setattr(library, "_atomic_write_text", no_rewrite)
    library.move_library_logins(DeviceLogins(data))

    [backup] = (data / "backups").glob("library-before-moving-device-logins-*.zip")
    assert _mode(backup) == 0o600
    assert _mode(data / "backups") == 0o700
    assert [p.name for p in (data / "backups").iterdir()] == [backup.name]
    assert DeviceLogins(data).secret(("project", "isc", ""), "auth_key", library="room_b") == (
        "mesh-key-room-b"
    )
    assert SAVED_LOGIN not in (lib / "room_b" / "project.avc").read_text(encoding="utf-8")


def test_a_start_makes_the_backups_an_earlier_version_left_owner_only(tmp_path, umask_022):
    data = tmp_path / "data"
    backups = data / "backups"
    backups.mkdir(parents=True, mode=0o755)
    old = [
        backups / "pre-update-v0.39.0-20261101T000000Z.zip",
        backups / "library-before-moving-device-logins-20261101_000000.zip",
    ]
    for path in old:
        path.write_bytes(b"zip")
        path.chmod(0o644)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("not a backup", encoding="utf-8")
    elsewhere.chmod(0o644)
    (backups / "linked.zip").symlink_to(elsewhere)

    step = dict((fn, msg) for fn, msg in engine_lifecycle._AFTER_SYSTEM_JSON)
    assert engine_lifecycle._make_backups_owner_only in step
    engine_lifecycle._make_backups_owner_only(SimpleNamespace(data_dir=data), tmp_path)

    assert _mode(backups) == 0o700
    assert [_mode(p) for p in old] == [0o600, 0o600]
    assert _mode(elsewhere) == 0o644  # a link is never followed


def test_a_start_makes_them_owner_only_through_the_real_path(tmp_path, umask_022, caplog):
    from openavc.system_config import get_system_config

    backups = Path(get_system_config().data_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backups.chmod(0o755)
    left = backups / "pre-update-v0.0.1-20260101T000000Z.zip"
    left.write_bytes(b"zip")
    left.chmod(0o644)
    try:
        engine_lifecycle.prepare_host(tmp_path)
        assert _mode(left) == 0o600
        assert _mode(backups) == 0o700
        assert "Could not make the data folder's backups" not in caplog.text
    finally:
        left.unlink()


def test_a_data_folder_with_no_backups_yet_is_left_as_it_is(tmp_path):
    updater_backup.make_backups_private(tmp_path)
    assert not (tmp_path / "backups").exists()
