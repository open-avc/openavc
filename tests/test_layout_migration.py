"""The 0.7.0 -> 0.8.0 layout conversion preserves what an author drew.

The promise this migration makes is narrow and worth stating exactly: a panel
looks *identical* at the 1280x800 reference it was implicitly designed against,
and proportionally identical on any screen of the same shape. It cannot be
identical at every size, because the old model's gaps were hard pixels (and
shrank on small glass), so its rendering was resolution-dependent to begin with.

The tests below pin the parts that would be silently wrong rather than loudly
broken: the exact rect CSS Grid laid out, the container adoption rule, and the
style conversion for an element whose font size is not the base 14.
"""

import pytest

from openavc.core.project_loader import ProjectConfig
from openavc.core.project_migration import (
    DEFAULT_GRID_GAP,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    migrate_0_7_to_0_8,
)


def _grid_rect_px(col, row, col_span, row_span, columns=12, rows=8,
                  box_w=REFERENCE_WIDTH, box_h=REFERENCE_HEIGHT,
                  pad=DEFAULT_GRID_GAP, gap=DEFAULT_GRID_GAP):
    """What CSS Grid actually laid out, computed independently of the code.

    Deliberately a second implementation rather than a call into the migration:
    a test that reuses the thing it is checking proves only that the code is
    consistent with itself.
    """
    cell_w = (box_w - 2 * pad - (columns - 1) * gap) / columns
    cell_h = (box_h - 2 * pad - (rows - 1) * gap) / rows
    return (
        pad + (col - 1) * (cell_w + gap),
        pad + (row - 1) * (cell_h + gap),
        col_span * cell_w + (col_span - 1) * gap,
        row_span * cell_h + (row_span - 1) * gap,
    )


def _project(pages, masters=None, settings=None):
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": "P"},
        "ui": {
            "settings": settings if settings is not None else {},
            "pages": pages,
            "master_elements": masters or [],
        },
    }


def _page(elements, page_id="main", columns=12, rows=8, **extra):
    return {
        "id": page_id, "name": page_id,
        "grid": {"columns": columns, "rows": rows},
        "elements": elements,
        **extra,
    }


def _el(el_id, col, row, col_span=1, row_span=1, el_type="button", **extra):
    return {
        "id": el_id, "type": el_type,
        "grid_area": {"col": col, "row": row,
                      "col_span": col_span, "row_span": row_span},
        **extra,
    }


def _placements(out, page_index=0):
    return out["ui"]["pages"][page_index]["layouts"][0]["placements"]


# ── Geometry ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("col,row,col_span,row_span", [
    (1, 1, 1, 1),      # first cell — catches a missing page padding
    (12, 8, 1, 1),     # last cell — catches an off-by-one gap accumulation
    (2, 3, 4, 2),      # a wide span — catches the between-cells gaps
    (1, 1, 12, 8),     # the whole grid
])
def test_cell_converts_to_the_exact_rect_css_grid_laid_out(col, row, col_span, row_span):
    out = migrate_0_7_to_0_8(_project([_page([_el("e", col, row, col_span, row_span)])]))
    got = _placements(out)["e"]

    exp_x, exp_y, exp_w, exp_h = _grid_rect_px(col, row, col_span, row_span)
    # Back to pixels at the reference size, where the promise is exactness.
    assert got["x"] / 100 * REFERENCE_WIDTH == pytest.approx(exp_x, abs=0.06)
    assert got["y"] / 100 * REFERENCE_HEIGHT == pytest.approx(exp_y, abs=0.06)
    assert got["w"] / 100 * REFERENCE_WIDTH == pytest.approx(exp_w, abs=0.06)
    assert got["h"] / 100 * REFERENCE_HEIGHT == pytest.approx(exp_h, abs=0.06)


