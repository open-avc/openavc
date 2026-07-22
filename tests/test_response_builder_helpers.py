"""Regression tests for the Driver Builder response editor helpers
(responseBuilderHelpers.ts).

The editor is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling responseBuilderHelpers.ts on the fly with the esbuild
already in web/programmer/node_modules and asserting on the results. Like the
other frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the response-editor bugs fixed in ResponseBuilder.tsx: renaming a
value-map key onto an existing key silently merged the two rows (one mapping
vanished), set:-shorthand rows displayed type String regardless of the state
variable's declared type (which is what the runtime actually coerces by), and
a Type chosen on a set:-form row was silently discarded on save because the
shorthand output has nowhere to carry it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_response_builder_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "response_builder_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "driver-builder"
    / "responseBuilderHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "response builder helpers harness missing"
    if not HELPERS.is_file():
        return "responseBuilderHelpers.ts missing"
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
            f"response builder helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "rename_collision_rejected",
    "rename_preserves_order_and_values",
    "rename_to_empty_rejected",
    "rename_noop_ok",
    "add_entry_guards_pending_draft",
    "set_capture_shows_declared_type",
    "set_static_shows_declared_type",
    "set_undeclared_defaults_string",
    "set_roundtrip_keeps_shorthand",
    "static_type_choice_survives_save",
    "capture_type_choice_survives_save",
    "matching_type_returns_to_shorthand",
    "mappings_form_stays_mappings",
    "child_set_rides_along",
    "child_id_long_form_renders_ref",
    "child_id_rebuild_shapes",
    "osc_child_id_shapes",
    "osc_child_prop_shapes",
    "osc_rebuild_carries_child_set",
    "json_rows_from_string_set",
    "json_rows_from_object_set",
    "json_rows_from_mappings",
    "json_minimal_string_form",
    "json_object_form_when_needed",
    "json_roundtrip_minimizes_and_preserves",
    "json_require_shapes",
    "json_duplicate_states_fall_back_to_mappings",
    "json_number_float_equivalence",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_response_builder_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
