"""THE usable-number rule: whether a value is a number every reader can use.

A usable number is finite (never NaN or an infinity), is not a boolean (YAML's
and JSON's ``true`` and ``false``, which Python counts as 1 and 0), and fits a
float (an integer past about 1.8e308 does not). Reading one never raises.

Two doors, because the readers take text differently on purpose:

* ``number_value`` reads a value written as a number, an int or a float, and
  never text. The checker's number fields, a driver's declared bounds and the
  page review's range check ask it: in a driver file, ``"5"`` is text.
* ``usable_number`` also reads text that ``float()`` reads (trimmed), as a
  value off the wire, a config field or a command parameter may be written.

A reader with a text grammar of its own (the serial line settings' decimal
digits) checks its grammar first and hands what it accepts to
``usable_number``. Each reader keeps its own range test: this says only
whether there is a number to test.

Purity contract, as ``spec``: standard library only, and no logger. It is part
of the driver contract the community catalog vendors (openavc-drivers'
``scripts/vendor_platform_contract.py``) and the SDK syncs.
"""

from __future__ import annotations

import math
from typing import Any


def number_value(value: Any) -> float | None:
    """``value`` as a float when it is a usable number written as one (an int
    or a float); None for anything else, text included."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _finite(value)


def is_number_value(value: Any) -> bool:
    """Whether ``value`` is a usable number written as one (``number_value``)."""
    return number_value(value) is not None


def usable_number(raw: Any) -> float | None:
    """``raw`` as a float when it is a usable number, or text ``float()``
    reads as one once trimmed (``" 5 "``, ``"1e3"``); None for anything else."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return _finite(raw)
    try:
        number = float(str(raw).strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _finite(value: int | float) -> float | None:
    try:
        number = float(value)
    except OverflowError:  # an int too large to be a float
        return None
    return number if math.isfinite(number) else None
