"""Tests for AI tool handler — agent-side tool call dispatch."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.cloud.ai_tool_handler import AIToolHandler
from server.cloud.protocol import (
    AI_TOOL_CALL, AI_TOOL_RESULT,
    build_ai_tool_result, _now_iso,
)


async def _handle_and_wait(handler, msg):
    """Call handle() and wait for the background task to complete."""
    await handler.handle(msg)
    await asyncio.sleep(0)


def _make_tool_call_msg(tool_name, tool_input=None, request_id="req-1"):
    """Build a mock AI_TOOL_CALL message."""
    return {
        "type": AI_TOOL_CALL,
        "ts": _now_iso(),
        "seq": 1,
        "session": "test",
        "payload": {
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": tool_input or {},
        },
    }


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.send_message = AsyncMock()
    agent.state = MagicMock()
    agent.state.snapshot.return_value = {"device.projector.power": "on", "var.room_mode": "presentation"}
    agent.state.get.return_value = "on"
    agent.state.set = MagicMock()
    return agent


@pytest.fixture
def mock_devices():
    devices = MagicMock()
    devices.list_devices.return_value = [
        {"id": "projector", "name": "Main Projector", "status": "connected"}
    ]
    devices.get_device_info.return_value = {
        "id": "projector", "name": "Main Projector", "driver": "pjlink",
        "commands": ["power_on", "power_off"]
    }
    devices.send_command = AsyncMock()
    return devices


@pytest.fixture
def mock_events():
    events = MagicMock()
    events.emit = AsyncMock()
    return events


@pytest.fixture
def handler(mock_agent, mock_devices, mock_events):
    return AIToolHandler(mock_agent, mock_devices, mock_events)


# --- Basic dispatch ---


@pytest.mark.asyncio
async def test_get_project_state(handler, mock_agent):
    msg = _make_tool_call_msg("get_project_state")
    await _handle_and_wait(handler, msg)

    mock_agent.send_message.assert_called_once()
    call_args = mock_agent.send_message.call_args
    assert call_args[0][0] == AI_TOOL_RESULT
    payload = call_args[0][1]
    assert payload["request_id"] == "req-1"
    assert payload["success"] is True
    assert "device.projector.power" in payload["result"]


@pytest.mark.asyncio
async def test_get_state_value(handler, mock_agent):
    msg = _make_tool_call_msg("get_state_value", {"key": "device.projector.power"})
    await _handle_and_wait(handler, msg)

    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is True
    assert payload["result"]["key"] == "device.projector.power"


@pytest.mark.asyncio
async def test_list_devices(handler, mock_agent, mock_devices):
    msg = _make_tool_call_msg("list_devices")
    await _handle_and_wait(handler, msg)

    mock_devices.list_devices.assert_called_once()
    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is True
    assert len(payload["result"]) == 1
    assert payload["result"][0]["id"] == "projector"


@pytest.mark.asyncio
async def test_get_device_info(handler, mock_agent, mock_devices):
    msg = _make_tool_call_msg("get_device_info", {"device_id": "projector"})
    await _handle_and_wait(handler, msg)

    mock_devices.get_device_info.assert_called_once_with("projector")
    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is True
    assert payload["result"]["driver"] == "pjlink"


@pytest.mark.asyncio
async def test_send_device_command(handler, mock_agent, mock_devices, mock_events):
    msg = _make_tool_call_msg("send_device_command", {
        "device_id": "projector",
        "command": "power_on",
        "params": {},
    })
    await _handle_and_wait(handler, msg)

    mock_devices.send_command.assert_called_once_with("projector", "power_on", {})
    mock_events.emit.assert_called()
    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is True


@pytest.mark.asyncio
async def test_set_state_value(handler, mock_agent):
    msg = _make_tool_call_msg("set_state_value", {"key": "var.mode", "value": "away"})
    await _handle_and_wait(handler, msg)

    mock_agent.state.set.assert_called_once_with("var.mode", "away", source="ai")
    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is True


@pytest.mark.asyncio
async def test_execute_macro(handler, mock_agent):
    # Mock engine with macros
    mock_engine = MagicMock()
    mock_engine.macros = MagicMock()
    mock_engine.macros.execute = AsyncMock()

    with patch("server.cloud.ai_tool_handler.AIToolHandler._get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("execute_macro", {"macro_id": "all_off"})
        await _handle_and_wait(handler, msg)

    mock_engine.macros.execute.assert_called_once_with("all_off")
    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is True


# --- Error handling ---


@pytest.mark.asyncio
async def test_unknown_tool(handler, mock_agent):
    msg = _make_tool_call_msg("nonexistent_tool")
    await _handle_and_wait(handler, msg)

    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is False
    assert "Unknown tool" in payload["error"]


@pytest.mark.asyncio
async def test_device_command_error(handler, mock_agent, mock_devices):
    mock_devices.send_command.side_effect = ValueError("Device not found")

    msg = _make_tool_call_msg("send_device_command", {
        "device_id": "unknown",
        "command": "power_on",
    })
    await _handle_and_wait(handler, msg)

    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is False
    assert "Device not found" in payload["error"]


@pytest.mark.asyncio
async def test_no_request_id_skips_result(handler, mock_agent):
    """If no request_id, no result is sent."""
    msg = _make_tool_call_msg("list_devices", request_id="")
    await _handle_and_wait(handler, msg)

    # Should still call list_devices but not send result (empty request_id)
    mock_agent.send_message.assert_not_called()


# --- Discovery ---


@pytest.mark.asyncio
async def test_get_discovery_results_envelope_includes_identification_summary(handler, mock_agent):
    """Envelope exposes a state-bucketed summary so Claude doesn't have
    to walk every device's identification.state to count buckets."""
    mock_engine = MagicMock()
    mock_engine.get_results.return_value = [
        {"ip": "10.0.0.1", "identification": {"state": "identified", "driver_id": "pjlink"}},
        {"ip": "10.0.0.2", "identification": {"state": "identified", "driver_id": "extron_sis"}},
        {"ip": "10.0.0.3", "identification": {"state": "possible", "candidates": ["a", "b"]}},
        {"ip": "10.0.0.4", "identification": {"state": "unknown"}},
        # No identification record — falls through to the "unknown" bucket.
        {"ip": "10.0.0.5", "identification": None},
    ]
    mock_engine.get_status.return_value = {"status": "complete", "duration": 12.5}

    with patch("server.api.discovery._engine", mock_engine):
        msg = _make_tool_call_msg("get_discovery_results")
        await _handle_and_wait(handler, msg)

    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is True
    result = payload["result"]
    assert result["total_devices"] == 5
    assert result["scan_status"] == "complete"
    assert result["scan_duration_seconds"] == 12.5
    assert result["identification_summary"] == {
        "identified": 2,
        "possible": 1,
        "unknown": 2,
    }


