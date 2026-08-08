"""Behaviour tests for the shared Modal in the Programmer IDE.

The IDE used to carry twenty separate modal overlays: three shared components
that each kept their own copy of a focus trap, and seventeen hand-rolled at the
point of use. Of those seventeen, none trapped focus and only two could be
closed from the keyboard, and they sat on four different z-index tiers with the
picker popovers wedged between them — so whether a dropdown appeared above or
below the dialog you opened it from depended on which dialog you were in.

All twenty now go through `components/shared/Modal.tsx`, so these are the
behaviours the whole IDE inherits at once. They run the real component in a
jsdom document via `tests/fixtures/modal_harness.cjs` (esbuild + react-dom),
rather than pinning source text, because "Escape closes the top-most dialog
only" is not something you can read off a regex.

The harness earned its keep twice. Registering in a plain effect made the OUTER
modal look top-most (React runs a child's effects before its parent's), so
Escape and the Tab trap both went to the wrong dialog; the stack now orders by
document position. And because every modal listens on `document`, one Escape
reached all of them — closing the top one mid-dispatch let the next one answer
the same keypress, which live in the browser looked like Escape doing nothing
at all. A modal now marks the keypress it answered.

Like the other TypeScript harnesses these skip when Node/esbuild/jsdom aren't
installed, and fail instead when a run promised them (OPENAVC_REQUIRE_NODE=1).
Run them locally after `npm ci` in web/programmer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_modal_behaviour.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "modal_harness.cjs"
MODAL_TSX = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "shared" / "Modal.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not (NODE_MODULES / "esbuild").is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not (NODE_MODULES / "jsdom").is_dir():
        return "jsdom not installed (run `npm ci` in web/programmer)"
    if not (NODE_MODULES / "react-dom").is_dir():
        return "react-dom not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "modal harness missing"
    if not MODAL_TSX.is_file():
        return "Modal.tsx missing"
    return None


@pytest.fixture(scope="module")
def modal_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(MODAL_TSX)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(f"modal harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    # Structure and ARIA — on the panel, where a screen reader expects it.
    "aria_and_structure",
    "aria_labelledby",
    # Dismissal.
    "escape_closes",
    "escape_opt_out",
    "non_dismissible",
    "escape_only_top_most",
    "escape_returns_to_outer",
    "one_escape_is_consumed_once",
    "escape_survives_a_render_from_another_listener",
    "backdrop_click_closes",
    "panel_click_does_not_close",
    "backdrop_opt_out",
    # Focus.
    "initial_focus_first_focusable",
    "initial_focus_selector",
    "initial_focus_none",
    "select_on_focus",
    "focus_returns_on_close",
    "tab_wraps_forward",
    "tab_wraps_backward",
    "tab_trap_only_top_most",
    # The ladder.
    "z_index_ladder",
    "stack_empties_on_unmount",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_modal_behaviour(modal_results: dict, scenario: str) -> None:
    result = modal_results.get(scenario)
    assert result is not None, f"harness did not run scenario {scenario}"
    assert result["pass"], result["detail"]


def test_every_scenario_reported(modal_results: dict) -> None:
    """A scenario added to the harness must be listed here to be enforced."""
    assert set(modal_results) == set(SCENARIOS), (
        "harness scenarios and the pytest list have drifted: "
        f"{sorted(set(modal_results) ^ set(SCENARIOS))}"
    )
