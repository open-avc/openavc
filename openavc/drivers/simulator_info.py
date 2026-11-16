"""THE check of a Python simulator's ``SIMULATOR_INFO``: read from its source,
never imported, and held to the keys the driver contract declares for it
(``spec.SIMULATOR_INFO_FIELDS``: a YAML driver's ``simulator:`` section's keys
and the seven that say which device it stands in for,
``spec.SIMULATOR_IDENTITY``).

``check_simulator_info`` reads it as the simulator does, each key written as a
null left out (``spec.without_null_settings``), then judges:

* each declared key's value against the node that declares it, by the shape
  rule a ``simulator:`` section is judged by (``shape_checks.ShapeWalk``): an
  error, since the simulator cannot use the value;
* a key the contract does not declare: a warning naming the nearest declared
  key, since the simulator never reads it;
* a value the source reader could not see is never judged, and is named
  (``python_info._Coverage``, THE skip).

What a driver and its simulator must agree on (the ids, the transports, the
state the simulator seeds, the controls the Simulator UI can draw) is the
simulator validator's (``openavc/simulator/python_parity.py``).

Where it is asked: ``python -m openavc.drivers.check`` and the community
catalog's CI check a driver's companion simulator (``companion_simulator``)
with the driver, and ``drivers.check`` checks a ``_sim.py`` named on its own;
the simulator validator reads ``SIMULATOR_INFO`` through
``read_simulator_info``.

Purity contract: standard library plus the contract modules, like
``python_info``; vendored into openavc-drivers.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openavc.drivers.avcdriver_semantic import UNEVALUATED_KEY
from openavc.drivers.declared_names import did_you_mean
from openavc.drivers.python_info import _Coverage, lenient_ast_value
from openavc.drivers.shape_checks import ShapeWalk
from openavc.drivers.spec import SIMULATOR_INFO_FIELDS, without_null_settings

#: How every finding names the mapping it is about.
_PLACE = "SIMULATOR_INFO"


@dataclass
class SimulatorInfoVerdict:
    """What the check found in one ``SIMULATOR_INFO``: errors (a value the
    simulator cannot use), warnings (a key it never reads) and the places it
    could not judge, each with why."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def companion_simulator(driver_path: Path) -> Path:
    """Where a driver's simulator sits: ``<driver>_sim.py`` beside it."""
    return driver_path.with_name(f"{driver_path.stem}_sim.py")


def read_simulator_info(sim_path: Path) -> dict | None:
    """``SIMULATOR_INFO`` from a Python simulator file, or None when there is
    no readable mapping (the file does not parse, or no class assigns one).

    Read off the first class body that assigns it, through the lenient
    evaluator the driver side uses (``python_info.lenient_ast_value``): a
    value it cannot evaluate comes back as the UNEVALUATED marker, never as
    its own source text, so a check never judges an invention.
    """
    try:
        tree = ast.parse(sim_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for item in _class_body_items(tree):
        if any(_names_simulator_info(target) for target in _assigned(item)):
            value = lenient_ast_value(item.value, _PLACE, [])
            return value if isinstance(value, dict) else None
    return None


def _class_body_items(tree: ast.AST):
    """Every statement in every class body, classes in ``ast.walk`` order."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield from node.body


def _assigned(item: ast.stmt) -> list[ast.expr]:
    """What a statement assigns to: an assignment's targets, or an annotated
    assignment's target when it has a value."""
    if isinstance(item, ast.Assign):
        return item.targets
    if isinstance(item, ast.AnnAssign) and item.value is not None:
        return [item.target]
    return []


def _names_simulator_info(target: ast.expr) -> bool:
    return isinstance(target, ast.Name) and target.id == _PLACE


def check_simulator_info(info: dict[str, Any]) -> SimulatorInfoVerdict:
    """THE verdict on one ``SIMULATOR_INFO`` (the module docstring says what it
    judges): the keys the contract does not declare, then each declared key's
    value, in the order the contract declares them."""
    info = without_null_settings(info)
    verdict = SimulatorInfoVerdict()
    known = set(SIMULATOR_INFO_FIELDS)
    for key in [k for k in info if k != UNEVALUATED_KEY and k not in known]:
        verdict.warnings.append(
            f"{_PLACE}: unknown key '{key}'{did_you_mean(key, known)}; the simulator does not read it"
        )
    coverage = _Coverage(info)
    for key in [k for k in SIMULATOR_INFO_FIELDS if k in info]:
        place = f"{_PLACE}.{key}"
        if coverage.judged(place, info[key]):
            walk = ShapeWalk("python")
            walk.walk(SIMULATOR_INFO_FIELDS[key], info[key], place)
            verdict.errors.extend(sentence for _, sentence in walk.refusals)
    verdict.skipped = coverage.lines()
    return verdict


def check_simulator_file(sim_path: Path) -> SimulatorInfoVerdict:
    """The verdict on a simulator file's ``SIMULATOR_INFO``; one it cannot
    read is an error, never a pass."""
    info = read_simulator_info(sim_path)
    if info is None:
        return SimulatorInfoVerdict(errors=[
            f"no {_PLACE} could be read: the file does not parse, or no class in it "
            f"assigns {_PLACE} a mapping"
        ])
    return check_simulator_info(info)
