"""Behaviour tests for the shared anchored panel in the Programmer IDE.

Five pickers each carried their own copy of "measure the trigger, decide
whether to flip up, place a fixed panel, close on an outside click or scroll" --
the state-key picker, the device-property picker, the param combobox, the colour
swatch and the surface preset list. The copies had drifted: two different
flip-up thresholds, two different width floors, and only two of the five
clamped the panel back into the viewport, which is the one that matters because
these triggers sit in the narrow right-docked properties pane.

They all run `useAnchoredPanel` now, so these are the rules the whole IDE
inherits at once. They run the real component in a jsdom document via
`tests/fixtures/anchored_panel_harness.cjs` (esbuild + react-dom), driven
through `ParamCombobox` -- the one caller with no store or API dependency.

One of them guards a defect the merge found rather than introduced: every copy
closes on a capture-phase scroll, but four ignored scrolls that started inside
the panel and the combobox did not, so a list longer than the panel could not be
scrolled to the bottom.

Two more guard the panels that are NOT list dropdowns. A colour wheel is
whatever size a colour wheel is, so its panel is measured rather than estimated
from padding and border by hand -- and the estimate is what the clamp runs off,
so guessing small leaves the panel hanging off the very edge it was clamped away
from. And a combobox keeps its input's width, because the 320px floor a state
key list wants would hang a dropdown wider than the field off every param in the
properties pane.

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

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "anchored_panel_harness.cjs"
PANEL_TSX = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "shared" / "AnchoredPanel.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    for pkg in ("esbuild", "jsdom", "react-dom"):
        if not (NODE_MODULES / pkg).is_dir():
            return f"{pkg} not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "anchored panel harness missing"
    if not PANEL_TSX.is_file():
        return "AnchoredPanel.tsx missing"
    return None


@pytest.fixture(scope="module")
def panel_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_TSX)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(f"anchored panel harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # Closing rules.
    "scrolling_the_list_keeps_it_open",
    "scrolling_the_page_closes_it",
    "click_outside_closes",
    "click_inside_keeps_it_open",
    # Horizontal placement.
    "clamped_into_the_viewport_at_the_right_edge",
    "unclamped_when_there_is_room",
    # Vertical placement.
    "flips_up_near_the_bottom",
    "opens_downward_when_there_is_room",
    # Why the flip-up threshold is per-panel rather than one constant.
    "a_short_panel_does_not_flip_when_it_fits",
    "a_list_panel_does_flip_in_the_same_spot",
    # An intrinsically-sized panel is measured, never estimated.
    "an_intrinsic_panel_is_clamped_by_its_measured_width",
    "a_combobox_panel_stays_its_input_width",
    # The two call sites that share only the positioning, executed at last.
    "the_colour_popover_opens_on_the_shared_panel",
    "the_surface_preset_list_opens_on_the_shared_panel",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_anchored_panel(panel_results: dict, scenario: str) -> None:
    result = panel_results.get(scenario)
    assert result is not None, f"harness did not run {scenario}: {sorted(panel_results)}"
    assert result["pass"], result["detail"]


def test_every_scenario_is_named(panel_results: dict) -> None:
    """A harness scenario nobody asserts is a test that cannot fail the suite."""
    assert sorted(panel_results) == sorted(SCENARIOS)
