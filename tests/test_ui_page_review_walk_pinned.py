"""The page review's two walks, pinned where the rest of the suite never looks.

A page is reviewed element by element (the checks an element gets on its own:
its type, its bindings, its actions, its properties, its content, a keypad's
settings, a matrix's lists) and then arrangement by arrangement (each box, the
siblings that share a container, the masters underneath). A master element gets
the same element checks and the same per-box checks against its own
orientation-keyed boxes. These tests hold the order those checks report in, on
a page and on a master, and the edges of both walks: a page with no
arrangement, a container an arrangement never places, a master with no box or
one that is not a number.

The Builder's copy of the same review is held to these exact sentences by
``tests/test_ui_review_parity.py``; this file pins what both must keep saying.
Every element here is invented.
"""

from __future__ import annotations

from types import SimpleNamespace

from openavc.core.project_loader import Layout, Placement, UIElement, UIPage
from openavc.ui.page_review import review_master_element, review_page

# 1280 x 800 is the reference: 1% of the width is 12.8px, 1% of the height 8px.
PX_W = 12.8
PX_H = 8.0


def _page(elements, placements, page_id="main"):
    return UIPage(
        id=page_id,
        name="Main",
        elements=[UIElement.model_validate(e) for e in elements],
        layouts=[Layout(
            id="landscape", orientation="landscape", primary=True,
            placements={k: Placement(**v) for k, v in placements.items()},
        )],
    )


def _sequence(findings):
    return [(f.kind, f.element_id) for f in findings]


# A keypad that trips four of the element checks at once, so their order shows.
KEYPAD = {
    "id": "pad", "type": "keypad", "icon": "x", "auto_send": True,
    "auto_send_delay_ms": 200,
    "bindings": {"show": {"look": {"key": "var.a"}}, "do": {"press": {"action": "delay"}}},
}
KEYPAD_ORDER = [
    ("binding_not_rendered", "pad"),
    ("binding_not_dispatched", "pad"),
    ("property_not_rendered", "pad"),
    ("keypad_sends_early", "pad"),
]
FULL_PAGE = {"x": 0, "y": 0, "w": 100, "h": 100}


def test_an_elements_own_checks_report_in_one_order_on_a_page():
    findings, _ = review_page(_page([KEYPAD], {"pad": FULL_PAGE}))
    assert _sequence(findings) == KEYPAD_ORDER


def test_a_master_gets_the_same_checks_in_the_same_order_and_words():
    on_page, _ = review_page(_page([KEYPAD], {"pad": FULL_PAGE}))
    as_master = review_master_element(dict(KEYPAD))
    assert _sequence(as_master) == KEYPAD_ORDER
    assert [f.message for f in as_master] == [f.message for f in on_page]


def test_an_unknown_type_is_said_first_and_its_actions_are_still_read():
    findings = review_master_element(
        {"id": "w", "type": "widgetx", "bindings": {"do": {"press": {"action": "delay"}}}},
    )
    assert _sequence(findings) == [
        ("unknown_element_type", "w"),
        ("binding_not_dispatched", "w"),
    ]


def test_nothing_to_draw_comes_after_the_binding_and_action_findings():
    findings = review_master_element({
        "id": "lb", "type": "label",
        "bindings": {"show": {"items": {"key_pattern": "x"}}, "do": {"press": {"action": "delay"}}},
    })
    assert _sequence(findings) == [
        ("binding_not_rendered", "lb"),
        ("binding_not_dispatched", "lb"),
        ("nothing_to_draw", "lb"),
    ]
    assert findings[2].message == (
        "lb (label) has no text, and no show.value to supply one, so it draws an "
        "empty box. Set text to the string to draw, or a show.value binding."
    )


def test_a_per_state_icon_on_a_label_points_at_its_own_icon():
    """A label draws its own icon, so the remedy is that field, not a button."""
    findings = review_master_element({
        "id": "lab", "type": "label", "text": "A",
        "bindings": {"show": {"look": {"key": "var.a", "states": {"on": {"icon": "star"}}}}},
    })
    assert [f.message for f in findings] == [
        "lab (label): show.look sets an icon for on, but a label takes only color and "
        "text from show.look, so that icon never appears. Set the element's own icon -- "
        "it draws that one, it just cannot change it per state.",
    ]


