"""The monitor rule, checked against the corpus the IDE is checked against.

``openavc/core/monitors.py`` decides whether a monitored reading is all right,
and that decision reaches somebody as an alert. ``monitorHelpers.ts`` makes the
same decision about the tile they are looking at when it arrives. The two must
not disagree, so both run this corpus and compare answer for answer -- the same
arrangement that holds ``page_review`` and ``reviewPage`` together.

Two of the assertions here guard the guard: parity over a corpus that never
reaches a branch passes for free, so the corpus is required to produce every
status and at least one authored word.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.core.monitors import (
    ABNORMAL, NORMAL, NO_VALUE, UNSET,
    compile_alert_rules, monitor_reading, monitor_status, monitor_word,
)

#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule, so
#: a corpus under tests/data/ is silently untracked -- green here, ENOENT in CI.
CORPUS_PATH = Path(__file__).parent / "fixtures" / "monitor_parity_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case):
    monitor, value = case["monitor"], case["value"]
    assert monitor_status(monitor, value) == case["status"], case["name"]
    assert monitor_word(monitor, value) == case["word"], case["name"]
    assert monitor_reading(monitor, value) == case["reading"], case["name"]


def test_the_corpus_reaches_every_verdict():
    """A parity corpus that never produces a verdict proves nothing about it."""
    produced = {monitor_status(c["monitor"], c["value"]) for c in CASES}
    assert produced == {UNSET, NO_VALUE, NORMAL, ABNORMAL}


def test_the_corpus_covers_words_and_bare_values():
    """...and one that never renders a word proves nothing about the words."""
    words = {monitor_word(c["monitor"], c["value"]) for c in CASES}
    assert "Occupied" in words, "no authored word in the corpus"
    assert "Yes" in words, "no bare boolean in the corpus"
    assert None in words, "no value that speaks for itself in the corpus"


def test_a_boolean_never_renders_as_true_or_false():
    """The rule the whole states map exists for."""
    for case in CASES:
        rendered = monitor_reading(case["monitor"], case["value"])
        assert rendered not in ("True", "False", "true", "false"), case["name"]


def test_a_monitor_with_no_limits_compiles_to_no_rule():
    """Informational is informational: nothing fires, so nothing is compiled."""
    assert compile_alert_rules([{"key": "device.proj.lamp_hours", "label": "Lamp"}]) == []
    assert compile_alert_rules([
        {"key": "device.sw.input", "states": {"hdmi1": {"label": "Laptop"}}},
    ]) == []


def test_a_range_compiles_to_one_rule_per_end():
    rules = compile_alert_rules([{
        "key": "device.dsp.temp_c", "label": "DSP Temp",
        "normal_min": 10, "normal_max": 45, "duration_seconds": 300,
    }])
    assert [r["id"] for r in rules] == [
        "monitor.device.dsp.temp_c.below", "monitor.device.dsp.temp_c.above",
    ]
    assert [r["condition"]["operator"] for r in rules] == ["<", ">"]
    assert [r["condition"]["value"] for r in rules] == [10, 45]
    assert all(r["condition"]["duration_seconds"] == 300 for r in rules)
    assert all(r["rule_type"] == "threshold" for r in rules)
    assert all(r["category"] == "device" for r in rules)


def test_a_one_sided_range_compiles_to_one_rule():
    rules = compile_alert_rules([
        {"key": "device.proj.lamp_hours", "normal_max": 2000},
    ])
    assert len(rules) == 1
    assert rules[0]["id"] == "monitor.device.proj.lamp_hours.above"


def test_a_value_limit_compiles_to_a_pattern_rule_holding_the_normal_set():
    rules = compile_alert_rules([{
        "key": "var.occupied", "label": "Occupancy",
        "states": {"true": {"label": "Occupied", "normal": True},
                   "false": {"label": "Vacant"}},
        "duration_seconds": 600,
    }])
    assert len(rules) == 1
    rule = rules[0]
    assert rule["id"] == "monitor.var.occupied"
    assert rule["rule_type"] == "pattern"
    assert rule["condition"] == {
        "key": "var.occupied", "operator": "not_in",
        "value": ["true"], "duration_seconds": 600,
    }
    # var.* is not a device, and an alert attributed to a device that does not
    # exist is worse than one attributed to the system.
    assert rule["category"] == "system"


def test_the_corpus_carries_a_bound_that_is_not_a_number():
    """...and one that never meets a word where a number belongs proves nothing
    about it. A bound this rule cannot evaluate raised out of ``float()`` here
    and quietly drew NORMAL in the IDE, which is two wrong answers to one
    declaration -- so the corpus has to keep meeting one."""
    assert any(
        isinstance(c["monitor"].get("normal_min"), str)
        or isinstance(c["monitor"].get("normal_max"), str)
        for c in CASES
    ), "no non-numeric bound in the corpus"


def test_a_bound_that_is_not_a_number_compiles_to_no_rule():
    """Nothing is claimed, so nothing fires. A threshold rule carrying "warm"
    as its value would arm on every reading and go off on none."""
    assert compile_alert_rules([
        {"key": "device.amp.temp", "normal_max": "warm"},
    ]) == []


def test_the_good_half_of_a_range_still_compiles():
    rules = compile_alert_rules([
        {"key": "device.amp.temp", "normal_min": 10, "normal_max": "warm"},
    ])
    assert [r["id"] for r in rules] == ["monitor.device.amp.temp.below"]
    assert rules[0]["condition"]["value"] == 10.0


def test_a_compiled_rule_id_carries_no_colon():
    """The alert monitor's bookkeeping key is ``rule:<id>:<state key>`` and it
    splits on the colon, so a rule id containing one silently resolves to the
    wrong rule -- and the timer that fires it never finds its duration."""
    rules = compile_alert_rules([
        {"key": "device.dsp.temp_c", "normal_max": 45},
        {"key": "var.occupied", "states": {"true": {"normal": True}}},
    ])
    assert rules
    for rule in rules:
        assert ":" not in rule["id"]
