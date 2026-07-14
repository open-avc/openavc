"""Regression test for hasUpdate() semver comparison (api/types.ts).

The community driver/plugin "Update available" check compared versions with
``installed.split('.').map(Number)``. Any pre-release or build suffix turned a
segment into NaN, and ``b[i] || 0`` coerced NaN to 0 — so an update to a
suffixed version was silently hidden (the Update button never appeared), and a
``+build`` suffix spuriously registered as a new version. The catalog's
SEMVER_RE allows ``-``/``+`` suffixes, so a contributor could ship one.

This bundles the real ``types.ts`` with the esbuild in
``web/programmer/node_modules`` and runs hasUpdate() across pre-release and
build-metadata cases. Skips when the Node toolchain or esbuild is absent rather
than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_has_update_semver.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "has_update_semver_harness.cjs"
TYPES_TS = OPENAVC_ROOT / "web" / "programmer" / "src" / "api" / "types.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "hasUpdate semver harness missing"
    if not TYPES_TS.is_file():
        return "api/types.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(TYPES_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"hasUpdate semver harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # Clean x.y.z behavior is unchanged.
    "clean_patch_newer",
    "clean_none_newer",
    "clean_equal",
    "clean_minor_numeric_order",
    "clean_missing_patch",
    # Pre-release / build suffixes (the bug).
    "prerelease_available_newer",
    "release_over_installed_prerelease",
    "prerelease_bump",
    "build_metadata_not_an_update",
    "installed_release_available_prerelease",
    # Guards.
    "empty_installed",
    "empty_available",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_has_update_semver(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
