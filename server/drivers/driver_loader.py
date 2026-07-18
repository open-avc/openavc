"""
OpenAVC Driver Loader — scans for .avcdriver definition files and Python
driver modules, and registers them.

Supported formats:
    - .avcdriver  YAML definition files (loaded via ConfigurableDriver)
    - .py         Python modules containing BaseDriver subclasses

Directories scanned:
    - server/drivers/definitions/  (built-in .avcdriver definitions)
    - driver_repo/                 (community/user drivers — .avcdriver and .py)

Each valid driver is registered in the global driver registry.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import sys
import tempfile
from pathlib import Path
from typing import Sequence, Any

import yaml

from server.drivers.avcdriver_semantic import (
    validate_driver_definition as _validate_definition_rules,
)
from server.drivers.spec import REQUIRED_FIELDS as REQUIRED_FIELDS
from server.utils.logger import get_logger

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
_COMPANION_SUFFIXES: tuple[str, ...] = ("_discovery.py", "_sim.py")


def _is_driver_file(filepath: Path) -> bool:
    """Return False for companion / helper .py files that aren't drivers."""
    name = filepath.name
    if name.startswith("_"):
        return False
    if any(name.endswith(suf) for suf in _COMPANION_SUFFIXES):
        return False
    return True


def _discovery_hint_errors(driver_def: dict[str, Any]) -> list[str]:
    """Deep-validate the ``discovery:`` block through the real hints parser.

    Lives here rather than in avcdriver_semantic because the parser pulls in
    the discovery engine; the rules module stays importable without it.
    """
    from server.discovery.hints import DiscoveryHintError, parse_driver_discovery

    try:
        parse_driver_discovery(driver_def)
    except DiscoveryHintError as exc:
        return [f"discovery: {exc}"]
    return []


