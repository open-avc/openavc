"""THE two steps of a driver change: ``admit``, which puts a driver file on
this server, and ``changed``, which follows every change at every door; THE
rule that orders two changes on one driver, ``holding``; and THE revision a
save over a driver file is held to, ``hold_to_revision``.

**A save names the copy it was made from.** A driver file's revision is the
SHA-256 of its stored bytes (``revision``). Every read of a driver answers it
(the Builder's listing and read, the AI's read, the Code view's source), and
every save over a driver answers the revision it stored. A save that names a
revision the file no longer has is refused (``DriverChangedElsewhere``, 409)
with the revision it has now, so a save made from a copy another door has
changed since says so instead of undoing that change; saving it again naming
the revision answered is "save mine over it". The check is made inside
``holding`` the id, so no other door's write can land between it and the
save it lets through.

**One change at a time on each driver id.** Every door that writes, removes,
registers or reloads a driver (the community install, update and uninstall,
the single-file and bundle imports, the Driver Builder's create, save, patch,
delete and reload, the Code view's create, save, delete and reload, the
assistant's create and update) runs inside ``holding`` the ids it names, from
its first check to its announcement. A door awaits between its own steps (a
catalog download, the devices on the driver coming down and being built
again), and a second door on the same driver waits for the first to answer
before it checks anything, so each answer describes the end state it left and
the later door's answer is the end state of both. Doors on other drivers do
not wait. A door that learns an id only once it has read or run a file (an
import whose id is computed, a reload that changed the id) ``claim``s it
before registering anything under it: taken when free, refused while another
change holds it, never waited for, since a change waiting while it holds an id
could wait forever on one that waits for its own. ``admit`` claims the id it
registers.

Every door that writes a driver file (the community install and update, the
single-file and bundle imports, the Driver Builder's saves, which the AI's
create and update are too) hands ``admit``
the whole change, every file it brings and every file it removes, and gets
back the registered class, or a refusal that left the driver folder, the
registry and ``sys.modules`` exactly as they were. A person who imports a
newer copy of their driver with a mistake in it keeps the copy that worked.

``admit`` runs these steps in order, with no ``await`` anywhere in it, so no
other request ever sees a file half admitted:

1. **Check.** Every file's protected identity, and the platform floor the
   driver file declares, read from its bytes for both kinds of driver and never
   by running it (``declared_floor``, ``floor_refusal``).
2. **Write beside.** Every file into a staging folder inside the driver folder,
   under the name it will have. The folder goes when ``admit`` returns,
   whatever happened, and one left by a crash goes at the next change.
3. **Admit the staged copy.** The loader's own checks on the staged driver
   file, a Python file imported under a throwaway module name; then the door's
   own gates. A staged file the loader refuses is answered with the loader's
   reasons (``read_driver_file``, ``trial_driver_import``) and recorded nowhere:
   the file in the folder is still the one it was. The staging folder holds the driver as it will be: a companion a
   YAML names that is already installed, and not brought, is copied in beside
   it, so the loader finds it where it will find it after the swap.
4. **Swap.** What each target holds is noted (nothing, a link, or its bytes),
   then each staged file replaces its target (``os.replace``) and each file
   the change removes goes.
5. **Register.** A Python driver is imported again from its final path,
   because its module name and file are what the rest of the platform reads,
   and then the class is registered over the previous one, and any record of
   the file it replaced not loading (``refused_files``) is dropped.

A failure in the swap or the register puts every target back as it was noted,
and the previous module back in ``sys.modules``. The previous class was never
unregistered, so it goes on serving. A Python driver's conventional companions
(``_sim.py``, ``_discovery.py``, found by name) are optional: one that cannot
be put in place is left out with a warning, and the driver goes in without it.

What follows a change is ``changed``, which every door calls once the folder
and the registry say what serves each id now (after ``admit``, a delete, a
reload, an uninstall): the running simulation reads its drivers again and
restarts the simulated devices on the id, the devices on it are built again
and the waiting ones come up, Discovery matches its found devices again, and
every open Programmer is told (``announce``). A door names the ids a driver
arrived for or changed, and the ids it took away (``gone``), whose devices it
leaves as they are.

There is no FastAPI here: ``admit`` and ``claim`` raise ``DriverRefused`` (a
status, a sentence, and the loader's reasons when it refused the file) for
their own refusals, and a door's gates raise what the door raises. ``changed``
raises nothing. Logs as ``openavc.api.routes.drivers``, where the install
doors' lines have always been filed.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from openavc.drivers import refused_files
from openavc.drivers.driver_loader import (
    DRIVER_EXTENSION,
    DRIVER_FILE_STEM,
    companion_relpath_from_yaml,
    declared_driver_info,
    load_python_driver_file,
    read_driver_file,
    require_mutable_driver_file,
)
from openavc.drivers.python_modules import module_name, trial_driver_import
from openavc.drivers.registry import register_driver
from openavc.utils.logger import get_logger

log = get_logger("openavc.api.routes.drivers")

# A file a change brings: a driver file name, never a path.
_FILE_NAME = re.compile(rf"{DRIVER_FILE_STEM}\.(avcdriver|py)")

# A file a change removes, already in the folder under whatever name it was
# given: one plain name on every platform (no separator, drive or stream).
_PLAIN_NAME = re.compile(r"[^/\\:\x00]+")

_STAGING_PREFIX = ".staging-"


class DriverRefused(Exception):
    """A change ``admit`` refused, with the status and sentence a door answers,
    and, when the loader refused the staged driver file, its reasons
    (``errors``), which the door answers beneath the sentence."""

    def __init__(self, status_code: int, detail: str, errors: Collection[str] = ()) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.errors = tuple(errors)


@dataclass(frozen=True)
class DriverChange:
    """One driver arriving: the folder it goes into, its driver file's name,
    every file it brings by name (the driver file included), the brought
    files that are optional, and the files already there that it removes."""

    folder: Path
    main: str
    files: Mapping[str, bytes]
    optional: Collection[str] = ()
    removes: Collection[str] = ()


@dataclass(frozen=True)
class Gates:
    """What a door holds the staged driver to beyond the loader's own checks,
    and the sentence it says when the loader refuses a YAML driver file
    (``not_a_definition``) or a Python one (``no_driver_class``), the loader's
    reasons beneath it. ``definition`` is asked of a YAML driver's parsed
    definition, ``driver_class`` of either kind's class; each raises the door's
    refusal."""

    definition: Callable[[dict[str, Any]], None] | None = None
    driver_class: Callable[[type], None] | None = None
    not_a_definition: str = "The driver was not installed. Its file did not load"
    no_driver_class: str = "The driver was not installed. Its file did not load"


@dataclass(frozen=True)
class Admitted:
    """A change that went in: the class now registered, the names of the
    files it removed that were there, and the notices on the driver file it
    put in: what the author's doors refuse in it, which a door that loads a
    driver says instead (``DefinitionRead.notices``)."""

    driver_class: type
    removed: list[str]
    notices: tuple[str, ...] = ()


# --- one change at a time on each driver id --------------------------------------


class _Holds:
    """Which change holds each driver id on one event loop, and the changes
    waiting for one. An id is here only while a change holds it."""

    def __init__(self) -> None:
        self.holders: dict[str, asyncio.Task[Any]] = {}
        self.waiting: list[asyncio.Future[None]] = []

    def free_for(self, change: asyncio.Task[Any] | None, driver_ids: Collection[str]) -> bool:
        """Whether no other change holds any of ``driver_ids``."""
        return all(self.holders.get(driver_id, change) is change for driver_id in driver_ids)

    def take(self, change: asyncio.Task[Any] | None, driver_ids: Collection[str]) -> list[str]:
        """Take each of ``driver_ids`` this change does not hold already; the
        ones it took, which are the ones it gives back."""
        taken = [driver_id for driver_id in driver_ids if driver_id not in self.holders]
        for driver_id in taken:
            self.holders[driver_id] = change
        return taken

    def give_back(self, driver_ids: Collection[str]) -> None:
        """Give ``driver_ids`` back and wake every waiting change to look again."""
        for driver_id in driver_ids:
            self.holders.pop(driver_id, None)
        waiting, self.waiting = self.waiting, []
        for waiter in waiting:
            if not waiter.done():
                waiter.set_result(None)


_holds_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Holds] = (
    weakref.WeakKeyDictionary()
)

# The ids the innermost ``holding`` of this change took, which ``claim`` adds to.
_this_change: ContextVar[list[str] | None] = ContextVar("driver_change_held", default=None)


def _holds_on(loop: asyncio.AbstractEventLoop) -> _Holds:
    """The holds on ``loop``: a test, a restart, each runs its own."""
    holds = _holds_by_loop.get(loop)
    if holds is None:
        holds = _holds_by_loop[loop] = _Holds()
    return holds


def _busy(driver_id: str) -> DriverRefused:
    return DriverRefused(
        409,
        f"Another change to the driver '{driver_id}' is still being made. "
        "Try again once it has finished.",
    )


@asynccontextmanager
async def holding(*driver_ids: str) -> AsyncIterator[None]:
    """Hold ``driver_ids`` for one change: wait until no other change holds
    any of them, take them all at once, and give them back when the change
    ends, however it ends. Ids this change holds already (a door that calls
    another) are not waited for or given back here. An empty id is ignored."""
    loop = asyncio.get_running_loop()
    holds = _holds_on(loop)
    change = asyncio.current_task()
    wanted = [driver_id for driver_id in dict.fromkeys(driver_ids) if driver_id]
    while not holds.free_for(change, wanted):
        waiter = loop.create_future()
        holds.waiting.append(waiter)
        try:
            await waiter
        finally:
            if waiter in holds.waiting:
                holds.waiting.remove(waiter)
    taken = holds.take(change, wanted)
    token = _this_change.set(taken)
    try:
        yield
    finally:
        _this_change.reset(token)
        holds.give_back(taken)


def claim(driver_id: str) -> None:
    """Make ``driver_id`` this change's before anything is registered or
    dropped under it. Inside ``holding``, a free id is taken until the hold
    ends; an id another change holds is refused (``DriverRefused``, 409), never
    waited for. Outside one, only the refusal applies, and with no event loop
    running no change can be under way."""
    try:
        holds = _holds_on(asyncio.get_running_loop())
    except RuntimeError:
        return
    if not driver_id:
        return
    change = asyncio.current_task()
    if not holds.free_for(change, [driver_id]):
        raise _busy(driver_id)
    mine = _this_change.get()
    if mine is not None:
        mine.extend(holds.take(change, [driver_id]))


# --- the revision a save is held to ---------------------------------------------

#: What a save made from an older copy of a driver is refused with.
CHANGED_ELSEWHERE = "This driver was changed by another session since you opened it."


def revision(data: bytes) -> str:
    """THE revision of a stored driver file, from its bytes: their SHA-256 in
    hex. An opaque token, compared for equality and nothing else."""
    return hashlib.sha256(data).hexdigest()


def file_revision(path: Path) -> str:
    """``revision`` of the file at ``path`` as it is stored now."""
    return revision(path.read_bytes())


class DriverChangedElsewhere(DriverRefused):
    """A save that named a revision the stored file no longer has, with the
    revision it has now (``current``), which a save over it names."""

    def __init__(self, current: str) -> None:
        super().__init__(409, CHANGED_ELSEWHERE)
        self.current = current


def hold_to_revision(sent: Any, current: str) -> None:
    """THE revision check, made by every door that saves over a driver file
    (the Driver Builder's replace and patch, which the AI's update is, and the
    Code view's save) while ``holding`` its id, once the door has found the
    save is its to make and before the content in hand is judged or written:
    a save naming a revision (``sent``) other than the stored file's
    (``current``) is refused (``DriverChangedElsewhere``).

    ``None`` names none, and is not held: a REST client that never read the
    driver replaces it, as a project save with no If-Match does. The
    Builder and the Code view name the revision their copy was read at; the
    AI's update always names one, empty when its definition carried none."""
    if sent is not None and sent != current:
        raise DriverChangedElsewhere(current)


# --- the platform floor ---------------------------------------------------------


def parse_version(v: str) -> tuple[int, int, int]:
    """Parse "X.Y.Z" into a comparable 3-tuple.

    Always three parts, so "0.22" compares equal to "0.22.0" instead of
    less-than, and a part with a pre-release/build suffix keeps its numeric
    prefix ("22-rc1" -> 22) instead of vanishing from the tuple.
    """
    parts: list[int] = []
    for piece in v.strip().split(".")[:3]:
        match = re.match(r"\d+", piece)
        parts.append(int(match.group()) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def floor_refusal(required: str) -> str | None:
    """The sentence refusing a driver that needs OpenAVC ``required`` or later
    when this one is older; None when it is not, or a version cannot be read."""
    from openavc.version import __version__

    try:
        current = parse_version(__version__)
        needed = parse_version(required)
    except Exception:
        log.debug("Version parse failed; allowing install", exc_info=True)
        return None
    if current >= needed:
        return None
    return (
        f"This driver requires OpenAVC {required} or later. "
        f"You are running {__version__}. Please update OpenAVC first."
    )


def declared_floor(filename: str, content: bytes) -> str | None:
    """The platform floor a driver file declares, read without running it.

    Asked before anything is written, which is why it works from the bytes in
    hand rather than from a path: importing a ``.py`` to find out would run
    the very code the floor exists to keep off this box.

    Unreadable is not the same as absent, and both answer ``None`` on purpose:
    a file whose floor cannot be read is a file that is about to fail to load
    anyway, and that message is the useful one.
    """
    value = declared_driver_info(filename, content).get("min_platform_version")
    return value if isinstance(value, str) and value else None


def check_driver_file(filename: str, content: bytes) -> None:
    """What a driver file is held to before anything about it is written or
    fetched: its protected identity, then the floor it declares. ``admit``
    asks it again; a door that downloads more asks it first, so a driver this
    platform cannot run is refused before its companions are asked for."""
    require_mutable_driver_file(filename, content)
    declared = declared_floor(filename, content)
    refusal = floor_refusal(declared) if declared else None
    if refusal:
        raise DriverRefused(422, refusal)


# --- admit -----------------------------------------------------------------------


def admit(change: DriverChange, gates: Gates = Gates()) -> Admitted:
    """Check, stage, admit, swap and register one driver change (the module
    docstring has the steps). Raises ``DriverRefused``, a gate's refusal, or
    whatever the swap or the register raised, always with the driver folder,
    the registry and ``sys.modules`` as they were."""
    check_driver_file(change.main, change.files[change.main])
    for name, content in change.files.items():
        if name != change.main:
            require_mutable_driver_file(name, content)

    folder = os.path.realpath(change.folder)
    _sweep_staging(folder)
    staging = tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=folder)
    try:
        staged = _write_beside(os.path.realpath(staging), change.files)
        _installed_companion_beside(folder, os.path.realpath(staging), change)
        driver_class, notices = _staged_class(staged[change.main], gates)
        return _Swap(folder, change, staged).register(driver_class, gates, notices)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _path_in(folder: str, name: str, pattern: re.Pattern[str] = _FILE_NAME) -> Path:
    """Where the file ``name`` is in ``folder`` (a real path): a name
    ``pattern`` takes whole, never a path, so nothing a change names can be
    outside it."""
    if not pattern.fullmatch(name) or name in (".", ".."):
        raise ValueError(f"{name!r} is not a driver file name")
    path = os.path.normpath(os.path.join(folder, name))
    if not path.startswith(folder + os.sep):
        raise ValueError(f"{name!r} is not in the driver folder")
    return Path(path)


def _sweep_staging(folder: str) -> None:
    """Remove staging folders a crash left behind. Only ever new files are
    in one, never the only copy of an old one, and no change is under way
    while this runs: ``admit`` does not yield."""
    for entry in Path(folder).glob(f"{_STAGING_PREFIX}*"):
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)


