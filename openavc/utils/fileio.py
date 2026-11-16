"""Atomic file-write helper shared by user-data writers, and the owner-only
rule for what holds a login.

Everything on the user-data path (project.avc, state.json, system.json,
themes, script sources, library projects) writes through this shape so a
crash mid-write leaves either the old file or the new one, never a
truncated mix.

A folder or file that holds a login is readable by the server's account only
on POSIX (``private_dir``, ``make_private``): 0700 for a folder, 0600 for a
file. On Windows it inherits the data folder's ACL, which the installer
restricts (``installer/secure-data-dir.bat``) and chmod cannot change, so
nothing is set there.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_FOLDER = stat.S_IRWXU
_FILE = stat.S_IRUSR | stat.S_IWUSR


def make_private(path: Path) -> bool:
    """``path``, a folder or a file, readable by its owner only on POSIX. A
    link is never followed: it is left as it is. False, with a warning in the
    log, when the mode cannot be set (a file another account owns); never
    raises, so what was being written goes ahead."""
    if os.name != "posix" or path.is_symlink():
        return False
    try:
        os.chmod(path, _FOLDER if path.is_dir() else _FILE)
    except OSError as exc:
        log.warning("Could not make %s readable by this account only: %s", path, exc)
        return False
    return True


def private_dir(path: Path) -> Path:
    """``path``, made if needed, readable by its owner only (``make_private``).
    The mode is set after ``mkdir``, whose own mode the umask masks and which
    leaves a folder that exists as it is. Raises OSError when the folder
    cannot be made."""
    path.mkdir(parents=True, exist_ok=True)
    make_private(path)
    return path


def atomic_write_text(path: Path, content: str, mode: int | None = None) -> None:
    """Write text to a file atomically via temp file + rename.

    The file is readable by its owner only (``mkstemp``), unless ``mode`` is
    given: it is applied to the temp file before the rename, so the file is
    never in place with any other permissions.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
