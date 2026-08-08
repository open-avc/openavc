"""Regression tests for the state-key picker group labels (VariableKeyPicker).

The picker groups keys by source as a primary UX affordance, but the inline
label switch relabelled only device:/system/ui: groups — so plugin.* keys and
orphan ui.* keys (whose element isn't in the project) fell through to the
default "Project Variables" header, mislabelling where a key comes from. The
label mapping is now a pure helper (variableKeyPickerHelpers.groupLabel) that
covers every source, exercised here via the esbuild-on-the-fly harness like the
other frontend-logic suites; it skips when the Node toolchain or esbuild is
absent rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_variable_key_picker_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "variable_key_picker_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "shared"
    / "variableKeyPickerHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "variable key picker helpers harness missing"
    if not HELPERS.is_file():
        return "variableKeyPickerHelpers.ts missing"
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
            f"variable key picker helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "label_control",
    "label_variables_default",
    "label_device",
    "label_system",
    "label_ui_element",
    "label_trigger",
    "label_plugin",
    "label_orphan_ui",
    "plugin_was_mislabeled_before",
    "orphan_ui_was_mislabeled_before",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_variable_key_picker_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
