"""A command's declared effect, checked against what the device reports.

Per ``driver-roadmap`` "An acknowledgement is not an application": a command
that says what it sets (``sets``) is judged, once its window closes, by what
the device reports then. Each declared value is ``confirmed``, ``different``
(it changed, to something else), ``unchanged``, ``not_reported``, or
``already`` (it read that before the command and never moved, so the audit
cannot tell), and ``no_value`` when it names a parameter nobody gave. A
status query's ``query_for`` value is read the same way.

A value the device says its own way still counts (:func:`same_value`): ``"on"``
or ``1`` for ``true``, ``"40"`` for ``40``, another case. The settings step
reads a value it wrote back by the same rule.

On a command with exactly one ``child_id`` parameter, a key the child type
declares means that child's variable (the child's own name wins, as in the
contract); anything else is the device's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openavc.audit.changes import state_label
from openavc.audit.observe import recorded
from openavc.drivers.child_ids import coerce_child_local_id
from openavc.drivers.compiled_protocol import coerce_bool_token, is_bool_token

_PARAM_REF = re.compile(r"\{([^{}]+)\}")


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
    for ``40``, another case), so a flag is read as a flag, a number as a
    number, and anything else as text.
    """
    if actual is None:
        return False
    if actual == expected:
        return True
    if isinstance(expected, bool) or isinstance(actual, bool):
        if is_bool_token(expected) and is_bool_token(actual):
            return coerce_bool_token(expected) == coerce_bool_token(actual)
        return False
    try:
        return float(expected) == float(actual)
    except (TypeError, ValueError):
        pass
    return str(expected).strip().lower() == str(actual).strip().lower()


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
    ref = _PARAM_REF.fullmatch(value) if isinstance(value, str) else None
    if ref:
        given = params.get(ref.group(1))
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
    for effect in declared_effects(entry, trial.params, readout.driver):
        key = effect["state_key"]
        now = readout.state.get(key)
        outcome = effect_outcome(effect, now, trial.before.get(key), key in readout.changed)
        out.append({
            **effect, "label": state_label(readout.info, key), "value": recorded(now),
            "outcome": outcome,
        })
    return out


def effect_outcome(effect: dict[str, Any], now: Any, before: Any, changed: bool) -> str:
    """One declared value against what the device reports now (``now``),
    what it read as the command was sent (``before``), and whether it
    changed inside the window."""
    if not effect["has_value"]:
        return "no_value"
    if same_value(effect["expected"], now):
        if not changed and same_value(effect["expected"], before):
            return "already"
        return "confirmed"
    if now is None:
        return "not_reported"
    return "different" if changed else "unchanged"


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
    """A declared variable's key: the addressed child's when the child type
    declares it, else the device's own."""
    if child and name in child[2]:
        return f"{child[0]}.{child[1]}.{name}"
    return name


def _addressed_child(
    entry: dict[str, Any], params: dict[str, Any], driver: Any,
) -> tuple[str, str, set[str]] | None:
    """(child type, its id as state keys spell it, its variables) for a
    command with exactly one ``child_id`` parameter that was given a value.

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
    variables = set((types[ctype] or {}).get("state_variables") or {})
    return ctype, padded, variables


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
