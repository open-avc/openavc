"""
Pre-update backup system.

Creates a zip archive of user data (projects, drivers, plugins,
system.json) before applying an update, so it can be restored if
the update fails. The zip is written and the old ones trimmed by the core
every backup shares (``utils/backup_zip``); which of a project's files at the
data root it carries is ``core/project_unit``'s list.

**A restore that cannot finish** (``restore_user_data``). The backup is read
through first, so one that cannot be opened or read changes nothing
(``BackupNotReadable``). A file it holds that cannot be written back is
reported once the rest are (``RestoreFailed``), and the automatic rollback
stops there (``rollback.restore_pre_update_data``); the next start tries the
same backup again and keeps what this attempt set aside (``ASIDE_RECORD``).

**The backup before a run.** ``back_up_first`` is the rule every update
and every rollback follows before it changes anything: the backup is made,
or the run stops (``BackupFailed``, a sentence that names the reason and
the remedy, never a path). A rollback stops too: it restores no project
data, so this zip is the only copy of the projects as the newer version
left them. Trimming the old backups after it is best-effort, so a trim
that fails never stops a run whose backup was made.

**Owner-only.** The data folder's ``backups/`` holds what the platform backs
up on its own: each pre-update zip (the projects tree, so a paired system's
cloud key in ``cloud.json``) and the Project Library's zip taken before its
logins moved (``core/project_library.py``). The folder and every zip in it are
readable by the server's account only (``utils/fileio.py``): each zip is
created owner-only, never changed afterwards, and a start makes what an
earlier version left there owner-only too (``make_backups_private``).

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

import itertools
import json
import logging
import os
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from openavc.core.project_unit import BESIDE_PROJECT
from openavc.utils.backup_zip import keep_newest, write_zip
from openavc.utils.fileio import atomic_write_text, make_private, private_dir
from openavc.utils.paths import files_below, is_link

log = logging.getLogger(__name__)

#: The archive member recording the links in each project tree, as
#: ``{"projects": {"<path below the tree>": "<where it leads>"}, ...}``.
#: Older backups have none, and are restored as they always were.
LINKS_MEMBER = "links.json"

#: What a run says when its backup could not be made, before the reason.
NOT_BACKED_UP = "the projects and settings could not be backed up first."
#: The reason when this module has no words for the failure.
SEE_THE_LOG = "The Programmer's Log has the details."


class BackupFailed(RuntimeError):
    """The backup before an update or a rollback could not be made; its
    text is the sentence the run ends with, after "Update failed: " or
    "Rollback failed: "."""


class BackupNotReadable(ValueError):
    """A pre-update backup that cannot be opened: nothing was restored from
    it, and nothing ever will be."""


class RestoreFailed(RuntimeError):
    """Files a pre-update backup holds that could not all be written back
    (``restore_user_data``): it wrote back what it could and stopped short.
    ``cause`` is the first error, which the rollback words
    (``rollback.restore_pre_update_data``)."""

    def __init__(self, cause: OSError) -> None:
        super().__init__(f"The pre-update backup could not all be written back: {cause}")
        self.cause = cause


def back_up_first(
    data_dir: Path,
    current_version: str,
    project_path: Path | None = None,
) -> Path:
    """The backup an update or a rollback takes before it changes
    anything (``create_backup``), then the old ones trimmed to the newest
    five. Returns the backup made; raises ``BackupFailed`` when it could not
    be made. A trim that fails is logged, and the run goes on."""
    from openavc.api.error_messages import worded_write_failure

    try:
        made = create_backup(data_dir, current_version, project_path=project_path)
    except Exception as exc:
        log.exception("The backup before the update or rollback could not be made")
        raise BackupFailed(f"{NOT_BACKED_UP} {worded_write_failure(exc) or SEE_THE_LOG}") from exc
    try:
        cleanup_old_backups(data_dir)
    except Exception:
        log.exception("The old pre-update backups could not be trimmed")
    return made


def create_backup(
    data_dir: Path,
    current_version: str,
    project_path: Path | None = None,
) -> Path:
    """Create a pre-update backup of user data.

    Archives: projects/, driver_repo/, plugin_repo/, themes/, system.json,
    cloud.json, panel_devices.json, device_logins.json (never its key).
    Stores in:
    {data_dir}/backups/pre-update-v{version}-{timestamp}.zip

    When ``project_path`` points outside ``data_dir`` (set via
    ``OPENAVC_PROJECT`` to keep project files on a different volume,
    typical for production deployments), the walk of ``data_dir/projects``
    won't reach it. Its parent directory is archived under
    ``external-project/`` instead so state.json, scripts/, and assets/
    travel with the backup. Links in either project tree are followed and
    recorded in ``links.json``; see the module docstring.

    A project directly in ``data_dir`` uses that same archive prefix, but
    only its named file and what ``core/project_unit`` lists beside it are
    included. The rest of the data root belongs to the system, not the
    project.

    Returns the path to the created backup file.
    """
    backup_dir = private_dir(data_dir / "backups")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"pre-update-v{current_version}-{timestamp}.zip"
    log.info("Creating pre-update backup: %s", backup_dir / backup_name)

    # In place whole or not at all, owner-only (utils/backup_zip), so a
    # truncated archive is never listed or restored.
    backup_path = write_zip(
        backup_dir, backup_name, lambda zf: _write_update_archive(zf, data_dir, backup_dir, project_path),
    )
    size_mb = backup_path.stat().st_size / (1024 * 1024)
    log.info("Backup created: %s (%.1f MB)", backup_path, size_mb)
    return backup_path


#: The files at the data root an update's backup carries. ``panel_devices.json``
#: and ``device_logins.json`` (never its key: the copy opens only on the system
#: it was sealed on) are carried like ``system.json``, and a rollback restores
#: none of them: an approval or a login should not roll back with the code.
_DATA_ROOT_FILES = ("system.json", "cloud.json", "panel_devices.json", "device_logins.json")


def _write_update_archive(
    zf: zipfile.ZipFile, data_dir: Path, backup_dir: Path, project_path: Path | None,
) -> None:
    """Every member of an update's backup (``create_backup``), written into
    ``zf``, with the links each project tree holds recorded last."""
    links = {"projects": _write_projects_tree(zf, data_dir, backup_dir)}
    _write_system_files(zf, data_dir)
    # Projects outside data_dir, and directly at its root, are not reached by
    # the projects/ walk. The data-root case selects only project entries,
    # never the supervisor, update cache or backups.
    if project_path is not None and project_path.exists():
        links["external-project"] = _backup_extra_project(zf, project_path, data_dir)
    recorded = {tree: found for tree, found in links.items() if found}
    if recorded:
        zf.writestr(LINKS_MEMBER, json.dumps(recorded, indent=2, sort_keys=True))


def _write_projects_tree(zf: zipfile.ZipFile, data_dir: Path, backup_dir: Path) -> dict[str, str]:
    """The ``projects/`` tree, links followed; returns the links it holds.
    User backups (backup_manager writes {project_dir}/backups/ inside it) are
    skipped: embedding up to 15 already-compressed backup zips per update
    multiplies disk and CPU cost for zero restore value."""
    projects_dir = data_dir / "projects"
    if not projects_dir.exists():
        return {}
    files, links = _project_files(projects_dir, backup_dir)
    for file_path in files:
        if not _in_user_backups(file_path, projects_dir):
            zf.write(file_path, file_path.relative_to(data_dir).as_posix())
    return links


def _write_system_files(zf: zipfile.ZipFile, data_dir: Path) -> None:
    """The community drivers, the themes and the plugins, without following
    a link: a rollback never restores these, and a linked plugin or driver
    is somebody's working copy that lives elsewhere. A project kept at the
    data root has its own themes here, which go into external-project/ with
    the project and come back with it. Then the files at the data root."""
    trees = [files_below(data_dir / tree) for tree in ("driver_repo", "themes", "plugin_repo")]
    root_files = [data_dir / name for name in _DATA_ROOT_FILES if (data_dir / name).exists()]
    for file_path in [*itertools.chain.from_iterable(trees), *root_files]:
        zf.write(file_path, file_path.relative_to(data_dir).as_posix())


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


def _walk_project_tree(root: Path, root_names: set[str] | None):
    """Walk selected entries without visiting the system's other directories."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        if Path(dirpath) == root and root_names is not None:
            dirnames[:] = sorted(root_names.intersection(dirnames))
            filenames = sorted(root_names.intersection(filenames))
        yield dirpath, dirnames, filenames


