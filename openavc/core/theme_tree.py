"""Project-local theme files carried by library copies, bundles and backups.

Theme Studio stores custom themes beside project.avc, independently of the
theme id in UI settings. Moving only that id loses the actual design. Keep
every custom theme, including alternatives the author has not selected.
Themes are flat JSON files; built-in themes remain application resources.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from openavc.core.themes import ID_PATTERN
from openavc.utils.paths import safe_path_within


def _is_theme_filename(name: str) -> bool:
    """A theme's file name: its id (the theme rule's) and ``.json``."""
    stem, dot, suffix = name.rpartition(".")
    return bool(dot) and suffix == "json" and ID_PATTERN.fullmatch(stem) is not None


def zip_entries(themes_dir: Path) -> list[tuple[str, Path]]:
    """Collect only regular theme files directly inside the project tree."""
    if not themes_dir.is_dir() or themes_dir.is_symlink():
        return []
    return [
        (f"themes/{f.name}", f)
        for f in sorted(themes_dir.iterdir())
        if _is_theme_filename(f.name) and f.is_file() and not f.is_symlink()
    ]


def copy_tree(source: Path, destination: Path) -> None:
    """Carry the same theme files a bundle includes when copying a project."""
    for _, source_file in zip_entries(source):
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination / source_file.name)


def extract_from_zip(zf: zipfile.ZipFile, destination: Path, prefix: str = "themes") -> None:
    """The archive's themes under ``prefix`` into ``destination``, skipping
    non-theme members and unsafe paths without flattening them."""
    marker = f"{prefix}/"
    for name in zf.namelist():
        if not name.startswith(marker):
            continue
        filename = name[len(marker):]
        if not _is_theme_filename(filename):
            continue
        target = safe_path_within(destination, filename)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(name))
