"""A state variable's own fields, checked when a driver loads.

Two blocks declare state variables: the driver's ``state_variables`` and each
child type's (``child_entity_types.<type>.state_variables``). A field both may
carry (``type``, ``unit``, ``control``, ``cloud_priority``) is one function
here, so the two blocks say the same thing about it. Each block asks its own
list, which says which fields it checks and in what order: only the driver's
own variables need a ``label`` (a child's properties are named by the child),
and the two lists keep the order each block has always reported in. A field a
variable may gain is one function and a line in each list that takes it.

The message opens with ``where``, which each caller words its own way
(``State variable 'power'``, ``child_entity_types.zone.state_variables.level``),
and the caller checks that the variable is a mapping at all.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling ``spec`` table only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.drivers.spec import CLOUD_PRIORITIES, VALUE_TYPES

# A set, so a type YAML reads as a list (``type: [number]``) raises at the
# lookup, as it always has.
_VALUE_TYPES = set(VALUE_TYPES)


def _type_error(var_def: dict[str, Any]) -> str | None:
    var_type = var_def.get("type", "")
    if var_type and var_type not in _VALUE_TYPES:
        return f"unknown type '{var_type}'"
    return None


def _label_error(var_def: dict[str, Any]) -> str | None:
    return None if var_def.get("label") else "missing 'label'"


def _unit_error(var_def: dict[str, Any]) -> str | None:
    unit = var_def.get("unit")
    if unit is not None and not isinstance(unit, str):
        return f"unit must be a string (got {unit!r})"
    return None


def _control_error(var_def: dict[str, Any]) -> str | None:
    control = var_def.get("control")
    if control is not None and not isinstance(control, bool):
        return f"control must be true or false (got {control!r})"
    return None


def _cloud_priority_error(var_def: dict[str, Any]) -> str | None:
    # The cloud state relay reads this tag to pick the forwarding tier; a
    # typo would silently fall back to the default cadence.
    priority = var_def.get("cloud_priority")
    if priority is not None and priority not in CLOUD_PRIORITIES:
        return (
            f"cloud_priority must be 'low' or 'high' (got {priority!r}); "
            f"omit it for the default cadence"
        )
    return None


VariableCheck = Callable[[dict[str, Any]], str | None]

#: The driver's own state variables, in the order they are reported.
DEVICE_VARIABLE: tuple[VariableCheck, ...] = (
    _type_error,
    _label_error,
    _unit_error,
    _control_error,
    _cloud_priority_error,
)

#: A child type's state variables, in the order they are reported.
CHILD_VARIABLE: tuple[VariableCheck, ...] = (
    _type_error,
    _cloud_priority_error,
    _unit_error,
    _control_error,
)


def state_variable_errors(
    where: str, var_def: dict[str, Any], checks: tuple[VariableCheck, ...],
) -> list[str]:
    """What is wrong with one state variable's fields, in ``checks``' order."""
    found = (check(var_def) for check in checks)
    return [f"{where}: {message}" for message in found if message]
