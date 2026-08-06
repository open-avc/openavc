"""The page reviewer: what it flags, what it leaves alone, and what it fixes.

Every device here is invented. The reviewer is a platform capability -- it has
no opinion about any particular product -- and the numbers it works from are
pinned by ``tests/e2e/test_control_minimums.py`` against a real browser.

The tests that matter most are the ones asserting silence. A checker that flags
everything is the same as no checker: a caller learns to skip the field, and the
one finding that would have saved the panel goes with it.
"""

from __future__ import annotations

import pytest

from server.core.project_loader import Layout, Placement, UIElement, UIPage
from server.ui.page_review import (
    Finding,
    review_master_element,
    review_page,
)

# 1280 x 800 is the reference, so a percentage is easy to state in pixels:
# 1% of the width is 12.8px and 1% of the height is 8px.
PX_PER_W_PCT = 12.8
PX_PER_H_PCT = 8.0


def _page(elements, placements, *, extra_layouts=(), page_id="main"):
    layouts = [Layout(
        id="landscape", orientation="landscape", primary=True,
        placements={k: Placement(**v) for k, v in placements.items()},
    )]
    layouts.extend(extra_layouts)
    return UIPage(id=page_id, name="Main", elements=elements, layouts=layouts)


def _box(x, y, w_px, h_px):
    return {"x": x, "y": y, "w": w_px / PX_PER_W_PCT, "h": h_px / PX_PER_H_PCT}


def _kinds(findings: list[Finding]) -> list[str]:
    return sorted(f.kind for f in findings)


def _of_kind(findings: list[Finding], kind: str) -> list[Finding]:
    return [f for f in findings if f.kind == kind]


# --- Fixed internals: the defect class that started this --------------------


def test_a_captioned_status_led_too_narrow_for_its_dot_is_flagged():
    page = _page(
        [UIElement(id="led", type="status_led", label="Ready")],
        {"led": _box(0, 0, 28, 44)},
    )
    findings, _ = review_page(page)
    assert _kinds(findings) == ["too_small_for_contents"]
    message = findings[0].message
    assert "28x44px" in message
    assert "needs 29px" in message
    assert "led-dot is 20px" in message


def test_the_same_led_without_a_caption_is_left_alone():
    """20px of dot fits in 28px. The caption is what does not fit.

    The floor is a property of the element as written, not of its type -- so
    the unlabelled LED beside the flagged one is correct at a size the labelled
    one is broken at, and saying otherwise would be a false alarm on a control
    that draws perfectly.
    """
    page = _page(
        [UIElement(id="led", type="status_led")],
        {"led": _box(0, 0, 28, 44)},
    )
    findings, _ = review_page(page)
    assert findings == []


def test_a_bound_value_is_not_a_caption_and_only_warns_that_it_is_inert():
    """The two checks used to contradict each other on the same element.

    A 28px LED with a bound show.value was told it needed 29px for a caption AND
    that the binding drawing that caption has no effect. Only the second is
    true, so the box is fine at 28px and one finding comes back, not two.
    """
    page = _page(
        [UIElement(id="led", type="status_led", bindings={
            "show": {"value": {"key": "device.acme_widget.status"}},
        })],
        {"led": _box(0, 0, 28, 44)},
    )
    findings, _ = review_page(page)
    assert not _of_kind(findings, "too_small_for_contents")
    assert _of_kind(findings, "binding_not_rendered")


def test_a_fader_narrower_than_its_handle_and_scale_names_both():
    page = _page(
        [UIElement(id="fader", type="fader")],
        {"fader": _box(0, 0, 60, 273)},
    )
    findings, _ = review_page(page)
    message = _of_kind(findings, "too_small_for_contents")[0].message
    assert "needs 72px" in message
    assert "fader-handle is 44px" in message
    assert "fader-scale is 28px" in message


def test_types_with_no_fixed_internals_are_never_too_small():
    """A button, a label and an image are limited by their text, not a box.

    Phase 5 deliberately declined to invent a floor for these: how wide a
    caption is depends on the string and the theme's font, it is unbounded, and
    a made-up number would flag layouts that render fine.
    """
    page = _page(
        [
            UIElement(id="btn", type="button", label="Go"),
            UIElement(id="lbl", type="label", text="Room"),
            UIElement(id="img", type="image", src="logo.png"),
        ],
        {
            "btn": _box(0, 0, 6, 6),
            "lbl": _box(20, 0, 6, 6),
            "img": _box(40, 0, 6, 6),
        },
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "too_small_for_contents") == []


