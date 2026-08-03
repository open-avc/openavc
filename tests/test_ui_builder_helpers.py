"""Regression tests for the UI Builder grid/id/rename helpers (uiBuilderHelpers.ts).

The UI Builder is React/TypeScript with no jsdom-loadable entry point, so these
exercise the pure helpers by transpiling uiBuilderHelpers.ts on the fly with the
esbuild already in web/programmer/node_modules and asserting on the results.
Like the colorUtils suite, they skip when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate. Run them locally after
`npm ci` in web/programmer; `node` ships on the CI runners.

Covers the audit findings fixed in the UIBuilderView.tsx group:
  H-038 clampOriginToGrid, M-077 findFreeGridPosition, L-051 pointerToCell,
  H-039 duplicateElementInPage reserved ids, L-052 renameElement array identity.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_ui_builder_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ui_builder_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "ui-builder" / "uiBuilderHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "ui builder helpers harness missing"
    if not HELPERS.is_file():
        return "uiBuilderHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"ui builder helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "s001_px_to_rem",
    "s001_rem_to_px",
    "s001_round_trip_is_stable",
    "s001_blank_clears",
    "s001_only_lengths_convert",
    "s001_display_and_store",
    "s001_hairline_survives",
    "h038_ptp_origin",
    "h038_ptp_centre",
    "h038_ptp_offset_rect",
    "h038_ptp_past_edge",
    "h038_ptp_zero_rect",
    "h038_round_precision",
    "h038_round_stable_round_trip",
    "m077_snap_grid",
    "m077_snap_bypass_exact",
    "m077_snap_element_edge_wins",
    "m077_edge_magnetism_without_grid",
    "m077_snap_right_edge",
    "m077_no_attraction_no_move",
    "m077_resize_east_leaves_x",
    "m077_resize_west_holds_far_edge",
    "m077_resize_south",
    "m077_resize_bypass_exact",
    "l051_autoplace_empty",
    "l051_autoplace_skips_occupied",
    "l051_autoplace_next_row",
    "l051_autoplace_cascade_when_snap_off",
    "l051_autoplace_no_room_falls_back",
    "l051_drop_adopts_when_contained",
    "l051_drop_partial_overlap_not_adopted",
    "l051_drop_innermost_container_wins",
    "l051_touch_warning",
    "l051_touch_warning_container_relative",
    "h039_dup_reserved_skips_master",
    "l052_rename_preserves_untouched",
    "l052_rename_rewrites_referencing",
    "h086_validate_array_device",
    "h086_validate_array_navigate",
    "h086_validate_array_change_macro",
    "h086_validate_legacy_object",
    "h086_validate_valid_refs_pass",
    "h086_removepage_scrubs_arrays",
    "m143_duplicate_rewrites_self_ref",
    "m143_duplicate_page_rewrites_sibling_refs",
    "m143_duplicate_page_respects_reserved",
    "m144_demote_collision_renamed",
    "m144_demote_no_collision_keeps_id",
    "m144_promote_collision_renamed",
    "m144_promote_no_collision_keeps_id",
    "l087_value_map_recursion",
    "l088_out_of_bounds_ids",
    "m231_snap_change_moves_nothing",
    "m231_no_clamp_successor",
    "l142_scrub_identity_when_untouched",
    "l142_scrub_new_when_changed",
    "l147_page_alias_dangling",
    "l147_value_map_dangling_descends",
    "l146_script_call_incomplete",
    "l147_page_alias_incomplete",
    "l147_value_map_branch_incomplete",
    "l147_valid_actions_clean",
    "m306_swap_adjacent",
    "m306_swap_visible_neighbor_skips_hidden",
    "m306_swap_noop_unknown_or_same",
    "m306_swap_scoped_to_page",
    "a101_absolute_flattens_container",
    "a101_absolute_survives_parent_cycle",
    "a101_align_left_across_container",
    "a101_topmost_selection_drops_children",
    "a101_locked_anchors_but_does_not_move",
    "a102_distribute_evens_gaps",
    "a102_distribute_pins_first_and_last",
    "a102_distribute_vertical_evens_gaps",
    "a102_distribute_needs_three",
    "a103_match_width_height_both",
    "a103_match_width_matches_what_is_drawn",
    "a103_match_skips_locked_target",
    "a104_marquee_selects_what_it_touches",
    "a104_marquee_normalises_direction",
    "a104_marquee_excludes_flush_edge",
    "a105_locked_ids_span_page_and_masters",
    "a105_absent_lock_reads_unlocked",
    "a106_align_writes_named_layout_only",
    "a106_variant_reads_through_inherits",
    "c107_tree_indents_by_depth",
    "c107_collapse_hides_subtree",
    "c107_search_keeps_ancestors",
    "c107_order_buttons_pair_siblings",
    "c107_cycle_members_still_listed",
    "c108_reparent_keeps_the_pixels",
    "c108_reparent_out_to_page_level",
    "c108_children_ride_along_untouched",
    "c108_every_layout_reconverted",
    "c108_lands_on_top_of_its_new_siblings",
    "c109_descendants_are_found_at_any_depth",
    "c109_cannot_parent_into_itself_or_its_own",
    "c109_illegal_reparent_is_a_no_op",
    "c109_drop_on_container_goes_in",
    "c109_drop_on_a_peer_joins_its_parent",
    "c109_drop_onto_itself_or_its_own_is_refused",
    "c109_picker_hides_self_and_descendants",
    "c110_orphans_rehome_without_moving",
    "c111_nested_container_adopts_in_page_space",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ui_builder_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