def test_the_grid_still_fills_the_page_edge_to_edge():
    """The last cell must stop one padding short of the edge, not overrun it."""
    out = migrate_0_7_to_0_8(_project([_page([_el("last", 12, 8)])]))
    p = _placements(out)["last"]
    right = (p["x"] + p["w"]) / 100 * REFERENCE_WIDTH
    bottom = (p["y"] + p["h"]) / 100 * REFERENCE_HEIGHT
    assert right == pytest.approx(REFERENCE_WIDTH - DEFAULT_GRID_GAP, abs=0.06)
    assert bottom == pytest.approx(REFERENCE_HEIGHT - DEFAULT_GRID_GAP, abs=0.06)


def test_a_page_gap_override_widens_cells_but_not_the_padding():
    """`page.grid_gap` only ever overrode the gap between cells."""
    out = migrate_0_7_to_0_8(_project([_page([_el("e", 1, 1)], grid_gap=20)]))
    p = _placements(out)["e"]
    # Position is still one *padding* in, and padding stayed on the CSS var.
    assert p["x"] / 100 * REFERENCE_WIDTH == pytest.approx(DEFAULT_GRID_GAP, abs=0.06)
    exp_w = _grid_rect_px(1, 1, 1, 1, gap=20)[2]
    assert p["w"] / 100 * REFERENCE_WIDTH == pytest.approx(exp_w, abs=0.06)
    assert "grid_gap" not in out["ui"]["pages"][0]


def test_percentages_are_rounded_to_four_places():
    """Without one canonical rounding, a round-trip arms phantom autosaves."""
    out = migrate_0_7_to_0_8(_project([_page([_el("e", 2, 3, 4, 2)])]))
    for value in _placements(out)["e"].values():
        assert round(value, 4) == value


# ── Overlays ────────────────────────────────────────────────────────────────

def test_an_overlay_converts_against_its_own_box_and_the_box_goes_percent():
    out = migrate_0_7_to_0_8(_project([
        _page([_el("ok", 1, 4, 4, 1)], page_id="dlg", columns=4, rows=4,
              page_type="overlay", overlay={"width": 400, "height": 300}),
    ]))
    page = out["ui"]["pages"][0]
    assert page["overlay"]["width"] == pytest.approx(400 / REFERENCE_WIDTH * 100)
    assert page["overlay"]["height"] == pytest.approx(300 / REFERENCE_HEIGHT * 100)

    # The element's percentages are of the 400x300 overlay, not the viewport.
    exp = _grid_rect_px(1, 4, 4, 1, columns=4, rows=4, box_w=400, box_h=300)
    got = _placements(out)["ok"]
    assert got["x"] / 100 * 400 == pytest.approx(exp[0], abs=0.06)
    assert got["w"] / 100 * 400 == pytest.approx(exp[2], abs=0.06)


# ── Containers ──────────────────────────────────────────────────────────────

def test_a_group_adopts_only_what_it_fully_contains():
    out = migrate_0_7_to_0_8(_project([_page([
        _el("frame", 2, 2, 6, 4, el_type="group"),
        _el("inside", 3, 3, 2, 1),
        _el("straddling", 7, 2, 4, 1),   # starts inside, ends outside
        _el("outside", 10, 6, 2, 1),
    ])]))
    by_id = {e["id"]: e for e in out["ui"]["pages"][0]["elements"]}
    assert by_id["inside"]["parent"] == "frame"
    assert by_id["straddling"]["parent"] is None
    assert by_id["outside"]["parent"] is None
    assert by_id["frame"]["parent"] is None


def test_an_adopted_child_is_positioned_against_its_container():
    out = migrate_0_7_to_0_8(_project([_page([
        _el("frame", 2, 2, 6, 4, el_type="group"),
        _el("inside", 3, 3, 2, 1),
    ])]))
    place = _placements(out)
    frame, child = place["frame"], place["inside"]

    # Re-expand the child against the frame and it must land where the grid
    # would have put it on the page.
    abs_x = frame["x"] + child["x"] / 100 * frame["w"]
    exp_x = _grid_rect_px(3, 3, 2, 1)[0] / REFERENCE_WIDTH * 100
    assert abs_x == pytest.approx(exp_x, abs=0.01)


