"""Every AI tool's declaration holds to its handler's source, both ways.

``cloud/tool_declarations.py`` declares what each tool takes, and the cloud
tells the model exactly that. Each handler is read from its source here: the
fields it reads from its input (``input.get("x")``, ``input["x"]``,
``"x" in input``, and those reads inside every helper it hands the input to),
the defaults it takes for one left out, whether it reaches the project's edit
door, and how long it may wait. A field read and not declared is one the
cloud would tell the model it ignored; a field declared and never read is one
the model would send for nothing.

A read the walk cannot name fails here rather than being skipped: a helper it
cannot find, or a key it cannot work out. A door that hands its whole input on
to a project model names that model below, and its fields are the door's.
"""

from __future__ import annotations

import ast
import functools
import inspect
import textwrap
from typing import Any, Callable

import pytest

from openavc.cloud import tool_declarations as decl
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.tools.input_shapes import input_problem
from openavc.cloud.tools.ui_review import WriteNotes

TOOL_NAMES = list(decl.TOOLS)

#: Keys a door reads only to refuse them, so they are never declared.
REFUSED_ONLY = {
    # Page geometry project format 0.8.0 retired, refused by name.
    "grid_area", "grid", "grid_gap",
}
#: A master's box goes in ``placements`` keyed by orientation; one sent as a
#: page element's ``placement`` is refused with that sentence.
REFUSED_ONLY_BY_TOOL = {
    "add_master_element": {"placement"},
    "update_master_element": {"placement"},
}


def _model_fields(model_name: str, *left_out: str) -> Callable[[], set[str]]:
    def fields() -> set[str]:
        from openavc.core import project_loader

        return set(getattr(project_loader, model_name).model_fields) - set(left_out)
    return fields


#: The doors that hand every field of their input on to a project model: the
#: model's fields are what they read (an id is never changed in place).
PASSES_THROUGH: dict[str, Callable[[], set[str]]] = {
    "update_ui_element": _model_fields("UIElement", "id"),
    "add_master_element": _model_fields("MasterElement"),
    "update_master_element": _model_fields("MasterElement", "id"),
}

#: Methods on another object that a door hands its input to, by the name the
#: call uses.
METHODS: dict[str, Callable] = {"assigning": WriteNotes.assigning}

#: Defaults a model or helper sets rather than the door's own ``get``.
def _probe_default(field: str) -> Callable[[], Any]:
    def default() -> Any:
        from openavc.api.models import TestCommandRequest

        return TestCommandRequest.model_fields[field].default
    return default


DEFAULTS_ELSEWHERE: dict[tuple[str, str], Callable[[], Any]] = {
    ("test_driver_command", "port"): _probe_default("port"),
    ("test_driver_command", "delimiter"): _probe_default("delimiter"),
    ("test_driver_command", "timeout"): _probe_default("timeout"),
}

#: The names that are the project's edit door.
EDIT_DOOR = {"apply_tool_edit", "apply_tool_change"}

#: The tools that change what the summary reads without the edit door, and how.
CHANGES_ELSEWHERE = {
    # A driver arriving or changing rebuilds each device on it, and one waiting
    # for it comes up (api/routes/drivers and driver_definitions end in
    # drivers.driver_change.changed): a device's status in the summary.
    "install_community_driver": decl.DEVICES,
    "create_driver_definition": decl.DEVICES,
    "update_driver_definition": decl.DEVICES,
    # An installed plugin registers, and the summary lists every registered one.
    "install_plugin": decl.PLUGINS,
}


# --- Reading a function's source ---

@functools.cache
def _tree(fn: Callable) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _params(fn: Callable) -> list[str]:
    node = _tree(fn).body[0]
    names = [a.arg for a in node.args.posonlyargs + node.args.args]
    return names[1:] if names and names[0] in ("self", "cls") else names


