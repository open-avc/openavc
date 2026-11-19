"""THE rule for which simulator a ``_sim.py`` file offers for each driver id.

A simulator file is imported as a module of its own (``module_name``: one name
per file, so a rescan replaces the module rather than adding another), put in
``sys.modules`` before its code runs, as an import puts it there: a dataclass
under ``from __future__ import annotations`` looks its own module up by name
while the class is made. A file whose code raises leaves no module behind.

Its simulators are the ``BaseSimulator`` subclasses the file itself defines
(``__module__``, so a base class it imports, or another file's simulator, is
never its own) whose ``SIMULATOR_INFO`` names a driver id. One class per
driver id: a class listed under a second name is still one class, while two
different classes naming one id (both written out, or one inheriting the
other's ``SIMULATOR_INFO``) serve neither, refused in the contract's sentence
(``drivers/simulator_info.claim_conflicts``). A reader that cannot import the
file reads the same classes from its source (``simulator_info.simulator_claims``),
held to this rule by ``tests/test_simulator_claims_agree.py``.

One driver folder holds one simulator class per driver id as well
(``load_simulator_folder``): two of its files naming one id serve neither,
and a file that fails to import keeps why under its driver's id (its name,
``<driver id>_sim.py``). ``SimulatorManager`` asks it for each folder it
scans; which folder serves an id when two hold one is the scan's order (an
installed driver's folder last), not this module's.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import logging
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from openavc.core.connection_fault import simulator_error_text
from openavc.drivers.simulator_info import claim_conflicts
from openavc.simulator.base import BaseSimulator

# Where these lines have always appeared.
logger = logging.getLogger("openavc.simulator.engine")


@dataclass(frozen=True)
class SimulatorFile:
    """What one simulator file (or one driver folder's) offers: the class
    serving each driver id, and each id it names that no class serves, with
    why."""

    classes: dict[str, type[BaseSimulator]] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)


def module_name(path: Path) -> str:
    """The name a simulator file is imported under: its stem, and a mark of
    where it is, so the same file name in two driver folders is two modules."""
    where = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"openavc_simulator_{path.stem}_{where}"


def load_simulator_file(path: Path) -> SimulatorFile:
    """Import ``path`` and say which class serves each driver id it names.
    Raises what the file's own code raises."""
    module = _import(path, module_name(path))
    by_id: dict[str, list[type[BaseSimulator]]] = {}
    for _attr, obj in inspect.getmembers(module, inspect.isclass):
        driver_id = _driver_id_of(obj, module.__name__)
        if driver_id and obj not in by_id.setdefault(driver_id, []):
            by_id[driver_id].append(obj)
    refused = claim_conflicts(
        (cls.__name__, driver_id) for driver_id, found in by_id.items() for cls in found
    )
    return SimulatorFile(
        classes={driver_id: found[0] for driver_id, found in by_id.items() if driver_id not in refused},
        refused=refused,
    )


def load_simulator_folder(paths: Iterable[Path]) -> SimulatorFile:
    """The simulators one driver folder's ``_sim.py`` files offer, one class
    per driver id across them all (the module docstring has the rule). A
    file that fails to import is named under its driver's id only when no
    other file serves that id."""
    offered: list[tuple[Path, SimulatorFile]] = []
    failed: dict[str, str] = {}
    for path in paths:
        try:
            offered.append((path, load_simulator_file(path)))
        except Exception as e:
            logger.exception("Failed to import simulator: %s", path)
            failed[path.stem.removesuffix("_sim")] = simulator_error_text(e)
    refused = _refused_in(offered)
    for driver_id, why in refused.items():
        logger.error("No simulator for %s: %s", driver_id, why)
    classes = {
        driver_id: cls
        for _path, found in offered
        for driver_id, cls in found.classes.items()
        if driver_id not in refused
    }
    failed = {i: why for i, why in failed.items() if i not in classes and i not in refused}
    return SimulatorFile(classes=classes, refused={**failed, **refused})


def _refused_in(offered: list[tuple[Path, SimulatorFile]]) -> dict[str, str]:
    """Each driver id the folder's files refuse: one a file refuses itself,
    and one classes in two of its files name."""
    refused: dict[str, str] = {}
    for _path, found in offered:
        refused.update(found.refused)
    refused.update(claim_conflicts(
        (f"{cls.__name__} in {path.name}", driver_id)
        for path, found in offered
        for driver_id, cls in found.classes.items()
    ))
    return refused


def _import(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path.name} cannot be imported as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _driver_id_of(obj: type, module: str) -> str | None:
    """The driver id ``obj`` serves: a ``BaseSimulator`` subclass defined in
    ``module`` whose ``SIMULATOR_INFO`` names one, as text."""
    if obj.__module__ != module or not issubclass(obj, BaseSimulator):
        return None
    info = getattr(obj, "SIMULATOR_INFO", None)
    driver_id = info.get("driver_id") if isinstance(info, Mapping) else None
    return driver_id if isinstance(driver_id, str) and driver_id else None
