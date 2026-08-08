"""Regression tests for the UI Builder undo/redo store paths (uiBuilderStore.ts).

The stores are zustand and work headless, so these exercise the REAL
uiBuilderStore + projectStore (bundled together on the fly with the esbuild
already in openavc/web/programmer/node_modules) through their getState/setState API,
with the project store's debouncedSave replaced by a spy. Like the other
frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the two bugs fixed in uiBuilderStore.ts: undo/redo applied the
rollback patch via projectStore.update(), which only marks the project dirty
and never arms the save debounce — so flushSave and the beforeunload handler
no-op'd and a reload silently lost the undone state (disk kept the pre-undo
project while the canvas showed the post-undo one); and rolling back never
re-selected what it re-created, so redo of an add/paste (or undo of a
delete) left nothing selected and the Properties panel empty.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_ui_builder_undo.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ui_builder_undo_harness.cjs"
STORE_DIR = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "store"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "ui builder undo harness missing"
    if not (STORE_DIR / "uiBuilderStore.ts").is_file():
        return "uiBuilderStore.ts missing"
    return None


@pytest.fixture(scope="module")
def store_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(STORE_DIR)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"ui builder undo harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "undo_applies_patch_and_marks_dirty",
    "undo_schedules_save",
    "redo_schedules_save",
    "undo_of_add_drops_stale_selection",
    "redo_of_add_restores_selection",
    "undo_of_delete_restores_selection",
    "rollback_keeps_valid_selection",
    "undo_of_page_delete_selects_page",
    "redo_of_master_add_restores_selection",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ui_builder_undo(store_results: dict, scenario: str) -> None:
    assert scenario in store_results, f"harness did not report {scenario}"
    outcome = store_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
