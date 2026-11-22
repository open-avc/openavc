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

from typing import Any

from openavc.cloud.tool_declarations import TOOLS


def json_kind(value: Any) -> str:
    """What ``value`` is, in the JSON words the tool definitions use."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


#: Whether a value is each JSON type.
_IS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
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


def _item_problem(path: str, shape: dict[str, Any], items: list) -> str | None:
    item_shape = shape.get("items")
    if not item_shape:
        return None
    for position, item in enumerate(items, start=1):
        if _type_problem("item", item_shape, item):
            return f"{path} must be {_shape_words(shape)}: item {position} is {json_kind(item)}."
        problem = _inside(f"{path} item {position}", item_shape, item, item=True)
        if problem:
            return problem
    return None


def _inside(path: str, shape: dict[str, Any], value: Any, *, item: bool = False) -> str | None:
    """A problem inside a value whose own type is right: its array's items,
    its object's fields. An array item's fields are named after the item
    ("layouts item 1: placements"), an object's after the object."""
    if isinstance(value, list):
        return _item_problem(path, shape, value)
    if not isinstance(value, dict):
        return None
    prefix = f"{path}: " if item else f"{path}."
    for name, sub_shape in (shape.get("properties") or {}).items():
        if name in value:
            problem = _problem(f"{prefix}{name}", sub_shape, value[name])
            if problem:
                return problem
    for name in shape.get("required") or ():
        if name not in value:
            return f"{prefix}{name} is required."
    free = shape.get("additionalProperties")
    if isinstance(free, dict):
        for key, sub_value in value.items():
            if key not in (shape.get("properties") or {}):
                problem = _problem(f"{prefix}{key}", free, sub_value)
                if problem:
                    return problem
    return None


def _problem(path: str, shape: dict[str, Any], value: Any) -> str | None:
    return _type_problem(path, shape, value) or _inside(path, shape, value)


def input_problem(tool_name: str, tool_input: Any) -> str | None:
    """The sentence for the first problem with a call's input, None when the
    tool's declaration takes it."""
    if not isinstance(tool_input, dict):
        return f"The tool's input must be an object, not {json_kind(tool_input)}."
    tool = TOOLS.get(tool_name)
    if tool is None:
        return None
    for name, value in tool_input.items():
        shape = tool.inputs.get(str(name))
        if shape is None or value is None:
            continue
        problem = _problem(str(name), shape, value)
        if problem:
            return problem
    for name in tool.required:
        if name not in tool_input:
            return f"{name} is required."
    return None
