"""A tool input field sent as the wrong JSON type is refused before the tool runs.

The cloud's tool definitions declare each field's type, and nothing enforces
it on the way here. Each case below reached a tool and failed inside it with
Python's own error, which the cloud was told only as the fixed sentence; it is
now answered with the field and the shape it must have, and the tool is never
called.
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
    ("update_ui_element", {"page_id": "p", "element_id": "e", "parent": ["c"]}, "parent must be a string, not an array."),
    ("get_state_value", {"key": 5}, "key must be a string, not a number."),
    ("set_state_value", {"key": ["var.x"]}, "key must be a string, not an array."),
    ("get_logs", {"level": 5}, "level must be a string, not a number."),
    ("get_logs", {"search": ["x"]}, "search must be a string, not an array."),
    ("update_script_source", {"script_id": "s", "source": 5}, "source must be a string, not a number."),
    ("create_script", {"id": "s", "file": ["a.py"]}, "file must be a string, not an array."),
    ("test_driver_command", {"command_string": 5}, "command_string must be a string, not a number."),
    ("test_driver_command", {"host": {"h": 1}}, "host must be a string, not an object."),
    ("test_driver_command", {"delimiter": 5}, "delimiter must be a string, not a number."),
    ("update_device", {"device_id": "d", "config": "x"}, "config must be an object, not a string."),
    ("add_device", {"id": "d9", "config": ["a"]}, "config must be an object, not an array."),
    ("create_driver_definition", {"definition": "x"}, "definition must be an object, not a string."),
    ("update_ui_element", {"page_id": "p", "element_id": "e", "placement": 5}, "placement must be an object, not a number."),
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


@pytest.mark.parametrize("tool_input", [
    {"device_id": "projector1", "config": None},  # null is the tool's own call (update_device keeps the config)
    {"page_id": "p", "element_id": "e", "parent": None},  # null brings an element out of its container
    {"id": "g", "device_ids": ["a", "b"], "elements": [{"id": "b1"}]},
    {"value": ["any", {"shape": 1}], "default": 5, "params": {"level": 3}},  # fields not in the table
])
async def test_a_field_of_the_right_shape_or_null_reaches_the_tool(tool_input):
    payload, tool_fn = await _answer(_handler(), "update_device", tool_input)

    assert payload["success"] is True
    tool_fn.assert_awaited_once_with(tool_input)


@pytest.mark.parametrize(("tool_input", "kind"), [("x", "a string"), ([1], "an array"), (5, "a number")])
async def test_an_input_that_is_not_an_object_is_refused(tool_input, kind):
    payload, tool_fn = await _answer(_handler(), "list_devices", tool_input)

    assert payload["error"] == f"The tool's input must be an object, not {kind}."
    tool_fn.assert_not_awaited()


def test_every_id_field_is_text_whatever_its_name():
    assert input_problem({"layout_id": 3}) == "layout_id must be a string, not a number."
    assert input_problem({"element_ids": ["a"]}) is None
