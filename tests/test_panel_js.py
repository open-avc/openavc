"""Regression tests for web/panel/panel.js.

The panel is vanilla browser JS with no build step, so these run the real file
inside a jsdom window via a Node harness (tests/fixtures/panel_harness.cjs) and
assert on the resulting behaviour. Node + jsdom are optional dev dependencies
(jsdom lives in web/programmer/node_modules), so — exactly like the Playwright
e2e suite — these tests skip when the toolchain isn't present rather than
failing the Python-only CI gate. Run them locally after `npm ci` in
web/programmer; `node` ships on the CI runners.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_panel_js.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "panel_harness.cjs"
PANEL_JS = OPENAVC_ROOT / "web" / "panel" / "panel.js"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
JSDOM_DIR = NODE_MODULES / "jsdom"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not JSDOM_DIR.is_dir():
        return "jsdom not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "panel harness missing"
    return None


@pytest.fixture(scope="module")
def harness_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_JS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"panel harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    # The harness prints diagnostic [panel] warnings to stderr; results are on stdout.
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}") from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h001_matrix_reeval",
    "h002_gauge_reset",
    "h002_meter_reset",
    "h002_m005_slider_reset_and_drag",
    "h002_select_reset",
    "h002_textinput_reset",
    "h002_fader_reset",
    "h003_l007_lock_reconcile",
    "h004_plugin_broadcast_scope",
    "h005_action_capability_gate",
    "m001_l003_countdown",
    "m004_text_loose_compare",
    "l002_format_replace_all",
    "l004_max_reconnect_delay",
    "l005_status_led_active",
    "l009_audio_cap",
    "m006_meeting_baseline_persists",
    "m007_ui_override_revert",
    "m010_m011_css_sanitizers",
    "m002_m003_overlay_cleanup",
    "m008_l006_offline_handling",
    "l001_divide_by_zero_guards",
    "select_look_applies_matching_option_style",
    "select_look_registered_and_dispatched",
    "slider_fader_step_no_float_noise",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_panel_js_behaviour(harness_results: dict, scenario: str) -> None:
    assert scenario in harness_results, f"harness did not run {scenario}"
    result = harness_results[scenario]
    assert result["pass"], f"{scenario} failed: {result.get('error')}\n{result.get('stack')}"