@pytest.mark.asyncio
async def test_get_discovery_results_summary_respects_category_filter(handler, mock_agent):
    """Summary reflects post-filter counts so a category slice carries
    its own state breakdown."""
    mock_engine = MagicMock()
    mock_engine.get_results.return_value = [
        {"ip": "10.0.0.1", "category": "projector",
         "identification": {"state": "identified", "driver_id": "pjlink"}},
        {"ip": "10.0.0.2", "category": "projector",
         "identification": {"state": "possible", "candidates": ["a"]}},
        {"ip": "10.0.0.3", "category": "switcher",
         "identification": {"state": "identified", "driver_id": "extron_sis"}},
    ]
    mock_engine.get_status.return_value = {"status": "complete", "duration": 1.0}

    with patch("server.api.discovery._engine", mock_engine):
        msg = _make_tool_call_msg("get_discovery_results", {"category": "projector"})
        await _handle_and_wait(handler, msg)

    payload = mock_agent.send_message.call_args[0][1]
    result = payload["result"]
    assert result["total_devices"] == 2
    assert result["identification_summary"] == {
        "identified": 1,
        "possible": 1,
        "unknown": 0,
    }


# --- Protocol tests ---


def test_ai_tool_call_constant():
    from server.cloud.protocol import AI_TOOL_CALL
    assert AI_TOOL_CALL == "ai_tool_call"


def test_ai_tool_result_constant():
    from server.cloud.protocol import AI_TOOL_RESULT
    assert AI_TOOL_RESULT == "ai_tool_result"


def test_ai_tool_result_in_upstream_types():
    from server.cloud.protocol import UPSTREAM_TYPES, AI_TOOL_RESULT
    assert AI_TOOL_RESULT in UPSTREAM_TYPES


def test_ai_tool_call_in_downstream_types():
    from server.cloud.protocol import DOWNSTREAM_TYPES, AI_TOOL_CALL
    assert AI_TOOL_CALL in DOWNSTREAM_TYPES


def test_build_ai_tool_result():
    msg = build_ai_tool_result(
        seq=1,
        session_token="test",
        signing_key=b"key_32_bytes____________________",
        request_id="req-abc",
        success=True,
        result={"devices": []},
    )
    assert msg["type"] == AI_TOOL_RESULT
    assert msg["payload"]["request_id"] == "req-abc"
    assert msg["payload"]["success"] is True
    assert msg["payload"]["result"] == {"devices": []}
    assert "sig" in msg


