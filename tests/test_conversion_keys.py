"""The keys a value conversion is declared with have one home.

``spec.CONVERSION_KEYS`` names them in the order the checks report them. The
runtime reads a declaration's conversion through them
(``compiled_protocol.conversion_of``) and the load-time check refuses them
where nothing would apply them (``conversion_checks``), so both must read the
same tuple, and the registry must declare a field for each, so the published
schema accepts what the runtime converts.
"""
from __future__ import annotations

from openavc.drivers import compiled_protocol, spec

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
