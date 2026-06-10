"""Regression tests for the Live Test panel's pure helpers (liveTestHelpers.ts).

The panel's wire preview must show what the runtime actually sends: it now
routes by field presence exactly like configurable.py (an `address` key —
even empty — goes to the OSC sender), renders HTTP query_params on the
request line (the runtime appends them to the URL; the old preview dropped
them), and a command whose shape doesn't match the driver transport gets an
explanatory mismatch message instead of a bogus preview (the runtime's
senders refuse mismatched shapes). Bundled with the esbuild in
web/programmer/node_modules; skips when the Node toolchain is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_live_test_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "live_test_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "liveTestHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "live test helpers harness missing"
    if not HELPERS.is_file():
        return "liveTestHelpers.ts missing"
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
            f"live test helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "l091_preview_includes_query_params",
    "l091_preview_query_appends_to_existing",
    "l091_preview_no_query_params_unchanged",
    "m154_preview_routes_empty_address_as_osc",
    "m154_preview_osc_substitution",
    "m154_mismatch_detected",
    "m154_matched_shapes_pass",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_live_test_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
