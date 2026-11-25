"""THE words that mean true and false.

A value read as a flag, wherever it came from: a word a device sent for a
boolean state variable (a text reply, a JSON body, an OSC argument), a
condition comparing a reading with ``true`` or ``false`` (``core/
condition_eval``: macros, triggers, a reply's ``only_when``, the panel's
rule), a driver's opt-in switch (``reads_as_on``), a device setting a person
writes (``param_validation``), a value learned from a device
(``core/learned_config``) and a command's declared effect (the Device Audit).

The rule:

- a boolean is itself;
- a word, trimmed and apart from case: ``on``, ``true``, ``yes``,
  ``enabled`` are true, ``off``, ``false``, ``no``, ``disabled`` false;
- a number by value, non-zero true (``1``, ``01`` and ``2`` are true; ``0``,
  ``00`` and ``0.0`` false), read the way ``core/monitors.as_number`` reads
  one, so ``1_000``, ``inf`` and ``nan`` are not numbers here;
- anything else, an empty value included, is no reading: ``read_truth``
  answers None, and each caller says what that means at its door (a reply
  writes no value and reports the word, a setting is refused, a condition
  with a boolean on one side does not hold).

Generated twice, and written nowhere else: ``TRUE_WORDS`` and
``FALSE_WORDS`` go into the Programmer's ``api/types.gen.ts`` and the
panel's ``modules/truth_words.gen.js`` (``drivers/contract_gen``), which the
IDE's and the panel's copies of the condition rule read.

Pure: the standard library and ``core/monitors``, no logger, so the
simulator and the condition rule can import it.
"""

from __future__ import annotations

import math
from typing import Any

from openavc.core.monitors import as_number

#: The words that mean true, trimmed and apart from case.
TRUE_WORDS: tuple[str, ...] = ("on", "true", "yes", "enabled")
#: The words that mean false, trimmed and apart from case.
FALSE_WORDS: tuple[str, ...] = ("off", "false", "no", "disabled")


def read_truth(value: Any) -> bool | None:
    """``value`` read as true or false, or None when it is neither (the rule
    is the module's)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        word = value.strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
    number = as_number(value)
    if number is None or not math.isfinite(number):
        return None
    return number != 0
