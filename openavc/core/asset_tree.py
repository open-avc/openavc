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
blob whose only rules are the ones any archive needs — stay inside the
directory, and don't drag in the noise a design tool leaves behind.

Direction of the fix, settled in backlog §139: every path becomes recursive.
The reverse (flatten everywhere) was available and rejected — flattening is
what loses data, and ``ui/`` next door already proves the folder-carrying
shape works.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from openavc.utils.paths import safe_path_within

ASSETS_DIR_NAME = "assets"


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
    if not assets_dir.is_dir():
        return []
    return [
        (f"{prefix}/{f.relative_to(assets_dir).as_posix()}", f)
        for f in sorted(assets_dir.rglob("*"))
        if f.is_file() and not f.is_symlink()
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
    outside the tree is ever written.
    """
    written: list[str] = []
    marker = f"{prefix}/"
    for name in zf.namelist():
        if not name.startswith(marker) or name.endswith("/"):
            continue
        relpath = name[len(marker):].replace("\\", "/").strip("/")
        if not relpath or _rejected(relpath):
            continue
        target = safe_path_within(dest_dir, relpath)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src:
            target.write_bytes(src.read())
        written.append(relpath)
    return written
