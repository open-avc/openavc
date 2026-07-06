"""Regression tests for the UI Builder grid/id/rename helpers (uiBuilderHelpers.ts).

The UI Builder is React/TypeScript with no jsdom-loadable entry point, so these
exercise the pure helpers by transpiling uiBuilderHelpers.ts on the fly with the
esbuild already in web/programmer/node_modules and asserting on the results.
Like the colorUtils suite, they skip when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate. Run them locally after
`npm ci` in web/programmer; `node` ships on the CI runners.

Covers the audit findings fixed in the UIBuilderView.tsx group:
  H-038 clampOriginToGrid, M-077 findFreeGridPosition, L-051 pointerToCell,
  H-039 duplicateElementInPage reserved ids, L-052 renameElement array identity.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_ui_builder_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ui_builder_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "ui-builder" / "uiBuilderHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "ui builder helpers harness missing"
    if not HELPERS.is_file():
        return "uiBuilderHelpers.ts missing"
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
        raise AssertionError(f"ui builder helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h038_clamp_fits",
    "h038_clamp_overflow_right",
    "h038_clamp_overflow_bottom",
    "h038_clamp_min",
    "m077_free_empty",
    "m077_free_avoid_overlap",
    "m077_free_drop_down",
    "m077_free_too_big_fallback",
    "l051_ptc_basic",
    "l051_ptc_center",
    "l051_ptc_pad_corrects_edge",
    "h039_dup_reserved_skips_master",
    "l052_rename_preserves_untouched",
    "l052_rename_rewrites_referencing",
    "h086_validate_array_device",
    "h086_validate_array_navigate",
    "h086_validate_array_change_macro",
    "h086_validate_legacy_object",
    "h086_validate_valid_refs_pass",
    "h086_removepage_scrubs_arrays",
    "m143_duplicate_rewrites_self_ref",
    "m143_duplicate_page_rewrites_sibling_refs",
    "m143_duplicate_page_respects_reserved",
    "m144_demote_collision_renamed",
    "m144_demote_no_collision_keeps_id",
    "m144_promote_collision_renamed",
    "m144_promote_no_collision_keeps_id",
    "l087_value_map_recursion",
    "l088_out_of_bounds_ids",
    "m231_shrink_clamps_out_of_bounds",
    "m231_identity_when_fits",
    "m231_span_shrinks_to_grid",
    "l142_scrub_identity_when_untouched",
    "l142_scrub_new_when_changed",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ui_builder_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
