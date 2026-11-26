"""
THE comparison of a reading with what an author wrote.

Asked by every reader of a condition on the server: a macro's ``skip_if`` and
conditional steps, a trigger's guards, a panel press the server resolves
(``ui_events.resolve_press``), a select's value map, and both page reviews.
``match_key`` is also every driver value map's lookup (a reply's, a device
setting's and a command parameter's), asked by the driver runtime and the
simulator through ``drivers/compiled_protocol.map_entry``; this module stays
standard-library-only (and ``core.monitors``, ``drivers.truth_words`` and
``core.value_resolver``) for them, since the simulator and the driver
validator import it.
The rule, in the order it is applied:

- a reading that has not arrived (None) is never equal; what it does to
  not-equal is the reader's mode (``MODES``, below);
- a boolean on either side compares both as booleans
  (``drivers/truth_words``: the words that mean true and false, trimmed and
  apart from case, and a number by value, non-zero true);
- values of different types compare as numbers when both read as one
  (``core/monitors.py``'s ``as_number``: trimmed digits, a sign, a point, an
  exponent; never "1_000", "inf" or "nan", which compare as text);
- two words compare apart from case;
- an authored map is read in one order (``match_key``): the exact key, then
  one apart from case, then one the reading equals as a boolean or number;
- a condition's value that names a key (``$var.wanted``) is compared with
  what that key holds, one with no value being a reading that has not
  arrived (``holds``; ``condition_keys`` are the keys a condition reads),
  over ``tests/fixtures/condition_reference_cases.json`` in every copy.

Copied twice, and nowhere else: ``conditionEval.ts`` in the Programmer (the
trigger editor's "Evaluated now", the page reviews) and the panel's
``modules/compare.js``, both reading the words that mean true and false from
files generated from ``drivers/truth_words`` (``types.gen.ts``,
``truth_words.gen.js``). All three run
``tests/fixtures/condition_parity_cases.json`` in both modes
(``tests/test_condition_parity.py``, ``conditionEval.test.ts``,
``tests/test_panel_condition_parity.py``): a change here goes into both
copies and the corpus in the same commit.

Also THE rule for what a macro or trigger condition is where it sits
(``PLACES``: a step's Skip If, a Conditional, a Wait Until, a trigger's guard,
a State Change trigger's own operator): the operator names and their aliases
(``OPERATORS``, ``OPERATOR_ALIASES``) and the trigger's own ``ANY_CHANGE``,
what an operator left out reads as, and what a condition lacks. ``judge`` is
asked by every place that acts on one (``core/macro_steps.py``,
``core/trigger_engine.py``) and ``condition_issues`` by the validator
(``core/macro_validation.py``) for every one it checks. A condition ``judge``
cannot read raises ``UnknownOperator``, and each place still says what that
does there (``UnknownOperator``'s docstring); ``tests/test_condition_sites_pinned.py``
holds each place's answer. The Programmer's operator pickers, its trigger
summaries and ``conditionEval.ts`` read the operator lists and the places from
``conditionOperators.gen.ts``, rendered from here by ``core/condition_gen``
(run it after changing them), and read an operator where it sits as
``written_operator`` does, over ``tests/fixtures/written_operator_cases.json``.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from openavc.core.monitors import as_number
from openavc.core.value_resolver import referenced_key
from openavc.drivers.truth_words import read_truth

#: A reader that acts (a macro, a trigger, a reply gate, an alert) makes no
#: decision on a reading that has not arrived: never equal, never not-equal,
#: so ``power != "on"`` fires no shutdown against a device that has not
#: polled. The default.
DECIDE = "decide"
#: A reader that shows or hides (the panel, the IDE's preview of it, the page
#: reviews) reads one as unequal to every value: a control shown while
#: ``power != "on"`` shows before the projector reports, and one shown while
#: ``power == "on"`` stays hidden.
SURFACE = "surface"
#: Whether a reading that has not arrived holds ``ne``, by mode. The two
#: modes differ there and nowhere else; an authored value that is None never
#: holds in either (``falsy`` is the operator for an unset value).
MODES: dict[str, bool] = {DECIDE: False, SURFACE: True}

#: The operators a condition may name, by their own names, in the order an
#: editor lists them. ``OPERATOR_ALIASES`` maps every other spelling the rule
#: takes onto one of them; anything else, a name in another case included, is
#: no operator.
OPERATORS: tuple[str, ...] = ("eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy")

#: The operators that read no value: whether the reading holds one at all.
#: An editor asks for no value under them.
VALUELESS_OPERATORS: tuple[str, ...] = ("truthy", "falsy")

#: The other spellings of an operator, each mapped to its own name.
OPERATOR_ALIASES: dict[str, str] = {
    "equals": "eq",
    "not_equals": "ne",
    "==": "eq",
    "!=": "ne",
    ">": "gt",
    "<": "lt",
    ">=": "gte",
    "<=": "lte",
    "equal": "eq",
    "not_equal": "ne",
    "greater_than": "gt",
    "less_than": "lt",
    "greater_or_equal": "gte",
    "less_or_equal": "lte",
}


def _coerce_numeric(value: Any) -> float | int | None:
    """A value as a number to compare: ``as_number``'s reading, and a
    boolean as 1 or 0, so ``true < 2`` still orders the way it always has.
    Equality never reaches a boolean here: the boolean branch comes first."""
    if isinstance(value, bool):
        return int(value)
    return _finite_number(value)


def _finite_number(value: Any) -> float | None:
    """``as_number``'s reading, less a float inf or nan, which the panel's
    number reader refuses too: a device's nan is no number to compare."""
    number = as_number(value)
    return number if number is not None and math.isfinite(number) else None


