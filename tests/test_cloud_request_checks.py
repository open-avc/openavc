"""A request from the cloud is checked against the wire contract before any
handler runs.

The checks are the agent's door for every downstream request (command,
config_push, restart, diagnostic, software_update, panel_software_update,
ai_tool_call, get_project, get_device_commands): a field of the wrong JSON
type is answered with a refusal that names it, in the request's own result
type; a request with no request_id, or whose payload is not an object, has
nothing an answer could be matched to, so it is not run.
"""

from __future__ import annotations

import json
import logging

import pytest

from openavc.cloud.protocol import (
    REQUEST_ANSWERS,
    RequestRefused,
    read_request,
)


class RecordingHandler:
    """Stands in for every handler the agent dispatches to."""

    def __init__(self):
        self.handled: list[dict] = []

    async def handle(self, msg):
        self.handled.append(msg)


def _agent(handler=None, capabilities=("remote_access", "diagnostics", "fleet_update")):
    from openavc.cloud.agent import CloudAgent

    agent = CloudAgent.__new__(CloudAgent)
    agent._session = None
    agent._connected = False
    agent._enabled_capabilities = list(capabilities)
    agent._command_handler = handler or RecordingHandler()
    agent._ai_tool_handler = agent._command_handler
    agent._tunnel_handler = None
    agent.sent = []

    async def spy_send(msg_type, payload):
        agent.sent.append((msg_type, payload))

    agent.send_message = spy_send
    return agent


# --- Nothing to answer with: not run ---


@pytest.mark.parametrize("payload", ["projector", ["r1"], 5, None])
@pytest.mark.asyncio
async def test_a_request_whose_payload_is_not_an_object_is_not_run(payload, caplog):
    agent = _agent()
    with caplog.at_level(logging.WARNING, logger="openavc.cloud.agent"):
        await agent._handle_message({"type": "command", "payload": payload})
    assert agent._command_handler.handled == []
    assert agent.sent == []
    assert "command" in caplog.text and "not an object" in caplog.text


@pytest.mark.parametrize("request_id", ["<absent>", "", None, 17])
@pytest.mark.asyncio
async def test_a_request_with_no_request_id_is_not_run(request_id, caplog):
    payload = {"device_id": "projector", "command": "power_on", "params": {}}
    if request_id != "<absent>":
        payload["request_id"] = request_id
    agent = _agent()
    with caplog.at_level(logging.WARNING, logger="openavc.cloud.agent"):
        await agent._handle_message({"type": "command", "payload": payload})
    assert agent._command_handler.handled == []
    assert agent.sent == []
    assert "request_id" in caplog.text


# --- A field of the wrong type: refused by name, in the request's own answer ---


@pytest.mark.asyncio
async def test_an_auto_restart_sent_as_text_is_refused_and_nothing_is_updated():
    agent = _agent()
    await agent._handle_message({"type": "software_update", "payload": {
        "request_id": "r1", "target_version": "0.40.0",
        "update_url": "https://example.invalid/openavc-0.40.0.tar.gz",
        "auto_restart": "false",
    }})
    assert agent._command_handler.handled == []
    assert agent.sent == [("command_result", {
        "request_id": "r1", "success": False, "result": None,
        "error": "invalid_field: auto_restart must be a boolean, not a string.",
    })]


@pytest.mark.parametrize("msg_type,payload,answer,says", [
    ("diagnostic", {"action": "ping", "target": 5}, "diagnostic_result",
     "invalid_field: target must be a string, not a number."),
    ("command", {"device_id": "projector", "command": "power_on", "params": "on"},
     "command_result", "invalid_field: params must be an object, not a string."),
    ("panel_software_update", {"target_version": "0.3.28", "target_version_code": True},
     "command_result", "invalid_field: target_version_code must be an integer, not a boolean."),
    ("ai_tool_call", {"tool_name": ["get_status"], "tool_input": {}}, "ai_tool_result",
     "invalid_field: tool_name must be a string, not an array."),
    ("config_push", {"project_json": ""}, "command_result",
     "invalid_field: project_json must be an object, not a string."),
])
@pytest.mark.asyncio
async def test_a_field_of_the_wrong_type_is_answered_in_the_requests_own_result(
    msg_type, payload, answer, says,
):
    agent = _agent(capabilities=(
        "remote_access", "diagnostics", "fleet_update", "panel_software_update",
    ))
    await agent._handle_message({"type": msg_type, "payload": {"request_id": "r9", **payload}})
    assert agent._command_handler.handled == []
    assert len(agent.sent) == 1
    sent_type, sent = agent.sent[0]
    assert sent_type == answer == REQUEST_ANSWERS[msg_type]
    assert sent["request_id"] == "r9"
    assert sent["success"] is False
    assert sent["error"] == says


