"""Tests for WebSocket protocol — message handling, panel vs programmer, state validation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.api.ws import (
    _handle_message,
    _is_flat_primitive,
    _PANEL_ALLOWED_TYPES,
    _PANEL_STATE_SET_PREFIXES,
)


# ── _is_flat_primitive tests ──


def test_flat_primitive_none():
    assert _is_flat_primitive(None) is True


def test_flat_primitive_str():
    assert _is_flat_primitive("hello") is True


def test_flat_primitive_int():
    assert _is_flat_primitive(42) is True


def test_flat_primitive_float():
    assert _is_flat_primitive(3.14) is True


def test_flat_primitive_bool():
    assert _is_flat_primitive(True) is True


def test_flat_primitive_list_rejected():
    assert _is_flat_primitive([1, 2]) is False


def test_flat_primitive_dict_rejected():
    assert _is_flat_primitive({"a": 1}) is False


def test_flat_primitive_bytes_rejected():
    assert _is_flat_primitive(b"data") is False


# ── Panel restriction tests ──


class FakeWS:
    """Minimal WebSocket mock that records sent messages."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict):
        self.sent.append(data)


def _make_engine():
    """Create a minimal mock engine for message handling tests."""
    engine = MagicMock()
    engine.state = MagicMock()
    engine.events = MagicMock()
    engine.events.emit = AsyncMock()
    engine.devices = MagicMock()
    engine.devices.send_command = AsyncMock()
    engine.macros = MagicMock()
    engine.macros.execute = AsyncMock()
    engine.handle_ui_event = AsyncMock()
    engine.broadcast_ws = AsyncMock()
    engine.reload_project = AsyncMock()
    engine.isc = None
    return engine


@pytest.mark.asyncio
async def test_panel_can_set_state():
    """Panel clients can send state.set (needed for plugin iframes)."""
    ws = FakeWS()
    engine = _make_engine()
    engine.state.get.return_value = None
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "state.set", "key": "var.foo", "value": 1}, "panel")
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "state.set.ack"


@pytest.mark.asyncio
async def test_panel_can_set_plugin_namespace():
    """Panel clients can set plugin.* keys (plugin iframe state)."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws, {"type": "state.set", "key": "plugin.my_plugin.foo", "value": "bar"}, "panel"
        )
    engine.state.set.assert_called_once_with("plugin.my_plugin.foo", "bar", source="ws")
    assert ws.sent[0]["type"] == "state.set.ack"
    assert ws.sent[0]["success"] is True


@pytest.mark.asyncio
async def test_panel_cannot_set_device_namespace():
    """Panel clients cannot overwrite device state (e.g. device.<id>.connected)."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws,
            {"type": "state.set", "key": "device.proj1.connected", "value": True},
            "panel",
        )
    engine.state.set.assert_not_called()
    assert ws.sent[0]["type"] == "error"
    assert ws.sent[0]["source_type"] == "state.set"
    assert "var.*" in ws.sent[0]["message"]
    assert "plugin.*" in ws.sent[0]["message"]


@pytest.mark.asyncio
async def test_panel_cannot_set_system_namespace():
    """Panel clients cannot overwrite system state (e.g. trigger cooldown markers)."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws,
            {"type": "state.set", "key": "system.trigger.t1.last_fired", "value": 0},
            "panel",
        )
    engine.state.set.assert_not_called()
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_panel_cannot_set_isc_namespace():
    """Panel clients cannot pollute ISC mesh state."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws,
            {"type": "state.set", "key": "isc.peer1.foo", "value": "bar"},
            "panel",
        )
    engine.state.set.assert_not_called()
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_panel_cannot_set_ui_namespace():
    """Panel clients cannot directly write ui.* keys (only ui.* events)."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws,
            {"type": "state.set", "key": "ui.element1.value", "value": 42},
            "panel",
        )
    engine.state.set.assert_not_called()
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_programmer_can_set_any_namespace():
    """Programmer clients are not restricted by the panel namespace allowlist."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws,
            {"type": "state.set", "key": "device.proj1.connected", "value": True},
            "programmer",
        )
    engine.state.set.assert_called_once_with("device.proj1.connected", True, source="ws")


def test_panel_state_set_prefixes_are_documented_namespaces():
    """The panel allowlist should map to var.* (user variables) and plugin.* (iframes)."""
    assert _PANEL_STATE_SET_PREFIXES == ("var.", "plugin.")


