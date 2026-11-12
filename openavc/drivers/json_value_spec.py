"""What a json rule's value spec compiles to, in a flat ``set:`` and in a child.

A json response rule says what to read out of a body with a value spec: a JSON
path written plainly, or the long form ``{key | path, type, map, contains}``,
``key`` winning over ``path``. A rule's flat ``set:`` reads one into a state
variable (``compiled_protocol.build_json_mappings``), a json ``child_set:``
entry's ``state:`` into a child's prop (``child_routes``). Both compile it
here, so a new field on the spec is one edit to the compile; what the runtime
does with the compiled mapping is ``json_replies.read_json_mapping``. Three
other readings of the spec are kept by hand and do not ask this module: the
loader's checks (``avcdriver_semantic``, vendored), the simulator validator's
json-rule keys (``openavc.simulator.reply_reading.json_rule_keys``) and the Driver
Builder's json rows (``getJsonRows`` in ``responseBuilderHelpers.ts``).

The two read the spec alike except in four ways, each a field of the place's
``Place`` row below (``FLAT``, ``CHILD``):

* the name the mapping carries: ``state`` or ``prop``;
* a long form naming neither key nor path reads the state variable's own name
  in a flat ``set:`` and leaves a child's prop out;
* the key is kept as written in a flat ``set:`` and as text in a child, so a
  ``key: 0`` is read in a child and passed over by the runtime's flat reader,
  which skips a falsy key, and a ``key: null`` is kept in a flat ``set:`` and
  leaves a child's prop out;
* ``map`` is kept as written in a flat ``set:`` (present, as None, when the
  spec has none) and only when it is an object in a child.

The loader reports a child spec with no path up front (``avcdriver_semantic``),
so a catalog driver never reaches a child's skip. Standard library only, like
``compiled_protocol`` and ``child_routes``, which import it: the simulator and
the validator import those directly (``tests/test_compiled_protocol_purity.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: A long form that names neither ``key`` nor ``path``.
_UNNAMED = object()
#: What a place's key rule returns to leave the value out.
SKIP = object()


@dataclass(frozen=True)
class Place:
    """Where a value spec's reading goes, and how that place reads the spec."""

    #: The name the compiled mapping carries the variable under.
    field: str
    #: The written key or path (``_UNNAMED`` when the spec names neither) and
    #: the variable's name, to the key the mapping carries, or ``SKIP``.
    key: Callable[[Any, str], Any]
    #: A long form's ``map``, as the mapping's fields: none, or ``{"map": ...}``.
    value_map: Callable[[dict[str, Any]], dict[str, Any]]


def _flat_key(written: Any, name: str) -> Any:
    """The key as written; the state variable's own name when none is."""
    return name if written is _UNNAMED else written


def _flat_map(spec: dict[str, Any]) -> dict[str, Any]:
    """The map as written, None when the spec has none."""
    return {"map": spec.get("map")}


def _child_key(written: Any, _name: str) -> Any:
    """The key as text; ``SKIP`` when none is written, or it is null."""
    if written is _UNNAMED or written is None:
        return SKIP
    return str(written)


def _child_map(spec: dict[str, Any]) -> dict[str, Any]:
    """The map when it is an object; nothing otherwise."""
    value_map = spec.get("map")
    return {"map": value_map} if isinstance(value_map, dict) else {}


#: A json rule's flat ``set:`` item, read into a state variable. Its key rule
#: never returns ``SKIP``, so a flat item always compiles to a mapping.
FLAT = Place(field="state", key=_flat_key, value_map=_flat_map)
#: A json ``child_set:`` entry's ``state:`` item, read into a child's prop.
CHILD = Place(field="prop", key=_child_key, value_map=_child_map)


def json_value_mapping(
    name: str, spec: Any, var_type: Any, place: Place
) -> dict[str, Any] | None:
    """One value spec as the mapping the runtime reads, ``{<field>, key, type,
    map?, contains?}`` in that order, typed by ``var_type`` unless the spec
    says otherwise; None when ``place`` leaves it out.

    A spec that is not an object is the path itself, as text. ``contains`` is
    carried as written: the runtime answers "does the value at key hold this?"
    and stores the answer under the mapping's type."""
    if not isinstance(spec, dict):
        return {place.field: name, "key": str(spec), "type": var_type}
    key = place.key(spec.get("key", spec.get("path", _UNNAMED)), name)
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
