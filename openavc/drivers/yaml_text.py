"""THE rule for a value YAML did not read as text where a driver file means text.

The loader reads a driver file as YAML 1.1 (``yaml.safe_load``), which turns a
bare ``on``, ``off``, ``yes``, ``no``, ``true`` or ``false`` into a boolean and
bare digits into a number (``01`` into 1). Where the file means text, the
author meant the word they typed:

* a key of a reply's value map: a ``mappings:`` entry's ``map:``, a json
  ``set:`` value spec's, a child's ``state:`` value's. Every value map is
  looked up by its keys' words (``compiled_protocol.map_entry``), which still
  matches an unquoted ``01:`` by value, so the driver works; the author's doors
  refuse it so that what was written is what is meant, and a loaded driver
  names it.
* a value such a map writes into a variable that holds text or an enum choice:
  a bare ``on`` there stores ``True``, never the word "on". A value of the
  variable's own kind (a boolean into a boolean variable, a number into an
  integer one) is what the author meant, and null writes no value.

``NOT_TEXT`` is the words every such place says, the shape walk's for an
author-chosen name too (a setting's or a parameter's map key among them,
``avcdriver_semantic``), and a command parameter's map word YAML read as a
boolean (``param_aid_checks``). ``reply_map_problems`` is read by the driver
model (``driver_model``), whose problems the author's doors refuse and a load
names.

Pure: the standard library and ``spec``, vendored with the driver contract.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from openavc.drivers.spec import CHILD_RESERVED_PROP_SCHEMA

NOT_TEXT = (
    "YAML reads bare on/off/yes/no/true/false as booleans and bare "
    "digits as numbers. Put it in quotes."
)

# The value types whose variable holds text: a word, or one of its choices.
_TEXT_TYPES = ("string", "enum")

# A value spec with no type of its own and a variable that declares none is
# read as text, as the compile reads it.
_UNTYPED = "string"


def reply_map_problems(definition: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """``(place, words)`` for each reply map key YAML did not read as text,
    and each value such a map writes that is not text where its variable
    holds text, in rule order. Never raises."""
    responses = definition.get("responses")
    state_vars = _mapping(definition.get("state_variables"))
    child_types = _mapping(definition.get("child_entity_types"))
    for i, rule in enumerate(responses if isinstance(responses, list) else ()):
        if isinstance(rule, Mapping):
            for place, spec, variables, name in _value_specs(f"responses[{i}]", rule, state_vars, child_types):
                yield from _map_problems(place, spec, _declared_type(variables, name))


def _value_specs(
    place: str, rule: Mapping[str, Any], state_vars: Mapping[str, Any], child_types: Mapping[str, Any],
) -> Iterator[tuple[str, Any, Mapping[str, Any], Any]]:
    """Each value spec a rule writes through: its map's place, the spec, the
    variables it is typed by and the name it writes."""
    yield from _mapping_specs(place, rule.get("mappings"), state_vars)
    if rule.get("json"):
        # A text rule's set: takes no map (refused as such where it is checked).
        yield from _named_specs(f"{place}.set", rule.get("set"), state_vars)
    for where, entry in _child_entries(place, rule):
        variables = {**CHILD_RESERVED_PROP_SCHEMA, **_child_variables(child_types, entry.get("type"))}
        yield from _named_specs(f"{where}.state", entry.get("state"), variables)


def _mapping_specs(
    place: str, mappings: Any, state_vars: Mapping[str, Any],
) -> Iterator[tuple[str, Any, Mapping[str, Any], Any]]:
    """A ``mappings:`` list's entries, each naming the variable it writes."""
    listed = enumerate(mappings) if isinstance(mappings, list) else ()
    for j, mapping in listed:
        name = mapping.get("state") if isinstance(mapping, Mapping) else None
        yield f"{place}.mappings[{j}].map", mapping, state_vars, name


def _named_specs(
    place: str, specs: Any, variables: Mapping[str, Any],
) -> Iterator[tuple[str, Any, Mapping[str, Any], Any]]:
    """A ``set:`` or a child's ``state:``: each value spec under the name it writes."""
    for name, spec in _mapping(specs).items():
        yield f"{place}.{name}.map", spec, variables, name


def _child_entries(place: str, rule: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    child_set = rule.get("child_set")
    for j, entry in enumerate(child_set if isinstance(child_set, list) else ()):
        if isinstance(entry, Mapping):
            yield f"{place}.child_set[{j}]", entry
    child_list = rule.get("child_list")
    if isinstance(child_list, Mapping):
        yield f"{place}.child_list", child_list


def _child_variables(child_types: Mapping[str, Any], child_type: Any) -> Mapping[str, Any]:
    type_def = child_types.get(child_type) if isinstance(child_type, str) else None
    return _mapping(type_def.get("state_variables")) if isinstance(type_def, Mapping) else {}


def _declared_type(variables: Mapping[str, Any], name: Any) -> Any:
    var_def = variables.get(name) if isinstance(name, str) else None
    return var_def.get("type") if isinstance(var_def, Mapping) else None


def _map_problems(place: str, spec: Any, declared: Any) -> Iterator[tuple[str, str]]:
    """A spec's map: each key that is not text, then, where the value it
    writes holds text, each value that is not."""
    value_map = spec.get("map") if isinstance(spec, Mapping) else None
    if not isinstance(value_map, Mapping):
        return
    holds_text = _holds_text(spec, declared)
    for key, value in value_map.items():
        yield from ((place, words) for words in _entry_words(key, value, holds_text))


def _holds_text(spec: Mapping[str, Any], declared: Any) -> bool:
    """Whether what a spec writes is text: its own type, else its variable's."""
    return (spec.get("type") or declared or _UNTYPED) in _TEXT_TYPES


def _entry_words(key: Any, value: Any, holds_text: bool) -> list[str]:
    """What one map entry says: its key when YAML did not read it as text,
    then its value when that holds text and YAML read a boolean or number
    (null writes no value)."""
    words = [] if isinstance(key, str) else [f"key '{key}' is not text. {NOT_TEXT}"]
    if holds_text and not isinstance(value, (str, type(None))):
        words.append(f"value '{value}' for '{key}' is not text. {NOT_TEXT}")
    return words


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
