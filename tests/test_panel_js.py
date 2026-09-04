"""Regression tests for openavc/web/panel/panel.js.

The panel is vanilla browser JS with no build step, so these run the real file
inside a jsdom window via a Node harness (tests/fixtures/panel_harness.cjs) and
assert on the resulting behaviour. Node + jsdom are optional dev dependencies
(jsdom lives in openavc/web/programmer/node_modules), so — exactly like the Playwright
e2e suite — these tests skip when the toolchain isn't present rather than
failing the Python-only CI gate. Run them locally after `npm ci` in
openavc/web/programmer; `node` ships on the CI runners.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_panel_js.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "panel_harness.cjs"
PANEL_JS = OPENAVC_ROOT / "openavc" / "web" / "panel" / "panel.js"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
JSDOM_DIR = NODE_MODULES / "jsdom"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not JSDOM_DIR.is_dir():
        return "jsdom not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "panel harness missing"
    return None


@pytest.fixture(scope="module")
def harness_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_JS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"panel harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    # The harness prints diagnostic [panel] warnings to stderr; results are on stdout.
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}") from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h001_matrix_reeval",
    "h002_gauge_reset",
    "h002_meter_reset",
    "h002_m005_slider_reset_and_drag",
    "h002_select_reset",
    "h002_textinput_reset",
    "h002_fader_reset",
    "h003_l007_lock_reconcile",
    "h004_plugin_broadcast_scope",
    "h005_action_grant_gate",
    "plugin_element_map_shape",
    "plugin_bridge_respects_edit_mode",
    # Custom controls: the same iframe machinery pointed at the project's ui/ tree.
    "custom_element_render",
    "custom_element_sends_nothing_without_a_grant",
    "custom_element_without_a_file",
    # Seeing the control while you build it.
    "custom_control_draws_in_the_designer",
    "custom_control_in_the_designer_reaches_nothing",
    "custom_control_init_says_it_is_the_designer",
    "custom_control_says_when_its_file_is_missing",
    "custom_control_reports_its_own_error",
    "custom_control_reloads_when_a_file_is_saved",
    # Per-element grants: what an iframe element may see and do.
    "grant_scopes_what_an_element_sees",
    "grant_scopes_the_opening_snapshot",
    "grant_switches_gate_macros_and_navigation",
    # Custom pages: the same frame, sized to the whole screen.
    "custom_page_renders_one_full_box_frame",
    "custom_page_draws_master_elements_over_it",
    "custom_page_without_a_file_falls_back_to_its_elements",
    "custom_page_grant_scopes_what_it_sees",
    "custom_overlay_page_renders_a_frame",
    "frame_navigation_tells_the_server",
    "frame_activity_resets_the_idle_timer_only_when_focused",
    "offline_overlay_outranks_an_open_dialog",
    "m001_l003_countdown",
    "m004_text_loose_compare",
    "l002_format_replace_all",
    "l004_max_reconnect_delay",
    "l005_status_led_active",
    "l009_audio_cap",
    "m006_meeting_baseline_persists",
    "m007_ui_override_revert",
    "m010_m011_css_sanitizers",
    "m002_m003_overlay_cleanup",
    "m008_l006_offline_handling",
    "l001_divide_by_zero_guards",
    "select_look_applies_matching_option_style",
    "select_look_registered_and_dispatched",
    "slider_fader_step_no_float_noise",
    # display_decimals reaches every element that draws a number.
    "label_display_decimals_rounds_a_numeric_value",
    "label_display_decimals_leaves_text_alone",
    "gauge_display_decimals",
    "display_decimals_out_of_range_cannot_throw",
    # Layout engine (percentage geometry, project format 0.8.0).
    "layout_elements_paint_above_page_background",
    "layout_placement_is_percentages",
    "layout_selected_by_orientation",
    "layout_falls_back_to_primary",
    "layout_inherits_merges_deltas",
    "layout_container_children_render_inside_parent",
    "layout_containers_nest",
    "layout_container_border_does_not_shift_its_contents",
    "layout_container_border_from_a_stylesheet_is_converted_too",
    "layout_parent_cycle_does_not_hang",
    "layout_aspect_lock_centres_within_its_box",
    "layout_unlocked_elements_take_their_box",
    "layout_overlay_uses_percentages",
    "layout_master_elements_place_by_orientation",
    "layout_snap_overlay_follows_page_snap",
    "layout_style_units_are_rem",
    "layout_vmin_override_hook",
    "layout_stylesheets_are_rem_except_hairlines",
    "layout_type_scale_calibration",
    # Power-user hooks (element.css_class + the ui.custom_css stylesheet).
    "power_css_class_on_element",
    "power_css_class_on_master",
    "power_css_class_under_aspect_lock",
    "power_css_class_tolerates_ragged_input",
    "power_custom_css_injected",
    "power_custom_css_replaced_and_cleared",
    # A control whose device is unreachable draws no value.
    "q213_fader_draws_no_value_for_an_unreachable_device",
    "q213_a_connectivity_flip_reaches_bindings_on_that_device",
    "q213_child_entity_keys_belong_to_their_parent_device",
    "q213_the_controls_that_report_the_fault_keep_working",
    "q213_a_state_look_asserts_nothing_while_the_device_is_gone",
    "q213_a_bound_label_keeps_its_sentence_and_loses_its_number",
    "q213_every_value_renderer_has_an_unknown_form",
    "q213_a_matrix_is_marked_one_destination_at_a_time",
    "q213_no_connected_key_is_not_a_claim_that_the_device_is_down",
    "q213_the_design_canvas_draws_the_live_look",
    "q213_an_element_is_unavailable_while_any_of_its_devices_is",
    # A refused command must not leave the operator's value standing.
    "q206_a_fader_announces_the_value_it_is_drawing",
    "q206_a_refused_change_puts_the_fader_back",
    "q206_the_memo_is_what_kept_the_wrong_value_standing",
    "q206_the_revert_lands_on_the_control_the_refusal_is_about",
    "q206_a_refused_slider_goes_back_even_though_it_still_has_focus",
    "q206_a_control_the_operator_is_still_holding_is_left_alone",
    "q206_a_refused_selection_goes_back",
    "q206_a_text_box_being_typed_into_is_not_yanked",
    "q206_an_accepted_command_leaves_the_control_alone",
    "q206_a_failed_macro_step_puts_its_control_back",
    # A toggle button says whether the thing it toggles is on.
    "q209_a_toggle_button_shows_whether_it_is_on",
    "q209_the_press_still_picks_its_half_from_the_same_state",
    "q209_the_lit_colour_follows_the_accent_and_stays_readable",
    "q209_a_toggle_says_the_words_it_was_given",
    "q209_a_toggle_naming_no_words_is_left_alone",
    "q209_an_appearance_binding_takes_the_whole_job",
    "q209_an_unreachable_device_is_not_a_toggle_that_is_off",
    "q209_a_frameless_toggle_is_ringed_rather_than_filled",
    "q209_a_toggle_with_no_state_key_indicates_nothing",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_panel_js_behaviour(harness_results: dict, scenario: str) -> None:
    assert scenario in harness_results, f"harness did not run {scenario}"
    result = harness_results[scenario]
    assert result["pass"], f"{scenario} failed: {result.get('error')}\n{result.get('stack')}"


def test_every_harness_scenario_is_listed(harness_results: dict) -> None:
    """The list above is hand-maintained, so a new scenario can be written and
    never run -- the suite stays green and the behaviour is untested. Only this
    direction matters; the other one is already covered per-scenario above.
    """
    unlisted = sorted(set(harness_results) - set(SCENARIOS))
    assert not unlisted, (
        "these harness scenarios are not in SCENARIOS, so they never run: "
        + ", ".join(unlisted)
    )
