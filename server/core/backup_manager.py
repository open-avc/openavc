"""
Project backup manager — creates, lists, restores, and rotates ZIP backups.

Backups are ZIP files stored in {project_dir}/backups/ containing:
  - project.avc          (the project file)
  - backup_meta.json     (reason, timestamp, project name, version)
  - scripts/             (all .py script files)
  - assets/              (all asset files)
  - state.json           (persisted variable state, if present)

Backups are created at meaningful boundaries (project replacement, AI changes,
cloud pushes, manual request, periodic timer) — NOT on every save.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from server.utils.logger import get_logger
from server.version import __version__

log = get_logger(__name__)

MAX_BACKUPS = 15


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically (temp file in the same dir, then
    os.replace). A crash mid-write leaves the original ``target`` intact."""
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp", prefix=".restore_")
    try:
        os.write(fd, data)
        os.close(fd)
        fd = None
        os.replace(tmp, str(target))
        tmp = None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _swap_dir(staging: Path, target: Path) -> None:
    """Replace ``target`` directory with the fully-populated ``staging`` dir using
    atomic renames. The old dir is moved aside to a ``.restore-old`` sibling
    first, so a crash between the two renames keeps the old content (recoverable)
    rather than losing both. Rolls the old dir back if the swap-in fails."""
    old = target.with_name(target.name + ".restore-old")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    moved_old = False
    if target.exists():
        os.replace(target, old)
        moved_old = True
    try:
        os.replace(staging, target)
    except OSError:
        if moved_old and not target.exists():
            os.replace(old, target)  # roll the old dir back into place
        raise
    if moved_old:
        shutil.rmtree(old, ignore_errors=True)


def _restore_subdir(
    zf: zipfile.ZipFile, names: list[str], prefix: str, target_dir: Path
) -> None:
    """Extract the ZIP entries under ``prefix`` into a fresh staging dir, then
    atomically swap it in for ``target_dir``. The old content is only removed
    once the new content is fully on disk — no unlink-then-extract window where a
    crash leaves the directory half-cleared (old gone, new missing)."""
    staging = target_dir.with_name(target_dir.name + ".restore-new")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    for name in names:
        if name.startswith(prefix) and name != prefix:
            fname = Path(name).name
            if fname:
                with zf.open(name) as src:
                    (staging / fname).write_bytes(src.read())
    _swap_dir(staging, target_dir)


@dataclass
class BackupInfo:
    """Metadata about a single backup file."""
    filename: str
    reason: str
    timestamp: str        # ISO 8601
    project_name: str
    size_bytes: int
    format: str           # "zip" or "legacy"


