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

import importlib.util
import inspect
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from server.utils.logger import get_logger

log = get_logger(__name__)

# Required top-level fields in a driver definition
REQUIRED_FIELDS = {"id", "name", "transport"}

# File extension for driver definitions
DRIVER_EXTENSION = ".avcdriver"


def validate_driver_definition(driver_def: dict[str, Any]) -> list[str]:
    """
    Validate a driver definition.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in driver_def:
            errors.append(f"Missing required field: {field}")

    transport = driver_def.get("transport", "")
    if transport and transport not in ("tcp", "serial", "udp", "http", "osc"):
        errors.append(f"Unsupported transport: {transport}")

    # Validate response patterns compile and don't have catastrophic backtracking
    for i, resp in enumerate(driver_def.get("responses", [])):
        # OSC responses use "address" key — validate it starts with /
        if "address" in resp:
            addr = resp["address"]
            if not isinstance(addr, str) or not addr.startswith("/"):
                errors.append(f"Response {i}: OSC address must start with '/'")
            continue

        pattern = resp.get("pattern", "") or resp.get("match", "")
        if not pattern:
            errors.append(f"Response {i}: missing pattern, match, or address")
        else:
            try:
                compiled = re.compile(pattern)
                # Heuristic: detect nested quantifiers that cause exponential
                # backtracking (e.g., (.+)+, (.*)*). Test with multiple non-matching
                # strings — if any take measurably long, warn.
                if re.search(r'[+*]\)[+*?]', pattern):
                    import time as _time
                    test_strings = [
                        "a" * 25,
                        "x" * 25 + "!",
                        "0123456789" * 3,
                        "\t \n" * 10,
                    ]
                    for test_str in test_strings:
                        t0 = _time.monotonic()
                        compiled.search(test_str)
                        elapsed = _time.monotonic() - t0
                        if elapsed > 0.1:
                            errors.append(
                                f"Response {i}: regex '{pattern}' has nested "
                                f"quantifiers that may cause catastrophic backtracking"
                            )
                            break
            except re.error as e:
                errors.append(f"Response {i}: invalid regex '{pattern}': {e}")

    # Validate commands structure
    for cmd_name, cmd_def in driver_def.get("commands", {}).items():
        if not isinstance(cmd_def, dict):
            errors.append(f"Command '{cmd_name}': must be a dict")
            continue
        # TCP/serial commands need send/string, HTTP need path/method, OSC needs address
        has_send = cmd_def.get("send") or cmd_def.get("string")
        has_http = cmd_def.get("path") or cmd_def.get("method")
        has_osc = cmd_def.get("address") is not None
        if not has_send and not has_http and not has_osc:
            errors.append(
                f"Command '{cmd_name}': must have 'send' (TCP/serial), "
                f"'path'/'method' (HTTP), or 'address' (OSC)"
            )

    # Validate state_variables structure
    valid_types = {"string", "integer", "number", "boolean", "enum", "float"}
    for var_name, var_def in driver_def.get("state_variables", {}).items():
        if not isinstance(var_def, dict):
            errors.append(f"State variable '{var_name}': must be a dict")
            continue
        var_type = var_def.get("type", "")
        if var_type and var_type not in valid_types:
            errors.append(f"State variable '{var_name}': unknown type '{var_type}'")
        if not var_def.get("label"):
            errors.append(f"State variable '{var_name}': missing 'label'")

    return errors


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

    return driver_def


def load_driver_files(directories: list[Path | str]) -> int:
    """
    Scan directories for .avcdriver files, validate them,
    create ConfigurableDriver subclasses, and register them.

    Returns the number of drivers successfully loaded.
    """
    from server.core.device_manager import register_driver
    from server.drivers.configurable import create_configurable_driver_class

    count = 0
    seen_ids: set[str] = set()
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                continue

            driver_id = driver_def.get("id", "")
            if driver_id in seen_ids:
                log.warning(f"Duplicate driver ID '{driver_id}' in {filepath.name} — skipping")
                continue
            seen_ids.add(driver_id)
            try:
                driver_class = create_configurable_driver_class(driver_def)
                register_driver(driver_class)
                count += 1
                log.info(f"Loaded driver: {driver_id} from {filepath.name}")
            except Exception:  # Catch-all: YAML parsing/validation can fail in many ways
                log.exception(f"Failed to create driver class from {filepath}")

    return count


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

    return driver_class


def load_python_drivers(directories: list[Path | str]) -> int:
    """
    Scan directories for .py driver files, load them, and register.

    Returns the number of drivers successfully loaded.
    """
    from server.core.device_manager import register_driver

    count = 0
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob("*.py")):
            # Skip __init__.py and other non-driver files
            if filepath.name.startswith("_"):
                continue

            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                continue

            driver_id = driver_class.DRIVER_INFO.get("id", "")
            try:
                register_driver(driver_class)
                count += 1
                log.info(f"Loaded Python driver: {driver_id} from {filepath.name}")
            except Exception:  # Catch-all: isolates one bad driver from breaking all loading
                log.exception(f"Failed to register Python driver from {filepath}")

    return count


def load_all_drivers(directories: list[Path | str]) -> int:
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
    filepath.write_text(text, encoding="utf-8")

    log.info(f"Saved driver definition: {filepath}")
    return filepath


def delete_driver_definition(
    driver_id: str,
    directories: list[Path | str],
) -> bool:
    """
    Delete a driver definition file by driver ID.

    Searches all provided directories. Returns True if a file was deleted.
    """
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue
        for filepath in dir_path.glob(f"*{DRIVER_EXTENSION}"):
            try:
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == driver_id:
                    filepath.unlink()
                    log.info(f"Deleted driver definition: {filepath}")
                    return True
            except (OSError, yaml.YAMLError):
                continue
    return False


def list_driver_definitions(directories: list[Path | str]) -> list[dict[str, Any]]:
    """
    List all driver definitions from the given directories.

    Returns a list of driver definition dicts.
    """
    definitions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                continue
            driver_id = driver_def.get("id", "")
            if driver_id in seen_ids:
                continue
            seen_ids.add(driver_id)
            # Add source info
            driver_def["_source_file"] = str(filepath)
            definitions.append(driver_def)

    return definitions


def list_python_drivers(directories: list[Path | str]) -> list[dict[str, Any]]:
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
            if filepath.name.startswith("_"):
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
                # Check if there was a load error by trying module lookup
                module_name = f"openavc_driver_{filepath.stem}"
                if module_name not in sys.modules:
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
    sys.modules.pop(module_name, None)

    final_class = load_python_driver_file(filepath)
    if final_class is None:
        # This shouldn't happen since validation passed, but handle it
        return {"status": "error", "error": "Failed to load driver after validation passed"}

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
