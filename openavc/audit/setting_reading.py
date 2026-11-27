"""A variable a device setting reads back through, read as that setting's
value, for every comparison the Device Audit makes on one.

A device may report a setting's value in its own form: an option's label for
its value ("HDMI 1" for "1"), "on" for true, "40" for 40. The platform
confirms a written setting by one rule, ``core/pending_settings.
readback_confirms``, which reads the report through the setting's
declaration. The audit asks the same rule wherever it judges whether the
device shows a value on such a variable: the settings step (``settings``: the
value a setting already has, a write the device never reported, the fresh
read before a write) and a command's declared effect on it (``effects``,
:func:`setting_says`). So the audit never calls a setting written, held or
put back by another rule than the one that confirms it.

Whether such a variable changed (``changes``) is a stricter question:
:func:`setting_reads_alike` asks whether the setting's declaration writes
both readings as one value, so a label and its value are one reading, while
a free-text name that changed only in case still changed.

A variable no device setting reads back through, or more than one does, has
no one declaration to read it by (:func:`settings_by_variable` leaves it
out), and a value the setting refuses (a ``sets:`` value outside its list)
is not one the setting can say anything about (:func:`setting_says` answers
None): each caller reads those by its own rule.

A variable a setting the driver marks secret reads through
(:func:`secret_status_values`) is shown as ``***`` wherever the audit shows
it as a status value (:func:`shown_status_value`: the connect step's status
table, what changed), as the settings step shows the setting itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from openavc.core.pending_settings import readback_confirms, readback_state_key
from openavc.drivers.base import validate_device_setting_value
from openavc.drivers.secret_params import secret_setting_state_keys
from openavc.utils.log_redaction import REDACTED

# What the value gate raises for a value a declaration refuses, and for a
# declaration it cannot read.
_REFUSED = (ValueError, TypeError)


class Setting(NamedTuple):
    """One device setting: its key and its declaration."""

    key: str
    definition: Any


def settings_by_variable(info: Mapping[str, Any]) -> dict[str, Setting]:
    """Each state variable exactly one of the driver's device settings reads
    back through (``readback_state_key``), with that setting."""
    variables = info.get("state_variables") or {}
    found: dict[str, list[Setting]] = {}
    for key, sdef in (info.get("device_settings") or {}).items():
        state_key = readback_state_key(key, sdef, variables)
        if state_key is not None:
            found.setdefault(state_key, []).append(Setting(key, sdef))
    return {state_key: only for state_key, (only, *more) in found.items() if not more}


def secret_status_values(info: Mapping[str, Any]) -> frozenset[str]:
    """The status values a device setting the driver marks secret reads
    through (``drivers/secret_params.secret_setting_state_keys``)."""
    return secret_setting_state_keys(info.get("device_settings"))


def shown_status_value(secret: frozenset[str], key: str, value: Any) -> Any:
    """The status value ``key`` as the audit shows it: ``***`` when it is one
    of ``secret`` (:func:`secret_status_values`) and holds anything."""
    return REDACTED if key in secret and value not in (None, "") else value


def _written(setting: Setting, value: Any) -> tuple[bool, Any]:
    """``value`` as the setting writes it, through the platform's value gate,
    and whether the gate took it."""
    try:
        return True, validate_device_setting_value(setting.key, setting.definition, value)
    except _REFUSED:
        return False, None


def setting_says(setting: Setting, value: Any, reported: Any) -> bool | None:
    """Does ``reported`` say ``value``, as the platform confirms a write of
    the setting? ``value`` (a command's parameter, a ``sets:`` value, an
    earlier report) is put through the setting's declaration as a write is
    first. None when the declaration refuses ``value``: it has no say on it."""
    took, written = _written(setting, value)
    if not took:
        return None
    return readback_confirms(setting.key, setting.definition, written, reported)


def setting_reads_alike(setting: Setting, one: Any, other: Any) -> bool:
    """Are two readings one value of the setting: does its declaration take
    both and write them as the same value?"""
    took_one, first = _written(setting, one)
    took_other, second = _written(setting, other)
    return took_one and took_other and first == second