def test_the_fix_is_stated_as_a_percentage_of_the_container_not_the_page():
    """The caller writes percentages of the parent, so that is what it is told.

    This is the arithmetic the model could not do: a status light 3% wide is
    38px on the page and 6px inside a narrow container, and nothing in the file
    says which. Handing back "needs 29px" alone would leave the same gap open.
    """
    page = _page(
        [
            UIElement(id="strip", type="group"),
            UIElement(id="led", type="status_led", label="Ready", parent="strip"),
        ],
        # A container a quarter of the page wide (320px), holding an LED at 10%
        # of that -- 32px on the page, but the caller writes 10.
        {"strip": {"x": 0, "y": 0, "w": 25, "h": 50}, "led": {"x": 0, "y": 0, "w": 8, "h": 20}},
    )
    findings, _ = review_page(page)
    message = _of_kind(findings, "too_small_for_contents")[0].message
    # 29px of a 320px container is 9.0625%, not 2.27% of the page.
    assert "w at least 9.06%" in message
    assert "its container 'strip'" in message


def test_a_sliders_floor_follows_the_thumb_the_theme_sets():
    """The only minimum that is a function rather than a number.

    A slider's floor is 24px plus its thumb, and the thumb is authored -- per
    element, or per theme. A fixed number here would be wrong for any theme
    that touched it, in the direction that flags working layouts.
    """
    page = _page(
        [UIElement(id="slide", type="slider")],
        {"slide": _box(0, 0, 70, 200)},
    )
    default_thumb, _ = review_page(page)
    assert default_thumb == []  # 24 + 44 = 68, and it has 70

    big_thumb, _ = review_page(page, theme={"thumb_size": 66 / 14})
    assert _of_kind(big_thumb, "too_small_for_contents")  # 24 + 66 = 90


# --- Collisions and edges --------------------------------------------------


def test_two_overlapping_siblings_are_reported_once_naming_both():
    page = _page(
        [
            UIElement(id="source_label", type="label", text="Source"),
            UIElement(id="source_value", type="label", text="HDMI 1"),
        ],
        {
            "source_label": {"x": 10, "y": 10, "w": 20, "h": 10},
            "source_value": {"x": 25, "y": 12, "w": 20, "h": 10},
        },
    )
    findings, _ = review_page(page)
    overlaps = _of_kind(findings, "overlap")
    assert len(overlaps) == 1
    assert "source_label" in overlaps[0].message
    assert "source_value" in overlaps[0].message
    assert "px" in overlaps[0].message


def test_boxes_in_different_containers_are_not_compared():
    """Only siblings share a coordinate space, and only siblings are compared.

    The two groups here overlap and are reported for it -- they are siblings.
    Their children land on top of each other too, in page space, and are not:
    which container a control belongs to is a real structural statement, and
    comparing across it would also report every container as 100% overlapping
    each of its own children. That noise buries the collisions that matter.
    """
    page = _page(
        [
            UIElement(id="left", type="group"),
            UIElement(id="right", type="group"),
            UIElement(id="a", type="label", text="A", parent="left"),
            UIElement(id="b", type="label", text="B", parent="right"),
        ],
        {
            "left": {"x": 0, "y": 0, "w": 60, "h": 100},
            "right": {"x": 30, "y": 0, "w": 60, "h": 100},
            "a": {"x": 50, "y": 10, "w": 40, "h": 20},
            "b": {"x": 0, "y": 10, "w": 40, "h": 20},
        },
    )
    findings, _ = review_page(page)
    assert [f.key for f in _of_kind(findings, "overlap")] == [("overlap", "left", "right")]


def test_boxes_that_only_touch_at_an_edge_are_not_a_collision():
    page = _page(
        [
            UIElement(id="a", type="label", text="A"),
            UIElement(id="b", type="label", text="B"),
        ],
        {"a": {"x": 0, "y": 0, "w": 20, "h": 10}, "b": {"x": 20, "y": 0, "w": 20, "h": 10}},
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "overlap") == []


def test_an_element_hanging_out_of_its_container_says_by_how_much():
    page = _page(
        [
            UIElement(id="status", type="group"),
            UIElement(id="clip", type="button", label="Clip", parent="status"),
        ],
        {
            "status": {"x": 0, "y": 0, "w": 50, "h": 50},  # 400px tall
            "clip": {"x": 5, "y": 85, "w": 40, "h": 16},
        },
    )
    findings, _ = review_page(page)
    overhang = _of_kind(findings, "outside_its_container")
    assert len(overhang) == 1
    assert "past the bottom" in overhang[0].message
    assert "= 101%" in overhang[0].message
    assert "4px" in overhang[0].message  # 1% of the container's 400px


