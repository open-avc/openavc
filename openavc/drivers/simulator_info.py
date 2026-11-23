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

Which driver ids a simulator file serves is the simulator's loader's rule
(``openavc/simulator/simulator_file.py``): each class the file defines whose
``SIMULATOR_INFO`` names a driver id, one class per id. ``simulator_claims``
reads the same classes from the source, for a reader that cannot import the
file (the community catalog works out each driver's "simulated" from it), and
``claim_conflicts`` is THE sentence for two classes naming one id, which the
loader, this check (``check_simulator_file``) and the catalog's check across
its files all give.

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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openavc.drivers.avcdriver_semantic import UNEVALUATED_KEY
from openavc.drivers.declared_names import did_you_mean
from openavc.drivers.python_driver_class import derives, file_classes, inherited_table
from openavc.drivers.python_info import UNEVALUATED, _Coverage, lenient_ast_value
from openavc.drivers.python_source import SourceDecodeError, python_source
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
    no readable mapping (the import cannot decode the file, it does not
    parse, or no class assigns one).

    Read off the class the simulator serves, the first whose table names a
    driver id (``simulator_claims``: its own table, or the one a base in the
    file gives it); in a file where none names one, off the first class body
    that assigns it. Through the lenient evaluator the driver side uses
    (``python_info.lenient_ast_value``): a value it cannot evaluate comes back
    as the UNEVALUATED marker, never as its own source text, and a table
    merged from another mapping carries ``UNEVALUATED_KEY``, so a check never
    judges an invention.
    """
    try:
        return _simulator_info_in(python_source(sim_path))
    except SourceDecodeError:
        return None


def _simulator_info_in(source: str) -> dict | None:
    """``read_simulator_info`` on source text already read."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    table = _served_table(tree)
    if table is None:
        table = next((
            item.value for item in _class_body_items(tree)
            if any(_names_simulator_info(target) for target in _assigned(item))
        ), None)
    if table is None:
        return None
    value = lenient_ast_value(table, _PLACE, [])
    return value if isinstance(value, dict) else None


def _served_table(tree: ast.Module) -> ast.Dict | None:
    """The table of the first class ``_claims_in`` would take, or None when
    no class names a driver id."""
    classes = file_classes(tree)
    for name, node in classes.items():
        table = inherited_table(node, classes, _PLACE) if derives(node) else None
        if _claim(name, table) is not None:
            return table
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
    read is an error, never a pass, and so are two classes naming one driver
    id (``claim_conflicts``)."""
    try:
        source = python_source(sim_path)
    except SourceDecodeError as e:
        return SimulatorInfoVerdict(errors=[f"the file {e.without_name}"])
    info = _simulator_info_in(source)
    if info is None:
        return SimulatorInfoVerdict(errors=[
            f"no {_PLACE} could be read: the file does not parse, or no class in it "
            f"assigns {_PLACE} a mapping"
        ])
    verdict = check_simulator_info(info)
    claims = _claims_in(source)
    verdict.errors.extend(claim_conflicts((c.class_name, c.driver_id) for c in claims).values())
    return verdict


@dataclass(frozen=True)
class SimulatorClaim:
    """A class in a simulator file whose ``SIMULATOR_INFO`` names a driver
    id: the class's name and the id, or None for an id written in code (or
    merged in from another mapping), which a reader of the source cannot see."""

    class_name: str
    driver_id: str | None


def simulator_claims(sim_path: Path) -> list[SimulatorClaim]:
    """THE classes a simulator file offers, read from its source as the
    simulator's loader takes them from the imported file: each class the file
    defines at its top level, deriving from another, whose ``SIMULATOR_INFO``
    (its own, or the one a class in the file it derives from gives it) names
    a driver id. A table naming none (missing or empty) is no driver's
    simulator. A file that does not parse claims nothing; one the import
    cannot decode raises ``SourceDecodeError``."""
    return _claims_in(python_source(sim_path))


def _claims_in(source: str) -> list[SimulatorClaim]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    classes = file_classes(tree)
    claims = []
    for name, node in classes.items():
        table = inherited_table(node, classes, _PLACE) if derives(node) else None
        claim = _claim(name, table)
        if claim is not None:
            claims.append(claim)
    return claims


def _claim(class_name: str, table: ast.Dict | None) -> SimulatorClaim | None:
    """What a class's table says about its driver id, or None when it names
    none: an id it cannot read (written in code, or merged in from another
    mapping that may hold one) is a claim of an unknown id."""
    if table is None:
        return None
    info = lenient_ast_value(table, _PLACE, [])
    if not isinstance(info, dict):
        return None
    driver_id = info.get("driver_id")
    if _id_unseen(info, driver_id):
        return SimulatorClaim(class_name, None)
    if isinstance(driver_id, str) and driver_id:
        return SimulatorClaim(class_name, driver_id)
    return None


def _id_unseen(info: dict, driver_id: Any) -> bool:
    """Whether the id is written in code, or may be merged in from another
    mapping the table spreads into itself."""
    if driver_id is UNEVALUATED:
        return True
    return driver_id is None and UNEVALUATED_KEY in info


def claim_conflicts(claims: Iterable[tuple[str, str | None]]) -> dict[str, str]:
    """Each driver id two or more different names claim (a name is a class,
    or a class and where it is), with THE sentence saying none of them is
    used. A claim of an unknown id (None) is never a conflict."""
    by_id: dict[str, list[str]] = {}
    for name, driver_id in claims:
        if driver_id is not None and name not in by_id.setdefault(driver_id, []):
            by_id[driver_id].append(name)
    return {
        driver_id: _two_simulators_sentence(driver_id, names)
        for driver_id, names in by_id.items()
        if len(names) > 1
    }


def _two_simulators_sentence(driver_id: str, names: list[str]) -> str:
    """Names in sorted order, so every door words one conflict alike."""
    names = sorted(names)
    listed = ", ".join(names[:-1]) + f" and {names[-1]}"
    return (
        f"{listed} each name driver id '{driver_id}' in {_PLACE}, and a driver "
        "has one simulator, so none of them is used. Keep the id on one class; "
        "a class another builds on can leave driver_id out."
    )