def test_nested_groups_adopt_into_the_innermost():
    out = migrate_0_7_to_0_8(_project([_page([
        _el("outer", 1, 1, 10, 6, el_type="group"),
        _el("inner", 2, 2, 4, 3, el_type="group"),
        _el("leaf", 3, 3, 1, 1),
    ])]))
    by_id = {e["id"]: e for e in out["ui"]["pages"][0]["elements"]}
    assert by_id["leaf"]["parent"] == "inner"
    assert by_id["inner"]["parent"] == "outer"
    assert by_id["outer"]["parent"] is None


def test_identical_group_rects_do_not_create_a_parent_cycle():
    """Two groups on the same cells could each adopt the other."""
    out = migrate_0_7_to_0_8(_project([_page([
        _el("a", 1, 1, 4, 4, el_type="group"),
        _el("b", 1, 1, 4, 4, el_type="group"),
    ])]))
    by_id = {e["id"]: e for e in out["ui"]["pages"][0]["elements"]}
    chain, cursor, seen = 0, by_id["a"]["parent"], set()
    while cursor is not None and chain < 10:
        assert cursor not in seen, "parent chain loops"
        seen.add(cursor)
        cursor = by_id[cursor]["parent"]
        chain += 1
    assert chain < 10


# ── Style units ─────────────────────────────────────────────────────────────

def test_style_px_becomes_rem_even_when_the_font_size_is_not_the_base():
    """The rem-not-em claim. Under em, padding would resolve against the
    element's own 20px font and come out 11.4px instead of 8."""
    out = migrate_0_7_to_0_8(_project([_page([
        _el("e", 1, 1, style={"font_size": 20, "padding": 8, "margin": 4,
                              "border_radius": 6, "cell_size": 44}),
    ])]))
    style = out["ui"]["pages"][0]["elements"][0]["style"]
    assert style["font_size"] == pytest.approx(20 / 14, abs=1e-4)
    assert style["padding"] == pytest.approx(8 / 14, abs=1e-4)
    assert style["margin"] == pytest.approx(4 / 14, abs=1e-4)
    assert style["border_radius"] == pytest.approx(6 / 14, abs=1e-4)
    assert style["cell_size"] == pytest.approx(44 / 14, abs=1e-4)
    # And each still renders at its original px against the 14px root.
    assert style["padding"] * 14 == pytest.approx(8, abs=0.01)


def test_the_three_element_level_sizes_survive_as_fractions():
    """icon_size and item_height were int-typed and thumb_size undeclared;
    all three rejected or dropped a rem value before 0.8.0."""
    out = migrate_0_7_to_0_8(_project([_page([
        _el("e", 1, 1, icon_size=24, item_height=44, thumb_size=44),
    ])]))
    el = out["ui"]["pages"][0]["elements"][0]
    assert el["icon_size"] == pytest.approx(24 / 14, abs=1e-4)
    assert el["item_height"] == pytest.approx(44 / 14, abs=1e-4)
    assert el["thumb_size"] == pytest.approx(44 / 14, abs=1e-4)
    # The model has to accept them, which is the half that used to fail.
    project = ProjectConfig(**out)
    assert project.ui.pages[0].elements[0].icon_size == pytest.approx(24 / 14, abs=1e-4)
    assert project.ui.pages[0].elements[0].thumb_size == pytest.approx(44 / 14, abs=1e-4)


# ── Spacers ─────────────────────────────────────────────────────────────────

def test_a_bare_spacer_is_dropped_and_a_styled_one_keeps_its_box():
    out = migrate_0_7_to_0_8(_project([_page([
        _el("gap", 1, 1, el_type="spacer"),
        _el("art", 2, 1, el_type="spacer", style={"bg_color": "#334455"}),
    ])]))
    elements = out["ui"]["pages"][0]["elements"]
    assert [e["id"] for e in elements] == ["art"]
    assert elements[0]["type"] == "label"
    assert elements[0]["text"] == ""
    assert elements[0]["style"]["bg_color"] == "#334455"
    assert "gap" not in _placements(out)


