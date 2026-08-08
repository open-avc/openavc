"""Regression tests for RestartProgressDialog's poll logic.

Two fixes:
- L-171: the dialog's cancellation flag was a shared useRef reset to false at the
  top of every effect run, so when targetUrl/expectsNewCert changed a superseded
  run's guards stopped cancelling and it could re-POST restart or navigate to a
  stale URL. It now uses a boolean local to each effect closure.
- L-172: the cert-error heuristic flipped after 5 consecutive fetch failures
  alone, so a slow-but-healthy restart (server still rebinding) was misread as
  the browser rejecting the new cert, misdirecting the user to install a CA. The
  decision (shouldEnterCertError) now also requires polling to have run past the
  normal restart window.

L-172 is the pure shouldEnterCertError, exercised via the esbuild harness. L-171
is inline React effect-closure logic (no pure seam), so it's pinned at the
source. Skips when the Node toolchain or esbuild is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_restart_poll_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "restart_poll_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "shared" / "restartPollHelpers.ts"
)
DIALOG = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "shared" / "RestartProgressDialog.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "restart poll helpers harness missing"
    if not HELPERS.is_file():
        return "restartPollHelpers.ts missing"
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
            f"restart poll helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "l172_cert_error_when_persistent",
    "l172_no_cert_error_too_early",
    "l172_no_cert_error_very_early",
    "l172_no_cert_error_below_threshold",
    "l172_no_cert_error_without_new_cert",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_should_enter_cert_error(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


# --- L-171 source pins: per-run cancellation, not a shared ref ---------------

def test_cancellation_is_a_local_closure_flag() -> None:
    src = DIALOG.read_text(encoding="utf-8")
    assert "let cancelled = false" in src, (
        "each effect run must own its cancellation flag (a local boolean)"
    )
    assert "useRef(false)" not in src, (
        "the shared useRef cancellation flag must be gone — a superseded run "
        "reset it to false and un-cancelled itself"
    )
    assert "cancelled.current" not in src, (
        "no reference to a shared ref's .current — use the closure boolean"
    )


def test_cleanup_cancels_this_run() -> None:
    src = DIALOG.read_text(encoding="utf-8")
    assert "cancelled = true" in src, (
        "the effect cleanup must set this run's cancelled flag to true"
    )
