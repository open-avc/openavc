"""The panel's comparison, and every control that asks it, run over the condition corpus.

Whether a reported value matches what an author wrote is asked in more than a
dozen places on the panel: a look's named states and its two-state form, a
toggle's light and its press, a label's text condition, a `visible_when`, the
status LED's colour map, a dropdown and a list. They all ask
`web/panel/modules/compare.js`, the panel's copy of the server's rule
(`core/condition_eval.py`, which macros and triggers ask and the IDE's
`conditionEval.ts` mirrors), and all three copies run
`fixtures/condition_parity_cases.json`.

Executed rather than read: Node runs `compare.js` over every case in both
modes, and each control through what the panel itself calls
(`fixtures/panel_condition_harness.cjs`); the answers are compared with
`eval_operator` and `match_key` over the same corpus. A control that only
matches is asked each pair of reading and authored value once, against the
rule's `eq`; one that reads an authored map is also asked which key each of
the corpus's `keys` cases picks; `visible_when` is asked every case with its
own operator, in the surface mode a control that shows or hides reads in,
with a null reading and with the key missing. A named state, a colour map's
entry and a dropdown's look are named by text, an object's key, so the
authored value is written there as text.

Left out, because JSON cannot carry it: a whole number written with a
decimal point. JSON's 1.0 reaches JavaScript as 1, so a case of 1.0 runs as 1
on the panel; `test_press_parity.py` runs the server's 1.0 against it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.condition_eval import (
    _OPERATOR_ALIASES,
    DECIDE,
    SURFACE,
    eval_operator,
    match_key,
    value_words,
)
from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "panel_condition_harness.cjs"
PANEL_JS = OPENAVC_ROOT / "openavc" / "web" / "panel" / "panel.js"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
CORPUS_PATH = OPENAVC_ROOT / "tests" / "fixtures" / "condition_parity_cases.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CASES = CORPUS["cases"]
KEYS = CORPUS["keys"]

#: Every spelling the rule takes: its own eight and the aliases onto them.
RULE_SPELLINGS = {"eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"} | set(_OPERATOR_ALIASES)


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

#: The controls that only match, each asked every pair.
MATCHERS = (
    "pickState", "pickTwoState", "toggleLit", "toggleIsOn", "conditionWords",
    "evaluateColor", "selectValue", "selectLook", "listAtDraw", "listAfterReading",
)
#: The controls that read an authored map, each asked every keys case.
PICKERS = ("pickState", "evaluateColor", "selectValue", "selectLook", "listAtDraw", "listAfterReading")

def _expected_in(case: dict, mode: str) -> object:
    return case.get("surface", case["expected"]) if mode == SURFACE else case["expected"]


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
        "keys": [{"keys": c["keys"], "reading": c["reading"]} for c in KEYS],
    })
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_JS)],
        input=corpus, capture_output=True, text=True, timeout=60,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("mode", [DECIDE, SURFACE])
def test_the_panel_s_copy_answers_every_case_as_the_rule(panel_answers: dict, mode: str) -> None:
    """An operator the rule refuses never holds on the panel; every other case answers as the rule."""
    got = panel_answers["compareDecide" if mode == DECIDE else "compareSurface"]
    differ = [
        case["name"] for case, answer in zip(CASES, got, strict=True)
        if answer is not (False if case["expected"] == "error" else _expected_in(case, mode))
    ]
    assert differ == []


def test_the_panel_s_copy_picks_every_key_as_the_rule(panel_answers: dict) -> None:
    assert panel_answers["compareKeys"] == [c["expected"] for c in KEYS]
    assert [match_key(c["keys"], c["reading"]) for c in KEYS] == [c["expected"] for c in KEYS]


def _rule_matches(comparer: str, actual: object, target: object) -> bool:
    """The rule's answer: ``eq``, or for a control that reads a map, whether
    the reading picks the key the authored value is written as."""
    if comparer in PICKERS:
        key = value_words(target) if target is not None else "null"
        return match_key([key], actual) is not None
    return eval_operator("eq", actual, target)


@pytest.mark.parametrize("comparer", MATCHERS)
def test_every_control_matches_as_the_rule(panel_answers: dict, comparer: str) -> None:
    differ = {
        name for (name, actual, target), matched in zip(PAIRS, panel_answers[comparer], strict=True)
        if matched != _rule_matches(comparer, actual, target)
    }
    assert differ == set()


@pytest.mark.parametrize("comparer", MATCHERS)
def test_each_control_answers_both_ways(panel_answers: dict, comparer: str) -> None:
    """A control the harness reaches wrongly answers one way for every pair."""
    assert set(panel_answers[comparer]) == {True, False}


@pytest.mark.parametrize("picker", PICKERS)
def test_every_map_reader_picks_the_key_the_rule_picks(panel_answers: dict, picker: str) -> None:
    differ = {
        case["name"] for case, picked in zip(KEYS, panel_answers["picks"][picker], strict=True)
        if picked != case["expected"]
    }
    assert differ == set()


def test_visible_when_shows_as_the_rule_s_surface_mode(panel_answers: dict) -> None:
    differ = []
    for case, shown, missing in zip(
        CASES, panel_answers["visibleWhen"], panel_answers["visibleWhenMissing"], strict=True,
    ):
        want = False if case["expected"] == "error" else _expected_in(case, SURFACE)
        if shown is not want:
            differ.append(case["name"])
        if case["actual"] is None and missing is not want:
            differ.append(f"{case['name']} (key missing)")
    assert differ == []


def test_visible_when_shows_a_not_on_control_before_the_device_reports(panel_answers: dict) -> None:
    """The plain reading of "show this while the power is not on", and the case that tells the modes apart."""
    name = "unreported never differs, but on a panel it differs from everything"
    index = next(i for i, c in enumerate(CASES) if c["name"] == name)
    assert panel_answers["visibleWhen"][index] is True
    assert panel_answers["visibleWhenMissing"][index] is True


def test_visible_when_knows_every_spelling_the_rule_takes(panel_answers: dict) -> None:
    assert set(panel_answers["spellings"]) == RULE_SPELLINGS


def test_visible_when_holds_and_fails_under_every_operator(panel_answers: dict) -> None:
    seen = {
        (_canonical(case["op"]), shown)
        for case, shown in zip(CASES, panel_answers["visibleWhen"], strict=True)
        if case["expected"] != "error"
    }
    canonical = {"eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"}
    assert seen == {(op, verdict) for op in canonical for verdict in (True, False)}


def test_a_list_row_whose_value_is_zero_lights(panel_answers: dict) -> None:
    """Readings 0, "0" and a blank, against rows 0 and 1: 0 lights for both spellings of it, never for a blank."""
    assert panel_answers["zeroRow"] == {"atDraw": [[0], [0], []], "afterReading": [[0], [0], []]}
