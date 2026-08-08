"""Regression test for getProject()'s worker-parse fallback (api/projectClient.ts).

For projects over 512 KB the Programmer parses the response body in a Worker.
If the worker errors, getProject() falls back to a main-thread parse. The old
fallback ran ``resolve(JSON.parse(text))`` inline inside ``worker.onerror`` — a
malformed body made JSON.parse throw *inside* the handler, so the exception
escaped and the promise never resolved or rejected. getProject() hung forever
and the IDE stayed in a perpetual loading state with no error surfaced.

This bundles the real ``projectClient.ts`` (with ``base.ts``) using the esbuild
in ``openavc/web/programmer/node_modules`` and drives getProject() against a fake fetch
and Worker: it asserts the promise now SETTLES (rejects) on a malformed body,
still resolves the fallback for a valid body, and resolves the worker-success
path. Skips when the Node toolchain or esbuild is absent rather than failing the
Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_project_client_get.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "project_client_get_harness.cjs"
PROJECT_CLIENT_TS = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "api" / "projectClient.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "project client harness missing"
    if not PROJECT_CLIENT_TS.is_file():
        return "api/projectClient.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(PROJECT_CLIENT_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"project client harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The core fix: a malformed body must settle the promise, not hang.
    "malformed_worker_error_settles_not_hang",
    # The fix must not break the valid-body fallback...
    "valid_worker_error_falls_back_parse",
    # ...nor the normal worker-success path.
    "worker_success_resolves",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_get_project_worker_fallback(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
