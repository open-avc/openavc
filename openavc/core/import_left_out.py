"""THE project file an import into the Project Library takes from a zip, and
THE account it gives of what it left out.

A copy in the Project Library is a project file with its scripts, images,
custom controls and custom themes, read from beside it in the zip
(``core/project_library``): at the top, the way an export and a project
backup write them, or in the folder that holds it, the way the backup an
update takes keeps a project (``projects/<name>/``, or
``project_unit.UPDATE_BACKUP_PROJECT`` for the project that ran when it was
kept outside the projects folder, which ``project_member`` takes first).

A zip can hold more than a copy keeps, and the import names each part of it
that it recognises and did not bring, so nothing is dropped without a word:

- the saved variable values beside the project file, and the device logins
  of a project backup or an update's backup (``core/backup_manager``,
  ``updater/backup``): a copy keeps no saved values, and a backup's logins
  open only on the system that took it, where restoring a project backup
  from Backups brings them back;
- every other project file in the zip, since one import brings one project.

Anything else (a backup's own record, the system's files in an update's
backup, what a design tool leaves in a zip) is passed over without a word.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

from openavc.core import project_unit
from openavc.core.backup_manager import ARCHIVE_LOGINS

#: Each entry beside a project file (``project_unit.BESIDE_PROJECT``) as a
#: warning names it.
WORDS = {
    project_unit.SCRIPTS_DIR: "scripts",
    project_unit.ASSETS_DIR: "images",
    project_unit.UI_DIR: "custom controls",
    project_unit.THEMES_DIR: "custom themes",
    project_unit.STATE_FILE: "saved variable values",
}

#: What an import reads from beside a project file.
_READ = (project_unit.SCRIPTS_DIR, *project_unit.FILE_TREES)

_LOGINS = "device logins"

#: The folders of a zip whose files are never a project file: what an import
#: reads beside one, the drivers and plugins an export bundles, the system's
#: own trees in an update's backup (``updater/backup``) and the copies macOS
#: adds when it zips a folder.
_NOT_PROJECTS = frozenset({
    *_READ, "drivers", "plugins", "driver_repo", "plugin_repo", "__MACOSX",
})


def is_project_file(name: str) -> bool:
    """Whether the zip member ``name`` is a project file: a ``.avc`` in no
    folder of ``_NOT_PROJECTS``, never macOS's ``._`` copy of one."""
    path = PurePosixPath(name)
    folders = set(path.parts[:-1])
    return path.suffix == ".avc" and not folders & _NOT_PROJECTS and not path.name.startswith("._")


def project_member(names: Sequence[str]) -> str | None:
    """The project file an import of a zip holding ``names`` brings: the one
    an update's backup keeps for the project that ran, when it holds one
    (``project_unit.UPDATE_BACKUP_PROJECT``), since its ``projects/`` folder
    may also hold a project the system no longer runs; otherwise the first.
    None when the zip holds none."""
    found = [name for name in names if is_project_file(name)]
    ran = [name for name in found if str(PurePosixPath(name).parent) == project_unit.UPDATE_BACKUP_PROJECT]
    return next(iter(ran or found), None)


def folder_of(project_member: str) -> str:
    """What each entry beside the project file ``project_member`` starts
    with in the zip: nothing at its top, ``<folder>/`` in a folder of it."""
    folder = str(PurePosixPath(project_member).parent)
    return "" if folder == "." else f"{folder}/"


def left_out(names: Sequence[str], project_member: str) -> list[str]:
    """The import's warning for each part of a zip holding ``names`` that the
    copy made from its project file ``project_member`` does not bring."""
    folder = folder_of(project_member)
    parts = [WORDS[entry] for entry in project_unit.BESIDE_PROJECT
             if entry not in _READ and _holds(names, folder + entry)]
    parts += [_LOGINS] if ARCHIVE_LOGINS in names else []
    kept = _not_kept(parts) if folder else _from_a_backup(parts)
    return [note for note in (kept, _other_projects(names, project_member)) if note]


def _holds(names: Sequence[str], entry: str) -> bool:
    """Whether the zip holds the file ``entry``, or a file below the folder."""
    return entry in names or any(name.startswith(f"{entry}/") and not name.endswith("/") for name in names)


def _from_a_backup(parts: list[str]) -> str:
    if not parts:
        return ""
    return (
        f"The {_listed(parts, 'the ')} in this file were left out. Restoring it from Backups in the "
        f"Program view, on the system that took it, brings the project back with them."
    )


def _not_kept(parts: list[str]) -> str:
    if not parts:
        return ""
    return f"The {_listed(parts, 'the ')} in this file were left out."


def _other_projects(names: Sequence[str], project_member: str) -> str:
    others = [name for name in names if is_project_file(name) and name != project_member]
    if not others:
        return ""
    verb = "was" if len(others) == 1 else "were"
    return (
        f"This file holds more than one project. Only {project_member} was imported; "
        f"{_listed(others)} {verb} left out."
    )


def _listed(parts: Sequence[str], article: str = "") -> str:
    """``parts`` as a sentence lists them, ``article`` before the last one
    when there are two or more."""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {article}{parts[-1]}"
