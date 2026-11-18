"""THE rule for what a driver file may be called, and what a door asks of
the library before it names one.

``DRIVER_FILE_STEM`` is what the name of a driver file may be made of before
its extension or companion suffix: the name of a file a door writes or looks
up, which an upload, a bundle, or a driver installed before ids were held to
their pattern may give with capitals or ``-``. A new driver's id, which names
the file it is saved as, is held to the narrower id pattern (``driver_id``).
A name is always judged whole (``fullmatch``): under ``re.match`` a ``$`` lets
a trailing newline through.

Before a save names a file for a driver it asks two things of the library:
whether that file already holds another driver (``other_driver_in``: a save
never writes over one), and whether the driver's id is one the library
already serves (``id_installed``: a save that keeps such an id is not held to
the id pattern, since the devices on it name it by that id).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

DRIVER_FILE_STEM = r"[a-zA-Z0-9_\-]+"
DRIVER_FILE_NAME = re.compile(rf"{DRIVER_FILE_STEM}\.(avcdriver|py)")
_STEM = re.compile(DRIVER_FILE_STEM)


def is_driver_file_stem(text: Any) -> bool:
    """True when ``text`` may name a driver file before its extension."""
    return isinstance(text, str) and _STEM.fullmatch(text) is not None


def is_driver_file_name(name: Any) -> bool:
    """True when ``name`` may be a ``.avcdriver`` or ``.py`` driver file's name."""
    return isinstance(name, str) and DRIVER_FILE_NAME.fullmatch(name) is not None


def other_driver_in(path: Path | str, driver_id: str) -> str | None:
    """The id of the driver the file at ``path`` holds when it is not
    ``driver_id``; None when there is no file, it is that driver, or what it
    declares cannot be read. On a file system that ignores case, a file named
    in other capitals is the same file."""
    from openavc.drivers.driver_loader import driver_id_from_file

    path = Path(path)
    if not path.is_file():
        return None
    declared = driver_id_from_file(path)
    return declared if declared is not None and declared != driver_id else None


def id_installed(directories: Sequence[Path | str], driver_id: Any) -> bool:
    """Whether a file in the library already serves ``driver_id``, a refused
    copy too (``driver_loader.serving_file``)."""
    from openavc.drivers.driver_loader import serving_file

    if not isinstance(driver_id, str) or not driver_id:
        return False
    return serving_file(directories, driver_id, refused_too=True) is not None
