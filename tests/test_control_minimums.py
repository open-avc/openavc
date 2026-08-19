"""The per-control minimums, without a browser.

The measurement that produced these numbers needs Chromium and lives in
``tests/e2e/test_control_minimums.py``, which skips wherever Playwright is not
installed -- including the automated Python job. That is the right place for
"is this number still true", but it means the module's own logic would be
untested in most runs. This covers the parts that are arithmetic rather than
rendering: which types have a floor at all, how the overridable ones scale,
and that the reported reasons carry numbers an author can act on.
"""

from __future__ import annotations

import pytest

from openavc.ui.control_minimums import (
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    RULES,
    TYPES_WITH_MINIMUMS,
    minimum_box,
    minimum_percent,
)


@pytest.mark.parametrize("type_", TYPES_WITH_MINIMUMS)
def test_every_listed_type_has_a_floor(type_: str) -> None:
    box = minimum_box({"type": type_})
    assert box is not None, f"{type_} is listed but has no minimum"
    assert box.width_px > 0 and box.height_px > 0


@pytest.mark.parametrize(
    "type_", ["button", "label", "image", "clock", "page_nav", "camera_preset", "gauge", "group"]
)
def test_types_limited_only_by_their_text_have_no_floor(type_: str) -> None:
    """A button's limit is its caption, which is unbounded and theme-dependent.

    Returning a number here would be inventing one. These degrade rather than
    break, which is a different problem with a different fix.
    """
    assert minimum_box({"type": type_}) is None


def test_a_caption_raises_the_status_led_floor() -> None:
    """The dot is 20 and never shrinks; a caption adds a gap and needs room."""
    bare = minimum_box({"type": "status_led"})
    labelled = minimum_box({"type": "status_led", "label": "Mic 1"})
    assert (bare.width_px, bare.height_px) == (20, 20)
    assert (labelled.width_px, labelled.height_px) == (29, 20)


def test_a_bound_value_is_not_a_caption() -> None:
    """A status LED renders show.look and nothing else, so no text appears.

    This used to assert the opposite -- that a bound value "is a device name at
    runtime, so it still needs room". It is not rendered at all: panel.js builds
    .led-label under `if (element.label)`, which is what HONORED_SHOW_SLOTS
    records and what Phase 7 confirmed in a real browser. Widening the floor for
    it demanded 9px for text that never draws, while the review separately
    warned the same binding was inert.
    """
    for slot in ("value", "text"):  # `text` is not even a slot in the model
        bound = minimum_box(
            {"type": "status_led", "bindings": {"show": {slot: {"key": "device.x.name"}}}}
        )
        assert bound.width_px == 20, f"show.{slot} must not widen the floor"


@pytest.mark.parametrize("thumb_px", [44.0, 66.0, 88.0])
def test_the_slider_floor_tracks_its_thumb(thumb_px: float) -> None:
    """Measured linear with slope exactly 1 on both axes.

    The base is read from the rule rather than typed again: the SLOPE is what
    this pins, and the base moves whenever a machine measures the control
    higher (control_minimums, "When two machines disagree").
    """
    base = RULES["slider"]
    box = minimum_box({"type": "slider", "thumb_size": thumb_px / 14.0})
    assert box.width_px == base.base_width_px + thumb_px
    assert box.height_px == base.base_height_px + thumb_px


@pytest.mark.parametrize("row_px", [44.0, 66.0, 88.0])
def test_the_list_floor_tracks_its_row_height_but_not_its_width(row_px: float) -> None:
    base = RULES["list"]
    box = minimum_box({"type": "list", "item_height": row_px / 14.0})
    assert box.height_px == base.base_height_px + row_px
    assert box.width_px == base.base_width_px, (
        "row height does not change how wide a list must be"
    )


def test_a_theme_default_applies_when_the_element_says_nothing() -> None:
    themed = minimum_box({"type": "slider"}, theme={"thumb_size": 88.0 / 14.0})
    assert themed.width_px == 24 + 88.0


def test_an_element_value_beats_the_theme() -> None:
    """panel.js:1696 resolves element first, then theme, then 44."""
    box = minimum_box({"type": "slider", "thumb_size": 44.0 / 14.0},
                      theme={"thumb_size": 88.0 / 14.0})
    assert box.width_px == 24 + 44.0


def test_the_matrix_floor_does_not_grow_with_its_crosspoint_count() -> None:
    """It scrolls internally, so a bigger grid does not need a bigger box.

    Worth pinning because the obvious model -- cell size times inputs -- is
    wrong, and was written down as fact before anyone measured it.
    """
    small = minimum_box({"type": "matrix", "matrix_config": {"inputs": 2, "outputs": 2}})
    large = minimum_box({"type": "matrix", "matrix_config": {"inputs": 16, "outputs": 16}})
    assert (small.width_px, small.height_px) == (large.width_px, large.height_px)


def test_starvation_reasons_name_the_numbers_and_the_part() -> None:
    box = minimum_box({"type": "status_led", "label": "Mic 1"})
    reasons = box.starves(12, 8)
    assert len(reasons) == 2
    joined = " ".join(reasons)
    assert "12px wide" in joined and "29px" in joined
    assert "8px tall" in joined and "20px" in joined
    assert "led-dot" in joined, "a warning has to say which part does not fit"


def test_a_box_that_fits_reports_nothing() -> None:
    box = minimum_box({"type": "status_led", "label": "Mic 1"})
    assert box.starves(200, 60) == []


def test_percentages_are_taken_against_the_parent_not_the_page() -> None:
    """An element inside a container is measured against the container.

    This is the arithmetic the AI never had: 3% of a 1280px page is 38px, but
    3% of a half-width container is 19px, and only one of those fits a dot.
    """
    whole = minimum_percent({"type": "status_led"})
    half = minimum_percent(
        {"type": "status_led"},
        parent_width_px=REFERENCE_WIDTH_PX / 2,
        parent_height_px=REFERENCE_HEIGHT_PX / 2,
    )
    assert half[0] == pytest.approx(whole[0] * 2)
    assert half[1] == pytest.approx(whole[1] * 2)


def test_percent_is_none_where_there_is_no_floor() -> None:
    assert minimum_percent({"type": "button"}) is None
