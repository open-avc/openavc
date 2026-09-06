"""Project-local theme files carried by library copies, bundles and backups.

Theme Studio stores custom themes beside project.avc, independently of the
theme id in UI settings. Moving only that id loses the actual design. Keep
every custom theme, including alternatives the author has not selected.
Themes are flat JSON files; built-in themes remain application resources.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from openavc.utils.paths import safe_path_within

_FILENAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\.json")


def zip_entries(themes_dir: Path) -> list[tuple[str, Path]]:
    """Collect only regular theme files directly inside the project tree."""
    if not themes_dir.is_dir() or themes_dir.is_symlink():
        return []
    return [
        (f"themes/{f.name}", f)
        for f in sorted(themes_dir.iterdir())
        if _FILENAME.fullmatch(f.name) and f.is_file() and not f.is_symlink()
    ]


def copy_tree(source: Path, destination: Path) -> None:
    """Carry the same theme files a bundle includes when copying a project."""
    for _, source_file in zip_entries(source):
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination / source_file.name)


def extract_from_zip(zf: zipfile.ZipFile, destination: Path) -> None:
    """Skip non-theme members and unsafe paths without flattening them."""
    for name in zf.namelist():
        if not name.startswith("themes/"):
            continue
        filename = name[len("themes/"):]
        if not _FILENAME.fullmatch(filename):
            continue
        target = safe_path_within(destination, filename)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(name))
