"""A driver's settings, checked when they load: each ``device_settings``
entry, and a config field's ``learned_from``.

Both point into the driver's state variables. A device setting reads back
from its ``state_key`` (its own name when left out) and is written by its
``write`` block; the YAML runtime writes it in ``drivers/setting_writes.py``,
applying the ``map``, and ``core/pending_settings.py`` holds a write for a
device that is away until the device reports the value. A config field with
``learned_from`` is filled in from the variable it names
(``core/learned_config.py``).

``device_setting_errors`` is what one YAML entry is held to, each part one
function in the order its messages have always come: the type, the
``state_key``, ``min`` against ``max``, the ``write`` block (and an OSC
write's args, through the command encoder's own check), the ``map``. The
``state_key`` and ``learned_from`` rules are shared with a Python driver's
``DRIVER_INFO`` (``python_info``), which words its own shape messages and
judges nothing else of a setting; a state-variable block built at runtime
(``UNEVALUATED_KEY``) is skipped there, not failed. A Python driver's
setting is refused a ``map`` (``python_setting_map_errors``, beside the YAML
rule for one), since only the YAML write applies it.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling contract modules (``spec``, ``declared_names``, ``command_checks``).
The module is vendored with the rest of the driver contract (openavc-drivers'
catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.drivers.command_checks import osc_args_errors
from openavc.drivers.declared_names import UNEVALUATED_KEY, declared_enum_values
from openavc.drivers.spec import CONNECTION_FIELDS, VALUE_TYPES, is_secret_setting

# A set, so a type that is not hashable raises at the lookup, as it always has.
_SETTING_TYPES = set(VALUE_TYPES)


def device_setting_errors(
    setting_name: Any, setting_def: Any, declared_vars: dict[str, Any],
) -> list[str]:
    """What one YAML device setting is held to, in the order it has always
    reported: a mapping, of a known type, reading back from a declared state
    variable, with ``min`` no greater than ``max``, writable, and a ``map`` the
    setting can use."""
    where = f"Device setting '{setting_name}'"
    if not isinstance(setting_def, dict):
        return [f"{where}: must be a mapping"]
    return [
        *_type_errors(where, setting_def),
        *device_setting_state_key_errors(where, setting_name, setting_def, declared_vars),
        *_bounds_errors(where, setting_def),
        *_write_errors(where, setting_def),
        *device_setting_map_errors(where, setting_def),
    ]


def _type_errors(where: str, setting_def: dict[str, Any]) -> list[str]:
    stype = setting_def.get("type", "")
    if not stype or stype in _SETTING_TYPES:
        return []
    return [f"{where}: unknown type '{stype}'"]


def _bounds_errors(where: str, setting_def: dict[str, Any]) -> list[str]:
    """``min`` above ``max`` leaves no value to set; a bound that is not a
    number (a boolean included) is not compared."""
    mn, mx = setting_def.get("min"), setting_def.get("max")
    if not all(map(_is_number, (mn, mx))):
        return []
    if mn > mx:
        return [f"{where}: min ({mn}) is greater than max ({mx})"]
    return []


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _write_errors(where: str, setting_def: dict[str, Any]) -> list[str]:
    """A setting needs a ``write`` block (the runtime raises
    NotImplementedError without one). An OSC write shares the command arg
    encoder, so its arg types are checked too and a bad tag fails at load,
    not at the first write."""
    write = setting_def.get("write")
    if not isinstance(write, dict):
        return [
            f"{where}: missing 'write' block (send / path / address); "
            f"a device setting must be writable"
        ]
    if write.get("address") is None:
        return []
    return osc_args_errors(f"{where} write", write.get("args"))


def device_setting_state_key_errors(
    where: str,
    setting_name: str,
    setting_def: dict[str, Any],
    declared_vars: Any,
) -> list[str]:
    """A device setting's ``state_key`` must name a declared state variable.

    One rule for both surfaces: the YAML entry check and the Python surface
    (``python_info``) both ask it.

    A typo'd ``state_key`` loads fine and shows "(not set)" forever while
    writes fire into nothing — a setting that appears to work and never reads
    back.

    ``declared_vars`` may be an unreadable block on the Python surface (a
    driver that builds its state variables at runtime). "Not in a set we could
    not read" proves nothing, so that case is left to the caller's skip
    reporting rather than turned into an error.
    """
    if not isinstance(declared_vars, dict) or UNEVALUATED_KEY in declared_vars:
        return []
    state_key = setting_def.get("state_key", setting_name)
    if not isinstance(state_key, str):
        return []
    if state_key in declared_vars:
        return []
    return [
        f"{where}: state_key '{state_key}' is not a declared "
        f"state variable, so the setting would never read back"
    ]


def config_learned_from_errors(
    field_name: str,
    field_def: dict[str, Any],
    declared_vars: Any,
) -> list[str]:
    """A config field's ``learned_from`` names the state variable the device
    reports the value in, and the platform saves that report into the field.

    One rule for both surfaces, like ``device_setting_state_key_errors``. A
    name that is not a declared state variable is never reported, so the
    field is silently never filled in. A secret is never learned (the value
    would have to sit in visible state first), a table has no single value
    to learn, and a connection field is saved to the connections table, where
    a reported value would move where the device dials. A state-variable
    block built at runtime cannot be judged from the source and is skipped,
    not failed.
    """
    learned_from = field_def.get("learned_from")
    if learned_from is None:
        return []
    where = f"Config field '{field_name}'"
    if not isinstance(learned_from, str) or not learned_from.strip():
        return [f"{where}: learned_from must name a state variable"]
    return [
        *(f"{where}: {why}" for refused, why in _NEVER_LEARNED if refused(field_name, field_def)),
        *_undeclared_learned_from_errors(where, learned_from, declared_vars),
    ]


# The fields no report may fill in, each with why, in the order they report.
_NEVER_LEARNED: tuple[tuple[Callable[[Any, dict[str, Any]], bool], str], ...] = (
    (
        is_secret_setting,
        "a secret field cannot be learned_from a state variable, "
        "because the value would have to be shown as state first",
    ),
    (
        lambda _name, field_def: field_def.get("type") == "table",
        "a table field cannot be learned_from a state variable, "
        "because it has no single value to fill in",
    ),
    (
        lambda name, _field_def: name in CONNECTION_FIELDS,
        "a connection field cannot be learned_from a state "
        "variable, because a reported value would change how the device "
        "is reached",
    ),
)


def _undeclared_learned_from_errors(
    where: str, learned_from: str, declared_vars: Any,
) -> list[str]:
    if not isinstance(declared_vars, dict) or UNEVALUATED_KEY in declared_vars:
        return []
    if learned_from in declared_vars:
        return []
    return [
        f"{where}: learned_from '{learned_from}' is not a declared "
        f"state variable, so the field would never be filled in"
    ]


def device_setting_map_errors(where: str, setting_def: dict[str, Any]) -> list[str]:
    """A device setting's ``map``: the device's own word for each value.

    The keys are values the setting can take, so a key the editor can never
    write is a word that is never sent: on a ``boolean`` setting only true and
    false, on an ``enum`` only its declared values. Other types take any key.

    A bare YAML ``true`` / ``on`` / ``yes`` (key or word) loads as a boolean,
    which the published schema refuses, so it is refused here with the remedy
    rather than left to fail in catalog CI. Same shape rule as a command
    param's ``map``. A Python driver writes its settings in its own code, so
    there the key is refused outright (``python_setting_map_errors``).
    """
    value_map = setting_def.get("map")
    if value_map is None:
        return []
    if not isinstance(value_map, dict) or not value_map:
        return [
            f"{where}: map must be a non-empty mapping of setting value -> "
            f"device word"
        ]
    stype = setting_def.get("type", "string")
    allowed = declared_enum_values(setting_def) if stype == "enum" else None
    errors: list[str] = []
    for key, word in value_map.items():
        error = _map_pair_error(where, key, word) or _map_key_error(where, key, stype, allowed)
        if error:
            errors.append(error)
    return errors


def python_setting_map_errors(info: dict[str, Any]) -> list[str]:
    """A Python driver's settings, refused a ``map``: the YAML runtime's write
    applies it, and a Python driver writes its settings in its own code, so
    any ``map`` key is refused, whatever it holds. Reported last of the keys
    only a YAML driver's values use (``conversion_checks``)."""
    settings = info.get("device_settings")
    if not isinstance(settings, dict):
        return []
    return [
        f"Device setting '{name}': remove map; the platform maps a setting's "
        f"value only for YAML drivers, and a Python driver writes its settings "
        f"in its own code"
        for name, setting_def in settings.items()
        if isinstance(setting_def, dict) and "map" in setting_def
    ]