# ===========================================================================
# A29 — Macro/trigger validators must accept the same operator aliases that
# the runtime (condition_eval) normalizes. Otherwise AI-driven updates to
# project files containing aliases like '==' or 'equals' silently fail.
# ===========================================================================


class TestValidatorOperatorAliases:
    """Validator should accept all canonical names plus condition_eval aliases."""

    def test_condition_eval_aliases_in_valid_set(self):
        from server.core.condition_eval import _OPERATOR_ALIASES
        from server.cloud.ai_tool_handler import _VALID_CONDITION_OPS, _VALID_STATE_TRIGGER_OPS

        for alias in _OPERATOR_ALIASES:
            assert alias in _VALID_CONDITION_OPS, (
                f"Alias '{alias}' is normalized by condition_eval but rejected "
                f"by the AI validator — drift will silently break AI tool calls "
                f"that pass through existing macros."
            )
            assert alias in _VALID_STATE_TRIGGER_OPS

    def test_conditional_step_accepts_alias(self):
        from server.cloud.ai_tool_handler import _validate_macro_step
        for op in ("equals", "==", "greater_than", ">=", "!=", "less_or_equal"):
            step = {
                "action": "conditional",
                "condition": {"key": "var.foo", "operator": op, "value": 1},
                "then_steps": [],
                "else_steps": [],
            }
            errs = _validate_macro_step(step, "test")
            assert not errs, f"alias '{op}' rejected: {errs}"

    def test_wait_until_step_accepts_alias(self):
        from server.cloud.ai_tool_handler import _validate_macro_step
        step = {
            "action": "wait_until",
            "condition": {"key": "device.proj1.power", "operator": "==", "value": "on"},
            "timeout": 60,
        }
        errs = _validate_macro_step(step, "test")
        assert not errs, f"alias rejected: {errs}"

    def test_skip_if_accepts_alias(self):
        from server.cloud.ai_tool_handler import _validate_macro_step
        step = {
            "action": "device.command",
            "device": "proj1",
            "command": "power_on",
            "skip_if": {"key": "device.proj1.power", "operator": "equals", "value": "on"},
        }
        errs = _validate_macro_step(step, "test")
        assert not errs, f"alias rejected in skip_if: {errs}"

    def test_trigger_guards_accept_alias(self):
        from server.cloud.ai_tool_handler import _validate_trigger
        trigger = {
            "type": "state_change",
            "state_key": "device.proj1.power",
            "state_operator": ">=",  # alias for gte
            "state_value": 50,
            "conditions": [
                {"key": "var.room_active", "operator": "==", "value": True},
            ],
        }
        errs = _validate_trigger(trigger, "test")
        assert not errs, f"alias rejected in trigger: {errs}"

    def test_unknown_operator_still_rejected(self):
        """Sanity check: the widening doesn't accept arbitrary strings."""
        from server.cloud.ai_tool_handler import _validate_macro_step
        step = {
            "action": "conditional",
            "condition": {"key": "var.foo", "operator": "snorgle", "value": 1},
            "then_steps": [],
            "else_steps": [],
        }
        errs = _validate_macro_step(step, "test")
        assert errs, "Garbage operator should still be rejected"


class TestVisibleWhenBindingValidation:
    """_validate_bindings accepts single, any:[] (OR) and all:[] (AND) visible_when."""

    def test_single_condition_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"visible_when": {"key": "device.proj.power", "operator": "eq", "value": "on"}}
        assert _validate_bindings(b) is None

    def test_any_group_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"visible_when": {"any": [
            {"key": "device.a.power", "operator": "eq", "value": "on"},
            {"key": "device.b.power", "operator": "truthy"},
        ]}}
        assert _validate_bindings(b) is None

    def test_all_group_valid(self):
        # The fix: the AND form used to be rejected as "missing key".
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"visible_when": {"all": [
            {"key": "device.a.power", "operator": "eq", "value": "on"},
            {"key": "device.b.online", "operator": "truthy"},
        ]}}
        assert _validate_bindings(b) is None

    def test_all_group_bad_operator_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"visible_when": {"all": [{"key": "device.a.power", "operator": "snorgle"}]}}
        err = _validate_bindings(b)
        assert err and "all[0]" in err

    def test_group_missing_key_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"visible_when": {"any": [{"operator": "truthy"}]}}
        err = _validate_bindings(b)
        assert err and "any[0]" in err

    def test_single_missing_key_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"visible_when": {"operator": "eq", "value": "on"}}
        err = _validate_bindings(b)
        assert err and "visible_when" in err