def _write_beside(staging: str, files: Mapping[str, bytes]) -> dict[str, Path]:
    """Every file of the change in the staging folder, under its own name."""
    staged: dict[str, Path] = {}
    for name, content in files.items():
        path = _path_in(staging, name)
        path.write_bytes(content)
        staged[name] = path
    return staged


def _installed_companion_beside(folder: str, staging: str, change: DriverChange) -> None:
    """Copy the companion a YAML driver names into the staging folder when it
    is already installed and the change does not bring it: the loader refuses
    a definition whose companion is not beside it, and after the swap it will
    be. Only a companion named by a driver file name, from the folder itself;
    the copy is never swapped in."""
    if not change.main.endswith(DRIVER_EXTENSION):
        return
    relpath = companion_relpath_from_yaml(change.files[change.main])
    name = PurePosixPath(relpath).name if relpath else ""
    if not _FILE_NAME.fullmatch(name) or name in change.files:
        return
    installed = _path_in(folder, name)
    if installed.is_file():
        shutil.copyfile(installed, _path_in(staging, name))


def _staged_class(staged: Path, gates: Gates) -> tuple[type, tuple[str, ...]]:
    """The class the staged driver file is, by the loader's checks and then
    the door's gates, with the loader's notices on it."""
    if staged.suffix == DRIVER_EXTENSION:
        driver_class, notices = _staged_definition_class(staged, gates)
        return _gated(driver_class, gates), notices
    read = trial_driver_import(staged)
    return _python_class(read.driver_class, gates, read.reason), read.notices


