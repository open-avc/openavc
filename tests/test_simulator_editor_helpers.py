"""Regression tests for the Driver Builder Simulation tab helpers
(simulatorEditorHelpers.ts).

The editor is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling simulatorEditorHelpers.ts on the fly with the esbuild
already in web/programmer/node_modules and asserting on the results. Like the
other frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the Simulation tab bugs fixed in SimulatorEditor.tsx: the response
delay input snapped an authored 0 back to 0.05 (`|| 0.05`), the error-mode
behavior dropdown offered custom_state/disconnect which no simulator transport
reads, and error modes had no way to author the set_state payload the runtime
applies on injection (the type even modeled the field under the wrong name).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_simulator_editor_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "simulator_editor_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "driver-builder"
    / "simulatorEditorHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "simulator editor helpers harness missing"
    if not HELPERS.is_file():
        return "simulatorEditorHelpers.ts missing"
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
            f"simulator editor helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "delay_zero_is_stored",
    "delay_value_stored",
    "delay_empty_unsets",
    "delay_unset_keeps_other_delays",
    "delay_negative_unsets",
    "behavior_options_match_runtime",
    "behavior_state_only_removes_key",
    "behavior_set_normally",
    "set_state_uses_runtime_key",
    "add_entry_picks_first_unused",
    "add_entry_noop_when_all_used",
    "rename_keeps_value_and_order",
    "remove_last_entry_drops_set_state",
    "coerce_matches_declared_types",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_simulator_editor_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
