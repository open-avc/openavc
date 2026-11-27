"""THE ``$``-reference resolver, THE reader of a condition's key, and the
firing context's field table.

Three rules that share one question, what a name means at run time:

- ``referenced_key``: whether a whole value is a ``$`` reference, and the key
  it names, asked by ``resolve_ref`` and by a condition's value
  (``condition_eval.read_value``); ``resolve_ref`` / ``resolve_in_text``: what a
  ``$`` reference in a value stands for (below).
- ``read_operand``: what a condition's key reads, for every site that judges
  one -- a step's Skip If, a Conditional, a Wait Until, a trigger's guard and a
  state-change trigger's own test. A ``trigger.<field>`` key is the firing
  context's field (none at a Wait Until, which is handed no context), and any
  other key is the state store's value.
- ``FIRING_CONTEXTS``: the fields each producer puts in that context, which a
  ``trigger.<field>`` key and a ``$trigger.<field>`` reference read, and what
  each holds. The IDE's picker and trigger editor list them from it.

A ``$``-prefixed string is a runtime value reference. The same sigil is used by
two authoring surfaces — UI Builder bindings (which deliver a UI-event value)
and macro/trigger steps (which may carry the firing trigger's context) — so one
resolver keeps every token's meaning consistent across both, instead of two
separate resolvers that silently disagree (``$value`` meaning the touched value
on a binding but ``state.get("value")`` in a macro).

Resolution order (first match wins):

1. **Event context** — ``$value`` / ``$input`` / ``$output`` / ``$mute`` from the
   UI event that fired a binding. When an ``event_ctx`` is supplied these tokens
   always resolve from it, never from the state store, so ``$value`` can't fall
   through to a stray ``state.get("value")``.
2. **Trigger context** — ``$trigger.<field>`` from the firing trigger's payload
   or state-change snapshot. A miss returns ``None`` *silently*: a macro run
   directly (no trigger) legitimately has no trigger context, so "empty when not
   fired by a trigger" is documented, expected behavior — not a mistake.
3. **State store** — any other ``$<state_key>``. A key that is genuinely absent
   is almost always a typo (``$var.volum``), so it logs a warning and resolves
   to ``None``. A key that exists but holds ``None``/``False`` is a real value
   and does **not** warn — which is why the state branch checks ``has`` before
   ``get`` instead of relying on ``get`` returning ``None``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, NamedTuple

log = logging.getLogger(__name__)

#: The namespace a condition key or a ``$`` reference reads the firing
#: context through: ``trigger.new_value`` is the context's ``new_value``.
TRIGGER_PREFIX = "trigger."

class ContextField(NamedTuple):
    """One field of a firing context: its name, read as ``trigger.<name>``,
    and what it holds, in the words the IDE shows beside it."""

    name: str
    description: str


class FiringContext(NamedTuple):
    """What one producer of a run puts in the run's firing context."""

    #: What ran the macro, as the IDE and the docs name it.
    name: str
    fields: tuple[ContextField, ...]
    #: The context also holds every field of the event's own payload, under
    #: the names its sender gave them.
    carries_payload: bool = False


_TRIGGER_TYPE = ContextField("trigger_type", 'The kind of trigger, "schedule" or "startup"')

#: The fields each producer of a run puts in its firing context, keyed by the
#: producer: a trigger's own type, and ``press`` for a control's macro action.
#: A field's name has one description wherever it appears. Fire now in the
#: trigger list hands the fields of its trigger's type that the trigger alone
#: can supply (``core/trigger_context.fire_now``). A child macro run by a Run
#: Macro step reads its parent's context; a run started any other way (the
#: Run button, the API, a script, a plugin, the AI assistant) has none.
#: ``python -m openavc.core.firing_context_gen`` renders this into the IDE,
#: and ``tests/test_firing_context_fields.py`` holds every producer to it.
FIRING_CONTEXTS: dict[str, FiringContext] = {
    "state_change": FiringContext("A state change trigger", (
        ContextField("key", "The state key that changed"),
        ContextField("old_value", "Its value before the change"),
        ContextField("new_value", "Its value after the change"),
    )),
    "event": FiringContext("An event trigger", (
        ContextField("event", "The name of the event that fired"),
    ), carries_payload=True),
    "schedule": FiringContext("A schedule trigger", (
        _TRIGGER_TYPE,
        ContextField("cron", "The trigger's schedule, as a cron expression"),
    )),
    "startup": FiringContext("A startup trigger", (_TRIGGER_TYPE,)),
    # The press's own values, then the control's tag and id
    # (``core/ui_events.py``).
    "press": FiringContext("A control that runs the macro", (
        ContextField(
            "value",
            "The value the user just set: a slider's position, a select's "
            "choice, the digits entered",
        ),
        ContextField("input", "The routed input number, on a matrix route"),
        ContextField("output", "The routed or muted output number, on a matrix route or mute"),
        ContextField("mute", "true when muting, false when unmuting, on a matrix mute"),
        ContextField("tag", "The Tag you gave the control in the UI Builder"),
        ContextField("element", "The control's ID"),
    )),
}


