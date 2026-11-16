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

Each value rewritten is a note for the Programmer's notice, worded with the
field's name where the Programmer shows it. The PIN's note says what happened
to it and never holds a digit of it.
"""

import math

from openavc.core.box_migration import element_places_0_17, number_0_17, say_number_0_17
from openavc.core.project_update import MigrationNotes

_DECIMALS_0_17 = (0, 20)
_PANEL_SETTINGS_0_17 = {
    "page_transition_duration": (50, 1000),
    "element_stagger_ms": (10, 200),
    "idle_timeout_seconds": (0, 86400),
}
# What Panel Settings calls each, and its unit, for a note.
_SETTING_NAMES_0_17 = {
    "page_transition_duration": ("Transition Duration", "ms"),
    "element_stagger_ms": ("Stagger Delay", "ms"),
    "idle_timeout_seconds": ("Idle Timeout", "seconds"),
}
# The panel drew a 0 in these as its default (200 and 30 ms).
_ZERO_IS_DEFAULT_0_17 = ("page_transition_duration", "element_stagger_ms")

# What the lock screen's box takes: these digits, and this many.
_PIN_DIGITS_0_17 = "0123456789"
_PIN_LENGTH_0_17 = 6

# A trigger's times in seconds; each, and a macro's cooldown, is this or more.
_TRIGGER_SECONDS_0_17 = ("delay_seconds", "debounce_seconds", "cooldown_seconds")
_SECONDS_MIN_0_17 = 0


def repair_bounds_0_17(data: dict, elements: list[dict], notes: MigrationNotes) -> None:
    """The project's bounded numbers, repaired in place; ``elements`` are its
    page and master elements, as the step reads them."""
    places = element_places_0_17(data.get("ui"))
    for el in elements:
        prefix, page_id = places.get(id(el), ("", ""))
        element_id = str(el.get("id", ""))
        _Noted(notes, f"{prefix} > {element_id}", page_id, element_id).decimals(el)
    for monitor in _dicts_0_17(data.get("monitors")):
        _Noted(notes, f"Monitored Readings > {monitor.get('key', '')}").decimals(monitor)
    ui = data.get("ui")
    _clamp_settings_0_17(ui.get("settings") if isinstance(ui, dict) else None, notes)
    for macro in _dicts_0_17(data.get("macros")):
        _repair_macro_0_17(macro, notes)


class _Noted:
    """One holder's repairs, each said at its place."""

    def __init__(self, notes: MigrationNotes, location: str, page_id: str = "", element_id: str = ""):
        self.notes = notes
        self.at = {"page_id": page_id, "element_id": element_id}
        self.location = location

    def changed(self, message: str) -> None:
        self.notes.changed(message, self.location, **self.at)

    def decimals(self, holder: dict) -> None:
        old, new = _clamp_0_17(holder, "display_decimals", _DECIMALS_0_17)
        if old != new:
            self.changed(f"Shown decimals changed from {old} to {new}. A readout shows 0 to 20.")


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


def _clamp_0_17(holder: dict, field: str, bounds: tuple[int, int]) -> tuple[int | None, int | None]:
    """The count before and after; None for one left alone or removed."""
    value = _whole_number_0_17(holder.get(field))
    if value == 0 and field in _ZERO_IS_DEFAULT_0_17:
        holder.pop(field)
        return value, None
    if value is None:
        return None, None
    holder[field] = max(bounds[0], min(bounds[1], value))
    return value, holder[field]


def _clamp_settings_0_17(settings, notes: MigrationNotes) -> None:
    if not isinstance(settings, dict):
        return
    noted = _Noted(notes, "Panel Settings")
    for field, bounds in _PANEL_SETTINGS_0_17.items():
        old, new = _clamp_0_17(settings, field, bounds)
        if old == new:
            continue
        name, unit = _SETTING_NAMES_0_17[field]
        if new is None:
            noted.changed(f"{name} of 0 cleared, so the panel uses its default, as it did before.")
        else:
            noted.changed(f"{name} changed from {old} to {new} {unit}, the range Panel Settings offers.")
    _repair_pin_0_17(settings, noted)


def _repair_pin_0_17(settings: dict, noted: _Noted) -> None:
    """The PIN as the lock screen's box holds it once typed. Anything but
    text the schema refused before this step, and still does."""
    pin = settings.get("lock_code")
    if not isinstance(pin, str):
        return
    typed = "".join(c for c in pin if c in _PIN_DIGITS_0_17)[:_PIN_LENGTH_0_17]
    settings["lock_code"] = typed
    if typed == pin:
        return
    if typed:
        noted.changed(
            "Lock Code (PIN) changed to the PIN the panel's lock screen takes: its digits only, at most six."
        )
    else:
        noted.changed(
            "Lock Code (PIN) cleared: it held no digits the panel's lock screen takes, "
            "so the panel no longer locks. Set a new PIN to lock it."
        )


def _repair_macro_0_17(macro: dict, notes: MigrationNotes) -> None:
    name = macro.get("name") or macro.get("id", "")
    old = _time_inside_0_17(macro, "cooldown_seconds")
    if old is not None:
        _say_time_0_17(_Noted(notes, f"Macros > {name}"), "Cooldown", old)
    for trigger in _dicts_0_17(macro.get("triggers")):
        _repair_trigger_0_17(trigger, _Noted(notes, f"Macros > {name} > {trigger.get('id', '')}"))


# A trigger's times as the trigger editor names them.
_TRIGGER_FIELD_NAMES_0_17 = {
    "delay_seconds": "Delay",
    "debounce_seconds": "Debounce",
    "cooldown_seconds": "Cooldown",
}


def _repair_trigger_0_17(trigger: dict, noted: _Noted) -> None:
    for field in _TRIGGER_SECONDS_0_17:
        was_on = trigger.get("enabled", True) is not False
        old = _time_inside_0_17(trigger, field)
        if old == math.inf:
            trigger["enabled"] = False
            name = _TRIGGER_FIELD_NAMES_0_17[field]
            noted.changed(
                _ENDLESS_TRIGGER_0_17[field] if was_on
                else f"{name} cleared: it never ended. The trigger was already off."
            )
        elif old is not None:
            _say_time_0_17(noted, _TRIGGER_FIELD_NAMES_0_17[field], old)


_ENDLESS_TRIGGER_0_17 = {
    "delay_seconds": "Trigger turned off: its Delay never ended, so it never fired. Set a Delay and turn it on to use it.",
    "debounce_seconds": "Trigger turned off: its Debounce never ended, so it never fired. Set a Debounce and turn it on to use it.",
    "cooldown_seconds": "Trigger turned off: its Cooldown never ended, so it fired once after each restart. Set a Cooldown and turn it on to use it.",
}


def _say_time_0_17(noted: _Noted, name: str, old: float) -> None:
    """The note for a time brought to 0 or more (``old`` is what it held)."""
    if old == math.inf:
        noted.changed(f"{name} removed: it never ended, so the macro ran once after each restart.")
    elif not math.isfinite(old):
        noted.changed(f"{name} was not a number, so it is now 0 seconds, which is how it ran.")
    elif old < _SECONDS_MIN_0_17:
        noted.changed(f"{name} changed from {say_number_0_17(old)} to 0 seconds, which is how it ran.")


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
