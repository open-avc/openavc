"""Regression tests for the Driver Builder's action rules (validateDriver.ts)
and the Actions editor's pure helpers (actionsEditorHelpers.ts).

A driver can promote commands to Quick Action buttons (``actions`` +
legacy ``quick_actions``) and declare a browser-reachable web interface
(``web_ui``). The loader validates all three at save time, so the Builder
mirrors those rules at author time: action ids must be unique, kinds and
availabilities must come from the contract tables, a command action must
resolve to a declared command, link URLs and a web_ui template only
substitute {host}/{port}/config placeholders, and visible_when conditions
need a key and a known operator. These tests bundle the real TypeScript with
the esbuild in web/programmer/node_modules and assert both the validator and
the editor helpers (quick_actions conversion, visible_when mode detection,
condition-value coercion). Skips when the Node toolchain or esbuild is
absent rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_driver_builder_actions.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "driver_builder_actions_harness.cjs"
BUILDER_DIR = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "driver-builder"
)
VALIDATOR = BUILDER_DIR / "validateDriver.ts"
HELPERS = BUILDER_DIR / "actionsEditorHelpers.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "driver builder actions harness missing"
    if not VALIDATOR.is_file():
        return "validateDriver.ts missing"
    if not HELPERS.is_file():
        return "actionsEditorHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def actions_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(VALIDATOR), str(HELPERS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"driver builder actions harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "actions_clean_ok",
    "action_missing_id_error",
    "action_duplicate_id_error",
    "action_unknown_kind_error",
    "action_bad_availability_error",
    "action_url_on_command_error",
    "action_link_empty_url_error",
    "action_link_no_url_ok",
    "action_command_unresolved_error",
    "action_command_field_resolves_ok",
    "action_no_commands_skips_resolution",
    "quick_actions_ok",
    "quick_action_unknown_error",
    "quick_action_blank_error",
    "visible_when_missing_key_error",
    "visible_when_unknown_operator_error",
    "visible_when_group_ok_empty_group_error",
    "web_ui_unknown_placeholder_warning",
    "web_ui_known_placeholders_ok",
    "link_url_unknown_placeholder_warning",
    "convert_quick_appends_and_skips",
    "convert_quick_edge_inputs",
    "visible_when_mode_matrix",
    "coerce_condition_value_matrix",
    "extra_keys_preserved",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_driver_builder_actions(actions_results: dict, scenario: str) -> None:
    assert scenario in actions_results, f"harness did not report {scenario}"
    outcome = actions_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
