"""
OpenAVC Driver Loader — scans for .avcdriver definition files and Python
driver modules, and registers them.

Supported formats:
    - .avcdriver  YAML definition files (loaded via ConfigurableDriver)
    - .py         Python modules containing BaseDriver subclasses

Directories scanned:
    - openavc/drivers/definitions/  (built-in .avcdriver definitions)
    - driver_repo/                 (community/user drivers — .avcdriver and .py)

Each valid driver is registered in the global driver registry. Which file
serves a driver id is THE rule ``serving_file`` keeps (``_rank``), and the
id a file declares THE one ``driver_id_from_file`` reads (a ``.py`` file's
from the class the runtime registers, ``python_info.driver_info_ast``): the
loader, every listing, remover, updater and exporter, the audit and the
simulator ask them. Whether a
file loads, and why not, is decided once per kind (``read_driver_file``,
``python_modules.read_python_driver_file``); the library pass keeps each
refused file's reasons in ``refused_files`` (``read_library_definition``,
``read_library_python_driver``), which every reader that would otherwise call
the driver not installed asks.
"""

from __future__ import annotations

import ast
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import yaml

from openavc.drivers.driver_file_name import is_driver_file_stem
from openavc.drivers.avcdriver_semantic import (
    DefinitionVerdict,
    check_driver_definition as _check_definition_rules,
    unknown_key_errors as _unknown_key_errors,
    validate_driver_definition as _validate_definition_rules,
    validate_driver_issues as _validate_definition_issues,
)
from openavc.drivers import refused_files
from openavc.drivers.python_modules import (
    NO_DRIVER_CLASS,
    PythonRead,
    is_imported as _is_imported,
    load_python_driver_file as load_python_driver_file,
    python_source_syntax_error as python_source_syntax_error,
    read_python_driver_file as read_python_driver_file,
    reload_python_driver as reload_python_driver,
)
from openavc.drivers.registry import (
    register_driver,
    unregister_driver,
)
from openavc.drivers.spec import REQUIRED_FIELDS as REQUIRED_FIELDS, without_null_sections
from openavc.utils.fileio import atomic_write_text
from openavc.utils.logger import get_logger
from openavc.version import __version__

log = get_logger(__name__)

# File extension for driver definitions
DRIVER_EXTENSION = ".avcdriver"

# Sibling companion files that live next to drivers but aren't drivers
# themselves. Discovery companions (`<id>_discovery.py`) expose
# ``async def probe(ctx)`` for the discovery engine; Python simulators
# (`<id>_sim.py`) expose a Simulator class for the device simulator.
# Neither has a ``DRIVER_INFO`` constant or a BaseDriver subclass, so
# the runtime loader silently skips them — but they would otherwise
# leak into the Code tab and the Installed Drivers panel as if they
# were standalone Python drivers. Filter them at the listing layer
# alongside underscore-prefixed files (which are conventional
# helpers / private modules).
# A YAML driver has at most one companion, the discovery file its
# ``discovery:`` block names; a Python driver's go by name, one per suffix.
DISCOVERY_COMPANION_SUFFIX = "_discovery.py"
COMPANION_SUFFIXES: tuple[str, ...] = (DISCOVERY_COMPANION_SUFFIX, "_sim.py")


