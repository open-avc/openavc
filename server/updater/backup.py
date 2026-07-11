"""
Pre-update backup system.

Creates a zip archive of user data (projects, drivers, plugins,
system.json) before applying an update, so it can be restored if
the update fails.
"""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def create_backup(
    data_dir: Path,
    current_version: str,
    project_path: Path | None = None,
) -> Path:
    """Create a pre-update backup of user data.

    Archives: projects/, driver_repo/, plugin_repo/, themes/, system.json,
    cloud.json. Stores in: {data_dir}/backups/pre-update-v{version}-{timestamp}.zip

    When ``project_path`` points outside ``data_dir`` (set via
    ``OPENAVC_PROJECT`` to keep project files on a different volume,
    typical for production deployments), the rglob of ``data_dir/projects``
    won't reach it. Its parent directory is archived under
    ``external-project/`` instead so state.json, scripts/, and assets/
    travel with the backup.

    Returns the path to the created backup file.
    """
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"pre-update-v{current_version}-{timestamp}.zip"
    backup_path = backup_dir / backup_name
    # Write to a temp name and atomically rename on success. A mid-write failure
    # (disk full, cancellation) then leaves only a *.zip.tmp that doesn't match
    # the pre-update-*.zip glob, so list_backups/cleanup_old_backups never count
    # a truncated archive as a real restore slot.
    tmp_path = backup_dir / f"{backup_name}.tmp"

    log.info("Creating pre-update backup: %s", backup_path)

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Back up projects directory
            projects_dir = data_dir / "projects"
            if projects_dir.exists():
                for file_path in projects_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(data_dir).as_posix()
                        zf.write(file_path, arcname)

            # Back up driver_repo directory (community-installed drivers)
            driver_repo_dir = data_dir / "driver_repo"
            if driver_repo_dir.exists():
                for file_path in driver_repo_dir.rglob("*"):
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

            # Back up the project file and its siblings when it lives outside
            # data_dir. Without this, OPENAVC_PROJECT users would lose project.avc,
            # state.json (persistent variables), scripts/, and assets/ on a
            # failed update.
            if project_path is not None and project_path.exists():
                external_dir = _project_external_dir(project_path, data_dir)
                if external_dir is not None:
                    for file_path in external_dir.rglob("*"):
                        if file_path.is_file():
                            rel = file_path.relative_to(external_dir).as_posix()
                            zf.write(file_path, f"external-project/{rel}")

        tmp_path.replace(backup_path)
    except BaseException:
        # Don't leave a half-written archive behind (it isn't a *.zip so it
        # wouldn't be restorable, but clean it up so temps don't accumulate).
        tmp_path.unlink(missing_ok=True)
        raise

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    log.info("Backup created: %s (%.1f MB)", backup_path, size_mb)
    return backup_path


def _project_external_dir(project_path: Path, data_dir: Path) -> Path | None:
    """Return the project's parent directory if it sits outside data_dir.

    Returns None when the project lives inside ``data_dir`` (already covered
    by the regular `projects/` rglob) so we don't archive everything twice.
    """
    try:
        project_path = project_path.resolve()
        data_dir = data_dir.resolve()
    except OSError:
        return None
    try:
        project_path.relative_to(data_dir)
    except ValueError:
        # Not a subpath of data_dir — external.
        return project_path.parent
    return None


def list_backups(data_dir: Path) -> list[dict]:
    """List available backups, newest first."""
    backup_dir = data_dir / "backups"
    if not backup_dir.exists():
        return []

    # Sort by mtime (when the archive was written), not filename. Filenames embed
    # the version, so a reverse-lexicographic sort orders "v0.9.0" ahead of the
    # newer "v0.13.0" ('9' > '1'), which is not chronological across a version bump.
    backups = []
    for path in sorted(
        backup_dir.glob("pre-update-*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        stat = path.stat()
        backups.append({
            "name": path.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return backups


def cleanup_old_backups(data_dir: Path, keep: int = 5) -> int:
    """Remove old backups, keeping the most recent `keep` files.

    Returns the number of backups removed.
    """
    backup_dir = data_dir / "backups"
    if not backup_dir.exists():
        return 0

    # Keep the chronologically newest `keep` by mtime. Sorting by filename would
    # order by embedded version string, so a newer "v0.13.0" backup could be evicted
    # while an older "v0.9.0" one survives the cross-version lexicographic sort.
    backups = sorted(
        backup_dir.glob("pre-update-*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old_backup in backups[keep:]:
        try:
            old_backup.unlink()
            removed += 1
            log.info("Removed old backup: %s", old_backup.name)
        except OSError as e:
            log.warning("Failed to remove old backup %s: %s", old_backup.name, e)
    return removed
