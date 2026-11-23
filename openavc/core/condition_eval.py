"""
THE comparison of a reading with what an author wrote.

Asked by every reader of a condition on the server: a macro's ``skip_if`` and
conditional steps, a trigger's guards, a panel press the server resolves
(``ui_events.resolve_press``), a select's value map, and both page reviews.
``match_key`` is also every driver value map's lookup (a reply's, a device
setting's and a command parameter's), asked by the driver runtime and the
simulator through ``drivers/compiled_protocol.map_entry``; this module stays
standard-library-only (and ``core.monitors``) for them, since the simulator
and the driver validator import it.
The rule, in the order it is applied:

- a reading that has not arrived (None) is never equal; what it does to
  not-equal is the reader's mode (``MODES``, below);
- a boolean on either side compares both as booleans: the words that mean
  true and false, and a number by value, non-zero true;
- values of different types compare as numbers when both read as one
  (``core/monitors.py``'s ``as_number``: trimmed digits, a sign, a point, an
  exponent; never "1_000", "inf" or "nan", which compare as text);
- two words compare apart from case;
- an authored map is read in one order (``match_key``): the exact key, then
  one apart from case, then one the reading equals as a boolean or number.

Copied twice, and nowhere else: ``conditionEval.ts`` in the Programmer (the
trigger editor's "Evaluated now", the page reviews) and the panel's
``modules/compare.js``. All three run
``tests/fixtures/condition_parity_cases.json`` in both modes
(``tests/test_condition_parity.py``, ``conditionEval.test.ts``,
``tests/test_panel_condition_parity.py``): a change here goes into both
copies and the corpus in the same commit.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Iterable
from typing import Any

from openavc.core.monitors import as_number

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

# Aliases for operator names (user-friendly → canonical)
_OPERATOR_ALIASES: dict[str, str] = {
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


_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")


def _coerce_bool(value: Any) -> bool | None:
    """Normalize boolean-like words: the config switches ``reads_as_on`` reads."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
    return None


def _as_bool(value: Any) -> bool | None:
    """A reading as a boolean for comparing with one: a boolean, one of the
    words, or a number by value, non-zero true (a device's 2 holds against
    ``true``). None where it is none of those."""
    as_word = _coerce_bool(value)
    if as_word is not None:
        return as_word
    number = _finite_number(value)
    return None if number is None else number != 0


def reads_as_on(value: Any) -> bool:
    """A config value read as an on/off switch.

    A bool, or a word ``_coerce_bool`` knows ("yes", "off", "1"), says what it
    says; anything else counts by plain truthiness, so an unset field (None)
    or an empty one is off. The integrator's opt-in checkboxes a driver
    declares (a ``when:`` on a poll query or a push registration) read this.
    """
    as_bool = _coerce_bool(value)
    return bool(value) if as_bool is None else as_bool


def eval_operator(op: str, actual: Any, target: Any, mode: str = DECIDE) -> bool:
    """Whether ``actual op target`` holds, for a reader in ``mode`` (``MODES``).

    Aliases are normalized first; an operator nobody knows raises ValueError.
    """
    op = _OPERATOR_ALIASES.get(op, op)

    if op in ("eq", "ne"):
        return _equality(op, actual, target, mode)
    if op in _ORDERINGS:
        return _ordering(_ORDERINGS[op], actual, target)
    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not bool(actual)
    raise ValueError(
        f"Unknown condition operator: '{op}'. "
        f"Valid operators: eq, ne, gt, lt, gte, lte, truthy, falsy"
    )


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
    a_bool, t_bool = _as_bool(actual), _as_bool(target)
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