def test_an_element_that_exactly_fills_its_container_does_not_overhang():
    page = _page(
        [
            UIElement(id="frame", type="group"),
            UIElement(id="inner", type="label", text="Hi", parent="frame"),
        ],
        {"frame": {"x": 0, "y": 0, "w": 50, "h": 50}, "inner": {"x": 0, "y": 0, "w": 100, "h": 100}},
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "outside_its_container") == []


def test_an_element_with_no_box_at_all_is_flagged():
    """The renderer's fallback fills the parent, and nothing in the file says so.

    An element the primary arrangement never positions draws at 0,0,100x100 --
    on top of everything already there. It is the one geometry defect with no
    geometry to look at, so it has to be inferred from the absence.
    """
    page = _page(
        [
            UIElement(id="btn", type="button", label="Go"),
            UIElement(id="ghost", type="button", label="Ghost"),
        ],
        {"btn": {"x": 0, "y": 0, "w": 20, "h": 10}},
    )
    findings, _ = review_page(page)
    missing = _of_kind(findings, "no_placement")
    assert [f.element_id for f in missing] == ["ghost"]
    assert "fills the page" in missing[0].message


def test_a_variant_arrangement_may_leave_an_element_unpositioned():
    """A variant stores only what moved; the rest is inherited, not missing."""
    page = _page(
        [UIElement(id="btn", type="button", label="Go")],
        {"btn": {"x": 0, "y": 0, "w": 20, "h": 10}},
        extra_layouts=[Layout(id="portrait", orientation="portrait", inherits="landscape")],
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "no_placement") == []


# --- Fingers ---------------------------------------------------------------


def test_a_select_under_the_touch_minimum_is_flagged_in_millimetres():
    page = _page(
        [UIElement(id="picker", type="select")],
        {"picker": _box(0, 0, 331, 44)},
    )
    findings, _ = review_page(page)
    touch = _of_kind(findings, "small_touch_target")
    assert len(touch) == 1
    assert "mm" in touch[0].message
    assert "on height" in touch[0].message


def test_a_small_label_is_not_a_touch_problem():
    page = _page(
        [UIElement(id="lbl", type="label", text="dB")],
        {"lbl": _box(0, 0, 30, 20)},
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "small_touch_target") == []


# --- Bindings the renderer never reads -------------------------------------


def test_a_label_carrying_a_look_binding_is_told_it_does_nothing():
    """The two-writers case, as the renderer actually resolves it.

    A label draws ``show.value`` and has no code path for ``show.look`` at all,
    so an element wired for both does not fight over the text -- it silently
    draws the raw value and the state text never appears. Saying "two writers"
    would be the wrong advice; saying which one draws is the right one.
    """
    page = _page(
        [UIElement(id="conn", type="label", bindings={
            "show": {
                "value": {"key": "device.acme_widget.connected", "format": "{value}"},
                "look": {
                    "key": "device.acme_widget.connected",
                    "states": {
                        "true": {"label": "ONLINE"},
                        "false": {"label": "OFFLINE"},
                    },
                },
            },
        })],
        {"conn": {"x": 0, "y": 0, "w": 20, "h": 10}},
    )
    findings, _ = review_page(page)
    ignored = _of_kind(findings, "binding_not_rendered")
    assert len(ignored) == 1
    assert "it reads show.value" in ignored[0].message


def test_a_button_carrying_the_same_look_binding_is_left_alone():
    page = _page(
        [UIElement(id="btn", type="button", label="Power", bindings={
            "show": {"look": {"key": "device.acme_widget.power", "states": {
                "on": {"label": "ON", "bg_color": "#4CAF50"},
            }}},
        })],
        {"btn": {"x": 0, "y": 0, "w": 20, "h": 10}},
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "binding_not_rendered") == []


def test_a_status_led_takes_colour_from_look_but_not_text():
    """Honored for one thing and not the other, which no schema can express."""
    page = _page(
        [UIElement(id="led", type="status_led", bindings={
            "show": {"look": {"key": "device.acme_widget.fault", "states": {
                "true": {"color": "#ef5350", "label": "FAULT"},
            }}},
        })],
        {"led": {"x": 0, "y": 0, "w": 20, "h": 10}},
    )
    findings, _ = review_page(page)
    ignored = _of_kind(findings, "binding_not_rendered")
    assert len(ignored) == 1
    assert "only colour" in ignored[0].message
    assert "FAULT" not in ignored[0].message.split("sets a label for")[0]


def test_a_status_led_look_that_only_sets_colour_is_left_alone():
    page = _page(
        [UIElement(id="led", type="status_led", bindings={
            "show": {"look": {"key": "device.acme_widget.fault", "map": {"true": "#ef5350"}}},
        })],
        {"led": {"x": 0, "y": 0, "w": 20, "h": 10}},
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "binding_not_rendered") == []


