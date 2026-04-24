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
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from server.utils.logger import get_logger
from server.version import __version__

log = get_logger(__name__)

MAX_BACKUPS = 15


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

    backup_d = _backup_dir(project_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _reason_slug(reason)
    filename = f"backup_{stamp}_{slug}.zip"
    backup_path = backup_d / filename

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

    try:
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Project file
            zf.write(project_file, "project.avc")

            # Metadata
            zf.writestr("backup_meta.json", json.dumps(meta, indent=2))

            # Scripts
            scripts_dir = project_dir / "scripts"
            if scripts_dir.is_dir():
                for f in scripts_dir.glob("*.py"):
                    zf.write(f, f"scripts/{f.name}")

            # Assets
            assets_dir = project_dir / "assets"
            if assets_dir.is_dir():
                for f in assets_dir.iterdir():
                    if f.is_file():
                        zf.write(f, f"assets/{f.name}")

            # Persisted variable state
            state_file = project_dir / "state.json"
            if state_file.is_file():
                zf.write(state_file, "state.json")

        cleanup_backups(backup_d, keep=max_backups)
        log.info(f"Backup created: {filename} ({reason})")
        return backup_path

    except OSError as e:
        log.error(f"Failed to create backup: {e}")
        return None


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

    # Legacy .avc.bak files (timestamped ones, not the quick-restore)
    for f in project_dir.glob("*.avc.bak"):
        # Skip the quick-restore file (project.avc.bak — no timestamp)
        stem = f.stem  # e.g. "project.20240315_143022.avc" or "project.avc"
        if stem.endswith(".avc"):
            # This is the quick-restore file (project.avc.bak), skip it
            continue
        try:
            stat = f.stat()
            # Try to extract timestamp from filename
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

    # Also include pre_restore backups
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

    # Sort newest first by timestamp (fall back to filename)
    results.sort(key=lambda b: b.timestamp or b.filename, reverse=True)

    # Deduplicate (pre_restore files matched by both globs)
    seen: set[str] = set()
    deduped: list[BackupInfo] = []
    for b in results:
        if b.filename not in seen:
            seen.add(b.filename)
            deduped.append(b)

    return deduped


def restore_from_backup(backup_path: Path, project_dir: Path) -> None:
    """Restore a project from a backup file.

    ZIP backups: extracts project.avc, scripts/, and assets/.
    Legacy .avc.bak: copies to project.avc only.
    """
    import shutil

    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    project_file = project_dir / "project.avc"

    if backup_path.suffix == ".zip":
        with zipfile.ZipFile(backup_path, "r") as zf:
            names = zf.namelist()

            # Extract project.avc
            if "project.avc" not in names:
                raise ValueError("Backup ZIP does not contain project.avc")
            with zf.open("project.avc") as src:
                project_file.write_bytes(src.read())

            # Extract scripts
            scripts_dir = project_dir / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            # Clear existing scripts
            for f in scripts_dir.glob("*.py"):
                f.unlink()
            for name in names:
                if name.startswith("scripts/") and name != "scripts/":
                    fname = Path(name).name
                    if fname:
                        with zf.open(name) as src:
                            (scripts_dir / fname).write_bytes(src.read())

            # Extract assets (clear existing first to remove orphans)
            assets_dir = project_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            for f in assets_dir.iterdir():
                if f.is_file():
                    f.unlink()
            for name in names:
                if name.startswith("assets/") and name != "assets/":
                    fname = Path(name).name
                    if fname:
                        with zf.open(name) as src:
                            (assets_dir / fname).write_bytes(src.read())

            # Restore persisted variable state
            if "state.json" in names:
                with zf.open("state.json") as src:
                    (project_dir / "state.json").write_bytes(src.read())

        log.info(f"Restored from ZIP backup: {backup_path.name}")

    elif backup_path.name.endswith(".avc.bak"):
        # Legacy .avc.bak — just copy to project.avc
        shutil.copy2(backup_path, project_file)
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
