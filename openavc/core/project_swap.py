"""THE swap of a project's files on disk for another project's, and the
"replace in progress" marker a start reads first.

A replace (``core/project_replace``), and a start's starter, restored
backup or Recovery Project (``core/project_recovery``, through
``project_replace.replace_files``), stage what comes in, in a
working folder beside the project file (``WORK_DIR``), then swap each entry
the project is made of (``core/project_unit``, the project file and the
start's notice, ``recovery.json``, included) by renames: the live entry is
moved aside into the working folder and the staged one moved into its place.
An entry the incoming project does not have is moved aside and nothing takes
its place. Every rename is undone, last first, when one fails or when the
caller asks (``Staged.swap_back``), and the working folder is removed once
the caller is done (``Staged.discard``).

The marker (``write_marker``) names the backup a replace took of the project
it replaces. It is on disk before the first rename and removed after the
last, so a power cut part way through leaves it there, and the next start
(``core/project_recovery.load_or_recover``) puts that backup back before it
reads anything else: the project before the replace, never a mix of two,
while that backup can be read.

Engine-free, so the start reads it before there is an engine.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openavc.core import project_unit
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: The working folder, beside the project file: ``new/`` holds what is
#: coming in, ``old/`` what the swap moved aside.
WORK_DIR = ".replacing"

#: The marker, beside the project file.
MARKER_NAME = "replace.json"

#: The start's notice about the project it had to load
#: (``core/project_recovery.MARKER_NAME``): not part of the project, so no
#: backup holds it, and a replace moves it aside with the project it is about.
RECOVERY_NOTICE = "recovery.json"


def entries(project_file: Path, *, beside: bool = True) -> tuple[str, ...]:
    """The names a replace of ``project_file`` swaps, in the order it swaps
    them: what sits beside it (``project_unit.BESIDE_PROJECT``) unless
    ``beside`` is False (a backup that holds the project file alone), the
    start's notice, and the project file last."""
    kept = project_unit.BESIDE_PROJECT if beside else ()
    return (*kept, RECOVERY_NOTICE, project_file.name)


def _rename_on_disk(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _sync_folder(folder: Path) -> None:
    """The folder's own entry list on disk, so the renames in it are kept
    through a power cut. Not every system opens a folder (Windows): there
    the renames are as durable as the system makes them."""
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def moved_aside(project_dir: Path) -> Path:
    """Where a swap moves the live entries it replaces, in its working
    folder: what a swap a power cut stopped leaves of the project before
    it, until the working folder is removed."""
    return project_dir / WORK_DIR / "old"


def _sync_file(path: Path) -> None:
    """``path``'s contents on disk, and its name in its folder. Opened for
    writing, which Windows asks of a file it flushes."""
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())
    _sync_folder(path.parent)


@dataclass
class Staged:
    """What is coming in, staged under ``WORK_DIR`` beside the live project,
    and the renames made so far, which ``swap_back`` undoes."""

    project_dir: Path
    names: tuple[str, ...]
    _done: list[tuple[Path, Path]] = field(default_factory=list)

    @property
    def work(self) -> Path:
        return self.project_dir / WORK_DIR

    @property
    def incoming(self) -> Path:
        return self.work / "new"

    @property
    def aside(self) -> Path:
        return moved_aside(self.project_dir)

    @property
    def partway(self) -> bool:
        """Whether renames are left made: after a ``swap_in``, all of them;
        after a failed swap whose undo also failed, some. False when the
        live entries are the ones before the swap."""
        return bool(self._done)

    def _rename(self, src: Path, dst: Path) -> None:
        _rename_on_disk(src, dst)
        self._done.append((src, dst))

    def swap_in(self) -> None:
        """Each entry in turn: the live one moved aside, the staged one moved
        into its place. A rename that fails undoes the ones made and raises."""
        self.aside.mkdir(parents=True, exist_ok=True)
        try:
            for name in self.names:
                live = self.project_dir / name
                if os.path.lexists(live):
                    self._rename(live, self.aside / name)
                if os.path.lexists(self.incoming / name):
                    self._rename(self.incoming / name, live)
        except OSError:
            log.exception("A project swap stopped part way; putting back the entries it moved")
            self.swap_back()
            raise
        _sync_folder(self.project_dir)

    def swap_back(self) -> None:
        """Every rename made, undone, last first: the live entries are the
        ones before the swap again. A rename that fails raises, and the
        marker, which the caller still holds, has the start put the backup
        back."""
        while self._done:
            src, dst = self._done.pop()
            _rename_on_disk(dst, src)
        _sync_folder(self.project_dir)

    def discard(self) -> None:
        """The working folder removed: after a swap, what it moved aside;
        after a swap back or none, what was staged. Never raises."""
        clear_leftovers(self.project_dir)


