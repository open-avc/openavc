"""The community driver library, for the sweeps that read every shipped driver.

The library normally sits beside this repo in the workspace.
OPENAVC_DRIVERS_ROOT points somewhere else: an automated run clones it into a
subdirectory of the checkout, the only place a checkout step may write.

Which of the library's files are drivers is the library's own rule, its
``scripts/catalog_files.py`` (the files directly in its category folders), so
a sweep reads exactly the files the catalog publishes. That module is standard
library only and is loaded here by its path.
"""

from __future__ import annotations

import functools
import importlib.util
import os
from pathlib import Path
from types import ModuleType

DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT")
    or Path(__file__).resolve().parent.parent.parent / "openavc-drivers"
)


@functools.cache
def _catalog_files() -> ModuleType:
    path = DRIVERS_ROOT / "scripts" / "catalog_files.py"
    if not path.is_file():
        raise FileNotFoundError(
            f"The driver library at {DRIVERS_ROOT} has no scripts/catalog_files.py, "
            "which says which of its files are drivers. Update the library checkout."
        )
    spec = importlib.util.spec_from_file_location("_driver_library_catalog_files", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def driver_files() -> list[Path]:
    """The library's driver files (``.avcdriver`` and Python), or [] with no library here."""
    if not DRIVERS_ROOT.exists():
        return []
    return _catalog_files().driver_files(DRIVERS_ROOT)


def yaml_driver_files() -> list[Path]:
    """The library's ``.avcdriver`` files, or [] with no library here."""
    return [path for path in driver_files() if path.suffix == ".avcdriver"]


def python_driver_files() -> list[Path]:
    """The library's Python drivers (not their simulators or discovery companions)."""
    return [path for path in driver_files() if path.suffix == ".py"]
