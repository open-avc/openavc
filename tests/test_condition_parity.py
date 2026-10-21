"""The condition rule, checked against the corpus the IDE is checked against.

``openavc/core/condition_eval.py`` decides whether a trigger's conditions pass.
The trigger editor shows the same decision as "Evaluated now" through its
mirror, ``conditionEval.ts``. Both run this corpus and compare answer for
answer, so the preview cannot say FALSE about a trigger the runtime fires.

The guards below keep the corpus honest: every operator has to come out both
true and false, and every alias has to appear, or parity over it proves
nothing about the cases it skips.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.core.condition_eval import _OPERATOR_ALIASES, eval_operator

#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule.
CORPUS_PATH = Path(__file__).parent / "fixtures" / "condition_parity_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
OPERATORS = ("eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case):
    if case["expected"] == "error":
        with pytest.raises(ValueError):
            eval_operator(case["op"], case["actual"], case["target"])
    else:
        assert eval_operator(case["op"], case["actual"], case["target"]) is case["expected"]


def test_every_operator_comes_out_both_ways():
    seen = {(c["op"], c["expected"]) for c in CASES}
    missing = [(op, want) for op in OPERATORS for want in (True, False) if (op, want) not in seen]
    assert missing == []


def test_every_alias_is_in_the_corpus():
    used = {c["op"] for c in CASES}
    assert set(_OPERATOR_ALIASES) <= used
