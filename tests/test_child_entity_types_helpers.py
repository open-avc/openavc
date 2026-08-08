"""Regression tests for the Child Entity Types editor helpers
(childEntityTypesHelpers.ts).

The editor is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling childEntityTypesHelpers.ts on the fly with the esbuild
already in openavc/web/programmer/node_modules and asserting on the results. Like the
other frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the audit findings fixed in the ChildEntityTypesEditor.tsx group:
  H-117 applyChildVarTypeChange computes the new var def as one atomic object so
  a type switch no longer reverts via stale-snapshot multi-writes; H-118
  nextChildFieldId / nextChildTypeId skip every existing id so add/remove/add
  never overwrites a field; M-168 sanitize* + checkRename back the commit-on-blur
  rename (reject empty/collision with a reason, accept a no-op).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_child_entity_types_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "child_entity_types_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "driver-builder"
    / "childEntityTypesHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "child entity types helpers harness missing"
    if not HELPERS.is_file():
        return "childEntityTypesHelpers.ts missing"
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
            f"child entity types helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h118_first_field",
    "h118_sequential",
    "h118_skips_existing_no_overwrite",
    "h118_type_skips_existing",
    "h117_string_to_integer_keeps_type",
    "h117_leaving_numeric_drops_bounds",
    "h117_leaving_enum_drops_values",
    "h117_numeric_to_numeric_keeps_bounds",
    "numeric_to_float_keeps_bounds",
    "h117_atomic_beats_stale_sequential",
    "m168_sanitize_field",
    "m168_sanitize_type",
    "m168_rename_empty_rejected",
    "m168_rename_noop_is_ok",
    "m168_rename_collision_rejected",
    "m168_rename_valid_accepted",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_child_entity_types_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