def _staged_definition_class(staged: Path, gates: Gates) -> tuple[type, tuple[str, ...]]:
    """A staged YAML driver's class, its definition held to the door's
    definition gate, with the loader's notices on it."""
    from openavc.drivers.configurable import create_configurable_driver_class

    read = read_driver_file(staged)
    definition = read.definition
    if definition is None:
        raise DriverRefused(422, gates.not_a_definition, read.reasons)
    if gates.definition is not None:
        gates.definition(definition)
    return create_configurable_driver_class(definition), read.notices


def _python_class(driver_class: type | None, gates: Gates, reason: str | None = None) -> type:
    """The class a Python driver file imported as, held to the door's class
    gate; the door's refusal, with ``reason`` beneath it, when it did not
    import or defined none."""
    if driver_class is None:
        raise DriverRefused(422, gates.no_driver_class, (reason,) if reason else ())
    return _gated(driver_class, gates)


def _gated(driver_class: type, gates: Gates) -> type:
    """``driver_class``, once the door's class gate has passed it and its id
    is this change's (``claim``)."""
    if gates.driver_class is not None:
        gates.driver_class(driver_class)
    claim(_class_id(driver_class))
    return driver_class


def _class_id(driver_class: type) -> str:
    info = getattr(driver_class, "DRIVER_INFO", None)
    driver_id = info.get("id") if isinstance(info, dict) else None
    return driver_id if isinstance(driver_id, str) else ""


