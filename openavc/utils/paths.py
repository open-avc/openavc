"""Filesystem path-safety helpers shared across the API and cloud-tool layers."""

import re
from pathlib import Path

_SCRIPT_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+\.py$")


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
