"""THE identity of an enum option, and the rule for a list naming one twice.

An enum's ``values:`` list is the set of values a state variable holds, a
parameter sends or a setting writes, each entry a bare value or a ``{value,
label}`` mapping. ``option_identity`` is the contract's rule for which option
an entry names: the same JSON type and value, so 1 and 1.0 are one number, a
boolean is never a number, and text is compared exactly. A list that names an
option twice offers a second entry nobody can tell from the first: a picker
shows it twice, and a value given by text names the first. So the author's
doors refuse it, naming the list and both positions; a driver already
installed with one still loads, and the load names it.

``option_lists`` is every such list a driver file declares, where it sits:
a device state variable's, a child type's property's, a command's and an
action's parameter's, a config field's and a table column's, a device
setting's (the contract's entries that carry ``values``, held to this walk by
``tests/test_enum_option_identity.py``). ``repeated_options`` is each repeat
in one list, said as ``(place, words)``. The driver model reads
``repeated_option_problems`` for a YAML driver (``driver_model``), and the
Python driver checker reads ``repeated_option_errors`` over the lists it
could read whole (``python_info``).

Pure: the standard library, ``declared_names``' marker, ``tagged_errors`` and
``yaml_text``'s words, vendored with the driver contract.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from openavc.drivers.declared_names import UNEVALUATED_KEY
from openavc.drivers.tagged_errors import TaggedErrors
from openavc.drivers.yaml_text import NOT_TEXT

# The JSON type of each scalar a driver file can hold, booleans first: in
# Python True == 1 and both hash alike, so the type is read before the value.
_JSON_KINDS: tuple[tuple[type | tuple[type, ...], str], ...] = (
    (bool, "boolean"),
    ((int, float), "number"),
    (str, "string"),
)

# A number written two ways (1 and 1.0) is said with why they are one.
_ONE_NUMBER = "since a number is one value however it is written"


def option_identity(entry: object) -> tuple | None:
    """Which option an entry of an enum's ``values:`` list names: THE rule
    for when two entries are one option.

    An entry is a bare value or a ``{value, label}`` mapping, which names its
    ``value`` (the label is only what a picker shows). Two entries are one
    option when they are the same JSON value: the same JSON type and value,
    so 1 and 1.0 are one number, a boolean is never a number (true is not 1),
    text is compared exactly (case and spaces count), a list is its entries
    in order and an object its keys and values. The answer compares equal
    for one option and hashes alike. None for an entry that names no JSON
    value: a mapping without ``value``, or a value of no JSON type (one a
    Python driver builds in code, which the source reader cannot see)."""
    if isinstance(entry, dict):
        if "value" not in entry:
            return None
        entry = entry["value"]
    return _json_identity(entry)


def _json_identity(value: object) -> tuple | None:
    if value is None:
        return ("null",)
    kind = next((name for kinds, name in _JSON_KINDS if isinstance(value, kinds)), None)
    if kind is not None:
        return (kind, value)
    return _composite_identity(value)


def _composite_identity(value: object) -> tuple | None:
    """A list's identity (its entries in order) or an object's (its keys and
    values); None for anything else, or when a part names no JSON value."""
    if isinstance(value, list):
        items = tuple(_json_identity(item) for item in value)
        return None if None in items else ("array", items)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        fields = {key: _json_identity(item) for key, item in value.items()}
        return None if None in fields.values() else ("object", frozenset(fields.items()))
    return None


def option_lists(definition: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
    """``(place, values)`` for each ``values:`` a driver file declares, in
    the order the sections are checked. ``values`` is as written, a list or
    not; an entry a Python driver merged in or built in code is passed over."""
    yield from _entries("state_variables", definition.get("state_variables"))
    for name, type_def in _named(definition.get("child_entity_types")):
        yield from _entries(f"child_entity_types.{name}.state_variables", _get(type_def, "state_variables"))
    for name, command in _named(definition.get("commands")):
        yield from _entries(f"commands.{name}.params", _get(command, "params"))
    actions = definition.get("actions")
    for i, action in enumerate(actions if isinstance(actions, list) else ()):
        yield from _entries(f"actions[{i}].params", _get(action, "params"))
    config = definition.get("config_schema")
    yield from _entries("config_schema", config)
    for name, field in _named(config):
        yield from _entries(f"config_schema.{name}.columns", _get(field, "columns"))
    yield from _entries("device_settings", definition.get("device_settings"))


def repeated_options(place: str, values: Any, *, yaml: bool = False) -> Iterator[tuple[str, str]]:
    """``(place, words)`` for each entry of the list at ``place`` that names
    an option an earlier entry names: the repeat's own position, and the
    words said there (without the place). ``yaml`` adds how YAML reads a
    bare word where a repeat is not text. Nothing for anything but a list;
    never raises."""
    first: dict[tuple, int] = {}
    for i, entry in enumerate(values if isinstance(values, list) else ()):
        identity = option_identity(entry)
        if identity is None:
            continue
        earlier = first.setdefault(identity, i)
        if earlier == i:
            continue
        words = _same_as(values[earlier], entry, earlier, identity[0])
        if yaml and identity[0] != "string":
            words += f". {NOT_TEXT}"
        yield f"{place}[{i}]", words


def repeated_option_problems(definition: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """Each repeat in every list a YAML driver file declares, as the driver
    model carries it: ``(place, words)``."""
    for place, values in option_lists(definition):
        yield from repeated_options(place, values, yaml=True)


def repeated_option_errors(
    definition: Mapping[str, Any], judged: Callable[[str, Any], bool],
) -> TaggedErrors:
    """Each repeat as a message opening with its place, tagged with it, in
    the lists ``judged(entry, values)`` lets be judged: ``entry`` is the
    place of the entry that declares the list (a Python driver's checker
    skips one holding a value built in code, and names the skip there)."""
    errors = TaggedErrors()
    for place, values in option_lists(definition):
        if judged(place.removesuffix(".values"), values):
            errors.at_each((where, f"{where}: {words}") for where, words in repeated_options(place, values))
    return errors


def _same_as(earlier: Any, repeat: Any, position: int, kind: str) -> str:
    shown, shown_earlier = _shown(repeat), _shown(earlier)
    words = f"{shown} is the same value as values[{position}]"
    if shown != shown_earlier:
        words += f" ({shown_earlier})"
        if kind == "number":
            words += f", {_ONE_NUMBER}"
    return words + "; list each value once"


def _shown(entry: Any) -> str:
    """An entry's value as JSON writes it: ``"hdmi1"``, ``1.0``, ``true``."""
    value = entry["value"] if isinstance(entry, dict) else entry
    return json.dumps(value)


def _entries(section: str, block: Any) -> Iterator[tuple[str, Any]]:
    for name, entry in _named(block):
        if isinstance(entry, Mapping) and "values" in entry:
            yield f"{section}.{name}.values", entry["values"]


def _named(block: Any) -> Iterator[tuple[Any, Any]]:
    if isinstance(block, Mapping):
        yield from ((name, entry) for name, entry in block.items() if name != UNEVALUATED_KEY)


def _get(block: Any, key: str) -> Any:
    return block.get(key) if isinstance(block, Mapping) else None
