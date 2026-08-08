"""Regression test for the assistant token-count footer (ai/ChatMessage.tsx).

The footer guarded its token-count span with
``{(message.inputTokens || message.outputTokens) && (...)}``. When a response
reported zero of both, ``0 || 0`` evaluated to ``0`` — and React renders a bare
``0`` as literal text, so a stray digit appeared in the footer. The fix wraps
the guard in ``Boolean(...)`` so a false-y count renders nothing.

This bundles the real ``ChatMessage.tsx`` with the esbuild in
``web/programmer/node_modules`` and server-renders it. The key case: a 0/0
message must produce the same markup as a no-counts message (a stray ``0``
means the bug is back). Skips when the Node toolchain or esbuild is absent
rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_chat_message_tokens.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "chat_message_tokens_harness.cjs"
CHAT_MESSAGE_TSX = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "ai" / "ChatMessage.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "chat-message tokens harness missing"
    if not CHAT_MESSAGE_TSX.is_file():
        return "ai/ChatMessage.tsx missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(CHAT_MESSAGE_TSX)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"chat-message tokens harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "footer_shows_counts",
    "zero_zero_no_stray",
    "no_counts_no_span",
    "partial_zero_shown",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_chat_message_tokens(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
