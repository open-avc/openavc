"""THE reading of a command's ``restarts_device_for``: how long sending the
command takes the device away, in whole seconds from 1 to 600, or None.

``restart_seconds(raw)`` reads the value as written. A usable number
(``usable_number``: text ``float()`` reads included; a boolean, an infinity,
NaN or a number past a float is none) above 0 is rounded up to the whole
second, as the contract tells an author to round a measured gap, and held to
``RESTART_SECONDS_MAX``. 0 or less is no restart. So a driver dropped
straight into the driver folder, which no author's door checked, still opens
a window of at most ten minutes, and never one no clock can end.

``restart_problem(raw)`` is what the author's doors refuse: a value written
that is not a whole number of seconds in the range, the range named, as the
catalog's schema refuses it. A loaded driver keeps loading and has the same
sentence on its Installed entry. None for a value left out or written null.

The range is the contract's (``spec``'s ``commandEntry`` field), and the
Programmer IDE's copy is generated from the two constants here
(``contract_gen_ts``).

Who asks it: the runtime's commanded-restart window
(``core/device_away``), the command model the Device Audit serves
(``command_model.CommandModel.restarts_device_for``), and the checker for
both kinds of driver (``driver_model``'s problems for a YAML file,
``python_info``'s command section for a ``DRIVER_INFO``). The sleep rule
(``command_checks``) names only a wake command's window left out, so a value
written is this rule's alone to judge.

Purity contract, as ``spec``: standard library plus ``spec`` and
``usable_number``, no logger. Vendored with the driver contract.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from openavc.drivers.spec import DEFS
from openavc.drivers.usable_number import number_value, usable_number

_FIELD = DEFS["commandEntry"]["fields"]["restarts_device_for"]
RESTART_SECONDS_MIN: int = _FIELD["min"]
RESTART_SECONDS_MAX: int = _FIELD["max"]

# The code a driver model's problem carries for a value outside the range.
RESTART_RANGE = "restart_range"


def restart_seconds(raw: Any) -> int | None:
    """The whole seconds a command declaring ``raw`` takes the device away
    for, from 1 to ``RESTART_SECONDS_MAX``; None when it declares none."""
    seconds = usable_number(raw)
    if seconds is None or seconds <= 0:
        return None
    return min(RESTART_SECONDS_MAX, math.ceil(seconds))


def restart_problem(raw: Any) -> str | None:
    """What the author's doors say of ``raw``, or None when it is left out or
    is a whole number of seconds in the range."""
    if raw is None:
        return None
    number = number_value(raw)
    if number is not None and number.is_integer() and RESTART_SECONDS_MIN <= number <= RESTART_SECONDS_MAX:
        return None
    return (
        f"must be a whole number of seconds from {RESTART_SECONDS_MIN} to {RESTART_SECONDS_MAX}, "
        f"not {_shown(raw)}; leave it out when the command does not restart the device"
    )


def restart_problems(
    commands: Any, judged: Callable[[str, Any], bool] = lambda place, value: True,
) -> Iterator[tuple[str, str]]:
    """Each command in ``commands`` whose ``restarts_device_for`` the author's
    doors refuse, as its place and the words said there, among the values
    ``judged(place, value)`` lets be judged (a Python driver's checker skips
    one built in code, and names the skip there)."""
    if not isinstance(commands, Mapping):
        return
    for name, cdef in commands.items():
        if not isinstance(cdef, Mapping):
            continue
        place, value = f"commands.{name}.restarts_device_for", cdef.get("restarts_device_for")
        words = restart_problem(value)
        if words is not None and judged(place, value):
            yield place, words


def _shown(raw: Any) -> str:
    """``raw`` as the file wrote it: text in quotes, true and false as YAML
    writes them."""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, str):
        return f'"{raw}"'
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw)