def declared_driver_info(filename: str, content: bytes) -> dict[str, Any]:
    """Read declarative metadata before writing a driver, without executing it.

    Incomplete source remains saveable in Code. Dynamically computed metadata
    cannot be established here; registration still enforces reserved identity.
    This is a workflow guard, not a sandbox for imported Python code.
    """
    if filename.endswith(".py"):
        from openavc.drivers.python_info import ExtractError, extract_python_driver_info_full

        with tempfile.TemporaryDirectory() as tmp:
            # A fixed name, never the one the file arrived with: on Windows
            # the last part of an uploaded or bundled ``D:x.py`` names drive
            # D:, and the probe would be written there instead of in ``tmp``.
            probe = Path(tmp) / "driver.py"
            try:
                probe.write_bytes(content)
                info, _opaque = extract_python_driver_info_full(probe)
                return info
            except (ExtractError, OSError, ValueError):
                return {}
    try:
        info = yaml.safe_load(content)
    except (yaml.YAMLError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def require_mutable_driver_file(filename: str, content: bytes) -> None:
    """Refuse a protected filename or declared identity before filesystem writes."""
    from openavc.drivers.registry import require_mutable_driver

    require_mutable_driver(Path(filename).stem)
    driver_id = declared_driver_info(filename, content).get("id")
    if isinstance(driver_id, str):
        require_mutable_driver(driver_id)


def driver_companions(main_path: Path) -> list[Path]:
    """Existing simulator and discovery siblings belonging to this driver.

    Python drivers use conventional names; YAML drivers declare their
    discovery companion. Only files beside the installed driver travel with it.
    """
    if main_path.suffix == DRIVER_EXTENSION:
        candidates = _declared_companion(main_path)
    else:
        candidates = [
            main_path.with_name(f"{main_path.stem}{suffix}")
            for suffix in COMPANION_SUFFIXES
        ]
    return [path for path in candidates if path.is_file()]


def _declared_companion(main_path: Path) -> list[Path]:
    """The discovery companion a YAML driver names, when it names a file
    beside itself; nothing when it names none, or a path elsewhere."""
    try:
        definition = yaml.safe_load(main_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return []
    relpath = companion_relpath_from_def(definition) if isinstance(definition, dict) else None
    name = _companion_name(relpath) if relpath else None
    if name is None:
        return []
    return [main_path.with_name(name)]


def _companion_name(relpath: str) -> str | None:
    """THE rule for where a definition's discovery file may be: one file
    beside the driver, written as its name or as ``./<name>``. That name, or
    None for a path anywhere else (an absolute path, another folder, ``..``).
    Only a file beside the driver travels with it and is found by the
    discovery scan, so the installer and ``missing_companion`` both ask this."""
    relative = Path(relpath)
    if relative.is_absolute() or relative.parent != Path("."):
        return None
    if relative.name in ("", ".."):
        return None
    return relative.name


def _is_driver_file(filepath: Path) -> bool:
    """Return False for companion / helper .py files that aren't drivers."""
    name = filepath.name
    if name.startswith("_"):
        return False
    if any(name.endswith(suf) for suf in COMPANION_SUFFIXES):
        return False
    return True


def validate_driver_definition(
    driver_def: dict[str, Any], *, strict: bool = False, keeps_id: bool = False
) -> list[str]:
    """
    Validate a driver definition.

    Returns a list of error strings. Empty list means valid.

    The rules live in ``avcdriver_semantic`` (shared with the community
    catalog's validator); this wrapper hands in the running release, which a
    discovery block's ``requires:`` is judged against.

    ``strict`` is the author's doors' switch (save, Validate, the AI tool,
    ``drivers.check``): leave it off when loading a driver, where
    ``check_driver_definition`` returns what it moves as notices.
    ``keeps_id``: the save keeps an id outside the id pattern the library serves.
    """
    return _validate_definition_rules(
        driver_def, platform_version=__version__, strict=strict, keeps_id=keeps_id
    )


def check_driver_definition(
    driver_def: dict[str, Any], *, strict: bool = False
) -> DefinitionVerdict:
    """The errors and the notices for a definition
    (``avcdriver_semantic.check_driver_definition``), judged as this release."""
    return _check_definition_rules(
        driver_def, platform_version=__version__, strict=strict
    )


def validate_driver_issues(
    driver_def: dict[str, Any], *, strict: bool = False, keeps_id: bool = False
) -> list[dict[str, str]]:
    """The same validation as ``validate_driver_definition``, as issue records.

    ``{severity, message, path}`` per issue — errors plus the warnings the
    error list has no way to carry. Serves ``POST /driver-definitions/validate``,
    which is how the Driver Builder gets its issues instead of holding a
    second copy of the contract rules.
    """
    return _validate_definition_issues(
        driver_def, platform_version=__version__, strict=strict, keeps_id=keeps_id
    )


def companion_relpath_from_def(driver_def: dict[str, Any]) -> str | None:
    """Return the relative ``discovery.python.file`` path if declared.

    Used by ``load_driver_file`` and by the ``/drivers/upload`` REST
    route to spot YAMLs that declare a Python companion before
    accepting them. Returns ``None`` when no ``python:`` declaration is
    present (any other discovery fingerprint type stands alone).
    """
    discovery = driver_def.get("discovery") or {}
    if not isinstance(discovery, dict):
        return None
    block = discovery.get("python")
    if isinstance(block, str):
        return block or None
    if isinstance(block, dict):
        path = block.get("file")
        if isinstance(path, str) and path:
            return path
    return None


def companion_relpath_from_yaml(yaml_text: str | bytes) -> str | None:
    """The discovery companion a YAML driver's text names, as written.

    The driver routes' install, update, upload and uninstall use it to find
    the sibling Python file that goes with a YAML driver (``python:
    ./<id>_discovery.py`` in its ``discovery:`` block), from a download or a
    file not yet loaded; the loader refuses the driver without that file
    beside it. What counts as a declaration is ``companion_relpath_from_def``;
    this only reads the text first.

    ``None`` when the text does not parse to a mapping or names no companion;
    the caller decides what that means in context.
    """
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None
    return companion_relpath_from_def(parsed) if isinstance(parsed, dict) else None


def count_comment_lines(filepath: Path) -> int:
    """How many comment lines a driver file carries.

    A ``.avcdriver`` is loaded as YAML and saved by dumping the parsed
    structure, so a save through an editor cannot carry comments back out —
    they were never in the object it edited. That matters more here than in
    most formats: comments are the documented reason this contract is YAML
    rather than JSON ("essential for documenting protocol details from
    manufacturer manuals"), the schema hook editors validate against lives in
    one, and the shipped corpus carries thousands of lines of protocol notes
    transcribed from vendor manuals.

    So the count travels with the definition, and the editor warns before a
    save rather than after. Cheap and honest: preserving comments through a
    form editor needs a round-trip YAML engine, and this says what will happen
    instead of pretending otherwise.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


@dataclass(frozen=True)
class DefinitionRead:
    """What reading one ``.avcdriver`` came to: the checked definition (a
    null top-level section read as absent), or None with the reasons it does
    not load (sentences without the file's path, which at an import is a
    staging folder's); the id it declares, when one can be read; and, for a
    definition that loads, the notices: what the author's doors refuse in it
    (``check_driver_definition``), which a door loading it says instead."""

    definition: dict[str, Any] | None
    reasons: tuple[str, ...] = ()
    declared_id: str | None = None
    notices: tuple[str, ...] = ()


def load_driver_file(filepath: Path) -> dict[str, Any] | None:
    """
    Load and validate a single driver definition file (.avcdriver YAML).

    Returns the driver definition dict, or None if invalid (``read_driver_file``
    has the reasons).
    """
    return read_driver_file(filepath).definition


def read_driver_file(filepath: Path) -> DefinitionRead:
    """Read and check one driver definition file: THE verdict on whether an
    ``.avcdriver`` loads and, when it does not, why. Each reason is also
    logged, with the file's path."""
    try:
        text = filepath.read_text(encoding="utf-8")
        driver_def = yaml.safe_load(text)
    except UnicodeDecodeError:
        log.warning(f"Driver file {filepath} is not UTF-8 text; save it as UTF-8 to load it")
        return DefinitionRead(None, ("The file is not UTF-8 text. Save it as UTF-8.",))
    except OSError as e:
        log.warning(f"Failed to load driver file {filepath}: {e}")
        return DefinitionRead(None, (f"The file could not be read: {e.strerror or e}",))
    except yaml.YAMLError as e:
        log.warning(f"Failed to load driver file {filepath}: {e}")
        return DefinitionRead(None, (f"The file is not valid YAML: {e}",))

    if not isinstance(driver_def, dict):
        log.warning(f"Driver file {filepath} is not a valid YAML mapping")
        return DefinitionRead(
            None, ("The file holds no driver definition: its top level is not a YAML mapping.",)
        )
    declared = driver_def.get("id")
    declared_id = declared if isinstance(declared, str) and declared else None
    try:
        reasons, notices = _definition_refusals(filepath, driver_def)
    except Exception as exc:  # one file a check cannot take is that file's problem
        log.warning(f"Driver file {filepath} could not be checked, so it was not loaded: {exc}")
        reasons, notices = (f"The driver check could not read it: {exc}",), ()
    if reasons:
        return DefinitionRead(None, reasons, declared_id)
    return DefinitionRead(without_null_sections(driver_def), (), declared_id, notices)


def _definition_refusals(
    filepath: Path, driver_def: dict[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``read_driver_file``'s verdict on a definition it read: why the loader
    drops it (each reason logged), or nothing when it loads, with what the
    author's doors would refuse in it (each logged too)."""
    verdict = check_driver_definition(driver_def)
    if verdict.errors:
        log.warning(
            f"Invalid driver definition in {filepath}: "
            + "; ".join(verdict.errors)
        )
        return tuple(verdict.errors), ()

    # Loud, but not fatal. A value of the wrong kind is said out loud: logged
    # here and shown on the driver's Installed entry (the notices). A key the
    # contract doesn't declare is logged only: it is nearly always a typo,
    # but a definition written for a newer platform can carry a field this one
    # hasn't learned yet, which is no mistake in the file. Neither is worth
    # dropping the driver over, which would take its devices offline; the
    # authoring gates refuse both outright.
    for message in verdict.notices:
        log.warning(f"Driver definition {filepath}: {message}")
    for message in _unknown_key_errors(driver_def):
        log.warning(f"Driver definition {filepath}: {message}")

    missing = missing_companion(driver_def, filepath.parent)
    if missing:
        log.warning(f"Driver {filepath.name}: {missing} Skipping driver")
        return (missing,), ()

    return (), tuple(verdict.notices)


def missing_companion(driver_def: dict[str, Any], folder: Path) -> str | None:
    """THE rule for a definition whose discovery file is missing: the sentence
    saying the file it names (``discovery.python.file``) is not in ``folder``,
    the folder the definition is in, or None when it names none or the file
    is there. The loader refuses such a definition, and the Driver Builder's
    check names it before a save. A path outside the folder is not there,
    whatever it points at (``_companion_name``), so the check never says
    whether a file exists anywhere else.

    A declaration pointing at a missing file would register its discovery
    rules at hint-load time with nothing to produce their evidence: the device
    would be matchable in theory and invisible in practice."""
    companion_relpath = companion_relpath_from_def(driver_def)
    if not companion_relpath:
        return None
    name = _companion_name(companion_relpath)
    if name is not None and (folder / name).is_file():
        return None
    return (
        f"It names the discovery file {companion_relpath!r} "
        "(discovery.python.file), which is not in the driver folder. "
        "Add that file first."
    )


def python_driver_info(filepath: Path) -> dict[str, str | int | float | bool]:
    """The values written out in a ``.py`` driver's ``DRIVER_INFO``, read
    without running it from the class the runtime registers
    (``python_info.driver_info_ast``); empty when no class has a table, or the
    source cannot be read or parsed. A value built in code is left out."""
    from openavc.drivers.python_info import ExtractError, driver_info_ast

    try:
        return _ast_dict_to_simple(driver_info_ast(filepath))
    except ExtractError:  # no class with a table, or source that does not parse
        return {}
    except OSError as e:
        log.debug(f"Could not read DRIVER_INFO from {filepath}: {e}")
        return {}


def driver_id_from_file(filepath: Path) -> str | None:
    """THE id a driver file declares, read without importing it.

    A driver is registered under the id it declares, which may differ from
    its file name (a file placed by hand, or imported before imports were
    named for their id), so a reader that maps an id back to its file asks
    here rather than going by the name.

    - ``.py``: the ``id`` the class the runtime registers declares
      (``python_driver_info``, which agrees with ``driver_class_in``), when
      it is written out as text.
    - everything else (``.avcdriver`` YAML): the top-level ``id`` field.

    Returns the declared id, or None if it can't be determined.
    """
    if filepath.suffix == ".py":
        declared = python_driver_info(filepath).get("id")
        return declared if isinstance(declared, str) and declared else None
    try:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, ValueError) as e:
        log.debug(f"Could not read driver id from {filepath}: {e}")
        return None
    if isinstance(data, dict):
        did = data.get("id")
        return did if isinstance(did, str) and did else None
    return None


def _is_driver(filepath: Path, driver_id: str) -> bool:
    """Whether a main driver file is driver ``driver_id``.

    A file is the driver its source declares. One whose id cannot be read
    (half-written source saved from Code) is the driver its name says, so a
    syntax error never makes a driver's file disappear from its own id.
    """
    declared = driver_id_from_file(filepath)
    if declared is not None:
        return declared == driver_id
    return filepath.stem == driver_id


def driver_files_for(
    directory: Path | str, driver_id: str, extension: str
) -> list[Path]:
    """Every main driver file of one kind (``".py"`` or ``".avcdriver"``) in
    ``directory`` that is driver ``driver_id``, the one named
    ``<id><extension>`` first, then by name.

    A driver uploaded before uploads were named by their id keeps the name it
    arrived with, and a renamed copy can sit beside the properly named one, so
    a driver id is looked up by what each file declares. Which of several is
    the one that serves is ``serving_file``'s; deleting the driver removes
    them all. Companions and underscore helpers are never driver files.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    found = [
        filepath
        for filepath in sorted(directory.glob(f"*{extension}"))
        if _is_driver_file(filepath) and _is_driver(filepath, driver_id)
    ]
    found.sort(key=lambda path: path.stem != driver_id)
    return found


def python_driver_file(directory: Path | str, driver_id: str) -> Path:
    """The file that is Python driver ``driver_id`` in ``directory``: the
    copy that serves it (``serving_file``'s rule among the Python files, so
    the one the Code view lists), or, when none loads, the one that would;
    ``<id>.py`` when no file there is the driver (which may not exist: the
    caller decides whether that is "not found" or "create it")."""
    python = [(0, path) for path in driver_files_for(directory, driver_id, ".py")]
    chosen = _chosen(sorted(python), driver_id, _loads, refused_too=True)
    return chosen if chosen is not None else Path(directory) / f"{driver_id}.py"


def _library_files(
    directories: Sequence[Path | str], pattern: str
) -> Iterator[tuple[int, Path]]:
    """Each file matching ``pattern`` in the directories that exist, with its
    directory's index: directories in the order given, files by name.

    The order the loaders and the listings read the library in, so a later
    directory's copy of an id is met after an earlier one's (``_takes_the_id``).
    """
    for dir_index, dir_path in enumerate(directories):
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue
        for filepath in sorted(dir_path.glob(pattern)):
            yield dir_index, filepath


def _python_driver_files(
    directories: Sequence[Path | str],
) -> Iterator[tuple[int, Path]]:
    """The ``.py`` files in the library that are drivers, in library order:
    companions and underscore helpers are passed over (``_is_driver_file``)."""
    for dir_index, filepath in _library_files(directories, "*.py"):
        if _is_driver_file(filepath):
            yield dir_index, filepath


def _rank(dir_index: int, filepath: Path, driver_id: str) -> tuple[bool, int, bool]:
    """THE order in which the copies of one driver id serve it, highest
    first: a Python file over a definition (the loader registers every Python
    file after every definition), a later directory over an earlier one, and
    in one directory the file named for the id over a renamed copy. Copies
    that rank the same: the first by name serves."""
    return (filepath.suffix == ".py", dir_index, filepath.stem == driver_id)


def _takes_the_id(
    loaded: tuple[int, Path] | None, dir_index: int, filepath: Path, driver_id: str
) -> bool:
    """Whether ``filepath`` should serve ``driver_id`` over the copy already
    loaded (``(dir index, file)``, or None), by ``_rank``; logged."""
    if loaded is None:
        return True
    loaded_index, loaded_path = loaded
    takes = _rank(dir_index, filepath, driver_id) > _rank(loaded_index, loaded_path, driver_id)
    if loaded_index != dir_index:
        log.info(
            f"Driver '{driver_id}' from {filepath.name} overrides the copy "
            f"loaded from an earlier directory"
        )
        return takes
    used = filepath if takes else loaded_path
    log.warning(
        f"Driver '{driver_id}' is in both {loaded_path.name} and "
        f"{filepath.name}; {used.name} is used"
    )
    return takes


def _chosen(
    copies: Sequence[tuple[int, Path]], driver_id: str, loads: Callable[[Path], bool],
    *, refused_too: bool,
) -> Path | None:
    """The copy (``(dir index, file)``, in library order) that serves
    ``driver_id`` by ``_rank`` among those that load; when none does and
    ``refused_too``, the one that would serve if they all did."""
    pool = [copy for copy in copies if loads(copy[1])]
    if not pool and refused_too:
        pool = list(copies)
    best: tuple[int, Path] | None = None
    for copy in pool:
        if best is None or _rank(*copy, driver_id) > _rank(*best, driver_id):
            best = copy
    return best[1] if best is not None else None


def _loads(filepath: Path) -> bool:
    """Whether the loader takes a library file: not refused at its last load
    (``refused_files``) and, for a definition, one that reads and passes its
    check now. A Python file is never imported only to answer this."""
    if refused_files.refusal_of(filepath) is not None:
        return False
    if filepath.suffix == DRIVER_EXTENSION:
        return read_driver_file(filepath).definition is not None
    return True


def _library_copies(directories: Sequence[Path | str]) -> dict[str, list[tuple[int, Path]]]:
    """Every main driver file in the library by the driver it is (the id it
    declares, else its file name), definitions then Python files, each in
    library order."""
    copies: dict[str, list[tuple[int, Path]]] = {}
    files = [
        *_library_files(directories, f"*{DRIVER_EXTENSION}"),
        *_python_driver_files(directories),
    ]
    for dir_index, filepath in files:
        driver_id = driver_id_from_file(filepath) or filepath.stem
        copies.setdefault(driver_id, []).append((dir_index, filepath))
    return copies


def serving_file(
    directories: Sequence[Path | str], driver_id: str, *, refused_too: bool = False
) -> Path | None:
    """THE file that serves ``driver_id`` from the library's directories, of
    either kind: the copy the loader registers (``_rank`` among the files
    that load), or None when no file is that driver or none loads.

    ``refused_too``: when no copy loads, the one that would serve if it did,
    which is the file a listing shows with its reasons, an update replaces
    and a reload reads again.

    Every reader that maps an id back to its file asks here (the listings,
    the removers' restore, the update, the export, the audit and the
    simulator), so none acts on a copy that does not run."""
    return _chosen(
        _library_copies(directories).get(driver_id, []), driver_id, _loads,
        refused_too=refused_too,
    )


def serving_files(
    directories: Sequence[Path | str], *, refused_too: bool = False
) -> dict[str, Path]:
    """``serving_file`` for every driver in the library: each id the files
    declare, with the file that serves it (by first appearance in library
    order). An id no file of which loads is left out, unless ``refused_too``."""
    served: dict[str, Path] = {}
    for driver_id, copies in _library_copies(directories).items():
        chosen = _chosen(copies, driver_id, _loads, refused_too=refused_too)
        if chosen is not None:
            served[driver_id] = chosen
    return served


def load_driver_files(directories: Sequence[Path | str]) -> int:
    """
    Scan directories for .avcdriver files, validate them,
    create ConfigurableDriver subclasses, and register them.

    Directories are scanned in order and LATER directories take precedence:
    a same-id driver in a later directory (the user's ``driver_repo``)
    replaces one from an earlier directory (the read-only built-in
    definitions), so an installed or edited user copy actually serves the
    id. Within a single directory a duplicate id is a conflict: the file named
    for the id wins (``driver_files_for``), else the first alphabetically, and
    the rest are skipped with a warning.

    Returns the number of distinct drivers successfully registered.
    """
    from openavc.drivers.configurable import create_configurable_driver_class

    # driver_id -> (index of the dir it loaded from, the file it loaded from)
    registered: dict[str, tuple[int, Path]] = {}
    for dir_index, filepath in _library_files(directories, f"*{DRIVER_EXTENSION}"):
        try:
            _load_definition_file(filepath, dir_index, registered, create_configurable_driver_class)
        except Exception:  # one file that cannot be taken never stops the pass
            log.exception(f"Failed to load driver file {filepath}")

    return len(registered)


def _load_definition_file(
    filepath: Path,
    dir_index: int,
    registered: dict[str, tuple[int, Path]],
    create_class: Callable[[dict[str, Any]], type],
) -> None:
    """One ``.avcdriver`` of the library pass: read, checked, and registered
    when it serves its id (``registered`` records it); a file that does not
    load is kept in ``refused_files`` with why."""
    driver_def = read_library_definition(filepath).definition
    if driver_def is None:
        return
    driver_id = driver_def.get("id", "")
    if not _takes_the_id(registered.get(driver_id), dir_index, filepath, driver_id):
        return
    try:
        driver_class = create_class(driver_def)
        register_driver(driver_class)
        registered[driver_id] = (dir_index, filepath)
        log.info(f"Loaded driver: {driver_id} from {filepath.name}")
    except Exception as exc:  # Catch-all: YAML parsing/validation can fail in many ways
        log.exception(f"Failed to create driver class from {filepath}")
        refused_files.record(filepath, driver_id, (f"{type(exc).__name__}: {exc}",))


def read_library_definition(filepath: Path) -> DefinitionRead:
    """``read_driver_file`` for a file in the driver library, loaded where it
    is: its verdict is kept in ``refused_files`` (why it did not load, or no
    record once it does), for every reader that would otherwise call the
    driver not installed. The start-up pass, a project bundle's driver
    install and the Builder's reload read a library file through here; a
    staged copy (an install, an update, an import) never does."""
    read = read_driver_file(filepath)
    driver_id = read.declared_id or filepath.stem
    if read.definition is None:
        refused_files.record(filepath, driver_id, read.reasons)
    else:
        refused_files.clear(filepath)
    refused_files.record_notices(filepath, driver_id, read.notices)
    return read


def read_library_python_driver(filepath: Path) -> PythonRead:
    """``read_python_driver_file`` for a file in the driver library, imported
    where it is, its verdict and its notices kept in ``refused_files`` as
    ``read_library_definition`` keeps a definition's."""
    read = read_python_driver_file(filepath)
    if read.driver_class is None:
        refused_files.record(
            filepath, driver_id_from_file(filepath) or filepath.stem, (read.reason or NO_DRIVER_CLASS,)
        )
    else:
        refused_files.clear(filepath)
        refused_files.record_notices(
            filepath, read.driver_class.DRIVER_INFO.get("id") or filepath.stem, read.notices
        )
    return read


def load_python_drivers(directories: Sequence[Path | str]) -> int:
    """
    Scan directories for .py driver files, load them, and register.

    Same precedence as ``load_driver_files``: later directories win for a
    duplicate id; within one directory the file named for the id wins, else
    the first alphabetically.

    Returns the number of distinct drivers successfully registered.
    """
    # driver_id -> (index of the dir it loaded from, the file it loaded from)
    registered: dict[str, tuple[int, Path]] = {}
    for dir_index, filepath in _python_driver_files(directories):
        try:
            _load_python_file(filepath, dir_index, registered)
        except Exception:  # one file that cannot be taken never stops the pass
            log.exception(f"Failed to load Python driver file {filepath}")

    return len(registered)


def _load_python_file(
    filepath: Path, dir_index: int, registered: dict[str, tuple[int, Path]]
) -> None:
    """One ``.py`` driver of the library pass: imported, and registered when
    it serves its id (``registered`` records it); a file that does not load
    is kept in ``refused_files`` with why."""
    driver_class = read_library_python_driver(filepath).driver_class
    if driver_class is None:
        return
    driver_id = driver_class.DRIVER_INFO.get("id", "")
    if not _takes_the_id(registered.get(driver_id), dir_index, filepath, driver_id):
        return
    try:
        register_driver(driver_class)
        registered[driver_id] = (dir_index, filepath)
        log.info(f"Loaded Python driver: {driver_id} from {filepath.name}")
    except Exception as exc:
        log.exception(f"Failed to register Python driver from {filepath}")
        refused_files.record(filepath, driver_id, (f"{type(exc).__name__}: {exc}",))


def load_all_drivers(directories: Sequence[Path | str]) -> int:
    """
    Load both .avcdriver YAML definitions and .py Python drivers from
    the given directories. This is the main entry point for loading all
    driver types in one pass.

    Returns the total number of drivers successfully loaded.
    """
    count = 0
    count += load_driver_files(directories)
    count += load_python_drivers(directories)
    return count


def load_builtin_drivers() -> int:
    """Load every driver the standard directories hold, and register it.

    The startup pass: the built-in ``.avcdriver`` definitions that ship with
    the server (the generic tcp / serial / http devices among them) plus
    whatever the user has installed into ``driver_repo/``. Called once from
    ``Engine.start()``, after the legacy-repo migration has moved anything
    still sitting in the pre-data_dir layout, and before the project loads
    and starts asking which drivers exist.

    Returns the number of drivers loaded.
    """
    from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR

    driver_dirs = [
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    ]
    from openavc.drivers.equipment import EquipmentDriver

    register_driver(EquipmentDriver)
    loaded = 1 + load_all_drivers(driver_dirs)
    if loaded:
        log.info(f"Loaded {loaded} driver(s) from definition/driver files")
    return loaded


def definition_file(
    driver_def: dict[str, Any], *, in_place_of: Path | str | None = None
) -> tuple[str, bytes]:
    """THE file a definition is saved as: its name, the driver's id as it is
    written plus ``.avcdriver``, and its bytes, YAML in the definition's own
    key order. An id no file may be named for (``driver_file_name``), which
    only a driver installed before ids were held to their pattern has, is
    saved into the file it is in, ``in_place_of``; with none, ``ValueError``."""
    driver_id = driver_def.get("id")
    if is_driver_file_stem(driver_id):
        name = f"{driver_id}{DRIVER_EXTENSION}"
    elif in_place_of is not None:
        name = Path(in_place_of).name
    else:
        raise ValueError(f"The driver id {driver_id!r} cannot name a driver file")
    text = yaml.dump(
        driver_def,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return name, text.encode("utf-8")


def save_driver_definition(
    driver_def: dict[str, Any],
    directory: Path | str,
) -> Path:
    """
    Save a driver definition as a .avcdriver YAML file (``definition_file``).

    Returns the path to the saved file. The Driver Builder's saves go through
    ``driver_change.admit`` instead, which checks the file before it is in
    place.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    from openavc.drivers.registry import require_mutable_driver
    require_mutable_driver(driver_def.get("id", "unknown"))
    name, data = definition_file(driver_def)
    filepath = directory / name
    atomic_write_text(filepath, data.decode("utf-8"))

    log.info(f"Saved driver definition: {filepath}")
    return filepath


def _is_within(path: Path, root: Path) -> bool:
    """True if ``path`` resolves to a location inside ``root``."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def is_builtin_definition_path(filepath: Path) -> bool:
    """True if ``filepath`` lives in the read-only built-in definitions tree.

    The built-in ``.avcdriver`` files ship inside ``APP_DIR`` (the install
    tree on an installed/frozen deployment). They must never be unlinked or
    overwritten by an API call — there is no recovery short of reinstalling.
    """
    from openavc.system_config import DRIVER_DEFINITIONS_DIR

    return _is_within(filepath, DRIVER_DEFINITIONS_DIR)


def is_builtin_driver(
    driver_id: str,
    directories: Sequence[Path | str],
) -> bool:
    """True if ``driver_id`` is served by a read-only built-in with no override.

    A user copy in ``driver_repo`` with the same id (which the Driver Builder
    never creates — "Customize a copy" forks to a new ``<id>_copy``) takes
    precedence and is freely editable, so we only treat an id as a protected
    built-in when its only on-disk file is under the definitions tree.
    """
    from openavc.drivers.registry import is_reserved_driver
    if is_reserved_driver(driver_id):
        return True
    builtin_match = False
    user_match = False
    for filepath in _definitions_declaring(directories, driver_id):
        if is_builtin_definition_path(filepath):
            builtin_match = True
        else:
            user_match = True
    return builtin_match and not user_match


def _definitions_declaring(
    directories: Sequence[Path | str], driver_id: str
) -> Iterator[Path]:
    """Each ``.avcdriver`` file that declares ``driver_id``
    (``driver_id_from_file``), in library order: directories in the order
    given, files by name. A file whose id cannot be read is passed over."""
    for _dir_index, filepath in _library_files(directories, f"*{DRIVER_EXTENSION}"):
        if driver_id_from_file(filepath) == driver_id:
            yield filepath


def restore_driver_registration(
    driver_id: str,
    directories: Sequence[Path | str],
) -> bool:
    """Register ``driver_id`` again from whatever file serves it now
    (``serving_file``), once a door has taken a copy away.

    After a user copy that overrode a built-in is deleted (or renamed away),
    or a definition beside a Python driver of the same id is, the registry
    may still hold the removed copy's class, or have dropped the id. When a
    file still serves the id it is registered, so the id keeps working with
    that file's behaviour; otherwise the stale registration is dropped.

    Returns True if a file was registered, False if the id is now
    unregistered.
    """
    filepath = serving_file(directories, driver_id)
    if filepath is not None:
        try:
            driver_class = _class_from(filepath)
            if driver_class is not None:
                register_driver(driver_class)
                log.info(f"Restored driver '{driver_id}' from {filepath}")
                return True
        except Exception:  # Catch-all: class creation can fail in many ways
            log.exception(f"Failed to restore driver '{driver_id}' from {filepath}")
    unregister_driver(driver_id)
    return False


def _class_from(filepath: Path) -> type | None:
    """The driver class a library file defines: a definition's built from it,
    a Python file's from its module when it is imported, else imported now."""
    if filepath.suffix == DRIVER_EXTENSION:
        from openavc.drivers.configurable import create_configurable_driver_class

        driver_def = read_library_definition(filepath).definition
        return create_configurable_driver_class(driver_def) if driver_def is not None else None
    from openavc.drivers.python_modules import driver_class_in, module_name

    name = module_name(filepath)
    module = sys.modules.get(name)
    if module is not None:
        driver_class = driver_class_in(module, name)
        if driver_class is not None:
            return driver_class
    return read_library_python_driver(filepath).driver_class


def delete_driver_definition(
    driver_id: str,
    directories: Sequence[Path | str],
    *,
    keep: Path | str | None = None,
) -> bool:
    """
    Delete every definition file of a driver ID: a renamed copy too, as the
    uninstall and the Code view's delete take every file that is the driver,
    so no copy left behind can serve the id at the next start.

    Searches all provided directories. Returns True if a file was deleted.

    Never unlinks a shipped built-in (a file under the read-only definitions
    tree): a single API call with a built-in id would otherwise permanently
    remove a platform driver from the install tree with no recovery. A
    same-id user copy in ``driver_repo`` is still deleted.

    ``keep`` is a file left in place although it carries the id. A save
    writes the new file first and passes it here, so the driver is on disk
    throughout: deleting first left a gap where a reader found no file and a
    crash lost the driver.
    """
    kept = Path(keep).resolve() if keep is not None else None
    deleted = False
    for filepath in list(_definitions_declaring(directories, driver_id)):
        try:
            deleted = _delete_definition_file(filepath, kept) or deleted
        except OSError:
            continue
    return deleted


def replaced_definition(
    driver_id: str,
    directories: Sequence[Path | str],
    *,
    keep: Path | str,
) -> Path | None:
    """The definition file a save of ``driver_id`` to ``keep`` replaces: the
    one ``delete_driver_definition`` would remove, the first other file
    declaring the id that is not a shipped built-in. None when there is none."""
    kept = Path(keep).resolve()
    for filepath in _definitions_declaring(directories, driver_id):
        if _removable_definition(filepath, kept):
            return filepath
    return None


def _removable_definition(filepath: Path, kept: Path | None) -> bool:
    """Whether a definition file may go: not the one being kept, nor a
    shipped built-in."""
    if kept is not None and filepath.resolve() == kept:
        return False
    return not is_builtin_definition_path(filepath)


def _delete_definition_file(filepath: Path, kept: Path | None) -> bool:
    """Remove one definition file unless it is the one being kept or a
    shipped built-in; whether it was removed. An ``OSError`` reaches the
    caller, which goes on to the next file."""
    if not _removable_definition(filepath, kept):
        if is_builtin_definition_path(filepath):
            log.warning(f"Refusing to delete built-in driver definition: {filepath}")
        return False
    filepath.unlink()
    log.info(f"Deleted driver definition: {filepath}")
    return True


def list_driver_definitions(directories: Sequence[Path | str]) -> list[dict[str, Any]]:
    """
    List the driver definitions that serve their ids in the given directories.

    One entry per id, the copy the loader registers (``serving_file``'s rule
    among the definitions that load), in the order each id is first met:
    a later directory's copy replaces an earlier one's in place, and in one
    directory the file named for the id is listed over a renamed copy. An id
    a Python file serves is not a definition's to list. ``_source_file`` on
    each entry points at the listed file, and ``_revision`` is that file's
    (``driver_change.revision``).

    Returns a list of driver definition dicts.
    """
    loaded = {
        filepath: read
        for _dir_index, filepath in _library_files(directories, f"*{DRIVER_EXTENSION}")
        if (read := _definition_at_revision(filepath)) is not None
    }

    def loads(path: Path) -> bool:  # each definition was just read
        return path in loaded if path.suffix == DRIVER_EXTENSION else _loads(path)

    chosen = [
        _chosen(copies, driver_id, loads, refused_too=False)
        for driver_id, copies in _library_copies(directories).items()
    ]
    return [
        _listed_definition(*loaded[path], path)
        for path in chosen
        if path is not None and path.suffix == DRIVER_EXTENSION
    ]


def _definition_at_revision(filepath: Path) -> tuple[dict[str, Any], str] | None:
    """A definition file that loads, with its revision, or None. The revision
    is read before the definition: a write landing between the two leaves the
    revision older than what was read, so a save from it is refused as made
    from an older copy, never let through over a change it never saw."""
    from openavc.drivers.driver_change import file_revision

    try:
        revision: str | None = file_revision(filepath)
    except OSError:
        revision = None
    driver_def = load_driver_file(filepath)  # logs why a file it cannot take is skipped
    if driver_def is None or revision is None:
        return None
    return driver_def, revision


def _listed_definition(driver_def: dict[str, Any], revision: str, filepath: Path) -> dict[str, Any]:
    """A definition as the Builder lists it: with the file it was read from
    (``_source_file``), that file's revision, and how many comment lines it
    holds."""
    driver_def["_source_file"] = str(filepath)
    driver_def["_revision"] = revision
    driver_def["_comment_lines"] = count_comment_lines(filepath)
    return driver_def


def list_python_drivers(directories: Sequence[Path | str]) -> list[dict[str, Any]]:
    """
    List the Python driver files (.py) in the given directories, one per id.

    The file listed for an id is the copy the loader registers
    (``serving_file``'s rule among the Python files), or, when none of them
    loaded, the one that would, with why it did not. In the order each id is
    first met in library order. Metadata is read from the source without
    importing it (``python_driver_info``).
    """
    rows: list[tuple[int, dict[str, Any]]] = []
    order = {path: n for n, (_index, path) in enumerate(_python_driver_files(directories))}
    for driver_id, copies in _library_copies(directories).items():
        python = [copy for copy in copies if copy[1].suffix == ".py"]
        chosen = _chosen(python, driver_id, _loads, refused_too=True)
        if chosen is None:
            continue
        entry = _listed_entry(chosen)
        entry["loaded"], entry["load_error"] = _load_status(entry["id"], chosen)
        rows.append((order[chosen], entry))
    return [entry for _position, entry in sorted(rows, key=lambda row: row[0])]


_LISTED_FIELDS = ("id", "name", "manufacturer", "category")


def _listed_entry(filepath: Path) -> dict[str, Any]:
    """A file's row in the Code view's list, before its load status: the
    fields its source declares, else its file name and blanks."""
    entry: dict[str, Any] = {
        "id": filepath.stem,
        "filename": filepath.name,
        "name": filepath.stem,
        "manufacturer": "",
        "category": "",
        "loaded": False,
        "load_error": None,
        "devices_using": [],
    }
    info = python_driver_info(filepath)  # unreadable source keeps the defaults
    for field in _LISTED_FIELDS:
        if info.get(field):
            entry[field] = info[field]
    return entry


def _load_status(driver_id: str, filepath: Path) -> tuple[bool, str | None]:
    """``(loaded, load_error)`` for a listed file.

    Loaded when the class registered under its id came from this file.
    Otherwise the Code view says why: the reason it did not load
    (``refused_files``), else its module is in ``sys.modules`` but what is
    registered under this id is not from it (a duplicate id, or a last reload
    that left another id registered), or it never imported.
    """
    refused = refused_files.load_error(filepath)
    if refused is not None:
        return False, refused
    if _registered_from(driver_id, filepath):
        return True, None
    if _is_imported(filepath):
        return False, (
            "Imported but not registered: a duplicate driver ID "
            "or a failed last reload"
        )
    return False, "Not loaded"


def _registered_from(driver_id: str, filepath: Path) -> bool:
    """Whether the class registered under ``driver_id`` was imported from
    the Python file ``filepath``."""
    from openavc.drivers.python_modules import imported_from
    from openavc.drivers.registry import get_driver_class

    driver_class = get_driver_class(driver_id)
    return driver_class is not None and imported_from(driver_class, filepath)


def _ast_dict_to_simple(node: Any) -> dict[str, str | int | float | bool]:
    """Extract simple key-value pairs from an AST Dict node."""
    result: dict[str, str | int | float | bool] = {}
    if not isinstance(node, ast.Dict):
        return result
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if isinstance(value, ast.Constant) and isinstance(value.value, (str, int, float, bool)):
                result[key.value] = value.value
    return result
