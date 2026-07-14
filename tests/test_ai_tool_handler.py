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


@pytest.mark.asyncio
async def test_execute_macro_rate_limited(handler, mock_agent):
    """The AI execute_macro tool debounces rapid re-firing of the same macro,
    matching the REST /macros/{id}/execute guard and sharing its window."""
    mock_engine = MagicMock()
    mock_engine.macros = MagicMock()
    mock_engine.macros.execute = AsyncMock()

    with patch("server.cloud.ai_tool_handler.AIToolHandler._get_engine", return_value=mock_engine):
        await _handle_and_wait(handler, _make_tool_call_msg(
            "execute_macro", {"macro_id": "rl_all_off"}, request_id="req-1"))
        await _handle_and_wait(handler, _make_tool_call_msg(
            "execute_macro", {"macro_id": "rl_all_off"}, request_id="req-2"))

    # The macro ran once; the throttled second call never reached the engine.
    mock_engine.macros.execute.assert_called_once_with("rl_all_off")
    payload = mock_agent.send_message.call_args[0][1]
    assert payload["success"] is False
    assert "Too many requests" in payload["error"]


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
    """_validate_bindings accepts single, any:[] (OR) and all:[] (AND)
    show.visible_when forms in the show/do model."""

    def test_single_condition_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"visible_when": {"key": "device.proj.power", "operator": "eq", "value": "on"}}}
        assert _validate_bindings(b) is None

    def test_any_group_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"visible_when": {"any": [
            {"key": "device.a.power", "operator": "eq", "value": "on"},
            {"key": "device.b.power", "operator": "truthy"},
        ]}}}
        assert _validate_bindings(b) is None

    def test_all_group_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"visible_when": {"all": [
            {"key": "device.a.power", "operator": "eq", "value": "on"},
            {"key": "device.b.online", "operator": "truthy"},
        ]}}}
        assert _validate_bindings(b) is None

    def test_all_group_bad_operator_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"visible_when": {"all": [{"key": "device.a.power", "operator": "snorgle"}]}}}
        err = _validate_bindings(b)
        assert err and "all[0]" in err

    def test_group_missing_key_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"visible_when": {"any": [{"operator": "truthy"}]}}}
        err = _validate_bindings(b)
        assert err and "any[0]" in err

    def test_single_missing_key_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"visible_when": {"operator": "eq", "value": "on"}}}
        err = _validate_bindings(b)
        assert err and "visible_when" in err


class TestShowDoBindingValidation:
    """The show/do binding model: do.<interaction> action lists, show.value /
    show.look / show.items, and the device-two-way safety rule."""

    def test_do_press_macro_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"do": {"press": [{"action": "macro", "macro": "m1"}]}}
        assert _validate_bindings(b) is None

    def test_do_change_device_command_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"do": {"change": [{"action": "device.command", "device": "amp",
                                "command": "setVolume", "params": {"level": "$value"}}]}}
        assert _validate_bindings(b) is None

    def test_legacy_flat_press_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"press": [{"action": "macro", "macro": "m1"}]}
        err = _validate_bindings(b)
        assert err and "show/do model" in err and "press" in err

    def test_unknown_interaction_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"do": {"wiggle": [{"action": "macro", "macro": "m1"}]}}
        err = _validate_bindings(b)
        assert err and "wiggle" in err

    def test_state_set_to_device_rejected(self):
        # The central device-safety rule: never write a device.* key directly.
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"do": {"change": [{"action": "state.set", "key": "device.amp.level", "value": "$value"}]}}
        err = _validate_bindings(b)
        assert err and "device.command" in err

    def test_state_set_to_var_allowed(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"do": {"change": [{"action": "state.set", "key": "var.volume", "value": "$value"}]}}
        assert _validate_bindings(b) is None

    def test_value_map_inner_device_state_set_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"do": {"change": [{"action": "value_map", "map": {
            "hi": {"action": "state.set", "key": "device.x.mode", "value": "h"},
        }}]}}
        err = _validate_bindings(b)
        assert err and "device.command" in err

    def test_show_value_two_way_var_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"value": {"source": "state", "key": "var.vol", "write_back": True}}}
        assert _validate_bindings(b) is None

    def test_show_value_write_back_on_device_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"value": {"source": "state", "key": "device.amp.level", "write_back": True}}}
        err = _validate_bindings(b)
        assert err and "write_back" in err

    def test_show_value_missing_key_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"value": {"source": "state"}}}
        err = _validate_bindings(b)
        assert err and "show.value" in err

    def test_show_value_macro_progress_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"value": {"source": "macro_progress", "macro": "m1", "idle_text": "Ready"}}}
        assert _validate_bindings(b) is None

    def test_show_look_feedback_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"look": {"source": "state", "key": "device.x.power",
                               "condition": {"equals": True},
                               "style_active": {"bg_color": "#0f0"}}}}
        assert _validate_bindings(b) is None

    def test_show_look_nested_style_rejected(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"look": {"key": "var.s", "states": {"on": {"style": {"bg_color": "#0f0"}}}}}}
        err = _validate_bindings(b)
        assert err and "flat" in err

    def test_show_items_key_pattern_valid(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"show": {"items": {"source": "state", "key_pattern": "device.m.input_*_name"}}}
        assert _validate_bindings(b) is None

    def test_do_toggle_requires_off_action(self):
        from server.cloud.ai_tool_handler import _validate_bindings
        b = {"do": {"press": [{"action": "macro", "macro": "m", "mode": "toggle",
                               "toggle_key": "var.x"}]}}
        err = _validate_bindings(b)
        assert err and "off_action" in err

    def test_normalize_wraps_do_single_action(self):
        from server.cloud.ai_tool_handler import _normalize_bindings
        b = {"do": {"press": {"action": "macro", "macro": "m1"}}}
        out = _normalize_bindings(b)
        assert out["do"]["press"] == [{"action": "macro", "macro": "m1"}]