def reads_as_on(value: Any) -> bool:
    """A config value read as an on/off switch.

    A bool, a word or a number says what it says (``drivers/truth_words``:
    "disabled", "00" and "0.0" are off); anything else counts by plain
    truthiness, so an unset field (None) or an empty one is off. The
    integrator's opt-in checkboxes a driver declares (a ``when:`` on a poll
    query or a push registration) read this.
    """
    as_bool = read_truth(value)
    return bool(value) if as_bool is None else as_bool


def canonical_operator(op: Any) -> str | None:
    """``op``'s own name (``OPERATORS``): itself, or the one its alias names.
    None for anything else, a name in another case and one that is not text
    included."""
    if not isinstance(op, str):
        return None
    canonical = OPERATOR_ALIASES.get(op, op)
    return canonical if canonical in OPERATORS else None


class UnknownOperator(ValueError):
    """An operator nobody knows, or one its place does not take: what
    ``eval_operator`` and ``judge`` raise for a condition they cannot judge,
    with the operator as written in ``operator``.

    The place that asked decides what it does: a macro step fails before it
    acts, a trigger's guard reads as not met, and a State Change trigger is
    skipped as ``invalid_condition``.
    """

    def __init__(self, op: Any) -> None:
        super().__init__(
            f"Unknown condition operator: '{op}'. Valid operators: {', '.join(OPERATORS)}"
        )
        self.operator = op


def eval_operator(op: str, actual: Any, target: Any, mode: str = DECIDE) -> bool:
    """Whether ``actual op target`` holds, for a reader in ``mode`` (``MODES``).

    An alias reads as its own name (``canonical_operator``); any other
    operator raises ``UnknownOperator``.
    """
    canonical = canonical_operator(op)
    if canonical is None:
        raise UnknownOperator(op)
    if canonical in ("eq", "ne"):
        return _equality(canonical, actual, target, mode)
    if canonical in _ORDERINGS:
        return _ordering(_ORDERINGS[canonical], actual, target)
    return bool(actual) == (canonical == "truthy")


_ORDERINGS: dict[str, Callable[[Any, Any], bool]] = {
    "gt": operator.gt, "lt": operator.lt, "gte": operator.ge, "lte": operator.le,
}


def _equality(op: str, actual: Any, target: Any, mode: str) -> bool:
    """``eq`` or ``ne``. An authored None compares with nothing (the falsy
    operator is the one for an unset value); a reading that has not arrived
    is never equal, and not-equal only to a reader that shows or hides."""
    if target is None:
        return False
    if actual is None:
        return op == "ne" and MODES[mode]
    return _same(actual, target) == (op == "eq")


def _ordering(compare: Callable[[Any, Any], bool], actual: Any, target: Any) -> bool:
    """An ordering: as numbers when both read as one, else two words as text."""
    if actual is None or target is None:
        return False
    a_num, t_num = _coerce_numeric(actual), _coerce_numeric(target)
    if a_num is not None and t_num is not None:
        return compare(a_num, t_num)
    return isinstance(actual, str) and isinstance(target, str) and compare(actual, target)


def _booleans_equal(actual: Any, target: Any) -> bool | None:
    """Equal as booleans, when one side is a boolean and both read as one; else None."""
    if not (isinstance(actual, bool) or isinstance(target, bool)):
        return None
    a_bool, t_bool = read_truth(actual), read_truth(target)
    return None if a_bool is None or t_bool is None else a_bool == t_bool


