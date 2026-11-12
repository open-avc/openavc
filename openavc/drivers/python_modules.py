"""A Python driver file as a module in this process: importing it, which class
in it is the driver, reloading it while the server runs, and what the author
is told when that fails.

A ``.py`` driver is imported as ``openavc_driver_<file stem>`` (``module_name``),
and that entry in ``sys.modules`` is what "this file is loaded" means to the
rest of the platform: a failed import leaves none, an import that finds no
driver class leaves one (the Code view then lists the file as imported but not
registered), and a reload whose final import fails puts the previous module
back. Removing a driver's file removes its entry (``forget_module``).

``driver_class_in`` is the one rule for which class is the driver: the first,
by name, defined in that module, whose ``DRIVER_INFO`` is a mapping with an id
(any other ``DRIVER_INFO`` makes it no driver, so the scan never raises). The reload's
trial import, its final import and its reading of the previous module's id
all ask it, so a file that passes the trial is one the final import accepts.

The name is also what the IDE files a driver's own log lines under (a driver
that logs through ``getLogger(__name__)``): the Code view's console matches
the ``openavc_driver_`` prefix and builds one driver's filter from it, so a
change to the name changes those two places in ``web/programmer`` too.

Logs as ``openavc.drivers.driver_loader``, where these lines have always
appeared. ``driver_loader`` re-exports ``load_python_driver_file``,
``reload_python_driver`` and ``python_source_syntax_error``, the import path
the routes, the project library and the tests use.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import re
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from openavc.drivers.python_info import (
    python_driver_info_issues as _python_driver_info_issues,
)
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.utils.logger import get_logger

log = get_logger("openavc.drivers.driver_loader")

_TRIAL_PREFIX = "_openavc_driver_validate_"

# The import roots a driver written before the platform moved under
# ``openavc`` still names. A driver that survived an upgrade untouched in
# driver_repo/ fails on these, and the author is told how to replace it.
_RETIRED_ROOTS = ("server", "simulator")


def module_name(filepath: Path) -> str:
    """The name a driver file is imported under."""
    return f"openavc_driver_{filepath.stem}"


def is_imported(filepath: Path) -> bool:
    """Whether a driver file's module is in ``sys.modules``."""
    return module_name(filepath) in sys.modules


def forget_module(filepath: Path) -> None:
    """Drop a driver file's module, once the file is gone."""
    sys.modules.pop(module_name(filepath), None)


def driver_class_in(module: Any, name: str) -> type | None:
    """The driver class a module imported as ``name`` defines, or None.

    ``inspect.getmembers`` lists classes by name, so with two driver classes
    in one file the alphabetically first is the driver.
    """
    from openavc.drivers.base import BaseDriver

    for _attr, obj in inspect.getmembers(module, inspect.isclass):
        if _is_driver_class(obj, name, BaseDriver):
            return obj
    return None


def _is_driver_class(obj: type, name: str, base: type) -> bool:
    """A subclass of ``base`` (not ``base`` itself), defined in module
    ``name``, whose ``DRIVER_INFO`` is a mapping with an id. Asked in that order."""
    if not issubclass(obj, base) or obj is base:
        return False
    if obj.__module__ != name:
        return False
    info = getattr(obj, "DRIVER_INFO", None)
    return isinstance(info, Mapping) and bool(info.get("id"))


def _import(filepath: Path, name: str) -> ModuleType | None:
    """Execute a driver file as module ``name``; None when it cannot be one.

    The module is in ``sys.modules`` before its code runs, so imports inside
    the driver resolve; the caller removes it when the code raises.
    """
    spec = importlib.util.spec_from_file_location(name, filepath)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _imports_a_retired_name(exc: Exception) -> bool:
    if not isinstance(exc, ModuleNotFoundError):
        return False
    return (exc.name or "").split(".")[0] in _RETIRED_ROOTS


def _log_import_failure(filepath: Path, exc: Exception) -> None:
    """Say why a driver did not import. Called inside the ``except`` block,
    so ``log.exception`` carries the traceback, which names a missing
    third-party package; a driver that imports a retired platform name is told
    the fix instead, since its traceback names none."""
    if _imports_a_retired_name(exc):
        log.error(
            f"Driver {filepath.name} was written for an older OpenAVC and "
            f"imports '{exc.name}', which no longer exists. Update it from "
            f"Browse Drivers (or re-install it) to get the current version."
        )
    else:
        log.exception(f"Failed to load Python driver from {filepath}")


def load_python_driver_file(filepath: Path) -> type | None:
    """Import a ``.py`` driver file and return its driver class, or None.

    A module that fails to import is removed from ``sys.modules``: left
    behind, it would read as loaded in the Code view and confuse a later
    reload. One that imports but defines no driver class stays.
    """
    name = module_name(filepath)
    try:
        module = _import(filepath, name)
    except Exception as exc:  # Catch-all: exec_module runs arbitrary driver code
        _log_import_failure(filepath, exc)
        sys.modules.pop(name, None)
        return None
    if module is None:
        log.warning(f"Could not create module spec for {filepath}")
        return None

    driver_class = driver_class_in(module, name)
    if driver_class is None:
        log.warning(f"No BaseDriver subclass with DRIVER_INFO found in {filepath}")
    else:
        _warn_python_driver_info_issues(driver_class)
    return driver_class


