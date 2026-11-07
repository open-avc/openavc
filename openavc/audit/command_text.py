"""What one command did, in words.

The sentence the timeline, the wizard, the summary and the driver test report
all say for a command the audit sent. Every function here reads the command as
its record holds it (``CommandPass._trial_view``, and ``report.json``'s
``trials``), never the live objects, so a saved report reads the same way.

:func:`trial_sentence` is the parts in ``_PARTS``, in that order, joined with
"; ": each says one thing, and nothing when there is nothing to say. A
command whose parts are all silent gets one sentence about what moved anyway
and what came back.

:func:`params_text` is written the same way by the wizard's ``paramsText``:
change one, change the other.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.param_labels import param_label
from openavc.utils.boolean_words import value_text

# The person's answers to "Did it happen?", in words for the timeline.
ANSWERS = {
    "yes": "The person said the device did it.",
    "no": "The person said the device did not do it.",
    "partly": "The person said the device partly did it.",
    "cant_tell": "The person said they could not tell.",
}

# The values a command moved that its sentence names; the rest are counted.
MOVED_IN_SENTENCE = 3

EFFECT_TEXT = {
    "confirmed": "{state} is now {expected}, as the driver says it should be",
    "already": "{state} was already {expected}, so the audit cannot tell whether this command set it",
    "different": "{state} changed to {value}, not {expected}",
    "unchanged": "{state} is still {value}, not {expected}",
    "not_reported": "the device has not reported {state}",
    "no_value": "{state} was not checked: it depends on a value this command was not given",
}


def effect_sentence(effect: dict[str, Any]) -> str:
    return EFFECT_TEXT[effect["outcome"]].format(
        state=effect.get("label") or effect["state"], expected=value_text(effect["expected"]),
        value=value_text(effect.get("value")),
    )


def trial_sentence(trial: dict[str, Any]) -> str:
    """What one command did, as the timeline, the wizard and the summary say
    it. Takes the trial as its record holds it (``report.json`` included)."""
    if trial.get("error"):
        return f"not accepted: {trial['error']}"
    parts = [text for part in _PARTS for text in part(trial)]
    return "; ".join(parts or [_nothing_happened(trial)]) + "."


def trial_outcome(trial: dict[str, Any]) -> str:
    """One command's outcome as a sentence that stands on its own: the
    summary its record keeps, else :func:`trial_sentence`, capitalized. The
    summary's command rows and the driver test report both say it so."""
    text = trial.get("summary") or trial_sentence(trial)
    return text[:1].upper() + text[1:]


def params_text(params: dict[str, Any], defs: Any = None) -> str:
    """A command's parameters as a line reads them, each named as its field
    is (``param_labels.param_label``): " (Level 40, Input ID hdmi1)". The
    wizard's ``paramsText`` writes the same line."""
    if not params:
        return ""
    defs = defs if isinstance(defs, dict) else {}
    return " (" + ", ".join(f"{param_label(k, defs.get(k))} {value_text(v)}" for k, v in params.items()) + ")"


# -- the parts of the sentence, in the order it says them ----------------------


def _refused(trial: dict[str, Any]) -> list[str]:
    refusals = trial.get("refusals") or {}
    if not (refusals.get("device_errors") or refusals.get("last_error")):
        return []
    said = refusals.get("last_error") or ""
    return [f"the device refused it{': ' + str(said) if said else ''}"]


def _sent_nothing(trial: dict[str, Any]) -> list[str]:
    if not trial.get("sent_nothing"):
        return []
    return ["the driver said it succeeded, but nothing was sent"]


def _effects(trial: dict[str, Any]) -> list[str]:
    return [effect_sentence(e) for e in trial.get("effects") or []]


def _query(trial: dict[str, Any]) -> list[str]:
    query = trial.get("query")
    if not query:
        return []
    name = query.get("label") or query["state"]
    if query["outcome"] == "reported":
        return [f"it reported {name} as {value_text(query['value'])}"]
    if query["outcome"] == "no_reply":
        return ["nothing came back"]
    return [f"a reply came, but {name} was not reported"]