class _Reads:
    """What one tool's handler reads from its input, followed into helpers."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        self.fields: set[str] = set()
        self.defaults: dict[str, list[Any]] = {}
        self.whole = False
        self.unknown: list[str] = []
        self._seen: set[tuple] = set()
        self._tables: dict[str, list[Callable]] = {}

    def walk(self, fn: Callable, param: str, keys: dict[str, frozenset]) -> None:
        key = (fn, param, tuple(sorted(keys.items())))
        if key in self._seen:
            return
        self._seen.add(key)
        tree = _tree(fn)
        scope = {**keys, **_loop_values(tree, fn)}
        self._tables = _loop_callables(tree, fn)
        for node in ast.walk(tree):
            self._read(node, fn, param, scope)

    def _read(self, node: ast.AST, fn: Callable, param: str, scope: dict) -> None:
        where = f"{fn.__qualname__}: {ast.unparse(node)[:80]}"
        if isinstance(node, ast.Call) and _is_name(getattr(node.func, "value", None), param):
            self._method(node, param, scope, where)
        elif isinstance(node, ast.Subscript) and _is_name(node.value, param):
            self._key(node.slice, scope, where)
        elif (isinstance(node, ast.Compare) and len(node.ops) == 1
              and isinstance(node.ops[0], (ast.In, ast.NotIn))
              and _is_name(node.comparators[0], param)):
            self._key(node.left, scope, where)
        elif isinstance(node, ast.comprehension) and _is_name(node.iter, param):
            self.whole = True
        elif isinstance(node, ast.Call):
            self._handed_on(node, fn, param, scope, where)

    def _method(self, node: ast.Call, param: str, scope: dict, where: str) -> None:
        method = node.func.attr
        if method in ("items", "keys", "values", "copy"):
            self.whole = True
        elif method in ("get", "pop") and node.args:
            for name in self._key(node.args[0], scope, where):
                if len(node.args) > 1:
                    try:
                        self.defaults.setdefault(name, []).append(ast.literal_eval(node.args[1]))
                    except ValueError:
                        pass  # a default worked out at run time declares nothing
        else:
            self.unknown.append(f"{where} (input.{method})")

    def _key(self, expr: ast.AST, scope: dict, where: str) -> frozenset:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            names = frozenset({expr.value})
        elif isinstance(expr, ast.Name) and expr.id in scope:
            names = scope[expr.id]
        else:
            self.unknown.append(f"{where} (key {ast.unparse(expr)})")
            return frozenset()
        self.fields |= names
        return names

    def _handed_on(self, node: ast.Call, fn: Callable, param: str, scope: dict, where: str) -> None:
        positions = [i for i, arg in enumerate(node.args) if _is_name(arg, param)]
        if any(_is_name(kw.value, param) for kw in node.keywords):
            self.unknown.append(f"{where} (handed on by keyword)")
        if not positions:
            return
        callees = _callees(node.func, fn, self._tables)
        if not callees:
            self.unknown.append(f"{where} (a helper the walk cannot find)")
            return
        for callee in callees:
            params = _params(callee)
            bound = {
                params[i]: _key_values(arg, scope)
                for i, arg in enumerate(node.args)
                if i < len(params) and _key_values(arg, scope) is not None
            }
            for position in positions:
                self.walk(callee, params[position], bound)


def _is_name(node: Any, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _key_values(arg: ast.AST, scope: dict) -> frozenset | None:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return frozenset({arg.value})
    if isinstance(arg, ast.Name) and arg.id in scope:
        return scope[arg.id]
    return None


def _strings(value: Any) -> frozenset | None:
    if isinstance(value, dict):
        value = list(value)
    if isinstance(value, (tuple, list, set, frozenset)) and all(isinstance(v, str) for v in value):
        return frozenset(value)
    return None


def _constant(expr: ast.AST, fn: Callable) -> frozenset | None:
    """The strings a loop runs over: a literal, or a module-level table
    (a dict's keys)."""
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) \
            and expr.func.attr in ("items", "keys") and not expr.args:
        expr = expr.func.value
    if isinstance(expr, ast.Name):
        return _strings(fn.__globals__.get(expr.id))
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        try:
            return _strings(ast.literal_eval(expr))
        except ValueError:
            return None
    return None


