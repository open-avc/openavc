"""Filesystem path-safety helpers shared across the API and cloud-tool layers."""

import os
import re
from pathlib import Path

_SCRIPT_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+\.py$")

# A Windows junction is a folder link that is_symlink() does not report.
# os.path.isjunction arrived in Python 3.12; junctions exist only on Windows.
_is_junction = getattr(os.path, "isjunction", lambda _path: False)


def is_safe_script_filename(name: str) -> bool:
    """True when ``name`` is a bare Python script filename.

    Scripts live as flat files directly under a project's ``scripts/`` dir, so
    a valid ``file`` is a single basename like ``room_scripts.py`` — no
    directory separators, no ``..``, and a ``.py`` extension. This is stricter
    than :func:`safe_path_within`, which only blocks escaping the base dir: it
    also rejects nested subpaths and non-.py extensions, so an authoring
    surface can't drop a file into an unexpected subdir or with an unexpected
    type. Mirrors the ``.py``-basename discipline the driver endpoints enforce.
    """
    return bool(_SCRIPT_FILENAME_RE.match(name))


def safe_path_within(base: Path, candidate: str) -> Path | None:
    """Resolve ``candidate`` under ``base`` and confirm it stays inside.

    Returns the resolved absolute path when ``candidate`` is contained within
    ``base``; returns ``None`` when it escapes — via ``..``, an absolute path,
    or a symlink that resolves outside. Callers decide how to surface the
    rejection (HTTP 400, an AI-tool error dict, skip-the-zip-member, etc.).
    """
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return None
    return resolved


def passes_through_link(base: Path, relpath: str) -> bool:
    """True when ``relpath`` reaches a link on its way down from ``base``.

    A symlink or a junction at any step below ``base``: the file itself, or
    any folder on the way to it. ``base`` and the folders above it are not
    asked, so a base that is itself a link (a plugin folder linked in while
    it is developed) still works.

    Asked of the path as given. A resolved path has had its links followed
    already, so asking it whether it is a link always answers no.
    """
    current = base
    for part in Path(relpath).parts:
        current = current / part
        if is_link(current):
            return True
    return False


def files_below(base: Path) -> list[Path]:
    """Every regular file under ``base``, sorted, none of them reached by a link.

    A linked folder is not walked into and a linked file is left out, so a
    copy or an archive of ``base`` holds what is really in it and nothing a
    link points to elsewhere. ``rglob`` already skips a symlinked folder but
    walks into a junction, and keeps a symlinked file. ``base`` itself may be
    a link, as in :func:`passes_through_link`.
    """
    if not base.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        here = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not is_link(here / name)]
        for name in filenames:
            path = here / name
            if path.is_file() and not is_link(path):
                found.append(path)
    return sorted(found)


def is_link(path: Path) -> bool:
    """True when ``path`` is a symlink or a junction. Never raises: a path
    that cannot be read is not reported as a link."""
    return os.path.islink(path) or _is_junction(path)
