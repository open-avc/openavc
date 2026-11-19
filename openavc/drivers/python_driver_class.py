"""Which class in a Python driver file is the driver, read without running it.

The runtime registers the class ``python_modules.driver_class_in`` takes: the
first by name of the module's own ``BaseDriver`` subclasses whose
``DRIVER_INFO`` has an id, an inherited table counting. Every reader of a
``.py`` driver's source (the checker, the catalog build, the identity guard
before a write, the loader's id and the listings) asks ``driver_class_info``
for that class's table, through ``python_info.driver_info_ast``, so they read
the class that runs.

``inherited_table`` is THE rule for which table a class in the file has, read
as Python's lookup reads it: its own, else the first a class it derives from
in the same file gives it. The driver's ``DRIVER_INFO`` and a simulator's
``SIMULATOR_INFO`` (``simulator_info.simulator_claims``) are both read by it.

Purity contract, same as ``python_info``: standard library only. The
community catalog vendors this file beside it.
"""

from __future__ import annotations

import ast


def driver_class_info(tree: ast.Module) -> ast.Dict | None:
    """THE class a Python driver's ``DRIVER_INFO`` is read from: the module's
    own classes by name, the first whose table may name an id, its own or the
    one a class it derives from in the same file gives it. A table whose id
    is empty or missing is no driver's, so the next class is asked; one whose
    id is built in code may be, so it is taken.

    The driver class derives from ``BaseDriver``, so a class deriving from
    nothing is asked only when no class in the file derives from anything (a
    sketch or a fixture). When no table names an id, the first table is read
    all the same, so the checker says what is wrong with it rather than that
    there is none."""
    classes = file_classes(tree)
    derived = [name for name, node in classes.items() if derives(node)]
    first: ast.Dict | None = None
    for name in sorted(derived or classes):
        found = inherited_table(classes[name], classes, _DRIVER_INFO)
        if found is not None and _may_name_an_id(found):
            return found
        first = first or found
    return first


def derives(node: ast.ClassDef) -> bool:
    """Whether a class names a base other than ``object``."""
    return any(not (isinstance(base, ast.Name) and base.id == "object") for base in node.bases)


def file_classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    """The classes a file defines at its top level, by name (a later
    definition of a name replacing the earlier, as it does when the file runs)."""
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def inherited_table(
    node: ast.ClassDef, classes: dict[str, ast.ClassDef], table: str
) -> ast.Dict | None:
    """The ``table`` dict a class has: its own, else the first its bases in
    the same file (``classes``, ``file_classes``) give it, read left to right
    as Python's lookup reads them."""
    return _inherited_table(node, classes, table, set())


def _inherited_table(
    node: ast.ClassDef, classes: dict[str, ast.ClassDef], table: str, seen: set[str]
) -> ast.Dict | None:
    own = class_table(node, table)
    if own is not None:
        return own
    seen.add(node.name)
    for base in _bases_in_file(node, classes, seen):
        found = _inherited_table(base, classes, table, seen)
        if found is not None:
            return found
    return None


def _bases_in_file(
    node: ast.ClassDef, classes: dict[str, ast.ClassDef], seen: set[str]
) -> list[ast.ClassDef]:
    """The classes a class derives from that the same file defines, left to
    right, leaving out any already asked."""
    names = [base.id for base in node.bases if isinstance(base, ast.Name)]
    return [classes[name] for name in names if name in classes and name not in seen]


def _may_name_an_id(node: ast.Dict) -> bool:
    """Whether a table may give the class an id: one written as text that is
    not empty (the last ``"id"`` key, as Python keeps it), or one this reader
    cannot see (written in code, or merged in from another mapping)."""
    written = [
        value for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value == "id"
    ]
    if not written:
        return None in node.keys
    last = written[-1]
    return not isinstance(last, ast.Constant) or bool(last.value)


_DRIVER_INFO = "DRIVER_INFO"


def class_table(node: ast.ClassDef, table: str) -> ast.Dict | None:
    """The dict a class body assigns ``table``, plainly or annotated."""
    for item in node.body:
        if _assigns(item, table) and isinstance(item.value, ast.Dict):
            return item.value
    return None


def _assigns(item: ast.stmt, table: str) -> bool:
    if isinstance(item, ast.Assign):
        targets = item.targets
    elif isinstance(item, ast.AnnAssign):
        targets = [item.target]
    else:
        return False
    return len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == table
