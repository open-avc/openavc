"""What a load changed in a project an earlier release saved, said once.

The format chain (``core/project_migration.py``) brings a saved project up to
the format this release writes. A step that repairs a saved value a new rule
refuses says so as it works: one ``UpdateNote`` per value it changed, and one
per value it found and left for Validate. The load that ran the chain keeps
those notes in a record beside the project and publishes
``system.project_update_at``; the Programmer shows them as a notice that opens
Validate, until somebody dismisses it.

**Which loads keep notes.** Every door that brings a saved project in: the
boot's load and a reload from disk (a restored backup is one), an import into
the running project or into the library, an open from the library and a whole
project pushed from the cloud. A door that replaces the running project
(``replace``) clears the record first, so a notice always describes the project
that is running. Starter projects and the first boot's seed keep none: nobody
saved them.

**The record outlives the boot that wrote it, and only a person clears it.**
A project is migrated once and saved in the new format, so a notice lost to a
restart would never come back. A library entry keeps its own record from its
import until it is opened, when it becomes the running project's.

**A found note somebody dismissed is not raised again by a later load.** A
found note is about a value left as written, so every load finds it again (a
word a later release saved: ``core/saved_values.py``); a dismiss keeps the
notes it cleared, and a load that finds only those raises nothing. A changed
note always is: a load that rewrites a value again (a backup restored from
before the repair) says so. A project that replaces the running one starts
with no such list.

**A note never holds a secret.** The record is published as ``system.*``
state, which the cloud receives, so a note about the panel lock PIN says what
happened to it, never the digits.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openavc.utils.fileio import atomic_write_text
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore

log = get_logger(__name__)

# The record, beside project.avc (and in a library entry's folder until it opens).
MARKER_NAME = "project_update.json"

# When the record now standing was written; "" while there is none.
KEY_AT = "system.project_update_at"

# UpdateNote.kind values.
CHANGED = "changed"   # the load rewrote a saved value
FOUND = "found"       # the load left a saved value as written; Validate lists it

_KINDS = {CHANGED, FOUND}


@dataclass(frozen=True)
class UpdateNote:
    """One thing a load did to a saved project, or found in it.

    ``location`` is where it is, in the words Validate's report uses ("Main >
    volume_slider", "Panel Settings"); ``page_id`` and ``element_id`` name the
    page and control when it is on one, so the Programmer can go there.
    """

    kind: str
    message: str
    location: str
    page_id: str = ""
    element_id: str = ""


@dataclass
class MigrationNotes:
    """The notes the format chain's steps write while one load runs it."""

    notes: list[UpdateNote] = field(default_factory=list)

    def changed(self, message: str, location: str, *, page_id: str = "", element_id: str = "") -> None:
        self._add(UpdateNote(CHANGED, message, location, page_id, element_id))

    def found(self, message: str, location: str, *, page_id: str = "", element_id: str = "") -> None:
        self._add(UpdateNote(FOUND, message, location, page_id, element_id))

    def _add(self, note: UpdateNote) -> None:
        """A note said once: a format step and the repair every load makes can
        find the same thing in one load."""
        if note not in self.notes:
            self.notes.append(note)

    def __bool__(self) -> bool:
        return bool(self.notes)


def _marker_path(folder: Path) -> Path:
    return folder / MARKER_NAME


def record(folder: Path, notes: MigrationNotes) -> None:
    """Keep ``notes`` in ``folder``'s record. Notes from an earlier load that
    nobody dismissed stay ahead of them: both describe the project there. A
    note the record already holds is not added again (the same repair made
    twice: an update rolled back and installed again), nor a found note
    somebody dismissed (a word found at every load). Nothing new to note
    writes nothing.
    Never raises: a load that worked must still come up, and losing the notice
    is not worth losing the project."""
    earlier = list((read(folder) or {}).get("notes", []))
    dismissed = _dismissed(folder)
    added = [
        asdict(n) for n in notes.notes
        if asdict(n) not in earlier and not (n.kind == FOUND and asdict(n) in dismissed)
    ]
    if not added:
        return
    _write(folder, {
        "at": datetime.now(timezone.utc).isoformat(),
        "notes": earlier + added,
        "dismissed": dismissed,
    })


def _write(folder: Path, data: dict[str, Any]) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_marker_path(folder), json.dumps(data, indent=2))
    except OSError as e:
        log.warning(f"Could not write the project update record: {e}")


def _dismissed(folder: Path) -> list[dict[str, Any]]:
    """The notes a dismiss cleared from ``folder``'s record, as written."""
    try:
        data = json.loads(_marker_path(folder).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    kept = data.get("dismissed") if isinstance(data, dict) else None
    return [asdict(n) for n in map(_note, kept) if n is not None] if isinstance(kept, list) else []


def replace(folder: Path, notes: MigrationNotes) -> None:
    """The record for a project that replaced the one in ``folder``: its own
    notes, or none at all."""
    clear(folder)
    record(folder, notes)


def held(folder: Path) -> MigrationNotes:
    """The notes ``folder``'s record holds, left in it: a library entry's,
    handed to the running project once it opens (``clear`` then)."""
    data = read(folder) or {}
    return MigrationNotes([UpdateNote(**n) for n in data.get("notes", [])])


def read(folder: Path) -> dict[str, Any] | None:
    """The current record, or None when there is nothing to report.

    An unreadable or malformed record reads as nothing rather than as an
    error: it is a notice, and a broken notice must not become a second fault.
    """
    try:
        data = json.loads(_marker_path(folder).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning(f"Ignoring an unreadable project update record: {e}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
        return None
    notes = [asdict(n) for n in map(_note, data["notes"]) if n is not None]
    return {"at": str(data.get("at", "")), "notes": notes} if notes else None


def _note(raw: Any) -> UpdateNote | None:
    if not isinstance(raw, dict) or raw.get("kind") not in _KINDS:
        return None
    return UpdateNote(
        kind=raw["kind"],
        message=str(raw.get("message", "")),
        location=str(raw.get("location", "")),
        page_id=str(raw.get("page_id", "")),
        element_id=str(raw.get("element_id", "")),
    )


def clear(folder: Path) -> bool:
    """Remove the record. Returns True if there was one to remove."""
    path = _marker_path(folder)
    existed = path.exists()
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning(f"Could not remove the project update record: {e}")
    return existed


def publish(state: StateStore, project_dir: Path) -> None:
    """Publish when the running project's record was written ("" for none)."""
    data = read(project_dir) or {}
    state.set(KEY_AT, str(data.get("at", "")), source="system")


def dismiss(state: StateStore, project_dir: Path) -> bool:
    """Clear the running project's notice, keeping its notes as dismissed so
    a later load does not raise them again. Returns True if there was one."""
    standing = (read(project_dir) or {}).get("notes", [])
    if standing:
        _write(project_dir, {"at": "", "notes": [], "dismissed": _dismissed(project_dir) + standing})
    publish(state, project_dir)
    return bool(standing)
