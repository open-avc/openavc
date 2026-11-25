"""THE account an import into the Project Library gives of what it left out.

A copy in the Project Library is a project file with its scripts, images,
custom controls and custom themes, read from the top of the zip the way an
export writes them (``core/project_library``). A zip can hold more than a
copy keeps, and the import names each part of it that it recognises and did
not bring, so nothing is dropped without a word:

- the saved variable values and the device logins of a project backup
  (``core/backup_manager``): a copy keeps no saved values, and a backup's
  logins open only on the system that took it, where restoring the backup
  brings them back;
- a project file kept in a folder of the zip, as the backup an update takes
  holds it (``updater/backup``): only the project file is imported, and what
  sits beside it there (``core/project_unit``) is named;
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

#: What an import reads from beside a project file at the top of the zip.
_READ_AT_TOP = (project_unit.SCRIPTS_DIR, *project_unit.FILE_TREES)

_LOGINS = "device logins"

#: The folders of a zip whose files are never a project file: what an import
#: reads beside one, the drivers and plugins an export bundles, the system's
#: own trees in an update's backup (``updater/backup``) and the copies macOS
#: adds when it zips a folder.
_NOT_PROJECTS = frozenset({
    *_READ_AT_TOP, "drivers", "plugins", "driver_repo", "plugin_repo", "__MACOSX",
})


def is_project_file(name: str) -> bool:
    """Whether the zip member ``name`` is a project file: a ``.avc`` in no
    folder of ``_NOT_PROJECTS``, never macOS's ``._`` copy of one."""
    path = PurePosixPath(name)
    folders = set(path.parts[:-1])
    return path.suffix == ".avc" and not folders & _NOT_PROJECTS and not path.name.startswith("._")


def left_out(names: Sequence[str], project_member: str) -> list[str]:
    """The import's warning for each part of a zip holding ``names`` that the
    copy made from its project file ``project_member`` does not bring."""
    folder = str(PurePosixPath(project_member).parent)
    if folder == ".":
        logins = [_LOGINS] if ARCHIVE_LOGINS in names else []
        notes = [_from_a_backup(_beside(names, "", _READ_AT_TOP) + logins)]
    else:
        notes = [_from_a_folder(folder, _beside(names, f"{folder}/", ())), _logins_alone(names)]
    return [note for note in (*notes, _other_projects(names, project_member)) if note]


def _beside(names: Sequence[str], prefix: str, read: Sequence[str]) -> list[str]:
    """The words for each entry beside the project file, under ``prefix``,
    that the zip holds and the import does not ``read``."""
    return [WORDS[entry] for entry in project_unit.BESIDE_PROJECT
            if entry not in read and _holds(names, prefix + entry)]


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


def _from_a_folder(folder: str, parts: list[str]) -> str:
    if not parts:
        return ""
    return (
        f"Only the project file was imported, from this file's {folder} folder: the {_listed(parts)} "
        f"beside it were left out. A file made with Export brings a project with its files."
    )


def _logins_alone(names: Sequence[str]) -> str:
    if ARCHIVE_LOGINS not in names:
        return ""
    return "The device logins in this file were left out."


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
