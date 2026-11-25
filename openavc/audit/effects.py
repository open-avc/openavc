"""A command's declared effect, checked against what the device reports.

Per ``driver-roadmap`` "An acknowledgement is not an application": a command
that says what it sets (``sets``) is judged, once its window closes, by what
the device reports then. Each declared value is ``confirmed``, ``different``
(it changed, to something else), ``unchanged``, ``not_reported``, or
``already`` (it read that before the command and never moved, so the audit
cannot tell), and ``no_value`` when it names a parameter nobody gave. A
status query's ``query_for`` value is read the same way.

A value the device says its own way still counts (:func:`effect_holds`):
``"on"`` or ``1`` for ``true``, ``"40"`` for ``40``, another case
(:func:`same_value`). On a variable a device setting reads back through, a
value the setting's declaration reads as the one declared counts too, as the
platform confirms a write of that setting (``audit/setting_reading``): an
option's label for its value.

On a command with exactly one ``child_id`` parameter, a key that child may be
set by means that child's variable: one its type declares, or a property
every child has (``online``, ``label``, ...), the keys the checker lets such
a command name (``declared_names.child_writable_props``); the child's name
wins, as in the contract, and anything else is the device's own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openavc.audit.changes import state_label
from openavc.audit.observe import recorded
from openavc.audit.setting_reading import Setting, setting_says, settings_by_variable
from openavc.drivers.child_ids import coerce_child_local_id
from openavc.drivers.declared_names import child_writable_props
from openavc.drivers.spec import bare_placeholder
from openavc.drivers.truth_words import read_truth

@dataclass
class Readout:
    """The device as a command's window closed: its status values, the keys
    that changed inside the window, and the driver that names them."""

    state: dict[str, Any]
    changed: set[str]
    driver: Any

    @property
    def info(self) -> dict[str, Any]:
        return getattr(self.driver, "DRIVER_INFO", None) or {}


def same_value(expected: Any, actual: Any) -> bool:
    """Does a reported value say what a command's declared effect says?

    The device may say it its own way (``"on"`` or ``1`` for ``true``, ``"40"``
    for ``40``, another case), so a flag is read by the words that mean true
    and false (``drivers/truth_words``), a number as a number, and anything
    else as text.
    """
    if actual is None:
        return False
    if actual == expected:
        return True
    if isinstance(expected, bool) or isinstance(actual, bool):
        flags = read_truth(expected), read_truth(actual)
        return None not in flags and flags[0] == flags[1]
    try:
        return float(expected) == float(actual)
    except (TypeError, ValueError):
        pass
    return str(expected).strip().lower() == str(actual).strip().lower()


def effect_holds(expected: Any, actual: Any, setting: Setting | None) -> bool:
    """Does a reported value say what a command's declared effect says? By
    :func:`same_value`, or, when the variable is a device setting's
    (``setting``), as the setting reads it: a command is not a write of the
    setting, so its declaration adds the forms it knows and takes none
    away."""
    if same_value(expected, actual):
        return True
    return setting is not None and setting_says(setting, expected, actual) is True


def declared_effects(
    entry: dict[str, Any], params: dict[str, Any], driver: Any,
) -> list[dict[str, Any]]:
    """The state a command says it sets (``sets``), as keys of the device's
    state, each with the value it should now read (``has_value`` false when
    it names a parameter nobody gave)."""
    sets = entry.get("sets") or {}
    if not isinstance(sets, dict) or not sets:
        return []
    child = _addressed_child(entry, params, driver)
    return [_declared(key, value, child, params) for key, value in sets.items()]


def _declared(
    key: str, value: Any, child: tuple[str, str, set[str]] | None, params: dict[str, Any],
) -> dict[str, Any]:
    ref = bare_placeholder(value)
    if ref is not None:
        given = params.get(ref)
        has_value = given not in (None, "")
        expected = given if has_value else None
    else:
        has_value, expected = True, value
    return {
        "state": key, "state_key": _state_key(child, key), "expected": recorded(expected),
        "has_value": has_value,
    }


def judged_effects(
    entry: dict[str, Any], trial: Any, readout: Readout,
) -> list[dict[str, Any]]:
    """Each value ``trial`` declares it sets, with what the device reports
    now and the outcome."""
    out = []
    settings = settings_by_variable(readout.info)
    for effect in declared_effects(entry, trial.params, readout.driver):
        key = effect["state_key"]
        reading = VariableReading(
            now=readout.state.get(key), before=trial.before.get(key),
            changed=key in readout.changed, setting=settings.get(key),
        )
        out.append({
            **effect, "label": state_label(readout.info, key), "value": recorded(reading.now),
            "outcome": effect_outcome(effect, reading),
        })
    return out


@dataclass(frozen=True)
class VariableReading:
    """A declared value's variable as a command's window closed: what it
    reads now, what it read as the command was sent, whether it changed
    inside the window, and the device setting it reads back through, if one
    does."""

    now: Any
    before: Any
    changed: bool
    setting: Setting | None = None

    def says(self, expected: Any, value: Any) -> bool:
        return effect_holds(expected, value, self.setting)


def effect_outcome(effect: dict[str, Any], reading: VariableReading) -> str:
    """One declared value against what its variable reads (``reading``)."""
    if not effect["has_value"]:
        return "no_value"
    expected = effect["expected"]
    if reading.says(expected, reading.now):
        if not reading.changed and reading.says(expected, reading.before):
            return "already"
        return "confirmed"
    if reading.now is None:
        return "not_reported"
    return "different" if reading.changed else "unchanged"


def query_answer(
    entry: dict[str, Any], trial: Any, readout: Readout, answered: bool,
) -> dict[str, Any]:
    """What a status query's reply reported for its ``query_for`` value
    (``answered``: anything came back in its window)."""
    query_for = entry["query_for"]
    key = _state_key(_addressed_child(entry, trial.params, readout.driver), query_for)
    value = readout.state.get(key)
    if not answered:
        outcome = "no_reply"
    else:
        outcome = "reported" if value is not None else "not_reported"
    return {
        "state": query_for, "state_key": key, "label": state_label(readout.info, key),
        "value": recorded(value),
        "changed": key in readout.changed,
        "outcome": outcome,
    }


def _state_key(child: tuple[str, str, set[str]] | None, name: str) -> str:
    """A declared variable's key: the addressed child's when that child may
    be set by it, else the device's own."""
    if child and name in child[2]:
        return f"{child[0]}.{child[1]}.{name}"
    return name


