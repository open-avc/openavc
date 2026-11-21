"""The usable-number rule, and each reader that asks it, over one table.

``openavc/drivers/usable_number.py`` says whether a value is a number every
reader can use: finite, not a boolean, small enough to be a float, and read
without raising. Each reader below keeps its own range test and its own
reading of text; what they share is that NaN, an infinity, ``true`` and a
number too large to hold are never a number to any of them. On an invented
Acme device.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

import pytest

from openavc.core.learned_config import accepted_value
from openavc.drivers import auth_block, serial_line
from openavc.drivers.compiled_protocol import (
    build_throttle,
    is_unknown_reading,
    reading_from_wire,
    state_var_default,
    value_to_wire,
)
from openavc.drivers.driver_warnings import _number as warning_number
from openavc.drivers.param_validation import _as_number as param_number
from openavc.drivers.usable_number import is_number_value, number_value, usable_number
from openavc.simulator.yaml_auto import _as_number as simulator_bound

HUGE = 10**400
NEG_HUGE = -HUGE

# Every reader is asked the same values; the ones no reader takes as a number.
NEVER_A_NUMBER = [
    True, False, math.inf, -math.inf, math.nan, HUGE, NEG_HUGE,
    "inf", "-Infinity", "nan", "1e400", "a", "", None, [5], {"a": 5},
]
_IDS = {id(HUGE): "huge", id(NEG_HUGE): "-huge"}


def _ids(values: list[Any]) -> list[str]:
    return [_IDS.get(id(v), repr(v)) for v in values]


@pytest.mark.parametrize("value", NEVER_A_NUMBER, ids=_ids(NEVER_A_NUMBER))
def test_no_door_reads_a_value_that_is_not_a_usable_number(value: Any) -> None:
    assert number_value(value) is None
    assert is_number_value(value) is False
    assert usable_number(value) is None


@pytest.mark.parametrize(
    "value, number",
    [(5, 5.0), (-5, -5.0), (0, 0.0), (2.5, 2.5), (2**60, float(2**60)), (1.7e308, 1.7e308)],
)
def test_a_number_written_as_one_reads_at_both_doors(value: Any, number: float) -> None:
    assert number_value(value) == number
    assert usable_number(value) == number
    assert is_number_value(value) is True


@pytest.mark.parametrize(
    "text, number", [("5", 5.0), (" 5 ", 5.0), ("-2.5", -2.5), ("1e3", 1000.0), ("0255", 255.0)],
)
def test_text_is_a_number_only_at_the_text_door(text: str, number: float) -> None:
    assert usable_number(text) == number
    assert number_value(text) is None


# ── Each reader over the table ──
#
# A reader's own answer for "no number here" (None, a default, a refusal);
# the point is that none of them raises and none takes the value as a number.

_READERS: dict[str, tuple[Callable[[Any], Any], Any]] = {
    "the seed rule's integer min": (lambda v: state_var_default({"type": "integer", "min": v}), 0),
    "a reply rule's throttle": (lambda v: build_throttle({"throttle": v}), None),
    "a sign-in stage's timeout": (auth_block._timeout, auth_block.DEFAULT_TIMEOUT_S),
    "a command parameter (number)": (lambda v: param_number(v, "number"), None),
    "a command parameter (integer)": (lambda v: param_number(v, "integer"), None),
    "a serial line setting's number": (serial_line._line_number, None),
    "a simulator bound": (simulator_bound, None),
    "a config field the warnings read": (warning_number, None),
    "a learned integer field": (lambda v: accepted_value({"type": "integer"}, v), (False, None)),
    "a learned number field": (lambda v: accepted_value({"type": "number"}, v), (False, None)),
}


@pytest.mark.parametrize("reader", sorted(_READERS))
@pytest.mark.parametrize("value", NEVER_A_NUMBER, ids=_ids(NEVER_A_NUMBER))
def test_every_reader_takes_no_number_from_it_and_never_raises(
    reader: str, value: Any, caplog: pytest.LogCaptureFixture,
) -> None:
    read, nothing = _READERS[reader]
    with caplog.at_level(logging.CRITICAL):
        assert read(value) == nothing


@pytest.mark.parametrize("value", [5, 5.0, "5", " 5 "])
def test_every_reader_still_reads_an_ordinary_number(value: Any) -> None:
    assert state_var_default({"type": "integer", "min": value}) == 5
    assert build_throttle({"throttle": value}) == {"window": 5.0, "last": {}}
    assert auth_block._timeout(value) == 5.0
    assert param_number(value, "integer") == 5.0
    assert serial_line._line_number(value) == 5.0
    assert simulator_bound(value) == 5.0
    assert warning_number(value) == 5.0
    assert accepted_value({"type": "integer"}, value) == (True, 5)
    assert accepted_value({"type": "number"}, value) == (True, 5.0)


@pytest.mark.parametrize("bound", [math.inf, math.nan, HUGE, True], ids=["inf", "nan", "huge", "true"])
def test_a_learned_field_s_bound_that_is_no_number_is_not_a_bound(bound: Any) -> None:
    field = {"type": "integer", "min": bound, "max": bound}
    assert accepted_value(field, 5) == (True, 5)


@pytest.mark.parametrize("raw", [HUGE, math.inf, math.nan], ids=["huge", "inf", "nan"])
def test_a_conversion_reads_no_number_from_it(raw: Any) -> None:
    decl = {"scale": 0.5, "unknown": [255]}
    assert is_unknown_reading(raw, decl) is False
    assert reading_from_wire(raw, decl, "number") == (False, None)
    assert value_to_wire(raw, decl) is raw
