"""A driver's child types, checked when it loads: one ``child_entity_types`` entry.

A child type is checked in this order: its name, that its declaration is a
mapping, its ``id_format``, its ``state_variables`` (each variable's fields
from ``state_variable_checks``, as the driver's own are), then its
``instances:`` roster. ``validate_driver_definition`` asks ``child_type_errors``
about each type and tags what comes back with the type's place.

The name becomes a state-key segment, ``device.<id>.<child_type>.<local_id>.
<prop>``, and feeds ``subscribe_children``' ``device.<id>.<child_type>.*``
pattern, so a dot would corrupt the key and a glob character (``* ? [``) the
fnmatch dispatch that routes a child's state changes.

A roster declares exactly one source (``spec.INSTANCE_SOURCES``) and each has
one function in ``_SOURCE_CHECKS``, so a new way to say which children exist is
a function and a row there; ``count_from_state``, the device-reported count a
roster follows once connected, sits beside any of them. The runtime's twins:
``child_ids.declared_child_ids`` reads a roster, ``declared_roster.py``
registers it, and ``child_entities.py`` keeps the registry the name keys.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling contract modules only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from openavc.drivers.declared_names import config_field_names, declared_id_kind
from openavc.drivers.spec import CHILD_ID_TYPES, INSTANCE_SOURCES
from openavc.drivers.state_variable_checks import CHILD_VARIABLE, state_variable_errors

_GLOB_CHARACTERS = "*?["


class RosterNames:
    """What a roster may name, read once from the definition.

    A ``count_from`` or ``ids_from`` names a config field (``config_derived``
    keys resolve into config at runtime, so they count). A
    ``count_from_state`` names a state variable, judged against the
    ``state_variables`` block as written, not the walk's mapping-only view of
    it: a block that is not a mapping is named on its own, earlier.
    """

    def __init__(self, driver_def: dict[str, Any]) -> None:
        self.config_fields = config_field_names(driver_def)
        self.state_variables = driver_def.get("state_variables") or {}


def child_type_errors(child_type: Any, type_def: Any, names: RosterNames) -> list[str]:
    """What is wrong with one child type, in the order it is reported.

    A name that is not text, or a declaration that is not a mapping, ends the
    type's checks there.
    """
    if not isinstance(child_type, str) or not child_type:
        return [f"child_entity_types: type name {child_type!r} must be a non-empty string"]
    errors = _name_errors(child_type)
    where = f"child_entity_types.{child_type}"
    if not isinstance(type_def, dict):
        return errors + [f"{where}: must be a mapping"]
    errors += _id_format_errors(where, type_def.get("id_format"))
    errors += _child_variable_errors(where, type_def.get("state_variables"))
    errors += _instances_errors(where, child_type, type_def, names)
    return errors


def _name_errors(child_type: str) -> list[str]:
    errors: list[str] = []
    if "." in child_type:
        errors.append(
            f"child_entity_types: type name '{child_type}' must not contain dots "
            f"(used as state key separator)"
        )
    bad = [c for c in _GLOB_CHARACTERS if c in child_type]
    if bad:
        errors.append(
            f"child_entity_types: type name '{child_type}' must not contain "
            f"glob metacharacters ({', '.join(bad)}); they break state-change dispatch"
        )
    return errors


# --- id_format -----------------------------------------------------------
# Checked at load so a malformed declaration says so here, not at connect (a
# bad id_format used to surface as a confusing device-offline).


def _id_format_errors(where: str, id_format: Any) -> list[str]:
    if id_format is None:
        return []
    if not isinstance(id_format, dict):
        return [f"{where}.id_format: must be a mapping"]
    where = f"{where}.id_format"
    return (
        _id_kind_errors(where, id_format)
        + _id_bound_errors(where, id_format)
        + _pad_width_errors(where, id_format)
    )


def _id_kind_errors(where: str, id_format: dict[str, Any]) -> list[str]:
    id_type = id_format.get("type", "integer")
    if id_type in CHILD_ID_TYPES:
        return []
    return [f"{where}: unknown type '{id_type}' (expected 'integer' or 'string')"]


def _id_bound_errors(where: str, id_format: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    mn, mx = id_format.get("min"), id_format.get("max")
    for label, val in (("min", mn), ("max", mx)):
        if val is not None and not _is_integer(val):
            errors.append(f"{where}: {label} must be an integer (got {val!r})")
    # A boolean bound is named above and still compared here, as an int.
    if _all_ints(mn, mx) and mn > mx:
        errors.append(f"{where}: min ({mn}) is greater than max ({mx})")
    return errors


def _pad_width_errors(where: str, id_format: dict[str, Any]) -> list[str]:
    # 0 means "don't pad": it is the runtime's own default (the id is drawn
    # bare when pad_width is falsy), the field registry's minimum, and what the
    # Driver Builder seeds a new child type with. Only a negative width is
    # meaningless.
    pad = id_format.get("pad_width")
    if pad is None:
        return []
    if _is_integer(pad) and pad >= 0:
        return []
    return [f"{where}: pad_width must be zero or a positive integer (got {pad!r})"]


def _is_integer(value: Any) -> bool:
    """An int that is not a bool, which YAML's true/false would otherwise be."""
    return isinstance(value, int) and not isinstance(value, bool)


