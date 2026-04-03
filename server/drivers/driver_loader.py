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
    if transport and transport not in ("tcp", "serial", "udp", "http"):
        errors.append(f"Unsupported transport: {transport}")

    # Validate response patterns compile
    for i, resp in enumerate(driver_def.get("responses", [])):
        pattern = resp.get("pattern", "") or resp.get("match", "")
        if not pattern:
            errors.append(f"Response {i}: missing pattern or match")
        else:
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"Response {i}: invalid regex '{pattern}': {e}")

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


# --- Backward compatibility aliases ---
# These map old names to new names so existing code doesn't break during transition
load_json_driver = load_driver_file
load_json_drivers = load_driver_files
