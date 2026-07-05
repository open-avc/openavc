"""Regression tests for the AI chat store (aiChatStore.ts).

The AI chat undo path had a cross-store desync: undoMessage/revertAll
saved the snapshot with no If-Match ETag (skipping optimistic concurrency
— a concurrent edit from another session could be clobbered) and never
updated useProjectStore, so the Project editor kept showing the pre-undo
project with a stale ETag; the next manual save then 409'd, and when the
editor was dirty the WS project.reloaded refetch was suppressed, leaving
the divergence in place silently. Restores now save against the server's
current ETag and force-reload the project store.

Also covered: a failed (onError) send left a dangling undo entry that
inflated the "Revert all N" count; optimistic message ids used bare
Date.now() (a sub-ms double-send corrupted both bubbles); and the
conversation list/select/delete paths surfaced raw 'AI API 500: {json}'
strings instead of the friendly copy the streaming path already maps.

Two layers: the harness bundles the real ``aiErrors.ts`` with the esbuild
in ``web/programmer/node_modules`` (skips when the Node toolchain is
absent), and source-level checks pin aiChatStore.ts to the fixed shapes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ai_errors_harness.cjs"
HELPERS_TS = OPENAVC_ROOT / "web" / "programmer" / "src" / "api" / "aiErrors.ts"
STORE_TS = OPENAVC_ROOT / "web" / "programmer" / "src" / "store" / "aiChatStore.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "ai errors harness missing"
    if not HELPERS_TS.is_file():
        return "aiErrors.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
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
            f"ai errors harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "limit_429",
    "subscription_402",
    "unavailable_503",
    "detail_unwrapped",
    "non_json_falls_back",
    "empty_detail_falls_back",
    "other_error_kept",
    "string_error_kept",
    "empty_uses_fallback",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_friendly_ai_error_scenarios(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report '{scenario}'"
    assert helper_results[scenario] is True, f"scenario '{scenario}' failed"


# ── Source-level pins ──────────────────────────────────────────────────────


def test_undo_saves_with_current_etag_and_resyncs_project_store() -> None:
    """Undo/revert must not skip optimistic concurrency or leave the
    Project editor on the pre-undo project + stale ETag."""
    src = STORE_TS.read_text(encoding="utf-8")
    assert "restoreSnapshot" in src
    assert re.search(r"saveProject\([^)]*current\._etag\)", src), (
        "the undo write must carry the server's current ETag (If-Match)"
    )
    assert "forceReload()" in src, (
        "after a restore the project store must refetch (project + ETag)"
    )
    # Both restore paths go through the shared helper.
    assert src.count("await restoreSnapshot(") == 2


def test_failed_send_cleans_up_its_undo_entry() -> None:
    src = STORE_TS.read_text(encoding="utf-8")
    on_error = src[src.index("onError: (message)"):]
    on_error = on_error[: on_error.index("},")]
    assert "undoStack" in on_error, (
        "onError must drop the undo entry pushed for the failed send — "
        "phantom entries inflate the 'Revert all N' count"
    )


def test_optimistic_ids_are_unique_per_send() -> None:
    src = STORE_TS.read_text(encoding="utf-8")
    assert not re.search(r"`(temp|stream)_\$\{Date\.now\(\)\}`", src), (
        "bare Date.now() ids collide on a sub-ms double-send"
    )
    assert "localMessageId(" in src


def test_double_send_race_is_guarded() -> None:
    """The pre-send snapshot await leaves a window where the input isn't
    disabled yet — a second send must drop, not corrupt the stream."""
    src = STORE_TS.read_text(encoding="utf-8")
    send_body = src[src.index("sendMessage: (text"):]
    send_body = send_body[: send_body.index("const userMsg")]
    assert re.search(r"if \(sending\) return", send_body)


def test_conversation_errors_use_friendly_copy() -> None:
    src = STORE_TS.read_text(encoding="utf-8")
    assert src.count("friendlyAIError(") >= 3, (
        "load/select/delete error paths must map AI API errors to friendly "
        "copy instead of surfacing raw JSON"
    )
    assert not re.search(r"error: String\(e\)", src)
