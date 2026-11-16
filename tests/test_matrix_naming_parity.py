"""What a matrix row is called, held across the server, the Builder and the panel.

A row's name is the one typed for it, else the name its device reports under
`label_key`, else "In N" / "Out N" by where it was written. The server works
out the last of these when it resolves the matrix (`resolve_axis` in
`ui/matrix_model.py`): it names a row nobody named, and gives a row waiting on
its device's name the caption to draw until that name arrives. The Builder
resolves the same way for its page review (`resolveMatrixAxis` in
`matrixModel.ts`), and the panel draws the name (`entryLabel` in
`web/panel/modules/matrix.js`), whichever of its three styles it draws in.

One corpus, `tests/fixtures/matrix_naming_cases.json`: the server resolves
each case, the Builder must resolve it to the same entries, and the panel,
given the server's entries and the case's readings, must draw the names the
case expects.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.ui.matrix_model import resolve_axis
from tests import gates

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "fixtures" / "matrix_rules_harness.cjs"
PANEL_MATRIX = ROOT / "openavc" / "web" / "panel" / "modules" / "matrix.js"
BUILDER_MODEL = (
    ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder" / "matrixModel.ts"
)
NODE_MODULES = ROOT / "openavc" / "web" / "programmer" / "node_modules"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "matrix_naming_cases.json").read_text(encoding="utf-8"),
)["cases"]
NAMES = [c["name"] for c in CASES]


def _resolved(case: dict) -> list[dict]:
    return resolve_axis({case["axis"]: case["spec"]}, case["axis"])


@pytest.fixture(scope="module")
def answers() -> dict:
    """The panel's names and the Builder's entries for every case."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    if not (NODE_MODULES / "esbuild").is_dir():
        gates.skip_or_fail(gates.NODE, "esbuild not installed (run `npm ci` in openavc/web/programmer)")
    naming = [{**case, "entries": _resolved(case)} for case in CASES]
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_MATRIX), str(BUILDER_MODEL)],
        input=json.dumps({"naming": naming}), capture_output=True, text=True, timeout=120,
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    return {
        "names": dict(zip(NAMES, out["panel"]["names"], strict=True)),
        "resolved": dict(zip(NAMES, out["builder"]["resolved"], strict=True)),
    }


@pytest.mark.parametrize("case", CASES, ids=NAMES)
def test_the_panel_draws_the_name_the_corpus_says(case: dict, answers: dict) -> None:
    assert answers["names"][case["name"]] == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=NAMES)
def test_the_builder_resolves_each_row_as_the_server_does(case: dict, answers: dict) -> None:
    assert answers["resolved"][case["name"]] == _resolved(case)


def test_the_corpus_reaches_every_part_of_the_rule() -> None:
    """Typed, reported and positional names, a reading absent and present,
    both axes, and both ways of writing an axis."""
    specs = [c["spec"] for c in CASES]
    assert {c["axis"] for c in CASES} == {"sources", "destinations"}
    assert any(isinstance(s, dict) for s in specs) and any(isinstance(s, list) for s in specs)
    assert any(c["readings"] for c in CASES) and any(not c["readings"] for c in CASES)
    expected = [name for c in CASES for name in c["expected"]]
    assert any(name.startswith(("In ", "Out ")) for name in expected)
    assert any(name in {r for c in CASES for r in map(str, c["readings"].values())} for name in expected)
