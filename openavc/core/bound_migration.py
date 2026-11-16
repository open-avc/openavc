"""
The project format's 0.17.0 step, its repair of saved values the schema now
bounds, apart from boxes (``core/box_migration.py``): a readout's decimals,
the panel settings and the panel lock PIN, a trigger's delay, debounce and
cooldown, and a macro's own cooldown.

A readout's ``display_decimals`` and a panel setting are counts, moved to the
nearer end of their bound; a page transition or stagger of 0, which the panel
drew as its default, is removed instead, so it loads as the default. A count
that is not a whole number is left alone.

A panel lock PIN the lock screen could not take becomes what that screen
holds when somebody types it there: its digits 0 to 9, in order, the first
six. Before this step the screen kept the first six characters typed and the
server compared the whole PIN, so a longer one never unlocked the panel at the
glass; now the PIN that person knows does. One with no such digit (letters
only, or another script's digits) becomes blank, which is no lock: no PIN of
digits keeps that lock and can be typed.

A time in seconds is brought to 0 when negative: the engines waited no time
for it, as for 0. One that is no finite number is removed, so it loads as 0.
The engines read a NaN and a negative infinity as 0 already. A positive
infinity held a trigger back for good: a delay or debounce that never ended, a
cooldown that never lapsed after the trigger's first run. That trigger is
turned off as well, so it still does not fire. A macro's infinite cooldown
held it to one start a server run; a macro has no off switch, so it loads with
no cooldown. The file writer put such a value down as a bare ``Infinity``,
which a browser cannot read, so the Programmer could not open a project
holding one. A time is read as the schema read it before this step
(``number_0_17``: a number, true or false, or text that reads as one).

Anything the schema refused before this step, it still does, so it is left
alone. The numbers are the step's own, as they stood at 0.17.0, not read from
the project models, for the reason the 0.15.0 table gives: a step says what it
did to the projects of its day (``core/project_migration.py``, whose
``migrate_0_16_to_0_17`` calls this and says what the whole step does). A list
that is missing, null or not the shape it should be reads as nothing.
"""

import math

from openavc.core.box_migration import number_0_17

_DECIMALS_0_17 = (0, 20)
_PANEL_SETTINGS_0_17 = {
    "page_transition_duration": (50, 1000),
    "element_stagger_ms": (10, 200),
    "idle_timeout_seconds": (0, 86400),
}
# The panel drew a 0 in these as its default (200 and 30 ms).
_ZERO_IS_DEFAULT_0_17 = ("page_transition_duration", "element_stagger_ms")

# What the lock screen's box takes: these digits, and this many.
_PIN_DIGITS_0_17 = "0123456789"
_PIN_LENGTH_0_17 = 6

# A trigger's times in seconds; each, and a macro's cooldown, is this or more.
_TRIGGER_SECONDS_0_17 = ("delay_seconds", "debounce_seconds", "cooldown_seconds")
_SECONDS_MIN_0_17 = 0


def repair_bounds_0_17(data: dict, elements: list[dict]) -> None:
    """The project's bounded numbers, repaired in place; ``elements`` are its
    page and master elements, as the step reads them."""
    for holder in elements + _dicts_0_17(data.get("monitors")):
        _clamp_0_17(holder, "display_decimals", _DECIMALS_0_17)
    ui = data.get("ui")
    _clamp_settings_0_17(ui.get("settings") if isinstance(ui, dict) else None)
    for macro in _dicts_0_17(data.get("macros")):
        _repair_macro_0_17(macro)


def _dicts_0_17(items) -> list[dict]:
    """The objects in a list as written; anything but a list reads as none."""
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _whole_number_0_17(value) -> int | None:
    """A count as a release could have saved it: a whole number, which the
    schema has always taken (a float with no fraction included). Anything else
    the schema refused before this step and still does, so it is left alone."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clamp_0_17(holder: dict, field: str, bounds: tuple[int, int]) -> None:
    value = _whole_number_0_17(holder.get(field))
    if value == 0 and field in _ZERO_IS_DEFAULT_0_17:
        holder.pop(field)
    elif value is not None:
        holder[field] = max(bounds[0], min(bounds[1], value))


def _clamp_settings_0_17(settings) -> None:
    if isinstance(settings, dict):
        for field, bounds in _PANEL_SETTINGS_0_17.items():
            _clamp_0_17(settings, field, bounds)
        _repair_pin_0_17(settings)


def _repair_pin_0_17(settings: dict) -> None:
    """The PIN as the lock screen's box holds it once typed. Anything but
    text the schema refused before this step, and still does."""
    pin = settings.get("lock_code")
    if isinstance(pin, str):
        typed = "".join(c for c in pin if c in _PIN_DIGITS_0_17)
        settings["lock_code"] = typed[:_PIN_LENGTH_0_17]


def _repair_macro_0_17(macro: dict) -> None:
    _time_inside_0_17(macro, "cooldown_seconds")
    for trigger in _dicts_0_17(macro.get("triggers")):
        _repair_trigger_0_17(trigger)


def _repair_trigger_0_17(trigger: dict) -> None:
    for field in _TRIGGER_SECONDS_0_17:
        if _time_inside_0_17(trigger, field) == math.inf:
            trigger["enabled"] = False


def _time_inside_0_17(holder: dict, field: str) -> float | None:
    """``holder[field]`` brought to 0 or more, or removed when it is no finite
    number; the number it held, or None when it held none."""
    number = number_0_17(holder.get(field))
    if number is None:
        return None
    if not math.isfinite(number):
        holder.pop(field)
    elif number < _SECONDS_MIN_0_17:
        holder[field] = _SECONDS_MIN_0_17
    return number
