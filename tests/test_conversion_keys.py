"""The keys a value conversion is declared with have one home.

``spec.CONVERSION_KEYS`` names them in the order the checks report them. The
runtime reads a declaration's conversion through them
(``compiled_protocol.conversion_of``) and the load-time check refuses them
where nothing would apply them (``conversion_checks``), so both must read the
same tuple, and the registry must declare a field for each, so the published
schema accepts what the runtime converts.
"""
from __future__ import annotations

from openavc.drivers import compiled_protocol, conversion_checks, spec

# The registry entries a conversion may be declared on, and the conversion
# fields each one carries.
_DECLARED_ON = {
    "stateVariableEntry": ("scale", "offset", "unknown"),
    "childStateVariableEntry": ("scale", "offset", "unknown"),
    "paramEntry": ("scale", "offset"),
}
_CONVERSION_FIELDS = (spec.VALUE_SCALE, spec.VALUE_OFFSET, spec.VALUE_UNKNOWN)


def test_both_readers_read_the_contract_tuple() -> None:
    assert compiled_protocol.CONVERSION_KEYS is spec.CONVERSION_KEYS
    assert conversion_checks.CONVERSION_KEYS is spec.CONVERSION_KEYS


def _carriers(node: object) -> set[int]:
    """Every registry entry, at any depth, whose fields carry a conversion field."""
    found: set[int] = set()
    if isinstance(node, dict):
        fields = node.get("fields")
        if isinstance(fields, dict) and any(
            any(v is f for f in _CONVERSION_FIELDS) for v in fields.values()
        ):
            found.add(id(node))
        for value in node.values():
            found |= _carriers(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            found |= _carriers(value)
    return found


def test_every_entry_that_carries_a_conversion_is_named_here() -> None:
    """A registry entry that gains scale, offset or unknown is a new place a
    conversion may be declared: name it above, and give it its place in
    conversion_checks._PLACES, or it is never checked. An entry is found by
    the registry's own VALUE_SCALE / VALUE_OFFSET / VALUE_UNKNOWN dicts, so one
    that writes a fresh dict for the field instead is not seen."""
    named = {id(spec.DEFS[name]) for name in _DECLARED_ON}
    assert _carriers(spec.DEFS) | _carriers(spec.FIELDS) == named


def test_the_registry_declares_a_field_for_each_key() -> None:
    declared: list[str] = []
    for entry, expected in _DECLARED_ON.items():
        fields = spec.DEFS[entry]["fields"]
        carried = tuple(k for k, v in fields.items() if any(v is f for f in _CONVERSION_FIELDS))
        assert carried == expected, entry
        declared += [k for k in carried if k not in declared]
    assert tuple(declared) == spec.CONVERSION_KEYS


def test_each_key_is_the_field_that_names_it() -> None:
    fields = spec.DEFS["stateVariableEntry"]["fields"]
    assert [fields[k] for k in spec.CONVERSION_KEYS] == list(_CONVERSION_FIELDS)


def test_a_conversion_reads_only_those_keys() -> None:
    decl = {"type": "number", "scale": 2, "offset": None, "unknown": [1], "min": 0}
    assert compiled_protocol.conversion_of(decl) == {"scale": 2, "unknown": [1]}