class _Before:
    """What a target held before the swap, and how to put it back: nothing,
    a link (put back as the same link), a file (its bytes and mode), or
    something else, such as a folder, which is never touched."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.link: str | None = None
        self.data: bytes | None = None
        self.mode = 0
        if path.is_symlink():
            self.link = os.readlink(path)
            self.kind = "link"
        elif path.is_file():
            self.data = path.read_bytes()
            self.mode = path.stat().st_mode
            self.kind = "file"
        else:
            self.kind = "other" if path.exists() else "absent"

    def put_back(self) -> None:
        if self.kind == "other":
            return
        if self.kind == "link":
            self.path.unlink(missing_ok=True)
            os.symlink(self.link, self.path)
        elif self.kind == "file":
            _write_in_place(self.path, self.data or b"", self.mode)
        else:
            self.path.unlink(missing_ok=True)
        _forget_bytecode(self.path)


def _write_in_place(path: Path, data: bytes, mode: int) -> None:
    """Write ``data`` to ``path`` through a temporary file beside it, so the
    file is never half written, with ``mode``'s permissions."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class _Swap:
    """One change put in place: each staged file over its target and each
    file it removes gone, every target's previous state noted first, so a
    failure up to and including the register can put all of it back."""

    def __init__(self, folder: str, change: DriverChange, staged: dict[str, Path]) -> None:
        self.folder = folder
        self.change = change
        self.staged = staged
        self.main = _path_in(folder, change.main)
        self.removals = [
            path for path in (_path_in(folder, name, _PLAIN_NAME) for name in change.removes)
            if path.is_file() or path.is_symlink()
        ]
        self.module = module_name(self.main) if self.main.suffix == ".py" else None
        self.old_module = sys.modules.get(self.module) if self.module else None
        self.applied: list[_Before] = []
        self.removed: list[str] = []

    def register(
        self, driver_class: type, gates: Gates, notices: tuple[str, ...] = ()
    ) -> Admitted:
        """Swap, then register (a Python driver imported again from its
        final place first); on any failure, everything put back. The driver
        file's notices are recorded where it now is."""
        try:
            self._place()
            self._remove()
            if self.module is not None:
                driver_class = _python_class(load_python_driver_file(self.main), gates)
            register_driver(driver_class)
        except BaseException:
            self._put_back()
            raise
        refused_files.clear(self.main)
        refused_files.record_notices(
            self.main, driver_class.DRIVER_INFO.get("id") or self.main.stem, notices
        )
        return Admitted(driver_class, self.removed, notices)

    def _place(self) -> None:
        """Each staged file over its target. An optional file that cannot be
        put in place is left out with a warning."""
        for name, source in self.staged.items():
            target = _path_in(self.folder, name)
            before = _Before(target)
            try:
                os.replace(source, target)
            except OSError as e:
                if name not in self.change.optional:
                    raise
                log.warning("Optional companion %s not installed: %s", name, e)
                continue
            self.applied.append(before)
            _forget_bytecode(target)

    def _remove(self) -> None:
        """Each file the change removes, unless it is now one the change put
        in place: in a folder that ignores case (macOS and Windows by
        default), ``Acme.avcdriver`` and ``acme.avcdriver`` are one file."""
        placed = [before.path for before in self.applied]
        for path in self.removals:
            if any(_same_file(path, target) for target in placed):
                continue
            before = _Before(path)
            path.unlink()
            self.applied.append(before)
            self.removed.append(path.name)

    def _put_back(self) -> None:
        for before in reversed(self.applied):
            before.put_back()
        if self.module is None:
            return
        if self.old_module is not None:
            sys.modules[self.module] = self.old_module
        else:
            sys.modules.pop(self.module, None)


