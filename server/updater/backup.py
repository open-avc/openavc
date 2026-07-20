"""
Pre-update backup system.

Creates a zip archive of user data (projects, drivers, plugins,
system.json) before applying an update, so it can be restored if
the update fails.
"""

from __future__ import annotations

import logging
import os
import shutil
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
            # Back up projects directory. User backups (backup_manager writes
            # {project_dir}/backups/ inside this tree) are skipped: embedding
            # up to 15 already-compressed backup zips per update multiplies
            # disk and CPU cost for zero restore value.
            projects_dir = data_dir / "projects"
            if projects_dir.exists():
                for file_path in projects_dir.rglob("*"):
                    if file_path.is_file() and not _in_user_backups(file_path, projects_dir):
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
                        if file_path.is_file() and not _in_user_backups(file_path, external_dir):
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


def _in_user_backups(file_path: Path, root: Path) -> bool:
    """True when ``file_path`` sits under a user-backup directory below ``root``.

    backup_manager keeps user backups at ``{project_dir}/backups/`` inside the
    projects tree (and the external project dir); nothing else on the user-data
    path creates a ``backups`` directory — scripts and assets are flat.
    """
    return "backups" in file_path.relative_to(root).parts[:-1]


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


def restore_user_data(
    data_dir: Path,
    backup_path: Path,
    project_path: Path | None = None,
) -> bool:
    """Restore project data from a pre-update backup zip.

    Used by automatic rollback so code and data return to the pre-update
    snapshot together: the rolled-back code may predate the running version's
    project-format migrations, and a project file left in the newer shape
    would be written back mixed-shape and then skip re-migration after the
    next upgrade.

    The ``projects/`` tree is restored by staged swap — the displaced tree is
    kept at ``projects.pre-rollback`` so nothing is destroyed. User-backup
    subtrees (``{project_dir}/backups/``) are not archived, so they are
    carried over from the displaced tree. An external project directory
    (``OPENAVC_PROJECT`` outside data_dir, archived under
    ``external-project/``) is restored by per-file overwrite instead of a
    swap — that directory can be a mount point that cannot be renamed.

    Returns True when anything was restored, False otherwise.
    """
    try:
        zf = zipfile.ZipFile(backup_path)
    except (OSError, zipfile.BadZipFile) as e:
        log.error("Cannot open pre-update backup %s: %s", backup_path, e)
        return False

    restored = False
    with zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        project_members = [i for i in members if i.filename.startswith("projects/")]
        external_members = [
            i for i in members if i.filename.startswith("external-project/")
        ]

        if project_members:
            restored |= _restore_projects_tree(zf, project_members, data_dir)

        if external_members and project_path is not None:
            external_dir = _project_external_dir(project_path, data_dir)
            if external_dir is not None:
                restored |= _restore_external_files(zf, external_members, external_dir)

    return restored


def _member_relpath(name: str, prefix: str) -> Path | None:
    """Relative path of a zip member below ``prefix``, or None if unsafe.

    Archives are self-written, but never extract a traversal/absolute name.
    """
    rel = name[len(prefix):]
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        log.warning("Skipping unsafe backup member: %s", name)
        return None
    return p


def _restore_projects_tree(zf: zipfile.ZipFile, members: list, data_dir: Path) -> bool:
    """Swap data_dir/projects for the archived tree, keeping the old one aside."""
    live = data_dir / "projects"
    aside = data_dir / "projects.pre-rollback"
    staging = data_dir / "projects.restore-tmp"

    shutil.rmtree(staging, ignore_errors=True)
    try:
        for member in members:
            rel = _member_relpath(member.filename, "projects/")
            if rel is None:
                continue
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

        # Swap: displaced tree survives at projects.pre-rollback.
        if aside.exists():
            shutil.rmtree(aside)
        if live.exists():
            os.replace(live, aside)
        os.replace(staging, live)
    except OSError:
        log.exception("Failed to restore projects/ from pre-update backup")
        # Put the live tree back if the swap displaced it without finishing.
        if not live.exists() and aside.exists():
            try:
                os.replace(aside, live)
            except OSError:
                log.exception("Could not put original projects/ back after failed restore")
        shutil.rmtree(staging, ignore_errors=True)
        return False

    # User backups are excluded from the archive — carry them over from the
    # displaced tree so a rollback doesn't lose them.
    if aside.exists():
        for backups_dir in aside.rglob("backups"):
            if not backups_dir.is_dir():
                continue
            target = live / backups_dir.relative_to(aside)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(backups_dir), str(target))
            except OSError as e:
                log.warning("Could not carry user backups %s over: %s", backups_dir, e)

    log.warning("Restored projects/ from pre-update backup (previous tree kept at %s)", aside)
    return True


def _restore_external_files(
    zf: zipfile.ZipFile, members: list, external_dir: Path
) -> bool:
    """Overwrite external project files in place from the archive."""
    restored = False
    for member in members:
        rel = _member_relpath(member.filename, "external-project/")
        if rel is None:
            continue
        target = external_dir / rel
        tmp = target.parent / (target.name + ".restore-tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(tmp, target)
            restored = True
        except OSError:
            log.exception("Failed to restore external project file %s", target)
            tmp.unlink(missing_ok=True)
    if restored:
        log.warning("Restored external project files from pre-update backup: %s", external_dir)
    return restored


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
