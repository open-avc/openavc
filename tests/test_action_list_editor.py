"""Behaviour tests for the shared action list in the Programmer IDE.

A button's press actions and a slider's on-change actions were edited by two
separate components that each spelled out the same list mechanic -- render a
picker per action, add, remove, renumber -- and having spelled it twice they had
drifted into offering different things for the same action: the slider side
could Test one and could not reorder it, the button side could reorder and could
not Test. Same picker, same command, two different sets of affordances
depending on which control you happened to select.

The two editors stay separate, because a button has press styles (tap, toggle,
hold-repeat, tap/hold) and a slider's "on change" does not. What they share is
`components/shared/ActionListEditor.tsx`, and both now offer both capabilities.

So the scenarios below are deliberately written in pairs -- each capability
asserted on BOTH editors -- and a pair going half-red is exactly the regression
this guards. They run the real components in a jsdom document via
`tests/fixtures/action_list_harness.cjs` (esbuild + react-dom), with the action
picker and the API/store modules stubbed: what is under test is the list, not
what a picker draws. The stub records calls, so "Test actually sends" stays an
assertion.

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

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "action_list_harness.cjs"
LIST_TSX = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "shared" / "ActionListEditor.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    for pkg in ("esbuild", "jsdom", "react-dom"):
        if not (NODE_MODULES / pkg).is_dir():
            return f"{pkg} not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "action list harness missing"
    if not LIST_TSX.is_file():
        return "ActionListEditor.tsx missing"
    return None


@pytest.fixture(scope="module")
def action_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(LIST_TSX)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(f"action list harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# The capability pairs. Both halves of each pair must hold, which is the whole
# point: one of them was missing before the list was shared.
PAIRS = [
    ("slider_change_actions_can_be_tested", "button_press_action_can_be_tested"),
    ("slider_change_actions_can_be_tested", "button_extra_actions_can_be_tested"),
    ("slider_change_actions_can_be_reordered", "button_extra_actions_can_be_reordered"),
]

SCENARIOS = [
    # Test, which the button side did not have.
    "slider_change_actions_can_be_tested",
    "button_press_action_can_be_tested",
    "button_extra_actions_can_be_tested",
    "a_half_built_command_offers_no_test",
    "a_blocked_dollar_ref_reports_instead_of_sending",
    # Reordering, which the slider side did not have.
    "slider_change_actions_can_be_reordered",
    "slider_change_actions_can_be_moved_up",
    "button_extra_actions_can_be_reordered",
    "the_ends_of_the_list_have_no_useless_arrows",
    # The rest of the mechanic, now spelled once.
    "a_lone_action_is_not_numbered",
    "several_actions_are_numbered_from_one",
    "button_extras_are_numbered_after_the_primary",
    "removing_one_of_several_keeps_the_rest",
    "remove_binding_clears_the_whole_slot",
    "adding_appends_an_empty_action",
    "a_half_built_action_cannot_be_added_to",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_action_list(action_results: dict, scenario: str) -> None:
    result = action_results.get(scenario)
    assert result is not None, f"harness did not run {scenario}: {sorted(action_results)}"
    assert result["pass"], result["detail"]


@pytest.mark.parametrize("slider,button", PAIRS)
def test_both_editors_offer_the_same_capability(
    action_results: dict, slider: str, button: str
) -> None:
    """Half a pair passing is the drift this item existed to remove."""
    assert action_results[slider]["pass"] and action_results[button]["pass"], (
        f"{slider}={action_results[slider]} vs {button}={action_results[button]}"
    )


def test_every_scenario_is_named(action_results: dict) -> None:
    """A harness scenario nobody asserts is a test that cannot fail the suite."""
    assert sorted(action_results) == sorted(SCENARIOS)
