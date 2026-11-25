"""Every zip the platform backs up into is in place whole or not at all,
readable by the server's account only, and each kind's rotation keeps its
own newest and leaves the other kinds alone.

Four writers: a project backup (``core/backup_manager.create_backup``), the
same backup written again with its logins sealed (``seal_logins_in_backup``),
the backup before an update or a rollback (``updater/backup.create_backup``)
and the Project Library's zip before its logins move
(``core/project_library``). A project kept at the data root shares one
``backups/`` folder with the update's zips, so each rotation reads its own
names only.
"""

from __future__ import annotations

import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from openavc.core import backup_manager
from openavc.core import project_library as library
from openavc.core.device_logins import DeviceLogins
from openavc.updater import backup as updater_backup


def _project(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    project = folder / "project.avc"
    project.write_text(json.dumps({"project": {"id": "acme", "name": "Acme"}}), encoding="utf-8")
    return project


def _dated(path: Path, when: int) -> Path:
    path.write_bytes(b"zip")
    os.utime(path, (when, when))
    return path


def _left_behind(folder: Path) -> list[str]:
    """Every name in ``folder`` that is not a finished zip: a temporary file
    a write left, whatever it is called."""
    return sorted(p.name for p in folder.iterdir() if not p.name.endswith(".zip"))


def _fail_the_write(monkeypatch) -> None:
    """Every member a zip is given fails part way, as a full disk does."""

    def full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "write", full)
    monkeypatch.setattr(zipfile.ZipFile, "writestr", full)


# --- each rotation keeps its own newest -----------------------------------


