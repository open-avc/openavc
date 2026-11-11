"""The AI's UI write doors, pinned where a deliberate break found no test.

A batch of new controls says back only the grants it set, a master update
names an empty box map or an empty bindings object as changed and refuses
either when it is not an object, and a locked control can still be hidden or
shown in an arrangement. Each records what a door does today.
"""

from __future__ import annotations

import pytest

from tests.test_ui_tools_doors_pinned import _call, _engine, _layout


@pytest.mark.asyncio
async def test_add_elements_with_no_grant_says_nothing_about_grants():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {
        "page_id": "other",
        "elements": [{"id": "a1", "type": "button"}, {"id": "a2", "type": "label"}],
    })
    assert result["element_ids"] == ["a1", "a2"]
    assert "grants" not in result and "grant" not in result


@pytest.mark.asyncio
async def test_add_elements_names_only_the_controls_given_a_grant():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {
        "page_id": "other",
        "elements": [
            {"id": "a1", "type": "button"},
            {"id": "a2", "type": "button", "grant": {"devices": ["amp"]}},
        ],
    })
    assert list(result["grants"]) == ["a2"]
    assert result["grants"]["a2"]["devices"] == ["amp"]


@pytest.mark.asyncio
async def test_master_an_empty_box_map_is_named_as_changed():
    engine = _engine()
    result = await _call(engine, "update_master_element", {
        "element_id": "m_both", "placements": {},
    })
    assert result == {"status": "updated", "element_id": "m_both", "changed": ["placements"]}


@pytest.mark.asyncio
async def test_master_a_box_map_that_is_not_an_object_is_refused():
    engine = _engine()
    result = await _call(engine, "update_master_element", {
        "element_id": "m_both", "placements": None,
    })
    assert result == {"error": "Master element 'm_both': 'placements' must be an "
                               "object keyed by orientation"}


@pytest.mark.asyncio
async def test_element_a_locked_control_can_still_be_hidden_in_an_arrangement():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": "pin", "layout_id": "portrait", "hidden": True,
    })
    assert result == {"status": "updated", "element_id": "pin", "changed": ["hidden"]}
    assert "pin" in _layout(engine, "main", "portrait").hidden


@pytest.mark.asyncio
async def test_master_an_empty_bindings_object_is_named_as_changed():
    engine = _engine()
    result = await _call(engine, "update_master_element", {
        "element_id": "m_both", "bindings": {},
    })
    assert result == {"status": "updated", "element_id": "m_both", "changed": ["bindings"]}


@pytest.mark.asyncio
async def test_master_bindings_that_are_not_an_object_are_refused():
    engine = _engine()
    result = await _call(engine, "update_master_element", {
        "element_id": "m_both", "bindings": None,
    })
    assert result == {"error": "Master element 'm_both': 'bindings' must be an "
                               "object, got NoneType"}
