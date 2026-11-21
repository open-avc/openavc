"""THE reading of a json rule's value spec: what it compiles to in a flat
``set:``, in a ``mappings:`` list and in a child.

A json response rule says what to read out of a body with a value spec: a JSON
path written plainly, or the long form ``{key | path, type, map, contains}``,
``key`` winning over ``path``. A rule's flat ``set:`` reads one into a state
variable and its ``mappings:`` list entries the same way
(``compiled_protocol.build_json_mappings``), a json ``child_set:`` entry's
``state:`` into a child's prop (``child_routes``). All three compile it here,
so a new field on the spec is one edit to the compile; what the runtime does
with the compiled mapping is ``json_replies.read_json_mapping``.

The other readers ask it rather than reading the spec for themselves: the
simulator validator counts the keys the compile gives
(``openavc.simulator.reply_reading.json_rule_keys``), and the loader's checks
read the key a child's spec names through ``written_key``
(``response_rule_checks``, vendored with the driver contract). The Driver
Builder's json rows (``getJsonRows`` in ``responseBuilderHelpers.ts``) are a
TypeScript copy kept by hand.

The three places read the spec alike except in the ways each place's
``Place`` row below gives (``FLAT``, ``LISTED``, ``CHILD``):

* the name the mapping carries: ``state`` or ``prop``;
* which fields name the key: ``key`` then ``path``, or in a ``mappings:``
  entry ``key`` alone;
* a long form naming no key reads the state variable's own name in a flat
  ``set:``, reads no field in a ``mappings:`` entry (the runtime passes over
  a mapping with no key) and leaves a child's prop out;
* the key is kept as written in a flat ``set:`` and a ``mappings:`` entry and
  as text in a child, so a ``key: 0`` is read in a child and passed over by
  the runtime's flat reader, which skips a falsy key, and a ``key: null`` is
  kept in the flat forms and leaves a child's prop out;
* ``map`` is kept as written in a flat ``set:`` (present, as None, when the
  spec has none), kept as written when one is written in a ``mappings:``
  entry, and kept only when it is an object in a child.

Each form takes its type from the spec's ``type:``, else the variable's
declared type. The loader reports a child spec with no path up front
(``response_rule_checks``), so a catalog driver never reaches a child's skip.
Standard library only, like ``compiled_protocol`` and ``child_routes``, which
import it: the simulator and the validator import those directly
(``tests/test_compiled_protocol_purity.py``), and the checker's vendored
closure takes it in (``tests/test_avcdriver_semantic_purity.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: What ``written_key`` gives for a long form that names no key.
UNNAMED = object()
#: What a place's key rule returns to leave the value out.
SKIP = object()


@dataclass(frozen=True)
class Place:
    """Where a value spec's reading goes, and how that place reads the spec."""

    #: The name the compiled mapping carries the variable under.
    field: str
    #: The written key or path (``UNNAMED`` when the spec names neither) and
    #: the variable's name, to the key the mapping carries, or ``SKIP``.
    key: Callable[[Any, str], Any]
    #: A long form's ``map``, as the mapping's fields: none, or ``{"map": ...}``.
    value_map: Callable[[dict[str, Any]], dict[str, Any]]
    #: The spec's fields that name the key, the first one written winning.
    names: tuple[str, ...] = ("key", "path")


def _flat_key(written: Any, name: str) -> Any:
    """The key as written; the state variable's own name when none is."""
    return name if written is UNNAMED else written


def _flat_map(spec: dict[str, Any]) -> dict[str, Any]:
    """The map as written, None when the spec has none."""
    return {"map": spec.get("map")}


def _listed_key(written: Any, _name: str) -> Any:
    """The key as written; None, which reads no field, when none is."""
    return None if written is UNNAMED else written


def _listed_map(spec: dict[str, Any]) -> dict[str, Any]:
    """The map as written, when the entry writes one."""
    return {"map": spec["map"]} if "map" in spec else {}


def _child_key(written: Any, _name: str) -> Any:
    """The key as text; ``SKIP`` when none is written, or it is null."""
    if written is UNNAMED or written is None:
        return SKIP
    return str(written)


def _child_map(spec: dict[str, Any]) -> dict[str, Any]:
    """The map when it is an object; nothing otherwise."""
    value_map = spec.get("map")
    return {"map": value_map} if isinstance(value_map, dict) else {}


#: A json rule's flat ``set:`` item, read into a state variable. Its key rule
#: never returns ``SKIP``, so a flat item always compiles to a mapping.
FLAT = Place(field="state", key=_flat_key, value_map=_flat_map)
#: An entry of a json rule's ``mappings:`` list, read into the state variable
#: its ``state:`` names. Its key rule never returns ``SKIP`` either.
LISTED = Place(field="state", key=_listed_key, value_map=_listed_map, names=("key",))
#: A json ``child_set:`` entry's ``state:`` item, read into a child's prop.
CHILD = Place(field="prop", key=_child_key, value_map=_child_map)


def written_key(spec: dict[str, Any], place: Place) -> Any:
    """The key a long form names, as written, by the first of ``place``'s
    naming fields the spec writes; ``UNNAMED`` when it writes none."""
    for name in place.names:
        if name in spec:
            return spec[name]
    return UNNAMED


def json_value_mapping(
    name: str, spec: Any, var_type: Any, place: Place
) -> dict[str, Any] | None:
    """One value spec as the mapping the runtime reads, ``{<field>, key, type,
    map?, contains?}`` in that order, typed by ``var_type`` unless the spec
    says otherwise; None when ``place`` leaves it out.

    A spec that is not an object is the path itself, as text. ``contains`` is
    carried as written: the runtime answers "does the value at key hold this?"
    and stores the answer under the mapping's type. A field the spec carries
    beyond these is not read."""
    if not isinstance(spec, dict):
        return {place.field: name, "key": str(spec), "type": var_type}
    key = place.key(written_key(spec, place), name)
    if key is SKIP:
        return None
    mapping: dict[str, Any] = {
        place.field: name,
        "key": key,
        "type": spec.get("type", var_type),
        **place.value_map(spec),
    }
    if "contains" in spec:
        mapping["contains"] = spec["contains"]
    return mapping
