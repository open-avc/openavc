"""The binding check at each of the five AI doors that take bindings, pinned.

A page created with elements, an element batch, an element update, a new
master and a master update each normalise and validate ``bindings`` before
anything lands. They differ on purpose in two ways, both pinned here: the two
create doors leave a ``bindings`` that is not an object to the model (which
refuses it in its own words), where the two update doors refuse it themselves
("must be an object"); and each door says whose bindings they were ("Element
'x'", "Master element 'm'"). What each door checks before the bindings, and
what it checks after, is pinned too, because a refusal names only the first
problem a single-element door finds.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import AIToolHandler

from tests.test_ai_ui_write_guards import _drain, _tool_call
from tests.test_ui_tools_lookups_pinned import _Devices, _make_engine

BAD = {"do": {"press": [{"action": "nope.nope"}]}}
REFUSED = (
    "Binding validation failed: do.press[0]: action type 'nope.nope' is not valid. "
    "Use: device.command, event.emit, macro, script.call, state.set, ui.navigate, value_map"
)
# One action written as an object, where every slot holds a list.
ONE = {"do": {"press": {"action": "ui.navigate", "page": "other"}}}
LISTED = {"do": {"press": [{"action": "ui.navigate", "page": "other"}]}}
BOX = {"x": 0, "y": 0, "w": 20, "h": 20}


@pytest.fixture(autouse=True)
def _patch_save_project():
    with patch("openavc.core.project_loader.save_project"):
        yield


async def _send(engine, tool: str, payload: dict) -> dict:
    """The whole message the handler sends back, success or not."""
    agent = MagicMock()
    agent.send_message = AsyncMock()
    agent.state = MagicMock()
    handler = AIToolHandler(agent, _Devices(), MagicMock(emit=AsyncMock()))
    with patch.object(handler, "_get_engine", return_value=engine):
        await handler.handle(_tool_call(tool, payload))
        await _drain()
    return agent.send_message.call_args[0][1]


async def _engine():
    """A page 'main' holding button 'x1', and a master button 'm1'."""
    engine = _make_engine(None)
    await _send(engine, "add_ui_elements", {
        "page_id": "main",
        "elements": [{"id": "x1", "type": "button", "placement": dict(BOX)}],
    })
    await _send(engine, "add_master_element", {"id": "m1", "type": "button"})
    return engine


def _error(sent: dict) -> str:
    assert sent["success"] is False
    return sent["result"]["error"]


def _stored(engine, element_id: str):
    for page in engine.project.ui.pages:
        for el in page.elements:
            if el.id == element_id:
                return el
    return next(m for m in engine.project.ui.master_elements if m.id == element_id)


# --- the create doors ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_page_refuses_every_bad_element_binding_at_once():
    engine = await _engine()
    sent = await _send(engine, "add_ui_page", {"id": "p2", "elements": [
        {"id": "a", "type": "button", "bindings": BAD, "placement": dict(BOX)},
        {"id": "b", "type": "button", "bindings": BAD},
    ]})
    assert sent["result"] == {
        "error": f"2 elements could not be added: Element 'a': {REFUSED}; Element 'b': {REFUSED}",
        "errors": [f"Element 'a': {REFUSED}", f"Element 'b': {REFUSED}"],
    }
    assert [p.id for p in engine.project.ui.pages] == ["main", "other"]


@pytest.mark.asyncio
async def test_a_new_page_leaves_a_binding_that_is_not_an_object_to_the_model():
    engine = await _engine()
    error = _error(await _send(engine, "add_ui_page", {"id": "p3", "elements": [
        {"id": "a", "type": "button", "bindings": "oops"},
    ]}))
    assert error.startswith(
        "Page 'p3' is not shaped right: 1 validation error for UIPage\n"
        "elements.0.bindings\n  Input should be a valid dictionary"
    )


@pytest.mark.asyncio
async def test_a_new_page_stores_one_action_as_a_list_and_rewrites_the_callers_dict():
    engine = await _engine()
    element = {"id": "a", "type": "button", "bindings": json.loads(json.dumps(ONE))}
    payload = {"id": "p4", "elements": [element]}
    sent = await _send(engine, "add_ui_page", payload)
    assert sent["success"] is True
    assert _stored(engine, "a").bindings == LISTED
    assert element["bindings"] == LISTED


@pytest.mark.asyncio
async def test_an_element_batch_refuses_a_bad_binding_and_one_that_is_not_an_object():
    engine = await _engine()
    result = (await _send(engine, "add_ui_elements", {"page_id": "main", "elements": [
        {"id": "a", "type": "button", "bindings": BAD},
        {"id": "b", "type": "button", "bindings": "oops"},
        {"id": "c", "type": "button", "bindings": json.loads(json.dumps(ONE))},
    ]}))["result"]
    assert result["errors"][0] == f"Element 'a': {REFUSED}"
    assert result["errors"][1].startswith(
        "Element 'b': 1 validation error for UIElement\nbindings\n"
        "  Input should be a valid dictionary"
    )
    assert len(result["errors"]) == 2
    assert result["error"].startswith(f"2 elements could not be added: Element 'a': {REFUSED}; ")


@pytest.mark.asyncio
async def test_an_element_batch_stores_one_action_as_a_list_and_rewrites_the_callers_dict():
    engine = await _engine()
    element = {"id": "c", "type": "button", "bindings": json.loads(json.dumps(ONE)),
               "placement": {"x": 30, "y": 0, "w": 20, "h": 20}}
    sent = await _send(engine, "add_ui_elements", {"page_id": "main", "elements": [element]})
    assert sent["success"] is True
    assert _stored(engine, "c").bindings == LISTED
    assert element["bindings"] == LISTED


@pytest.mark.asyncio
async def test_a_new_master_refuses_a_bad_binding_after_its_id_is_checked():
    engine = await _engine()
    assert _error(await _send(engine, "add_master_element", {
        "id": "m2", "type": "button", "bindings": BAD,
    })) == f"Master element 'm2': {REFUSED}"
    # The id is checked first: a clash with a page element is the answer.
    assert _error(await _send(engine, "add_master_element", {
        "id": "x1", "type": "button", "bindings": BAD,
    })) == "Element 'x1' already exists on page 'main'"
    assert _error(await _send(engine, "add_master_element", {
        "id": "m1", "type": "button", "bindings": BAD,
    })) == "Master element 'm1' already exists"
    # And a box given the page way before that.
    assert _error(await _send(engine, "add_master_element", {
        "id": "x1", "type": "button", "bindings": BAD, "placement": dict(BOX),
    })).startswith("Master element 'x1': use 'placements' keyed by orientation")


@pytest.mark.asyncio
async def test_a_new_master_leaves_a_binding_that_is_not_an_object_to_the_model():
    engine = await _engine()
    sent = await _send(engine, "add_master_element", {"id": "m2", "type": "button", "bindings": "oops"})
    # Not a door's refusal: the model raises, and the dispatcher words it.
    assert sent["success"] is False
    assert sent["result"] is None
    assert sent["error"] == "bindings: Input should be a valid dictionary"
    assert [m.id for m in engine.project.ui.master_elements] == ["m1"]


@pytest.mark.asyncio
async def test_a_new_master_stores_one_action_as_a_list():
    engine = await _engine()
    payload = {"id": "m2", "type": "button", "bindings": json.loads(json.dumps(ONE))}
    sent = await _send(engine, "add_master_element", payload)
    assert sent["success"] is True
    assert _stored(engine, "m2").bindings == LISTED
    assert payload["bindings"] == LISTED


# --- the update doors ---------------------------------------------------------


@pytest.mark.asyncio
async def test_an_element_update_refuses_a_binding_that_is_not_an_object_itself():
    engine = await _engine()
    assert _error(await _send(engine, "update_ui_element", {
        "element_id": "x1", "bindings": "oops",
    })) == "Element 'x1': 'bindings' must be an object, got str"
    assert _error(await _send(engine, "update_ui_element", {
        "element_id": "x1", "bindings": ["a"],
    })) == "Element 'x1': 'bindings' must be an object, got list"


@pytest.mark.asyncio
async def test_an_element_update_checks_layout_then_bindings_then_the_lock():
    engine = await _engine()
    assert _error(await _send(engine, "update_ui_element", {
        "element_id": "x1", "bindings": BAD, "layout_id": "nope",
    })) == "Layout 'nope' not found on page 'main'. Available: landscape"
    assert _error(await _send(engine, "update_ui_element", {
        "element_id": "x1", "bindings": BAD, "label": "Renamed",
    })) == f"Element 'x1': {REFUSED}"
    assert _stored(engine, "x1").label != "Renamed"
    await _send(engine, "update_ui_element", {"element_id": "x1", "locked": True})
    assert _error(await _send(engine, "update_ui_element", {
        "element_id": "x1", "bindings": BAD, "placement": {"x": 50},
    })) == f"Element 'x1': {REFUSED}"
    assert _error(await _send(engine, "update_ui_element", {
        "element_id": "x1", "bindings": LISTED, "placement": {"x": 50},
    })).startswith("Element 'x1' is locked, so its placement cannot be changed")


@pytest.mark.asyncio
async def test_an_element_update_stores_one_action_as_a_list():
    engine = await _engine()
    payload = {"element_id": "x1", "bindings": json.loads(json.dumps(ONE))}
    sent = await _send(engine, "update_ui_element", payload)
    assert sent["result"]["changed"] == ["bindings"]
    assert _stored(engine, "x1").bindings == LISTED
    assert payload["bindings"] == LISTED


@pytest.mark.asyncio
async def test_a_master_update_refuses_a_binding_that_is_not_an_object_itself():
    engine = await _engine()
    assert _error(await _send(engine, "update_master_element", {
        "element_id": "m1", "bindings": "oops",
    })) == "Master element 'm1': 'bindings' must be an object, got str"


@pytest.mark.asyncio
async def test_a_master_update_checks_bindings_before_its_placements():
    engine = await _engine()
    assert _error(await _send(engine, "update_master_element", {
        "element_id": "m1", "bindings": BAD, "placements": "x",
    })) == f"Master element 'm1': {REFUSED}"
    assert _error(await _send(engine, "update_master_element", {
        "element_id": "m1", "bindings": LISTED, "placements": "x",
    })) == "Master element 'm1': 'placements' must be an object keyed by orientation"
    assert _error(await _send(engine, "update_master_element", {
        "element_id": "m1", "bindings": BAD, "placement": dict(BOX),
    })).startswith("Master element 'm1': use 'placements' keyed by orientation")


@pytest.mark.asyncio
async def test_a_master_update_stores_one_action_as_a_list_and_names_it_first():
    engine = await _engine()
    payload = {"element_id": "m1", "label": "Home", "bindings": json.loads(json.dumps(ONE))}
    sent = await _send(engine, "update_master_element", payload)
    assert sent["result"]["changed"] == ["bindings", "label"]
    assert _stored(engine, "m1").bindings == LISTED
    assert payload["bindings"] == LISTED


@pytest.mark.asyncio
async def test_a_master_update_on_a_page_element_or_nothing_names_the_other_door():
    engine = await _engine()
    assert _error(await _send(engine, "update_master_element", {
        "element_id": "x1", "bindings": BAD,
    })) == ("'x1' is an element on page 'main', not a master. "
            "Use update_ui_element to change it.")
    assert _error(await _send(engine, "update_master_element", {
        "element_id": "zz", "bindings": BAD,
    })) == "Master element 'zz' not found"