def validate_driver_definition(driver_def: dict[str, Any]) -> list[str]:
    """
    Validate a driver definition.

    Returns a list of error strings. Empty list means valid.

    The rules live in ``avcdriver_semantic`` (shared with the community
    catalog's validator); this wrapper adds the discovery-block check, which
    needs the discovery engine's parser.
    """
    return _validate_definition_rules(
        driver_def, discovery_validator=_discovery_hint_errors
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


def load_driver_file(filepath: Path) -> dict[str, Any] | None:
    """
    Load and validate a single driver definition file (.avcdriver YAML).

    Returns the driver definition dict, or None if invalid.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
        driver_def = yaml.safe_load(text)
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
    # ast.walk covers both. First declared id wins.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "DRIVER_INFO" for t in node.targets
        ):
            continue
        if isinstance(node.value, ast.Dict):
            for k, v in zip(node.value.keys, node.value.values):
                if (
                    isinstance(k, ast.Constant) and k.value == "id"
                    and isinstance(v, ast.Constant) and isinstance(v.value, str)
                ):
                    return v.value or None
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
        dir_path = Path(directory)
        if not dir_path.is_dir():
            continue
        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            if driver_id_from_file(filepath) == driver_id:
                found = filepath
                break  # first match within a directory; a later dir may override
    return found


def load_driver_files(directories: Sequence[Path | str]) -> int:
    """
    Scan directories for .avcdriver files, validate them,
    create ConfigurableDriver subclasses, and register them.

    Directories are scanned in order and LATER directories take precedence:
    a same-id driver in a later directory (the user's ``driver_repo``)
    replaces one from an earlier directory (the read-only built-in
    definitions), so an installed or edited user copy actually serves the
    id. Within a single directory a duplicate id is a conflict — the first
    file (alphabetical) wins and the rest are skipped with a warning.

    Returns the number of distinct drivers successfully registered.
    """
    from server.core.device_manager import register_driver
    from server.drivers.configurable import create_configurable_driver_class

    registered: dict[str, int] = {}  # driver_id -> index of the dir it loaded from
    for dir_index, dir_path in enumerate(directories):
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                continue

            driver_id = driver_def.get("id", "")
            if driver_id in registered:
                if registered[driver_id] == dir_index:
                    log.warning(f"Duplicate driver ID '{driver_id}' in {filepath.name} — skipping")
                    continue
                log.info(
                    f"Driver '{driver_id}' from {filepath.name} overrides the "
                    f"copy loaded from an earlier directory"
                )
            try:
                driver_class = create_configurable_driver_class(driver_def)
                register_driver(driver_class)
                registered[driver_id] = dir_index
                log.info(f"Loaded driver: {driver_id} from {filepath.name}")
            except Exception:  # Catch-all: YAML parsing/validation can fail in many ways
                log.exception(f"Failed to create driver class from {filepath}")

    return len(registered)


def load_python_driver_file(filepath: Path) -> type | None:
    """
    Load a Python driver module from a .py file and return the BaseDriver subclass.

    Uses importlib to dynamically load the module, then scans it for classes
    that are subclasses of BaseDriver (but not BaseDriver itself).

    Returns the driver class, or None if no valid driver was found.
    """
    from server.drivers.base import BaseDriver

    module_name = f"openavc_driver_{filepath.stem}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None or spec.loader is None:
            log.warning(f"Could not create module spec for {filepath}")
            return None

        module = importlib.util.module_from_spec(spec)
        # Add to sys.modules so relative imports within the driver work
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception:  # Catch-all: exec_module runs arbitrary driver code
        log.exception(f"Failed to load Python driver from {filepath}")
        # Drop the half-initialized module: leaving it resident for the process
        # lifetime leaks state, defeats the "module not loaded" health check in
        # list_python_drivers (so the panel shows no load error), and makes a
        # later hot-reload see an inconsistent sys.modules.
        sys.modules.pop(module_name, None)
        return None

    # Find BaseDriver subclasses defined in this module
    driver_class = None
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, BaseDriver)
            and obj is not BaseDriver
            and obj.__module__ == module_name
        ):
            if hasattr(obj, "DRIVER_INFO") and obj.DRIVER_INFO.get("id"):
                driver_class = obj
                break  # Take the first valid one

    if driver_class is None:
        log.warning(f"No BaseDriver subclass with DRIVER_INFO found in {filepath}")
    else:
        _warn_python_driver_info_issues(driver_class)

    return driver_class


def _warn_python_driver_info_issues(driver_class: type) -> None:
    """Structural sanity warnings for a Python driver's DRIVER_INFO.

    Warn-only, never rejects: Python drivers may populate ``commands`` /
    state at runtime (the Q-SYS pattern), so cross-references against the
    class-level dict can false-positive — but STRUCTURE is static, and a
    malformed entry used to be silently skipped by the action resolver (the
    button just never appears) or fail at first write. YAML drivers get the
    equivalent as hard load errors via validate_driver_definition.
    """
    from server.drivers.base import BaseDriver

    info = getattr(driver_class, "DRIVER_INFO", {}) or {}
    driver_id = info.get("id", driver_class.__name__)
    issues: list[str] = []

    qa = info.get("quick_actions")
    if qa is not None and (
        not isinstance(qa, list) or any(not isinstance(x, str) for x in qa)
    ):
        issues.append("quick_actions must be a list of command-id strings")

    actions = info.get("actions")
    declares_setup = False
    if actions is not None and not isinstance(actions, list):
        issues.append("actions must be a list")
    elif isinstance(actions, list):
        for i, entry in enumerate(actions):
            if not isinstance(entry, dict) or not entry.get("id"):
                issues.append(
                    f"actions[{i}] must be a mapping with an 'id' "
                    f"(the resolver silently drops it otherwise)"
                )
                continue
            kind = entry.get("kind", "command")
            if kind not in ("command", "setup"):
                issues.append(
                    f"actions[{i}] ('{entry.get('id')}'): unknown kind {kind!r}"
                )
            elif kind == "setup":
                declares_setup = True
            availability = entry.get("availability", "online")
            if availability not in ("online", "offline", "always"):
                issues.append(
                    f"actions[{i}] ('{entry.get('id')}'): unknown availability "
                    f"{availability!r}"
                )
    if declares_setup and driver_class.run_setup_action is BaseDriver.run_setup_action:
        issues.append(
            "declares a kind:'setup' action but does not override "
            "run_setup_action — the wizard will 501 on launch"
        )

    settings = info.get("device_settings")
    if settings is not None and not isinstance(settings, dict):
        issues.append("device_settings must be a mapping")
    elif isinstance(settings, dict):
        for key, sdef in settings.items():
            if not isinstance(sdef, dict):
                issues.append(f"device_settings['{key}'] must be a mapping")
        if settings and (
            driver_class.set_device_setting is BaseDriver.set_device_setting
        ):
            issues.append(
                "declares device_settings but does not override "
                "set_device_setting — every write will 501"
            )

    for msg in issues:
        log.warning(f"Python driver '{driver_id}': {msg}")


def load_python_drivers(directories: Sequence[Path | str]) -> int:
    """
    Scan directories for .py driver files, load them, and register.

    Same precedence as ``load_driver_files``: later directories win for a
    duplicate id; within one directory the first file (alphabetical) wins.

    Returns the number of distinct drivers successfully registered.
    """
    from server.core.device_manager import register_driver

    registered: dict[str, int] = {}  # driver_id -> index of the dir it loaded from
    for dir_index, dir_path in enumerate(directories):
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob("*.py")):
            if not _is_driver_file(filepath):
                continue

            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                continue

            driver_id = driver_class.DRIVER_INFO.get("id", "")
            if driver_id in registered:
                if registered[driver_id] == dir_index:
                    log.warning(f"Duplicate Python driver ID '{driver_id}' in {filepath.name} — skipping")
                    continue
                log.info(
                    f"Python driver '{driver_id}' from {filepath.name} overrides "
                    f"the copy loaded from an earlier directory"
                )
            try:
                register_driver(driver_class)
                registered[driver_id] = dir_index
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
    from server.system_config import DRIVER_DEFINITIONS_DIR

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
    builtin_match = False
    user_match = False
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue
        for filepath in dir_path.glob(f"*{DRIVER_EXTENSION}"):
            try:
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not (isinstance(data, dict) and data.get("id") == driver_id):
                continue
            if is_builtin_definition_path(filepath):
                builtin_match = True
            else:
                user_match = True
    return builtin_match and not user_match


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
    from server.core.device_manager import register_driver, unregister_driver
    from server.drivers.configurable import create_configurable_driver_class

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
) -> bool:
    """
    Delete a driver definition file by driver ID.

    Searches all provided directories. Returns True if a file was deleted.

    Never unlinks a shipped built-in (a file under the read-only definitions
    tree): a single API call with a built-in id would otherwise permanently
    remove a platform driver from the install tree with no recovery. A
    same-id user copy in ``driver_repo`` is still deleted.
    """
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue
        for filepath in dir_path.glob(f"*{DRIVER_EXTENSION}"):
            try:
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == driver_id:
                    if is_builtin_definition_path(filepath):
                        log.warning(
                            f"Refusing to delete built-in driver definition: {filepath}"
                        )
                        continue
                    filepath.unlink()
                    log.info(f"Deleted driver definition: {filepath}")
                    return True
            except (OSError, yaml.YAMLError):
                continue
    return False


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

    for dir_index, dir_path in enumerate(directories):
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                continue
            driver_id = driver_def.get("id", "")
            # Add source info
            driver_def["_source_file"] = str(filepath)
            if driver_id in slots:
                prev_dir, position = slots[driver_id]
                if prev_dir == dir_index:
                    continue  # duplicate within one directory — first file wins
                definitions[position] = driver_def  # later directory overrides
                slots[driver_id] = (dir_index, position)
                continue
            slots[driver_id] = (dir_index, len(definitions))
            definitions.append(driver_def)

    return definitions


def list_python_drivers(directories: Sequence[Path | str]) -> list[dict[str, Any]]:
    """
    List all Python driver files (.py) from the given directories.

    Returns metadata for each file without doing a full import — uses AST
    parsing to extract DRIVER_INFO safely.
    """
    import ast

    from server.core.device_manager import is_driver_registered

    drivers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob("*.py")):
            if not _is_driver_file(filepath):
                continue

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

            # Try AST extraction for DRIVER_INFO metadata
            try:
                source = filepath.read_text(encoding="utf-8")
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        for item in node.body:
                            if (
                                isinstance(item, ast.Assign)
                                and len(item.targets) == 1
                                and isinstance(item.targets[0], ast.Name)
                                and item.targets[0].id == "DRIVER_INFO"
                                and isinstance(item.value, ast.Dict)
                            ):
                                info = _ast_dict_to_simple(item.value)
                                if info.get("id"):
                                    entry["id"] = info["id"]
                                if info.get("name"):
                                    entry["name"] = info["name"]
                                if info.get("manufacturer"):
                                    entry["manufacturer"] = info["manufacturer"]
                                if info.get("category"):
                                    entry["category"] = info["category"]
                                break
                        break  # Only check first class
            except Exception:
                pass  # Fall back to filename-based defaults

            driver_id = entry["id"]
            if driver_id in seen_ids:
                continue
            seen_ids.add(driver_id)

            # Check if loaded in registry
            if is_driver_registered(driver_id):
                entry["loaded"] = True
            else:
                # Not registered under this file's id. Distinguish the two
                # failure modes so the Code tab / Installed Drivers panel can
                # tell the integrator WHY the driver isn't usable instead of
                # showing it as cleanly loaded with no error:
                #   - module present in sys.modules but not registered under
                #     this id → it imported but registration was rejected
                #     (duplicate driver id) or a last hot-reload left a stale
                #     class registered under a different id.
                #   - module absent → it never loaded (failed import, or the
                #     startup scan hasn't run for this file).
                module_name = f"openavc_driver_{filepath.stem}"
                if module_name in sys.modules:
                    entry["load_error"] = (
                        "Imported but not registered — duplicate driver ID "
                        "or a failed last reload"
                    )
                else:
                    entry["load_error"] = "Not loaded"

            drivers.append(entry)

    return drivers


def _ast_dict_to_simple(node: Any) -> dict[str, str | int | float | bool]:
    """Extract simple key-value pairs from an AST Dict node."""
    import ast

    result: dict[str, str | int | float | bool] = {}
    if not isinstance(node, ast.Dict):
        return result
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if isinstance(value, ast.Constant) and isinstance(value.value, (str, int, float, bool)):
                result[key.value] = value.value
    return result


def reload_python_driver(
    filepath: Path,
) -> dict[str, Any]:
    """
    Hot-reload a Python driver from disk.

    Safety: validates the new code by importing into a temporary module first.
    If the new code fails to import, the old driver stays active.

    Returns a dict with status, driver_id, and any errors.
    Does NOT handle device reconnection — that's the caller's responsibility.
    """
    from server.core.device_manager import register_driver, unregister_driver
    from server.drivers.base import BaseDriver

    stem = filepath.stem
    module_name = f"openavc_driver_{stem}"
    temp_module_name = f"_openavc_driver_validate_{stem}"

    # --- Step 1: Validate new code by importing into a temp module ---
    new_driver_class = None
    try:
        spec = importlib.util.spec_from_file_location(temp_module_name, filepath)
        if spec is None or spec.loader is None:
            return {"status": "error", "error": f"Could not create module spec for {filepath}"}

        temp_module = importlib.util.module_from_spec(spec)
        sys.modules[temp_module_name] = temp_module
        spec.loader.exec_module(temp_module)

        # Find BaseDriver subclass
        for _name, obj in inspect.getmembers(temp_module, inspect.isclass):
            if (
                issubclass(obj, BaseDriver)
                and obj is not BaseDriver
                and obj.__module__ == temp_module_name
            ):
                if hasattr(obj, "DRIVER_INFO") and obj.DRIVER_INFO.get("id"):
                    new_driver_class = obj
                    break
    except SyntaxError as e:
        return {
            "status": "error",
            "error": f"SyntaxError: {e.msg} ({filepath.name}, line {e.lineno})",
            "line": e.lineno,
            "old_driver_preserved": True,
        }
    except Exception as e:
        # Try to extract line number from traceback
        import traceback
        tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
        line_num = None
        for tb_line in tb_lines:
            import re as _re
            match = _re.search(r'line (\d+)', tb_line)
            if match and str(filepath) in tb_line:
                line_num = int(match.group(1))
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "line": line_num,
            "old_driver_preserved": True,
        }
    finally:
        # Clean up temp module
        sys.modules.pop(temp_module_name, None)

    if new_driver_class is None:
        return {
            "status": "error",
            "error": "No BaseDriver subclass with DRIVER_INFO found",
            "old_driver_preserved": True,
        }

    new_driver_id = new_driver_class.DRIVER_INFO["id"]

    # --- Step 2: Find old driver ID from this file (may differ if ID changed) ---
    old_driver_id = None
    if module_name in sys.modules:
        old_module = sys.modules[module_name]
        for _name, obj in inspect.getmembers(old_module, inspect.isclass):
            if (
                issubclass(obj, BaseDriver)
                and obj is not BaseDriver
                and obj.__module__ == module_name
            ):
                if hasattr(obj, "DRIVER_INFO") and obj.DRIVER_INFO.get("id"):
                    old_driver_id = obj.DRIVER_INFO["id"]
                    break

    # --- Step 3: Remove old module and load properly ---
    # Keep a handle on the old module so we can restore it if the canonical
    # re-import fails after Step-1 validation already passed (a TOCTOU edit /
    # delete of the file between validation and here, or an environment error).
    # Without the restore, sys.modules would be left empty while the old class
    # stays registered — registry and sys.modules disagreeing, with no repair.
    old_module = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)

    final_class = load_python_driver_file(filepath)
    if final_class is None:
        # Reload failed after validation passed. The old class is still
        # registered (Step 4 hasn't run), so restore its module to keep
        # sys.modules consistent and report that it is still serving devices —
        # matching the old_driver_preserved contract of the Step-1 error paths.
        if old_module is not None:
            sys.modules[module_name] = old_module
        return {
            "status": "error",
            "error": (
                "Failed to reload driver after validation passed; the "
                "previously loaded driver is still active"
            ),
            "old_driver_preserved": True,
        }

    # --- Step 4: Unregister old and register new ---
    if old_driver_id and old_driver_id != new_driver_id:
        unregister_driver(old_driver_id)
    register_driver(final_class)

    log.info(f"Hot-reloaded Python driver: {new_driver_id} from {filepath.name}")

    return {
        "status": "reloaded",
        "driver_id": new_driver_id,
        "old_driver_id": old_driver_id,
    }


# --- Backward compatibility aliases ---
# These map old names to new names so existing code doesn't break during transition
load_json_driver = load_driver_file
load_json_drivers = load_driver_files