# ===========================================================================
# H-078 — A tool that returns an {"error": ...} / {"success": False} dict has
# failed even though it didn't raise. It must be reported with success=False
# so the cloud sets is_error on the Anthropic tool_result, instead of handing
# Claude a "successful" result with the error buried in the body.
# ===========================================================================


class TestToolResultErrorSignaling:
    def test_classifier_pure_error_dict_is_error(self):
        from server.cloud.ai_tool_handler import _tool_result_is_error
        assert _tool_result_is_error({"error": "boom"}) is True

    def test_classifier_data_dict_is_not_error(self):
        from server.cloud.ai_tool_handler import _tool_result_is_error
        assert _tool_result_is_error({"devices": [], "total": 0}) is False

    def test_classifier_explicit_success_true_with_null_error(self):
        # {"success": True, "error": None, ...} — the explicit flag wins.
        from server.cloud.ai_tool_handler import _tool_result_is_error
        assert _tool_result_is_error({"success": True, "error": None, "latency_ms": 5}) is False

    def test_classifier_explicit_success_false(self):
        from server.cloud.ai_tool_handler import _tool_result_is_error
        assert _tool_result_is_error({"success": False, "error": "nope", "response": None}) is True

    def test_classifier_non_dict_is_not_error(self):
        from server.cloud.ai_tool_handler import _tool_result_is_error
        assert _tool_result_is_error([1, 2, 3]) is False
        assert _tool_result_is_error("ok") is False
        assert _tool_result_is_error(None) is False

    def test_classifier_data_with_truthy_error_is_error(self):
        # Partial result: data plus a soft error (e.g. stale catalog + fetch failure).
        from server.cloud.ai_tool_handler import _tool_result_is_error
        assert _tool_result_is_error({"plugins": [{"id": "x"}], "error": "fetch failed"}) is True

    def test_classifier_data_with_null_error_is_not_error(self):
        from server.cloud.ai_tool_handler import _tool_result_is_error
        assert _tool_result_is_error({"plugins": [{"id": "x"}], "error": None}) is False

    @pytest.mark.asyncio
    async def test_error_dict_return_reported_as_failure(self, handler, mock_agent):
        """A non-raising tool that returns an error dict must dispatch
        success=False, lift the message into the error field, and still carry
        the body so the model sees the structured detail."""
        async def _err_tool(_input):
            return {"error": "Device 'x' not found"}

        await handler._execute_tool("req-err", "add_device", _err_tool, {})

        payload = mock_agent.send_message.call_args[0][1]
        assert payload["success"] is False
        assert payload["error"] == "Device 'x' not found"
        assert payload["result"] == {"error": "Device 'x' not found"}

    @pytest.mark.asyncio
    async def test_success_dict_return_reported_as_success(self, handler, mock_agent):
        async def _ok_tool(_input):
            return {"status": "created", "id": "d1"}

        await handler._execute_tool("req-ok", "add_device", _ok_tool, {})

        payload = mock_agent.send_message.call_args[0][1]
        assert payload["success"] is True
        assert payload["error"] is None
        assert payload["result"]["status"] == "created"

    @pytest.mark.asyncio
    async def test_list_return_reported_as_success(self, handler, mock_agent):
        async def _list_tool(_input):
            return [{"id": "a"}, {"id": "b"}]

        await handler._execute_tool("req-list", "list_devices", _list_tool, {})

        payload = mock_agent.send_message.call_args[0][1]
        assert payload["success"] is True
        assert len(payload["result"]) == 2


# ===========================================================================
# M-132 — A failed pre-AI backup must NOT latch the "backup created" flag,
# otherwise the safety net is silently disabled for the rest of the session.
# ===========================================================================


