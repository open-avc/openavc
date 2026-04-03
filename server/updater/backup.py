"""
Pre-update backup system.

Creates a zip archive of user data (projects, drivers, system.json)
before applying an update, so it can be restored if the update fails.
"""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def create_backup(data_dir: Path, current_version: str) -> Path:
    """Create a pre-update backup of user data.

    Archives: projects/, drivers/, system.json, and any plugin configs.
    Stores in: {data_dir}/backups/pre-update-v{version}-{timestamp}.zip

    Returns the path to the created backup file.
    """
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"pre-update-v{current_version}-{timestamp}.zip"
    backup_path = backup_dir / backup_name

    log.info("Creating pre-update backup: %s", backup_path)

    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Back up projects directory
        projects_dir = data_dir / "projects"
        if projects_dir.exists():
            for file_path in projects_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(data_dir).as_posix()
                    zf.write(file_path, arcname)

        # Back up drivers directory
        drivers_dir = data_dir / "drivers"
        if drivers_dir.exists():
            for file_path in drivers_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(data_dir).as_posix()
                    zf.write(file_path, arcname)

        # Back up themes directory
        themes_dir = data_dir / "themes"
        if themes_dir.exists():
            for file_path in themes_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(data_dir).as_posix()
                    zf.write(file_path, arcname)

        # Back up plugin_repo directory
        plugin_dir = data_dir / "plugin_repo"
        if plugin_dir.exists():
            for file_path in plugin_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(data_dir).as_posix()
                    zf.write(file_path, arcname)

        # Back up system.json
        system_json = data_dir / "system.json"
        if system_json.exists():
            zf.write(system_json, "system.json")

        # Back up cloud.json if it exists
        cloud_json = data_dir / "cloud.json"
        if cloud_json.exists():
            zf.write(cloud_json, "cloud.json")

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    log.info("Backup created: %s (%.1f MB)", backup_path, size_mb)
    return backup_path


def list_backups(data_dir: Path) -> list[dict]:
    """List available backups, newest first."""
    backup_dir = data_dir / "backups"
    if not backup_dir.exists():
        return []

    backups = []
    for path in sorted(backup_dir.glob("pre-update-*.zip"), reverse=True):
        stat = path.stat()
        backups.append({
            "name": path.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
        })
    return backups


def cleanup_old_backups(data_dir: Path, keep: int = 5) -> int:
    """Remove old backups, keeping the most recent `keep` files.

    Returns the number of backups removed.
    """
    backup_dir = data_dir / "backups"
    if not backup_dir.exists():
        return 0

    backups = sorted(backup_dir.glob("pre-update-*.zip"), reverse=True)
    removed = 0
    for old_backup in backups[keep:]:
        try:
            old_backup.unlink()
            removed += 1
            log.info("Removed old backup: %s", old_backup.name)
        except OSError as e:
            log.warning("Failed to remove old backup %s: %s", old_backup.name, e)
    return removed
