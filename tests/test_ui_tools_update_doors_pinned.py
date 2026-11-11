"""The AI's UI update and delete doors, pinned where the earlier pins left a gap.

A stated box in a variant that stores its own, the master's boxes as stored, a
grant said back only when the call named one, which page update is reviewed,
what counts as saying something about a range, the order an element update
names what it changed, a taken id refused for that alone, and the master
delete door. Each records what a door does today; some of it looks odd (an
update stores a master box that is not an object as it was sent) and is
pinned as it is, not corrected here.
"""

from __future__ import annotations

import copy

import pytest

from tests.test_ui_tools_doors_pinned import (
    BOX,
    _boxes,
    _call,
    _engine,
)
from tests.test_ui_tools_review_masters_pinned import (
    _call as _review_call,
    _engine as _review_engine,
)
from tests.test_ui_tools_write_review_pinned import FILLED, _el as _review_el


def _master(engine, master_id: str):
    return next(m for m in engine.project.ui.master_elements if m.id == master_id)


# --- update_ui_element ----------------------------------------------------------


@pytest.mark.asyncio
async def test_element_a_box_in_a_variant_merges_over_the_box_it_stores_there():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": "kid", "layout_id": "portrait", "placement": {"x": 5},
    })
    assert result["changed"] == ["placement"]
    assert _boxes(engine, "main", "portrait")["kid"] == {"x": 5.0, "y": 0, "w": 100, "h": 50}
    assert _boxes(engine, "main", "landscape")["kid"] == {"x": 10, "y": 20, "w": 40, "h": 40}


@pytest.mark.asyncio
async def test_element_names_what_changed_sorted_whatever_order_it_was_sent_in():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": "btn", "text": "Go", "label": "Go", "hidden": True,
        "placement": {"x": 1},
    })
    assert result["changed"] == ["hidden", "label", "placement", "text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field, value", [("unit", "dB"), ("step", 1)])
async def test_element_a_unit_or_a_step_alone_says_something_about_the_range(field, value):
    engine = _review_engine()
    result = await _review_call(engine, "update_ui_element", {"element_id": FILLED, field: value})
    assert result["auto_filled"]
    el = _review_el(engine, "main", FILLED)
    assert (el.min, el.max) == (-60.0, 6.0)


# --- update_ui_page --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"grant": {"devices": ["amp"]}},
    {"custom_file": ""},
])
async def test_page_a_grant_or_a_file_alone_is_reviewed(payload):
    engine = _engine()
    await _call(engine, "update_ui_page", {"page_id": "other", "render_mode": "custom"})
    result = await _call(engine, "update_ui_page", {"page_id": "other", **payload})
    assert result["changed"] == list(payload)
    assert result["warnings"] == [
        "other is set to show a page you wrote but names no file, so it still draws its "
        "controls. Choose a file in the project's ui/ folder, or set the page back to controls."
    ]


# --- add_ui_elements -------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_elements_a_taken_id_is_refused_for_that_alone_and_left_as_sent():
    engine = _engine()
    element = {"id": "btn", "type": "button", "grid_area": "a", "placement": dict(BOX)}
    sent = copy.deepcopy(element)
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": [element]})
    assert result == {
        "error": "1 element could not be added: Element 'btn' already exists on page 'main'",
        "errors": ["Element 'btn' already exists on page 'main'"],
    }
    assert element == sent


# --- update_master_element -------------------------------------------------------


@pytest.mark.asyncio
async def test_master_an_update_that_names_no_grant_says_none_back():
    engine = _engine()
    result = await _call(engine, "update_master_element", {"element_id": "m_both", "label": "Nav"})
    assert result == {"status": "updated", "element_id": "m_both", "changed": ["label"]}


@pytest.mark.asyncio
async def test_master_an_update_merges_boxes_and_a_box_not_an_object_reaches_the_review():
    engine = _engine()
    await _call(engine, "update_master_element", {
        "element_id": "m_both", "placements": {"landscape": {"x": 1.00004, "y": 2, "w": 3, "h": 4}},
    })
    await _call(engine, "update_master_element", {
        "element_id": "m_both", "placements": {"portrait": dict(BOX)},
    })
    stored = _master(engine, "m_both").placements
    assert list(stored) == ["landscape", "portrait"]
    assert stored["landscape"].model_dump() == {"x": 1.0, "y": 2, "w": 3, "h": 4}
    before = engine.project
    with pytest.raises(TypeError, match="vars"):
        await _call(engine, "update_master_element", {
            "element_id": "m_both", "placements": {"portrait": "junk"},
        })
    assert engine.project is before


@pytest.mark.asyncio
async def test_master_a_new_box_not_an_object_is_handed_to_the_model_as_sent():
    engine = _engine()
    with pytest.raises(ValueError, match="input_value='junk'"):
        await _call(engine, "add_master_element", {
            "id": "logo", "type": "image", "placements": {"landscape": dict(BOX), "portrait": "junk"},
        })


# --- delete_master_element -------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_master_removes_only_the_one_named():
    engine = _engine()
    result = await _call(engine, "delete_master_element", {"element_id": "m_other"})
    assert result == {"status": "deleted", "element_id": "m_other"}
    assert [m.id for m in engine.project.ui.master_elements] == ["m_both", "m_every", "m_main"]


@pytest.mark.asyncio
async def test_delete_master_not_found_changes_nothing():
    engine = _engine()
    before = engine.project
    result = await _call(engine, "delete_master_element", {"element_id": "btn"})
    assert result == {"error": "Master element 'btn' not found"}
    assert engine.project is before


@pytest.mark.asyncio
async def test_delete_master_with_no_project():
    engine = _engine()
    engine.project = None
    result = await _call(engine, "delete_master_element", {"element_id": "m_other"})
    assert result == {"error": "No project loaded"}