def _numbers_equal(actual: Any, target: Any) -> bool | None:
    """Equal as numbers, when the types differ and both read as one; else None."""
    if type(actual) is type(target):
        return None
    a_num, t_num = _coerce_numeric(actual), _coerce_numeric(target)
    return None if a_num is None or t_num is None else a_num == t_num


def _words_equal(actual: Any, target: Any) -> bool:
    """Words apart from case, so `power == "On"` matches a device reporting
    "on" (device casing varies by vendor); anything else as given. Distinct
    tokens stay distinct: "on" and "true" are not made equivalent here."""
    if isinstance(actual, str) and isinstance(target, str):
        return actual.casefold() == target.casefold()
    return bool(actual == target)


def _same(actual: Any, target: Any) -> bool:
    """Whether two values, neither of them None, are equal under the rule:
    as booleans, else as numbers, else as words."""
    for reading in (_booleans_equal, _numbers_equal):
        answer = reading(actual, target)
        if answer is not None:
            return answer
    return _words_equal(actual, target)


def same_value(actual: Any, target: Any) -> bool:
    """Whether a reading equals an authored value: ``eq`` under the rule,
    the same in both modes."""
    return eval_operator("eq", actual, target)


def holds(
    op: str, actual: Any, value: Any, read: Callable[[str], Any], mode: str = DECIDE,
) -> bool:
    """Whether ``actual op value`` holds for a reader in ``mode``, a value
    that names a key (``value_resolver.referenced_key``: ``$var.wanted``)
    compared with what ``read`` reads for that key.

    A key with no value is a reading that has not arrived, on the authored
    side: never equal, never ordered, and not-equal only in the surface mode
    (``MODES``), so ``power != $var.wanted`` makes no decision while
    ``var.wanted`` is unset. A value is read once: text the key holds that
    starts with ``$`` is compared as that text. The panel's
    ``modules/compare.js`` and ``conditionEval.ts`` hold ``holds`` too, over
    ``tests/fixtures/condition_reference_cases.json``.
    """
    named = referenced_key(value)
    if named is None:
        return eval_operator(op, actual, value, mode)
    target = read(named)
    if target is not None:
        return eval_operator(op, actual, target, mode)
    canonical = canonical_operator(op)
    if canonical is None:
        raise UnknownOperator(op)
    if canonical in VALUELESS_OPERATORS:
        return eval_operator(canonical, actual, None, mode)
    return canonical == "ne" and MODES[mode]


def condition_keys(condition: Mapping[str, Any]) -> list[str]:
    """The keys a condition reads: its own, and the one its value names
    (``referenced_key``). A change to either can change its answer, so a Wait
    Until wakes on both and a panel draws a ``visible_when`` again on both."""
    keys = [condition["key"]] if condition.get("key") else []
    named = referenced_key(condition.get("value"))
    if named and named not in keys:
        keys.append(named)
    return keys


