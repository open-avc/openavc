"""Regression tests for the Theme Studio color utilities (colorUtils.ts).

The Theme Studio is React/TypeScript with no jsdom-loadable entry point, so these
exercise the pure color/contrast helpers by transpiling colorUtils.ts on the fly
with the esbuild already in openavc/web/programmer/node_modules and asserting on the
results. Like the panel.js jsdom suite, they skip when the Node toolchain or
esbuild isn't present rather than failing the Python-only CI gate. Run them
locally after `npm ci` in openavc/web/programmer; `node` ships on the CI runners.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_theme_studio_colors.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "color_utils_harness.cjs"
COLOR_UTILS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder" / "colorUtils.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "color utils harness missing"
    if not COLOR_UTILS.is_file():
        return "colorUtils.ts missing"
    return None


@pytest.fixture(scope="module")
def color_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(COLOR_UTILS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"color harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "m072_three_digit_hex",
    "m072_three_digit_to_hex6",
    "rgba_alpha",
    "transparent_null",
    "garbage_null",
    "h036_contrast_extreme",
    "h036_contrast_sixdigit",
    "h036_transparent_na",
    "h036_levels",
    "derive_surface_border",
    "css_var_fallbacks",
    "l053_is_light",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_color_util(color_results: dict, scenario: str) -> None:
    assert scenario in color_results, f"harness did not report {scenario}"
    outcome = color_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
