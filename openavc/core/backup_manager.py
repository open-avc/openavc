"""
Project backup manager — creates, lists, restores, and rotates ZIP backups.

Backups are ZIP files stored in {project_dir}/backups/ containing:
  - project.avc          (the project file, whatever it is called on disk)
  - backup_meta.json     (reason, kind, timestamp, project name and stamp,
                          version)
  - scripts/             (all .py script files)
  - assets/              (uploaded images, folders intact)
  - ui/                  (custom controls, folders intact)
  - themes/              (project custom themes)
  - state.json           (persisted variable state, if present)
  - device_logins.json   (the project's logins: its devices', its plugins' and
                          its mesh key, sealed with this system's key, never
                          the key: core/device_logins.py)

Backups are created at meaningful boundaries (project replacement, AI changes,
cloud pushes, manual request, periodic timer) — NOT on every save. Every door
that takes one before it changes the project, or on request, asks ``capture``,
which says when the backup could not be saved (``BackupFailed``): a door that
replaces the project refuses, and one that goes on says no backup was saved.

Each backup says what kind it is (``KIND_*``) and which project it holds (its
``project.uuid``), because the backups belong to the project file's folder,
not to one project: after another project is opened there, the backups
before it hold the project that was closed. ``backups_since_replaced`` is
THE rule for which backups are the running project's, read by a start whose
project file is missing or will not load.

The one backup taken with logins in the clear is the first start's, before the
logins move out of the project into the login file; once they all have, it
is written again with the marker in each one's place and the sealed copy
beside it (``seal_logins_in_backup``), keeping the file as it was otherwise.

Every door takes the project FILE, not its directory, because the file can be
called anything (``OPENAVC_PROJECT`` is a path). The archive member keeps its
one name, ``project.avc``, whatever the file is called: it is the archive's
format, and every backup already on disk carries it, so a backup restores into
the configured file however either was named.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import zipfile
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openavc.core import asset_tree, project_unit, theme_tree
from openavc.core.custom_ui import extract_from_zip, zip_entries
from openavc.utils.backup_zip import keep_newest, write_zip
from openavc.utils.logger import get_logger
from openavc.utils.paths import plain_file_in
from openavc.version import __version__

log = get_logger(__name__)

MAX_BACKUPS = 15

# The project file's name inside a backup ZIP, whatever it is called on disk.
ARCHIVE_PROJECT = "project.avc"

# The project's logins inside a backup ZIP (its devices', its plugins' and its
# mesh key), sealed with this system's key (core/device_logins.py
# backup_copy): restored on this system they fill in what it no longer has;
# anywhere else they do not open.
ARCHIVE_LOGINS = "device_logins.json"

# What the backup is: its reason, kind, time and held project.
_ARCHIVE_META = "backup_meta.json"

# What a backup is, written in its meta as "kind".
KIND_AUTO = "auto"              # the periodic backup
KIND_MANUAL = "manual"          # Create Backup, named by the person
KIND_CHANGE = "change"          # before a change to the running project (the AI, a cloud push, the login move; a replace that did not happen)
KIND_REPLACE = "replace_boundary"  # before another project replaced it (open, import, blank, restore)
KIND_SET_ASIDE = "set_aside"    # what a start replaced (core/project_recovery)
KINDS = frozenset({KIND_AUTO, KIND_MANUAL, KIND_CHANGE, KIND_REPLACE, KIND_SET_ASIDE})

# A backup from before kinds is judged by its reason, as each released door
# wrote it: these are the ones another project replaced.
_REPLACE_REASON_PREFIXES = ("Before opening ", "Before importing ")
_REPLACE_REASONS = frozenset({"Before creating blank project", "Before restore", "Pre-restore backup"})


def _logins_copy(data: object) -> str | None:
    """This system's saved logins for what ``data`` (the project file read as
    JSON) holds, sealed for a backup; None when there are none or they cannot
    be read. A backup is taken whatever happens here."""
    try:
        from openavc.core.device_logins import device_logins, in_project

        return device_logins().backup_copy(in_project(data))
    except Exception:
        log.exception("Could not add the device logins to the backup; it is taken without them")
        return None


def _restore_logins(zf: zipfile.ZipFile, names: list[str], project_file: Path, where: str) -> None:
    """A backup's sealed logins, filed for what the project just restored
    holds wherever this system has none saved. Never raises."""
    if ARCHIVE_LOGINS not in names:
        return
    try:
        from openavc.core.device_logins import device_logins, in_project

        data = json.loads(project_file.read_text(encoding="utf-8"))
        text = zf.read(ARCHIVE_LOGINS).decode("utf-8")
        device_logins().restore_copy(text, in_project(data), where=where)
    except Exception:
        log.exception("Could not restore the device logins in %s", where)


@dataclass
class BackupInfo:
    """Metadata about a single backup file."""
    filename: str
    reason: str
    timestamp: str        # ISO 8601
    project_name: str
    size_bytes: int
    format: str           # "zip" or "legacy"
    kind: str = ""        # KIND_*; "" for a backup from before kinds
    project_uuid: str = ""  # the held project's ``project.uuid``; "" when it had none

    @property
    def before_a_replace(self) -> bool:
        """Taken just before another project replaced the one it holds."""
        return _before_a_replace(self.kind, self.reason)


def _before_a_replace(kind: str, reason: str) -> bool:
    """A backup of ``kind`` named ``reason`` was taken just before another
    project replaced the one it holds; one with no kind is judged by its
    reason."""
    if kind:
        return kind == KIND_REPLACE
    return reason in _REPLACE_REASONS or reason.startswith(_REPLACE_REASON_PREFIXES)


def backup_id(project_dir: Path, backup_path: Path) -> str:
    """THE name a backup is known by outside this module: its path relative to
    the project directory, POSIX-separated.

    A ZIP lives in ``{project_dir}/backups/`` and is therefore
    ``backups/backup_….zip``; a legacy ``.avc.bak`` sits beside the project file
    and is a bare name. Every door that hands a backup out or takes one back in
    uses this form — the list endpoint, the recovery record, and the restore
    endpoint, which resolves it with ``project_dir / filename``. Create used to
    answer with a bare ``path.name``, which restore then could not find, so a
    caller could not feed one door's answer into the next.
    """
    return backup_path.relative_to(project_dir).as_posix()


def _backup_dir(project_dir: Path) -> Path:
    """Return the backups subdirectory, creating it if needed."""
    d = project_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _reason_slug(reason: str) -> str:
    """Convert a reason string to a short filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_")
    return slug[:40] or "backup"


