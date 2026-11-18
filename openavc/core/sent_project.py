"""A whole project sent to this system to run: THE read of one.

The Programmer's Save and Import (``PUT /api/project``) and a project pushed
from the cloud each hand ``read_sent_project`` the document as it arrived.
It is refused when it carries no ``openavc_version``
(``ProjectVersionMissingError``), migrated through the format chain as a
load migrates a file (``core/project_migration.py``), repaired as a load is
when ``repair`` (an import, a push) and refused past a bound when not (a
save), and validated. What the migration changed comes back with it, for
the update notice.

The chain keys on ``openavc_version`` and reads a missing one as the oldest
format, so a current-format document without it would run every step:
every placement collapsed, every rem value divided again, the connections
rebuilt from where the oldest format kept them, then saved stamped current.
Every producer of a whole project (the Programmer, an export, the library,
a system the cloud read a project from) writes the field. Each door words
the refusal itself, since what to send instead depends on where the
document came from.
"""

from __future__ import annotations

from typing import Any

from openavc.core import project_migration
from openavc.core.project_loader import ProjectConfig
from openavc.core.project_update import MigrationNotes


class ProjectVersionMissingError(ValueError):
    """A whole project sent with no ``openavc_version``, refused before the
    format chain reads it."""


def read_sent_project(body: dict[str, Any], *, repair: bool) -> tuple[ProjectConfig, MigrationNotes]:
    """``body`` as this release runs it, and what its migration changed.
    Raises ``ProjectVersionMissingError`` for a document with no version,
    the models' ``ValidationError`` for one that is not a valid project, and
    whatever a migration step raises on a document it cannot read."""
    if "openavc_version" not in body:
        raise ProjectVersionMissingError("The project has no 'openavc_version'.")
    notes = MigrationNotes()
    migrated, _ = project_migration.migrate_project(body, notes, repair=repair)
    return ProjectConfig(**migrated), notes
