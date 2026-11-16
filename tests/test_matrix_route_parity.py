"""The matrix's route rule, held across its three copies.

Which source a device's report names is decided three times: by the panel
(`routeValue` / `routeMatches` in `web/panel/modules/matrix.js`, the copy that
decides which crosspoint lights), by the server (`route_value` /
`route_matches` in `ui/matrix_model.py`, which the page review asks) and by the
Builder (`matrixRouteValue` / `matrixRouteMatches` in `matrixModel.ts`, its
page review's copy). Where they disagree the page review judges a matrix the
panel does not draw.

One corpus, `tests/fixtures/matrix_route_cases.json`, says the answer each
case must get, and all three copies are asked it: Python here, the other two in
Node (`fixtures/matrix_rules_harness.cjs`). An answer is compared by kind as
well as value, so the number 3 and the word "3" are different answers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.ui.matrix_model import route_matches, route_value, source_report_value
from tests import gates

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "fixtures" / "matrix_rules_harness.cjs"
PANEL_MATRIX = ROOT / "openavc" / "web" / "panel" / "modules" / "matrix.js"
BUILDER_MODEL = (
    ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder" / "matrixModel.ts"
)
NODE_MODULES = ROOT / "openavc" / "web" / "programmer" / "node_modules"
CORPUS = json.loads((ROOT / "tests" / "fixtures" / "matrix_route_cases.json").read_text(encoding="utf-8"))
VALUES, MATCHES, REPORTS = CORPUS["values"], CORPUS["matches"], CORPUS["reports"]
COPIES = ["panel", "builder"]


def _kind(value: object) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return type(value).__name__


def _same(got: object, expected: object) -> bool:
    """Same kind, same value; a number as the double JavaScript holds it, since
    Node writes a number past 2**53 back in its shortest digits."""
    if _kind(got) != _kind(expected):
        return False
    if _kind(got) == "number":
        return float(got) == float(expected)
    return got == expected


@pytest.fixture(scope="module")
def copies() -> dict:
    """The panel's and the Builder's answers to the whole corpus."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    if not (NODE_MODULES / "esbuild").is_dir():
        gates.skip_or_fail(gates.NODE, "esbuild not installed (run `npm ci` in openavc/web/programmer)")
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_MATRIX), str(BUILDER_MODEL)],
        input=json.dumps({"route": CORPUS}), capture_output=True, text=True, timeout=120,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert proc.returncode == 0, proc.stderr
    return {name: answers["route"] for name, answers in json.loads(proc.stdout).items()}


@pytest.mark.parametrize("case", VALUES, ids=[c["name"] for c in VALUES])
def test_the_server_reads_each_report_as_the_corpus_says(case: dict) -> None:
    got = route_value(case["reported"])
    assert _same(got, case["expected"]), got


@pytest.mark.parametrize("copy", COPIES)
def test_the_panel_and_the_builder_read_each_report_as_the_corpus_says(copies: dict, copy: str) -> None:
    got = copies[copy]["values"]
    wrong = [(c["name"], g) for c, g in zip(VALUES, got, strict=True) if not _same(g, c["expected"])]
    assert wrong == []


@pytest.mark.parametrize("case", MATCHES, ids=[c["name"] for c in MATCHES])
def test_the_server_matches_each_pair_as_the_corpus_says(case: dict) -> None:
    both_ways = [route_matches(case["a"], case["b"]), route_matches(case["b"], case["a"])]
    assert both_ways == [case["expected"]] * 2


@pytest.mark.parametrize("copy", COPIES)
def test_the_panel_and_the_builder_match_each_pair_as_the_corpus_says(copies: dict, copy: str) -> None:
    got = copies[copy]["matches"]
    wrong = [(c["name"], g) for c, g in zip(MATCHES, got, strict=True) if g != [c["expected"]] * 2]
    assert wrong == []


@pytest.mark.parametrize("case", REPORTS, ids=[c["name"] for c in REPORTS])
def test_the_server_matches_a_source_by_what_the_corpus_says(case: dict) -> None:
    got = source_report_value(case["source"])
    assert _same(got, case["expected"]), got


@pytest.mark.parametrize("copy", COPIES)
def test_the_panel_and_the_builder_match_a_source_by_what_the_corpus_says(copies: dict, copy: str) -> None:
    got = copies[copy]["reports"]
    wrong = [(c["name"], g) for c, g in zip(REPORTS, got, strict=True) if not _same(g, c["expected"])]
    assert wrong == []


def test_the_corpus_reaches_every_form_of_the_rule() -> None:
    """A corpus of plain numbers alone would prove nothing about the words."""
    kinds = {_kind(c["expected"]) for c in VALUES}
    assert kinds == {"nothing", "number", "str"}
    reported = {_kind(c["reported"]) for c in VALUES}
    assert reported == {"nothing", "boolean", "number", "str"}
    outcomes = {c["expected"] for c in MATCHES}
    assert outcomes == {True, False}
    # A match through a single embedded number, and one refused for two.
    assert any(c["expected"] and _kind(c["a"]) == "str" and _kind(c["b"]) == "number" for c in MATCHES)
    assert any(not c["expected"] and c["a"] == "1080p60" for c in MATCHES)
    assert any("report_value" in (c["source"] or {}) for c in REPORTS)
