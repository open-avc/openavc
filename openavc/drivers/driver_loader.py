"""
OpenAVC Driver Loader — scans for .avcdriver definition files and Python
driver modules, and registers them.

Supported formats:
    - .avcdriver  YAML definition files (loaded via ConfigurableDriver)
    - .py         Python modules containing BaseDriver subclasses

Directories scanned:
    - openavc/drivers/definitions/  (built-in .avcdriver definitions)
    - driver_repo/                 (community/user drivers — .avcdriver and .py)

Each valid driver is registered in the global driver registry.
"""

from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator, Sequence

import yaml

from openavc.drivers.avcdriver_semantic import (
    unknown_key_errors as _unknown_key_errors,
    validate_driver_definition as _validate_definition_rules,
    validate_driver_issues as _validate_definition_issues,
)
from openavc.drivers.python_modules import (
    is_imported as _is_imported,
    load_python_driver_file as load_python_driver_file,
    python_source_syntax_error as python_source_syntax_error,
    reload_python_driver as reload_python_driver,
)
from openavc.drivers.registry import (
    is_driver_registered,
    register_driver,
    unregister_driver,
)
from openavc.drivers.spec import REQUIRED_FIELDS as REQUIRED_FIELDS
from openavc.utils.logger import get_logger

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

# What the name of a driver file the import and install doors write may be
# made of, before its extension or companion suffix (a regular expression).
DRIVER_FILE_STEM = r"[a-zA-Z0-9_\-]+"


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
    if not relpath:
        return []
    relative = Path(relpath)
    if relative.is_absolute() or relative.parent != Path("."):
        return []
    return [main_path.with_name(relative.name)]


def _is_driver_file(filepath: Path) -> bool:
    """Return False for companion / helper .py files that aren't drivers."""
    name = filepath.name
    if name.startswith("_"):
        return False
    if any(name.endswith(suf) for suf in COMPANION_SUFFIXES):
        return False
    return True


def _discovery_hint_errors(driver_def: dict[str, Any]) -> list[str]:
    """Deep-validate the ``discovery:`` block through the real hints parser.

    Lives here rather than in avcdriver_semantic because the parser pulls in
    the discovery engine; the rules module stays importable without it.
    """
    from openavc.discovery.hints import DiscoveryHintError, parse_driver_discovery

    try:
        parse_driver_discovery(driver_def)
    except DiscoveryHintError as exc:
        return [f"discovery: {exc}"]
    return []


def validate_driver_definition(
    driver_def: dict[str, Any], *, strict: bool = False
) -> list[str]:
    """
    Validate a driver definition.

    Returns a list of error strings. Empty list means valid.

    The rules live in ``avcdriver_semantic`` (shared with the community
    catalog's validator); this wrapper adds the discovery-block check, which
    needs the discovery engine's parser.

    ``strict`` also rejects keys the contract doesn't declare. Pass it from
    the gates that *create* a driver (save, import, the AI tool); leave it off
    when loading one, where ``load_driver_file`` warns instead.
    """
    return _validate_definition_rules(
        driver_def, discovery_validator=_discovery_hint_errors, strict=strict
    )


