"""Every event name the platform sends is in one of the two tables in
`core/event_bus.py`, and every name in them is sent.

`EVENTS` is what a project is offered: the script editor's completions are
generated from it, and the scripting docs list it. `PLATFORM_FEEDS` is what the
platform sends for its own screens and services. Hand-kept lists of event names
named events nothing sent (`schedule.<id>`, `system.startup`,
`device.response.<id>`), so the tables are read against the code here.

Each emit's name is read from the source: a literal, or an f-string whose
substitutions are read the same way. A name assigned in the same function or
module is read from what it is assigned. A parameter is read from what the
function's callers in the package pass for it, when any pass a literal (the
panel's interaction kind is chosen in api/ws.py, three calls up from the emit
in core/ui_events.py); otherwise it stands for any value. A family is sent
when an emit spells out every word of it, a wildcard standing only for an id.
"""

from __future__ import annotations

import ast
import itertools
import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from openavc.core.event_bus import EVENTS, PLATFORM_EVENTS, PLATFORM_FEEDS, event_pattern

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "openavc"

#: The one family whose sender always names the event at run time: a project's
#: own event, sent by a macro's Emit Event step, a control's event action or a
#: script's events.emit().
NAMED_BY_THE_PROJECT = "custom.<anything>"

DOCS = ("docs/scripting-api-reference.md",)

ANY = "*"
MAX_DEPTH = 6

Scopes = tuple[ast.AST, ...]  # innermost function first, the module last


