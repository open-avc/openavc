"""What a UI write is told back, and how a batch of new controls is refused, pinned.

A write's review measures what it touched against the project's theme and the
masters that draw under the page, and fills a declared range in only when the
call said something about the range. A batch names every refusal, in the order
each definition is checked, under the id it was given (``'?'`` for none).
"""

from __future__ import annotations

import pytest

from tests.test_ui_tools_review_masters_pinned import (
    BIG_THUMB,
    _call,
    _engine,
    _project,
)

#: The fader's declared bounds, as filled in from the driver.
FILLED = "fill_me"


def _el(engine, page_id, el_id):
    page = next(p for p in engine.project.ui.pages if p.id == page_id)
    return next(el for el in page.elements if el.id == el_id)


@pytest.mark.asyncio
async def test_an_update_that_says_nothing_about_a_range_fills_none_in():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {"element_id": FILLED, "label": "Lvl"})
    assert "auto_filled" not in result
    el = _el(engine, "main", FILLED)
    assert (el.min, el.max) == (None, None)


@pytest.mark.asyncio
async def test_an_update_of_the_binding_fills_the_declared_range_in():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": FILLED,
        "bindings": {"show": {"value": {"key": "device.amp.level"}}},
    })
    assert result["auto_filled"]
    el = _el(engine, "main", FILLED)
    assert (el.min, el.max) == (-60.0, 6.0)


@pytest.mark.asyncio
async def test_a_page_layouts_edit_fills_none_in():
    engine = _engine()
    result = await _call(engine, "update_ui_page", {
        "page_id": "main",
        "layouts": [{"id": "landscape", "placements": {FILLED: {"x": 10, "y": 80, "w": 61, "h": 15}}}],
    })
    assert "auto_filled" not in result
    assert (_el(engine, "main", FILLED).min, _el(engine, "main", FILLED).max) == (None, None)


@pytest.mark.asyncio
async def test_a_new_control_over_a_master_is_told_so():
    result = await _call(_engine(), "add_ui_elements", {"page_id": "clean", "elements": [{
        "id": "over", "type": "button", "label": "Over",
        "placement": {"x": 45, "y": 0, "w": 30, "h": 15},
    }]})
    assert any(
        w.startswith("over (button) is drawn over the master element m_fine")
        for w in result["warnings"]
    ), result


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", [True, False])
async def test_a_new_slider_is_measured_against_the_projects_theme(theme):
    result = await _call(_engine(_project(theme=theme)), "add_ui_elements", {
        "page_id": "clean", "elements": [{
            "id": "vol", "type": "slider", "placement": {"x": 10, "y": 50, "w": 60, "h": 20},
        }],
    })
    assert any(BIG_THUMB in w for w in result.get("warnings", [])) is theme


@pytest.mark.asyncio
@pytest.mark.parametrize("element, first", [
    ({"id": "e", "type": "button", "placement": "x", "bindings": {"do": "x"}},
     "Element 'e': 'placement' must be an object"),
    ({"id": "e", "type": "button", "grid_area": "a", "bindings": {"do": "x"}},
     "Element 'e': 'grid_area' was removed"),
    ({"id": "e", "type": "button", "bindings": {"do": "x"}, "parent": "nowhere"},
     "Element 'e': Binding validation failed"),
])
async def test_each_new_control_is_refused_for_its_first_problem(element, first):
    result = await _call(_engine(), "add_ui_elements", {"page_id": "clean", "elements": [element]})
    assert result["errors"][0].startswith(first), result
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
async def test_a_control_with_no_id_is_named_by_a_question_mark():
    result = await _call(_engine(), "add_ui_elements", {"page_id": "clean", "elements": [
        {"type": "button"},
    ]})
    assert result["errors"][0].startswith("Element '?': "), result


@pytest.mark.asyncio
async def test_an_empty_parent_is_a_container_that_is_not_there():
    result = await _call(_engine(), "add_ui_elements", {"page_id": "clean", "elements": [
        {"id": "e", "type": "button", "parent": ""},
    ]})
    assert result["errors"] == [
        "Element 'e': container '' is not an element on page 'clean'",
    ]