def _loop_values(tree: ast.AST, fn: Callable) -> dict[str, frozenset]:
    """Each loop variable that runs over a fixed set of strings, and that set."""
    found: dict[str, frozenset] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)):
            values = _constant(node.iter, fn)
            target = node.target
            if isinstance(target, ast.Tuple) and target.elts:
                target = target.elts[0]
            if values is not None and isinstance(target, ast.Name):
                found[target.id] = values
    return found


def _loop_callables(tree: ast.AST, fn: Callable) -> dict[str, list[Callable]]:
    """Each loop variable that runs over a module-level table's functions
    (``for field, apply in TABLE.items()``), and those functions."""
    found: dict[str, list[Callable]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.comprehension)):
            continue
        it, target = node.iter, node.target
        if (isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute)
                and it.func.attr == "items" and isinstance(it.func.value, ast.Name)
                and isinstance(target, ast.Tuple) and len(target.elts) == 2
                and isinstance(target.elts[1], ast.Name)):
            table = fn.__globals__.get(it.func.value.id)
            if isinstance(table, dict) and all(inspect.isfunction(v) for v in table.values()):
                found[target.elts[1].id] = list(table.values())
    return found


def _callees(func: ast.AST, fn: Callable, tables: dict[str, list[Callable]]) -> list[Callable]:
    if isinstance(func, ast.Name) and func.id in tables:
        return tables[func.id]
    callee = _callee(func, fn)
    return [callee] if callee is not None else []


def _callee(func: ast.AST, fn: Callable) -> Callable | None:
    if isinstance(func, ast.Attribute) and _is_name(func.value, "self"):
        return getattr(AIToolHandler, func.attr, None)
    if isinstance(func, ast.Attribute) and func.attr in METHODS:
        return METHODS[func.attr]
    if isinstance(func, ast.Name):
        found = fn.__globals__.get(func.id)
        return found if inspect.isfunction(found) else None
    return None


@functools.cache
def _reads(tool: str) -> _Reads:
    reads = _Reads(tool)
    reads.walk(getattr(AIToolHandler, f"_{tool}"), "input", {})
    return reads


def _read_fields(tool: str) -> set[str]:
    reads = _reads(tool)
    fields = set(reads.fields)
    if tool in PASSES_THROUGH:
        fields |= PASSES_THROUGH[tool]()
    return fields - REFUSED_ONLY - REFUSED_ONLY_BY_TOOL.get(tool, set())


# --- Both ways ---

@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_the_walk_names_every_read(tool):
    reads = _reads(tool)
    assert reads.unknown == []
    assert reads.whole is (tool in PASSES_THROUGH), (
        "a door that hands its whole input on names the model it reads in PASSES_THROUGH"
    )


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_every_field_read_is_declared(tool):
    declared = set(decl.TOOLS[tool].inputs)
    assert sorted(_read_fields(tool) - declared) == []


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_every_declared_field_is_read(tool):
    declared = set(decl.TOOLS[tool].inputs)
    assert sorted(declared - _read_fields(tool)) == []


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_no_field_read_only_to_be_refused_is_declared(tool):
    refused = REFUSED_ONLY | REFUSED_ONLY_BY_TOOL.get(tool, set())
    assert sorted(set(decl.TOOLS[tool].inputs) & refused) == []


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_a_declared_default_is_what_the_handler_takes(tool):
    for name, shape in decl.TOOLS[tool].inputs.items():
        if "default" not in shape:
            continue
        elsewhere = DEFAULTS_ELSEWHERE.get((tool, name))
        taken = [elsewhere()] if elsewhere else _reads(tool).defaults.get(name, [])
        assert taken, f"{tool}.{name} declares a default the handler never takes"
        assert all(value == shape["default"] for value in taken), (tool, name, taken)


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_a_required_input_left_out_is_refused_by_name(tool):
    for name in decl.TOOLS[tool].required:
        assert input_problem(tool, {}) is not None
        sent = {other: None for other in decl.TOOLS[tool].required if other != name}
        assert input_problem(tool, sent) == f"{name} is required."