# ── Layouts and orientation ─────────────────────────────────────────────────

def test_the_project_orientation_becomes_the_primary_layouts_orientation():
    out = migrate_0_7_to_0_8(_project(
        [_page([_el("e", 1, 1)])], settings={"orientation": "portrait"},
    ))
    assert "orientation" not in out["ui"]["settings"]
    layout = out["ui"]["pages"][0]["layouts"][0]
    assert layout["orientation"] == "portrait"
    assert layout["primary"] is True


def test_snap_defaults_to_the_old_grid_spacing():
    """A fresh page must drop elements exactly where the grid would have."""
    out = migrate_0_7_to_0_8(_project([_page([])]))
    snap = out["ui"]["pages"][0]["snap"]
    assert snap["enabled"] is True
    assert snap["x"] == pytest.approx(100 / 12, abs=1e-4)
    assert snap["y"] == pytest.approx(100 / 8, abs=1e-4)


def test_a_page_always_ends_up_with_exactly_one_primary_layout():
    project = ProjectConfig(**migrate_0_7_to_0_8(_project([_page([])])))
    assert sum(1 for lay in project.ui.pages[0].layouts if lay.primary) == 1


# ── Master elements ─────────────────────────────────────────────────────────

def test_a_master_converts_against_the_first_page_it_targets():
    """It has no grid of its own, so it borrows one. Pages here disagree, which
    is the only case where the choice is visible."""
    out = migrate_0_7_to_0_8(_project(
        pages=[_page([], page_id="first", columns=12, rows=8),
               _page([], page_id="second", columns=4, rows=4)],
        masters=[{"id": "hdr", "type": "label", "pages": ["second"],
                  "grid_area": {"col": 1, "row": 1, "col_span": 1, "row_span": 1}}],
    ))
    master = out["ui"]["master_elements"][0]
    exp = _grid_rect_px(1, 1, 1, 1, columns=4, rows=4)
    assert master["placements"]["landscape"]["w"] / 100 * REFERENCE_WIDTH == pytest.approx(
        exp[2], abs=0.06,
    )
    assert "grid_area" not in master


def test_a_master_targeting_everything_uses_the_first_page():
    out = migrate_0_7_to_0_8(_project(
        pages=[_page([], page_id="first", columns=4, rows=4),
               _page([], page_id="second", columns=12, rows=8)],
        masters=[{"id": "hdr", "type": "label", "pages": "*",
                  "grid_area": {"col": 1, "row": 1, "col_span": 1, "row_span": 1}}],
    ))
    exp = _grid_rect_px(1, 1, 1, 1, columns=4, rows=4)
    got = out["ui"]["master_elements"][0]["placements"]["landscape"]
    assert got["w"] / 100 * REFERENCE_WIDTH == pytest.approx(exp[2], abs=0.06)


def test_when_every_page_shares_a_grid_the_master_choice_does_not_matter():
    """Which is why the reference-page rule is safe in practice."""
    results = []
    for targets in (["first"], ["second"], "*"):
        out = migrate_0_7_to_0_8(_project(
            pages=[_page([], page_id="first"), _page([], page_id="second")],
            masters=[{"id": "hdr", "type": "label", "pages": targets,
                      "grid_area": {"col": 3, "row": 2, "col_span": 2, "row_span": 1}}],
        ))
        results.append(out["ui"]["master_elements"][0]["placements"]["landscape"])
    assert results[0] == results[1] == results[2]


# ── End to end ──────────────────────────────────────────────────────────────

