"""Tests for WebSocket protocol — message handling, panel vs programmer, state validation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.api.ws import (
    _handle_message,
    _is_flat_primitive,
    _send_ws,
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
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "state.set", "key": "var.foo", "value": 1}, "panel")
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "state.set.ack"


@pytest.mark.asyncio
async def test_panel_can_set_plugin_namespace():
    """Panel clients can set plugin.* keys (plugin iframe state)."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
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
    with patch("server.api._engine._engine", engine):
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
    with patch("server.api._engine._engine", engine):
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
    with patch("server.api._engine._engine", engine):
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
    with patch("server.api._engine._engine", engine):
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
    with patch("server.api._engine._engine", engine):
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
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "test"}, "panel")
        await asyncio.sleep(0)  # the macro runs in a background task
    engine.macros.execute.assert_called_once()
    assert ws.sent[0]["type"] == "macro.execute.ack"


@pytest.mark.asyncio
async def test_panel_cannot_reload_project():
    """Panel clients cannot send project.reload messages."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(ws, {"type": "project.reload"}, "panel")
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_panel_can_send_ui_press():
    """Panel clients CAN send UI interaction messages."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "ui.press", "element_id": "btn1"}, "panel")
    engine.handle_ui_event.assert_awaited_once_with("press", "btn1")


@pytest.mark.asyncio
async def test_panel_can_send_pong():
    """Panel clients can respond to heartbeat pings."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(ws, {"type": "pong"}, "panel")
    assert len(ws.sent) == 0  # pong is a no-op


@pytest.mark.asyncio
async def test_panel_can_send_command():
    """Panel clients can send device commands."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
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
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "state.set", "key": "var.foo", "value": 42}, "programmer")
    engine.state.set.assert_called_once_with("var.foo", 42, source="ws")


@pytest.mark.asyncio
async def test_programmer_can_execute_macro():
    """Programmer clients can execute macros."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "system_on"}, "programmer")
        await asyncio.sleep(0)  # the macro runs in a background task
    engine.macros.execute.assert_awaited_once_with("system_on")


@pytest.mark.asyncio
async def test_programmer_can_reload_project():
    """Programmer clients can reload the project."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "project.reload"}, "programmer")
    engine.reload_project.assert_awaited_once()


# ── Message validation tests ──


@pytest.mark.asyncio
async def test_ui_press_missing_element_id():
    """ui.press without element_id returns error."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(ws, {"type": "ui.press"}, "programmer")
    assert ws.sent[0]["type"] == "error"
    assert "element_id" in ws.sent[0]["message"]


@pytest.mark.asyncio
async def test_state_set_missing_key():
    """state.set without key returns error."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(ws, {"type": "state.set", "value": 1}, "programmer")
    assert ws.sent[0]["type"] == "error"
    assert "key" in ws.sent[0]["message"].lower()


@pytest.mark.asyncio
async def test_state_set_rejects_dict_value():
    """state.set rejects non-primitive values."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(
            ws, {"type": "state.set", "key": "var.test", "value": {"nested": True}}, "programmer"
        )
    assert ws.sent[0]["type"] == "error"
    assert "primitive" in ws.sent[0]["message"].lower()


@pytest.mark.asyncio
async def test_ui_change_rejects_list_value():
    """ui.change rejects non-primitive values."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(
            ws, {"type": "ui.change", "element_id": "slider1", "value": [1, 2]}, "programmer"
        )
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_command_missing_device_id():
    """command without device_id returns error."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(ws, {"type": "command", "command": "power_on"}, "programmer")
    assert ws.sent[0]["type"] == "error"
    assert "device_id" in ws.sent[0]["message"].lower()


@pytest.mark.asyncio
async def test_macro_execute_missing_macro_id():
    """macro.execute without macro_id returns error."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(ws, {"type": "macro.execute"}, "programmer")
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_ui_page_sends_navigate_to_sender():
    """ui.page emits event and sends navigation back to the sender only."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "ui.page", "page_id": "page2"}, "panel")
    engine.events.emit.assert_awaited_once_with("ui.page.page2")
    engine.broadcast_ws.assert_not_awaited()
    assert any(m.get("type") == "ui.navigate" and m.get("page_id") == "page2" for m in ws.sent)


@pytest.mark.asyncio
async def test_unknown_message_type_is_silent():
    """Unknown message types are logged but no error sent to client."""
    ws = FakeWS()
    with patch("server.api._engine._engine", _make_engine()):
        await _handle_message(ws, {"type": "totally.unknown"}, "programmer")
    assert len(ws.sent) == 0


@pytest.mark.asyncio
async def test_ui_change_valid_value():
    """ui.change with valid value dispatches to engine."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.change", "element_id": "slider1", "value": 75}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with("change", "slider1", {"value": 75})


