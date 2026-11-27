"""A tool input sent as the wrong JSON type is refused before the tool runs.

Each tool's declaration (``cloud/tool_declarations.py``) says what it takes,
and nothing enforced it on the way here. Each case below reached a tool and
failed inside it with Python's own error, which the cloud was told only as
the fixed sentence; it is now answered with the input and the shape it must
have, and the tool is never called.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.cloud.ai_tool_handler import TOOL_FAILED, AIToolHandler
from openavc.cloud.tools.input_shapes import input_problem


def _handler() -> AIToolHandler:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return AIToolHandler(agent, MagicMock(), MagicMock())


async def _answer(handler: AIToolHandler, tool: str, tool_input) -> tuple[dict, AsyncMock]:
    """The payload sent back, with the tool replaced so a call is seen."""
    tool_fn = AsyncMock(return_value={"status": "ok"})
    await handler._execute_tool("req-1", tool, tool_fn, tool_input)
    return handler._agent.send_message.call_args[0][1], tool_fn


# Each was a crash inside the tool before this check (the sweep that found
# them is in the module docstring's terms: wrong JSON type, field by field).
WRONG = [
    ("get_device_info", {"device_id": ["projector1"]}, "device_id must be a string, not an array."),
    ("delete_device", {"device_id": 5}, "device_id must be a string, not a number."),
    ("delete_variable", {"id": {"a": 1}}, "id must be a string, not an object."),
    ("execute_macro", {"macro_id": ["m"]}, "macro_id must be a string, not an array."),
    ("test_trigger", {"trigger_id": {"t": 1}}, "trigger_id must be a string, not an object."),
    ("uninstall_plugin", {"plugin_id": True}, "plugin_id must be a string, not a boolean."),
    ("add_device", {"id": "d9", "driver": ["acme"]}, "driver must be a string, not an array."),
    ("set_device_setting", {"device_id": "d", "setting_key": ["k"]}, "setting_key must be a string, not an array."),
    ("add_variable", {"id": "v", "type": {"t": 1}}, "type must be a string, not an object."),
    ("update_ui_element", {"element_id": "e", "parent": ["c"]}, "parent must be a string or null, not an array."),
    ("get_state_value", {"key": 5}, "key must be a string, not a number."),
    ("set_state_value", {"key": ["var.x"]}, "key must be a string, not an array."),
    ("get_logs", {"level": 5}, "level must be a string, not a number."),
    ("get_logs", {"search": ["x"]}, "search must be a string, not an array."),
    ("update_script_source", {"script_id": "s", "source": 5}, "source must be a string, not a number."),
    ("create_script", {"id": "s", "file": ["a.py"]}, "file must be a string, not an array."),
    ("test_driver_command", {"command_string": 5}, "command_string must be a string, not a number."),
    ("test_driver_command", {"host": {"h": 1}}, "host must be a string, not an object."),
    ("test_driver_command", {"delimiter": 5}, "delimiter must be a string, not a number."),
    ("search_community_drivers", {"query": 5}, "query must be a string, not a number."),
    ("search_community_drivers", {"category": ["switcher"]}, "category must be a string, not an array."),
    ("search_community_drivers", {"transport": {"t": 1}}, "transport must be a string, not an object."),
    ("find_driver_for_device", {"manufacturer": ["Acme"], "model": "X"}, "manufacturer must be a string, not an array."),
    ("find_driver_for_device", {"manufacturer": "Acme", "model": 100}, "model must be a string, not a number."),
    ("update_device", {"device_id": "d", "config": "x"}, "config must be an object, not a string."),
    ("add_device", {"id": "d9", "config": ["a"]}, "config must be an object, not an array."),
    ("create_driver_definition", {"definition": "x"}, "definition must be an object, not a string."),
    ("update_ui_element", {"element_id": "e", "placement": 5}, "placement must be an object, not a number."),
    ("update_ui_page", {"page_id": "p", "snap": ["a"]}, "snap must be an object, not an array."),
    ("update_ui_page", {"page_id": "p", "overlay": "x"}, "overlay must be an object, not a string."),
    ("update_ui_page", {"page_id": "p", "background": 5}, "background must be an object, not a number."),
    ("add_device_group", {"id": "g", "device_ids": 5}, "device_ids must be an array of strings, not a number."),
    ("add_device_group", {"id": "g", "device_ids": ["a", 5]}, "device_ids must be an array of strings: item 2 is a number."),
    ("add_ui_elements", {"page_id": "p", "elements": {"a": 1}}, "elements must be an array of objects, not an object."),
    ("add_ui_elements", {"page_id": "p", "elements": [{"id": "b"}, "x"]}, "elements must be an array of objects: item 2 is a string."),
    ("add_ui_page", {"id": "p2", "elements": [None]}, "elements must be an array of objects: item 1 is null."),
]


@pytest.mark.parametrize(("tool", "tool_input", "sentence"), WRONG)
async def test_a_wrong_typed_field_is_answered_by_name_and_the_tool_never_runs(tool, tool_input, sentence):
    payload, tool_fn = await _answer(_handler(), tool, tool_input)

    assert payload["success"] is False
    assert payload["error"] == sentence
    assert payload["error"] != TOOL_FAILED
    tool_fn.assert_not_awaited()


@pytest.mark.parametrize(("tool", "tool_input"), [
    ("update_device", {"device_id": "projector1", "config": None}),  # null is the tool's own call (update_device keeps the config)
    ("update_ui_element", {"element_id": "e", "parent": None}),  # null brings an element out of its container
    ("add_device_group", {"id": "g", "device_ids": ["a", "b"]}),
    ("add_ui_elements", {"page_id": "p", "elements": [{"id": "b1", "placement": {"x": 2, "y": 2}}]}),
    ("set_state_value", {"key": "var.x", "value": ["any", {"shape": 1}]}),  # any value at all
    ("set_state_value", {"key": "var.x", "value": None}),  # a required input sent as null was sent
    ("update_variable", {"id": "v", "default": 5}),
    ("send_device_command", {"device_id": "d", "command": "c", "params": {"level": 3}}),
    ("update_device", {"device_id": "d", "layout_id": 3}),  # an input the tool does not declare
    ("check_references", {"type": "page", "id": "p1"}),  # an allowed value is the tool's own check
    ("get_state_history", {"count": 5.0}),  # a whole number sent with a point
    ("update_master_element", {"element_id": "m1", "pages": ["main"], "placements": {"portrait": {"x": 1}}}),
])
async def test_an_input_of_the_right_shape_or_null_reaches_the_tool(tool, tool_input):
    payload, tool_fn = await _answer(_handler(), tool, tool_input)

    assert payload["success"] is True
    tool_fn.assert_awaited_once_with(tool_input)


@pytest.mark.parametrize(("tool_input", "kind"), [("x", "a string"), ([1], "an array"), (5, "a number")])
async def test_an_input_that_is_not_an_object_is_refused(tool_input, kind):
    payload, tool_fn = await _answer(_handler(), "list_devices", tool_input)

    assert payload["error"] == f"The tool's input must be an object, not {kind}."
    tool_fn.assert_not_awaited()


def test_a_tool_checks_what_it_declares_and_nothing_else():
    assert input_problem("update_ui_element", {"element_id": "e", "layout_id": 3}) == (
        "layout_id must be a string, not a number."
    )
    assert input_problem("update_device", {"device_id": "d", "layout_id": 3}) is None
    assert input_problem("no_such_tool", {"anything": 3}) is None


@pytest.mark.parametrize(("tool", "tool_input", "sentence"), [
    ("get_macro", {}, "macro_id is required."),
    ("set_state_value", {"key": "var.x"}, "value is required."),
    ("get_state_history", {"count": 5.5}, "count must be a whole number, not 5.5."),
    ("get_state_history", {"count": True}, "count must be a whole number, not a boolean."),
    ("test_driver_command", {"host": "h", "command_string": "c", "port": [23]},
     "port must be a whole number or a string, not an array."),
    ("add_master_element", {"id": "m1", "type": "button", "pages": 5},
     "pages must be a string or an array of strings, not a number."),
    # A wrong shape is answered before a required input left out.
    ("send_device_command", {"command": 5}, "command must be a string, not a number."),
])
def test_what_a_call_is_refused_for(tool, tool_input, sentence):
    assert input_problem(tool, tool_input) == sentence


# --- Inside an input: the boxes ---

# A box that was not an object, or a coordinate that was not a number, reached
# the door and failed inside it (a TypeError from the page review, a
# ValidationError from the box's model), which the cloud was told only as the
# fixed sentence.
BOXES = [
    ("update_master_element", {"element_id": "m1", "placements": {"portrait": "raw"}},
     "placements.portrait must be an object, not a string."),
    ("add_master_element", {"id": "m1", "type": "button", "placements": {"landscape": [1, 2]}},
     "placements.landscape must be an object, not an array."),
    ("update_master_element", {"element_id": "m1", "placements": {"landscape": {"x": "abc"}}},
     "placements.landscape.x must be a number, not a string."),
    ("update_ui_page", {"page_id": "p", "layouts": [{"id": "primary", "placements": {"b1": 5}}]},
     "layouts item 1: placements.b1 must be an object, not a number."),
    ("add_ui_page", {"id": "p2", "name": "P", "layouts": [{"placements": {"b1": {"w": True}}}]},
     "layouts item 1: placements.b1.w must be a number, not a boolean."),
    ("update_ui_element", {"element_id": "e", "placement": {"x": "abc"}},
     "placement.x must be a number, not a string."),
    # JSON has no infinite number, but a figure too big for a float (1e400)
    # parses as one, and the box's model refuses it.
    ("update_ui_element", {"element_id": "e", "placement": {"w": float("inf")}},
     "placement.w must be a number, not Infinity."),
    ("update_ui_page", {"page_id": "p", "layouts": [
        {"id": "primary", "placements": {"b1": {"x": float("-inf")}}}]},
     "layouts item 1: placements.b1.x must be a number, not -Infinity."),
    ("update_master_element", {"element_id": "m1", "placements": {"landscape": {"h": float("nan")}}},
     "placements.landscape.h must be a number, not NaN."),
]


@pytest.mark.parametrize(("tool", "tool_input", "sentence"), BOXES)
async def test_a_box_of_the_wrong_shape_is_answered_by_name_and_the_tool_never_runs(
    tool, tool_input, sentence,
):
    payload, tool_fn = await _answer(_handler(), tool, tool_input)

    assert payload["success"] is False
    assert payload["error"] == sentence
    tool_fn.assert_not_awaited()
