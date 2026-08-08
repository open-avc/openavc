"""Regression tests for the panel-element config field router (panelElementConfig.ts).

The UI Builder Properties panel used to render every panel-element config_schema
field that wasn't boolean/select/number as one plain text box, so an author
declaring a state_key/device_ref/macro_ref field forced end users to type raw
IDs, and text vs string had no distinction. panelElementFieldKind now routes
ref types to their pickers and makes text a textarea / string a single-line
input, matching the plugin CONFIG_SCHEMA form. Bundled with the esbuild in
openavc/web/programmer/node_modules; skips when the Node toolchain is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_panel_element_config.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "panel_element_config_harness.cjs"
UTILS = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "ui-builder"
    / "PropertySections"
    / "panelElementConfig.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "panel element config harness missing"
    if not UTILS.is_file():
        return "panelElementConfig.ts missing"
    return None


@pytest.fixture(scope="module")
def kind_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(UTILS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"panel element config harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "m159_ref_types_get_pickers",
    "l094_text_is_textarea_string_is_input",
    "existing_widgets_unchanged",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_panel_element_config(kind_results: dict, scenario: str) -> None:
    assert scenario in kind_results, f"harness did not report {scenario}"
    outcome = kind_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