def _project_files(
    root: Path, backups: Path, root_names: set[str] | None = None,
) -> tuple[list[Path], dict[str, str]]:
    """The files a project tree shows, links followed, and the links themselves.

    Returns ``(files, links)``: every file as its path through the tree, and
    every link met on the way, as ``{path below root: where it leads}``, with
    ``"."`` for the tree itself when it is a link. A link is followed unless
    it would walk somewhere already covered or never wanted: back into a
    folder it sits in (a loop), into a folder another link already walked,
    into the tree itself or above it, or into the backups being written. Such
    a link is still recorded, so a rollback puts it back.

    ``root_names`` limits the walk to those immediate children, before any
    directory is visited. Used when project and system data share a root.
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

    for dirpath, dirnames, filenames in _walk_project_tree(root, root_names):
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


def _extra_project_tree(
    project_path: Path, data_dir: Path,
) -> tuple[Path | None, set[str] | None]:
    """Project tree needing the external-project archive, and its entry filter.

    An external directory is carried whole. The data root carries only the
    project's own entries (``core/project_unit``); both backup and rollback
    use this selection.
    Other locations inside data_dir retain the regular projects/ handling.
    """
    try:
        project_path = project_path.resolve()
        data_dir = data_dir.resolve()
    except OSError:
        return None, None
    if project_path.parent == data_dir:
        return data_dir, {project_path.name, *BESIDE_PROJECT}
    try:
        project_path.relative_to(data_dir)
    except ValueError:
        # Not a subpath of data_dir — external.
        return project_path.parent, None
    return None, None


def _backup_extra_project(zf: zipfile.ZipFile, project_path: Path, data_dir: Path) -> dict[str, str]:
    """Archive the selected extra project tree and return its recorded links."""
    root, root_names = _extra_project_tree(project_path, data_dir)
    if root is None:
        return {}
    files, links = _project_files(root, data_dir / "backups", root_names)
    for path in files:
        if not _in_user_backups(path, root):
            rel = path.relative_to(root).as_posix()
            zf.write(path, f"external-project/{rel}")
    return links


def _selected_external_members(members: list, root_names: set[str] | None) -> list:
    """Limit the external-project members to the data root's project entries.

    The restore writer still validates every selected member's full path.
    """
    if root_names is None:
        return members
    return [
        member for member in members
        if member.filename.removeprefix("external-project/").partition("/")[0] in root_names
    ]


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
    A project directly at the data root uses the same per-file restore,
    restricted to its own entries so system files are never rolled back.

    A link the backup recorded (``links.json``) that still leads where it did
    is kept a link: its files are restored through it rather than as a plain
    copy in its place. A backup without the record is restored as before.

    Returns True when anything was restored, False when the backup holds
    nothing to restore. Raises ``BackupNotReadable`` when the backup cannot
    be opened or a file in it does not read back as it was written: it is
    checked whole before anything is written, so nothing was. Raises
    ``RestoreFailed`` when a file it holds could not be written back, once
    the rest has been. The next attempt at the same backup keeps what this
    one set aside (``ASIDE_RECORD``).
    """
    with _opened(backup_path) as zf:
        job = _Restore(zf, backup_path.name, _recorded_links(zf))
        restored = _restore_trees(job, data_dir, project_path)
    if job.failed:
        raise RestoreFailed(job.failed[0])
    return restored


