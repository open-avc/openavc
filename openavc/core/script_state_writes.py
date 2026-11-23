"""What a script writes into the state store, read before it runs.

The script check's half of the authored-writer rule
(``core/state_store.check_authored_write``): a ``state.set`` or
``state.delete`` on a ``system.`` or ``isc.`` key, and a ``state.set`` whose
value is written as a list, a dict, a set or a tuple, are refused when they
run. This finds those lines, each with the runtime's own sentence, so the
editor marks them while the script is written. And a ``state.set`` on a
``device.`` key, which runs, but lasts only until the device's next report.

Read statically, as ``core/event_references`` reads a script: only a key the
line spells out (a literal, or an f-string's literal start) and a value
written as a literal can be judged. Anything built at runtime is left alone,
and the runtime still refuses it.

``state_write_issues`` is one script's issues, each ``{kind, line, event,
message}``, ``event`` the key as written; ``event_references.script_issues``
adds them to the script check.
"""

from __future__ import annotations

import ast
from typing import Any

from openavc.core.state_store import (
    DEVICE_PREFIX,
    DEVICE_WRITE_NOTE,
    flat_value_sentence,
    platform_key_sentence,
)

#: The kinds of issue, as ``script_issues`` tags them.
PLATFORM_KEY = "platform_key"
NOT_ONE_VALUE = "not_one_value"
DEVICE_KEY = "device_key"

#: What a literal that is not one value is written as, with an example of each.
_NOT_ONE_VALUE = {
    ast.List: [], ast.ListComp: [], ast.Tuple: (), ast.Dict: {}, ast.DictComp: {},
    ast.Set: set(), ast.SetComp: set(),
}


def _is_openavc_state(node: ast.AST) -> bool:
    """``openavc.state``."""
    if not isinstance(node, ast.Attribute) or node.attr != "state":
        return False
    return isinstance(node.value, ast.Name) and node.value.id == "openavc"


def _is_state(node: ast.AST) -> bool:
    """``state`` as a script imports it, or ``openavc.state``."""
    if isinstance(node, ast.Name):
        return node.id == "state"
    return _is_openavc_state(node)


def _state_call(node: ast.AST) -> str | None:
    """``set`` or ``delete`` for ``state.set(...)``, ``openavc.state.delete(...)``."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if not _is_state(node.func.value):
        return None
    return node.func.attr if node.func.attr in ("set", "delete") else None


def _argument(node: ast.Call, position: int, name: str) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    return next((kw.value for kw in node.keywords if kw.arg == name), None)


def _written_key(node: ast.AST | None) -> str | None:
    """The key as the line spells it out: a literal as it is, an f-string
    with each substitution as ``*``; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            piece.value if isinstance(piece, ast.Constant) and isinstance(piece.value, str) else "*"
            for piece in node.values
        )
    return None


def _issue(kind: str, line: int, key: str, message: str) -> dict[str, Any]:
    return {"kind": kind, "line": line, "event": key, "message": message}


def _issue_at(node: ast.Call, call: str) -> dict[str, Any] | None:
    key = _written_key(_argument(node, 0, "key"))
    if not key:
        return None
    refused = platform_key_sentence(key)
    if refused:
        return _issue(PLATFORM_KEY, node.lineno, key, refused)
    if call != "set":
        return None
    value = _argument(node, 1, "value")
    shape = _NOT_ONE_VALUE.get(type(value))
    if shape is not None:
        return _issue(NOT_ONE_VALUE, node.lineno, key, flat_value_sentence(shape) or "")
    if key.startswith(DEVICE_PREFIX):
        return _issue(DEVICE_KEY, node.lineno, key, DEVICE_WRITE_NOTE.format(key=key))
    return None


def state_write_issues(source: str) -> list[dict[str, Any]]:
    """Every line of ``source`` that writes a key or value the runtime refuses,
    or a device's key: each ``{kind, line, event, message}``, in line order."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    found = []
    for node in ast.walk(tree):
        call = _state_call(node)
        issue = _issue_at(node, call) if call else None
        if issue:
            found.append(issue)
    return sorted(found, key=lambda issue: issue["line"])
