"""Regression tests for the project-import adoption contract (projectImport).

ProjectView's "Import" used to JSON.parse the file and immediately setProject
the raw object onto the live store, BEFORE any validation. A valid-JSON but
structurally-wrong file (a common mistake) then put a malformed project on the
store, so dependent views (e.g. DashboardView's project.devices.filter) threw
until navigation. The import now persists the parsed project THROUGH the server
first — the server validates the shape (Pydantic) and rejects a bad file — and
adopts it into the store (forceReload) only once the server accepts it.

The adopt-only-on-success ordering is the pure, injectable importParsedProject,
exercised here via the esbuild-on-the-fly harness with fake deps. Skips when the
Node toolchain or esbuild is absent rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_project_import.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "project_import_harness.cjs"
HELPERS = OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "projectImport.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "project import harness missing"
    if not HELPERS.is_file():
        return "projectImport.ts missing"
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
            f"project import harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "m308_success_adopts",
    "m308_validation_failure_never_adopts",
    "m308_conflict_message",
    "m308_passes_etag_to_save",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_project_import(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