def stage(project_file: Path, project_bytes: bytes, fill: Callable[[Path], None] | None, *, beside: bool = True) -> Staged:
    """``project_bytes`` and what ``fill`` writes into the folder it is given
    (the scripts, the file trees, the saved values, each under its own name),
    staged beside ``project_file`` for a swap. With ``beside``, every
    directory the project has beside its file is staged, empty when ``fill``
    writes none, so a project that has none replaces the live one's with
    nothing. A failure removes what was staged and raises."""
    project_dir = project_file.parent
    clear_leftovers(project_dir)
    staged = Staged(project_dir, entries(project_file, beside=beside))
    try:
        staged.incoming.mkdir(parents=True)
        (staged.incoming / project_file.name).write_bytes(project_bytes)
        if beside:
            for name in (project_unit.SCRIPTS_DIR, *project_unit.FILE_TREES):
                (staged.incoming / name).mkdir()
            if fill is not None:
                fill(staged.incoming)
    except BaseException:
        staged.discard()
        raise
    return staged


def replace_on_disk(project_file: Path, project_bytes: bytes, fill: Callable[[Path], None] | None, *, beside: bool = True) -> None:
    """The project at ``project_file`` replaced by ``project_bytes`` and
    what ``fill`` writes (``stage``) where no project is running: staged,
    swapped and the working folder removed, with no marker. A swap that
    fails puts back what it moved and raises."""
    staged = stage(project_file, project_bytes, fill, beside=beside)
    try:
        staged.swap_in()
    finally:
        staged.discard()


def clear_leftovers(project_dir: Path) -> None:
    """The working folder a swap that finished, or never started its
    renames, left behind, removed. Never raises."""
    work = project_dir / WORK_DIR
    if os.path.lexists(work):
        shutil.rmtree(work, ignore_errors=True)


# --- The marker ---------------------------------------------------------------


def write_marker(project_dir: Path, backup: str | None) -> None:
    """The marker naming ``backup`` (``backup_manager.backup_id``), or none
    when there was no project file to back up, on disk before this returns,
    and the backup it names on disk before the marker is: a backup written
    a moment ago can still be only in memory, and a power cut would leave
    the next start a marker naming an empty file. Raises when either cannot
    be written: a replace with no marker does not start its renames."""
    if backup is not None:
        _sync_file(project_dir / backup)
    path = project_dir / MARKER_NAME
    fd, tmp = tempfile.mkstemp(dir=str(project_dir), prefix=".replace_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"backup": backup}, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
    _sync_folder(project_dir)


def read_marker(project_dir: Path) -> dict | None:
    """The marker as written, or None when there is none. One that cannot
    be read names no backup."""
    path = project_dir / MARKER_NAME
    if not os.path.lexists(path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning(f"The replace-in-progress marker could not be read: {error}")
        return {"backup": None}
    if not isinstance(data, dict) or not isinstance(data.get("backup"), (str, type(None))):
        return {"backup": None}
    return {"backup": data["backup"]}


def clear_marker(project_dir: Path) -> None:
    """The marker removed, on disk before this returns. Raises when it
    cannot be: the start then puts the backup back."""
    (project_dir / MARKER_NAME).unlink(missing_ok=True)
    _sync_folder(project_dir)