def _addressed_child(
    entry: dict[str, Any], params: dict[str, Any], driver: Any,
) -> tuple[str, str, set[str]] | None:
    """(child type, its id as state keys spell it, the keys it may be set by)
    for a command with exactly one ``child_id`` parameter that was given a
    value.

    The value arrives as the form sent it ("1"); it is coerced to the kind
    the child type declares before it is padded, as every door that takes a
    child id does."""
    pdefs = entry.get("params") or {}
    name = _child_param(pdefs)
    if name is None:
        return None
    ctype = pdefs[name].get("child_type")
    types = (getattr(driver, "DRIVER_INFO", {}) or {}).get("child_entity_types") or {}
    local = params.get(name)
    if not _addressable(ctype, types, local):
        return None
    padded = _padded_id(driver, ctype, types[ctype], local)
    return ctype, padded, child_writable_props(types[ctype])


def _addressable(ctype: Any, types: dict[str, Any], local: Any) -> bool:
    """A child type the driver declares, and an id given for it."""
    return isinstance(ctype, str) and ctype in types and local not in (None, "")


def _child_param(pdefs: dict[str, Any]) -> str | None:
    """The command's one ``child_id`` parameter, or None for none or several."""
    names = [n for n, p in pdefs.items() if isinstance(p, dict) and p.get("type") == "child_id"]
    return names[0] if len(names) == 1 else None


def _padded_id(driver: Any, ctype: str, cdef: Any, local: Any) -> str:
    typed = coerce_child_local_id(cdef, local)
    try:
        return str(driver.format_child_id(ctype, local if typed is None else typed))
    except Exception:
        return str(local)
