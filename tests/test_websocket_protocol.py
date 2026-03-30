"""Tests for WebSocket protocol — message handling, panel vs programmer, state validation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.api.ws import _handle_message, _is_flat_primitive, _PANEL_ALLOWED_TYPES


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
    engine._broadcast_ws = AsyncMock()
    engine.reload_project = AsyncMock()
    engine.isc = None
    return engine


@pytest.mark.asyncio
async def test_panel_cannot_set_state():
    """Panel clients cannot send state.set messages."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "state.set", "key": "var.foo", "value": 1}, "panel")
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "error"
    assert "Panel clients" in ws.sent[0]["message"]


@pytest.mark.asyncio
async def test_panel_cannot_execute_macro():
    """Panel clients cannot send macro.execute messages."""
    ws = FakeWS()
    with patch("server.api.ws._engine", _make_engine()):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "test"}, "panel")
    assert ws.sent[0]["type"] == "error"


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
async def test_ui_page_broadcasts_navigation():
    """ui.page emits event and broadcasts navigation."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api.ws._engine", engine):
        await _handle_message(ws, {"type": "ui.page", "page_id": "page2"}, "panel")
    engine.events.emit.assert_awaited_once_with("ui.page.page2")
    engine._broadcast_ws.assert_awaited_once()
    broadcast_msg = engine._broadcast_ws.call_args[0][0]
    assert broadcast_msg["type"] == "ui.navigate"
    assert broadcast_msg["page_id"] == "page2"


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
    for msg_type in ["ui.press", "ui.release", "ui.change", "ui.page", "command", "pong"]:
        assert msg_type in _PANEL_ALLOWED_TYPES


def test_programmer_only_types_excluded_from_panel():
    """Verify programmer-only message types are NOT in the panel allowed set."""
    for msg_type in ["state.set", "macro.execute", "project.reload", "isc.send", "isc.broadcast"]:
        assert msg_type not in _PANEL_ALLOWED_TYPES
