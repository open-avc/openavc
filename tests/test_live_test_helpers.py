"""Regression tests for the Live Test panel's pure helpers (liveTestHelpers.ts).

A command whose shape doesn't match the driver transport gets an explanatory
message instead of a send the runtime would refuse. That is declared-field
routing, not protocol interpretation, so it stays in the browser.

Building the wire does not. The panel used to preview it with a second,
TypeScript implementation of the send path, which disagreed with the runtime
about format specs, command framing, escape decoding and packet headers; the
panel now asks the server to build the command with the real driver, and this
harness checks the wire builder has not come back. What the preview shows is
covered by test_driver_command_dry_run.py and its corpus sweep.

Bundled with the esbuild in openavc/web/programmer/node_modules; skips when the Node
toolchain is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_live_test_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "live_test_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "liveTestHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "live test helpers harness missing"
    if not HELPERS.is_file():
        return "liveTestHelpers.ts missing"
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
    "m154_mismatch_detected",
    "m154_matched_shapes_pass",
    "no_wire_builder_exported",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_live_test_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
