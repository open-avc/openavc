"""Regression tests for the Driver Builder state-variable editor helpers
(stateVariableHelpers.ts).

The editor is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling stateVariableHelpers.ts on the fly with the esbuild
already in openavc/web/programmer/node_modules and asserting on the results. Like the
other frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the two silent-data-loss bugs fixed in StateVariableEditor.tsx:
nextStateVariableName skips every existing name so add/delete/add never
overwrites a configured variable, and applyStateVarTypeChange computes the new
var def as one atomic object so a type switch no longer reverts via
stale-snapshot multi-writes (which also left type-incompatible bounds behind).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_state_variable_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "state_variable_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "driver-builder"
    / "stateVariableHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "state variable helpers harness missing"
    if not HELPERS.is_file():
        return "stateVariableHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"state variable helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "add_first_variable",
    "add_sequential",
    "add_after_delete_no_overwrite",
    "add_alongside_custom_names",
    "type_change_applies",
    "leaving_numeric_drops_bounds",
    "leaving_enum_drops_values",
    "numeric_to_numeric_keeps_bounds",
    "numeric_to_float_keeps_bounds",
    "type_change_keeps_help",
    "atomic_beats_stale_sequential",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_state_variable_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