def validate_driver_issues(
    driver_def: dict[str, Any], *, strict: bool = False
) -> list[dict[str, str]]:
    """The same validation as ``validate_driver_definition``, as issue records.

    ``{severity, message, path}`` per issue — errors plus the warnings the
    error list has no way to carry. Serves ``POST /driver-definitions/validate``,
    which is how the Driver Builder gets its issues instead of holding a
    second copy of the contract rules.
    """
    return _validate_definition_issues(
        driver_def, discovery_validator=_discovery_hint_errors, strict=strict
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


def load_driver_file(filepath: Path) -> dict[str, Any] | None:
    """
    Load and validate a single driver definition file (.avcdriver YAML).

    Returns the driver definition dict, or None if invalid.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
        driver_def = yaml.safe_load(text)
    except UnicodeDecodeError:
        log.warning(f"Driver file {filepath} is not UTF-8 text; save it as UTF-8 to load it")
        return None
    except (OSError, yaml.YAMLError) as e:
        log.warning(f"Failed to load driver file {filepath}: {e}")
        return None

    if not isinstance(driver_def, dict):
        log.warning(f"Driver file {filepath} is not a valid YAML mapping")
        return None

    errors = validate_driver_definition(driver_def)
    if errors:
        log.warning(
            f"Invalid driver definition in {filepath}: "
            + "; ".join(errors)
        )
        return None

    # Loud, but not fatal. A key the contract doesn't declare is nearly always
    # a typo — and a typo'd section silently does nothing, which is why it is
    # worth saying out loud. It is not worth dropping the driver over: a
    # definition written for a newer platform can carry a field this one hasn't
    # learned yet, and refusing it here would take the device offline instead.
    # The authoring gates reject the same thing outright, so a typo can only
    # reach this point on a driver that was written elsewhere.
    for message in _unknown_key_errors(driver_def):
        log.warning(f"Driver definition {filepath}: {message}")

    # Companion existence check: a ``python:`` declaration that points at
    # a missing file would auto-register two SignalRules under
    # ``custom_<id>_companion_(udp|tcp)`` at hint-load time, but no
    # evidence producer would ever fire — the device would be matchable
    # in theory and silently invisible in practice. Reject up front.
    companion_relpath = companion_relpath_from_def(driver_def)
    if companion_relpath:
        companion_path = (filepath.parent / companion_relpath).resolve()
        if not companion_path.is_file():
            log.warning(
                f"Driver {filepath.name} declares discovery.python "
                f"file={companion_relpath!r} but no such file exists "
                f"at {companion_path}; skipping driver"
            )
            return None

    return driver_def


def _python_driver_id(filepath: Path) -> str | None:
    """Extract ``DRIVER_INFO['id']`` from a .py driver via AST (no import)."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    # DRIVER_INFO may be a module-level assignment or a class attribute;
    # ast.walk covers both. The first dict that names an id decides: an empty
    # one reads as no id.
    for node in ast.walk(tree):
        if _assigns_driver_info_dict(node):
            declared = _literal_id(node.value)
            if declared is not None:
                return declared or None
    return None


def _assigns_driver_info_dict(node: ast.AST) -> bool:
    """``DRIVER_INFO = {...}``, alone or among several names (not annotated)."""
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
        return False
    return any(isinstance(t, ast.Name) and t.id == "DRIVER_INFO" for t in node.targets)


def _literal_id(node: ast.Dict) -> str | None:
    """The first ``"id"`` key's text value, or None if no key gives one."""
    for key, value in zip(node.keys, node.values):
        if not (isinstance(key, ast.Constant) and key.value == "id"):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def driver_id_from_file(filepath: Path) -> str | None:
    """Return the driver id a file *declares*, without importing it.

    Drivers are registered under their declared id, which may differ from the
    filename stem (uploads keep their original filename — routes/drivers.py
    does not rename). Resolving a repo file by stem alone therefore misses
    such drivers; callers that map an id back to its file (export bundling,
    builtin/community source classification) use this instead.

    - ``.py``: the ``id`` key of the module/class ``DRIVER_INFO`` dict, read
      via ``ast`` so the module is never executed.
    - everything else (``.avcdriver`` YAML): the top-level ``id`` field.

    Returns the declared id, or None if it can't be determined.
    """
    try:
        if filepath.suffix == ".py":
            return _python_driver_id(filepath)
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            did = data.get("id")
            return did if isinstance(did, str) and did else None
    except (OSError, yaml.YAMLError, SyntaxError, ValueError) as e:
        log.debug(f"Could not read driver id from {filepath}: {e}")
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
    a driver id is looked up by what each file declares. When several files
    are the same driver, the one named for its id is the one that loads, is
    edited in Code and is exported; deleting the driver removes them all.
    Companions and underscore helpers are never driver files.
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


class AmbiguousDriverFile(Exception):
    """Several files are one driver and none is named for its id."""

    def __init__(self, driver_id: str, files: Sequence[Path]):
        self.driver_id = driver_id
        self.files = list(files)
        names = ", ".join(path.name for path in self.files)
        super().__init__(
            f"More than one file in the driver library is driver "
            f"'{driver_id}' ({names}). Delete the driver, then import the "
            f"copy you want to keep."
        )


def python_driver_file(directory: Path | str, driver_id: str) -> Path:
    """THE file that is Python driver ``driver_id`` in ``directory``.

    ``<id>.py`` when it is that driver, else the one file that declares the
    id, else ``<id>.py`` (which may not exist: the caller decides whether
    that is "not found" or "create it"). Raises ``AmbiguousDriverFile`` when
    several files declare the id and none is named for it, rather than
    guessing which one to edit or replace.
    """
    conventional = Path(directory) / f"{driver_id}.py"
    if conventional.is_file() and _is_driver(conventional, driver_id):
        return conventional  # it wins over any renamed copy anyway
    files = driver_files_for(directory, driver_id, ".py")
    if not files:
        return conventional
    if len(files) == 1 or files[0].stem == driver_id:
        return files[0]
    raise AmbiguousDriverFile(driver_id, files)


def find_driver_file_by_id(
    directories: Sequence[Path | str], driver_id: str
) -> Path | None:
    """Find the ``.avcdriver`` file on disk that declares ``driver_id``.

    Scans the given directories for a YAML driver whose declared id matches.
    When more than one directory declares the id, the match from the LAST
    directory wins — mirroring load precedence, where a user copy in
    ``driver_repo`` overrides a same-id built-in — so callers always get the
    file that actually serves the driver. Matches on the declared id, not
    the filename stem, since uploads keep their original name (see
    ``driver_id_from_file``). Returns the path, or None if no
    ``.avcdriver`` declares that id. (Python ``.py`` drivers are out of
    scope — they have their own reload path keyed by filename.)
    """
    found: Path | None = None
    for directory in directories:
        match = _definition_in(Path(directory), driver_id)
        if match is not None:
            found = match  # a later directory overrides
    return found


def _definition_in(dir_path: Path, driver_id: str) -> Path | None:
    """The ``.avcdriver`` in one directory that declares ``driver_id``: the
    file named for the id first, as everywhere (``driver_files_for``), then
    by name."""
    if not dir_path.is_dir():
        return None
    candidates = sorted(
        dir_path.glob(f"*{DRIVER_EXTENSION}"),
        key=lambda path: (path.stem != driver_id, path.name),
    )
    return next(
        (filepath for filepath in candidates if driver_id_from_file(filepath) == driver_id),
        None,
    )


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


def _takes_the_id(
    loaded: tuple[int, Path] | None, dir_index: int, filepath: Path, driver_id: str
) -> bool:
    """Whether ``filepath`` should serve ``driver_id`` over the copy already
    loaded (``(dir index, file)``, or None). A later directory wins; within
    one, the file named for the id wins over a renamed copy, and otherwise
    the first file stays."""
    if loaded is None:
        return True
    loaded_index, loaded_path = loaded
    if loaded_index != dir_index:
        log.info(
            f"Driver '{driver_id}' from {filepath.name} overrides the copy "
            f"loaded from an earlier directory"
        )
        return True
    if filepath.stem == driver_id and loaded_path.stem != driver_id:
        log.warning(
            f"Driver '{driver_id}' is in both {loaded_path.name} and "
            f"{filepath.name}; {filepath.name} is used"
        )
        return True
    log.warning(
        f"Driver '{driver_id}' is in both {loaded_path.name} and "
        f"{filepath.name}; {loaded_path.name} is used"
    )
    return False


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
        driver_def = load_driver_file(filepath)
        if driver_def is None:
            continue

        driver_id = driver_def.get("id", "")
        if not _takes_the_id(registered.get(driver_id), dir_index, filepath, driver_id):
            continue
        try:
            driver_class = create_configurable_driver_class(driver_def)
            register_driver(driver_class)
            registered[driver_id] = (dir_index, filepath)
            log.info(f"Loaded driver: {driver_id} from {filepath.name}")
        except Exception:  # Catch-all: YAML parsing/validation can fail in many ways
            log.exception(f"Failed to create driver class from {filepath}")

    return len(registered)


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
        driver_class = load_python_driver_file(filepath)
        if driver_class is None:
            continue

        driver_id = driver_class.DRIVER_INFO.get("id", "")
        if not _takes_the_id(registered.get(driver_id), dir_index, filepath, driver_id):
            continue
        try:
            register_driver(driver_class)
            registered[driver_id] = (dir_index, filepath)
            log.info(f"Loaded Python driver: {driver_id} from {filepath.name}")
        except Exception:
            log.exception(f"Failed to register Python driver from {filepath}")

    return len(registered)


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


def save_driver_definition(
    driver_def: dict[str, Any],
    directory: Path | str,
) -> Path:
    """
    Save a driver definition as a .avcdriver YAML file.

    The filename is derived from the driver's id field.
    Returns the path to the saved file.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    driver_id = driver_def.get("id", "unknown")
    from openavc.drivers.registry import require_mutable_driver
    require_mutable_driver(driver_id)
    # Sanitize filename
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in driver_id)
    filepath = directory / f"{safe_id}{DRIVER_EXTENSION}"

    text = yaml.dump(
        driver_def,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    fd, tmp = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, str(filepath))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

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
    """Each ``.avcdriver`` file whose top-level ``id`` is ``driver_id``.

    Directories in the order given, files in the order the file system lists
    them (``delete_driver_definition`` removes the first it may). A file that
    cannot be read, decoded or parsed is passed over.
    ``find_driver_file_by_id`` reads ids by its own rule (``driver_id_from_file``).
    """
    for dir_path in directories:
        dir_path = Path(dir_path)
        if dir_path.exists():
            yield from _declaring_in(dir_path, driver_id)


def _declaring_in(dir_path: Path, driver_id: str) -> Iterator[Path]:
    for filepath in dir_path.glob(f"*{DRIVER_EXTENSION}"):
        try:
            data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):  # ValueError: not UTF-8
            continue
        if isinstance(data, dict) and data.get("id") == driver_id:
            yield filepath


def restore_driver_registration(
    driver_id: str,
    directories: Sequence[Path | str],
) -> bool:
    """Re-register ``driver_id`` from whatever ``.avcdriver`` file still serves it.

    After a user copy that overrode a built-in is deleted (or renamed away),
    the registry still holds the removed copy's class. If any on-disk
    definition still declares the id — normally the shipped built-in — load
    and register it so the id keeps working with its original behavior;
    otherwise drop the stale registration.

    Returns True if a definition was re-registered, False if the id is now
    unregistered. (YAML definitions only; Python drivers have their own
    filename-keyed reload path.)
    """
    from openavc.drivers.configurable import create_configurable_driver_class

    filepath = find_driver_file_by_id(directories, driver_id)
    if filepath is not None:
        driver_def = load_driver_file(filepath)
        if driver_def is not None:
            try:
                register_driver(create_configurable_driver_class(driver_def))
                log.info(f"Restored driver '{driver_id}' from {filepath}")
                return True
            except Exception:  # Catch-all: class creation can fail in many ways
                log.exception(f"Failed to restore driver '{driver_id}' from {filepath}")
    unregister_driver(driver_id)
    return False


def delete_driver_definition(
    driver_id: str,
    directories: Sequence[Path | str],
    *,
    keep: Path | str | None = None,
) -> bool:
    """
    Delete a driver definition file by driver ID.

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
    for filepath in _definitions_declaring(directories, driver_id):
        try:
            if _delete_definition_file(filepath, kept):
                return True
        except OSError:
            continue
    return False


def _delete_definition_file(filepath: Path, kept: Path | None) -> bool:
    """Remove one definition file unless it is the one being kept or a
    shipped built-in; whether it was removed. An ``OSError`` reaches the
    caller, which goes on to the next file."""
    if kept is not None and filepath.resolve() == kept:
        return False
    if is_builtin_definition_path(filepath):
        log.warning(f"Refusing to delete built-in driver definition: {filepath}")
        return False
    filepath.unlink()
    log.info(f"Deleted driver definition: {filepath}")
    return True


def list_driver_definitions(directories: Sequence[Path | str]) -> list[dict[str, Any]]:
    """
    List all driver definitions from the given directories.

    Duplicate ids resolve the same way loading does: a definition in a
    later directory (the user's ``driver_repo``) replaces a same-id one
    from an earlier directory (the built-ins), so the listing shows the
    copy that actually serves the id at runtime. ``_source_file`` on each
    entry points at the winning file.

    Returns a list of driver definition dicts.
    """
    definitions: list[dict[str, Any]] = []
    # driver_id -> (index of the dir it came from, position in definitions)
    slots: dict[str, tuple[int, int]] = {}

    for dir_index, filepath in _library_files(directories, f"*{DRIVER_EXTENSION}"):
        driver_def = load_driver_file(filepath)
        if driver_def is None:
            continue
        driver_id = driver_def.get("id", "")
        driver_def["_source_file"] = str(filepath)
        driver_def["_comment_lines"] = count_comment_lines(filepath)
        slot = slots.get(driver_id)
        if slot is None:
            slots[driver_id] = (dir_index, len(definitions))
            definitions.append(driver_def)
        elif slot[0] != dir_index:  # a later directory replaces it in place
            definitions[slot[1]] = driver_def
            slots[driver_id] = (dir_index, slot[1])
        # else a duplicate within one directory: its first file stays

    return definitions


def list_python_drivers(directories: Sequence[Path | str]) -> list[dict[str, Any]]:
    """
    List all Python driver files (.py) from the given directories.

    Returns metadata for each file without doing a full import — uses AST
    parsing to extract DRIVER_INFO safely.
    """
    drivers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for _dir_index, filepath in _python_driver_files(directories):
        entry = _listed_entry(filepath)
        driver_id = entry["id"]
        if driver_id in seen_ids:
            # The file named for the id is the one that loads
            # (load_python_drivers), so it is the one listed.
            if filepath.stem != driver_id:
                continue
            drivers[:] = [d for d in drivers if d["id"] != driver_id]
        seen_ids.add(driver_id)
        entry["loaded"], entry["load_error"] = _load_status(driver_id, filepath)
        drivers.append(entry)
    return drivers


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
    try:
        info = _first_class_driver_info(filepath)
    except Exception:
        info = {}  # unreadable source keeps the file-name defaults
    for field in _LISTED_FIELDS:
        if info.get(field):
            entry[field] = info[field]
    return entry


def _first_class_driver_info(filepath: Path) -> dict[str, str | int | float | bool]:
    """The literal values of the first class's ``DRIVER_INFO = {...}``.

    Only the file's first class is read, and only a plain assignment in its
    body, so a helper class written first leaves the defaults. Loading and
    ``driver_id_from_file`` read a file's id by other rules.
    """
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    first_class = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)), None
    )
    if first_class is None:
        return {}
    for item in first_class.body:
        if _is_plain_driver_info(item):
            return _ast_dict_to_simple(item.value)
    return {}


def _is_plain_driver_info(node: ast.stmt) -> bool:
    """``DRIVER_INFO = {...}``: one name, a dict literal."""
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return False
    target = node.targets[0]
    return isinstance(target, ast.Name) and target.id == "DRIVER_INFO" and isinstance(node.value, ast.Dict)


def _load_status(driver_id: str, filepath: Path) -> tuple[bool, str | None]:
    """``(loaded, load_error)`` for a listed file.

    Registered under its id: loaded. Otherwise the Code view says why: its
    module is in ``sys.modules`` but nothing is registered under this id (a
    duplicate id, a class it could not find, or a last reload that left
    another id registered), or it never imported.
    """
    if is_driver_registered(driver_id):
        return True, None
    if _is_imported(filepath):
        return False, (
            "Imported but not registered — duplicate driver ID "
            "or a failed last reload"
        )
    return False, "Not loaded"


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
