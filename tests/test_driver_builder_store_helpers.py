"""Regression tests for the Driver Builder store helpers (driverBuilderStore.helpers.ts).

The Driver Builder store is React/Zustand with no jsdom-loadable entry point, so
these exercise the pure helpers by bundling driverBuilderStore.helpers.ts on the
fly with the esbuild already in web/programmer/node_modules and asserting on the
results. Unlike the uiBuilderHelpers suite this uses buildSync(bundle) rather
than transformSync, because importBlockers pulls in the real validateDriver.ts —
so the import-validation cases run against the actual validator the form editor
uses. Like the colorUtils suite it skips when the Node toolchain or esbuild
isn't present rather than failing the Python-only CI gate.

Covers the audit findings fixed in the driverBuilderStore.ts group:
  H-072/M-126 reconcileAfterSave (don't clobber edits made during the save
  await; keep selection consistent with the persisted id), M-127 makeLatestWins
  (overlapping list refreshes resolve newest-started-wins, not last-resolved),
  M-128 importBlockers (route imports through validateDriver instead of a 422),
  L-150 parseDriverDefinition (gate on a mapping so an imported list/scalar is
  rejected with a shape message, not cast through to a misleading 422).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_driver_builder_store_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "driver_builder_store_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "store" / "driverBuilderStore.helpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "driver builder store harness missing"
    if not HELPERS.is_file():
        return "driverBuilderStore.helpers.ts missing"
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
            f"driver builder store harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h072_reconcile_clean",
    "h072_reconcile_edited_keeps_dirty",
    "m126_reconcile_navigated_away_untouched",
    "m127_latest_single",
    "m127_latest_superseded",
    "m127_latest_independent",
    "m128_import_valid_no_blockers",
    "m128_import_missing_transport",
    "m128_import_missing_id",
    "m128_import_bad_id",
    "m128_import_deep_structural_error",
    "m128_import_warning_does_not_block",
    "m229_clone_fills_missing_state_variables",
    "m229_clone_preserves_shape_and_is_deep",
    "l150_json_object_ok",
    "l150_yaml_mapping_ok",
    "l150_json_array_rejected",
    "l150_yaml_sequence_rejected",
    "l150_scalar_rejected",
    "l150_null_rejected",
    "l150_unparseable_distinct_message",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_driver_builder_store_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