def test_a_page_with_no_arrangement_gets_only_its_element_findings():
    """The project model always fills one in; the walk must not assume it did."""
    page = SimpleNamespace(
        id="bare",
        elements=[UIElement.model_validate(
            {"id": "lbl", "type": "label", "text": "A",
             "bindings": {"show": {"items": {"key_pattern": "x"}}}},
        )],
        layouts=[],
    )
    findings, adjustments = review_page(page)
    assert _sequence(findings) == [("binding_not_rendered", "lbl")]
    assert adjustments == []


LED_IN_TWO_GROUPS = [
    {"id": "led", "type": "status_led", "label": "Ready", "parent": "inner"},
    {"id": "inner", "type": "group", "parent": "outer"},
    {"id": "outer", "type": "group"},
]


def test_the_container_chain_stops_at_a_container_this_arrangement_does_not_place():
    findings, _ = review_page(_page(LED_IN_TWO_GROUPS, {
        "led": FULL_PAGE,
        "inner": {"x": 0, "y": 0, "w": 28 / PX_W, "h": 44 / PX_H},
    }))
    assert [(f.kind, f.element_id, f.message) for f in findings] == [
        ("too_small_for_contents", "led",
         "led (status_led) is 28x44px at the 1280x800 reference, too small for what it "
         "draws: 28px wide, needs 29px (led-dot is 20px). 'inner' is 28px wide, too "
         "small to hold it at any width: give 'inner' w at least 2.27% of the page."),
        ("no_placement", "outer",
         "outer (group) has no placement, so it fills the page edge to edge and covers "
         "whatever is already there. Give it {x, y, w, h}."),
    ]


def test_the_container_chain_reaches_the_outermost_placed_container():
    findings, _ = review_page(_page(LED_IN_TWO_GROUPS, {
        "led": FULL_PAGE,
        "inner": FULL_PAGE,
        "outer": {"x": 0, "y": 0, "w": 28 / PX_W, "h": 44 / PX_H},
    }))
    assert [f.message for f in findings] == [
        "led (status_led) is 28x44px at the 1280x800 reference, too small for what it "
        "draws: 28px wide, needs 29px (led-dot is 20px). 'outer' is 28px wide, too "
        "small to hold it at any width: give 'outer' w at least 2.27% of the page.",
    ]


def test_a_master_with_no_boxes_gets_only_its_element_findings():
    assert _sequence(review_master_element(dict(KEYPAD))) == KEYPAD_ORDER
    assert review_master_element({"id": "m", "type": "status_led", "label": "Ready"}) == []


def test_a_master_box_that_is_not_a_number_is_passed_over():
    assert review_master_element({
        "id": "m", "type": "status_led", "label": "Ready",
        "placements": {"landscape": {"w": "wide", "h": 5}},
    }) == []


def test_a_master_is_measured_per_orientation_and_said_once():
    findings = review_master_element({
        "id": "m", "type": "status_led", "label": "Ready",
        "placements": {
            "landscape": {"x": 0, "y": 0, "w": 1, "h": 1},
            "portrait": {"w": 1, "h": 1},
        },
    })
    assert [(f.kind, f.message) for f in findings] == [
        ("too_small_for_contents",
         "m (status_led) is 13x8px at the 1280x800 reference, too small for what it "
         "draws: 13px wide, needs 29px (led-dot is 20px); 8px tall, needs 20px (led-dot "
         "is 20px). Give it w at least 2.27% and h at least 2.5% of the page in the "
         "landscape arrangement."),
    ]


def test_a_missing_master_width_is_read_as_the_whole_screen():
    """`w` absent is the full width, so only the height is short."""
    findings = review_master_element({
        "id": "m", "type": "status_led", "label": "Ready",
        "placements": {"landscape": {"h": 1}},
    })
    assert len(findings) == 1
    assert "1280x8px" in findings[0].message
