"""Shared ``$``-reference resolver for command/action parameter values.

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
from typing import Any

log = logging.getLogger(__name__)

# A ``$`` reference embedded in prose. Dots are part of the key
# (``$var.room_name``), but a trailing one is sentence punctuation, so it is
# left alone -- "$var.room is down." must not look up ``var.room.``.
_EMBEDDED_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)")


def resolve_ref(
    value: Any,
    *,
    state: Any,
    event_ctx: dict[str, Any] | None = None,
    trigger_ctx: dict[str, Any] | None = None,
) -> Any:
    """Resolve a ``$``-reference to its current value.

    Non-strings and strings that don't start with ``$`` pass through unchanged.
    See the module docstring for the namespace order and the precise rule for
    when an unknown reference warns.
    """
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    ref = value[1:]
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
