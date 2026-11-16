"""THE record of the driver files in the library that did not load, and why.

A driver file that is in a driver folder but did not load (its definition
fails the check, its Python does not import, it defines no driver class) is
recorded here with its reasons, by the code that loads library files in place:
the start-up pass, a project bundle's driver install and the two reload doors
(``driver_loader.load_library_definition`` / ``load_library_python_driver``).
A change that is refused before it reaches the folder (an install, an update,
an import) is answered with its reasons and recorded nowhere: the file in the
folder is still the one it was.

Every reader that says a driver is missing asks here first, so a file that is
there says "installed but did not load" with its reasons instead of "not
installed": the device page (``core/installed_equipment.missing_driver_reason``,
which a device waiting for its driver carries as ``orphan_reason``), the
Installed list, the Code view's list, a project import and the AI's tools.

A file that did load can still carry notices: what the author's doors refuse
in it (a value of the wrong kind, a key the contract does not declare), which a
door that loads a driver says instead of refusing it
(``avcdriver_semantic.check_driver_definition``). Those are recorded here too,
by the same code, and read by the Installed list beside a refusal's reasons
(``load_notices``).

A record holds only while the file is as it was when it was refused: its
modification time and size are kept, and a file that has since changed or gone
reads as having no record. Any door that writes the file (a save, an install,
an uninstall) so ends it without being asked to.

Stdlib only, so the core modules can import it at module scope.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Refusal:
    """One driver file that did not load: where it is, the id it declares
    (its file name when no id can be read), and why, in sentences a person
    can act on."""

    path: Path
    driver_id: str
    reasons: tuple[str, ...]
    stamp: tuple[int, int] | None

    @property
    def detail(self) -> str:
        """The reasons as one line."""
        return "; ".join(self.reasons)


_refused: dict[str, Refusal] = {}
# Files that loaded, with their notices (``Refusal.reasons``).
_noticed: dict[str, Refusal] = {}


def _key(path: Path | str) -> str:
    return os.path.realpath(path)


def _stamp(path: Path | str) -> tuple[int, int] | None:
    """The file as it is now (its modification time and size), or None when
    it is not a file."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def record(path: Path | str, driver_id: str, reasons: tuple[str, ...] | list[str]) -> None:
    """Record that the driver file at ``path`` did not load, and why."""
    real = Path(_key(path))
    _refused[str(real)] = Refusal(real, driver_id, tuple(reasons), _stamp(real))


def clear(path: Path | str) -> None:
    """The driver file at ``path`` loaded (or is gone): no record."""
    _refused.pop(_key(path), None)


def record_notices(
    path: Path | str, driver_id: str, notices: tuple[str, ...] | list[str]
) -> None:
    """The driver file at ``path`` loaded with these notices (none clears
    them)."""
    real = Path(_key(path))
    if notices:
        _noticed[str(real)] = Refusal(real, driver_id, tuple(notices), _stamp(real))
    else:
        _noticed.pop(str(real), None)


def load_notices(path: Path | str) -> list[str]:
    """What the author's doors refuse in the driver file at ``path``, which
    loaded anyway, while it is unchanged (``load_notices`` on the Installed
    list); empty when it has none."""
    noticed = _noticed.get(_key(path))
    if noticed is None or _stamp(noticed.path) != noticed.stamp:
        return []
    return list(noticed.reasons)


def refusal_of(path: Path | str) -> Refusal | None:
    """Why the driver file at ``path`` did not load, while it is unchanged."""
    refusal = _refused.get(_key(path))
    if refusal is None or _stamp(refusal.path) != refusal.stamp:
        return None
    return refusal


def refusal_for(driver_id: str) -> Refusal | None:
    """Why a driver file declaring ``driver_id`` did not load, while it is
    unchanged. The one recorded last, which is the user's own copy when a
    built-in and an installed file share the id (the start-up pass reads the
    built-ins first)."""
    found = None
    for refusal in list(_refused.values()):
        if refusal.driver_id == driver_id and refusal_of(refusal.path) is refusal:
            found = refusal
    return found


def load_error(path: Path | str) -> str | None:
    """Why the driver file at ``path`` did not load, as a listing shows it
    (``load_error`` on the Installed list, the Code view's list and the AI's
    installed drivers); None when it has no record."""
    refusal = refusal_of(path)
    return refusal.detail if refusal is not None else None


def not_loaded_sentence(driver_id: str) -> str | None:
    """What a reader says of a driver whose file is installed but did not
    load; None when no file declaring it was refused."""
    refusal = refusal_for(driver_id)
    if refusal is None:
        return None
    return f"Driver '{driver_id}' is installed but did not load: {refusal.detail}"


def forget_all() -> None:
    """Drop every record (tests)."""
    _refused.clear()
    _noticed.clear()
