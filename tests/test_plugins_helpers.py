"""Regression test for the Plugins view helper (pluginsView.helpers.ts).

The Programmer IDE used to decide a plugin was incompatible only when its
``status`` literally equalled ``"incompatible"`` — but the backend only sets
that status for project plugins it actually tried to start. A discovered but
unstarted incompatible plugin carries the truthful ``compatible: false`` flag
with some other status, so the badge / banner / Enable gating showed it as
compatible. ``isPluginIncompatible`` reads ``compatible`` (falling back to the
status string only when the flag is absent). This bundles the real helper with
the esbuild in web/programmer/node_modules and asserts that derivation. Skips
when the Node toolchain or esbuild is absent rather than failing the
Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_plugins_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "plugins_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "views"
    / "pluginsView.helpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "plugins helpers harness missing"
    if not HELPERS.is_file():
        return "pluginsView.helpers.ts missing"
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
            f"plugins helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "m174_compatible_false_is_incompatible",
    "m174_compatible_true_is_compatible",
    "m174_unstarted_incompatible_caught",
    "m174_status_fallback_incompatible",
    "m174_status_fallback_compatible",
    "m174_compatible_true_overrides_status",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_plugins_helpers(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
