"""A condition whose value names a key, run over one corpus in every copy.

``power == $var.wanted`` compares the projector's power with what
``var.wanted`` holds, not with the text "$var.wanted". A key with no value is
a reading that has not arrived, judged as one is on each side: a reader that
acts makes no decision on it, and one that shows or hides reads it as unequal
to every value. The key a value names is read too: a change to it wakes a Wait
Until and draws a ``visible_when`` again.

``fixtures/condition_reference_cases.json`` holds the answers, run here over
the server's rule (``core/condition_eval.py`` ``holds`` and
``condition_keys``) and the panel's copy (``modules/compare.js``, and a
``visible_when`` through ``modules/visibility.js``) in Node through
``fixtures/panel_condition_harness.cjs``; ``conditionEval.test.ts`` runs it
over the Programmer's.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.condition_eval import DECIDE, SURFACE, UnknownOperator, condition_keys, holds
from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "panel_condition_harness.cjs"
PANEL_JS = OPENAVC_ROOT / "openavc" / "web" / "panel" / "panel.js"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
CORPUS = json.loads(
    (OPENAVC_ROOT / "tests" / "fixtures" / "condition_reference_cases.json").read_text(encoding="utf-8")
)
CASES = CORPUS["cases"]
READS = CORPUS["reads"]


def _expected(case: dict, surface: bool) -> object:
    """A case's answer for a reader that acts, or for one that shows or hides."""
    return case.get("surface", case["expected"]) if surface else case["expected"]


def _never_on_the_panel(case: dict, surface: bool) -> bool:
    """The panel's answer: an operator the rule refuses never holds there."""
    want = _expected(case, surface)
    return False if want == "error" else want


def _server_answer(case: dict, mode: str) -> object:
    try:
        return holds(case["op"], case["actual"], case["value"], case["state"].get, mode)
    except UnknownOperator:
        return "error"


@pytest.mark.parametrize("mode", [DECIDE, SURFACE])
def test_the_server_s_rule_answers_every_reference_case(mode: str) -> None:
    differ = [
        case["name"] for case in CASES
        if _server_answer(case, mode) != _expected(case, mode == SURFACE)
    ]
    assert differ == []


def test_the_server_reads_the_keys_a_condition_names() -> None:
    assert [condition_keys(r["condition"]) for r in READS] == [r["expected"] for r in READS]


def test_the_corpus_tells_a_reference_with_no_value_from_an_authored_null() -> None:
    """The one place the two modes part on a reference: what a panel shows before the key has a value."""
    parted = {case["name"] for case in CASES if "surface" in case}
    assert "a reference whose key has no value makes no decision, but on a panel it differs" in parted
    assert "an authored null still compares with nothing, on a panel too" not in parted


@pytest.fixture(scope="module")
def panel() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    if not (NODE_MODULES / "jsdom").is_dir():
        gates.skip_or_fail(gates.NODE, "jsdom not installed (run `npm ci` in openavc/web/programmer)")
    corpus = json.dumps({
        "pairs": [], "cases": [], "keys": [],
        "references": [
            {"op": c["op"], "actual": c["actual"], "value": c["value"], "state": c["state"]} for c in CASES
        ],
        "reads": [r["condition"] for r in READS],
    })
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_JS)],
        input=corpus, capture_output=True, text=True, timeout=60,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["references"]


@pytest.mark.parametrize("surface", [False, True], ids=["decide", "surface"])
def test_the_panel_s_copy_answers_every_reference_case(panel: dict, surface: bool) -> None:
    got = panel["holdsSurface" if surface else "holdsDecide"]
    differ = [
        case["name"] for case, answer in zip(CASES, got, strict=True)
        if answer is not _never_on_the_panel(case, surface)
    ]
    assert differ == []


def test_a_visible_when_shows_as_the_surface_mode_answers(panel: dict) -> None:
    differ = [
        case["name"] for case, shown in zip(CASES, panel["visibleWhen"], strict=True)
        if shown is not _never_on_the_panel(case, True)
    ]
    assert differ == []


def test_a_visible_when_shows_a_not_equal_control_before_the_key_it_names_has_a_value(
    panel: dict,
) -> None:
    """The case that tells the modes apart, against an authored null that never holds."""
    names = [case["name"] for case in CASES]
    unset = names.index("a reference whose key has no value makes no decision, but on a panel it differs")
    authored_null = names.index("an authored null still compares with nothing, on a panel too")
    assert panel["visibleWhen"][unset] is True
    assert panel["visibleWhen"][authored_null] is False


def test_the_panel_reads_the_keys_a_condition_names(panel: dict) -> None:
    assert panel["keys"] == [r["expected"] for r in READS]


def test_a_visible_when_is_drawn_again_when_the_key_its_value_names_changes(panel: dict) -> None:
    assert panel["redrawKeys"] == [r["expected"] for r in READS]
    named = [
        reaches for r, reaches in zip(READS, panel["namedKeyRedraws"], strict=True)
        if len(r["expected"]) > 1
    ]
    assert named and all(reaches is True for reaches in named)
