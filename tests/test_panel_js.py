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
    # panel.js is an ES module; the harness links it inside jsdom's window
    # with vm.SourceTextModule, which Node keeps behind this flag.
    proc = subprocess.run(
        ["node", "--experimental-vm-modules", str(HARNESS), str(PANEL_JS)],
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
    "page_nav_opens_one_overlay_per_press",
    "page_nav_back_dismisses_without_leaving_the_page_behind",
    "custom_navigation_opens_one_overlay_per_request",
    "startup_uses_the_home_page_even_when_main_exists",
    "startup_keeps_main_when_it_is_home",
    "startup_honors_an_explicit_page_link",
    "startup_skips_overlays_when_choosing_home",
    "startup_does_not_reset_navigation_on_a_new_definition",
    "startup_recovers_an_invalid_page_link_to_home",
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
    "connection_status_hides_at_once_in_preview",
    "l001_divide_by_zero_guards",
    "select_look_applies_matching_option_style",
    "select_look_registered_and_dispatched",
    "slider_fader_step_no_float_noise",
    "level_slider_draws_follows_and_sends_as_today",
    "level_fader_draws_follows_and_sends_as_today",
    "level_controls_draw_their_first_reading_as_today",
    # A custom control and a plugin panel drawn, opened, spoken to and redrawn,
    # pinned whole (tests/fixtures/panel_frame_pins.json).
    "frames_custom_control_draws_opens_and_bridges_as_today",
    "frames_plugin_panel_draws_opens_and_bridges_as_today",
    "frames_read_the_status_of_their_own_request_as_today",
    # A list, a dropdown and a text field drawn, read, picked and typed into,
    # pinned whole (tests/fixtures/panel_choice_pins.json).
    "choices_draw_show_and_send_as_today",
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
    "fader_with_no_unit_prints_no_unit",
    "asleep_device_keeps_its_wake_control_live",
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
    # A live device that has simply not reported a reading yet -- which is now
    # every reading, until the device sends it.
    "q172_a_live_device_that_has_not_reported_draws_no_value",
    "q172_an_unreported_reading_is_not_the_unavailable_mark",
    # A refused command must not leave the operator's value standing.
    "q206_a_fader_announces_the_value_it_is_drawing",
    "q206_a_refused_change_puts_the_fader_back",
    "q206_the_memo_is_what_kept_the_wrong_value_standing",
    "q206_the_revert_lands_on_the_control_the_refusal_is_about",
    "slider_keeps_receiving_feedback_after_pointer_release",
    "slider_keeps_receiving_feedback_after_keyboard_change",
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
    "q139_a_run_that_ends_does_not_take_another_runs_claim",
    "q139_a_claim_is_given_back_when_the_run_it_started_ends",
    "q139_a_button_stays_busy_while_a_second_run_is_still_going",
    "macro_progress_keeps_its_step_through_a_wait",
    "macro_progress_never_counts_to_zero",
    "a_macro_named_like_an_object_built_in_is_an_ordinary_macro",
    "embedded_panel_posts_only_to_its_own_origin",
    "editor_messages_from_another_origin_are_ignored",
    "list_key_pattern_is_literal_apart_from_the_star",
    "rich_text_escapes_markup_before_adding_its_own",
    "q139_a_matrix_preset_and_lock_name_the_control_they_came_from",
    "matrix_a_redraw_ends_a_crosspoint_gesture_without_routing",
    "matrix_a_lock_variable_alone_redraws_the_lock",
    "matrix_a_tile_wall_takes_the_shape_of_the_page_it_is_drawn_on",
    "matrix_a_redraw_takes_down_an_open_chooser",
    "matrix_two_fingers_on_the_crosspoints_send_one_route",
    # The Programmer's session reaches the embedded panel's requests and socket.
    "bridge_sends_the_programmer_session_as_a_bearer_token",
    "bridge_attaches_nothing_without_a_session",
    "q139_a_connection_level_refusal_is_not_put_on_the_glass",
    # Panel access: the check-in comes first, and a device that is not
    # approved waits on a screen with its code.
    "access_the_check_in_comes_before_everything_else",
    "access_the_check_in_names_the_screens_time_zone",
    "access_a_waiting_device_sees_its_code_and_is_let_in_on_approval",
    "access_denied_and_unavailable_say_what_to_do",
    "access_a_4010_close_asks_again_instead_of_reconnecting",
    "access_the_admin_password_approves_from_the_panel",
    "access_the_designer_and_the_preview_skip_the_check_in",
    # A panel opened through Remote Access says when that session has ended,
    # and stops reconnecting.
    "remote_session_the_clouds_refusals_end_it",
    "remote_session_other_answers_leave_it_standing",
    "remote_session_a_drop_asks_once_and_stops_reconnecting",
    "remote_session_a_drop_that_is_not_the_end_reconnects",
    # The screens a refused or turned-off connection shows, the check-in's
    # pace and its unusable answers, the admin password's refusals, and every
    # server address under the path the panel was opened at.
    "remote_session_the_systems_json_with_a_charset_is_its_own_answer",
    "reconnect_backs_off_counts_its_attempts_and_starts_over",
    "access_requests_ask_the_server_not_a_cache",
    "access_a_4013_or_4014_close_says_why_and_offers_try_again",
    "access_a_check_in_with_no_usable_answer_is_null",
    "access_after_a_refusal_it_keeps_asking_at_its_own_pace",
    "access_the_waiting_screen_fills_in_what_the_answer_left_out",
    "access_the_screen_takes_the_loading_text_and_gives_it_back_before_a_first_draw",
    "access_the_admin_password_refusals_say_what_happened",
    "remote_session_ended_is_drawn_once_and_ends_the_holds",
    "hold_repeat_presses_until_let_go_and_stops_when_the_release_cannot_come",
    "every_server_address_follows_the_path_the_panel_was_opened_under",
    # A page nobody has built yet says so, without sending anyone to the IDE.
    "q160_an_empty_page_says_so",
    "q160_a_page_with_a_control_says_nothing",
    "q160_a_master_element_on_the_page_is_content",
    "q160_a_master_element_on_another_page_is_not_content",
    "q160_a_hidden_master_element_is_not_content",
    "q160_a_custom_page_says_nothing",
    "q160_the_designer_shows_no_notice",
    "q160_the_notice_takes_no_touches",
    # A project's theme_overrides reach the page background, not just the
    # elements standing on it.
    "theme_override_moves_the_page_background",
    "theme_with_no_override_paints_its_own_page_colour",
    "theme_page_defaults_references_read_one_set_of_variables",
    # A look that goes inactive lets go of what its active state drew, and
    # no look or toggle pass takes a button's own image away.
    "look_with_no_inactive_colour_lets_go_of_the_active_one",
    "input_picker_lights_only_the_current_input",
    "state_naming_no_colour_drops_the_last_states_colour_and_opacity",
    "themed_element_returns_to_its_theme_colour",
    "label_look_lets_go_of_its_colour",
    # One table of state values through every control with a look: the same
    # value picks the same entry everywhere.
    "look_picks_the_same_state_on_a_label",
    "look_picks_the_same_state_on_an_image",
    "look_picks_the_same_state_on_a_page_nav",
    "look_picks_the_same_state_on_a_container",
    "look_picks_the_same_state_on_a_button",
    "two_state_words_on_a_label_page_nav_and_button",
    "a_look_shows_nothing_for_an_unreachable_device_or_a_missing_key",
    "looks_and_toggles_keep_the_buttons_own_image",
    # Clearing a ui.<id>.* override hands the element back to its look as it
    # stands now.
    "cleared_override_returns_to_what_the_look_shows_now",
    # A PIN pad shows a dot per digit typed and sends the digits.
    "masked_keypad_draws_dots_and_sends_the_digits",
    # Every control takes a macro's ui.<id>.* overrides and gives them back,
    # and its unavailable mark goes on the control itself.
    "every_control_takes_its_overrides_and_gives_them_back",
    "a_cleared_label_override_gives_back_the_words_it_covered",
    "the_unavailable_mark_goes_on_the_control_itself",
    # Preview marks a press the server says cannot land; the canvas never does.
    "preview_marks_a_press_the_server_says_cannot_land",
    "the_design_canvas_takes_no_press_marks_from_the_server",
    # Only the Programmer's own frames count as embedded; any other framing
    # page gets an ordinary panel.
    "only_the_programmers_frames_are_embedded",
    # Simulation is marked on the panel while it runs, never in the IDE preview.
    "simulation_tag_shows_while_the_devices_are_simulated",
    "simulation_tag_is_never_drawn_in_the_ide_preview",
    # What a style writes onto a node, and a button's face after its look or
    # toggle, pinned value for value (tests/fixtures/panel_paint_pins.json).
    "paint_writes_each_style_key_as_it_does_today",
    "paint_draws_a_faded_picture_as_one_reused_layer",
    "paint_draws_a_button_picture_in_each_mode",
    "paint_retints_only_a_mask_or_blend_layer",
    "paint_frameless_and_the_state_colour_clear",
    "paint_sanitizes_and_resolves_as_it_does_today",
    "paint_draws_a_page_background_as_it_does_today",
    "button_face_after_feedback_and_toggle_looks",
    # A gauge and a level meter drawn, read, peak-held and redrawn, pinned
    # whole (tests/fixtures/panel_meter_pins.json).
    "meters_draw_and_show_readings_as_today",
    # A clock in every mode drawn and ticked through seconds, hours, midnight,
    # time zones and redraws, pinned whole (tests/fixtures/panel_clock_pins.json).
    "clock_draws_and_ticks_as_today",
    "clocks_share_one_interval_and_an_overlay_stops_its_own",
    # A keypad in every style pressed, auto-sent, cleared and redrawn, pinned
    # whole (tests/fixtures/panel_keypad_pins.json).
    "keypad_draws_and_sends_as_today",
    # Text edited in place on the design canvas: opened, typed, committed,
    # cancelled and refused through the Builder's messages, pinned whole
    # (tests/fixtures/panel_text_edit_pins.json).
    "text_edits_open_commit_and_refuse_as_today",
    # The PIN lock and the idle timer: unlocked by the server or a draft PIN,
    # once a session, back to the idle page and locked again, stopped while
    # offline, pinned whole (tests/fixtures/panel_lock_pins.json).
    "lock_and_idle_as_today",
    # Sound the audio player plugin asks for: played at the master volume,
    # once a request, stopped, capped at eight, allowed by the first touch,
    # pinned whole (tests/fixtures/panel_sound_pins.json).
    "sound_plays_and_stops_as_today",
    # The theme: fetched or handed over by the Theme Studio, waited for by the
    # first draw, fallen back from, kept and replaced, the page background it
    # gives a page with none, and the Theme Studio's control picker, pinned
    # whole (tests/fixtures/panel_theme_pins.json).
    "theme_applies_and_waits_as_today",
    # ui.<id>.label, bg_color, text_color, opacity and visible set and cleared
    # on every kind of control, over a look and a visible_when, before a draw
    # and through a redraw, pinned whole (tests/fixtures/panel_override_pins.json).
    "ui_overrides_apply_and_clear_as_today",
    # The project's own CSS: css_class tokens on a control, the project
    # stylesheet put in once, rewritten only when it changes, its declarations
    # raised to !important past quotes, parentheses and @keyframes, pinned whole
    # (tests/fixtures/panel_stylesheet_pins.json).
    "project_stylesheet_applies_as_today",
    # Whether a reading can be believed and a press can land: the marks, the
    # platform's own keys, a wake control, Preview's copy, and which bindings
    # a change reaches, pinned whole (tests/fixtures/panel_availability_pins.json).
    "availability_marks_and_reach_as_today",
    # Which macro runs are this panel's, what a run shows, and the failure band
    # with the refused control put back, pinned whole
    # (tests/fixtures/panel_macro_run_pins.json).
    "macro_runs_and_failures_as_today",
    # Which page draws, what opens over it and how it goes again: page
    # changes, back and dismiss, the overlay box, the empty page, master
    # elements, a ui/ file changing, the idle page and the Preview naming a
    # page, pinned whole (tests/fixtures/panel_page_pins.json).
    "pages_navigate_and_overlay_as_today",
    # Where an element goes: the arrangement for the way the glass is
    # turned, master boxes, aspect locks, containers, entry animations, the
    # coordinate space, a drag on the canvas, the vmin, the snap ruler and a
    # turn of the glass, pinned whole (tests/fixtures/panel_layout_pins.json).
    "layout_places_elements_as_today",
    # What the Programmer's frames take and send: who may speak to the panel,
    # every message it takes, the canvas starting up, the text sizes it
    # reports and which framings count as the Programmer's, pinned whole
    # (tests/fixtures/panel_builder_link_pins.json).
    "builder_link_takes_and_sends_as_today",
    # What a button's press sends: tap, toggle, hold to repeat, tap or hold,
    # the release that cannot be missed, on the canvas, and the camera
    # preset's own press, pinned whole (tests/fixtures/panel_press_pins.json).
    "button_press_sends_as_today",
    # A control's caption, the icon beside its words on every side, words a
    # look or an override changes beside an icon, and the accessible name,
    # pinned whole (tests/fixtures/panel_words_pins.json).
    "control_words_and_icons_draw_as_today",
    # visible_when: every operator spelling over every kind of reading, all
    # and any blocks, which changes draw it again, masters and overlays,
    # pinned whole (tests/fixtures/panel_visibility_pins.json).
    "visibility_follows_conditions_as_today",
    # Every frame the server sends, and the connection going and coming
    # back, pinned whole (tests/fixtures/panel_message_pins.json).
    "server_frames_handled_as_today",
    # A button's face in every display mode, its look in both forms through
    # every kind of reading and with its device away, the camera preset that
    # borrows it, a toggle's light and words, and the lit look's accent and
    # text colour on a toggle and a page nav, pinned whole
    # (tests/fixtures/panel_button_pins.json).
    "button_faces_and_looks_draw_as_today",
    # A label's own text, plain and rich, and a bound value with and without a
    # format, rounded or not, a text condition, the device away, on the panel
    # and the canvas, pinned whole (tests/fixtures/panel_label_pins.json).
    "label_words_draw_as_today",
    # An Image's own picture and its not-found note through a look's
    # pictures, the pictures a look names fetched when it is drawn, and a
    # Container's state picture as its background, pinned whole
    # (tests/fixtures/panel_picture_pins.json).
    "picture_looks_draw_as_today",
    # A Page Nav's name, press, look and lit look, drawn again when an
    # overlay closes, and a look that fails logged while the rest draw,
    # pinned whole (tests/fixtures/panel_nav_pins.json).
    "page_navs_draw_as_today",
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
