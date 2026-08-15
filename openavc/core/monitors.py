"""
THE monitor rule: what a monitored reading is, and whether one is all right.

A room's project says which readings matter in *this* room -- lamp hours, a DSP
temperature, whether the amp is faulted, whether the room is occupied -- and
what "normal" is for each. That declaration drives three surfaces: the local
Dashboard, the cloud health card, and an alert. Decision 6 of the monitor plan
is that all three read ONE declaration, because a red tile with no alert firing
(or an alert firing over a calm tile) is worse than either alone.

So the judgement lives here, once, as a pure function of (monitor, value), and
everything that needs it asks. The Programmer IDE's mirror is
``monitorHelpers.ts``, pinned to this module case-for-case by
``tests/test_monitor_parity.py`` and its vitest twin over the shared corpus in
``tests/fixtures/monitor_parity_cases.json`` -- the same arrangement that holds
``page_review`` and ``reviewPage`` together, and for the same reason: a person
reading a tile and an alert arriving on a phone must not disagree.

Two things this module deliberately does NOT do:

* **It never invents a judgement.** A monitor with no limits declared is
  ``UNSET`` -- informational, drawn neutral, never green. Green is a claim, and
  a green tile on a reading nobody defined as healthy is a claim its author did
  not make.
* **It never renders ``true`` / ``false``.** A boolean reads "Occupied" /
  "Vacant", or failing that "Yes" / "No". The raw value is a fact about the
  wire, not a word for a person.

Pure stdlib, so the agent, the API and a test can all import it without pulling
in the engine.
"""

from __future__ import annotations

import re
from typing import Any

#: Nothing was declared about this reading, so nothing is claimed about it.
UNSET = "unset"
#: The key has never reported. Draws "--", never 0 -- no value is not zero.
NO_VALUE = "no_value"
#: Inside the declared normal.
NORMAL = "normal"
#: Outside it.
ABNORMAL = "abnormal"

#: What a bare boolean reads as when the author wrote no words for it. Never
#: "true"/"false" -- see the module docstring.
_BOOLEAN_WORDS = {"true": "Yes", "false": "No"}


def _norm(value: Any) -> str:
    """A value as the string both sides of a comparison agree on.

    Booleans are the whole reason this exists. The live value off a driver is a
    Python ``True``; the project spells the same thing ``"true"`` because a
    ``states`` map is keyed by JSON strings. Lowercasing anything that spells a
    boolean makes those the same value from either direction, and leaves every
    other enum comparison case-sensitive -- which it must be, since ``"Mic"``
    and ``"mic"`` are different inputs on plenty of frames.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    lowered = text.lower()
    return lowered if lowered in ("true", "false") else text


#: What counts as a number when a range is compared against a reading.
#: Deliberately narrower than ``float()``: a state value is a flat primitive off
#: a wire, and ``float()`` also accepts ``"inf"``, ``"nan"`` and (unlike the
#: IDE's mirror) rejects things JavaScript's ``Number()`` takes. Pinning both
#: sides to one spelling of "is this a number" is what keeps the two from
#: disagreeing about a reading neither of them should be judging.
_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def as_number(value: Any) -> float | None:
    """The reading as a number, or ``None`` when a range cannot address it.

    A boolean is not a number here. ``float(True)`` is ``1.0`` in Python, so
    without this a range declared on a numeric key would quietly start judging
    an unrelated boolean as "0, which is below your minimum".
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _NUMERIC_RE.match(value.strip()):
        return float(value.strip())
    return None


def normal_values(monitor: dict[str, Any]) -> list[str]:
    """The values this monitor calls normal, normalised, in declared order.

    Empty means no value-level limit was declared. A ``states`` entry carrying
    only a ``label`` is vocabulary, not a limit -- it says what a value is
    CALLED, not whether it is all right.
    """
    states = monitor.get("states")
    if not isinstance(states, dict):
        return []
    return [
        _norm(value)
        for value, entry in states.items()
        if isinstance(entry, dict) and entry.get("normal") is True
    ]


def has_limits(monitor: dict[str, Any]) -> bool:
    """Whether anybody said what normal looks like for this reading."""
    if monitor.get("normal_min") is not None or monitor.get("normal_max") is not None:
        return True
    return bool(normal_values(monitor))


def monitor_status(monitor: dict[str, Any], value: Any) -> str:
    """Is this reading all right: one of the four constants above.

    ``value`` is the live state value, or ``None`` for a key that has never
    reported. ``None`` is the absence of a reading rather than a fourth type,
    so it is ``NO_VALUE`` even on a monitor with limits -- "the projector has
    not told us" is a different sentence from "the projector is wrong", and a
    key that stops reporting entirely is what an absence rule is for.
    """
    if not has_limits(monitor):
        return UNSET
    if value is None:
        return NO_VALUE

    allowed = normal_values(monitor)
    if allowed:
        return NORMAL if _norm(value) in allowed else ABNORMAL

    low = monitor.get("normal_min")
    high = monitor.get("normal_max")
    number = as_number(value)
    if number is None:
        # A range was declared and the reading is not a number. Saying ABNORMAL
        # would be a judgement about a value the limits cannot address; the
        # honest answer is that nothing here applies to it.
        return UNSET
    if low is not None and number < float(low):
        return ABNORMAL
    if high is not None and number > float(high):
        return ABNORMAL
    return NORMAL


