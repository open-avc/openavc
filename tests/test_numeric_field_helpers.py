"""Regression tests for the UI Builder numeric property-field parsers
(numericField.ts).

The property editors are React with no jsdom-loadable entry point, so these
exercise the pure parsers by bundling numericField.ts on the fly with the
esbuild already in web/programmer/node_modules and asserting on the results.
Like the other frontend-logic suites it skips when the Node toolchain or
esbuild isn't present rather than failing the Python-only CI gate.

Covers the clear-writes-zero bug fixed in BasicProperties.tsx: numeric
property inputs parsed with Number(v) — Number("") is 0 — so clearing a
Min/Max/Step/Digits field to retype committed a literal 0 (a keypad with
digits=0, a slider with step=0). Clearing now unsets the property so the
runtime default applies, and the inputs show the default as a placeholder.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_numeric_field_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "numeric_field_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "ui-builder" / "PropertySections"
    / "numericField.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "numeric field helpers harness missing"
    if not HELPERS.is_file():
        return "numericField.ts missing"
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
            f"numeric field helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "empty_unsets_not_zero",
    "whitespace_unsets",
    "zero_stays_zero",
    "garbage_unsets",
    "numbers_parse",
    "int_truncates_and_unsets",
    "commit_clamps_once_empty_stays_empty",
    "live_commit_only_when_no_correction_needed",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_numeric_field_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
