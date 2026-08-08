"""Regression tests for the shared copy-to-clipboard helper (clipboard.ts).

The IDE's copy buttons called ``navigator.clipboard.writeText`` directly.
That API only exists in a secure context — and the IDE ships with HTTPS
off, so reaching it from another machine over plain HTTP (the normal Pi /
mini-PC / Docker LAN setup) left ``navigator.clipboard`` undefined: every
copy button threw and copied nothing, the shared CopyButton still flashed
"Copied!", and the TLS-fingerprint copy's error toast never fired because
its ``.catch`` only covered promise rejections, not the synchronous throw.

Two layers: the harness bundles the real ``clipboard.ts`` with the esbuild
in ``web/programmer/node_modules`` and drives it under fake
navigator/document globals (skips when the Node toolchain is absent rather
than failing the Python-only CI gate), and a source-level check pins every
frontend copy site to the helper so a bare ``navigator.clipboard`` call
can't come back.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_clipboard_helper.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

PROGRAMMER_SRC = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src"
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "clipboard_helper_harness.cjs"
HELPERS_TS = PROGRAMMER_SRC / "components" / "shared" / "clipboard.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "clipboard harness missing"
    if not HELPERS_TS.is_file():
        return "clipboard.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"clipboard harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The defect: copying must work without the Clipboard API.
    "plain_http_copy_still_works",
    "rejected_clipboard_api_falls_back",
    "copy_failure_reports_false",
    # Contract guards.
    "clipboard_api_used_when_available",
    "selection_restored_after_fallback",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_copy_to_clipboard(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


def test_no_bare_clipboard_calls_in_frontend() -> None:
    """Every copy site must go through copyToClipboard.

    A direct ``navigator.clipboard`` call is undefined over plain HTTP —
    the copy silently dies (or false-succeeds) on the default deployment.
    Only the helper itself may touch the API.
    """
    offenders: list[str] = []
    copy_sites = 0
    for path in sorted(PROGRAMMER_SRC.rglob("*.ts*")):
        source = path.read_text(encoding="utf-8")
        if "navigator.clipboard" in source and path != HELPERS_TS:
            offenders.append(str(path.relative_to(PROGRAMMER_SRC)))
        if "copyToClipboard(" in source and path != HELPERS_TS:
            copy_sites += 1
    assert not offenders, (
        f"bare navigator.clipboard calls outside clipboard.ts: {offenders}"
    )
    # The known copy sites (CopyButton, Dashboard, System Settings, Driver
    # Builder) all route through the helper.
    assert copy_sites >= 4, (
        f"expected the copy sites to use copyToClipboard, found {copy_sites}"
    )
