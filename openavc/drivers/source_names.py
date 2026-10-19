"""Pure filename selection shared by the loader and installed-source census.

These rules classify names, not Python contents or imported dependencies. Keep
filesystem glob/case behavior with the owner enumerating the directory.
"""
from pathlib import Path


COMPANION_SUFFIXES: tuple[str, ...] = ("_discovery.py", "_sim.py")


def is_driver_file(filepath: Path) -> bool:
    """Exclude conventional helpers and companion files from primary loading."""
    name = filepath.name
    return not name.startswith("_") and not name.endswith(COMPANION_SUFFIXES)
