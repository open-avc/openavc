"""
Pre-update backup system.

Creates a zip archive of user data (projects, drivers, plugins,
system.json) before applying an update, so it can be restored if
the update fails.

**Links.** A project tree (``projects/``, or a project kept outside the
data directory) is backed up as the project sees it: a linked folder or
file in it is followed, because it is the project's own data and the update
may rewrite it (a format upgrade saves the project as it loads). Each link
is also recorded in ``links.json`` with where it leads, so a rollback puts
it back as a link and restores its files through it, instead of leaving a
plain copy in its place. The other trees are never restored automatically,
so a link in them is left out, as an export leaves it out.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from openavc.utils.paths import files_below, is_link

log = logging.getLogger(__name__)

#: The archive member recording the links in each project tree, as
#: ``{"projects": {"<path below the tree>": "<where it leads>"}, ...}``.
#: Older backups have none, and are restored as they always were.
LINKS_MEMBER = "links.json"


def create_backup(
    data_dir: Path,
    current_version: str,
    project_path: Path | None = None,
) -> Path:
    """Create a pre-update backup of user data.

    Archives: projects/, driver_repo/, plugin_repo/, themes/, system.json,
    cloud.json, panel_devices.json. Stores in:
    {data_dir}/backups/pre-update-v{version}-{timestamp}.zip

    When ``project_path`` points outside ``data_dir`` (set via
    ``OPENAVC_PROJECT`` to keep project files on a different volume,
    typical for production deployments), the walk of ``data_dir/projects``
    won't reach it. Its parent directory is archived under
    ``external-project/`` instead so state.json, scripts/, and assets/
    travel with the backup. Links in either project tree are followed and
    recorded in ``links.json``; see the module docstring.

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

    links: dict[str, dict[str, str]] = {}
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Back up projects directory. User backups (backup_manager writes
            # {project_dir}/backups/ inside this tree) are skipped: embedding
            # up to 15 already-compressed backup zips per update multiplies
            # disk and CPU cost for zero restore value.
            projects_dir = data_dir / "projects"
            if projects_dir.exists():
                files, links["projects"] = _project_files(projects_dir, backup_dir)
                for file_path in files:
                    if not _in_user_backups(file_path, projects_dir):
                        arcname = file_path.relative_to(data_dir).as_posix()
                        zf.write(file_path, arcname)

            # The community drivers, the themes and the plugins, without
            # following a link: a rollback never restores these, and a linked
            # plugin or driver is somebody's working copy that lives elsewhere.
            for tree in ("driver_repo", "themes", "plugin_repo"):
                for file_path in files_below(data_dir / tree):
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

            # The approved panel devices. Backed up like system.json; a
            # rollback restores projects/ only and leaves this in place,
            # because an approval should not roll back with the code.
            panel_devices = data_dir / "panel_devices.json"
            if panel_devices.exists():
                zf.write(panel_devices, "panel_devices.json")

            # Back up the project file and its siblings when it lives outside
            # data_dir. Without this, OPENAVC_PROJECT users would lose project.avc,
            # state.json (persistent variables), scripts/, and assets/ on a
            # failed update.
            if project_path is not None and project_path.exists():
                external_dir = _project_external_dir(project_path, data_dir)
                if external_dir is not None:
                    files, links["external-project"] = _project_files(external_dir, backup_dir)
                    for file_path in files:
                        if not _in_user_backups(file_path, external_dir):
                            rel = file_path.relative_to(external_dir).as_posix()
                            zf.write(file_path, f"external-project/{rel}")

            recorded = {tree: found for tree, found in links.items() if found}
            if recorded:
                zf.writestr(LINKS_MEMBER, json.dumps(recorded, indent=2, sort_keys=True))

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


def _real(path: Path | str) -> str:
    """The path with every link followed, in the form two paths compare in."""
    return os.path.normcase(os.path.realpath(path))


def _contains(outer: str, inner: str) -> bool:
    """True when the real path ``inner`` is ``outer`` or lies inside it."""
    return inner == outer or inner.startswith(outer.rstrip(os.sep) + os.sep)


def _project_files(root: Path, backups: Path) -> tuple[list[Path], dict[str, str]]:
    """The files a project tree shows, links followed, and the links themselves.

    Returns ``(files, links)``: every file as its path through the tree, and
    every link met on the way, as ``{path below root: where it leads}``, with
    ``"."`` for the tree itself when it is a link. A link is followed unless
    it would walk somewhere already covered or never wanted: back into a
    folder it sits in (a loop), into a folder another link already walked,
    into the tree itself or above it, or into the backups being written. Such
    a link is still recorded, so a rollback puts it back.
    """
    files: list[Path] = []
    links: dict[str, str] = {}
    if is_link(root):
        links["."] = os.path.realpath(root)
    root_real = _real(root)
    backups_real = _real(backups)
    walked_links: set[str] = set()

    def note(path: Path) -> None:
        links[path.relative_to(root).as_posix()] = os.path.realpath(path)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        here = Path(dirpath)
        here_real = _real(here)
        kept = []
        for name in dirnames:
            path = here / name
            if not is_link(path):
                kept.append(name)
                continue
            note(path)
            real = _real(path)
            if (
                _contains(real, here_real)
                or real in walked_links
                or _contains(real, root_real)
                or _contains(backups_real, real)
                or _contains(real, backups_real)
            ):
                continue
            walked_links.add(real)
            kept.append(name)
        dirnames[:] = sorted(kept)
        for name in filenames:
            path = here / name
            if is_link(path):
                note(path)
            if path.is_file() and not _contains(backups_real, _real(path)):
                files.append(path)
    return sorted(files), links


def _project_external_dir(project_path: Path, data_dir: Path) -> Path | None:
    """Return the project's parent directory if it sits outside data_dir.

    Returns None when the project lives inside ``data_dir`` (already covered
    by the regular `projects/` walk) so we don't archive everything twice.
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

    A link the backup recorded (``links.json``) that still leads where it did
    is kept a link: its files are restored through it rather than as a plain
    copy in its place. A backup without the record is restored as before.

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
        links = _recorded_links(zf)

        if project_members:
            restored |= _restore_projects_tree(
                zf, project_members, data_dir, links.get("projects", {}),
            )

        if external_members and project_path is not None:
            external_dir = _project_external_dir(project_path, data_dir)
            if external_dir is not None:
                restored |= _restore_external_files(
                    zf, external_members, external_dir, links.get("external-project", {}),
                )

    return restored


def _recorded_links(zf: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    """What the backup's ``links.json`` recorded; nothing for a backup without one."""
    try:
        data = json.loads(zf.read(LINKS_MEMBER).decode("utf-8"))
    except KeyError:
        return {}
    except (ValueError, OSError, zipfile.BadZipFile) as e:
        log.warning("Ignoring an unreadable %s in the pre-update backup: %s", LINKS_MEMBER, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(tree): {str(rel): str(target) for rel, target in found.items()}
        for tree, found in data.items() if isinstance(found, dict)
    }


def _still_leads_to(path: Path, target: str) -> bool:
    """True when ``path`` is a link to ``target`` and the target is there."""
    return is_link(path) and os.path.exists(path) and _real(path) == os.path.normcase(target)


def _unrecorded_link(base: Path, rel: Path, links: dict[str, str]) -> bool:
    """True when ``rel`` below ``base`` passes through a link the backup did
    not record, or one that leads elsewhere now. Nothing is written through
    one: it was made after the backup, and where it leads was not backed up."""
    current = base
    for depth, part in enumerate(rel.parts, start=1):
        current = current / part
        if is_link(current):
            key = "/".join(rel.parts[:depth])
            if key not in links or not _still_leads_to(current, links[key]):
                return True
    return False


def _has_link(base: Path, rel: Path) -> bool:
    current = base
    for part in rel.parts:
        current = current / part
        if is_link(current):
            return True
    return False


def _crc32(path: Path) -> int:
    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            crc = zlib.crc32(chunk, crc)
    return crc


def _write_through(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, displaced: Path | None,
) -> bool:
    """Write one archived file to where ``dest`` really lives.

    Written at the end of any link on the way, so a linked file stays a link.
    A file that already holds exactly these bytes is left alone (a synced or
    network folder is not rewritten for nothing), and the version about to be
    replaced is copied to ``displaced`` first when one is given. Returns True
    when the file was written.
    """
    real = Path(os.path.realpath(dest))
    if real.is_file():
        if real.stat().st_size == info.file_size and _crc32(real) == info.CRC:
            return False
        if displaced is not None:
            displaced.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(real, displaced)
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.parent / (real.name + ".restore-tmp")
    try:
        with zf.open(info) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, real)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return True


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


