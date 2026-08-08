"""Regression tests for the surface configurator's deck helpers.

The plugin surface editor keeps a second copy of rules the plugin runtime
also implements. A page exists because something references it, not because
anything declares one; a navigate action can sit in a list, in a lone action
object, or nested inside a toggle's ``off_action`` / ``hold_action``; and
"start from the current zones" has to reproduce exactly the strip the
runtime draws from the dials on its own. When those two copies disagree the
editor shows a deck that is not the deck, which is the kind of thing nobody
notices until a page vanishes from the tab row.

None of it was testable before: the logic lived inside a 6,470-line
component file, so reaching it meant bundling React, the project store and
the REST client. Splitting that file put these in
``components/plugins/surface/deckHelpers.ts`` — plain functions over a
config object — and this is the test that split bought.

Like the other TypeScript harnesses this skips when Node/esbuild aren't
installed, and fails instead when a run promised them
(``OPENAVC_REQUIRE_NODE=1``). Run it locally after ``npm ci`` in
openavc/web/programmer.
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

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "surface_deck_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "plugins"
    / "surface"
    / "deckHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "deck helpers harness missing"
    if not HELPERS_TS.is_file():
        return "deckHelpers.ts missing"
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
            f"deck helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # Pages are emergent: a name, a paging rule or a navigate target is
    # enough to keep one alive.
    "empty_view_is_one_page",
    "button_page_counts",
    "missing_page_is_page_zero",
    "page_name_counts",
    "auto_page_rule_counts",
    "navigate_target_counts",
    "relative_navigate_ignored",
    "highest_reference_wins",
    # Every place a navigate action can hide.
    "navigate_walk_reaches_every_slot",
    "navigate_walk_counts_pages",
    "has_any_navigate_true",
    "has_any_navigate_false",
    "single_action_object",
    "malformed_config_survives",
    # The zone seed has to match the strip the runtime draws.
    "one_zone_per_dial",
    "zone_carries_dial_fields",
    "fader_flag_moves_to_adjust",
    "adjust_is_copied_not_shared",
    "original_dial_adjust_untouched",
    "press_falls_back_to_touch",
    "long_press_falls_back",
    "no_adjust_key_no_drag",
    "unassigned_dial_gets_blank_zone",
    # Network deck entries.
    "no_network_decks_empty",
    "non_array_empty",
    "entry_needs_a_host",
    "entry_key_defaults_port",
    "entry_key_uses_port",
    # Adding a virtual unit disturbs nothing already configured.
    "virtual_unit_appended",
    "virtual_unit_keeps_existing",
    "virtual_unit_returns_its_serial",
    "virtual_unit_keeps_rest_of_config",
    "virtual_unit_does_not_mutate",
    "virtual_unit_from_empty_config",
    # What an own layout replaces.
    "deck_sections",
    "surface_actions",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_helper_scenarios(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report '{scenario}'"
    assert helper_results[scenario] is True, f"scenario '{scenario}' failed"


def test_every_reported_scenario_is_listed(helper_results: dict) -> None:
    """A scenario added to the harness but not to SCENARIOS runs nowhere."""
    unlisted = sorted(set(helper_results) - set(SCENARIOS))
    assert not unlisted, f"harness reports scenarios no test asserts: {unlisted}"


def test_helpers_stay_react_free() -> None:
    """These are testable because they import nothing from React or the app.

    If an editor concern leaks back in here the harness stops being able to
    bundle it on its own, which is exactly how the logic became untestable
    the first time.
    """
    src = HELPERS_TS.read_text(encoding="utf-8")
    for banned in ("from \"react\"", "useState", "useEffect", "useCallback", "jsx", "<div"):
        assert banned not in src, f"deckHelpers.ts must stay React-free (found {banned!r})"
    imports = [ln for ln in src.splitlines() if ln.startswith("import ")]
    assert all("./types" in ln for ln in imports), (
        f"deckHelpers.ts should import only its own types, got: {imports}"
    )
