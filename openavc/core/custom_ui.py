"""The rules for a project's ``ui/`` tree — custom controls the integrator writes.

One control is one folder: ``ui/room_map/index.html`` plus whatever CSS, JS,
images and fonts it needs. The tree sits beside ``scripts/`` and ``assets/`` in
the project directory.

Everything about *what may live there* is here, in one place, because four
doors write into that folder and they must agree: the IDE's editor, a
drag-and-drop upload, a ``.zip`` import, and a project import or backup
restore. A path rule spelled once at the API door would leave the archive
doors free to write anywhere.

Deliberately dependency-free (stdlib only) so ``core`` never has to reach into
``api`` for it. Serving is the other half and lives in ``api/static_files.py``:
this module says what may be *written*, that one says what a browser is told a
file *is*. The first is a subset of the second by construction, pinned by
``tests/test_custom_ui_files.py``.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

#: What may be written into ``ui/``: the file types a browser-side control is
#: actually built from. Notably absent are ``.py`` and ``.sh`` — this tree is
#: browser code, and the platform's own code has its own editors with their own
#: guards. Streaming-manifest types a plugin may serve are absent too: nothing
#: authors one by hand.
ALLOWED_EXTENSIONS = frozenset({
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".csv",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf",
    ".mp3", ".wav", ".ogg", ".mp4", ".webm", ".vtt",
})

#: Per-file and per-project caps. The cloud tunnel relays each file as its own
#: response under a 10 MB ceiling, so a file a panel can fetch on the LAN but
#: not remotely is a trap — 5 MB keeps every one of them well under it. The
#: tree total is deliberately small: these are controls, not a media library
#: (that is what ``assets/`` is for, with its own much larger quota).
MAX_FILE_SIZE = 5 * 1024 * 1024        # 5 MB per file
MAX_TOTAL_SIZE = 25 * 1024 * 1024      # 25 MB per project
MAX_FILES = 500                        # per project
MAX_PATH_DEPTH = 8

#: One path component: a normal name, no leading dot (which also rules out
#: ``..``, ``.git`` and ``.DS_Store``), no separators. Spaces are allowed
#: because people name a folder "Room Map"; everything else is deliberately
#: dull.
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\- ]*$")
_MAX_COMPONENT_LEN = 100

UI_DIR_NAME = "ui"


class CustomUIPathError(ValueError):
    """A path that may not be written into ``ui/``. The message is user-facing."""


def normalize_relpath(raw: str, *, require_extension: bool = True) -> str:
    """Validate a relative path inside ``ui/`` and return it in POSIX form.

    Structural rules only — containment is re-checked against the real
    directory before anything is written, since a symlink can move underneath
    us. Raises :class:`CustomUIPathError` naming what was wrong: this message
    is what an integrator reads when a drag-and-drop is refused.
    """
    cleaned = (raw or "").strip().replace("\\", "/").strip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts:
        raise CustomUIPathError("A file path is required.")
    if len(parts) > MAX_PATH_DEPTH:
        raise CustomUIPathError(f"Path is nested too deeply (max {MAX_PATH_DEPTH} levels).")
    for part in parts:
        if not _COMPONENT_RE.match(part) or len(part) > _MAX_COMPONENT_LEN:
            raise CustomUIPathError(
                f"'{part}' is not a valid file or folder name. Use letters, numbers, "
                "spaces, dots, dashes and underscores."
            )
    path = PurePosixPath(*parts)
    if require_extension and path.suffix.lower() not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise CustomUIPathError(
            f"'{path.name}' is not a file type custom UI can use. Allowed: {allowed}"
        )
    return path.as_posix()


def resolve_within(ui_dir: Path, relpath: str) -> Path:
    """Resolve a normalized relative path under ``ui_dir``.

    Raises :class:`CustomUIPathError` if it escapes the tree or lands on a
    symlink — the two things ``normalize_relpath`` cannot see, because they
    depend on what is actually on disk.
    """
    resolved = (ui_dir / relpath).resolve()
    try:
        resolved.relative_to(ui_dir.resolve())
    except ValueError:
        raise CustomUIPathError("That path is outside the project's custom UI folder.")
    if resolved.is_symlink():
        raise CustomUIPathError("Links are not allowed in custom UI files.")
    return resolved


def iter_files(ui_dir: Path) -> list[Path]:
    """Every real file in the tree, sorted, symlinks skipped."""
    if not ui_dir.is_dir():
        return []
    return [
        f for f in sorted(ui_dir.rglob("*"))
        if f.is_file() and not f.is_symlink()
    ]


def tree_totals(ui_dir: Path, *, excluding: Path | None = None) -> tuple[int, int]:
    """``(total bytes, file count)``, optionally ignoring one file.

    ``excluding`` is the file being overwritten: replacing a 1 MB file with a
    1 MB file must not count both.
    """
    total = 0
    count = 0
    for f in iter_files(ui_dir):
        if excluding is not None and f == excluding:
            continue
        total += f.stat().st_size
        count += 1
    return total, count


def zip_entries(ui_dir: Path, prefix: str = UI_DIR_NAME) -> list[tuple[str, Path]]:
    """``(archive path, file)`` pairs for writing the tree into an archive.

    Used by project export and backup creation, so both carry the folder
    structure rather than flattening it — a control is a folder, and a
    flattened one does not run.
    """
    return [
        (f"{prefix}/{f.relative_to(ui_dir).as_posix()}", f)
        for f in iter_files(ui_dir)
    ]


def extract_from_zip(
    zf: zipfile.ZipFile, dest_ui_dir: Path, prefix: str = UI_DIR_NAME
) -> list[str]:
    """Extract the archive's ``ui/`` members into ``dest_ui_dir``.

    Returns the relative paths written. Members that break the path rules, use
    a file type this tree does not take, or overrun a cap are **skipped**
    rather than failing the whole import: an archive from a design tool carries
    ``__MACOSX`` noise and a stray ``.DS_Store``, and refusing a project over
    those would be maddening. Skipping is also what makes a crafted archive
    harmless — nothing outside the tree is ever written.

    The declared sizes in an archive's directory can understate the real
    thing, so the bytes are counted as they come out.
    """
    written: list[str] = []
    unpacked = 0
    marker = f"{prefix}/"
    for info in zf.infolist():
        if info.is_dir() or not info.filename.startswith(marker):
            continue
        try:
            rel = normalize_relpath(info.filename[len(marker):])
            target = resolve_within(dest_ui_dir, rel)
        except CustomUIPathError:
            continue
        with zf.open(info) as src:
            data = src.read(MAX_FILE_SIZE + 1)
        if len(data) > MAX_FILE_SIZE:
            continue
        unpacked += len(data)
        if unpacked > MAX_TOTAL_SIZE or len(written) >= MAX_FILES:
            break
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(rel)
    return written