def test_a_migrated_project_loads_and_keeps_every_element():
    out = migrate_0_7_to_0_8(_project([
        _page([_el("frame", 2, 2, 6, 4, el_type="group"), _el("inside", 3, 3, 2, 1)]),
        _page([_el("ok", 1, 4, 4, 1)], page_id="dlg", columns=4, rows=4,
              page_type="overlay", overlay={"width": 400, "height": 300}),
    ], masters=[{"id": "hdr", "type": "label", "pages": "*",
                 "grid_area": {"col": 1, "row": 1, "col_span": 12, "row_span": 1}}]))

    project = ProjectConfig(**out)
    assert project.openavc_version == "0.8.0"
    for page in project.ui.pages:
        placements = page.layouts[0].placements
        assert {el.id for el in page.elements} == set(placements)
    assert project.ui.master_elements[0].placements["landscape"].w > 0


# ── The page-move action gets one spelling ──────────────────────────────────
#
# A button's page move is "ui.navigate" from 0.8.0 on, matching the macro step
# and the WS frame. The runtime stopped answering the older "navigate"/"page"
# spellings, and an unmigrated one is the worst kind of broken: the button
# still renders, still looks configured, and silently does nothing.

@pytest.mark.parametrize("retired", ["navigate", "page"])
def test_a_retired_page_move_spelling_is_rewritten(retired):
    out = migrate_0_7_to_0_8(_project([_page([
        _el("b", 1, 1, bindings={"do": {"press": [{"action": retired, "page": "home"}]}}),
    ])]))
    action = out["ui"]["pages"][0]["elements"][0]["bindings"]["do"]["press"][0]
    assert action == {"action": "ui.navigate", "page": "home"}


def test_the_rewrite_reaches_every_do_slot_and_the_single_object_shape():
    out = migrate_0_7_to_0_8(_project([_page([
        _el("b", 1, 1, bindings={"do": {
            "press": [{"action": "navigate", "page": "a"},
                      {"action": "macro", "macro": "m"}],
            "release": [{"action": "page", "page": "b"}],
            "hold": {"action": "navigate", "page": "c"},   # single object, not a list
        }}),
    ])]))
    do = out["ui"]["pages"][0]["elements"][0]["bindings"]["do"]
    assert [a["action"] for a in do["press"]] == ["ui.navigate", "macro"]
    assert do["release"][0]["action"] == "ui.navigate"
    assert do["hold"]["action"] == "ui.navigate"


def test_the_rewrite_descends_into_value_map_branches():
    """value_map branches hold real actions, so a page move can hide in one."""
    out = migrate_0_7_to_0_8(_project([_page([
        _el("s", 1, 1, el_type="select", bindings={"do": {"change": [{
            "action": "value_map",
            "map": {"a": {"action": "navigate", "page": "one"},
                    "b": [{"action": "page", "page": "two"}]},
        }]}}),
    ])]))
    branches = out["ui"]["pages"][0]["elements"][0]["bindings"]["do"]["change"][0]["map"]
    assert branches["a"]["action"] == "ui.navigate"
    assert branches["b"][0]["action"] == "ui.navigate"


def test_a_master_element_page_move_is_rewritten_too():
    """Masters carry bindings like any element, and are walked separately."""
    out = migrate_0_7_to_0_8(_project(
        pages=[_page([])],
        masters=[{"id": "hdr", "type": "button", "pages": [],
                  "grid_area": {"col": 1, "row": 1, "col_span": 1, "row_span": 1},
                  "bindings": {"do": {"press": [{"action": "navigate", "page": "home"}]}}}],
    ))
    assert out["ui"]["master_elements"][0]["bindings"]["do"]["press"][0] == {
        "action": "ui.navigate", "page": "home",
    }


def test_an_unrelated_action_is_left_alone():
    """Guard: only the page move is renamed. A deck's own "navigate" lives in
    plugin config, never in a UI element's bindings, so nothing here should
    touch a device command that happens to sit beside one."""
    out = migrate_0_7_to_0_8(_project([_page([
        _el("b", 1, 1, bindings={"do": {"press": [
            {"action": "device.command", "device": "proj", "command": "power_on"},
            {"action": "state.set", "key": "var.x", "value": 1},
        ]}}),
    ])]))
    actions = out["ui"]["pages"][0]["elements"][0]["bindings"]["do"]["press"]
    assert [a["action"] for a in actions] == ["device.command", "state.set"]