def _warn_python_driver_info_issues(driver_class: type) -> None:
    """Structural sanity warnings for a Python driver's DRIVER_INFO.

    Warn-only, never rejects — a driver written for a newer platform must not
    vanish at load and take its devices offline. The rules themselves live in
    ``python_info``, so ``python -m openavc.drivers.check`` and the community
    catalog reach the same verdict from the source text; this door adds the two
    facts only the loaded class knows.
    """
    from openavc.drivers.base import BaseDriver

    info = getattr(driver_class, "DRIVER_INFO", {}) or {}
    driver_id = info.get("id", driver_class.__name__)

    for msg in _python_driver_info_issues(
        info,
        overrides_run_setup_action=(
            driver_class.run_setup_action is not BaseDriver.run_setup_action
        ),
        overrides_set_device_setting=(
            driver_class.set_device_setting is not BaseDriver.set_device_setting
        ),
    ):
        log.warning(f"Python driver '{driver_id}': {msg}")


def syntax_error_report(exc: SyntaxError, filename: str) -> dict[str, Any]:
    """Render a SyntaxError the way the Code view already knows how to show it.

    ``{"error": "SyntaxError: <msg> (<file>, line N)", "line": N}`` — the line
    is a separate field because the editor turns it into a clickable marker on
    the offending line, so a formatter that folds it into the prose would break
    the link. Shared by :func:`reload_python_driver` (which gets its
    SyntaxError from importing the file) and the save door (which gets one from
    parsing source that is not on disk yet), so a driver that will not parse is
    described in exactly the same words wherever the author meets it.
    """
    return {
        "error": f"SyntaxError: {exc.msg} ({filename}, line {exc.lineno})",
        "line": exc.lineno,
    }


def python_source_syntax_error(source: str, filename: str) -> dict[str, Any] | None:
    """Report why ``source`` will not parse, or None when it parses.

    Deliberately a *parse*, not an import: the caller is a save door deciding
    whether bytes may be written, and importing would both run the module's
    top-level code and fail on a dependency the driver legitimately imports.
    A file that parses but cannot import is work in progress; a file that does
    not parse cannot load on any future startup.
    """
    try:
        ast.parse(source, filename=filename)
    except SyntaxError as e:
        return syntax_error_report(e, filename)
    return None


def _error_line(exc: BaseException, filepath: Path) -> int | None:
    """The line of the driver file nearest the error: the last traceback
    frame in that file, or None when no frame is in it."""
    line = None
    for frame_text in traceback.format_exception(type(exc), exc, exc.__traceback__):
        match = re.search(r"line (\d+)", frame_text)
        if match and str(filepath) in frame_text:
            line = int(match.group(1))
    return line


def _trial_import(filepath: Path) -> type | dict[str, Any]:
    """Import the file under a throwaway name and return its driver class, or
    the error result the reload returns. Nothing it imports stays loaded."""
    name = f"{_TRIAL_PREFIX}{filepath.stem}"
    try:
        module = _import(filepath, name)
        if module is None:
            return {"status": "error", "error": f"Could not create module spec for {filepath}"}
        driver_class = driver_class_in(module, name)
    except SyntaxError as e:
        return {"status": "error", **syntax_error_report(e, filepath.name), "old_driver_preserved": True}
    except Exception as e:
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "line": _error_line(e, filepath),
            "old_driver_preserved": True,
        }
    finally:
        sys.modules.pop(name, None)
    if driver_class is None:
        return {
            "status": "error",
            "error": "No BaseDriver subclass with DRIVER_INFO found",
            "old_driver_preserved": True,
        }
    return driver_class


def reload_python_driver(filepath: Path) -> dict[str, Any]:
    """Hot-reload a Python driver from disk.

    The new code is imported under a throwaway name first; if that fails, the
    running driver is untouched and the result says why. Then the file is
    imported for real, the previous id is dropped when the id changed, and the
    new class is registered.

    Returns ``{status, driver_id, old_driver_id}``, or ``{status: "error",
    error, ...}``. Reconnecting the devices that use it is the caller's job.
    """
    trial = _trial_import(filepath)
    if isinstance(trial, dict):
        return trial
    new_driver_id = trial.DRIVER_INFO["id"]

    # The previous id is read before its module is dropped.
    name = module_name(filepath)
    old_module = sys.modules.get(name)
    old_class = driver_class_in(old_module, name) if old_module is not None else None
    old_driver_id = old_class.DRIVER_INFO["id"] if old_class is not None else None
    sys.modules.pop(name, None)

    final_class = load_python_driver_file(filepath)
    if final_class is None:
        # The previous class is still registered and serving devices: put its
        # module back so sys.modules and the registry agree.
        if old_module is not None:
            sys.modules[name] = old_module
        return {
            "status": "error",
            "error": (
                "Failed to reload driver after validation passed; the "
                "previously loaded driver is still active"
            ),
            "old_driver_preserved": True,
        }

    if old_driver_id and old_driver_id != new_driver_id:
        unregister_driver(old_driver_id)
    register_driver(final_class)
    log.info(f"Hot-reloaded Python driver: {new_driver_id} from {filepath.name}")
    return {
        "status": "reloaded",
        "driver_id": new_driver_id,
        "old_driver_id": old_driver_id,
    }