def _same_file(path: Path, other: Path) -> bool:
    """Whether two names in the folder are one file; False when either is
    gone."""
    try:
        return os.path.samefile(path, other)
    except OSError:
        return False


def _forget_bytecode(path: Path) -> None:
    """Drop the bytecode compiled from the file a swap replaced: a new file
    of the same size written in the same second would otherwise be served
    from it."""
    if path.suffix != ".py":
        return
    try:
        cached = importlib.util.cache_from_source(str(path))
    except NotImplementedError:
        return
    Path(cached).unlink(missing_ok=True)


# --- changed -----------------------------------------------------------------------


@dataclass(frozen=True)
class Changed:
    """What a driver change did to this server's devices: ``reconnected``, the
    devices on a changed id built again (a waiting one on it that came up among
    them); ``activated``, every device that was waiting for its driver and now
    runs; ``not_reconnected``, the devices on a changed id that did neither."""

    reconnected: list[str] = field(default_factory=list)
    activated: list[str] = field(default_factory=list)
    not_reconnected: list[str] = field(default_factory=list)


async def changed(*driver_ids: str, gone: Collection[str] = ()) -> Changed:
    """Follow a driver change, once the driver folder and the registry say
    what serves each id now: ``driver_ids`` a driver arrived for or changed,
    ``gone`` the ids a door took away.

    1. **The simulation.** A running simulator reads its driver folders again,
       and each simulated device on one of ``driver_ids`` is started again from
       the file there now, in the next bring-up round.
    2. **The devices.** Each device on one of ``driver_ids`` is built again
       from the class that serves it now, and one waiting for it comes up
       (``reload_driver``); then every device waiting for a driver that is
       registered now comes up. A device on a ``gone`` id is left as it is.
    3. **Discovery.** Its driver hints and discovery files are read again, and
       every device a scan found is matched again.
    4. **The announcement** to every open Programmer (``announce``), naming
       the first id.

    Each part that fails is logged and the next still runs: the change is made
    by now, and a door that answered it as not made would be pressed again.
    """
    engine = _running_engine()
    outcome = Changed()
    if engine is not None:
        restarting = await _tell_the_simulation(engine, driver_ids)
        if driver_ids:
            outcome = await _rebuild_devices(engine.devices, driver_ids)
        if restarting:
            _ask_for_a_bringup_round(engine.devices)
    await _refresh_discovery()
    await announce(next(iter((*driver_ids, *gone)), None))
    return outcome


