"""The project format's 0.19.0 step: a stamp for a condition's value that
names a key.

``core/project_migration.py`` runs it in its chain. Nothing in a project
moves; the step's docstring says what the version records.
"""

from __future__ import annotations

from openavc.core.project_update import MigrationNotes


def migrate_0_18_to_0_19(data: dict, notes: MigrationNotes | None = None) -> dict:
    """
    Migrate from 0.18.0 to 0.19.0 -- a condition's value can name a key.

    A version stamp: nothing in an existing project moves. What the stamp
    records is that a condition's value starting with `$` (`$var.wanted`) now
    compares with what that key holds, at a macro's Skip If, Conditional and
    Wait Until, a trigger's guards and a State Change trigger's own value, and
    a control's `visible_when` (`condition_eval.read_value`). Before 0.19.0 it was
    compared as the text, so `power == $var.wanted` never held and `!=` always
    did.

    The same shape as 0.14.0's press context: open a 0.19.0 project on a
    platform that reads the value as text and every such condition silently
    takes the other branch. The version is what says which meaning the project
    was written against. No saved project held one, so none changes meaning.
    """
    data["openavc_version"] = "0.19.0"
    return data