class TestPreAIBackupSafetyNet:
    @pytest.mark.asyncio
    async def test_backup_failure_not_latched_and_retried(
        self, mock_agent, mock_devices, mock_events
    ):
        handler = AIToolHandler(
            mock_agent, mock_devices, mock_events,
            project_path="/tmp/proj/project.avc",
        )
        calls = {"n": 0}

        def _failing(*_a, **_k):
            calls["n"] += 1
            raise OSError("disk full")

        with patch("server.core.backup_manager.create_backup", _failing):
            await handler._maybe_create_pre_ai_backup()
        # Failure must leave the flag unset so the next change retries.
        assert handler._ai_backup_created is False
        assert calls["n"] == 1

        def _ok(*_a, **_k):
            calls["n"] += 1

        with patch("server.core.backup_manager.create_backup", _ok):
            await handler._maybe_create_pre_ai_backup()
        # The retry happened (proves the net wasn't disabled) and succeeded.
        assert handler._ai_backup_created is True
        assert calls["n"] == 2

        # Now that a backup exists, no further attempts are made this session.
        with patch("server.core.backup_manager.create_backup", _ok):
            await handler._maybe_create_pre_ai_backup()
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_backup_success_latches_once(
        self, mock_agent, mock_devices, mock_events
    ):
        handler = AIToolHandler(
            mock_agent, mock_devices, mock_events,
            project_path="/tmp/proj/project.avc",
        )
        calls = {"n": 0}

        def _ok(*_a, **_k):
            calls["n"] += 1

        with patch("server.core.backup_manager.create_backup", _ok):
            await handler._maybe_create_pre_ai_backup()
            await handler._maybe_create_pre_ai_backup()
        assert handler._ai_backup_created is True
        assert calls["n"] == 1


# ===========================================================================
# M-133 — The runtime supports ui.navigate macro steps; the AI validator must
# accept them so AI tools can author/edit macros that contain one.
# ===========================================================================


class TestUINavigateMacroStep:
    def test_ui_navigate_in_valid_step_actions(self):
        from server.cloud.ai_tool_handler import _VALID_STEP_ACTIONS
        assert "ui.navigate" in _VALID_STEP_ACTIONS

    def test_ui_navigate_with_page_valid(self):
        from server.cloud.ai_tool_handler import _validate_macro_step
        errs = _validate_macro_step({"action": "ui.navigate", "page": "home"}, "steps[0]")
        assert errs == []

    def test_ui_navigate_overlay_controls_valid(self):
        from server.cloud.ai_tool_handler import _validate_macro_step
        for page in ("$back", "$dismiss"):
            errs = _validate_macro_step({"action": "ui.navigate", "page": page}, "steps[0]")
            assert errs == [], f"'{page}' rejected: {errs}"

    def test_ui_navigate_missing_page_rejected(self):
        from server.cloud.ai_tool_handler import _validate_macro_step
        errs = _validate_macro_step({"action": "ui.navigate"}, "steps[0]")
        assert errs and any("page" in e for e in errs)

    def test_macro_with_ui_navigate_step_validates(self):
        from server.cloud.ai_tool_handler import _validate_macro
        steps = [
            {"action": "device.command", "device": "proj1", "command": "power_on"},
            {"action": "ui.navigate", "page": "controls"},
        ]
        assert _validate_macro(steps, []) is None


# ===========================================================================
# L-081 — The state store accepts isc. keys; the AI state-key validator must
# accept them too (parity with StateStore._VALID_PREFIXES).
# ===========================================================================


class TestStateKeyISCPrefix:
    def test_isc_prefix_accepted(self):
        from server.cloud.ai_tool_handler import _validate_state_key
        assert _validate_state_key("isc.room1.scene") is None

    def test_isc_listed_in_error_message(self):
        from server.cloud.ai_tool_handler import _validate_state_key
        err = _validate_state_key("bogus.key")
        assert err and "isc." in err

    def test_unknown_prefix_still_rejected(self):
        from server.cloud.ai_tool_handler import _validate_state_key
        assert _validate_state_key("bogus.key") is not None

    def test_validator_matches_state_store_prefixes(self):
        from server.cloud.ai_tool_handler import _VALID_STATE_PREFIXES
        from server.core.state_store import StateStore
        assert set(_VALID_STATE_PREFIXES) == set(StateStore._VALID_PREFIXES)


# ===========================================================================
# L-082 — Raw str(exception) must not leave the local trust boundary. The
# handler maps tool exceptions through friendly_error before forwarding to the
# cloud (where they're persisted in chat history).
# ===========================================================================


class TestToolExceptionFriendlyError:
    @pytest.mark.asyncio
    async def test_exception_mapped_to_friendly_message(self, handler, mock_agent):
        async def _raise_tool(_input):
            raise ConnectionRefusedError(111, "Connection refused")

        await handler._execute_tool("req-x", "send_device_command", _raise_tool, {})

        payload = mock_agent.send_message.call_args[0][1]
        assert payload["success"] is False
        # Friendly mapping applied — no raw errno / OS repr leaks through.
        assert "Could not connect" in payload["error"]
        assert "Errno" not in payload["error"]
        assert "111" not in payload["error"]