def test_a_project_backup_rotation_keeps_the_newest_fifteen_by_when_written(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    zips = [_dated(folder / f"backup_{n:02d}_x.zip", 1_700_000_000 + n) for n in range(17)]
    # A name that sorts last but was written first is still the oldest.
    os.utime(zips[0], (1_600_000_000, 1_600_000_000))
    zips[0].rename(folder / "backup_99_x.zip")

    assert backup_manager.cleanup_backups(folder) == 2

    assert sorted(p.name for p in folder.glob("backup_*.zip")) == sorted(z.name for z in zips[2:])


def test_the_backup_a_restore_is_about_to_read_is_neither_removed_nor_counted(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    oldest, *rest = [_dated(folder / f"backup_{n}_x.zip", 1_700_000_000 + n) for n in range(4)]

    assert backup_manager.cleanup_backups(folder, keep=2, spare=oldest) == 1

    assert oldest.exists()
    assert sorted(p.name for p in folder.glob("backup_*.zip")) == sorted(
        [oldest.name, rest[1].name, rest[2].name]
    )


def test_the_update_rotation_keeps_the_newest_five_by_when_written(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    zips = [_dated(folder / f"pre-update-v0.{n}.0-x.zip", 1_700_000_000 + n) for n in range(7)]

    assert updater_backup.cleanup_old_backups(tmp_path) == 2

    assert sorted(p.name for p in folder.glob("pre-update-*.zip")) == sorted(z.name for z in zips[2:])


def test_each_rotation_leaves_the_other_kinds_in_a_shared_folder_alone(tmp_path):
    """A project at the data root keeps its backups in the update's folder."""
    folder = tmp_path / "backups"
    folder.mkdir()
    project_zips = [_dated(folder / f"backup_{n}_x.zip", 1_600_000_000 + n) for n in range(3)]
    update_zips = [_dated(folder / f"pre-update-v0.{n}.0-x.zip", 1_700_000_000 + n) for n in range(3)]
    library_zip = _dated(folder / "library-before-moving-device-logins-1.zip", 1_500_000_000)

    assert updater_backup.cleanup_old_backups(tmp_path, keep=1) == 2
    assert backup_manager.cleanup_backups(folder, keep=1) == 2

    left = sorted(p.name for p in folder.iterdir())
    assert left == sorted([project_zips[2].name, update_zips[2].name, library_zip.name])


def test_a_backup_that_cannot_be_removed_is_left_and_the_rest_go(tmp_path, monkeypatch):
    folder = tmp_path / "backups"
    folder.mkdir()
    zips = [_dated(folder / f"pre-update-v0.{n}.0-x.zip", 1_700_000_000 + n) for n in range(4)]
    real_unlink = Path.unlink

    def held(self, *args, **kwargs):
        if self.name == zips[0].name:
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held)

    assert updater_backup.cleanup_old_backups(tmp_path, keep=1) == 2
    assert zips[0].exists() and zips[3].exists()
    assert not zips[1].exists() and not zips[2].exists()


# --- a zip is in place whole or not at all ---------------------------------


def test_a_project_backup_that_fails_part_way_leaves_nothing(tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    (tmp_path / "proj" / "scripts").mkdir()
    (tmp_path / "proj" / "scripts" / "a.py").write_text("x = 1", encoding="utf-8")
    _fail_the_write(monkeypatch)

    with pytest.raises(OSError):
        backup_manager.create_backup(project, "Before a change")

    assert list((tmp_path / "proj" / "backups").iterdir()) == []


def test_an_update_backup_that_fails_part_way_leaves_nothing(tmp_path, monkeypatch):
    _project(tmp_path / "projects" / "default")
    _fail_the_write(monkeypatch)

    with pytest.raises(OSError):
        updater_backup.create_backup(tmp_path, "0.1.0")

    assert list((tmp_path / "backups").iterdir()) == []


def test_a_library_backup_that_fails_part_way_leaves_nothing_and_rewrites_no_copy(
    tmp_path, monkeypatch,
):
    data = tmp_path / "data"
    lib = data / "saved_projects"
    copy = lib / "room_b" / "project.avc"
    copy.parent.mkdir(parents=True)
    copy.write_text(json.dumps({
        "project": {"id": "room_b", "name": "Room B"},
        "isc": {"auth_key": "mesh-key-room-b"},
    }), encoding="utf-8")
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", lib)
    _fail_the_write(monkeypatch)

    library.move_library_logins(DeviceLogins(data))

    assert list((data / "backups").iterdir()) == []
    assert "mesh-key-room-b" in copy.read_text(encoding="utf-8")


def test_a_backup_written_again_with_its_logins_sealed_keeps_the_old_one_on_failure(
    tmp_path, monkeypatch,
):
    project = _project(tmp_path / "proj")
    backup = backup_manager.create_backup(project, "Before moving device logins")
    before = backup.read_bytes()
    real_write = zipfile.ZipFile.writestr

    def full(self, name, *args, **kwargs):
        if name == backup_manager.ARCHIVE_LOGINS or getattr(name, "filename", "") == backup_manager.ARCHIVE_LOGINS:
            raise OSError(28, "No space left on device")
        return real_write(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", full)
    monkeypatch.setattr(backup_manager, "_logins_copy", lambda data: "sealed")

    def moved(raw):
        return {**raw, "moved": True}

    with pytest.raises(OSError):
        backup_manager.seal_logins_in_backup(backup, moved)

    assert backup.read_bytes() == before
    assert _left_behind(backup.parent) == []


def test_a_backup_is_sealed_with_no_zip_open_on_it(tmp_path, monkeypatch):
    """Windows refuses to replace a file the process holds open, so the
    rewrite closes the backup before it takes its place."""
    project = _project(tmp_path / "proj")
    backup = backup_manager.create_backup(project, "Before moving device logins")
    open_now: set[Path] = set()
    real_init, real_close, real_replace = zipfile.ZipFile.__init__, zipfile.ZipFile.close, os.replace

    def opened(self, file, mode="r", *args, **kwargs):
        real_init(self, file, mode, *args, **kwargs)
        if isinstance(file, (str, os.PathLike)):
            self.watched = Path(file).resolve()
            open_now.add(self.watched)

    def closed(self):
        open_now.discard(getattr(self, "watched", None))
        real_close(self)

    def replace(src, dst, *args, **kwargs):
        if Path(dst).resolve() in open_now:
            raise PermissionError(13, "The file is in use by another process", str(dst))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", opened)
    monkeypatch.setattr(zipfile.ZipFile, "close", closed)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(backup_manager, "_logins_copy", lambda data: "sealed")

    assert backup_manager.seal_logins_in_backup(backup, lambda raw: {**raw, "moved": True}) is True

    with zipfile.ZipFile(backup) as zf:
        assert zf.read(backup_manager.ARCHIVE_LOGINS) == b"sealed"


def test_no_temporary_name_matches_a_rotation(tmp_path, monkeypatch):
    """A write cut off by a power loss leaves its temporary file, which no
    list or rotation may take for a backup."""
    seen: list[str] = []
    real_init = zipfile.ZipFile.__init__

    def watch(self, file, mode="r", *args, **kwargs):
        if mode == "w" and isinstance(file, (str, os.PathLike)):
            seen.append(Path(file).name)
        return real_init(self, file, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", watch)
    project = _project(tmp_path / "proj")
    backup_manager.create_backup(project, "Before a change")
    _project(tmp_path / "data" / "projects" / "default")
    updater_backup.create_backup(tmp_path / "data", "0.1.0")

    assert len(seen) == 2
    for name in seen:
        assert name.startswith(".") and name.endswith(".tmp")
        for pattern in ("backup_*.zip", "pre-update-*.zip", "library-before-moving-device-logins-*.zip"):
            assert not Path(name).match(pattern)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_project_backup_is_owner_only_whatever_the_umask(tmp_path):
    previous = os.umask(0o022)
    try:
        backup = backup_manager.create_backup(_project(tmp_path / "proj"), "Before a change")
    finally:
        os.umask(previous)

    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