async def announce(driver_id: str | None = None) -> None:
    """Tell every open Programmer that the drivers on this server changed.

    The IDE keeps its driver lists (Drivers > Installed, the Builder's
    definitions) in a store that only reloads when that store does the
    installing. Many installs happen elsewhere: Discovery's Install & Add, a
    device page's Install, the AI's install tool, an upload, a Builder save,
    a Python driver edit, a project that brings its own drivers, another
    browser. So the server says it once, at every door that writes a driver,
    and every Programmer refreshes. ``driver_id`` is left out when a door
    wrote several (or cannot say which).
    """
    engine = _running_engine()
    if engine is None:
        return
    try:
        message: dict[str, Any] = {"type": "drivers.changed"}
        if driver_id:
            message["driver_id"] = driver_id
        await engine.ws.broadcast(message, client_type="programmer")
    except Exception:
        log.exception("Could not announce the driver change for '%s'", driver_id)


def _running_engine() -> Any:
    """The engine, or None before it has started (and in a bare test)."""
    from openavc.api._engine import get_engine_optional

    return get_engine_optional()


async def _tell_the_simulation(engine: Any, driver_ids: Collection[str]) -> bool:
    """Tell a running simulation the drivers changed; whether it has simulated
    devices to start again on ``driver_ids``."""
    simulation = getattr(engine, "simulation", None)
    if simulation is None:
        return False
    try:
        return bool(await simulation.drivers_changed(driver_ids))
    except Exception:
        log.exception("The simulation could not be told that the drivers changed")
        return False


