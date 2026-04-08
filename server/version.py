"""
Version management for OpenAVC.

Single source of truth: pyproject.toml [project].version
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_version() -> str:
    """Read the version string from pyproject.toml.

    Reads pyproject.toml directly when available (development and source installs).
    Falls back to importlib.metadata for frozen (PyInstaller) builds where
    pyproject.toml is not present.
    """
    # Parse pyproject.toml directly — always accurate for source/dev environments
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject_path.exists():
        text = pyproject_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                # Parse: version = "0.1.0"
                _, _, value = stripped.partition("=")
                return value.strip().strip('"').strip("'")

    # Fallback: importlib.metadata (frozen/PyInstaller builds)
    if sys.version_info >= (3, 10):
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("openavc")
        except PackageNotFoundError:
            pass

    return "0.0.0"


# Module-level constant for convenient import
__version__ = get_version()