def _backup_dir(project_dir: Path) -> Path:
    """Return the backups subdirectory, creating it if needed."""
    d = project_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _reason_slug(reason: str) -> str:
    """Convert a reason string to a short filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_")
    return slug[:40] or "backup"


def create_backup(
    project_dir: Path,
    reason: str,
    *,
    max_backups: int = MAX_BACKUPS,
) -> Path | None:
    """Create a ZIP backup of the current project state.

    Returns the backup path, or None if there's nothing to back up.
    """
    project_file = project_dir / "project.avc"
    if not project_file.exists():
        log.debug("No project.avc to back up")
        return None

    from server.core.project_loader import _project_save_lock

    backup_d = _backup_dir(project_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = _reason_slug(reason)

    # Read project name from the file
    project_name = ""
    try:
        data = json.loads(project_file.read_text(encoding="utf-8"))
        project_name = data.get("project", {}).get("name", "")
    except (json.JSONDecodeError, OSError):
        pass

    meta = {
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_name": project_name,
        "openavc_version": __version__,
    }

    # Compress into a temp file first (the slow part, lock-free). project.avc is
    # written atomically by save_project, so reading it here always sees a
    # complete file even under a concurrent save.
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(backup_d), suffix=".zip.tmp", prefix=".backup_")
    os.close(tmp_fd)
    tmp_path: Path | None = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(project_file, "project.avc")
            zf.writestr("backup_meta.json", json.dumps(meta, indent=2))
            scripts_dir = project_dir / "scripts"
            if scripts_dir.is_dir():
                for f in scripts_dir.glob("*.py"):
                    zf.write(f, f"scripts/{f.name}")
            assets_dir = project_dir / "assets"
            if assets_dir.is_dir():
                for f in assets_dir.iterdir():
                    if f.is_file():
                        zf.write(f, f"assets/{f.name}")
            state_file = project_dir / "state.json"
            if state_file.is_file():
                zf.write(state_file, "state.json")

        # Reserve a unique name and place the finished ZIP atomically. The lock
        # makes the existence-check + rename a single critical section, so two
        # concurrent backups in the same second (same reason) can't pick the same
        # name and corrupt each other — and an interrupted compression never
        # surfaces as a real backup because only the completed temp is renamed in.
        with _project_save_lock:
            base = f"backup_{stamp}_{slug}"
            backup_path = backup_d / f"{base}.zip"
            counter = 1
            while backup_path.exists():
                backup_path = backup_d / f"{base}_{counter}.zip"
                counter += 1
            os.replace(tmp_path, backup_path)
            tmp_path = None
            cleanup_backups(backup_d, keep=max_backups)

        log.info(f"Backup created: {backup_path.name} ({reason})")
        return backup_path

    except OSError as e:
        log.error(f"Failed to create backup: {e}")
        return None
    finally:
        # Never leave a partial/orphan temp behind on failure.
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def list_backups(project_dir: Path) -> list[BackupInfo]:
    """List all backups (ZIP + legacy .avc.bak), newest first."""
    results: list[BackupInfo] = []

    # New ZIP backups
    backup_d = project_dir / "backups"
    if backup_d.is_dir():
        for f in backup_d.glob("backup_*.zip"):
            try:
                meta = _read_zip_meta(f)
                results.append(BackupInfo(
                    filename=f"backups/{f.name}",
                    reason=meta.get("reason", "Backup"),
                    timestamp=meta.get("timestamp", ""),
                    project_name=meta.get("project_name", ""),
                    size_bytes=f.stat().st_size,
                    format="zip",
                ))
            except (OSError, zipfile.BadZipFile):
                continue

    # Legacy timestamped .avc.bak files. Skip only the exact quick-restore copy
    # (project.avc.bak) — the old `stem.endswith('.avc')` guard matched EVERY
    # timestamped legacy file too (e.g. "project.20240315.avc.bak"), hiding them
    # all. Pre-restore files match this glob as well but are listed below with a
    # clearer reason, so exclude them here to keep the two passes disjoint.
    for f in project_dir.glob("*.avc.bak"):
        if f.name == "project.avc.bak":
            continue
        if ".pre_restore_" in f.name:
            continue
        try:
            stat = f.stat()
            ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            results.append(BackupInfo(
                filename=f.name,
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
                filename=f.name,
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


def restore_from_backup(backup_path: Path, project_dir: Path) -> None:
    """Restore a project from a backup file.

    ZIP backups: restores project.avc, scripts/, assets/, and state.json.
    Legacy .avc.bak: restores project.avc only.

    Every write is atomic (temp + os.replace for files, staged rename swap for
    directories) and the whole restore runs under the shared project-save lock,
    so a crash mid-restore can't truncate project.avc or leave scripts/assets
    half-cleared, and a concurrent save/backup can't read a partial state.

    NOTE: when a running StatePersister is watching state keys, the caller must
    stop it before this and re-load persisted state after (see the engine /
    restore endpoint) — this function only touches the files.
    """
    from server.core.project_loader import _project_save_lock

    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    project_file = project_dir / "project.avc"

    with _project_save_lock:
        if backup_path.suffix == ".zip":
            with zipfile.ZipFile(backup_path, "r") as zf:
                names = zf.namelist()
                if "project.avc" not in names:
                    raise ValueError("Backup ZIP does not contain project.avc")

                # project.avc — atomic write so a torn restore can't corrupt the
                # room's single source of truth (the recovery path then loads it).
                _atomic_write_bytes(project_file, zf.read("project.avc"))

                # scripts/ and assets/ — staged extract + atomic swap (clears
                # orphans without a half-cleared window).
                _restore_subdir(zf, names, "scripts/", project_dir / "scripts")
                _restore_subdir(zf, names, "assets/", project_dir / "assets")

                # Persisted state — restore it, or clear a stale newer state.json
                # when the backup predates persistence, so the older restored
                # project doesn't boot with newer values (symmetry with scripts/
                # assets, which are always cleared).
                state_target = project_dir / "state.json"
                if "state.json" in names:
                    _atomic_write_bytes(state_target, zf.read("state.json"))
                else:
                    state_target.unlink(missing_ok=True)

            log.info(f"Restored from ZIP backup: {backup_path.name}")

        elif backup_path.name.endswith(".avc.bak"):
            _atomic_write_bytes(project_file, backup_path.read_bytes())
            log.info(f"Restored from legacy backup: {backup_path.name}")

        else:
            raise ValueError(f"Unrecognized backup format: {backup_path.name}")


def cleanup_backups(backup_dir: Path, *, keep: int = MAX_BACKUPS) -> int:
    """Remove oldest backups beyond the retention limit. Returns count removed."""
    backups = sorted(
        backup_dir.glob("backup_*.zip"),
        key=lambda p: p.stat().st_mtime,
    )
    removed = 0
    while len(backups) > keep:
        oldest = backups.pop(0)
        try:
            oldest.unlink()
            log.debug(f"Removed old backup: {oldest.name}")
            removed += 1
        except OSError:
            pass
    return removed


def _read_zip_meta(path: Path) -> dict:
    """Read backup_meta.json from a ZIP backup."""
    with zipfile.ZipFile(path, "r") as zf:
        if "backup_meta.json" in zf.namelist():
            return json.loads(zf.read("backup_meta.json"))
    return {}
