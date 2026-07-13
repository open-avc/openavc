"""Regression tests for the macro trigger editor helpers (triggerHelpers.ts).

The editor is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling triggerHelpers.ts on the fly with the esbuild already
in web/programmer/node_modules and asserting on the results. Like the other
frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the two bugs fixed in TriggerEditor.tsx: the weekday toggle (and preset
switch) used parseInt on cron minute/hour fields, so a stepped or ranged
schedule was rebuilt as an invalid "NaN ..." cron that silently disabled the
trigger — cronFieldInt / cronWithDays now preserve non-simple fields; and the
event trigger editor always opened in Device Events regardless of the saved
pattern, mis-displaying the trigger and inviting an accidental overwrite —
detectEventCategory now picks the saved pattern's category.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_trigger_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "trigger_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "macros"
    / "triggerHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "trigger helpers harness missing"
    if not HELPERS.is_file():
        return "triggerHelpers.ts missing"
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
            f"trigger helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "field_plain_int_parses",
    "field_step_falls_back",
    "field_range_falls_back",
    "field_star_falls_back",
    "days_weekday_range",
    "days_star_is_all",
    "days_weekend_list",
    "days_malformed_empty",
    "rebuild_preserves_stepped_fields",
    "rebuild_preserves_day_of_month",
    "rebuild_sorts_days",
    "rebuild_malformed_falls_back",
    "preset_switch_on_stepped_valid",
    "category_device_event",
    "category_macro_event",
    "category_system_event",
    "category_unknown_is_custom",
    "category_deleted_device_is_custom",
    "category_no_pattern_defaults_device",
    "cron_alias_daily",
    "cron_alias_hourly",
    "cron_dow_name_range",
    "cron_dow_name_single",
    "cron_dow_name_list",
    "cron_month_name",
    "cron_reboot_rejected",
    "cron_name_wrong_field_rejected",
    "cron_bad_name_rejected",
    "cron_out_of_range_rejected",
    "cron_numeric_range_still_valid",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_trigger_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
