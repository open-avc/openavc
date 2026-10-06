"""A boolean reads Yes / No in every sentence a person reads.

``openavc/utils/boolean_words.py`` is the server's copy, the Programmer's is
``components/shared/booleanWords.ts``, and ``core/monitors.py`` spells the same
two words itself because it is vendored on its own. These tests hold the three
together and pin what the helper does.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core import monitors
from openavc.utils.boolean_words import NO, YES, boolean_word, value_text

ROOT = Path(__file__).resolve().parent.parent
BOOLEAN_WORDS_TS = (
    ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "shared" / "booleanWords.ts"
)


def test_a_boolean_reads_yes_or_no():
    assert boolean_word(True) == "Yes"
    assert boolean_word(False) == "No"
    assert value_text(True) == "Yes"
    assert value_text(False) == "No"


def test_anything_else_reads_as_it_is():
    assert value_text("true") == "true"
    assert value_text(0) == "0"
    assert value_text(1) == "1"
    assert value_text("hdmi2") == "hdmi2"


def test_no_value_reads_as_the_callers_word():
    assert value_text(None, "not reported") == "not reported"
    assert value_text(None) == "None"


def test_the_programmer_uses_the_same_words():
    src = BOOLEAN_WORDS_TS.read_text(encoding="utf-8")
    words = dict(re.findall(r'export const (YES|NO) = "([^"]*)";', src))
    assert words == {"YES": YES, "NO": NO}


def test_the_monitor_rule_uses_the_same_words():
    assert monitors.monitor_reading({"key": "device.acme.ready"}, True) == YES
    assert monitors.monitor_reading({"key": "device.acme.ready"}, False) == NO
