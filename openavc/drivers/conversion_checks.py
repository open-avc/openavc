"""Where a value conversion may be declared, and on what: ``scale``,
``offset`` and ``unknown`` (``spec.CONVERSION_KEYS``), checked when a driver
loads.

The platform converts a YAML driver's values (``compiled_protocol``'s
``reading_from_wire`` and ``value_to_wire``, which the runtime and the
simulator call); a Python driver converts in its own code. So on the Python
surface (``python=True``, asked by ``python_info``) every one is refused
rather than silently doing nothing, and so is a device setting's ``map``,
which only the YAML write applies (``setting_checks.python_setting_map_errors``,
reported last). On a YAML driver all three need a numeric type, a scale of 0
cannot be converted back, and an action's parameters are never converted (the
command's own are).

Each place a conversion may be declared is one function in ``_PLACES``, in
the order its messages have always come: the driver's state variables, each
child type's, each command's parameters, each action's. A declaration is
judged by one function whatever its place, so a new place is one function and
one entry there. The
walk reports every message under its ``state_variables`` tag, whichever place
the message names.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling contract modules (``spec``, ``setting_checks``). The module is
vendored with the rest of the driver contract (openavc-drivers' catalog CI,
openavc-sdk).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from openavc.drivers.setting_checks import python_setting_map_errors
from openavc.drivers.spec import CONVERSION_KEYS

# A conversion needs a number to work on.
_CONVERTIBLE_TYPES = ("integer", "number", "float")


def value_conversion_errors(
    driver_def: dict[str, Any], *, python: bool = False
) -> list[str]:
    """Where ``scale`` / ``offset`` / ``unknown`` may appear, and on what.

    The platform converts a YAML driver's values; a Python driver converts in
    its own code, so on the Python surface (``python=True``) every one is
    refused rather than silently doing nothing, and so is a device setting's
    ``map``, which only the YAML write applies. On a YAML driver all three
    need a numeric type, a scale of 0 cannot be converted back, and an
    action's params are never converted (the command's own params are).
    """
    errors = [
        message
        for where, decl, in_action in _conversion_sites(driver_def)
        for message in _declaration_errors(where, decl, python=python, in_action=in_action)
    ]
    if python:
        errors.extend(python_setting_map_errors(driver_def))
    return errors


def _conversion_sites(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any, bool]]:
    """Each place a conversion may be declared, as ``(where, declaration,
    in_action)``, in the order they report. ``where`` is written for every
    entry, judged or not."""
    for place in _PLACES:
        yield from place(driver_def)


def _variable_sites(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any, bool]]:
    for name, decl in _entries(driver_def.get("state_variables")):
        yield f"State variable '{name}'", decl, False


def _child_variable_sites(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any, bool]]:
    for ctype, name, decl in _inner_entries(driver_def.get("child_entity_types"), "state_variables"):
        yield f"child_entity_types.{ctype}.state_variables.{name}", decl, False


def _command_param_sites(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any, bool]]:
    for cmd_name, pname, pdef in _inner_entries(driver_def.get("commands"), "params"):
        yield f"Command '{cmd_name}' param '{pname}'", pdef, False


def _action_param_sites(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any, bool]]:
    """An action's parameters, which are never converted (the command's are)."""
    actions = driver_def.get("actions")
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict):
            for pname, pdef in _entries(action.get("params")):
                yield f"Action '{action.get('id')}' param '{pname}'", pdef, True


_PLACES: tuple[Callable[[dict[str, Any]], Iterator[tuple[str, Any, bool]]], ...] = (
    _variable_sites,
    _child_variable_sites,
    _command_param_sites,
    _action_param_sites,
)


def _inner_entries(block: Any, key: str) -> Iterator[tuple[Any, Any, Any]]:
    """Each entry of each mapping's ``key`` block, with the mapping's name: a
    child type's variables, a command's parameters. An entry that is not a
    mapping has none."""
    for owner, owner_def in _entries(block):
        if isinstance(owner_def, dict):
            for name, decl in _entries(owner_def.get(key)):
                yield owner, name, decl


def _entries(block: Any) -> list[tuple[Any, Any]]:
    """A mapping's entries; anything else has none."""
    return list(block.items()) if isinstance(block, dict) else []


def _declaration_errors(
    where: str, decl: Any, *, python: bool, in_action: bool
) -> list[str]:
    """What one declaration is held to: nothing without a conversion key; on
    the Python surface, its removal; on an action's parameter, the move to
    the command's own; otherwise a numeric type and a scale that is not 0."""
    if not isinstance(decl, dict):
        return []
    present = [k for k in CONVERSION_KEYS if k in decl]
    if not present:
        return []
    if python:
        return [
            f"{where}: remove {', '.join(present)}; the platform converts values "
            f"only for YAML drivers, and a Python driver converts in its own code"
        ]
    if in_action:
        return [
            f"{where}: put scale and offset on the command's own parameter; an "
            f"action's parameters are not converted"
        ]
    errors = []
    if decl.get("type", "string") not in _CONVERTIBLE_TYPES:
        errors.append(
            f"{where}: the type must be integer, number or float to use "
            f"{', '.join(present)}"
        )
    if _is_zero(decl.get("scale")):
        errors.append(f"{where}: scale cannot be 0")
    return errors


def _is_zero(scale: Any) -> bool:
    """A number equal to 0. A boolean is not a number here, and text is not
    read as one."""
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        return False
    return scale == 0
