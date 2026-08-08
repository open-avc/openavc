"""Regression test for the Programmer SPA stream snapshot auth (api/streamsClient.ts).

The video stream Preview used to point a bare ``<img src>`` at the plugin's
snapshot endpoint. Native image loads carry none of the SPA's JS-managed
credentials (the fetch interceptor only wraps ``fetch``), so on any claimed
instance the request was a guaranteed 401 and the preview always failed.
``fetchSnapshot`` fetches the JPEG instead — the interceptor attaches the
Programmer credential to same-origin /api fetches — and returns an object URL
for the ``<img>``.

This bundles the real ``streamsClient.ts`` and ``auth.ts`` with the esbuild in
``openavc/web/programmer/node_modules`` and asserts the snapshot request rides the
installed interceptor with the credential attached, plus the error-path and
object-URL contract. Skips when the Node toolchain or esbuild is absent rather
than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_streams_client.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "streams_client_harness.cjs"
STREAMS_TS = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "api" / "streamsClient.ts"
AUTH_TS = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "api" / "auth.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "streams client harness missing"
    if not STREAMS_TS.is_file():
        return "api/streamsClient.ts missing"
    if not AUTH_TS.is_file():
        return "api/auth.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(STREAMS_TS), str(AUTH_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"streams client harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The snapshot request rides the auth interceptor
    "snapshot_carries_credential",
    "snapshot_requests_same_origin_api_path",
    # Response handling contract
    "snapshot_ok_returns_object_url",
    "snapshot_401_throws_api_error",
    # The credential-less URL helper stays gone
    "unauthenticated_img_url_export_gone",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_stream_snapshot_auth(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