@pytest.mark.asyncio
async def test_ui_select_dispatches():
    """ui.select (list item tap) dispatches the select event to the engine."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.select", "element_id": "src_list", "value": "hdmi_1"}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with("select", "src_list", {"value": "hdmi_1"})


@pytest.mark.asyncio
async def test_ui_select_in_panel_allowed_types():
    """Panel clients are permitted to send ui.select."""
    assert "ui.select" in _PANEL_ALLOWED_TYPES


@pytest.mark.asyncio
async def test_ui_route_dispatches():
    """ui.route without audio/mute dispatches to the route binding."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.route", "element_id": "matrix1", "input": 1, "output": 3}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with("route", "matrix1", {"input": 1, "output": 3})


@pytest.mark.asyncio
async def test_ui_route_audio_dispatches_to_audio_route():
    """ui.route with audio=true dispatches to the audio_route binding."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws,
            {"type": "ui.route", "element_id": "matrix1", "input": 2, "output": 4, "audio": True},
            "panel",
        )
    engine.handle_ui_event.assert_awaited_once_with(
        "audio_route", "matrix1", {"input": 2, "output": 4}
    )


@pytest.mark.asyncio
async def test_ui_route_mute_dispatches_to_mute_route():
    """ui.route with mute present dispatches to the mute_route binding with $mute data."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.route", "element_id": "matrix1", "output": 2, "mute": True}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with(
        "mute_route", "matrix1", {"output": 2, "mute": True}
    )


@pytest.mark.asyncio
async def test_ui_route_unmute_dispatches_to_mute_route():
    """ui.route with mute=false (unmute) still routes to mute_route, not the plain route."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.route", "element_id": "matrix1", "output": 2, "mute": False}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with(
        "mute_route", "matrix1", {"output": 2, "mute": False}
    )


@pytest.mark.asyncio
async def test_ui_route_audio_and_mute_dispatches_to_audio_mute_route():
    """ui.route with both audio=true and mute present dispatches to audio_mute_route."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws,
            {"type": "ui.route", "element_id": "matrix1", "output": 2, "mute": True, "audio": True},
            "panel",
        )
    engine.handle_ui_event.assert_awaited_once_with(
        "audio_mute_route", "matrix1", {"output": 2, "mute": True}
    )


@pytest.mark.asyncio
async def test_ui_route_audio_and_unmute_dispatches_to_audio_mute_route():
    """ui.route with audio=true and mute=false still routes to audio_mute_route."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws,
            {"type": "ui.route", "element_id": "matrix1", "output": 2, "mute": False, "audio": True},
            "panel",
        )
    engine.handle_ui_event.assert_awaited_once_with(
        "audio_mute_route", "matrix1", {"output": 2, "mute": False}
    )


# ── Panel allowed types completeness check ──


def test_panel_allowed_types_includes_expected():
    """Verify core panel message types are in the allowed set."""
    for msg_type in ["ui.press", "ui.release", "ui.change", "ui.page", "command",
                     "macro.execute", "state.set", "pong"]:
        assert msg_type in _PANEL_ALLOWED_TYPES


def test_programmer_only_types_excluded_from_panel():
    """Verify programmer-only message types are NOT in the panel allowed set."""
    for msg_type in ["project.reload", "isc.send", "isc.broadcast",
                     "log.subscribe", "log.unsubscribe"]:
        assert msg_type not in _PANEL_ALLOWED_TYPES


# ── Log stream gating ──
# The log buffer captures verbatim transport TX/RX, which can include device
# login credentials. Panel clients are unauthenticated, so the log stream is
# programmer-only.


@pytest.mark.asyncio
async def test_panel_cannot_subscribe_to_logs():
    """Panel log.subscribe is rejected: no history, no subscription started."""
    from server.api.ws import _log_subscriptions

    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "log.subscribe"}, "panel")
    assert id(ws) not in _log_subscriptions
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "error"
    assert all(m["type"] != "log.history" for m in ws.sent)


@pytest.mark.asyncio
async def test_programmer_can_subscribe_to_logs():
    """Programmer log.subscribe still gets history and a live subscription."""
    from server.api.ws import _cleanup_log_subscription, _log_subscriptions

    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "log.subscribe"}, "programmer")
    try:
        assert ws.sent[0]["type"] == "log.history"
        assert id(ws) in _log_subscriptions
    finally:
        _cleanup_log_subscription(id(ws))


# ── ui.submit / ui.route value validation ──


@pytest.mark.asyncio
async def test_ui_submit_rejects_dict_value():
    """ui.submit rejects non-primitive values (same rule as ui.change)."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.submit", "element_id": "kp1", "value": {"nested": True}}, "panel"
        )
    engine.handle_ui_event.assert_not_awaited()
    assert ws.sent[0]["type"] == "error"
    assert "primitive" in ws.sent[0]["message"].lower()


