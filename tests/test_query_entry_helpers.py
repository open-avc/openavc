"""Regression test for the shared query/on_connect entry model
(driver-builder/queryEntryHelpers.ts).

`buildQueryEntry` folds an entry's parts (send/address, each_child, when, args)
back into the simplest shape that can carry them. The render harness can't
exercise this (renderToStaticMarkup fires no events), so this drives the pure
function directly: OSC args force the `{address, args}` form, a chosen child
type drops args (each_child OSC is address-only), removing every arg collapses
back off the args form, and the readers pull the right field per shape.

This bundles the real ``queryEntryHelpers.ts`` with the esbuild in
``web/programmer/node_modules``. Skips when the Node toolchain or esbuild is
absent rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_query_entry_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "query_entry_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "queryEntryHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "query-entry-helpers harness missing"
    if not HELPERS_TS.is_file():
        return "driver-builder/queryEntryHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"query-entry-helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "bare",
    "gated",
    "each_child",
    "each_child_when",
    "args",
    "args_when",
    "each_child_drops_args",
    "empty_args_collapses_to_bare",
    "empty_args_collapses_to_gated",
    "querySend_reads_address",
    "queryWhen_reads_osc_when",
    "queryArgs_osc",
    "queryArgs_string_undefined",
    "queryArgs_gated_undefined",
    "isOscItem_true",
    "isOscItem_false_on_gated",
    "isGated_false_on_osc",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_query_entry_helpers(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
