"""What a project's ``assets/`` tree is on the paths that move it.

An asset is an uploaded image, background or icon the project references by
name. Every door that *writes* one sanitizes to a bare filename, so the tree
is flat in practice — but "flat in practice" is not the same as "flat", and
the paths that MOVE the tree did not agree about it. Save, duplicate and
export carried a nested asset; a template seed and a bundle import flattened
it onto its basename; and backup creation used ``iterdir()``, so a nested
asset was **dropped from the backup entirely**, with nothing logged and
nothing failing. An asset in a subfolder therefore survived a duplicate,
survived an export, and then quietly did not exist in the next backup.

So the tree gets one home, the way ``ui/`` has one in :mod:`custom_ui`. The
two are deliberately not the same module: a custom control is *code* and its
tree is rule-bound (file types, caps, depth), while an asset is an opaque
blob whose rules are the ones any archive needs — stay inside the directory,
and don't drag in the noise a design tool leaves behind — plus its file type.

**The file type is the upload's** (:data:`ALLOWED_EXTENSIONS`, images and
sounds), asked by every door that brings a tree in from outside: an import,
a backup restore and a starter seed all extract through
:func:`extract_from_zip`. They used to check names only, so an imported
project could carry an ``.html`` page that the open asset route then served,
with no credential, from this server's own address. The list has only ever
grown (images from the first release, sounds added later), so no file a
release let somebody upload is refused on its way back in. An SVG also gets
the upload's content check (:func:`svg_problem`), for the same reason, and
the open route serves only these types.

Direction of the fix: every path becomes recursive.
The reverse (flatten everywhere) was available and rejected — flattening is
what loses data, and ``ui/`` next door already proves the folder-carrying
shape works.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from openavc.utils.logger import get_logger
from openavc.utils.paths import files_below, safe_path_within

log = get_logger(__name__)

ASSETS_DIR_NAME = "assets"

#: The file types an asset may be: what the upload takes
#: (``POST /api/projects/{id}/assets``), the listing shows, and an import,
#: restore or seed brings in. Compared lower-cased.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".ogg", ".m4a"})
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS


def is_asset_type(relpath: str) -> bool:
    """True when the file's extension is one an asset may have."""
    return Path(relpath).suffix.lower() in ALLOWED_EXTENSIONS


# An SVG is a document that can carry script. Opened at its own URL, the open
# asset route's answer runs on this server's address; drawn by an <img> it
# never runs. So an SVG is checked before it lands here by any door, not only
# the upload. The patterns are unchanged since the first release, so an SVG a
# release let somebody upload passes again on its way back in.
SVG_DANGEROUS_PATTERNS = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"<foreignObject[\s>]", re.IGNORECASE),
    re.compile(r"<iframe[\s>]", re.IGNORECASE),
    re.compile(r"@import", re.IGNORECASE),
    re.compile(r"<animate[^>]*attributeName\s*=\s*[\"']href", re.IGNORECASE),
    re.compile(r"data:text/html", re.IGNORECASE),
    re.compile(r"<style[\s>]", re.IGNORECASE),
    re.compile(r"<base[\s>]", re.IGNORECASE),
    re.compile(r"<embed[\s>]", re.IGNORECASE),
    re.compile(r"<object[\s>]", re.IGNORECASE),
    re.compile(r"xlink:href\s*=\s*[\"'](?!#)", re.IGNORECASE),
]


def svg_problem(content: bytes) -> str | None:
    """Why this SVG may not be an asset, as the upload says it; None when it may."""
    # Valid XML first: defusedxml refuses entity expansion (billion laughs).
    try:
        import defusedxml.ElementTree as ET
        ET.fromstring(content)
    except Exception:
        return "SVG is not valid XML."
    text = content.decode("utf-8", errors="replace")
    if any(pattern.search(text) for pattern in SVG_DANGEROUS_PATTERNS):
        return "SVG contains potentially unsafe content (scripts, styles, or event handlers)."
    return None


def _rejected(relpath: str) -> bool:
    """True for a member this tree will not take, for either of two reasons.

    They are separated on purpose. A dot component covers **both** the noise a
    file manager leaves (``.DS_Store``, and now a dot-folder too, since folders
    survive) and, incidentally, ``..`` — and an incidental defence is one that
    disappears the day somebody decides to allow dotfiles. So traversal is
    rejected by name here, and containment is re-checked against the real
    directory afterwards, since a symlink can move underneath us.
    """
    parts = [p for p in relpath.split("/") if p]
    if any(p in (".", "..") for p in parts):
        return True
    return any(p.startswith(".") for p in parts)


def zip_entries(
    assets_dir: Path, prefix: str = ASSETS_DIR_NAME
) -> list[tuple[str, Path]]:
    """``(archive path, file)`` pairs for writing the tree into an archive.

    Shared by backup creation and project export so the two cannot disagree
    about what an ``assets/`` tree contains — they did, and the backup was the
    one that was wrong.
    """
    return [
        (f"{prefix}/{f.relative_to(assets_dir).as_posix()}", f)
        for f in files_below(assets_dir)
    ]


def extract_from_zip(
    zf: zipfile.ZipFile, dest_dir: Path, prefix: str = ASSETS_DIR_NAME
) -> list[str]:
    """Extract the archive's ``assets/`` members into ``dest_dir``, folders intact.

    Returns the relative paths written. A member that escapes the directory —
    via ``..``, an absolute path, or a symlink resolving outside — is
    **skipped**, not fatal, matching how the ``ui/`` tree treats an archive it
    doesn't like: an export from another machine is allowed to carry junk, and
    refusing the whole project over one stray entry would be maddening.
    Skipping is also what makes a crafted archive harmless, since nothing
    outside the tree is ever written. A file of a type an asset may not be
    (:func:`is_asset_type`), and an SVG the upload would refuse
    (:func:`svg_problem`), are skipped the same way, with a warning.
    """
    written: list[str] = []
    marker = f"{prefix}/"
    for name in zf.namelist():
        if not name.startswith(marker) or name.endswith("/"):
            continue
        relpath = name[len(marker):].replace("\\", "/").strip("/")
        if not relpath or _rejected(relpath):
            continue
        if not is_asset_type(relpath):
            log.warning("Skipping an asset that is not an image or sound: %r", name)
            continue
        target = safe_path_within(dest_dir, relpath)
        if target is None:
            continue
        with zf.open(name) as src:
            data = src.read()
        if relpath.lower().endswith(".svg") and svg_problem(data):
            log.warning("Skipping an SVG asset the upload would refuse: %r", name)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(relpath)
    return written