@pytest.mark.parametrize("msg_type,payload", [
    # Null is the handler's to read, as it always was.
    ("ai_tool_call", {"tool_name": "get_status", "tool_input": None}),
    ("diagnostic", {"action": "ping", "target": "10.0.0.5", "params": None}),
    ("software_update", {"target_version": "0.40.0", "auto_restart": False}),
    # A field this release does not know is read past: a newer cloud may send one.
    ("command", {"device_id": "projector", "command": "power_on", "priority": "high"}),
    ("get_project", {}),
])
@pytest.mark.asyncio
async def test_a_request_the_contract_allows_reaches_its_handler(msg_type, payload):
    agent = _agent()
    msg = {"type": msg_type, "payload": {"request_id": "r2", **payload}}
    await agent._handle_message(msg)
    assert agent._command_handler.handled == [msg]
    assert agent.sent == []


@pytest.mark.asyncio
async def test_a_gated_request_with_a_payload_that_is_not_an_object_sends_nothing():
    agent = _agent(capabilities=("monitoring",))
    await agent._handle_message({"type": "restart", "payload": "now"})
    assert agent._command_handler.handled == []
    assert agent.sent == []


# --- An empty push ---


def _push_handler(tmp_path, applied):
    from openavc.cloud.command_handler import CommandHandler
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    project_path = tmp_path / "project.avc"
    project_path.write_text(
        json.dumps({"openavc_version": "0.7.0", "project": {"id": "old", "name": "Old"}}),
        encoding="utf-8",
    )

    async def apply_fn(project, **kwargs):
        applied.append(project)
        return 1

    events = EventBus()
    agent = _agent()
    agent._command_handler = CommandHandler(
        agent, DeviceManager(StateStore(), events), events,
        apply_fn=apply_fn, project_path=str(project_path),
    )
    return agent, project_path


@pytest.mark.parametrize("project", ["<absent>", None, {}])
@pytest.mark.asyncio
async def test_a_push_with_no_project_is_refused(tmp_path, project):
    applied: list = []
    agent, project_path = _push_handler(tmp_path, applied)
    before = project_path.read_text(encoding="utf-8")
    payload = {"request_id": "r1", "mode": "full_replace", "user_id": "u1", "user_name": "Ann"}
    if project != "<absent>":
        payload["project_json"] = project

    await agent._handle_message({"type": "config_push", "payload": payload})

    assert applied == []
    assert project_path.read_text(encoding="utf-8") == before
    assert agent.sent == [("command_result", {
        "request_id": "r1", "success": False, "result": None,
        "error": "Not applied: the push carried no project. The previous project is still running.",
    })]


# --- The reader itself ---


@pytest.mark.parametrize("field,value,ok", [
    ("target_version_code", 328, True),
    ("target_version_code", True, False),   # a boolean is not an integer
    ("target_version_code", 3.5, False),
    ("install", "at_panel", True),
    ("install", 1, False),
])
def test_the_reader_checks_each_sent_field_against_its_declared_type(field, value, ok):
    msg = {"type": "panel_software_update", "payload": {"request_id": "r1", field: value}}
    if ok:
        assert read_request(msg)[field] == value
    else:
        with pytest.raises(RequestRefused) as refused:
            read_request(msg)
        assert refused.value.request_id == "r1"
        assert field in str(refused.value)


def test_a_boolean_field_takes_no_number():
    msg = {"type": "software_update", "payload": {"request_id": "r1", "auto_restart": 1}}
    with pytest.raises(RequestRefused, match="auto_restart must be a boolean, not a number"):
        read_request(msg)


def test_the_reader_fills_what_the_cloud_left_out():
    payload = read_request({"type": "software_update", "payload": {"request_id": "r1"}})
    assert payload["auto_restart"] is True
    assert payload["update_url"] == ""


def test_a_refusal_with_nothing_to_answer_carries_no_request_id():
    with pytest.raises(RequestRefused) as refused:
        read_request({"type": "restart", "payload": {"mode": "graceful"}})
    assert refused.value.request_id == ""
