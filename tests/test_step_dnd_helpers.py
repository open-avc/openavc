"""Regression tests for the macro step drag-reorder (stepDndHelpers.ts).

The step list's SortableContext items were stable per-object ids while each
rendered row registered an index-based ``step-${i}`` id. The two spaces only
coincide on a fresh editor: after one reorder the drag handler resolved the
row's index id against the permuted stable ids and moved the WRONG step, and
after switching macros (the editor is rendered unkeyed, so its id refs
survive) the spaces shared no entries at all and every drag silently
no-oped. The fix reads items, registered ids, and React keys from the one
``stepIds`` array, with the reorder logic extracted here.

Two layers: the harness bundles the real ``stepDndHelpers.ts`` with the
esbuild in ``web/programmer/node_modules`` and replays the drag flows (skips
when the Node toolchain is absent rather than failing the Python-only CI
gate), and a source-level check pins MacroEditor.tsx to rendering the shared
ids so the index-formula split can't quietly come back.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_step_dnd_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "step_dnd_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "macros" / "stepDndHelpers.ts"
)
MACRO_EDITOR_TSX = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "macros" / "MacroEditor.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "step dnd harness missing"
    if not HELPERS_TS.is_file():
        return "stepDndHelpers.ts missing"
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
            f"step dnd harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The two defects: wrong step moved after a reorder, dead drag after a
    # macro switch.
    "second_drag_after_reorder_moves_dragged_step",
    "drag_after_macro_switch_still_reorders",
    # Contract guards.
    "ids_follow_step_objects_across_reorder",
    "copied_steps_get_unique_ids",
    "self_and_unknown_drops_are_noops",
    "expanded_step_follows_moves",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_step_drag_reorder(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


def test_macro_editor_renders_shared_sortable_ids() -> None:
    """The step rows must register the same ids SortableContext sorts by.

    Rendering ``id={stepIds[i]}``/``key={stepIds[i]}`` is what keeps the
    sortable id space unified; an index-derived template id here recreates
    the wrong-step / dead-drag bug the helpers exist to prevent.
    """
    source = MACRO_EDITOR_TSX.read_text(encoding="utf-8")
    assert "items={stepIds}" in source
    assert "id={stepIds[i]}" in source
    assert "key={stepIds[i]}" in source
    template_ids = re.findall(r"(?:key|id)=\{`step-\$\{", source)
    assert not template_ids, (
        "MacroEditor renders index-formula sortable ids; they diverge from "
        f"SortableContext items after any reorder or macro switch: {template_ids}"
    )