def test_visible_when_is_honored_everywhere_and_never_flagged():
    page = _page(
        [UIElement(id="grp", type="group", bindings={
            "show": {"visible_when": {"key": "var.mode", "operator": "eq", "value": "on"}},
        })],
        {"grp": {"x": 0, "y": 0, "w": 20, "h": 10}},
    )
    findings, _ = review_page(page)
    assert _of_kind(findings, "binding_not_rendered") == []


# --- Ranges ----------------------------------------------------------------


ACME_FADER = {"type": "float", "min": -80.0, "max": 0.0, "step": 0.5, "unit": "dB"}


def _fader_page(**fields):
    return _page(
        [UIElement(id="fader", type="fader", bindings={
            "show": {"value": {"key": "device.acme_widget.channel.01.level"}},
        }, **fields)],
        {"fader": _box(0, 0, 100, 300)},
    )


def test_an_omitted_bound_is_filled_in_from_what_the_driver_declares():
    """The one auto-fix, and the reason it is safe.

    An absent ``max`` is not a narrower range, it is no range: the renderer
    substitutes 100, so a -80..0 dB fader silently becomes -80..+100 and the top
    of the throw commands a value the amplifier will refuse. Nothing was
    overridden, because nothing was there.
    """
    page = _fader_page(min=-80.0)
    findings, adjustments = review_page(page, declared_range=lambda key: ACME_FADER)

    assert {a.field: a.value for a in adjustments} == {"max": 0.0, "step": 0.5, "unit": "dB"}
    assert page.elements[0].max == 0.0
    assert page.elements[0].min == -80.0  # what was stated is untouched
    assert _of_kind(findings, "range_wider_than_device") == []
    assert "filled in as 0" in adjustments[0].message


def test_a_narrower_range_than_the_device_supports_is_left_alone():
    """A volume-limited install is authoring, not an error.

    The driver's numbers are the device's capability; the element's are the
    control's range, and deliberately clamping one is how a fader gets a ceiling
    in a classroom. Treating that as drift would undo a safety decision on every
    edit.
    """
    page = _fader_page(min=-40.0, max=-10.0)
    findings, adjustments = review_page(page, declared_range=lambda key: ACME_FADER)
    assert [a for a in adjustments if a.field in ("min", "max")] == []
    assert page.elements[0].max == -10.0
    assert _of_kind(findings, "range_wider_than_device") == []


def test_a_range_wider_than_the_device_supports_warns_with_both_numbers():
    page = _fader_page(min=-100.0, max=12.0)
    findings, _ = review_page(page, declared_range=lambda key: ACME_FADER)
    wider = _of_kind(findings, "range_wider_than_device")
    assert len(wider) == 2
    assert any("max 12 is above the 0" in f.message for f in wider)
    assert any("min -100 is below the -80" in f.message for f in wider)


def test_a_meter_scaled_past_the_device_is_a_scale_choice_not_a_defect():
    """A read-out sends nothing, so a wide scale commands nothing.

    Warning here would be wrong twice over: a meter that never reaches the top
    of its sweep is untidy rather than broken, and a scale shared across
    channels that report different ranges is a deliberate way to make two
    meters comparable.
    """
    page = _page(
        [UIElement(id="meter", type="level_meter", min=-100.0, max=12.0, bindings={
            "show": {"value": {"key": "device.acme_widget.channel.01.level"}},
        })],
        {"meter": _box(0, 0, 40, 200)},
    )
    findings, adjustments = review_page(page, declared_range=lambda key: ACME_FADER)
    assert _of_kind(findings, "range_wider_than_device") == []
    assert adjustments == []  # both bounds were stated; nothing to complete


def test_a_control_with_no_declared_range_is_left_entirely_alone():
    page = _fader_page(min=-80.0)
    findings, adjustments = review_page(page, declared_range=lambda key: None)
    assert adjustments == []
    assert _of_kind(findings, "range_wider_than_device") == []


def test_only_range_carrying_types_are_filled():
    page = _page(
        [UIElement(id="lbl", type="label", bindings={
            "show": {"value": {"key": "device.acme_widget.channel.01.level"}},
        })],
        {"lbl": {"x": 0, "y": 0, "w": 20, "h": 10}},
    )
    _, adjustments = review_page(page, declared_range=lambda key: ACME_FADER)
    assert adjustments == []


# --- Scope: a write answers for what it wrote ------------------------------


