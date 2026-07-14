"""Regression tests for the Device Settings editor/setup helpers
(deviceSettingsHelpers.ts).

The editor + setup dialog are React with no jsdom-loadable entry point, so these
exercise the pure helpers by bundling deviceSettingsHelpers.ts on the fly with
the esbuild already in web/programmer/node_modules and asserting on the results.
Like the other frontend-logic suites it skips when the Node toolchain or esbuild
isn't present rather than failing the Python-only CI gate.

Covers the audit findings fixed in the DeviceSettingsEditor.tsx group:
  H-119 oscWriteOmitsValue flags an OSC write that would send no value (the
  editor now offers an args sub-editor + warning); H-120
  normalizeWriteForTransport strips stale cross-transport write fields so a
  transport switch can't mis-route the setting write; M-169 validateSettingValue
  enforces the min/max/regex constraints the setup dialog now honors.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_device_settings_helpers.py).
import pytest

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "device_settings_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "driver-builder"
    / "deviceSettingsHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "device settings helpers harness missing"
    if not HELPERS.is_file():
        return "deviceSettingsHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
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
            f"device settings helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h120_osc_keeps_only_osc",
    "h120_http_keeps_only_http",
    "h120_tcp_drops_foreign_keeps_send",
    "h120_foreign_keys_detected",
    "h119_osc_address_only_omits_value",
    "h119_osc_value_arg_sends_value",
    "h119_osc_literal_arg_omits_value",
    "h119_osc_value_in_address_ok",
    "next_setting_key_skips_existing",
    "sanitize_setting_key",
    "check_setting_rename",
    "m169_int_in_range_ok",
    "m169_int_below_min",
    "m169_int_above_max",
    "m169_int_not_a_number",
    "m169_regex_match_ok",
    "m169_regex_mismatch",
    "l169_empty_integer_rejected",
    "l169_empty_number_rejected",
    "l169_empty_string_allowed",
    "l169_empty_no_def_allowed",
    "m169_malformed_regex_does_not_block",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_device_settings_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
