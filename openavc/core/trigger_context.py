"""THE context a trigger hands the macro it runs.

A real fire's is built here for each trigger type (``state_change_context``,
``event_context``, ``schedule_context``, ``startup_context``), and so is Fire
now's (``fire_now``), with the sentence that says what it handed. Which
fields each context holds is ``value_resolver.FIRING_CONTEXTS``, and
``tests/test_firing_context_fields.py`` fires every producer against it.

Fire now has no change and no event behind it, so it hands what the trigger
alone can supply:

- a startup or schedule trigger: what its real fire hands, exactly;
- a State Change trigger on one key: ``key``, and the key's current value as
  ``new_value`` only when that value passes the trigger's own test, since
  every real fire's does; failing that, for ``eq``, the value the trigger
  fires on; failing that, no ``new_value``, and the sentence says why.
  ``old_value`` is never known;
- a State Change trigger on a pattern, and an event trigger: nothing. Which
  key changed, and what an event carried, only a real fire knows.

The REST door and the AI's test tool answer with the sentence
(``FireNow.fired_with``), and the Programmer's toast shows it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from openavc.core.condition_eval import (
    NOT_ARRIVED,
    STATE_TRIGGER,
    UnknownOperator,
    canonical_operator,
    judge,
    read_value,
    state_condition,
)
from openavc.core.project_loader import TriggerConfig, reads_as
from openavc.core.state_store import GLOB_CHARS
from openavc.core.value_resolver import TRIGGER_PREFIX, read_operand
from openavc.utils.boolean_words import value_text

#: What a State Change trigger's own test judges: the value the change
#: carries, read from the context the change fires with.
CHANGED_VALUE = f"{TRIGGER_PREFIX}new_value"


def state_change_context(key: str, old_value: Any, new_value: Any) -> dict[str, Any]:
    """What a State Change trigger hands its macro: the key that changed and
    its value before and after."""
    return {"key": key, "old_value": old_value, "new_value": new_value}


def event_context(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """What an Event trigger hands its macro: the event's name, then every
    field of its payload under the name its sender gave it."""
    return {"event": event, **payload}


def schedule_context(cron: str) -> dict[str, Any]:
    """What a Schedule trigger hands its macro."""
    return {"trigger_type": "schedule", "cron": cron}


def startup_context() -> dict[str, Any]:
    """What a Startup trigger hands its macro."""
    return {"trigger_type": "startup"}


def passes_own_test(trigger: Mapping[str, Any], context: Mapping[str, Any], state: Any) -> bool:
    """Whether a State Change trigger's own test holds for the change in
    ``context`` (``condition_eval.judge`` at ``STATE_TRIGGER``). An operator
    nobody knows raises ``UnknownOperator``."""
    own = state_condition(trigger, CHANGED_VALUE)
    return judge(own, lambda key: read_operand(key, state=state, context=context), STATE_TRIGGER)


class FireNow(NamedTuple):
    """What Fire now hands a trigger's macro, and the sentence saying so."""

    context: dict[str, Any]
    fired_with: str


NO_EVENT_DATA = "Fired without event data."
_PATTERN = (
    f"{NO_EVENT_DATA} This trigger watches a pattern, so key, old_value and new_value are unknown."
)
_OLD_VALUE_UNKNOWN = "old_value is unknown."


def fire_now(trigger: Mapping[str, Any], state: Any) -> FireNow:
    """What Fire now hands ``trigger``'s macro, read from ``state`` now."""
    # A type this release does not know reads as none, as it does at start.
    ttype = reads_as(TriggerConfig, "type", trigger.get("type"))
    if ttype == "startup":
        return _exactly(startup_context())
    if ttype == "schedule":
        return _exactly(schedule_context(trigger.get("cron", "")))
    if ttype == "state_change":
        return _state_change(trigger, state)
    return FireNow({}, NO_EVENT_DATA)


def _shown(value: Any) -> str:
    """A value as the sentence writes it; empty text shows as ``""``."""
    return '""' if value == "" else value_text(value)


def _exactly(context: dict[str, Any]) -> FireNow:
    named = [f"{name} = {_shown(value)}" for name, value in context.items() if value != ""]
    listed = named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"
    return FireNow(context, f"Fired with {listed}.")


def _state_change(trigger: Mapping[str, Any], state: Any) -> FireNow:
    key = trigger.get("state_key") or ""
    if not isinstance(key, str) or not key:
        return FireNow({}, NO_EVENT_DATA)
    if any(c in key for c in GLOB_CHARS):
        return FireNow({}, _PATTERN)
    context: dict[str, Any] = {"key": key}
    current = state.get(key)
    if current is not None and _would_fire(trigger, {**context, "new_value": current}, state):
        return FireNow(
            {**context, "new_value": current},
            f"Fired with new_value = {_shown(current)}, the current value. {_OLD_VALUE_UNKNOWN}",
        )
    why = (
        f"{key} has no value yet" if current is None
        else f"The current value ({_shown(current)}) would not fire this trigger"
    )
    target = _fires_on(trigger, context, state)
    if target is None:
        return FireNow(context, f"{why}; fired without new_value. {_OLD_VALUE_UNKNOWN}")
    return FireNow(
        {**context, "new_value": target},
        f"{why}; fired with new_value = {_shown(target)}, the value it fires on. "
        f"{_OLD_VALUE_UNKNOWN}",
    )


def _would_fire(trigger: Mapping[str, Any], context: dict[str, Any], state: Any) -> bool:
    try:
        return passes_own_test(trigger, context, state)
    except UnknownOperator:
        return False


def _fires_on(trigger: Mapping[str, Any], context: dict[str, Any], state: Any) -> Any:
    """The one value an ``eq`` trigger fires on, read as its test reads it
    (a value naming a key reads that key), or None for any other operator
    and for a value that reads as nothing."""
    if canonical_operator(trigger.get("state_operator")) != "eq":
        return None
    target = read_value(
        trigger.get("state_value"),
        lambda key: read_operand(key, state=state, context=context),
    )
    return None if target is NOT_ARRIVED else target
