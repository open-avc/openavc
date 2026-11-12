"""What a tool's input fields must be, checked before the tool runs.

The cloud's tool definitions declare each input field's JSON type, and the
model sends what they declare, but nothing on the way here enforces it. A
field sent as the wrong type reached the tool and failed inside it with
Python's own error (an id used as a dict key, text that was a list, an object
that was text), which the cloud is told only as ``TOOL_FAILED``. Checked here,
the answer names the field and the shape it must have, and the tool is never
called.

The rule is by field name, because every name the cloud declares has one JSON
type in every tool that takes it: an ``id`` or any ``*_id`` is a string, and
the table below holds the other fields a wrong type breaks. A new tool taking
a field of a listed name keeps its shape. ``None`` is never refused here: a
null is the tool's own call (``update_device`` keeps a null config, a null
``parent`` brings an element out of its container), and a field nobody sent
is not checked at all.
"""

from __future__ import annotations

from typing import Any

STRING, OBJECT = "a string", "an object"
STRINGS, OBJECTS = "an array of strings", "an array of objects"

#: The fields other than ids that a wrong JSON type breaks, and what each must be.
FIELD_SHAPES: dict[str, str] = {
    "driver": STRING,
    "key": STRING,
    "setting_key": STRING,
    "type": STRING,
    "parent": STRING,
    "level": STRING,
    "search": STRING,
    "source": STRING,
    "file": STRING,
    "command_string": STRING,
    "delimiter": STRING,
    "host": STRING,
    "query": STRING,
    "manufacturer": STRING,
    "model": STRING,
    "category": STRING,
    "transport": STRING,
    "config": OBJECT,
    "definition": OBJECT,
    "placement": OBJECT,
    "snap": OBJECT,
    "overlay": OBJECT,
    "background": OBJECT,
    "device_ids": STRINGS,
    "elements": OBJECTS,
}


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


def _shape_of(field: str) -> str | None:
    if field == "id" or field.endswith("_id"):
        return STRING
    return FIELD_SHAPES.get(field)


#: The Python type each shape is, and each array's item type.
_TYPE_OF = {STRING: str, OBJECT: dict, STRINGS: list, OBJECTS: list}
_ITEM_TYPE_OF = {STRINGS: str, OBJECTS: dict}


def _item_problem(field: str, shape: str, items: list) -> str | None:
    item_type = _ITEM_TYPE_OF[shape]
    for position, item in enumerate(items, start=1):
        if not isinstance(item, item_type):
            return f"{field} must be {shape}: item {position} is {json_kind(item)}."
    return None


def _field_problem(field: str, shape: str, value: Any) -> str | None:
    if not isinstance(value, _TYPE_OF[shape]):
        return f"{field} must be {shape}, not {json_kind(value)}."
    if shape in _ITEM_TYPE_OF:
        return _item_problem(field, shape, value)
    return None


def input_problem(tool_input: Any) -> str | None:
    """The sentence for the first field of the wrong shape, None when every
    field the table knows is right (or null)."""
    if not isinstance(tool_input, dict):
        return f"The tool's input must be an object, not {json_kind(tool_input)}."
    for field, value in tool_input.items():
        shape = _shape_of(str(field))
        if shape is None or value is None:
            continue
        problem = _field_problem(str(field), shape, value)
        if problem:
            return problem
    return None
