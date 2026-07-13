"""Regression tests for the project-store save retry contract (projectStoreSave).

projectStore promises that a caller which `await save()` (DeviceView bulk
enable/disable, ProjectView) sees completion of the actual underlying write.
The old performSave broke that on a transient failure: it scheduled the retry
on a detached setTimeout and returned, so the awaited chain resolved after the
FIRST failed attempt while a retry was still pending. It also re-chained a fresh
save whenever the store was dirty — including after a failed save — so a
persistent failure retried forever.

The save write loop is now the pure, injectable runSaveWithRetry, exercised here
via the esbuild-on-the-fly harness with fake deps (no real network, no real
backoff waits). Skips when the Node toolchain or esbuild is absent rather than
failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_project_store_save.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "project_store_save_harness.cjs"
HELPERS = OPENAVC_ROOT / "web" / "programmer" / "src" / "store" / "projectStoreSave.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "project store save harness missing"
    if not HELPERS.is_file():
        return "projectStoreSave.ts missing"
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
            f"project store save harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "m307_success_first_try",
    "m307_retries_then_succeeds",
    "m307_persistent_failure_stops",
    "m307_conflict_no_retry",
    "m307_edit_during_save_keeps_dirty",
    "m307_noop_when_no_project",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_project_store_save(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