@pytest.mark.asyncio
async def test_panel_can_execute_macro():
    """Panel clients can send macro.execute (needed for presets)."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "test"}, "panel")
    engine.macros.execute.assert_called_once()


@pytest.mark.asyncio
async def test_panel_cannot_reload_project():
    """Panel clients cannot send project.reload messages."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "project.reload"}, "panel")
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_panel_can_send_ui_press():
    """Panel clients CAN send UI interaction messages."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "ui.press", "element_id": "btn1"}, "panel")
    engine.handle_ui_event.assert_awaited_once_with("press", "btn1")


@pytest.mark.asyncio
async def test_panel_can_send_pong():
    """Panel clients can respond to heartbeat pings."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "pong"}, "panel")
    assert len(ws.sent) == 0  # pong is a no-op


@pytest.mark.asyncio
async def test_panel_can_send_command():
    """Panel clients can send device commands."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws,
            {"type": "command", "device_id": "proj1", "command": "power_on", "params": {}},
            "panel",
        )
    engine.devices.send_command.assert_awaited_once()


# ── Programmer message tests ──


@pytest.mark.asyncio
async def test_programmer_can_set_state():
    """Programmer clients can set state."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "state.set", "key": "var.foo", "value": 42}, "programmer")
    engine.state.set.assert_called_once_with("var.foo", 42, source="ws")


@pytest.mark.asyncio
async def test_programmer_can_execute_macro():
    """Programmer clients can execute macros."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "system_on"}, "programmer")
    engine.macros.execute.assert_awaited_once_with("system_on")


@pytest.mark.asyncio
async def test_programmer_can_reload_project():
    """Programmer clients can reload the project."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "project.reload"}, "programmer")
    engine.reload_project.assert_awaited_once()


# ── Message validation tests ──


@pytest.mark.asyncio
async def test_ui_press_missing_element_id():
    """ui.press without element_id returns error."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "ui.press"}, "programmer")
    assert ws.sent[0]["type"] == "error"
    assert "element_id" in ws.sent[0]["message"]


@pytest.mark.asyncio
async def test_state_set_missing_key():
    """state.set without key returns error."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "state.set", "value": 1}, "programmer")
    assert ws.sent[0]["type"] == "error"
    assert "key" in ws.sent[0]["message"].lower()


@pytest.mark.asyncio
async def test_state_set_rejects_dict_value():
    """state.set rejects non-primitive values."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(
            ws, {"type": "state.set", "key": "var.test", "value": {"nested": True}}, "programmer"
        )
    assert ws.sent[0]["type"] == "error"
    assert "primitive" in ws.sent[0]["message"].lower()


@pytest.mark.asyncio
async def test_ui_change_rejects_list_value():
    """ui.change rejects non-primitive values."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(
            ws, {"type": "ui.change", "element_id": "slider1", "value": [1, 2]}, "programmer"
        )
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_command_missing_device_id():
    """command without device_id returns error."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "command", "command": "power_on"}, "programmer")
    assert ws.sent[0]["type"] == "error"
    assert "device_id" in ws.sent[0]["message"].lower()


@pytest.mark.asyncio
async def test_macro_execute_missing_macro_id():
    """macro.execute without macro_id returns error."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "macro.execute"}, "programmer")
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_ui_page_sends_navigate_to_sender():
    """ui.page emits event and sends navigation back to the sender only."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "ui.page", "page_id": "page2"}, "panel")
    engine.events.emit.assert_awaited_once_with("ui.page.page2")
    engine.broadcast_ws.assert_not_awaited()
    assert any(m.get("type") == "ui.navigate" and m.get("page_id") == "page2" for m in ws.sent)


@pytest.mark.asyncio
async def test_unknown_message_type_is_silent():
    """Unknown message types are logged but no error sent to client."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "totally.unknown"}, "programmer")
    assert len(ws.sent) == 0


@pytest.mark.asyncio
async def test_ui_change_valid_value():
    """ui.change with valid value dispatches to engine."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws, {"type": "ui.change", "element_id": "slider1", "value": 75}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with("change", "slider1", {"value": 75})


@pytest.mark.asyncio
async def test_ui_route_dispatches():
    """ui.route dispatches input/output to engine."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(
            ws, {"type": "ui.route", "element_id": "matrix1", "input": 1, "output": 3}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with("route", "matrix1", {"input": 1, "output": 3})


# ── Panel allowed types completeness check ──


def test_panel_allowed_types_includes_expected():
    """Verify core panel message types are in the allowed set."""
    for msg_type in ["ui.press", "ui.release", "ui.change", "ui.page", "command",
                     "macro.execute", "state.set", "pong"]:
        assert msg_type in _PANEL_ALLOWED_TYPES


def test_programmer_only_types_excluded_from_panel():
    """Verify programmer-only message types are NOT in the panel allowed set."""
    for msg_type in ["project.reload", "isc.send", "isc.broadcast"]:
        assert msg_type not in _PANEL_ALLOWED_TYPES
