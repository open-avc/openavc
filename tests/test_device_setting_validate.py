"""Regression tests for the device-setting edit validator (deviceUtils.ts).

The Device Settings editor used to coerce a blank or mistyped numeric entry
to 0 (``parseInt(v) || 0``) and write it straight to the AV hardware, and
never enforced the definition's min/max/regex. validateSettingValue now
rejects invalid input with an actionable message before anything is sent;
these scenarios pin that contract (including that a legitimate 0 still
saves). Bundled with the esbuild in web/programmer/node_modules; skips when
the Node toolchain is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_device_setting_validate.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "device_setting_validate_harness.cjs"
UTILS = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "views"
    / "devices"
    / "deviceUtils.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "device setting validate harness missing"
    if not UTILS.is_file():
        return "deviceUtils.ts missing"
    return None


@pytest.fixture(scope="module")
def validate_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
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
            f"device setting validate harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "m156_blank_and_garbage_rejected",
    "m156_zero_and_negatives_valid",
    "m156_min_max_enforced",
    "m156_integer_vs_number_coercion",
    "m156_string_regex_and_boolean",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_device_setting_validate(validate_results: dict, scenario: str) -> None:
    assert scenario in validate_results, f"harness did not report {scenario}"
    outcome = validate_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
