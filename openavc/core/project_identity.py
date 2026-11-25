"""THE rule for which project a project file is: whether one coming in is
the project running (``same_project``), and the stamp that tells two apart
(``ProjectMeta.uuid``).

A project's ``id`` is a slug of the name it was first given and never
changes after, and the starter project and the templates carry fixed ones,
so two rooms built from one of them share it. The stamp is a random uuid
made when a project is made (``new_stamp``): a blank project, a copy opened
from the library, a duplicate in the library, the Recovery Project, and a
project file loaded without one (``core/project_loader.load_project``: the
starter a first start copies in, and every project saved before the stamp
existed). A change that keeps the files beside the project, an edit or a
cloud push, keeps the running project's stamp (``keep_running``), and so
does a reload or a restore of the running project's file saved without one;
a project that comes in with files of its own brings its own stamp.

A file saved before the stamp existed names none. It is the running
project when its ``id`` and its ``created`` stamp are the running
project's, unless that ``created`` stamp is empty or one a release shipped
(``SHIPPED_CREATED``), which every room built from that starter or template
carries: those prove nothing, and the file is another project.

The bare ``.avc`` import asks it (``core/project_replace.imported``): the
same project keeps the files beside it, another sets them aside.
"""

from __future__ import annotations

import uuid

from openavc.core.project_loader import ProjectConfig, ProjectMeta

#: The ``created`` stamps the bundled starter project and the templates
#: carry, which every room built from one of them shares. A new template
#: with a fixed ``created`` stamp adds it here
#: (``tests/test_project_import_files.py`` holds the shipped files to it).
SHIPPED_CREATED = frozenset({
    "2026-01-01T00:00:00.000000",  # the starter project a first start copies in
    "2026-04-06T00:00:00",  # the templates
})


def new_stamp() -> str:
    """A stamp for a project being made."""
    return str(uuid.uuid4())


def same_project(running: ProjectMeta | None, incoming: ProjectMeta) -> bool:
    """Whether ``incoming`` is the project ``running`` describes: the same
    stamp, or, for a file saved before stamps, the same ``id`` and the same
    ``created`` stamp, one no release shipped."""
    if running is None:
        return False
    if incoming.uuid:
        return incoming.uuid == running.uuid
    created = incoming.created
    return (
        bool(created)
        and created not in SHIPPED_CREATED
        and created == running.created
        and incoming.id == running.id
    )


def stamp_missing(project: ProjectConfig, running: ProjectMeta | None = None) -> bool:
    """``project`` given a stamp when it has none, in place; whether it was.
    The stamp is ``running``'s when ``project`` is that project (a reload of
    its file, a restore of a backup taken before stamps), else a new one."""
    if project.project.uuid:
        return False
    project.project.uuid = _running_stamp(running, project.project) or new_stamp()
    return True


def _running_stamp(running: ProjectMeta | None, incoming: ProjectMeta) -> str:
    """``running``'s stamp when ``incoming`` is that project, else empty."""
    if running is None or not same_project(running, incoming):
        return ""
    return running.uuid


def keep_running(running: ProjectConfig | None, incoming: ProjectConfig) -> None:
    """``incoming`` given the running project's stamp, in place: a change
    that keeps the files beside the project is still that project, whatever
    stamp its document names or leaves out."""
    if running is not None and running.project.uuid:
        incoming.project.uuid = running.project.uuid
