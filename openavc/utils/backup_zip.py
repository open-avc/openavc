"""THE shared core of the platform's backup zips: a zip in place whole or not
at all, readable by the server's account only from its first byte
(``write_zip``), and the rotation that keeps the newest of one kind
(``keep_newest``).

Its writers: a project backup and the same backup written again with its
logins sealed (``core/backup_manager``), the backup before an update or a
rollback (``updater/backup``) and the Project Library's zip before its logins
move (``core/project_library``). What goes in each zip, what it is called and
how many are kept are each writer's own. Stdlib and ``utils/fileio`` only, so
the updater reads it without the project runtime.

A zip is written to a temporary file beside where it goes, named
``.<name>.<random>.tmp`` so that no list and no rotation takes it for a
backup, then renamed into place. ``mkstemp`` creates it owner-only and the
rename keeps that, so a backup is never in place readable by another
account. A write that fails, the disk full part way or the rename refused,
removes the temporary file and raises.

The rotation orders by when each zip was written, never by its name: a name
that holds a version sorts 0.9.0 after 0.13.0. A project backup and an
update's backup can share one folder (a project kept at the data root), so
each rotation reads only the names its own pattern matches.
"""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


def write_zip(
    folder: Path,
    name: str,
    fill: Callable[[zipfile.ZipFile], None],
    *,
    place: Callable[[Path], Path] | None = None,
) -> Path:
    """A zip written by ``fill`` and put in ``folder`` once it is complete
    (the module docstring). ``place`` renames the finished temporary file
    into its name and returns where it went, for a writer that picks the name
    only then (a project backup takes the first free one under the save
    lock); without it the zip goes to ``folder / name``, replacing one there.
    Returns the zip; raises whatever stopped it, with nothing left behind."""
    fd, tmp_name = tempfile.mkstemp(dir=str(folder), prefix=f".{name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            fill(zf)
        if place is not None:
            return place(tmp)
        target = folder / name
        os.replace(tmp, target)
        return target
    finally:
        _remove_leftover(tmp)


def _remove_leftover(tmp: Path) -> None:
    """The temporary file a write left when it stopped, removed; gone already
    once the zip is in place. One that cannot be removed is logged, and what
    stopped the write is what the writer hears."""
    try:
        tmp.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove the unfinished backup %s: %s", tmp.name, exc)


def keep_newest(folder: Path, pattern: str, keep: int, *, spare: Path | None = None) -> int:
    """Remove the zips in ``folder`` matching ``pattern`` past the newest
    ``keep``, by when each was written. ``spare`` is neither removed nor
    counted, so the folder holds one more while it is spared: the backup a
    restore is about to read, which is the oldest when somebody goes back the
    furthest. Paths are compared resolved, since a folder reached through a
    link names the same file two ways. A zip that cannot be removed is logged
    and left. Returns how many were removed."""
    candidates = list(folder.glob(pattern))
    if spare is not None:
        spared = spare.resolve()
        candidates = [path for path in candidates if path.resolve() != spared]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed = 0
    for old in candidates[keep:]:
        try:
            old.unlink()
        except OSError as exc:
            log.warning("Could not remove the old backup %s: %s", old.name, exc)
            continue
        removed += 1
        log.debug("Removed the old backup %s", old.name)
    return removed
