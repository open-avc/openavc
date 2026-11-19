"""THE way a project's own drivers come in: the drivers a project bundle
carries (a ``.zip`` export, a starter's ``bundle.zip``), each brought into
the driver folder the way every other driver is.

**One change per driver.** A bundle's ``drivers/`` folder is read as drivers,
each with the companions it owns (``driver_loader.companion_names``: a Python
driver's ``_sim.py`` and ``_discovery.py``, the discovery file a YAML driver
names), and each goes in as one change through ``drivers/driver_change.admit``:
the platform floor its file declares, a staged copy the loader must take, then
the swap and the register. A refused driver leaves nothing of itself in the
folder, and the rest still go in.

**A project never replaces a driver here** (``install``). A driver whose file
is already in the folder, or whose id a driver here already serves, is kept as
it is, its companions too; a companion whose driver the bundle does not carry
is left out. Only a starter's refresh at startup replaces anything
(``refresh_starter``), and only a copy byte-identical to the one its old
bundle unpacked, which proves nobody has edited it since.

**What each driver came to** is recorded (``BundledDrivers``) for the door's
answer: an import's and an open's warnings say why a driver was left out. The
door that installs them holds their ids (``driver_change.holding``) from its
first step to its announcement, ``declared_ids`` naming them before anything
is read by running it; the routes in ``api/routes/project.py`` do, around the
synchronous steps in ``core/project_library.py``. The starter refresh holds
nothing: it runs at startup, before any door can change a driver.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection

from openavc.drivers.driver_change import DriverChange, DriverRefused, Gates, admit
from openavc.drivers.driver_file_name import is_driver_file_name
from openavc.drivers.driver_loader import (
    COMPANION_SUFFIXES,
    DRIVER_EXTENSION,
    companion_names,
    declared_driver_info,
)
from openavc.drivers.registry import is_driver_registered
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.project_library")

#: The sentence beneath a bundled driver the loader refused, its reasons after it.
_DID_NOT_LOAD = "Its file did not load"


@dataclass(frozen=True)
class BundledDriver:
    """One driver a bundle carries: its driver file's name, every file it
    brings (the driver file and the companions it owns), the ones that are
    optional (a Python driver's simulator and discovery files), and the id its
    driver file declares, read without running it (its file's name when none
    can be read)."""

    main: str
    files: dict[str, bytes]
    optional: tuple[str, ...]
    declared_id: str


@dataclass
class BundledDrivers:
    """What one bundle's install did with the drivers it carries: the ids it
    installed, and for each it left out, the sentence saying why, by the id
    its driver file declares. One kept as it was is neither."""

    installed: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)

    def refuse(self, driver_id: str, why: str) -> None:
        self.refused[driver_id] = (
            f"Driver '{driver_id}' came with this project but was not installed: {why}"
        )
        log.warning("Bundled driver '%s' was not installed: %s", driver_id, why)


# --- reading a bundle -------------------------------------------------------------


def bundled_drivers(zf: zipfile.ZipFile) -> list[BundledDriver]:
    """Each driver in a bundle's ``drivers/`` folder, with the companions it
    owns. A private ``_`` helper, a file no driver file name can be
    (``driver_file_name``), and a companion whose driver the bundle does not
    carry are left out."""
    entries = _driver_files(zf)
    named = {
        companion for name, content in entries.items() if name.endswith(DRIVER_EXTENSION)
        for companion in companion_names(name, content)
    }
    drivers = [
        _driver(name, entries) for name in sorted(entries)
        if not name.endswith(COMPANION_SUFFIXES) and name not in named
    ]
    carried = {name for driver in drivers for name in driver.files}
    for name in sorted(set(entries) - carried):
        log.warning("Skipping bundled driver companion %s: the bundle does not carry its driver", name)
    return drivers


def declared_ids(zf: zipfile.ZipFile) -> list[str]:
    """The ids the drivers in a bundle declare: what a door holds while it
    installs them (``admit`` claims one a file turns out to have instead)."""
    return [driver.declared_id for driver in bundled_drivers(zf)]


def _driver_files(zf: zipfile.ZipFile) -> dict[str, bytes]:
    """The files in a bundle's ``drivers/`` folder a driver could be called,
    by name."""
    entries: dict[str, bytes] = {}
    for member in zf.infolist():
        name = _member_name(member)
        if name is not None:
            entries[name] = zf.read(member)
    return entries


def _member_name(member: zipfile.ZipInfo) -> str | None:
    """A member's file name when it is a file in ``drivers/`` that is not a
    private helper and a driver file could be called; None otherwise."""
    if member.is_dir() or not member.filename.startswith("drivers/"):
        return None
    name = Path(member.filename).name
    if name.startswith("_"):
        return None
    if not is_driver_file_name(name):
        log.warning("Skipping a bundled driver file with a name a driver cannot have: %r",
                    member.filename)
        return None
    return name


def _driver(name: str, entries: dict[str, bytes]) -> BundledDriver:
    """The driver file ``name`` with the companions it owns among ``entries``."""
    content = entries[name]
    owned = [companion for companion in companion_names(name, content) if companion in entries]
    return BundledDriver(
        main=name,
        files={name: content, **{companion: entries[companion] for companion in owned}},
        optional=tuple(owned) if name.endswith(".py") else (),
        declared_id=_declared_id(name, content),
    )


def _declared_id(name: str, content: bytes) -> str:
    declared = declared_driver_info(name, content).get("id")
    return declared if isinstance(declared, str) and declared else Path(name).stem


# --- installing a project's drivers -------------------------------------------------


class _ServedHere(Exception):
    """A bundled driver whose id a driver on this system already serves."""

    def __init__(self, driver_id: str) -> None:
        super().__init__(driver_id)
        self.driver_id = driver_id


def _new_here(driver_class: type) -> None:
    """The gate a project's driver is held to beyond the loader's checks: an
    id a driver here already serves keeps that driver (``register_driver``
    would replace it)."""
    driver_id = getattr(driver_class, "DRIVER_INFO", {}).get("id")
    if driver_id and is_driver_registered(driver_id):
        raise _ServedHere(driver_id)


_GATES = Gates(driver_class=_new_here, not_a_definition=_DID_NOT_LOAD, no_driver_class=_DID_NOT_LOAD)


def install(zf: zipfile.ZipFile, folder: Path) -> BundledDrivers:
    """Install the drivers a bundle carries into ``folder``, each as one
    change: what each came to. A driver whose file is already there is kept,
    and so is one whose id a driver here serves (the gate refuses it before
    anything is written). A protected identity raises
    (``InstalledEquipmentError``); the caller checks the whole bundle for one
    before anything is written."""
    folder.mkdir(exist_ok=True)
    outcome = BundledDrivers()
    for driver in bundled_drivers(zf):
        if not (folder / driver.main).exists():  # never over an installed driver
            _admit(folder, driver, outcome)
    return outcome


def _admit(folder: Path, driver: BundledDriver, outcome: BundledDrivers) -> None:
    """One bundled driver admitted, kept or refused, into ``outcome``."""
    from openavc.core.installed_equipment import InstalledEquipmentError

    try:
        admitted = admit(_change(folder, driver, replaceable={driver.main}), _GATES)
    except _ServedHere as served:
        log.warning("Bundled driver id '%s' (%s) already registered; keeping the existing driver",
                    served.driver_id, driver.main)
        return
    except DriverRefused as refusal:
        outcome.refuse(driver.declared_id, _refusal_reason(refusal))
        return
    except InstalledEquipmentError:
        raise
    except Exception as e:  # Catch-all: loading a driver runs its code
        log.exception("Could not install bundled driver %s", driver.main)
        outcome.refuse(driver.declared_id, f"{type(e).__name__}: {e}")
        return
    outcome.installed.append(admitted.driver_class.DRIVER_INFO.get("id") or driver.declared_id)
    log.info("Installed bundled driver: %s", driver.main)


def _change(
    folder: Path, driver: BundledDriver, replaceable: Collection[str], removes: Collection[str] = ()
) -> DriverChange:
    """The change that brings ``driver`` into ``folder``: each of its files
    that is not there yet or is ``replaceable``; a companion already there
    stays as it is."""
    files = {
        name: content for name, content in driver.files.items()
        if name in replaceable or not (folder / name).exists()
    }
    return DriverChange(
        folder, driver.main, files,
        optional=[name for name in driver.optional if name in files],
        removes=list(removes),
    )


def _refusal_reason(refusal: Any) -> str:
    """``admit``'s refusal as one sentence: its own, then the loader's
    reasons after it when there are any."""
    if refusal.errors:
        return f"{refusal.detail}: {'; '.join(refusal.errors)}"
    detail = refusal.detail
    return detail if detail.endswith(".") else f"{detail}."


# --- a starter's refresh ------------------------------------------------------------


def refresh_starter(old_bundle: Path, new_bundle: Path, folder: Path) -> bool:
    """Put this release's copy in place of each driver a starter's old bundle
    unpacked into ``folder`` that nobody has edited since.

    ``install`` never goes over a file already in the folder, so a driver
    unpacked from a stale bundle would otherwise stay forever and keep losing
    to the fresh one. A driver file byte-identical to the old bundle's is the
    proof that the library put it there and nobody has changed it, so
    replacing it cannot lose anybody's work; one that differs is left strictly
    alone, its companions too.

    Each comes in as one change, over the copy it replaces, with the
    companions of the old copy that are still unedited: refused (it does not
    load here), the old copy stays, so the live project opened from this
    starter keeps the driver its devices use. A driver the new bundle no
    longer carries goes.

    Whether every driver it had to replace was replaced: the caller keeps
    the old bundle while one was refused, so the next start (a release that
    fixes it) tries again. Bundles it cannot read have nothing it can
    replace, which counts as done.
    """
    bundles = _read_both(old_bundle, new_bundle)
    if bundles is None:
        return True
    before, after = bundles
    results = [_refresh(folder, old, after.get(main)) for main, old in before.items()]
    return all(results)


def _read_both(old_bundle: Path, new_bundle: Path) -> tuple[dict, dict] | None:
    """Each bundle's drivers by their driver file's name; None, logged, when
    either cannot be read."""
    try:
        with zipfile.ZipFile(old_bundle, "r") as old, zipfile.ZipFile(new_bundle, "r") as new:
            return _by_main(old), _by_main(new)
    except Exception as e:  # noqa: BLE001 - never let this block startup
        log.warning("Could not read the starter bundles to refresh their drivers: %s", e)
        return None


def _by_main(zf: zipfile.ZipFile) -> dict[str, BundledDriver]:
    return {driver.main: driver for driver in bundled_drivers(zf)}


def _refresh(folder: Path, old: BundledDriver, new: BundledDriver | None) -> bool:
    """One driver of the old bundle: replaced by ``new`` when its driver file
    is still the old bundle's, removed when the new bundle has no ``new``.
    False only when ``new`` was refused."""
    ours = [name for name, content in old.files.items() if _holds(folder / name, content)]
    if old.main not in ours:
        return True  # edited since, or never unpacked: not this library's to touch
    if new is None:
        for name in ours:
            (folder / name).unlink(missing_ok=True)
        log.info("Removed a bundled driver this release no longer ships: %s", old.main)
        return True
    removes = [name for name in ours if name not in new.files]
    try:
        admit(_change(folder, new, replaceable=ours, removes=removes))
    except Exception as e:  # noqa: BLE001 - never let this block startup
        log.warning("Kept the bundled driver %s it had: this release's copy did not install: %s",
                    new.main, e)
        return False
    log.info("Installed the current bundled driver: %s", new.main)
    return True


def _holds(path: Path, content: bytes) -> bool:
    """Whether the file at ``path`` holds exactly ``content``."""
    return path.is_file() and path.read_bytes() == content
