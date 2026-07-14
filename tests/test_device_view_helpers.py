"""Regression tests for DeviceView's status-count helper (deviceViewHelpers).

The status-filter chips (All / Online / Offline / Orphaned) showed counts
computed from ALL devices, even when a search query was active, so the chip
numbers disagreed with the search-narrowed visible list. The counts are now
computed by computeStatusCounts over the search-filtered list, which the helper
supports by counting exactly the list it's handed (and reporting that list's
length as `total`).

Exercised via the esbuild-on-the-fly harness (deviceViewHelpers.ts is zero-import
pure logic). Skips when the Node toolchain or esbuild is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_device_view_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "device_view_helpers_harness.cjs"
HELPERS = OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "deviceViewHelpers.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "device view helpers harness missing"
    if not HELPERS.is_file():
        return "deviceViewHelpers.ts missing"
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
            f"device view helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "l174_counts_by_status",
    "l174_orphaned_precedence",
    "l174_no_state_is_offline",
    "l174_counts_only_the_passed_list",
    "l174_empty",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_compute_status_counts(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