def _restore_projects_tree(
    zf: zipfile.ZipFile, members: list, data_dir: Path, links: dict[str, str] | None = None,
) -> bool:
    """Swap data_dir/projects for the archived tree, keeping the old one aside.

    ``links`` is what the backup recorded for this tree. When ``projects/``
    itself is one of them, it is restored through the link instead of
    swapped: renaming it would leave a plain folder where the link was.
    """
    links = links or {}
    live = data_dir / "projects"
    aside = data_dir / "projects.pre-rollback"
    staging = data_dir / "projects.restore-tmp"

    if "." in links and _still_leads_to(live, links["."]):
        return _restore_tree_through_link(zf, members, live, aside, links)

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
        _clear(aside)
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

    # A projects/ that was itself a link the backup did not record (made since,
    # or a backup from before links were recorded) was swapped like a folder:
    # the link is now projects.pre-rollback, and what it leads to is not ours
    # to rearrange. Its links stay where they are, and its project backups
    # are copied over rather than moved out of it.
    aside_is_link = is_link(aside)
    if aside_is_link:
        log.warning(
            "projects/ was a link the backup did not record; restored it as a plain "
            "folder, and the link is kept at %s", aside,
        )
    else:
        _put_links_back(zf, members, live, aside, links)

    # User backups are excluded from the archive — carry them over from the
    # displaced tree so a rollback doesn't lose them. Not from behind a link
    # still in the displaced tree: that folder is somewhere else, not ours.
    if aside.exists():
        for backups_dir in _backups_dirs(aside):
            target = live / backups_dir.relative_to(aside)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if aside_is_link:
                    shutil.copytree(backups_dir, target)
                else:
                    shutil.move(str(backups_dir), str(target))
            except OSError as e:
                log.warning("Could not carry user backups %s over: %s", backups_dir, e)

    log.warning("Restored projects/ from pre-update backup (previous tree kept at %s)", aside)
    return True


