"""The condition rule, checked against the corpus its copies are checked against.

``openavc/core/condition_eval.py`` is the one comparison of a reading with what
an author wrote: a trigger's and a macro's conditions, a panel press the
server resolves, a select's value map. The trigger editor shows the same
decision as "Evaluated now" through its mirror, ``conditionEval.ts``, and the
panel asks ``modules/compare.js``. All three run this corpus and compare
answer for answer, in both of the rule's modes, so a preview, a panel and the
runtime cannot judge one condition differently.

The guards below keep the corpus honest: every operator has to come out both
true and false, every alias has to appear, the surface mode has to answer
differently somewhere, and every tier of the key order has to pick, or parity
over it proves nothing about the cases it skips.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.core.condition_eval import (
    OPERATOR_ALIASES,
    DECIDE,
    MODES,
    SURFACE,
    eval_operator,
    match_key,
)

#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule.
CORPUS_PATH = Path(__file__).parent / "fixtures" / "condition_parity_cases.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CASES = CORPUS["cases"]
KEYS = CORPUS["keys"]
OPERATORS = ("eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy")


def expected_in(case: dict, mode: str):
    """What a reader in ``mode`` answers: a surface reader's own answer where the case names one."""
    return case.get("surface", case["expected"]) if mode == SURFACE else case["expected"]


@pytest.mark.parametrize("mode", [DECIDE, SURFACE])
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case, mode):
    if case["expected"] == "error":
        with pytest.raises(ValueError):
            eval_operator(case["op"], case["actual"], case["target"], mode)
    else:
        assert eval_operator(case["op"], case["actual"], case["target"], mode) is expected_in(case, mode)


def test_a_reader_that_acts_is_the_default():
    for case in CASES:
        if case["expected"] != "error":
            assert eval_operator(case["op"], case["actual"], case["target"]) is case["expected"]


def test_the_modes_are_the_two_the_corpus_names():
    assert set(MODES) == {DECIDE, SURFACE}


def test_the_surface_mode_answers_differently_somewhere():
    assert any("surface" in c and c["surface"] != c["expected"] for c in CASES)


@pytest.mark.parametrize("case", KEYS, ids=[c["name"] for c in KEYS])
def test_key_case(case):
    assert match_key(case["keys"], case["reading"]) == case["expected"]


@pytest.mark.parametrize("case", KEYS, ids=[c["name"] for c in KEYS])
def test_the_key_order_is_not_the_authored_order(case):
    """JavaScript lists a map's number-like keys first, so the order cannot be the author's."""
    assert match_key(list(reversed(case["keys"])), case["reading"]) == case["expected"]


def test_every_tier_of_the_key_order_picks_a_key():
    tiers = {"exact": False, "case": False, "value": False, "none": False}
    for case in KEYS:
        picked = case["expected"]
        if picked is None:
            tiers["none"] = True
            continue
        reading = case["reading"]
        if isinstance(reading, str) and picked == reading:
            tiers["exact"] = True
        elif isinstance(reading, str) and picked.casefold() == reading.casefold():
            tiers["case"] = True
        elif not isinstance(reading, str):
            tiers["value"] = True
    assert all(tiers.values()), tiers


def test_every_operator_comes_out_both_ways():
    seen = {(c["op"], c["expected"]) for c in CASES}
    missing = [(op, want) for op in OPERATORS for want in (True, False) if (op, want) not in seen]
    assert missing == []


def test_every_alias_is_in_the_corpus():
    used = {c["op"] for c in CASES}
    assert set(OPERATOR_ALIASES) <= used