def _two_leds_page():
    return _page(
        [
            UIElement(id="old_led", type="status_led", label="Old"),
            UIElement(id="new_led", type="status_led", label="New"),
        ],
        {"old_led": _box(0, 0, 24, 44), "new_led": _box(20, 0, 24, 44)},
    )


def test_a_write_is_not_told_about_elements_it_did_not_touch():
    """Otherwise every call re-reports the whole page and the field gets ignored."""
    findings, _ = review_page(_two_leds_page(), touched={"new_led"})
    assert [f.element_id for f in findings] == ["new_led"]


def test_reviewing_the_whole_page_is_what_no_scope_means():
    findings, _ = review_page(_two_leds_page())
    assert {f.element_id for f in findings} == {"old_led", "new_led"}


def test_a_collision_with_an_untouched_neighbour_is_still_the_writers_problem():
    page = _page(
        [
            UIElement(id="existing", type="label", text="A"),
            UIElement(id="added", type="label", text="B"),
        ],
        {
            "existing": {"x": 10, "y": 10, "w": 20, "h": 10},
            "added": {"x": 15, "y": 10, "w": 20, "h": 10},
        },
    )
    findings, _ = review_page(page, touched={"added"})
    assert len(_of_kind(findings, "overlap")) == 1


# --- Arrangements ----------------------------------------------------------


def test_a_variant_only_problem_names_the_arrangement():
    page = _page(
        [UIElement(id="led", type="status_led", label="Ready")],
        {"led": _box(0, 0, 60, 44)},
        extra_layouts=[Layout(
            id="portrait", orientation="portrait", inherits="landscape",
            placements={"led": Placement(**_box(0, 0, 24, 44))},
        )],
    )
    findings, _ = review_page(page)
    assert len(findings) == 1
    assert "in the 'portrait' arrangement" in findings[0].message


def test_the_same_problem_in_both_arrangements_is_said_once():
    page = _page(
        [UIElement(id="led", type="status_led", label="Ready")],
        {"led": _box(0, 0, 24, 44)},
        extra_layouts=[Layout(
            id="portrait", orientation="portrait", inherits="landscape",
            placements={"led": Placement(**_box(0, 0, 25, 44))},
        )],
    )
    findings, _ = review_page(page)
    assert len(findings) == 1
    assert "arrangement" not in findings[0].message  # the primary's phrasing wins


def test_an_element_hidden_in_an_arrangement_is_not_measured_there():
    page = _page(
        [UIElement(id="led", type="status_led", label="Ready")],
        {"led": _box(0, 0, 60, 44)},
        extra_layouts=[Layout(
            id="portrait", orientation="portrait", inherits="landscape",
            hidden=["led"], placements={"led": Placement(**_box(0, 0, 24, 44))},
        )],
    )
    findings, _ = review_page(page)
    assert findings == []


# --- Master elements -------------------------------------------------------


def test_a_master_element_is_measured_against_the_viewport():
    from server.core.project_loader import MasterElement

    master = MasterElement(
        id="conn_led", type="status_led", label="Online",
        placements={"landscape": Placement(x=1, y=1, w=1.5, h=4)},
    )
    findings = review_master_element(master)
    assert [f.kind for f in findings] == ["too_small_for_contents"]
    assert "in the landscape arrangement" in findings[0].message


def test_a_master_element_wide_enough_is_left_alone():
    from server.core.project_loader import MasterElement

    master = MasterElement(
        id="conn_led", type="status_led", label="Online",
        placements={"landscape": Placement(x=1, y=1, w=8, h=6)},
    )
    assert review_master_element(master) == []


# --- Nothing here may ever cost a write ------------------------------------


@pytest.mark.parametrize("page", [
    UIPage(id="empty", name="Empty", elements=[], layouts=[]),
    UIPage(
        id="cycle", name="Cycle",
        elements=[
            UIElement(id="a", type="group", parent="b"),
            UIElement(id="b", type="group", parent="a"),
        ],
        layouts=[Layout(id="l", primary=True, placements={
            "a": Placement(w=50, h=50), "b": Placement(w=50, h=50),
        })],
    ),
    UIPage(
        id="dangling", name="Dangling",
        elements=[UIElement(id="a", type="label", parent="nobody")],
        layouts=[Layout(id="l", primary=True, placements={"a": Placement()})],
    ),
])
def test_a_malformed_page_is_reviewed_without_raising(page):
    """A hand-edited project still has to be reviewable.

    Everything upstream of this rejects a cycle, so one only reaches here in a
    file someone wrote by hand -- and a reviewer that raises on it would turn an
    advisory into an outage on the one project that most needs the advice.
    """
    findings, adjustments = review_page(page)
    assert isinstance(findings, list)
    assert adjustments == []