def _clear(path: Path) -> None:
    """Remove ``path``: a link as itself, never what it leads to, or a folder tree."""
    if is_link(path):
        try:
            os.unlink(path)
        except OSError:
            os.rmdir(path)  # a junction, or a folder symlink on Windows
    elif path.exists():
        shutil.rmtree(path)


def _backups_dirs(root: Path) -> list[Path]:
    """Every folder named ``backups`` under ``root``, without going into a link."""
    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not is_link(here / name)]
        found.extend(here / name for name in dirnames if name == "backups")
    return found


def _put_links_back(
    zf: zipfile.ZipFile, members: list, live: Path, aside: Path, links: dict[str, str],
) -> None:
    """Put each recorded link back where the swap left a plain copy of it.

    The swap moved the live tree, links and all, to ``aside`` and put the
    archive in its place, so a linked folder came back as a plain folder:
    OpenAVC would go on using that copy while the linked location kept what
    the failed update wrote there. A link the backup recorded that still
    leads where it did is moved back, and its archived files are written
    through it, the versions they replace kept under ``aside``. A link that
    is gone, leads elsewhere now, or cannot be reached keeps the copy, so the
    project is there either way.
    """
    for rel in sorted(links):
        if rel == "." or any(rel.startswith(f"{outer}/") for outer in links if outer != "."):
            continue  # the tree itself, or a link inside another: restored through that one
        rel_path = _member_relpath(f"projects/{rel}", "projects/")
        if rel_path is None:
            continue
        link, copy = aside / rel_path, live / rel_path
        if not _still_leads_to(link, links[rel]):
            log.warning(
                "Rollback kept a plain copy of projects/%s: its link is gone, "
                "leads elsewhere now, or cannot be reached", rel,
            )
            continue
        held = copy.with_name(f"{copy.name}.restore-copy")
        try:
            if os.path.lexists(copy):
                os.replace(copy, held)
            copy.parent.mkdir(parents=True, exist_ok=True)
            os.replace(link, copy)
        except OSError:
            log.exception("Could not put the link projects/%s back; kept the restored copy", rel)
            if os.path.lexists(held) and not os.path.lexists(copy):
                try:
                    os.replace(held, copy)
                except OSError:
                    log.exception("Could not put the restored copy of projects/%s back", rel)
            continue
        if held.is_dir():
            shutil.rmtree(held, ignore_errors=True)
        elif os.path.lexists(held):
            held.unlink()
        prefix = f"projects/{rel_path.as_posix()}"
        for info in members:
            if info.filename != prefix and not info.filename.startswith(f"{prefix}/"):
                continue
            below = Path(info.filename[len(prefix):].lstrip("/"))
            if _unrecorded_link(live, rel_path / below, links):
                log.warning("Not restoring %s through a link the backup did not record", info.filename)
                continue
            try:
                _write_through(zf, info, copy / below, aside / rel_path / below)
            except OSError:
                log.exception("Could not restore %s through its link", info.filename)
        log.warning("Put the link projects/%s back and restored its files through it", rel)


def _restore_tree_through_link(
    zf: zipfile.ZipFile, members: list, live: Path, aside: Path, links: dict[str, str],
) -> bool:
    """Restore a ``projects/`` that is itself a link, file by file through it.

    The versions replaced are kept under ``aside``. Like an external project
    folder, files made after the backup stay where they are.
    """
    try:
        _clear(aside)
    except OSError:
        log.exception("Could not clear %s; projects/ was not restored", aside)
        return False
    for member in members:
        rel = _member_relpath(member.filename, "projects/")
        if rel is None:
            continue
        if _unrecorded_link(live, rel, links):
            log.warning("Not restoring %s through a link the backup did not record", member.filename)
            continue
        try:
            _write_through(zf, member, live / rel, aside / rel)
        except OSError:
            log.exception("Failed to restore %s through the projects link", member.filename)
    log.warning(
        "Restored projects/ through its link from pre-update backup (replaced files kept at %s)",
        aside,
    )
    return True


def _restore_external_files(
    zf: zipfile.ZipFile, members: list, external_dir: Path, links: dict[str, str] | None = None,
) -> bool:
    """Overwrite external project files in place from the archive.

    A file reached through links the backup recorded, each still leading
    where it did, is written where it really lives, so a linked file stays a
    link rather than being replaced by a plain one.
    """
    links = links or {}
    restored = False
    for member in members:
        rel = _member_relpath(member.filename, "external-project/")
        if rel is None:
            continue
        target = external_dir / rel
        if links and _has_link(external_dir, rel) and not _unrecorded_link(external_dir, rel, links):
            try:
                restored |= _write_through(zf, member, target, None)
            except OSError:
                log.exception("Failed to restore external project file %s", target)
            continue
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
