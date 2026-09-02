"""Regression tests for the Updates view decision helpers (updatesHelpers.ts).

The Updates view is React, so the testable decisions live in pure helpers
exercised by bundling the real updatesHelpers.ts with the esbuild already in
openavc/web/programmer/node_modules (tests/fixtures/updates_helpers_harness.cjs).
Skips when the Node toolchain is absent rather than failing the Python-only
CI gate.

Covers the audit findings fixed in the UpdatesView.tsx group: the completion
toast distinguishes rollback from update (in-flight action, semver-direction
fallback); a restart that changes nothing is detected instead of hanging the
progress modal; history labels render rollbacks with the real target version
(legacy literal-"rollback" entries included), and a row whose update was
applied and then reverted reads as reverted rather than as a success.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "updates_helpers_harness.cjs"
HELPERS = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "views" / "updatesHelpers.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "updates helpers harness missing"
    if not HELPERS.is_file():
        return "updatesHelpers.ts missing"
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
            f"updates helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "semver_lt",
    "outcome_updated",
    "outcome_rollback_by_action",
    "outcome_rollback_by_direction",
    "outcome_same_version_restart",
    "outcome_null_non_restart",
    "outcome_null_first_mount",
    "history_update_label",
    "history_rollback_label",
    "history_legacy_rollback_label",
    "history_rollback_unknown_target",
    "history_reverted_update",
    "history_successful_update",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_updates_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
