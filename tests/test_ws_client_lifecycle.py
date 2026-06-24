"""Regression tests for the Programmer WebSocket client (wsClient.ts).

wsClient.ts is a browser module (real WebSocket + window), so these bundle it on
the fly with the esbuild already in web/programmer/node_modules and drive it
against a fake WebSocket + window in Node, asserting on the JSON results. Like
the other frontend-logic suites it skips when the Node toolchain or esbuild
isn't present rather than failing the Python-only CI gate.

Covers the audit findings fixed in the wsClient.ts group:
  H-116 disconnect() detaches handlers so the (still-async) onclose can't
  reschedule a reconnect and resurrect a connection the app tore down; M-166
  disconnect() resets per-session module state (everConnected, sendQueue,
  preOpenFailures) so stale commands aren't replayed and fresh-connect auth
  detection works again; M-167 a transient pre-open 1006 retries with backoff
  instead of immediately wiping valid credentials, logging out only once it
  persists past the retry budget.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_ws_client_lifecycle.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ws_client_harness.cjs"
WS_CLIENT = OPENAVC_ROOT / "web" / "programmer" / "src" / "api" / "wsClient.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "ws client harness missing"
    if not WS_CLIENT.is_file():
        return "wsClient.ts missing"
    return None


@pytest.fixture(scope="module")
def harness_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(WS_CLIENT)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"ws client harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h116_disconnect_does_not_resurrect",
    "m166_stale_command_not_replayed",
    "m166_everconnected_reset_reenables_auth",
    "m167_transient_1006_retries_not_logout",
    "m167_persistent_1006_logs_out_after_threshold",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ws_client_lifecycle(harness_results: dict, scenario: str) -> None:
    assert scenario in harness_results, f"harness did not report {scenario}"
    outcome = harness_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