def _modules() -> list[ast.Module]:
    trees = []
    for path in sorted(PACKAGE.rglob("*.py")):
        # The IDE's sources, and the starter projects' scripts (a project's
        # own emits, not the platform's).
        if path.relative_to(PACKAGE).parts[0] in ("web", "templates"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.path = str(path.relative_to(REPO))  # type: ignore[attr-defined]
        trees.append(tree)
    return trees


def _walk(tree: ast.Module):
    """Every node with the function scopes it sits in."""
    stack: list[tuple[ast.AST, Scopes]] = [(tree, (tree,))]
    while stack:
        node, scopes = stack.pop()
        for child in ast.iter_child_nodes(node):
            inner = scopes
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (child, *scopes)
            yield child, inner
            stack.append((child, inner))


MODULES = _modules()
CALLS: dict[str, list[tuple[ast.Call, Scopes]]] = {}
for _tree in MODULES:
    for _node, _scopes in _walk(_tree):
        if isinstance(_node, ast.Call):
            func = _node.func
            callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            CALLS.setdefault(callee, []).append((_node, _scopes))


def _assigned(scope: ast.AST, name: str) -> list[ast.AST]:
    """The values ``name`` is assigned in ``scope``, outside nested functions."""
    values: list[ast.AST] = []
    nodes = list(ast.iter_child_nodes(scope))
    while nodes:
        node = nodes.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            values.append(node.value)
        nodes.extend(ast.iter_child_nodes(node))
    return values


def _argument_for(call: ast.Call, func: ast.FunctionDef | ast.AsyncFunctionDef, name: str):
    """What ``call`` passes for ``func``'s parameter ``name``, or None."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    params = [a.arg for a in (*func.args.posonlyargs, *func.args.args)]
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    if name not in params:
        return None
    index = params.index(name)
    if any(isinstance(a, ast.Starred) for a in call.args[: index + 1]):
        return None
    return call.args[index] if index < len(call.args) else None


def _passed_for(name: str, scopes: Scopes, depth: int) -> set[str]:
    """What the callers of the innermost function pass for its parameter."""
    func = scopes[0]
    # Another `emit` passing its name on: what its callers send is read where
    # they call it.
    if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef) or func.name == "emit":
        return {ANY}
    found: set[str] = set()
    for call, caller_scopes in CALLS.get(func.name, ()):
        argument = _argument_for(call, func, name)
        if argument is not None:
            found |= _values(argument, caller_scopes, depth + 1)
    literals = {value for value in found if ANY not in value}
    return literals or {ANY}


def _name_values(name: str, scopes: Scopes, depth: int) -> set[str]:
    for scope in scopes:
        values = _assigned(scope, name)
        if values:
            return set().union(*(_values(v, scopes, depth) for v in values))
        func = scope
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef) and name in {
            a.arg for a in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs)
        }:
            return _passed_for(name, (func, *scopes[scopes.index(func) + 1:]), depth)
    return {ANY}


def _fstring_values(node: ast.JoinedStr, scopes: Scopes, depth: int) -> set[str]:
    """An f-string's names. Its last substitution is the id the emit is
    about, so it is any value; one before it chooses the kind of event, and is
    read through the callers."""
    last = max(i for i, piece in enumerate(node.values) if not isinstance(piece, ast.Constant))
    pieces = [
        {piece.value} if isinstance(piece, ast.Constant)
        else {ANY} if i == last
        else _values(piece.value, scopes, depth)
        for i, piece in enumerate(node.values)
    ]
    return {"".join(combo) for combo in itertools.product(*pieces)}


def _values(node: ast.AST, scopes: Scopes, depth: int = 0) -> set[str]:
    """Every string ``node`` can be, as globs (``*`` for what is not readable)."""
    if depth > MAX_DEPTH:
        return {ANY}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _values(node.body, scopes, depth) | _values(node.orelse, scopes, depth)
    if isinstance(node, ast.JoinedStr):
        return _fstring_values(node, scopes, depth)
    if isinstance(node, ast.Name):
        return _name_values(node.id, scopes, depth)
    return {ANY}


def _sent() -> dict[str, list[str]]:
    """Every readable name the package emits, as a glob, and where."""
    sent: dict[str, list[str]] = {}
    for tree in MODULES:
        for node, scopes in _walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "emit"
                and node.args
            ):
                continue
            for glob in _values(node.args[0], scopes):
                if glob != ANY:  # passed through from whoever named it
                    sent.setdefault(glob, []).append(f"{tree.path}:{node.lineno}")
    return sent


def _sample(name: str) -> str:
    """A family's name with each ``<...>`` filled in, as an emit would send it."""
    return re.sub(r"<[^>]+>", "x", name)


def _spells_out(glob: str, family: str) -> bool:
    """Whether an emit sending ``glob`` sends ``family``: every word of the
    family is in it, and a wildcard stands only for one of its ids."""
    words, parts = family.split("."), glob.split(".")
    return len(words) == len(parts) and all(
        word.startswith("<") or part == word for word, part in zip(words, parts)
    )


SENT = _sent()
FAMILIES = [family.name for family in EVENTS] + list(PLATFORM_FEEDS)


def test_every_name_the_code_sends_is_in_a_table():
    """A new emit is put in `EVENTS`, if a project should hear it, or in
    `PLATFORM_FEEDS`, if it is for the platform's own screens."""
    unlisted = {
        glob: where for glob, where in SENT.items()
        if not any(fnmatchcase(_sample(family), glob) for family in FAMILIES)
    }
    assert not unlisted, (
        f"Sent but in neither EVENTS nor PLATFORM_FEEDS (core/event_bus.py): {unlisted}"
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_every_name_in_the_tables_is_sent(family):
    """A family nothing sends is a handler that never runs."""
    if family == NAMED_BY_THE_PROJECT:
        return
    assert any(_spells_out(glob, family) for glob in SENT), (
        f"'{family}' is listed in core/event_bus.py, but no emit in the code sends it"
    )


@pytest.mark.parametrize("family", [
    "schedule.<schedule_id>", "system.startup", "device.response.<device_id>",
    "ui.swipe.<element_id>", "macro.paused.<macro_id>", "isc.message.<instance_id>",
])
def test_the_check_finds_a_family_nothing_sends(family):
    assert not any(_spells_out(glob, family) for glob in SENT)


@pytest.mark.parametrize("glob", [
    "ui.hold.*", "ui.audio_mute_route.*", "ui.change.*", "macro.cancelled.*",
    "device.connected.*", "device.disconnected.*", "simulation.crashed",
])
def test_a_kind_chosen_by_a_caller_is_read(glob):
    """The interaction kind, a macro's ending and a bridged device's edge are
    passed in by a caller or chosen just above the emit, not spelled in it."""
    assert glob in SENT


def test_a_name_is_in_one_table_once():
    assert len(FAMILIES) == len(set(FAMILIES))


def test_the_platform_events_are_the_system_families():
    assert PLATFORM_EVENTS == {f.name for f in EVENTS if f.name.startswith("system.")}


@pytest.mark.parametrize("name,pattern", [
    ("device.connected.<device_id>", "device.connected.*"),
    ("isc.<instance_id>.<event>", "isc.*.*"),
    ("system.started", "system.started"),
    ("custom.<anything>", "custom.*"),
])
def test_a_family_s_pattern_hears_all_of_it(name, pattern):
    assert event_pattern(name) == pattern


def _doc_table(doc: str) -> list[str]:
    """The first column of a doc's "Event Types" table."""
    text = (REPO / doc).read_text(encoding="utf-8")
    section = text.split("## Event Types", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| `([^`]+)` \|", section, flags=re.MULTILINE)


@pytest.mark.parametrize("doc", DOCS)
def test_the_docs_list_the_events_a_project_hears(doc):
    """What the docs teach a script to listen for is `EVENTS`, in its order."""
    assert _doc_table(doc) == [family.name for family in EVENTS]