def _read_project(project_file: Path) -> tuple[object, dict]:
    """The project file read as JSON and its ``project`` section, for a
    backup's logins and its meta; (None, {}) or an empty section when it
    cannot be read, and the backup is taken all the same."""
    try:
        data = json.loads(project_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, {}
    meta = data.get("project") if isinstance(data, dict) else None
    return data, meta if isinstance(meta, dict) else {}


def _text(meta: dict, key: str) -> str:
    value = meta.get(key)
    return value if isinstance(value, str) else ""


def _write_archive(zf: zipfile.ZipFile, project_file: Path, meta: dict, logins: str | None) -> None:
    """Every member of a project backup, written into ``zf``: the project
    file (none when it is missing: ``set_aside``), the meta, what sits
    beside the project file (``_beside_members``) and the sealed logins."""
    if project_file.exists():
        zf.write(project_file, ARCHIVE_PROJECT)
    zf.writestr(_ARCHIVE_META, json.dumps(meta, indent=2))
    for archive_path, f in _beside_members(project_file.parent):
        zf.write(f, archive_path)
    if logins is not None:
        zf.writestr(ARCHIVE_LOGINS, logins)


#: How each of the project's file trees (``project_unit.FILE_TREES``) is
#: written into a backup: what the tree may hold is its own module's.
_TREE_ENTRIES: dict[str, Callable[[Path], list[tuple[str, Path]]]] = {
    project_unit.ASSETS_DIR: asset_tree.zip_entries,
    project_unit.UI_DIR: zip_entries,
    project_unit.THEMES_DIR: theme_tree.zip_entries,
}


def _beside_members(project_dir: Path) -> Iterator[tuple[str, Path]]:
    """(archive path, file) for what a backup carries from beside the
    project file (``core/project_unit``): the scripts, each tree with its
    folders intact, state.json.

    Assets used to be a non-recursive iterdir(), so a nested one was not
    flattened but absent from the backup, the one failure a backup must not
    have; one custom control is a folder, so ui/ cannot be flattened on the
    way in or out."""
    scripts = project_unit.SCRIPTS_DIR
    for f in (project_dir / scripts).glob("*.py"):
        yield f"{scripts}/{f.name}", f
    for tree in project_unit.FILE_TREES:
        yield from _TREE_ENTRIES[tree](project_dir / tree)
    state_file = project_dir / project_unit.STATE_FILE
    if state_file.is_file():
        yield project_unit.STATE_FILE, state_file


def _free_name(backup_d: Path, base: str) -> Path:
    """``base``.zip in ``backup_d``, or the first ``base``_N.zip not taken.
    The caller holds the project-save lock until it has renamed into it."""
    backup_path = backup_d / f"{base}.zip"
    counter = 1
    while backup_path.exists():
        backup_path = backup_d / f"{base}_{counter}.zip"
        counter += 1
    return backup_path


def create_backup(
    project_file: Path,
    reason: str,
    *,
    kind: str = "",
    spare: Path | None = None,
) -> Path | None:
    """Create a ZIP backup of the current project state.

    ``project_file`` is the project file itself; the scripts, assets, ui/,
    themes and state.json beside it go into the same archive. ``kind`` is
    what it is (``KIND_*``), which a door says through ``capture``; with
    none it is judged by ``reason``, as a backup from before kinds is
    (``BackupInfo.before_a_replace``). Returns the
    backup path, or None when there is no project file to back up. Raises
    the ``OSError`` when the backup cannot be saved (the disk full part way,
    no permission), leaving no part of it behind; a door asks ``capture``,
    which says so in the door's words.

    ``spare`` is a backup this one's rotation leaves alone: the one a restore
    is about to read, which is the oldest when somebody goes back the furthest
    (``cleanup_backups``).
    """
    if not project_file.exists():
        log.debug(f"No project file to back up at {project_file}")
        return None
    return _take(project_file, reason, kind=kind, spare=() if spare is None else (spare,))


def set_aside(project_file: Path, reason: str, *, spare: Collection[Path] = ()) -> Path | None:
    """What a start is about to replace (``core/project_recovery``), kept
    under Backups first: the project file as it is, however unreadable, and
    what a backup carries from beside it (``_beside_members``), as the
    backup a running replace takes keeps them. When the project file is
    missing, the files beside it alone, a backup that restores nothing but
    keeps them. Returns the backup, or None when there is nothing there
    at all (a first start). Raises the ``OSError`` as ``create_backup``
    does. ``spare`` are the backups the start is about to try, which its
    rotation leaves alone (``cleanup_backups``)."""
    if not project_file.exists() and next(_beside_members(project_file.parent), None) is None:
        return None
    return _take(project_file, reason, kind=KIND_SET_ASIDE, spare=spare)


def _take(project_file: Path, reason: str, *, kind: str, spare: Collection[Path]) -> Path:
    """The backup ``create_backup`` and ``set_aside`` take, written and
    placed under Backups, the oldest past ``MAX_BACKUPS`` rotated away."""
    project_dir = project_file.parent
    from openavc.core.project_loader import _project_save_lock

    backup_d = _backup_dir(project_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = _reason_slug(reason)

    data, held = _read_project(project_file)
    logins = _logins_copy(data) if data is not None else None

    meta = {
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_name": _text(held, "name"),
        "openavc_version": __version__,
    }
    if kind:
        meta["kind"] = kind
    if _text(held, "uuid"):
        meta["project_uuid"] = _text(held, "uuid")

    def place(finished: Path) -> Path:
        # The lock makes the free-name check and the rename one step, so two
        # backups in the same second with the same reason never pick one name.
        with _project_save_lock:
            backup_path = _free_name(backup_d, f"backup_{stamp}_{slug}")
            os.replace(finished, backup_path)
            cleanup_backups(backup_d, keep=MAX_BACKUPS, spare=spare)
        return backup_path

    # Compressed outside the lock (the slow part). project.avc is written
    # atomically by save_project, so this reads a complete file even under a
    # concurrent save.
    try:
        backup_path = write_zip(
            backup_d, "backup", lambda zf: _write_archive(zf, project_file, meta, logins), place=place,
        )
    except OSError as e:
        log.error(f"Failed to create backup: {e}")
        raise
    log.info(f"Backup created: {backup_path.name} ({reason})")
    return backup_path


class BackupFailed(Exception):
    """A backup a door asked for (``capture``) that could not be saved.
    ``cause`` is the operating system's error, which the door words without
    its path (``api/error_messages.backup_failure_why``)."""

    def __init__(self, cause: OSError) -> None:
        super().__init__(f"The project backup could not be saved: {cause}")
        self.cause = cause


async def capture(project_file: Path, reason: str, *, kind: str, spare: Path | None = None) -> Path | None:
    """THE backup a door takes before it changes the project, or when asked
    for one: ``create_backup`` named ``reason``, of ``kind`` (``KIND_*``,
    which every door says, since the start's recovery reads it), off the
    event loop. Returns
    the backup, or None when there is no project file, so nothing on disk is
    lost. Raises ``BackupFailed`` when it could not be saved, and the door
    hears it: a door that replaces the project (an import, opening another,
    a blank project, a restore, a cloud push) refuses with the project left
    as it was (``error_messages.describe_change_failure`` words it), the
    login move leaves the logins where they are, and the cloud AI's change
    goes on with its answer saying no backup was saved."""
    try:
        return await asyncio.to_thread(create_backup, project_file, reason, kind=kind, spare=spare)
    except OSError as error:
        raise BackupFailed(error) from error


def seal_logins_in_backup(backup_path: Path, move: Callable[[dict[str, Any]], Any]) -> bool:
    """The backup at ``backup_path`` written again without the logins its
    project file holds in the clear, once they are saved on this system.

    ``move`` is ``device_logins.move_logins`` bound to this system's login
    file: the file read as JSON with the marker in place of each login, or the
    same object when it holds none or the move could not finish. The backup
    then carries the project's logins sealed (``ARCHIVE_LOGINS``), as every
    backup does, so restored on this system it fills in the same logins. The
    rest is kept: the file's format version, every other member (copied one
    at a time, never read whole), the name, and the time it was taken, which
    the list shows and the rotation sorts by. False, the backup untouched,
    when the file holds no login in the clear, the move could not finish or
    the logins cannot be sealed.
    """
    with zipfile.ZipFile(backup_path) as source:
        replaced = _sealed_members(source, move)
    if replaced is None or not _rewrite(backup_path, replaced):
        return False
    log.info(f"Backup {backup_path.name} now holds its logins sealed, not in the clear")
    return True


def not_replaced(backup_path: Path | None) -> None:
    """The backup a replace took of the project it replaces
    (``KIND_REPLACE``, or judged one by its reason), written again as one
    taken before a change to the running project (``KIND_CHANGE``), when
    the replace did not happen: it stopped part way and the project before
    it runs again (``core/project_replace``), or a start put that backup
    back (``core/project_recovery``). The project it holds is the one that runs,
    so a later start whose project file is missing or will not load walks
    on through it (``backups_since_replaced``) rather than stopping there,
    as at a project another one replaced. Its reason, what the person was
    doing, and the time it was taken are kept. None, or a backup of another
    kind, is left as it is. Never raises: a backup that cannot be written
    again is logged and stays as it was."""
    if backup_path is None:
        return
    try:
        meta = _read_zip_meta(backup_path)
        if not _before_a_replace(_text(meta, "kind"), _text(meta, "reason")):
            return
        meta["kind"] = KIND_CHANGE
        _rewrite(backup_path, {_ARCHIVE_META: json.dumps(meta, indent=2).encode("utf-8")})
    except (OSError, ValueError, zipfile.BadZipFile):
        log.exception("Could not mark backup %s as the running project's", backup_path.name)


def _rewrite(backup_path: Path, replaced: dict[str, bytes]) -> bool:
    """The backup at ``backup_path`` written again with each member
    ``replaced`` names holding its bytes, every other member copied one at
    a time, never read whole, its name and the time it was taken kept,
    which the list shows and the rotation sorts by. False when it was
    rotated away meanwhile."""
    from openavc.core.project_loader import _project_save_lock

    taken = backup_path.stat()

    def place(finished: Path) -> Path:
        os.utime(finished, ns=(taken.st_atime_ns, taken.st_mtime_ns))
        with _project_save_lock:
            if not backup_path.exists():
                raise _RotatedAway
            os.replace(finished, backup_path)
        return backup_path

    def fill(out: zipfile.ZipFile) -> None:
        # Opened again and closed before place: Windows refuses to replace a
        # file this process holds open.
        try:
            with zipfile.ZipFile(backup_path) as again:
                _copy_replacing(again, out, dict(replaced))
        except FileNotFoundError as gone:
            raise _RotatedAway from gone

    try:
        write_zip(backup_path.parent, backup_path.stem, fill, place=place)
    except _RotatedAway:
        return False
    return True


class _RotatedAway(Exception):
    """The backup being written again was rotated away while its copy was written."""


def _copy_replacing(source: zipfile.ZipFile, out: zipfile.ZipFile, replaced: dict[str, bytes]) -> None:
    """Every member of ``source`` into ``out``, one at a time, never read
    whole, those ``replaced`` holds written in their place (``_rewrite``)."""
    for info in source.infolist():
        if info.filename in replaced:
            out.writestr(copy.copy(info), replaced.pop(info.filename))
            continue
        with source.open(info) as member, out.open(copy.copy(info), "w") as copied:
            shutil.copyfileobj(member, copied, 1 << 20)
    for name, content in replaced.items():
        out.writestr(name, content)


def _sealed_members(source: zipfile.ZipFile, move: Callable[[dict[str, Any]], Any]) -> dict[str, bytes] | None:
    """The two members a sealed backup replaces: its project file with the
    logins moved, and their sealed copy; None when there is nothing to seal
    (``seal_logins_in_backup``)."""
    try:
        raw = json.loads(source.read(ARCHIVE_PROJECT).decode("utf-8"))
    except (KeyError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    moved = move(raw)
    logins = _logins_copy(moved) if moved is not raw else None
    if logins is None:
        return None
    return {
        ARCHIVE_PROJECT: json.dumps(moved, indent=4, ensure_ascii=False).encode("utf-8"),
        ARCHIVE_LOGINS: logins.encode("utf-8"),
    }


def list_backups(project_file: Path) -> list[BackupInfo]:
    """List all backups (ZIP + legacy .avc.bak) of ``project_file``, newest first."""
    from openavc.core.project_loader import crash_copy_path

    project_dir = project_file.parent
    crash_copy = crash_copy_path(project_file).name
    results: list[BackupInfo] = []

    # New ZIP backups
    backup_d = project_dir / "backups"
    if backup_d.is_dir():
        for f in backup_d.glob("backup_*.zip"):
            try:
                meta = _read_zip_meta(f)
                results.append(BackupInfo(
                    filename=backup_id(project_dir, f),
                    reason=meta.get("reason", "Backup"),
                    timestamp=meta.get("timestamp", ""),
                    project_name=meta.get("project_name", ""),
                    size_bytes=f.stat().st_size,
                    format="zip",
                    kind=_text(meta, "kind"),
                    project_uuid=_text(meta, "project_uuid"),
                ))
            except (OSError, zipfile.BadZipFile):
                continue

    # Legacy timestamped .avc.bak files. Skip only this file's crash copy, the
    # one save_project rewrites before every save (``project.avc.bak`` for
    # ``project.avc``, ``main-hall.avc.bak`` for ``main-hall.avc``) — the old
    # `stem.endswith('.avc')` guard matched EVERY timestamped legacy file too
    # (e.g. "project.20240315.avc.bak"), hiding them all, and a guard on the
    # literal ``project.avc.bak`` listed any other file's crash copy as a backup
    # for boot recovery to walk. Pre-restore files match this glob as well but
    # are listed below with a clearer reason, so exclude them here to keep the
    # two passes disjoint.
    for f in project_dir.glob("*.avc.bak"):
        if f.name == crash_copy:
            continue
        if ".pre_restore_" in f.name:
            continue
        try:
            stat = f.stat()
            ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            results.append(BackupInfo(
                filename=backup_id(project_dir, f),
                reason="Legacy backup",
                timestamp=ts,
                project_name="",
                size_bytes=stat.st_size,
                format="legacy",
            ))
        except OSError:
            continue

    # Pre-restore backups (clearer reason label; disjoint from the pass above).
    for f in project_dir.glob("*.pre_restore_*.avc.bak"):
        try:
            stat = f.stat()
            ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            results.append(BackupInfo(
                filename=backup_id(project_dir, f),
                reason="Pre-restore backup",
                timestamp=ts,
                project_name="",
                size_bytes=stat.st_size,
                format="legacy",
            ))
        except OSError:
            continue

    # Sort newest first by timestamp (fall back to filename).
    results.sort(key=lambda b: b.timestamp or b.filename, reverse=True)
    return results


def backups_since_replaced(project_file: Path) -> list[BackupInfo]:
    """THE backups of the project at ``project_file`` (``list_backups``,
    newest first), when the file itself cannot say which project it is (it
    is missing, or will not load): the ones taken since another project last
    replaced it.

    The walk stops at the first backup taken just before a replace (opening
    another project, an import, a blank project, a restore:
    ``BackupInfo.before_a_replace``), since that one and everything older
    hold the project that was replaced, and at the first that holds a
    different project than the newer ones (two ``project.uuid`` stamps that
    differ: a file put in place by hand). A backup with no stamp (one from
    before stamps, or one that holds no project file) is judged by its kind
    alone."""
    since: list[BackupInfo] = []
    running = ""
    for info in list_backups(project_file):
        if info.before_a_replace or _holds_another(info, running):
            break
        running = running or info.project_uuid
        since.append(info)
    return since


def _holds_another(info: BackupInfo, running: str) -> bool:
    """``info`` holds a different project than ``running``, the stamp of
    the newer backups: never when either has none."""
    return bool(running and info.project_uuid and info.project_uuid != running)


class BackupUnreadable(ValueError):
    """A backup that holds no project file this system can read: not a ZIP,
    a ZIP with no ``project.avc``, or a file in no backup format."""


def is_zip_backup(backup_path: Path) -> bool:
    """Whether ``backup_path`` is a ZIP backup, which holds the project's
    files beside its project file; a legacy ``.avc.bak`` holds the project
    file alone."""
    return backup_path.suffix == ".zip"


def archived_project(backup_path: Path) -> bytes:
    """The project file a backup holds, as it was saved. Raises
    ``FileNotFoundError`` when the backup is not there and
    ``BackupUnreadable`` when it holds no project file."""
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    if backup_path.name.endswith(".avc.bak"):
        return backup_path.read_bytes()
    if not is_zip_backup(backup_path):
        raise BackupUnreadable(f"Unrecognized backup format: {backup_path.name}")
    return _zipped_project(backup_path)


def _zipped_project(backup_path: Path) -> bytes:
    """The project file a ZIP backup holds (``archived_project``)."""
    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            return zf.read(ARCHIVE_PROJECT)
    except KeyError as missing:
        raise BackupUnreadable(f"Backup ZIP does not contain {ARCHIVE_PROJECT}") from missing
    except zipfile.BadZipFile as bad:
        raise BackupUnreadable(f"Backup {backup_path.name} is not a readable ZIP") from bad


def _extract_scripts(zf: zipfile.ZipFile, dest: Path) -> None:
    """The archive's scripts into ``dest``, flat by design.

    Each entry is placed by the last part of its path, which must be one
    plain file name (``plain_file_in``): on Windows the last part of
    ``scripts/D:x.py`` is ``D:x.py``, a path on drive D:. An entry that is
    not is skipped with a warning, and the rest goes ahead."""
    prefix = f"{project_unit.SCRIPTS_DIR}/"
    for name in zf.namelist():
        if not name.startswith(prefix) or name == prefix:
            continue
        target = plain_file_in(dest, Path(name).name)
        if target is None:
            log.warning("Skipping backup entry with an unsafe file name: %r", name)
            continue
        with zf.open(name) as src:
            target.write_bytes(src.read())


#: How each of the project's file trees is read back out of a backup, its
#: folders intact: what the tree may hold is its own module's, so a member
#: it does not take is skipped.
_TREE_EXTRACTS: dict[str, Callable[[zipfile.ZipFile, Path], Any]] = {
    project_unit.ASSETS_DIR: asset_tree.extract_from_zip,
    project_unit.UI_DIR: extract_from_zip,
    project_unit.THEMES_DIR: theme_tree.extract_from_zip,
}


def backup_filler(backup_path: Path) -> Callable[[Path], None] | None:
    """What a backup holds beside its project file, as the ``fill`` a swap
    stages (``core/project_swap.stage``): the scripts, each file tree and
    state.json when it holds one, each written under its own name in the
    folder it is given. None for a legacy ``.avc.bak``, which holds the
    project file alone."""
    if not is_zip_backup(backup_path):
        return None

    def fill(dest: Path) -> None:
        with zipfile.ZipFile(backup_path, "r") as zf:
            _extract_scripts(zf, dest / project_unit.SCRIPTS_DIR)
            for tree, extract in _TREE_EXTRACTS.items():
                extract(zf, dest / tree)
            if project_unit.STATE_FILE in zf.namelist():
                (dest / project_unit.STATE_FILE).write_bytes(zf.read(project_unit.STATE_FILE))

    return fill


def restore_backup_logins(backup_path: Path, project_file: Path) -> None:
    """A ZIP backup's sealed device logins, filed for what ``project_file``
    (the project just restored from it) holds wherever this system has none
    saved, such as those of a device removed long ago; its own always win.
    Never raises."""
    if not is_zip_backup(backup_path):
        return
    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            _restore_logins(zf, zf.namelist(), project_file, f"backup {backup_path.name}")
    except (OSError, zipfile.BadZipFile):
        log.exception("Could not read the device logins in backup %s", backup_path.name)


def restore_from_backup(backup_path: Path, project_file: Path) -> None:
    """Restore a project from a backup file into ``project_file`` where no
    project is running: a start putting back the backup a replace took
    (``core/project_recovery``).

    ZIP backups: the project file, scripts/, assets/, ui/, themes/ and
    state.json (none when the backup holds none), and the device logins it
    holds wherever this system has none saved for that device. Legacy
    .avc.bak: the project file only.

    The backup prepared as every restore prepares it
    (``project_replace.from_backup``: read and checked as a load reads it,
    before anything is written) and put in place by the swap every replace
    makes (``project_replace.replace_files``), an update notice nobody has
    read yet kept: the backup is of the same project. A running project is
    replaced through ``Engine.replace_with`` instead, which stops its
    persister and reloads it.
    """
    from openavc.core.project_replace import from_backup, replace_files

    replace_files(from_backup(backup_path), project_file, keep_earlier_notes=True)
    log.info(f"Restored from backup: {backup_path.name}")


def cleanup_backups(backup_dir: Path, *, keep: int = MAX_BACKUPS, spare: Collection[Path] = ()) -> int:
    """Remove the oldest project backups past the newest ``keep``
    (``utils/backup_zip.keep_newest``). Returns how many were removed.

    Each in ``spare`` is neither removed nor counted, so the list holds that
    many more while they are spared and nothing else goes in their place: a
    person going back through the oldest backups one at a time still finds
    the next one, and a start that tries every backup it has removes none of
    them when it keeps what it replaces.
    """
    return keep_newest(backup_dir, "backup_*.zip", keep, spare=spare)


def _read_zip_meta(path: Path) -> dict:
    """Read backup_meta.json from a ZIP backup."""
    with zipfile.ZipFile(path, "r") as zf:
        if _ARCHIVE_META in zf.namelist():
            return json.loads(zf.read(_ARCHIVE_META))
    return {}
