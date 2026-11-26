"""What a condition's operator reads as where it sits, on the server's side.

``tests/fixtures/written_operator_cases.json`` holds the cases: a State Change
trigger saved with no operator, a null one or a blank one fires on any change,
and everywhere else an operator left out reads as ``eq`` while a blank one is
no operator. The Programmer runs the same file over its own reading
(``conditionEval.test.ts``), which its operator pickers and the trigger
summaries use, so the editor never shows a trigger doing something other than
what the engine does with it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.core.condition_eval import condition_issues, written_operator

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "written_operator_cases.json").read_text(encoding="utf-8")
)["cases"]


def _condition(case: dict) -> dict:
    condition = {"key": "var.level"}
    if "operator" in case:
        condition["operator"] = case["operator"]
    return condition


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_operator_reads_as_the_case_says(case: dict) -> None:
    assert written_operator(_condition(case), case["where"]) == case["written"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_place_takes_it_as_the_case_says(case: dict) -> None:
    assert (condition_issues(_condition(case), case["where"]) == []) is case["taken"]


def test_the_cases_reach_every_place() -> None:
    from openavc.core.condition_eval import PLACES

    assert {c["where"] for c in CASES} == set(PLACES)