@dataclass
class _Restore:
    """One restore of a pre-update backup (``restore_user_data``): the open
    zip, the backup's name, which names what it sets aside
    (``ASIDE_RECORD``), the links it recorded in each tree, and each file it
    could not write back."""

    zf: zipfile.ZipFile
    backup: str
    links: dict[str, dict[str, str]]
    failed: list[OSError] = field(default_factory=list)


@dataclass
class _ProjectsTree:
    """The ``projects/`` tree a restore puts back: the archived files below
    ``projects/``, and the links the backup recorded in it."""

    job: _Restore
    data_dir: Path
    members: list

    @property
    def links(self) -> dict[str, str]:
        return self.job.links.get("projects", {})

    @property
    def live(self) -> Path:
        return self.data_dir / "projects"

    @property
    def aside(self) -> Path:
        return self.data_dir / "projects.pre-rollback"


def _restore_trees(job: _Restore, data_dir: Path, project_path: Path | None) -> bool:
    """The projects tree, then a project kept outside it, each put back from
    the backup; True when anything was."""
    members = [i for i in job.zf.infolist() if not i.is_dir()]
    restored = False
    projects = [i for i in members if i.filename.startswith("projects/")]
    if projects:
        restored |= _restore_projects_tree(_ProjectsTree(job, data_dir, projects))
    external = [i for i in members if i.filename.startswith("external-project/")]
    if external and project_path is not None:
        restored |= _restore_external_files(job, external, data_dir, project_path)
    return restored