def _map_pair_error(where: str, key: Any, word: Any) -> str | None:
    """A pair is text or numbers each side, and never a bare YAML boolean."""
    if isinstance(key, bool):
        return (
            f"{where}: map key '{key}' is not text; YAML reads a bare "
            f"true, false, on, off, yes or no as a boolean. Put it in quotes."
        )
    if isinstance(word, bool):
        return (
            f"{where}: map word for '{key}' is not text; YAML reads a bare "
            f"ON, OFF, YES or NO as a boolean. Put it in quotes."
        )
    if isinstance(key, (str, int, float)) and isinstance(word, (str, int, float)):
        return None
    return (
        f"{where}: map entries must be setting value -> device word "
        f"pairs of text or numbers"
    )


def _map_key_error(where: str, key: Any, stype: Any, allowed: list[str] | None) -> str | None:
    """A key is a value the setting takes: true or false on a boolean, a
    declared value on an enum, anything on another type."""
    if stype == "boolean":
        if str(key).strip().lower() in ("true", "false"):
            return None
        return (
            f"{where}: map key '{key}' is not a value a boolean setting "
            f"takes; use \"true\" and \"false\""
        )
    if allowed is None or str(key) in allowed:
        return None
    return (
        f"{where}: map key '{key}' is not one of the setting's values "
        f"({', '.join(allowed)})"
    )