@pytest.mark.asyncio
async def test_ui_submit_rejects_list_value():
    """ui.submit rejects list values."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.submit", "element_id": "kp1", "value": [1, 2]}, "panel"
        )
    engine.handle_ui_event.assert_not_awaited()
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_ui_submit_valid_value_dispatches():
    """ui.submit with a primitive value dispatches to the engine."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws, {"type": "ui.submit", "element_id": "kp1", "value": "123"}, "panel"
        )
    engine.handle_ui_event.assert_awaited_once_with("submit", "kp1", {"value": "123"})


@pytest.mark.asyncio
async def test_ui_route_rejects_nested_input():
    """ui.route rejects non-primitive input/output indices."""
    ws = FakeWS()
    engine = _make_engine()
    with patch("server.api._engine._engine", engine):
        await _handle_message(
            ws,
            {"type": "ui.route", "element_id": "matrix1", "input": {"i": 1}, "output": 2},
            "panel",
        )
    engine.handle_ui_event.assert_not_awaited()
    assert ws.sent[0]["type"] == "error"


# ── _send_ws exception handling ──


class _RaisingWS:
    """WebSocket stub whose send always raises the given exception."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def send_json(self, data: dict):
        raise self._exc


@pytest.mark.asyncio
async def test_send_ws_swallows_disconnect_silently(caplog):
    """Disconnect-class send failures are expected and stay silent."""
    import logging

    with caplog.at_level(logging.DEBUG, logger="server.api.ws"):
        await _send_ws(_RaisingWS(ConnectionResetError("gone")), {"type": "x"})
    assert not caplog.records


@pytest.mark.asyncio
async def test_send_ws_logs_unexpected_send_failure(caplog):
    """Non-disconnect send failures are debug-logged instead of vanishing."""
    import logging

    with caplog.at_level(logging.DEBUG, logger="server.api.ws"):
        await _send_ws(_RaisingWS(ValueError("encode boom")), {"type": "x"})
    assert any("send failed" in r.message.lower() for r in caplog.records)


# ── Handshake ordering: register before snapshot (V-LC-005) ──


@pytest.mark.asyncio
async def test_ws_client_registered_before_snapshot(tmp_path):
    """The connection handler must register the client (delivery deferred)
    BEFORE taking the state snapshot, so changes flushed mid-handshake buffer
    into its queue instead of being missed."""
    from fastapi import WebSocketDisconnect

    from server.api.ws import _run_ws_connection
    from server.core.engine import Engine

    eng = Engine(str(tmp_path / "no_project.avc"))
    order: list[str] = []

    real_add = eng.add_ws_client

    def spy_add(ws, ns_prefixes=None, **kwargs):
        order.append("register")
        real_add(ws, ns_prefixes=ns_prefixes, **kwargs)

    eng.add_ws_client = spy_add

    class HandshakeWS:
        async def accept(self, subprotocol=None):
            pass

        async def send_json(self, data):
            order.append(f"send:{data.get('type')}")

        async def send_text(self, text):
            pass

        async def receive_text(self):
            raise WebSocketDisconnect(1000)

        async def close(self, code=1000, reason=None):
            pass

    with patch("server.api._engine._engine", eng):
        await _run_ws_connection(HandshakeWS(), {}, {}, "programmer")

    assert "register" in order
    assert "send:state.snapshot" in order
    assert order.index("register") < order.index("send:state.snapshot"), (
        f"snapshot taken before the client was registered: {order}"
    )


# ── macro.execute must not head-of-line block the client loop (V-LC-006) ──


@pytest.mark.asyncio
async def test_macro_execute_returns_before_macro_completes():
    """A long-running macro must not block the client's message loop — the
    handler acks immediately and the macro runs in the background."""
    ws = FakeWS()
    engine = _make_engine()
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_macro(macro_id, *a, **kw):
        started.set()
        await release.wait()

    engine.macros.execute = slow_macro
    with patch("server.api._engine._engine", engine):
        # Pre-fix this awaited the macro to completion and timed out here.
        await asyncio.wait_for(
            _handle_message(ws, {"type": "macro.execute", "macro_id": "warmup"}, "panel"),
            timeout=0.5,
        )
        assert ws.sent[0] == {"type": "macro.execute.ack", "macro_id": "warmup"}
        await asyncio.wait_for(started.wait(), timeout=0.5)
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_macro_execute_failure_still_reaches_client():
    """Errors from the background macro run come back on the same socket."""
    ws = FakeWS()
    engine = _make_engine()
    engine.macros.execute = AsyncMock(side_effect=ValueError("no such macro"))
    with patch("server.api._engine._engine", engine):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "ghost"}, "panel")
        for _ in range(10):
            await asyncio.sleep(0)
    types = [m["type"] for m in ws.sent]
    assert types[0] == "macro.execute.ack"
    assert "error" in types
