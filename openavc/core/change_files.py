"""THE rule for a file beside the project that a project change writes or
removes with it (a script's ``.py``): it moves with the change, under the
project's lock, and stays as it was when the change is not applied.

The change door takes one (``Engine.apply_project_edit(mutate, files=...)``)
and the change's ``mutate`` fills it:

- ``write`` puts a file in place at once, inside the change, so the running
  system can take it as the change is reconciled (a new script is loaded
  from its file). When the change is not applied, the file is put back: a
  new one is removed, and one that was already at that path gets its bytes
  and permissions back.
- ``remove`` waits until the project file holds the change. Until then the
  file is still there, which a failed change's undo needs (a deleted script
  is loaded again from it). A file that cannot be removed then is logged and
  left: the change is applied, and a file no record names is inert.

The door calls ``put_back`` when the change is not applied and ``settle``
once it is; each is logged on a failure and never raised, so the caller
hears what the change itself did.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openavc.utils.fileio import atomic_write_text
from openavc.utils.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _Replaced:
    """A file a ``write`` replaced: its bytes and permission bits."""

    data: bytes
    mode: int


@dataclass(frozen=True)
class _Written:
    """A file a ``write`` put in place, and what it replaced (None: nothing)."""

    path: Path
    replaced: _Replaced | None


class ChangeFiles:
    """The files one project change writes and removes (see the module)."""

    def __init__(self) -> None:
        self._written: list[_Written] = []
        self._removed: list[Path] = []

    def write(self, path: Path, text: str) -> None:
        """Put ``text`` at ``path`` now, its folder made if missing; put back
        when the change is not applied. Raises when the write fails, with
        nothing written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        replaced = _read(path)
        atomic_write_text(path, text)
        self._written.append(_Written(path, replaced))

    def remove(self, path: Path) -> None:
        """Remove ``path`` once the project file holds the change."""
        self._removed.append(path)

    def put_back(self) -> None:
        """The change was not applied: every file it wrote back as it was,
        last first, and nothing it would have removed is."""
        self._removed.clear()
        while self._written:
            written = self._written.pop()
            try:
                _restore(written)
            except OSError:
                log.error("A change that was not applied could not put %s back", written.path, exc_info=True)

    def settle(self) -> None:
        """The project file holds the change: the files it drops go."""
        self._written.clear()
        while self._removed:
            path = self._removed.pop(0)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("Project saved, but %s could not be removed", path, exc_info=True)


def _read(path: Path) -> _Replaced | None:
    """What is at ``path`` now, to put back; None when there is no file."""
    try:
        return _Replaced(path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    except FileNotFoundError:
        return None


def _restore(written: _Written) -> None:
    """``written.path`` as it was before the write: gone, or the bytes and
    permissions it held, replaced in one rename."""
    if written.replaced is None:
        written.path.unlink(missing_ok=True)
        return
    fd, tmp = tempfile.mkstemp(dir=str(written.path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(written.replaced.data)
        os.chmod(tmp, written.replaced.mode)
        os.replace(tmp, written.path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