def read_operand(
    key: str,
    *,
    state: Any,
    context: Mapping[str, Any] | None = None,
) -> Any:
    """What a condition's ``key`` reads at the site judging it.

    A ``trigger.<field>`` key reads the firing context's field; a field the
    context lacks, and every field of a run with no context (a Wait Until's),
    is a value that has not arrived (None). Any other key is the state store's
    value, None when it has none.

    A key is read as written: a leading ``$`` is part of the key here, not a
    reference. Nothing is logged for a key with no value, since a Wait Until
    reads its key again on every change.
    """
    if key.startswith(TRIGGER_PREFIX):
        return (context or {}).get(key[len(TRIGGER_PREFIX):])
    return state.get(key)

# A ``$`` reference embedded in prose. Dots are part of the key
# (``$var.room_name``), but a trailing one is sentence punctuation, so it is
# left alone -- "$var.room is down." must not look up ``var.room.``.
_EMBEDDED_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)")


def referenced_key(value: Any) -> str | None:
    """The key a whole value names (``$var.wanted`` names ``var.wanted``), or
    None for a value used as written: anything but text that starts with
    ``$``. A ``$`` alone names the empty key, which never has a value.

    The panel's ``modules/compare.js`` and the Programmer's
    ``conditionEval.ts`` read a condition's value the same way
    (``referencedKey``), over ``tests/fixtures/condition_reference_cases.json``.
    """
    return value[1:] if isinstance(value, str) and value.startswith("$") else None


def resolve_ref(
    value: Any,
    *,
    state: Any,
    event_ctx: dict[str, Any] | None = None,
    trigger_ctx: dict[str, Any] | None = None,
) -> Any:
    """Resolve a ``$``-reference to its current value.

    A value that names no key (``referenced_key``) passes through unchanged.
    See the module docstring for the namespace order and the precise rule for
    when an unknown reference warns.
    """
    ref = referenced_key(value)
    if ref is None:
        return value
    if event_ctx is not None and ref in event_ctx:
        return event_ctx[ref]
    if ref.startswith("trigger."):
        return (trigger_ctx or {}).get(ref[len("trigger."):])
    if state.has(ref):
        return state.get(ref)
    log.warning("unknown state reference '$%s' resolved to None", ref)
    return None


def resolve_in_text(
    text: Any,
    *,
    state: Any,
    event_ctx: dict[str, Any] | None = None,
    trigger_ctx: dict[str, Any] | None = None,
) -> str:
    """Resolve every ``$`` reference *inside* a string, keeping the rest.

    ``resolve_ref`` above is whole-value: a parameter is either a literal or a
    reference, because it is a value on its way to a device and half a value
    means nothing. This one is for the other case -- a **sentence on its way to
    a person** -- where the references are the interesting part and the words
    around them are what make it readable. "$var.room projector failed to power
    on $var.attempts times" is the whole reason the help primitive carries a
    message at all.

    Kept here rather than at the call site because this module owns what ``$``
    means, and a second interpretation of the sigil living somewhere else is
    exactly the drift the module docstring exists to prevent. Deliberately NOT
    used for macro parameters: widening those would change what an existing
    project does.

    An unknown reference resolves to an empty string and warns once, like the
    whole-value path -- a typo should leave a gap, not print ``$var.volum`` on
    a wall panel.
    """
    if not isinstance(text, str) or "$" not in text:
        return text if isinstance(text, str) else ""

    def _one(match: re.Match[str]) -> str:
        value = resolve_ref(
            f"${match.group(1)}", state=state,
            event_ctx=event_ctx, trigger_ctx=trigger_ctx,
        )
        return "" if value is None else str(value)

    return _EMBEDDED_REF.sub(_one, text)


def embedded_refs(text: Any) -> list[str]:
    """The keys ``resolve_in_text`` would look up in ``text``, in order.

    For a caller asking whether a sentence uses a key -- a variable named in a
    help message is a use of that variable -- without a second reading of
    where a reference ends.
    """
    if not isinstance(text, str):
        return []
    return _EMBEDDED_REF.findall(text)
