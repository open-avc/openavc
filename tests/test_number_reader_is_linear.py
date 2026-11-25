"""The platform's number reader takes time in proportion to its input.

``core/monitors.as_number`` reads every value a condition, a monitor and a
boolean reply compare as a number, so it reads text a device sent. Its
pattern must not backtrack: a long run of digits that fails at its last
character (``000...0x``) is answered at once, not in time that grows with
the square of its length. The IDE's and the panel's copies carry the same
pattern (``monitorHelpers.ts``, ``modules/readings.js``).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from openavc.core.monitors import as_number

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
COPIES = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "api" / "monitorHelpers.ts",
    OPENAVC_ROOT / "openavc" / "web" / "panel" / "modules" / "readings.js",
)


@pytest.mark.parametrize(
    "text",
    ["0" * 30000 + "x", "1" * 30000 + ".x", "+" + "9" * 30000 + "e"],
    ids=["digits then a letter", "digits, a point, a letter", "digits then an e"],
)
def test_a_long_run_of_digits_that_is_no_number_is_answered_at_once(text):
    started = time.perf_counter()
    assert as_number(text) is None
    assert time.perf_counter() - started < 0.5


def test_a_long_number_still_reads():
    assert as_number("1" * 300) == float("1" * 300)


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: p.name)
def test_the_ide_and_the_panel_carry_the_same_pattern(copy):
    from openavc.core import monitors

    pattern = monitors._NUMERIC_RE.pattern
    found = re.search(r"/(\^\[\+-\]\?\(.*\)\?\$)/", copy.read_text(encoding="utf-8"))
    assert found is not None and found.group(1) == pattern