def _all_ints(*values: Any) -> bool:
    """Every value an int, a bool included (a bool compares as 0 or 1)."""
    return all(isinstance(value, int) for value in values)


def _child_variable_errors(where: str, child_vars: Any) -> list[str]:
    if child_vars is None:
        return []
    if not isinstance(child_vars, dict):
        return [f"{where}.state_variables: must be a mapping"]
    errors: list[str] = []
    for var_name, var_def in child_vars.items():
        var_where = f"{where}.state_variables.{var_name}"
        if not isinstance(var_def, dict):
            errors.append(f"{var_where}: must be a mapping")
            continue
        errors += state_variable_errors(var_where, var_def, CHILD_VARIABLE)
    return errors


# --- the instances: roster ---------------------------------------------------
# A misdeclared roster silently registers nothing: the "declared types, empty
# panel" failure the block exists to replace.


@dataclass(frozen=True)
class _Roster:
    """One type's roster, and what its source's check reads beside it."""

    where: str
    child_type: str
    id_format: dict[str, Any]
    id_kind: Any
    names: RosterNames


def _instances_errors(
    where: str, child_type: str, type_def: dict[str, Any], names: RosterNames,
) -> list[str]:
    """``count_from_state``, then the one source, then ``label``."""
    instances = type_def.get("instances")
    if instances is None:
        return []
    if not isinstance(instances, dict):
        return [f"{where}.instances: must be a mapping"]
    id_format = type_def.get("id_format")
    roster = _Roster(
        where=f"{where}.instances",
        child_type=child_type,
        id_format=id_format if isinstance(id_format, dict) else {},
        id_kind=declared_id_kind(type_def),
        names=names,
    )
    return (
        _count_from_state_errors(roster, instances.get("count_from_state"))
        + _source_errors(roster, instances)
        + _label_errors(roster, instances.get("label"))
    )


def _source_errors(roster: _Roster, instances: dict[str, Any]) -> list[str]:
    sources = [key for key in INSTANCE_SOURCES if key in instances]
    if len(sources) != 1:
        listed = ", ".join(f"'{key}'" for key in INSTANCE_SOURCES)
        return [f"{roster.where}: declare exactly one of {listed}"]
    source = sources[0]
    return _SOURCE_CHECKS[source](roster, source, instances[source])


def _label_errors(roster: _Roster, label: Any) -> list[str]:
    if label is None or isinstance(label, str):
        return []
    return [f"{roster.where}: label must be a string"]


def _count_from_state_errors(roster: _Roster, state_name: Any) -> list[str]:
    if state_name is None:
        return []
    if not isinstance(state_name, str) or not state_name:
        return [f"{roster.where}: count_from_state must name a state variable"]
    if state_name not in roster.names.state_variables:
        return [
            f"{roster.where}: count_from_state {state_name!r} is not a declared "
            f"state variable"
        ]
    return []


def _ids_errors(roster: _Roster, source: str, raw_ids: Any) -> list[str]:
    """``ids``: a literal list, each entry a scalar, an integer on integer ids.

    The first entry that fails is the one named.
    """
    if not isinstance(raw_ids, list) or not raw_ids:
        return [f"{roster.where}: ids must be a non-empty list of literal child ids"]
    for item in raw_ids:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            return [f"{roster.where}: ids entries must be scalars (got {item!r})"]
        if roster.id_kind == "integer" and not _reads_as_integer(item):
            return [
                f"{roster.where}: id {item!r} is not an integer "
                f"({roster.child_type} declares integer ids)"
            ]
    return []


def _reads_as_integer(item: str | int) -> bool:
    try:
        int(str(item).strip())
    except ValueError:
        return False
    return True


def _count_errors(roster: _Roster, source: str, count: Any) -> list[str]:
    """``count``: an integer of at least 1, within the id range."""
    errors: list[str] = []
    if not _is_integer(count) or count < 1:
        errors.append(f"{roster.where}: count must be an integer >= 1 (got {count!r})")
    else:
        mx = roster.id_format.get("max")
        if _is_integer(mx) and count > mx:
            errors.append(f"{roster.where}: count ({count}) exceeds id_format.max ({mx})")
    return errors + _integer_ids_errors(roster, source)


def _config_field_errors(roster: _Roster, source: str, field: Any) -> list[str]:
    """``count_from`` / ``ids_from``: a config field this driver declares."""
    errors: list[str] = []
    if not isinstance(field, str) or not field:
        errors.append(f"{roster.where}: {source} must name a config field")
    elif field not in roster.names.config_fields:
        errors.append(
            f"{roster.where}: {source} '{field}' is not a declared config field "
            f"(config_schema / default_config / config_derived)"
        )
    if source == "count_from":
        errors += _integer_ids_errors(roster, source)
    return errors


def _integer_ids_errors(roster: _Roster, source: str) -> list[str]:
    """A count numbers its children 1..N, which a string-keyed type cannot use."""
    if roster.id_kind != "string":
        return []
    return [
        f"{roster.where}: '{source}' requires integer ids (id_format.type is "
        f"'string'; use 'ids_from')"
    ]


_SOURCE_CHECKS: dict[str, Callable[[_Roster, str, Any], list[str]]] = {
    "count": _count_errors,
    "count_from": _config_field_errors,
    "ids_from": _config_field_errors,
    "ids": _ids_errors,
}