def _opened(backup_path: Path) -> zipfile.ZipFile:
    """The pre-update backup, opened and read through once, so a file in it
    that does not read back as written is found before anything is written.
    Raises ``BackupNotReadable``."""
    try:
        zf = zipfile.ZipFile(backup_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise BackupNotReadable(f"Cannot open the pre-update backup {backup_path.name}: {e}") from e
    try:
        bad = zf.testzip()
    except (OSError, zipfile.BadZipFile, zlib.error, EOFError) as e:
        zf.close()
        raise BackupNotReadable(f"Cannot read the pre-update backup {backup_path.name}: {e}") from e
    if bad is not None:
        zf.close()
        raise BackupNotReadable(f"The pre-update backup {backup_path.name} is damaged at {bad}")
    return zf


#: Beside ``projects.pre-rollback``: the name of the backup whose restore set
#: it aside. A restore of that same backup tried again at the next start (a
#: rollback stops when a file could not be written back) keeps it, never
#: clearing it, since it holds what the failed update left; a restore of any
#: other backup clears it, as it clears what an earlier rollback set aside.
ASIDE_RECORD = "projects.pre-rollback.from"


def _aside_is_from(data_dir: Path, backup: str) -> bool:
    """Whether ``projects.pre-rollback`` was set aside restoring ``backup``."""
    try:
        return (data_dir / ASIDE_RECORD).read_text(encoding="utf-8").strip() == backup
    except OSError:
        return False


def _clear_aside_for(data_dir: Path, aside: Path, backup: str) -> None:
    """Make way for what restoring ``backup`` sets aside, and record it:
    what an earlier rollback set aside is cleared, what an earlier attempt
    at this same backup set aside is kept. Raises the ``OSError``."""
    if _aside_is_from(data_dir, backup):
        return
    _clear(aside)
    atomic_write_text(data_dir / ASIDE_RECORD, backup)


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

    Archives are self-written, but a member is placed only where its name
    says on every platform. A name with a drive or a root as either platform
    reads it, a ``..``, or a colon, a backslash or a NUL is refused wherever
    the rollback runs: on Windows ``/etc/x.py`` is not absolute but joins to
    the root of the current drive, ``D:x.py`` joined to a folder is a path on
    drive D:, a backslash is a separator the ``..`` check would not see on
    POSIX, and a colon deeper in makes a hidden stream. The writers OpenAVC puts files in
    a project with never use one. A file somebody copied into a project by
    hand on Linux or macOS under such a name is skipped with the warning
    below, and stays in the tree the rollback keeps aside.
    """
    rel = name[len(prefix):]
    if not rel:
        return None
    p = Path(rel)
    rooted = PurePosixPath(rel).anchor or PureWindowsPath(rel).anchor
    if rooted or ".." in p.parts or any(c in rel for c in ":\\\x00"):
        log.warning("Skipping unsafe backup member: %r", name)
        return None
    return p


def _restore_projects_tree(tree: _ProjectsTree) -> bool:
    """Swap data_dir/projects for the archived tree, keeping the old one aside.

    When ``projects/`` itself is a link the backup recorded, it is restored
    through the link instead of swapped: renaming it would leave a plain
    folder where the link was.
    """
    live, aside = tree.live, tree.aside
    if "." in tree.links and _still_leads_to(live, tree.links["."]):
        return _restore_tree_through_link(tree)

    if not _swapped_in(tree):
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
        _put_links_back(tree)

    # User backups are excluded from the archive — carry them over from the
    # displaced tree so a rollback doesn't lose them. Not from behind a link
    # still in the displaced tree: that folder is somewhere else, not ours.
    if aside.exists():
        _carry_user_backups(live, aside, copy=aside_is_link)

    log.warning("Restored projects/ from pre-update backup (previous tree kept at %s)", aside)
    return True


def _swapped_in(tree: _ProjectsTree) -> bool:
    """The archived tree in place at ``projects/``, the tree there kept at
    ``projects.pre-rollback``. True once it is, by this attempt or by an
    earlier attempt at the same backup, whose aside is kept; False, the
    error in the restore's failures, when it is not."""
    try:
        if _swapped_earlier(tree):
            return True
    except OSError as e:
        log.exception("Could not put projects/ back from %s", tree.aside)
        tree.job.failed.append(e)
        return False
    return _swap(tree)


def _swapped_earlier(tree: _ProjectsTree) -> bool:
    """Whether an earlier attempt at the same backup swapped the tree in
    already. One that could not put the projects back when its swap failed
    has them put back here first, for this attempt to swap again. Raises
    the ``OSError``."""
    if not (tree.aside.exists() and _aside_is_from(tree.data_dir, tree.job.backup)):
        return False
    if tree.live.exists():
        return True
    os.replace(tree.aside, tree.live)
    return False


def _swap(tree: _ProjectsTree) -> bool:
    """Stage the archived tree, set the live one aside and put the staged
    one in its place. False, the projects put back as they were and the
    error in the restore's failures, when any step fails."""
    live, aside = tree.live, tree.aside
    staging = tree.data_dir / "projects.restore-tmp"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        _extract(tree.job.zf, tree.members, "projects/", staging)
        _clear_aside_for(tree.data_dir, aside, tree.job.backup)
        if live.exists():
            os.replace(live, aside)
        os.replace(staging, live)
    except OSError as e:
        log.exception("Failed to restore projects/ from pre-update backup")
        tree.job.failed.append(e)
        _put_projects_back(live, aside)
        shutil.rmtree(staging, ignore_errors=True)
        return False
    return True


def _put_projects_back(live: Path, aside: Path) -> None:
    """The live tree moved back from ``aside`` when a failed swap displaced
    it without finishing. One that cannot be is logged, and is put back by
    the next attempt (``_swapped_earlier``)."""
    if live.exists() or not aside.exists():
        return
    try:
        os.replace(aside, live)
    except OSError:
        log.exception("Could not put original projects/ back after failed restore")


def _extract(zf: zipfile.ZipFile, members: list, prefix: str, dest: Path) -> None:
    """Every member below ``prefix`` written under ``dest``, an unsafe name
    skipped (``_member_relpath``). Raises the ``OSError``."""
    for member in members:
        rel = _member_relpath(member.filename, prefix)
        if rel is None:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _carry_user_backups(live: Path, aside: Path, *, copy: bool) -> None:
    """Each ``backups/`` folder in the displaced tree moved (copied, when
    the displaced tree is a link) into the restored one where it has none."""
    for backups_dir in _backups_dirs(aside):
        target = live / backups_dir.relative_to(aside)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if copy:
                shutil.copytree(backups_dir, target)
            else:
                shutil.move(str(backups_dir), str(target))
        except OSError as e:
            log.warning("Could not carry user backups %s over: %s", backups_dir, e)


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


def _put_links_back(tree: _ProjectsTree) -> None:
    """Put each recorded link back where the swap left a plain copy of it.

    The swap moved the live tree, links and all, to ``aside`` and put the
    archive in its place, so a linked folder came back as a plain folder:
    OpenAVC would go on using that copy while the linked location kept what
    the failed update wrote there. A link the backup recorded that still
    leads where it did is moved back, and its archived files are written
    through it, the versions they replace kept under ``aside``; a file that
    could not be goes in the restore's failures. A link an earlier attempt
    at the same backup moved back has its files written through it again. A
    link that is gone, leads elsewhere now, or cannot be reached keeps the
    copy, so the project is there either way.
    """
    links = tree.links
    for rel in sorted(links):
        if rel == "." or any(rel.startswith(f"{outer}/") for outer in links if outer != "."):
            continue  # the tree itself, or a link inside another: restored through that one
        rel_path = _member_relpath(f"projects/{rel}", "projects/")
        if rel_path is None:
            continue
        if _link_in_place(tree.live / rel_path, tree.aside / rel_path, rel, links[rel]):
            _write_through_link(tree, rel_path)
            log.warning("Put the link projects/%s back and restored its files through it", rel)


def _link_in_place(copy: Path, link: Path, rel: str, target: str) -> bool:
    """The recorded link ``projects/<rel>`` at ``copy``, where the swap left
    a plain copy: True once it is there, also when an earlier attempt put
    it there; False, the copy kept, when it cannot be moved back from
    ``link`` in the displaced tree."""
    if _still_leads_to(copy, target):
        return True
    if not _still_leads_to(link, target):
        log.warning(
            "Rollback kept a plain copy of projects/%s: its link is gone, "
            "leads elsewhere now, or cannot be reached", rel,
        )
        return False
    return _move_link_back(link, copy, rel)


def _move_link_back(link: Path, copy: Path, rel: str) -> bool:
    """``link`` moved to ``copy``, the plain copy there removed. False, the
    copy kept, when the move fails."""
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
        return False
    if held.is_dir():
        shutil.rmtree(held, ignore_errors=True)
    elif os.path.lexists(held):
        held.unlink()
    return True


def _write_through_link(tree: _ProjectsTree, rel_path: Path) -> None:
    """The archived files below the link ``projects/<rel_path>``, written
    through it, the versions they replace kept under ``aside``."""
    prefix = f"projects/{rel_path.as_posix()}"
    for info in tree.members:
        if info.filename != prefix and not info.filename.startswith(f"{prefix}/"):
            continue
        member = _member_relpath(info.filename, "projects/")
        if member is None:
            continue
        below = member.relative_to(rel_path)
        if _unrecorded_link(tree.live, rel_path / below, tree.links):
            log.warning("Not restoring %s through a link the backup did not record", info.filename)
            continue
        try:
            _write_through(tree.job.zf, info, tree.live / rel_path / below, tree.aside / rel_path / below)
        except OSError as e:
            log.exception("Could not restore %s through its link", info.filename)
            tree.job.failed.append(e)


def _restore_tree_through_link(tree: _ProjectsTree) -> bool:
    """Restore a ``projects/`` that is itself a link, file by file through it.

    The versions replaced are kept under ``projects.pre-rollback``, which an
    earlier attempt at the same backup leaves as it is (``ASIDE_RECORD``).
    Like an external project folder, files made after the backup stay where
    they are. A file that could not be written back goes in the restore's
    failures.
    """
    live, aside = tree.live, tree.aside
    try:
        _clear_aside_for(tree.data_dir, aside, tree.job.backup)
    except OSError as e:
        log.exception("Could not clear %s; projects/ was not restored", aside)
        tree.job.failed.append(e)
        return False
    for member in tree.members:
        rel = _member_relpath(member.filename, "projects/")
        if rel is None:
            continue
        if _unrecorded_link(live, rel, tree.links):
            log.warning("Not restoring %s through a link the backup did not record", member.filename)
            continue
        try:
            _write_through(tree.job.zf, member, live / rel, aside / rel)
        except OSError as e:
            log.exception("Failed to restore %s through the projects link", member.filename)
            tree.job.failed.append(e)
    log.warning(
        "Restored projects/ through its link from pre-update backup (replaced files kept at %s)",
        aside,
    )
    return True


def _restore_external_files(job: _Restore, members: list, data_dir: Path, project_path: Path) -> bool:
    """Overwrite external project files in place from the archive: the
    whole folder of a project kept outside ``data_dir``, or the project's own
    entries of one kept at its root (``_extra_project_tree``).

    A file reached through links the backup recorded, each still leading
    where it did, is written where it really lives, so a linked file stays a
    link rather than being replaced by a plain one. A file that could not be
    written goes in the restore's failures, and the rest are still written.
    """
    external_dir, root_names = _extra_project_tree(project_path, data_dir)
    if external_dir is None:
        return False
    links = job.links.get("external-project", {})
    restored = False
    for member in _selected_external_members(members, root_names):
        rel = _member_relpath(member.filename, "external-project/")
        if rel is None:
            continue
        target = external_dir / rel
        through = bool(links) and _has_link(external_dir, rel) and not _unrecorded_link(external_dir, rel, links)
        try:
            if through:
                restored |= _write_through(job.zf, member, target, None)
            else:
                _write_in_place(job.zf, member, target)
                restored = True
        except OSError as e:
            log.exception("Failed to restore external project file %s", target)
            job.failed.append(e)
    if restored:
        log.warning("Restored external project files from pre-update backup: %s", external_dir)
    return restored


def _write_in_place(zf: zipfile.ZipFile, member: zipfile.ZipInfo, target: Path) -> None:
    """One archived file written over ``target`` through a temporary file
    beside it, which a failure removes. Raises the ``OSError``."""
    tmp = target.parent / (target.name + ".restore-tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def make_backups_private(data_dir: Path) -> None:
    """The data folder's ``backups/`` and every file in it readable by the
    server's account only (the module docstring): what an earlier version
    wrote there under the umask. Run at every start. A link in it is left as
    it is, and a file whose mode cannot be set is logged and skipped."""
    backup_dir = data_dir / "backups"
    if not backup_dir.is_dir():
        return
    make_private(backup_dir)
    for path in sorted(backup_dir.iterdir()):
        if path.is_file() and not path.is_symlink():
            make_private(path)


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
    """Remove the oldest pre-update backups past the newest ``keep``, by when
    each was written (``utils/backup_zip.keep_newest``). Returns how many
    were removed."""
    return keep_newest(data_dir / "backups", "pre-update-*.zip", keep)
