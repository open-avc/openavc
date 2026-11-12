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


@pytest.mark.asyncio
async def test_add_elements_a_master_id_is_refused():
    """A press reaches the first element with an id, pages before masters, so a
    page control with a master's id would take the master's presses on its page."""
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_elements", {"page_id": "other", "elements": [
        {"id": "m_main", "type": "button", "placement": dict(BOX)},
    ]})
    assert result == {
        "error": "1 element could not be added: Element 'm_main' already exists as a master element",
        "errors": ["Element 'm_main' already exists as a master element"],
    }
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_add_page_an_inline_control_with_a_master_id_is_refused():
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_page", {"id": "fresh", "elements": [
        {"id": "ok", "type": "button", "placement": dict(BOX)},
        {"id": "m_every", "type": "button", "placement": dict(BOX)},
    ]})
    assert result == {
        "error": "1 element could not be added: Element 'm_every' already exists as a master element",
        "errors": ["Element 'm_every' already exists as a master element"],
    }
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_add_elements_an_id_another_page_holds_is_refused():
    """A press reaches the first page holding an id, so a second control with
    it would run the first one's actions."""
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_elements", {"page_id": "other", "elements": [
        {"id": "btn", "type": "button", "placement": dict(BOX)},
    ]})
    assert result == {
        "error": "1 element could not be added: Element 'btn' already exists on page 'main'",
        "errors": ["Element 'btn' already exists on page 'main'"],
    }
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_add_page_an_inline_id_another_page_holds_or_used_twice_is_refused():
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_page", {"id": "fresh", "elements": [
        {"id": "btn", "type": "button", "placement": dict(BOX)},
        {"id": "twin", "type": "button", "placement": dict(BOX)},
        {"id": "twin", "type": "label", "placement": dict(BOX)},
    ]})
    assert result["errors"] == [
        "Element 'btn' already exists on page 'main'",
        "Element 'twin' appears twice in this batch",
    ]
    assert engine.project.model_dump() == before


HIDE_REFUSAL = (
    "Element 'h1': 'hidden' is not set on a new control, since a hide belongs to an "
    "arrangement. Create it, then send update_ui_element with hidden: true (and a "
    "layout_id for an arrangement other than the primary)."
)


@pytest.mark.asyncio
async def test_add_elements_a_hidden_flag_is_refused_before_anything_lands():
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_elements", {"page_id": "other", "elements": [
        {"id": "h1", "type": "button", "hidden": True, "placement": dict(BOX)},
    ]})
    assert result == {"error": f"1 element could not be added: {HIDE_REFUSAL}", "errors": [HIDE_REFUSAL]}
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_add_page_an_inline_hidden_flag_is_refused():
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_page", {"id": "fresh", "elements": [
        {"id": "h1", "type": "button", "hidden": True, "placement": dict(BOX)},
    ]})
    assert result == {"error": f"1 element could not be added: {HIDE_REFUSAL}", "errors": [HIDE_REFUSAL]}
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_add_elements_hidden_false_is_taken_and_not_kept_on_the_control():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {"page_id": "other", "elements": [
        {"id": "h1", "type": "button", "hidden": False, "placement": dict(BOX)},
    ]})
    assert result["status"] == "created"
    page = next(p for p in engine.project.ui.pages if p.id == "other")
    control = next(el for el in page.elements if el.id == "h1")
    assert "hidden" not in control.model_dump()


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


# --- what a write reviews, and the create doors' order ----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"name": "Other"}, {"custom_config": {"a": 1}}])
async def test_page_a_rename_or_a_config_alone_is_not_reviewed(payload):
    engine = _engine()
    await _call(engine, "update_ui_page", {"page_id": "other", "render_mode": "custom"})
    result = await _call(engine, "update_ui_page", {"page_id": "other", **payload})
    assert result == {"status": "updated", "page_id": "other", "changed": list(payload)}


@pytest.mark.asyncio
async def test_add_page_a_control_naming_the_page_being_made_finds_it():
    engine = _engine()
    result = await _call(engine, "add_ui_page", {"id": "fresh", "elements": [
        {"id": "home", "type": "page_nav", "target_page": "fresh", "placement": dict(BOX)},
        {"id": "gone", "type": "page_nav", "target_page": "nowhere", "placement": dict(BOX)},
    ]})
    assert result["status"] == "created"
    assert [w for w in result["warnings"] if "fresh" in w and "home" in w] == []
    assert [w for w in result["warnings"] if "nowhere" in w]


@pytest.mark.asyncio
async def test_add_page_a_taken_id_is_refused_before_retired_geometry():
    engine = _engine()
    result = await _call(engine, "add_ui_page", {"id": "main", "grid": {"columns": 4}})
    assert result == {"error": "UI page 'main' already exists"}


@pytest.mark.asyncio
async def test_add_elements_an_empty_box_is_stored_as_the_default_one():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {"page_id": "other", "elements": [
        {"id": "plain", "type": "button", "placement": {}},
        {"id": "boxless", "type": "button"},
    ]})
    assert result["element_ids"] == ["plain", "boxless"]
    stored = _boxes(engine, "other", "landscape")
    assert "boxless" not in stored
    assert stored["plain"] == {"x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("box, raised", [(5, TypeError), ({"x": "abc"}, ValueError)])
async def test_element_a_bad_box_is_met_before_an_unhide_the_chain_refuses(box, raised):
    engine = _engine()
    before = engine.project
    with pytest.raises(raised):
        await _call(engine, "update_ui_element", {
            "element_id": "lbl", "layout_id": "portrait", "hidden": False, "placement": box,
        })
    assert engine.project is before
    result = await _call(engine, "update_ui_element", {
        "element_id": "lbl", "layout_id": "portrait", "hidden": False,
    })
    assert result["error"].startswith("Element 'lbl' is hidden by layout 'landscape'")
