"""Regression tests for the simulator AudioPanel level scale (audioLevelScale).

The simulator's fallback AudioPanel normalized a device `level` to a 0-100 meter
with `level <= 1 && level >= 0 ? level*100 : level+100`, so any dB value in [0,1]
— notably 0 dB, a common nominal/unity level on a Biamp-style -100..12 scale —
was read as a 0..1 fraction and rendered a silent 0% meter (1 dB rendered a full
100%), and the slider write-back used the same ambiguous test and sent the wrong
scale back. The scale is now classified explicitly (fraction / dB / percent),
using a reported dB reading or a negative value to disambiguate dB, with one
classifier driving both the meter and the write-back.

Exercised via the esbuild-on-the-fly harness (audioLevelScale.ts is zero-import
pure math). Skips when the Node toolchain or esbuild is absent rather than
failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_audio_level_scale.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "audio_level_scale_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "simulator" / "src" / "components" / "devices" / "audioLevelScale.ts"
)
# esbuild lives in the programmer app's node_modules; it only strips TS syntax.
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "audio level scale harness missing"
    if not HELPERS.is_file():
        return "audioLevelScale.ts missing"
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
            f"audio level scale harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "m310_scale_db_when_hasdb",
    "m310_scale_fraction",
    "m310_scale_db_when_negative",
    "m310_scale_percent",
    "m310_zero_db_not_silent",
    "m310_one_db_not_full",
    "m310_fraction_half",
    "m310_fraction_bounds",
    "m310_db_bounds",
    "m310_percent_passthrough",
    "m310_denorm_fraction",
    "m310_denorm_percent",
    "m310_denorm_db",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_audio_level_scale(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
