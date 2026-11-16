"""
The project format's 0.17.0 step, its repair of saved times in seconds: a
trigger's delay, debounce and cooldown, and a macro's own cooldown, which the
schema took at 0.17.0 as a finite number, 0 or more.

A negative one is brought to 0: the engines waited no time for it, as for 0.
One that is no finite number is removed, so it loads as 0. The engines read a
NaN and a negative infinity as 0 already. A positive infinity held a trigger
back for good: a delay or debounce that never ended, a cooldown that never
lapsed after the trigger's first run. That trigger is turned off as well, so
it still does not fire. A macro's infinite cooldown held it to one start a
server run; a macro has no off switch, so it loads with no cooldown. The file
writer put such a value down as a bare ``Infinity``, which a browser cannot
read, so the Programmer could not open a project holding one.

A time is read as the schema read it before this step (``number_0_17``: a
number, true or false, or text that reads as one); anything else the schema
refused before and still does, so it is left alone. The numbers are the
step's own, as they stood at 0.17.0, not read from the project models: a step
says what it did to the projects of its day (``core/project_migration.py``,
whose ``migrate_0_16_to_0_17`` calls this and says what the whole step does).
A list that is missing, null or not the shape it should be reads as nothing.
"""

import math

from openavc.core.box_migration import number_0_17

# A trigger's times in seconds; each, and a macro's cooldown, is this or more.
_TRIGGER_SECONDS_0_17 = ("delay_seconds", "debounce_seconds", "cooldown_seconds")
_SECONDS_MIN_0_17 = 0


def repair_seconds_0_17(data: dict) -> None:
    """Every macro's cooldown and every time of its triggers, repaired in place."""
    for macro in _dicts_0_17(data.get("macros")):
        _time_inside_0_17(macro, "cooldown_seconds")
        for trigger in _dicts_0_17(macro.get("triggers")):
            _repair_trigger_0_17(trigger)


def _dicts_0_17(items) -> list[dict]:
    """The objects in a list as written; anything but a list reads as none."""
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


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