async def _rebuild_devices(devices: Any, driver_ids: Collection[str]) -> Changed:
    """Build every device on ``driver_ids`` again and bring up every device
    waiting for a driver that is registered now; what came of each."""
    using, waiting = _devices_on(devices, driver_ids)
    reconnected: list[str] = []
    for driver_id in driver_ids:
        reconnected += await _logged(
            lambda driver_id=driver_id: devices.reload_driver(driver_id),
            f"Driver '{driver_id}' changed, but its devices could not be built again",
        )
    swept = await _logged(
        lambda: devices.retry_all_orphans(), "Could not bring up the devices waiting for a driver"
    )
    activated = list(dict.fromkeys([d for d in reconnected if d in waiting] + list(swept)))
    not_reconnected = [d for d in using if d not in reconnected and d not in activated]
    return Changed(reconnected, activated, not_reconnected)


def _devices_on(devices: Any, driver_ids: Collection[str]) -> tuple[list[str], set[str]]:
    """The devices on ``driver_ids``, and those of them waiting to run (for
    their driver, or for their settings to be worked out)."""
    from openavc.core.device_records import ORPHANED, SETTINGS_FAILED

    try:
        using = [d for driver_id in driver_ids for d in devices.get_devices_using_driver(driver_id)]
        return using, {d for d in using if devices.status_of(d) in (ORPHANED, SETTINGS_FAILED)}
    except Exception:
        log.exception("Could not read which devices use the changed drivers")
        return [], set()


async def _logged(step: Callable[[], Awaitable[list[str]]], failure: str) -> list[str]:
    """The devices one part of the rebuild names, or none, with ``failure``
    logged, when it raises."""
    try:
        return list(await step())
    except Exception:
        log.exception(failure)
        return []


def _ask_for_a_bringup_round(devices: Any) -> None:
    """Ask for the bring-up round in which the simulation starts its devices
    on a changed driver again, when no rebuild asked for one already."""
    request = getattr(devices, "request_bringup", None)
    if request is None:
        return
    try:
        request()
    except Exception:
        log.exception("Could not ask for a bring-up round after a driver change")


async def _refresh_discovery() -> None:
    """Discovery's driver hints and discovery files read again, and every
    found device matched again."""
    from openavc.api.discovery import refresh_all_device_matches

    try:
        await refresh_all_device_matches()
    except Exception:
        log.exception("Discovery could not match its devices again after a driver change")
