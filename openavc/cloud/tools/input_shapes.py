"""What a tool's input must be, checked before the tool runs.

Each tool's own declaration (``cloud/tool_declarations.py``) says what it
takes: each input's JSON type, the fields of an object and the items of an
array, down to the depth the tool reads, and which inputs it cannot run
without. An input's allowed values and bounds are declared for the model and
left to the tool, which refuses one outside them in words of its own that say
what each value does. The cloud tells the model the same, but nothing
on the way here enforces it. An input sent as the wrong type reached the tool
and failed inside it with Python's own error (an id used as a dict key, text
that was a list, a box that was a number), which the cloud is told only as
``TOOL_FAILED``. Checked here, the answer names the input and the shape it
must have, and the tool is never called.

The first problem is answered: an input of the wrong shape, in the order the
call sent them, then the first required input left out. An input the tool
does not declare is not checked. A null sent as one of the tool's own inputs
is the tool's own call (``update_device`` keeps a null config, a null
``parent`` brings an element out of its container), so it is never refused
here; inside an input, a null is refused unless the declared type admits it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from openavc.cloud.tool_declarations import TOOLS


#: Each JSON type's Python types and its words, a boolean before a number
#: since a bool is an int.
_KIND_WORDS = (
    (bool, "a boolean"),
    ((int, float), "a number"),
    (str, "a string"),
    (list, "an array"),
    (dict, "an object"),
)


def json_kind(value: Any) -> str:
    """What ``value`` is, in the JSON words the tool definitions use."""
    if value is None:
        return "null"
    if isinstance(value, float) and not math.isfinite(value):
        return _not_finite_words(value)
    return next(
        (words for kinds, words in _KIND_WORDS if isinstance(value, kinds)),
        type(value).__name__,
    )


def _not_finite_words(value: float) -> str:
    """A float JSON cannot hold, as Python's encoder writes it. JSON has no
    such number, but a figure too big for a float (``1e400``) parses as one,
    and nothing downstream can store it."""
    if math.isnan(value):
        return "NaN"
    return "Infinity" if value > 0 else "-Infinity"


def _is_number(value: Any) -> bool:
    """A JSON number: never a boolean, and finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _is_integer(value: Any) -> bool:
    if not _is_number(value):
        return False
    return isinstance(value, int) or value.is_integer()


#: Whether a value is each JSON type.
_IS = {
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "integer": _is_integer,
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}

_WORDS = {
    "string": "a string", "number": "a number", "integer": "a whole number",
    "boolean": "a boolean", "object": "an object", "array": "an array", "null": "null",
}


def _types(shape: dict[str, Any]) -> list[str]:
    kind = shape.get("type")
    if kind is None:
        return []
    return list(kind) if isinstance(kind, list) else [kind]


def _type_words(kind: str, shape: dict[str, Any]) -> str:
    item_kind = _types(shape.get("items") or {})
    if kind == "array" and len(item_kind) == 1:
        return f"an array of {_WORDS[item_kind[0]].split(' ', 1)[1]}s"
    return _WORDS[kind]


def _shape_words(shape: dict[str, Any]) -> str:
    words = [_type_words(kind, shape) for kind in _types(shape)]
    return " or ".join(words)


def _type_problem(path: str, shape: dict[str, Any], value: Any) -> str | None:
    kinds = _types(shape)
    if kinds and not any(_IS[kind](value) for kind in kinds):
        # A number with a fraction where a whole one belongs is named as sent.
        sent = value if "integer" in kinds and _IS["number"](value) else json_kind(value)
        return f"{path} must be {_shape_words(shape)}, not {sent}."
    return None


def _first(problems: Iterable[str | None]) -> str | None:
    """The first problem of several, checked in order."""
    return next((problem for problem in problems if problem), None)


def _item_problem(path: str, shape: dict[str, Any], items: list) -> str | None:
    if not shape.get("items"):
        return None
    return _first(
        _one_item(path, shape, position, item) for position, item in enumerate(items, start=1)
    )


def _one_item(path: str, shape: dict[str, Any], position: int, item: Any) -> str | None:
    if _type_problem("item", shape["items"], item):
        return f"{path} must be {_shape_words(shape)}: item {position} is {json_kind(item)}."
    return _inside(f"{path} item {position}", shape["items"], item, item=True)


def _inside(path: str, shape: dict[str, Any], value: Any, *, item: bool = False) -> str | None:
    """A problem inside a value whose own type is right: its array's items,
    its object's fields. An array item's fields are named after the item
    ("layouts item 1: placements"), an object's after the object."""
    if isinstance(value, list):
        return _item_problem(path, shape, value)
    if not isinstance(value, dict):
        return None
    prefix = f"{path}: " if item else f"{path}."
    return (
        _fields_problem(prefix, shape, value)
        or _missing_problem(prefix, shape.get("required") or (), value)
        or _free_problem(prefix, shape, value)
    )


def _fields_problem(prefix: str, shape: dict[str, Any], value: dict) -> str | None:
    """The first named field of the wrong shape."""
    fields = shape.get("properties") or {}
    return _first(
        _problem(f"{prefix}{name}", fields[name], value[name]) for name in fields if name in value
    )


def _missing_problem(prefix: str, required: Iterable[str], value: dict) -> str | None:
    """The first required field left out."""
    missing = next((name for name in required if name not in value), None)
    return None if missing is None else f"{prefix}{missing} is required."


def _free_problem(prefix: str, shape: dict[str, Any], value: dict) -> str | None:
    """The first freely keyed value of the wrong shape."""
    free = shape.get("additionalProperties")
    if not isinstance(free, dict):
        return None
    named = shape.get("properties") or {}
    return _first(
        _problem(f"{prefix}{key}", free, sub_value)
        for key, sub_value in value.items() if key not in named
    )


def _problem(path: str, shape: dict[str, Any], value: Any) -> str | None:
    return _type_problem(path, shape, value) or _inside(path, shape, value)


def _sent_problem(inputs: dict[str, dict[str, Any]], tool_input: dict) -> str | None:
    """The first declared input of the wrong shape, in the order sent. A
    null sent as one of the tool's inputs is the tool's own call."""
    return _first(
        _problem(str(name), inputs[str(name)], value)
        for name, value in tool_input.items()
        if str(name) in inputs and value is not None
    )


def input_problem(tool_name: str, tool_input: Any) -> str | None:
    """The sentence for the first problem with a call's input, None when the
    tool's declaration takes it."""
    if not isinstance(tool_input, dict):
        return f"The tool's input must be an object, not {json_kind(tool_input)}."
    tool = TOOLS.get(tool_name)
    if tool is None:
        return None
    return _sent_problem(tool.inputs, tool_input) or _missing_problem("", tool.required, tool_input)