def monitor_word(monitor: dict[str, Any], value: Any) -> str | None:
    """The word for this value, or ``None`` when the value speaks for itself.

    Returns the author's word first, then the built-in Yes/No for a bare
    boolean. A number returns ``None`` -- 412 is already the right thing to
    show, and the unit rides beside it rather than inside it.
    """
    if value is None:
        return None
    key = _norm(value)
    states = monitor.get("states")
    if isinstance(states, dict):
        for candidate, entry in states.items():
            if _norm(candidate) == key and isinstance(entry, dict) and entry.get("label"):
                return str(entry["label"])
    if isinstance(value, bool):
        return _BOOLEAN_WORDS[key]
    return None


def monitor_reading(monitor: dict[str, Any], value: Any) -> str:
    """Value plus unit, as a tile shows it.

    "--" when nothing has reported: no value is not zero, and a key that has
    never spoken must not be drawn as one that answered 0.
    """
    if value is None:
        return "—"
    word = monitor_word(monitor, value)
    if word:
        return word
    unit = str(monitor.get("unit") or "").strip()
    return f"{value} {unit}" if unit else str(value)


def monitor_label(monitor: dict[str, Any]) -> str:
    """What to call this reading. Falls back to the key so a tile is never blank."""
    label = monitor.get("label")
    if isinstance(label, str) and label.strip():
        return label
    return str(monitor.get("key") or "")


# --- Keeping the list honest --------------------------------------------------
#
# A monitor whose key can never report again is a tile that reads "--" forever
# and an alert that can never fire. Every door that deletes the thing being
# watched takes its monitors with it, and each namespace is spelled out once,
# here, rather than three times at three delete sites.


def _monitor_key(monitor: Any) -> str:
    """The key off a monitor, whether it is a model or a plain dict."""
    key = monitor.get("key") if isinstance(monitor, dict) else getattr(monitor, "key", "")
    return key if isinstance(key, str) else ""


def drop_monitors_for_device(monitors: list[Any], device_id: str) -> list[Any]:
    """Monitors left after a device goes, its child entities included."""
    prefix = f"device.{device_id}."
    return [m for m in monitors if not _monitor_key(m).startswith(prefix)]


def drop_monitors_for_variable(monitors: list[Any], variable_id: str) -> list[Any]:
    """Monitors left after a variable goes."""
    key = f"var.{variable_id}"
    return [m for m in monitors if _monitor_key(m) != key]


# --- Compiling to alert rules -------------------------------------------------

#: Rule ids for project-declared monitors are namespaced so they cannot collide
#: with the cloud's UUIDs, and must contain no ``:`` -- the alert monitor's
#: bookkeeping key is ``rule:<id>:<state key>`` and splits on it.
_RULE_PREFIX = "monitor."

#: Every project-declared limit fires at the same severity. The authoring form
#: is deliberately one toggle, a "normal is..." widget and a duration (plan
#: §5.2); a severity picker there would be a fourth control earning its place by
#: nothing, and the fleet-wide portal rules are where graded severity belongs.
_SEVERITY = "warning"


def rule_id_for(key: str) -> str:
    """The alert rule id a monitor on ``key`` compiles to."""
    return _RULE_PREFIX + key


def is_monitor_rule(rule_id: Any) -> bool:
    """Whether an alert rule id came from a project monitor rather than the cloud."""
    return isinstance(rule_id, str) and rule_id.startswith(_RULE_PREFIX)


def compile_alert_rules(monitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project monitors, as rules the existing AlertMonitor already evaluates.

    This is the whole of decision 6's mechanism: nothing new evaluates a limit.
    A monitor becomes an ordinary rule dict in the shape the cloud already
    pushes, so notification routes, acknowledgement, alert history and the
    alerts list all work exactly as they do for a portal-authored rule -- and
    the tile and the alert cannot drift, because both are reading the same
    declaration.

    ``Alert.alert_rule_id`` is nullable on the cloud and "Ask for help" already
    fires with ``rule_id="help"``, so there is no cloud row behind any of these.

    A value-level limit compiles to a ``pattern`` rule (``not_in`` the normal
    set), which is what carries the duration. A range compiles to up to two
    ``threshold`` rules, one per end, because a threshold rule holds one
    operator and one value.
    """
    rules: list[dict[str, Any]] = []
    for monitor in monitors:
        key = monitor.get("key")
        if not isinstance(key, str) or not key:
            continue
        if not has_limits(monitor):
            continue
        name = monitor_label(monitor)
        duration = _duration_seconds(monitor)
        base = {
            "id": rule_id_for(key),
            "name": name,
            "enabled": True,
            "severity": _SEVERITY,
            "category": "device" if key.startswith("device.") else "system",
        }
        allowed = normal_values(monitor)
        if allowed:
            rules.append({
                **base,
                "rule_type": "pattern",
                "condition": {
                    "key": key,
                    "operator": "not_in",
                    "value": allowed,
                    "duration_seconds": duration,
                },
            })
            continue

        low = monitor.get("normal_min")
        high = monitor.get("normal_max")
        if low is not None:
            rules.append({
                **base,
                "id": base["id"] + ".below",
                "rule_type": "threshold",
                "condition": {
                    "key": key,
                    "operator": "<",
                    "value": low,
                    "duration_seconds": duration,
                },
            })
        if high is not None:
            rules.append({
                **base,
                "id": base["id"] + ".above",
                "rule_type": "threshold",
                "condition": {
                    "key": key,
                    "operator": ">",
                    "value": high,
                    "duration_seconds": duration,
                },
            })
    return rules


def _duration_seconds(monitor: dict[str, Any]) -> float:
    """How long a reading must be wrong before anyone is told. 0 = immediately."""
    try:
        return max(0.0, float(monitor.get("duration_seconds") or 0))
    except (TypeError, ValueError):
        return 0.0