# --- What a tool changes ---

def _reaches(fn: Callable, names: set[str], seen: set | None = None) -> bool:
    """Whether ``fn``, or a helper of the handler's own it calls, calls one
    of ``names``."""
    seen = set() if seen is None else seen
    if fn in seen:
        return False
    seen.add(fn)
    for node in ast.walk(_tree(fn)):
        if not isinstance(node, ast.Call):
            continue
        called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if called in names:
            return True
        callee = _callee(node.func, fn)
        if callee is not None and callee.__module__.startswith("openavc.cloud") \
                and _reaches(callee, names, seen):
            return True
    return False


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_what_a_tool_changes_is_what_its_handler_does(tool):
    row = decl.TOOLS[tool]
    through_the_door = _reaches(getattr(AIToolHandler, f"_{tool}"), EDIT_DOOR)
    if through_the_door:
        assert row.changes == decl.PROJECT
    else:
        assert row.changes == CHANGES_ELSEWHERE.get(tool)


def test_the_edit_door_tools_are_the_ones_the_plan_counted():
    assert sum(1 for row in decl.TOOLS.values() if row.changes == decl.PROJECT) == 30


# --- How long a tool may take ---

def _bound_in(fn: Callable, keyword: str = "hi") -> int:
    """The one ``hi=`` bound a helper's clamp sets."""
    bounds = [
        kw.value.value
        for node in ast.walk(_tree(fn)) if isinstance(node, ast.Call)
        for kw in node.keywords if kw.arg == keyword and isinstance(kw.value, ast.Constant)
    ]
    assert len(bounds) == 1, bounds
    return bounds[0]


def _operator_wait() -> int:
    from openavc.core.macro_engine import OPERATOR_RUN_WAIT_SECONDS

    return int(OPERATOR_RUN_WAIT_SECONDS)


def _probe_timeout() -> int:
    from openavc.api.models import TestCommandRequest

    (bound,) = [m.le for m in TestCommandRequest.model_fields["timeout"].metadata if hasattr(m, "le")]
    return int(bound)


def _discovery_wait() -> int:
    from openavc.cloud.tools import system_tools

    return _bound_in(system_tools._await_scan)


WAITS: dict[str, Callable[[], int]] = {
    "wait": lambda: _bound_in(AIToolHandler._wait),
    "get_discovery_results": _discovery_wait,
    "execute_macro": _operator_wait,
    "test_trigger": _operator_wait,
    "test_driver_command": _probe_timeout,
}


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_a_tool_that_waits_says_for_how_long(tool):
    wait = WAITS.get(tool)
    assert decl.TOOLS[tool].answer_within_seconds == (wait() if wait else None)


# --- The element doors' fields ---

def _json_types(schema: dict, defs: dict) -> set[str]:
    if "$ref" in schema:
        return {"object"}
    if "anyOf" in schema:
        return set().union(*(_json_types(part, defs) for part in schema["anyOf"]))
    return {schema["type"]} if "type" in schema else set()


def _declared_types(shape: dict) -> set[str]:
    kind = shape.get("type")
    return set(kind) if isinstance(kind, list) else {kind}


@pytest.mark.parametrize(("fields", "model_name"), [
    (decl.ELEMENT_FIELDS, "UIElement"),
    (decl.MASTER_FIELDS, "MasterElement"),
])
def test_an_element_door_declares_each_model_field_as_the_model_types_it(fields, model_name):
    from openavc.core import project_loader

    schema = getattr(project_loader, model_name).model_json_schema()
    properties, defs = schema["properties"], schema.get("$defs", {})
    assert set(fields) == set(properties)
    for name, shape in fields.items():
        assert _declared_types(shape) == _json_types(properties[name], defs), name
