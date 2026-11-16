"""The panel's comparers, run over the condition corpus.

When a reported value matches what an author wrote is decided in more than a
dozen places on the panel, each its own way: a look's named states and its
two-state form, a toggle's light and its press, a label's text condition, a
`visible_when`, the status LED's colour map, a dropdown and a list. The
server's rule is `core/condition_eval.py`, which macros and triggers ask and
the IDE's `conditionEval.ts` is held to through
`fixtures/condition_parity_cases.json`. Nothing held the panel to it.

Executed rather than read: Node runs each comparer through what the panel
itself calls (`fixtures/panel_condition_harness.cjs`) and the answers are
compared with `eval_operator` over the same corpus. A comparer that only
matches (every one but `visible_when`) is asked each pair of reading and
authored value once, against the rule's `eq`; `visible_when` is asked every
case with its own operator. A named state, a colour map's entry and a
dropdown's look are named by text, an object's key, so the authored value is
written there as text.

Every difference is pinned below as it is today, by cause, so a change on
either side shows up here and updates these sets rather than passing by:
- case: the named states, the LED, the dropdown, the list and `visible_when`
  compare words exactly, so `ON` is not `on`;
- the words that mean true: the rule reads a boolean beside "on", "yes",
  "off" (and 1, "1") as a boolean; the panel compares `true` as the word
  "true";
- a number's spelling: the rule reads "5.0", " 5 " and "1e3" as 5 and 1000
  beside a number; the comparers that compare words do not;
- what only Python's `float` reads as a number ("1_000", "inf");
- JavaScript's loose `==` in `visible_when`: "0x10" is 16, a blank is 0, a
  not-equal holds for a reading that is not there, and two numbers written as
  text are ordered as text;
- a list row whose value is 0: once a reading arrives the row is matched as a
  blank (`item.value || ''`), so a blank reading lights it;
- the operator spellings: `visible_when` knows ten of the rule's fourteen
  aliases, and an operator it does not know never holds where the rule
  refuses it.

Left out, because the corpus does not reach it:
- a reading that is not there at all: JSON carries no missing key, so the
  corpus's null is a state that holds null; the comparers that compare words
  read it as the word "null" (a look's two-state form, a toggle's press) or as
  no reading (a toggle's light, a label), and no case has null or "null" on
  both sides to tell them apart;
- a whole number written with a decimal point: JSON's 1.0 reaches JavaScript
  as 1, so a case of 1.0 runs as 1 on the panel;
- a list row of 0 against a reading of 0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.condition_eval import _OPERATOR_ALIASES, eval_operator
from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "panel_condition_harness.cjs"
PANEL_JS = OPENAVC_ROOT / "openavc" / "web" / "panel" / "panel.js"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
CORPUS_PATH = OPENAVC_ROOT / "tests" / "fixtures" / "condition_parity_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]

#: Every spelling the rule takes: its own eight and the aliases onto them.
RULE_SPELLINGS = {"eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"} | set(_OPERATOR_ALIASES)
#: The rule's spellings `visible_when` does not know.
UNKNOWN_TO_THE_PANEL = {
    "equal", "not_equal", "greater_than", "less_than", "greater_or_equal", "less_or_equal",
}


def _canonical(op: str) -> str:
    return _OPERATOR_ALIASES.get(op, op)


def _pairs() -> list[tuple[str, object, object]]:
    """Each (reading, authored value) the equality cases hold, once, named by its first case."""
    seen: dict[str, tuple[str, object, object]] = {}
    for case in CASES:
        if _canonical(case["op"]) in ("eq", "ne"):
            seen.setdefault(json.dumps([case["actual"], case["target"]]),
                            (case["name"], case["actual"], case["target"]))
    return list(seen.values())


PAIRS = _pairs()

# The differences, by cause. Each comparer's pin is the union of the causes
# it has; a name is the corpus case that first holds the pair.
CASE = {"text equals regardless of case"}
TRUE_WORDS = {"boolean true equals on", "boolean true equals yes", "boolean false equals off"}
TRUE_AS_ONE = {"boolean true equals 1 as text", "boolean true equals number 1"}
NUMBER_SPELLINGS = {"decimal text equals whole number", "text with spaces is a number", "exponent text"}
PYTHON_ONLY_NUMBERS = {"underscores are a number"}
ZERO_ROW_AS_BLANK = {"empty text is not zero"}

WORDS_APART_FROM_CASE = TRUE_WORDS | TRUE_AS_ONE | NUMBER_SPELLINGS | PYTHON_ONLY_NUMBERS
EXACT_WORDS = CASE | WORDS_APART_FROM_CASE

#: Where each comparer that only matches differs from the rule's `eq`.
MATCHER_DIFFERENCES = {
    "pickState": EXACT_WORDS,
    "pickTwoState": WORDS_APART_FROM_CASE,
    "toggleLit": WORDS_APART_FROM_CASE,
    "toggleIsOn": WORDS_APART_FROM_CASE,
    "conditionWords": WORDS_APART_FROM_CASE,
    "evaluateColor": EXACT_WORDS,
    "selectValue": EXACT_WORDS,
    "selectLook": EXACT_WORDS,
    "listAtDraw": EXACT_WORDS,
    "listAfterReading": EXACT_WORDS | ZERO_ROW_AS_BLANK,
}

#: Where `visible_when` differs from the rule, over the cases whose operator
#: it knows: case and the true words as above, then JavaScript's loose `==`.
VISIBLE_WHEN_DIFFERENCES = {
    # case, under each spelling of eq and ne
    "text equals regardless of case", "the same text in another case is not different",
    "alias equals", "alias not_equals", "alias ==", "alias !=",
    *TRUE_WORDS,
    *PYTHON_ONLY_NUMBERS, "infinity text",
    # loose ==: Number("0x10") is 16 and Number("") is 0
    "hex text is not a number", "empty text is not zero", "empty text differs from zero",
    # loose !=: a reading that is not there differs from everything
    "unreported never differs",
    # two numbers written as text are ordered as text: "10" < "9"
    "both text, read as numbers",
}


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not (NODE_MODULES / "jsdom").is_dir():
        return "jsdom not installed (run `npm ci` in openavc/web/programmer)"
    return None


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    corpus = json.dumps({
        "pairs": [[actual, target] for _name, actual, target in PAIRS],
        "cases": [{"op": c["op"], "actual": c["actual"], "target": c["target"]} for c in CASES],
    })
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_JS)],
        input=corpus, capture_output=True, text=True, timeout=60,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("comparer", sorted(MATCHER_DIFFERENCES))
def test_where_each_comparer_differs_from_the_rule(panel_answers: dict, comparer: str) -> None:
    differ = {
        name for (name, actual, target), matched in zip(PAIRS, panel_answers[comparer], strict=True)
        if matched != eval_operator("eq", actual, target)
    }
    assert differ == MATCHER_DIFFERENCES[comparer]


@pytest.mark.parametrize("comparer", sorted(MATCHER_DIFFERENCES))
def test_each_comparer_answers_both_ways(panel_answers: dict, comparer: str) -> None:
    """A comparer the harness reaches wrongly answers one way for every pair."""
    assert set(panel_answers[comparer]) == {True, False}


def test_where_visible_when_differs_from_the_rule(panel_answers: dict) -> None:
    differ = set()
    for case, shown in zip(CASES, panel_answers["visibleWhen"], strict=True):
        if case["op"] in UNKNOWN_TO_THE_PANEL or case["expected"] == "error":
            continue
        if shown != eval_operator(case["op"], case["actual"], case["target"]):
            differ.add(case["name"])
    assert differ == VISIBLE_WHEN_DIFFERENCES


def test_visible_when_knows_all_but_six_of_the_rules_spellings(panel_answers: dict) -> None:
    panel = set(panel_answers["spellings"])
    assert panel <= RULE_SPELLINGS
    assert RULE_SPELLINGS - panel == UNKNOWN_TO_THE_PANEL


def test_an_operator_visible_when_does_not_know_never_holds(panel_answers: dict) -> None:
    """Where the rule refuses an operator, or knows a spelling the panel does not."""
    unknown = [
        (case["name"], shown) for case, shown in zip(CASES, panel_answers["visibleWhen"], strict=True)
        if case["op"] in UNKNOWN_TO_THE_PANEL or case["expected"] == "error"
    ]
    assert unknown and not any(shown for _name, shown in unknown)
    assert any(case["expected"] == "error" for case in CASES)


def test_visible_when_holds_and_fails_under_every_operator_it_knows(panel_answers: dict) -> None:
    seen = {
        (_canonical(case["op"]), shown)
        for case, shown in zip(CASES, panel_answers["visibleWhen"], strict=True)
        if case["op"] not in UNKNOWN_TO_THE_PANEL and case["expected"] != "error"
    }
    canonical = {"eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"}
    assert seen == {(op, verdict) for op in canonical for verdict in (True, False)}


def test_every_difference_names_a_corpus_case() -> None:
    """A pin naming a case the corpus no longer holds would pass by itself."""
    names = {case["name"] for case in CASES}
    pair_names = {name for name, _actual, _target in PAIRS}
    for pinned in MATCHER_DIFFERENCES.values():
        assert pinned <= pair_names
    assert VISIBLE_WHEN_DIFFERENCES <= names
