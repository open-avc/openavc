"""Regression tests for the Discovery view helpers (discoveryViewHelpers.ts).

The view is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling discoveryViewHelpers.ts on the fly with the esbuild
already in web/programmer/node_modules and asserting on the results. Like the
other frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the two Discovery view bugs: the hardcoded generic port labels (VNC /
HTTP alt) shadowed driver/catalog-supplied vendor labels for ports 5900 and
9090 (the merge now lets dynamic labels win), and the SNMP community payload
rule that backs the config fix — a blank input means "keep the stored
community" so the field is omitted from scan/save payloads instead of
echoing a placeholder back.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_discovery_view_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "discovery_view_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "discoveryViewHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "discovery view helpers harness missing"
    if not HELPERS.is_file():
        return "discoveryViewHelpers.ts missing"
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
            f"discovery view helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "port_label_dynamic_wins",
    "port_label_fallbacks_kept",
    "snmp_blank_omitted",
    "snmp_value_sent_verbatim",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_discovery_view_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
