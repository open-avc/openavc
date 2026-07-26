"""Regression tests for button press-action removal (buttonBindingHelpers.ts).

A button's ``press`` binding is an array of actions; the runtimes (panel
engine and control surfaces) fire every entry in order in tap mode, and the
mode/toggle/hold config rides on ``press[0]``. The shared binding editor's
Remove button rebuilt ``press[0]`` from the config fields alone: with no
config (a tap button) that produced an empty object and the whole binding —
including every additional action — was nulled out; with config present
(toggle / tap-hold) the extras were left stranded behind an action-less
primary, invisible in the editor (the extras UI is tap-only) but still fired
by the runtimes. Removing the primary now promotes the next additional
action instead, keeping the config on ``press[0]``.

Two layers: the harness bundles the real ``buttonBindingHelpers.ts`` with
the esbuild in ``web/programmer/node_modules`` and replays the edit flows
(skips when the Node toolchain is absent rather than failing the
Python-only CI gate), and a source-level check pins ButtonBindingEditor.tsx
to delegating the press rebuild to the helper so the lossy inline rebuild
can't quietly come back.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_button_binding_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "button_binding_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "shared" / "buttonBindingHelpers.ts"
)
EDITOR_TSX = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "shared" / "ButtonBindingEditor.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "button binding harness missing"
    if not HELPERS_TS.is_file():
        return "buttonBindingHelpers.ts missing"
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
            f"button binding harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The two defects: extras discarded (tap), extras stranded (non-tap).
    "remove_primary_promotes_next_action",
    "remove_promoted_action_keeps_mode_config",
    # Contract guards.
    "remove_last_action_clears_binding",
    "remove_on_action_keeps_toggle_config",
    "edit_action_keeps_config_and_extras",
    "press_entry_splits_config_from_action",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_press_action_removal(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


def test_editor_delegates_press_rebuild_to_helper() -> None:
    """ButtonBindingEditor must rebuild press via pressAfterActionEdit.

    An inline rebuild here is how the Remove path came to null out the
    binding (dropping the additional actions) whenever press[0] carried no
    mode config.
    """
    source = EDITOR_TSX.read_text(encoding="utf-8")
    assert "pressAfterActionEdit(press, extraActions, value)" in source
    assert "hasContent" not in source, (
        "inline press rebuild is back in ButtonBindingEditor; route it "
        "through pressAfterActionEdit so removal keeps the extra actions"
    )