def value_words(value: Any) -> str:
    """A value as JavaScript spells it, so a map key reads the same on the
    panel: a boolean is "true", a whole number has no point (1.0 is "1")."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def match_key(keys: Iterable[Any], reading: Any) -> Any:
    """The key of an authored map (a look's states, a value map, an LED's
    colours, a select's or a list's values) that ``reading`` picks, or None.

    The exact key first (the reading's words, ``value_words``), then one
    apart from case, then one the reading equals as a boolean or number
    (``same_value``). Where several keys in one tier match, the first in code
    point order: the author's order cannot decide it, since JavaScript lists a
    map's number-like keys first. A reading that has not arrived picks none.
    """
    if reading is None:
        return None
    candidates = [key for key in keys if key is not None]
    words = value_words(reading)
    tiers = (
        lambda key: value_words(key) == words,
        lambda key: value_words(key).casefold() == words.casefold(),
        lambda key: same_value(reading, key),
    )
    for matches in tiers:
        found = sorted((key for key in candidates if matches(key)), key=value_words)
        if found:
            return found[0]
    return None


# --- a condition where it sits ----------------------------------------------------

#: A State Change trigger's own operator: every change to its key fires it.
ANY_CHANGE = "any"

#: The places a condition sits, each named the way ``judge`` and
#: ``condition_issues`` are asked for it (``PLACES``).
SKIP_IF = "skip_if"
CONDITIONAL = "conditional"
WAIT_UNTIL = "wait_until"
GUARD = "guard"
STATE_TRIGGER = "state_trigger"


@dataclass(frozen=True)
class Place:
    """What one place a condition sits takes, and what its validator says.

    ``takes_any_change``: the place takes ``ANY_CHANGE``, and reads an
    operator left out, blank or null as it (a trigger saved with no operator
    carries an explicit null). Everywhere else an operator left out reads as
    ``eq``, and a blank or null one is no operator.

    ``no_key`` is the validator's sentence for a condition with no key and
    ``key_field`` the field an editor names for it; ``bad_operator`` is its
    sentence for an operator the place does not take, written ``{op}``.
    """

    no_key: str
    key_field: str
    bad_operator: str
    takes_any_change: bool = False


PLACES: dict[str, Place] = {
    SKIP_IF: Place(
        "skip_if requires 'key'", "skip_if.key", "skip_if operator '{op}' is not valid",
    ),
    CONDITIONAL: Place(
        "conditional condition requires 'key'", "condition.key",
        "condition operator '{op}' is not valid",
    ),
    WAIT_UNTIL: Place(
        "wait_until condition requires 'key'", "condition.key",
        "wait_until condition operator '{op}' is not valid",
    ),
    GUARD: Place("missing 'key'", "conditions.key", "operator '{op}' is not valid"),
    STATE_TRIGGER: Place(
        "state_change trigger requires 'state_key'", "state_key",
        "state_operator '{op}' is not valid. Use: " + ", ".join((ANY_CHANGE, *OPERATORS)),
        takes_any_change=True,
    ),
}


def state_condition(trigger: Mapping[str, Any], key: str | None = None) -> dict[str, Any]:
    """A State Change trigger's own condition, as ``judge`` and
    ``condition_issues`` read it at ``STATE_TRIGGER``: its ``state_operator``
    and ``state_value``, on ``key`` (the trigger's ``state_key`` when none is
    given)."""
    return {
        "key": trigger.get("state_key") if key is None else key,
        "operator": trigger.get("state_operator"),
        "value": trigger.get("state_value"),
    }


def written_operator(condition: Mapping[str, Any], where: str) -> Any:
    """The operator ``condition`` names at ``where`` (``PLACES``), as written:
    at a place that takes ``ANY_CHANGE``, one left out, blank or null reads as
    it; elsewhere one left out reads as ``eq``. Kept as written otherwise, so
    an alias stays itself and a word nobody knows stays that word.

    The Programmer's ``writtenOperator`` reads it the same way, over
    ``tests/fixtures/written_operator_cases.json``.
    """
    if PLACES[where].takes_any_change:
        return condition.get("operator") or ANY_CHANGE
    return condition.get("operator", "eq")


def _takes(place: Place, op: Any) -> bool:
    return canonical_operator(op) is not None or (place.takes_any_change and op == ANY_CHANGE)


def judge(condition: Mapping[str, Any], read: Callable[[str], Any], where: str) -> bool:
    """Whether ``condition`` holds at ``where`` (``PLACES``): THE judge every
    place that acts on a condition asks.

    ``read`` is what the condition's key reads there: a state key, or a field
    of the firing context (``value_resolver.read_operand``). A value that
    names a key (``$var.wanted``) is read through it too, so both sides follow
    the place's rule for the firing context (``holds``). ``ANY_CHANGE``, at a
    place that takes it, holds without a read. An operator the place does not
    take raises ``UnknownOperator``, the one answer for a condition that
    cannot be judged.
    """
    place = PLACES[where]
    op = written_operator(condition, where)
    if place.takes_any_change and op == ANY_CHANGE:
        return True
    return holds(op, read(condition.get("key", "")), condition.get("value"), read)


class ConditionIssue(NamedTuple):
    """One thing that keeps a condition from being judged where it sits: the
    validator's sentence, and for a part left out, the field an editor names."""

    sentence: str
    missing: str | None = None


def condition_issues(condition: Mapping[str, Any], where: str) -> list[ConditionIssue]:
    """What ``condition`` lacks at ``where`` (``PLACES``): a key, then an
    operator the place takes. Empty for a condition ``judge`` reads."""
    place = PLACES[where]
    issues: list[ConditionIssue] = []
    if not condition.get("key"):
        issues.append(ConditionIssue(place.no_key, place.key_field))
    op = written_operator(condition, where)
    if not _takes(place, op):
        issues.append(ConditionIssue(place.bad_operator.format(op=op)))
    return issues