def _moved(trial: dict[str, Any]) -> list[str]:
    """What else it moved, less what was already changing and what the
    declared effect or the query already said."""
    said = _already_said(trial)
    moved = [m for m in trial.get("moved") or [] if _left_moved(m, said)]
    if not moved:
        return []
    texts = [
        f"{m['label']} went from {_reads(m['first'])} to {_reads(m['last'])}"
        for m in moved[:MOVED_IN_SENTENCE]
    ]
    more = len(moved) - MOVED_IN_SENTENCE
    if more > 0:
        texts.append(f"{more} more {'value' if more == 1 else 'values'} changed")
    return [", ".join(texts)]


def _already_said(trial: dict[str, Any]) -> set[Any]:
    """The values the declared effect or the query already names."""
    said = {e.get("state_key") for e in trial.get("effects") or []}
    if trial.get("query"):
        said.add(trial["query"].get("state_key"))
    return said


def _left_moved(move: dict[str, Any], said: set[Any]) -> bool:
    """A value the command moved and left moved, that no other part names."""
    if move.get("already_moving") or move.get("went_back"):
        return False
    return move.get("key") not in said


def _drops(trial: dict[str, Any]) -> list[str]:
    return [_drops_text(trial["drops"])] if trial.get("drops") else []


def _restart(trial: dict[str, Any]) -> list[str]:
    restart = trial.get("restart")
    if not restart:
        return []
    if restart["back_after"] is not None:
        return [
            f"the device came back {restart['back_after']} s after the command "
            f"(the driver says up to {restart['declared_seconds']} s)"
        ]
    if restart["went_away_after"] is not None:
        return ["the device went away and had not come back when the audit stopped watching"]
    return ["the connection stayed up"]


def _later(trial: dict[str, Any]) -> list[str]:
    refusals = trial.get("refusals") or {}
    return [_later_refusal_text(later) for later in refusals.get("later") or []]


def _unmatched(trial: dict[str, Any]) -> list[str]:
    count = (trial.get("refusals") or {}).get("unmatched")
    if not count:
        return []
    return [
        f"{count} {'reply' if count == 1 else 'replies'} while it was watched matched none of "
        "the driver's rules"
    ]


_PARTS = (
    _refused, _sent_nothing, _effects, _query, _moved, _drops, _restart, _later, _unmatched,
)


def _nothing_happened(trial: dict[str, Any]) -> str:
    """The sentence for a command no part had anything to say about."""
    received = (trial.get("traffic") or {}).get("received", 0)
    what = _what_moved_anyway(trial.get("moved") or [])
    if received:
        return f"{what}; the device sent {received} {'reply' if received == 1 else 'replies'}"
    return f"{what}, and nothing came back"


def _what_moved_anyway(moved: list[dict[str, Any]]) -> str:
    if any(m.get("already_moving") for m in moved):
        return "only values that were already changing moved"
    if any(m.get("went_back") for m in moved):
        return "values changed and went back to where they were"
    return "no status value changed"


def _drops_text(drops: list[dict[str, Any]]) -> str:
    """Each drop in a command's window, the later ones timed from the
    reconnect before them (a device rebooting can reset that one too)."""
    texts = []
    for i, drop in enumerate(drops):
        if i == 0:
            head = f"the connection dropped {drop['after']} s after it was sent"
        else:
            prev = drops[i - 1]
            since = max(round(drop["after"] - prev["after"] - (prev["back_after"] or 0), 1), 0.0)
            head = f"it dropped again {since} s after that"
        tail = (
            f" and the driver reconnected {drop['back_after']} s later"
            if drop["back_after"] is not None
            else " and had not come back when the audit stopped watching"
        )
        texts.append(head + tail)
    return "; ".join(texts)


def _later_refusal_text(later: dict[str, Any]) -> str:
    """A refusal of a request the driver sent on its own inside a command's
    window, said apart from the command."""
    count = later.get("count", 1)
    when = (
        f"{count} times, the first {later['after']} s later" if count > 1
        else f"{later['after']} s later"
    )
    request = f" ({later['request']})" if later.get("request") else ""
    said = f": {later['error']}" if later.get("error") else ""
    return f"the device refused a request the driver sent on its own {when}{request}{said}"


def _reads(value: Any) -> str:
    """A value as a sentence says it ("not reported" for none)."""
    return "not reported" if value is None else value_text(value)
