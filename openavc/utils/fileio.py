"""Atomic file-write helper shared by user-data writers.

Everything on the user-data path (project.avc, state.json, system.json,
themes, script sources, library projects) writes through this shape so a
crash mid-write leaves either the old file or the new one, never a
truncated mix.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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
