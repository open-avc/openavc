"""The event bus's two doors for an outside system, and their one policy.

Until these landed the bus left the box in neither direction: nothing on the
WebSocket carried a `custom.*` event (a control's Emit Event action was
invisible outside), and no REST or WS door emitted one (a flow in Node-RED
could not fire an `event` trigger). Both were worked around through `var.*`.

What is pinned here:

  * the stream -- subscribe, replace, unsubscribe, `?events=` at connect, and
    that a disconnect takes the bus handler with it;
  * what a PANEL-posture subscriber sees (what a panel already reflects) and
    what only a programmer sees; that `state.*` rides the state stream and
    never this one;
  * that the bus handler only enqueues -- a subscriber that stops reading is
    dropped, and the emit path never blocks on it;
  * the emit door: `custom.*` only, a JSON object for a payload, a receipt
    ack, the same sentence through REST and the socket;
  * and the claim the whole thing rests on: an emit from outside fires an
    `event` trigger, and the macro sees the payload.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openavc.api.ws as ws_module
from openavc.api.ws import (
    _PANEL_ALLOWED_TYPES,
    _cleanup_event_subscription,
    _event_subscriptions,
    _handle_message,
    _parse_event_patterns,
)
from openavc.core.event_bus import (
    EVENT_STREAM_EXCLUDED_PREFIXES,
    EXTERNAL_EMIT_PREFIXES,
    PANEL_VISIBLE_EVENT_PREFIXES,
    EventBus,
    check_event_emit,
    event_visible,
)
from openavc.core.state_store import StateStore
from openavc.core.ws_hub import _WS_SEND_QUEUE_MAX, WSHub


class FakeWS:
    """Records everything the server sends, whichever door it uses.

    ``send_json`` is the handler's direct path (acks, errors, data replies);
    ``send_text`` is what the hub's writer task calls for queued frames.
    """

    def __init__(self):
        self.sent: list[dict] = []
        self.closed: int | None = None

    async def send_json(self, data: dict):
        self.sent.append(data)

    async def send_text(self, text: str):
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000):
        self.closed = code

    def frames(self, type_: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == type_]


def _engine():
    """A real bus and a real hub behind a mock engine: the stream is exercised
    end to end, from `events.emit` to the bytes a client reads."""
    engine = MagicMock()
    engine.state = StateStore()
    engine.events = EventBus()
    engine.state.set_event_bus(engine.events)
    engine.ws = WSHub(engine.state)
    engine.macros = MagicMock()
    engine.macros.execute = AsyncMock()
    engine.isc = None
    return engine


def _plugin(engine, plugin_id="foo"):
    from openavc.core.plugin_api import PluginAPI
    from openavc.core.plugin_registry import PluginRegistry
    return PluginAPI(plugin_id, ["event_emit"], {}, PluginRegistry(plugin_id),
                     engine.state, engine.events, engine.macros, MagicMock(), "fixture")


@pytest.fixture
async def stream(request):
    """A connected client plus the engine it is talking to, torn down after.
    Async because the hub starts each client's writer task on the running loop."""
    engine = _engine()
    ws = FakeWS()
    option = getattr(request, "param", "panel")
    kind, held = option if isinstance(option, tuple) else (option, False)
    engine.ws.add_client(ws, client_type=kind, defer_delivery=held, authority=lambda: True)
    with patch("openavc.api._engine._engine", engine):
        yield engine, ws
    _cleanup_event_subscription(id(ws), engine)
    engine.ws.remove_client(ws)


async def _say(ws, engine, msg: dict, client_type: str = "panel") -> None:
    await _handle_message(ws, msg, client_type)


async def _emit_and_deliver(engine, event: str, payload: dict | None = None) -> None:
    await engine.events.emit(event, payload or {})
    await engine.ws.flush_sends()


# --- The stream ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["panel", "programmer"], indirect=True)
async def test_matrix_socket_interactions_reach_event_subscribers(stream, request):
    from openavc.core.ui_events import UIEventRuntime

    engine, ws = stream
    kind = request.node.callspec.params["stream"]
    # No bindings: exercise the actual socket dispatcher and event producer
    # without commanding equipment or relying on a synthetic event payload.
    engine.project = None
    engine.handle_ui_event = UIEventRuntime(engine).handle
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["ui.*"]}, kind)
    for options in ({}, {"audio": True}, {"mute": True}, {"audio": True, "mute": False}):
        await _say(ws, engine, {"type": "ui.route", "element_id": "matrix",
                                "input": "source-1", "output": "destination-1", **options}, kind)
    await engine.ws.flush_sends()
    frames = ws.frames("event")
    assert [(f["event"], f["payload"]) for f in frames] == [
        ("ui.route.matrix", {"element_id": "matrix", "input": "source-1", "output": "destination-1"}),
        ("ui.audio_route.matrix", {"element_id": "matrix", "input": "source-1", "output": "destination-1"}),
        ("ui.mute_route.matrix", {"element_id": "matrix", "output": "destination-1", "mute": True}),
        ("ui.audio_mute_route.matrix", {"element_id": "matrix", "output": "destination-1", "mute": False}),
    ]


@pytest.mark.asyncio
async def test_subscribe_then_emit_delivers_a_frame(stream):
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.*"]})
    await _emit_and_deliver(engine, "custom.room_ready", {"room": "auditorium"})

    frames = ws.frames("event")
    assert len(frames) == 1
    assert frames[0]["event"] == "custom.room_ready"
    assert frames[0]["payload"] == {"room": "auditorium"}
    assert isinstance(frames[0]["timestamp"], float)


@pytest.mark.asyncio
async def test_subscribe_replies_with_the_set_that_is_live(stream):
    engine, ws = stream
    await _say(ws, engine, {
        "type": "event.subscribe",
        "patterns": [" custom.* ", "", "device.*", "custom.*"],
    })
    reply = ws.frames("event.subscribed")
    assert reply == [{"type": "event.subscribed", "patterns": ["custom.*", "device.*"]}]


@pytest.mark.asyncio
async def test_unsubscribe_silences_the_stream(stream):
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.*"]})
    await _say(ws, engine, {"type": "event.unsubscribe"})
    await _emit_and_deliver(engine, "custom.room_ready")

    assert ws.frames("event") == []
    assert ws.frames("event.subscribed")[-1]["patterns"] == []
    assert engine.events.handler_count() == 0


@pytest.mark.asyncio
async def test_a_second_subscribe_replaces_the_first(stream):
    """A reconnecting client resends its whole set; stacking would double
    every delivery."""
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.a"]})
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.b"]})
    assert engine.events.handler_count() == 1

    await _emit_and_deliver(engine, "custom.a")
    await _emit_and_deliver(engine, "custom.b")
    assert [f["event"] for f in ws.frames("event")] == ["custom.b"]


@pytest.mark.asyncio
async def test_a_subscribe_of_the_wrong_shape_is_an_error_frame(stream):
    engine, ws = stream
    for bad in ({"patterns": 5}, {"patterns": [1, "custom.*"]}, {"patterns": {"a": 1}}):
        await _say(ws, engine, {"type": "event.subscribe", **bad})
    errors = ws.frames("error")
    assert len(errors) == 3
    assert all(e["source_type"] == "event.subscribe" for e in errors)
    assert engine.events.handler_count() == 0


def test_the_query_form_and_the_message_form_parse_the_same():
    assert _parse_event_patterns("custom.*, ui.press.*,,") == ("custom.*", "ui.press.*")
    assert _parse_event_patterns(["custom.*", "ui.press.*"]) == ("custom.*", "ui.press.*")
    assert _parse_event_patterns([]) == ()
    assert _parse_event_patterns(None) is None


@pytest.mark.asyncio
async def test_a_panel_sees_what_a_panel_reflects_and_nothing_else(stream):
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["*"]}, "panel")
    for event in (
        "custom.x", "ui.press.btn", "device.connected.p1", "macro.started.m",
        "system.project.reloaded",
        "cloud.command", "plugin.foo.bar", "isc.peer.custom.x", "ai.device_command",
        "script.error",
    ):
        await engine.events.emit(event, {"macro_id": "m"} if event == "macro.started.m" else {})
    await engine.ws.flush_sends()

    seen = [f["event"] for f in ws.frames("event")]
    assert seen == [
        "custom.x", "ui.press.btn", "device.connected.p1", "macro.started.m",
        "system.project.reloaded",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["programmer"], indirect=True)
async def test_a_programmer_sees_the_rest_too(stream):
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["*"]}, "programmer")
    from openavc.cloud.command_handler import CommandHandler
    await engine.events.emit("custom.x", {})
    devices = MagicMock(send_command=AsyncMock())
    cloud = CommandHandler(MagicMock(), devices, engine.events)
    cloud._send_result = AsyncMock()
    await cloud._handle_device_command({"device_id": "acme", "command": "power_on",
                                        "params": {"nested": [1, 2]}, "user_name": "Operator",
                                        "user_id": "user-1"}, "request-1", "Operator")
    await _plugin(engine).event_emit("bar", {})
    await engine.events.emit("script.error", {"script_id": "room", "handler": "tick",
                                             "timer_id": "timer-1", "error": "detail", "traceback": "trace"})
    await engine.ws.flush_sends()

    seen = [f["event"] for f in ws.frames("event")]
    assert seen == ["custom.x", "cloud.command", "plugin.foo.bar", "script.error"]
    payloads = {frame["event"]: frame["payload"] for frame in ws.frames("event")}
    assert payloads["cloud.command"]["params"] == {"nested": [1, 2]}
    assert payloads["script.error"]["timer_id"] == "timer-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [("panel", True), ("programmer", True)], indirect=True)
async def test_plugin_publications_preserve_authored_data_without_a_prefix_grant(stream, request, caplog):
    from openavc.core.outbound_messages import plugin_publication

    engine, ws = stream
    kind = request.node.callspec.params["stream"][0]
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["*"]}, kind)
    data = {"nested": {"value": "before"}, "type": "state.snapshot", "event": "custom.forged"}
    await _plugin(engine).event_emit("changed", data)
    data["nested"]["value"] = "after"
    await _plugin(engine).event_emit("connected", None)
    await engine.events.emit("plugin.foo.forged", {"publication": "plugin", "value": "forged"})
    await engine.events.emit("plugin.foo.wrong", plugin_publication("foo", "right", {}))

    class LegacyValue:
        def __str__(self):
            return "legacy-text"

    class BrokenValue:
        def __str__(self):
            raise RuntimeError("private-conversion-marker")

    await _plugin(engine).event_emit("legacy", {"value": LegacyValue()})
    await _plugin(engine).event_emit("broken", {"value": BrokenValue()})
    engine.ws.mark_client_ready(ws)
    await engine.ws.flush_sends()
    frames = ws.frames("event")
    assert "private-conversion-marker" not in caplog.text
    if kind == "panel":
        assert frames == []
    else:
        assert [f["event"] for f in frames] == ["plugin.foo.changed", "plugin.foo.connected", "plugin.foo.legacy"]
        assert frames[0]["payload"]["nested"]["value"] == "before"
        assert frames[0]["type"] == "event"
        assert frames[1]["payload"] == {}
        assert frames[2]["payload"] == {"value": "legacy-text"}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["programmer"], indirect=True)
async def test_context_action_and_peer_mirror_keep_their_authenticated_source(stream, monkeypatch):
    from openavc.api import plugins
    from openavc.api.plugins import emit_context_action
    from openavc.core.isc import ISCManager

    engine, ws = stream
    monkeypatch.setattr(plugins, "_engine", engine)
    engine.project = SimpleNamespace(plugins={"foo": {}})
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["*"]}, "programmer")
    await emit_context_action("foo", "route", SimpleNamespace(json=AsyncMock(return_value={"input": [1, 2]})))
    await ISCManager._handle_remote_event(SimpleNamespace(events=engine.events), "peer-1", {
        "event": "custom.ready", "payload": {"value": 1, "source_instance": "forged"},
    })
    await engine.ws.flush_sends()
    frames = ws.frames("event")
    assert [f["event"] for f in frames] == ["plugin.foo.action.route", "isc.peer-1.custom.ready"]
    assert frames[0]["payload"] == {"input": [1, 2]}
    assert frames[1]["payload"] == {"value": 1, "source_instance": "peer-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [("panel", True)], indirect=True)
async def test_replacing_a_subscription_revokes_its_already_queued_events(stream):
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.old"]})
    await engine.events.emit("custom.old", {"value": "old"})
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.new"]})
    await engine.events.emit("custom.new", {"value": "new"})
    engine.ws.mark_client_ready(ws)
    await engine.ws.flush_sends()
    assert [f["event"] for f in ws.frames("event")] == ["custom.new"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["programmer"], indirect=True)
async def test_state_changes_ride_the_state_stream_never_this_one(stream):
    """A `*` subscriber must not get every state change twice."""
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["*"]}, "programmer")
    engine.state.set("var.volume", 40, source="test")
    await asyncio.sleep(0)  # the store's own state.changed emit
    await engine.ws.flush_sends()

    assert not [f for f in ws.frames("event") if f["event"].startswith("state.")]


def test_visibility_is_one_rule_with_three_lists():
    for prefix in EVENT_STREAM_EXCLUDED_PREFIXES:
        assert not event_visible(prefix + "x", panel=False)
        assert not event_visible(prefix + "x", panel=True)
    for prefix in PANEL_VISIBLE_EVENT_PREFIXES:
        assert event_visible(prefix + "x", panel=True)
    assert not event_visible("cloud.x", panel=True)
    assert event_visible("cloud.x", panel=False)


@pytest.mark.asyncio
async def test_a_subscriber_that_stops_reading_is_dropped_not_awaited():
    """The bus handler enqueues and returns. A client whose queue fills is
    dropped and closed -- exactly the panel rule -- and every emit before
    and after returns promptly."""
    engine = _engine()
    ws = FakeWS()
    # Delivery deferred and never released: the writer holds, the queue fills.
    engine.ws.add_client(ws, defer_delivery=True, authority=lambda: True)
    with patch("openavc.api._engine._engine", engine):
        await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.*"]})
    try:
        for i in range(_WS_SEND_QUEUE_MAX + 1):
            await asyncio.wait_for(engine.events.emit("custom.tick", {"i": i}), timeout=1.0)
        await asyncio.sleep(0)
        assert engine.ws.client_count == 0
        assert ws.closed == 1013
    finally:
        _cleanup_event_subscription(id(ws), engine)
        engine.ws.remove_client(ws)


@pytest.mark.asyncio
async def test_a_payload_that_is_not_json_does_not_take_the_stream_down(stream):
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.*"]})
    await _emit_and_deliver(engine, "custom.x", {"when": object()})
    frames = ws.frames("event")
    assert len(frames) == 1
    assert isinstance(frames[0]["payload"]["when"], str)


# --- The emit door ------------------------------------------------------------


def test_the_emit_policy():
    assert check_event_emit("custom.room_ready", {"a": 1}) is None
    assert check_event_emit("custom.room_ready", None) is None
    for bad in ("custom.", "custom. ", "device.connected.x", "cloud.command", "", None, 5):
        assert check_event_emit(bad, {}) is not None, bad
    assert check_event_emit("custom.x", [1]) is not None
    assert check_event_emit("custom.x", "str") is not None


def test_the_refusal_names_what_is_allowed():
    reason = check_event_emit("device.connected.x", {})
    for prefix in EXTERNAL_EMIT_PREFIXES:
        assert prefix in reason


def test_the_panel_door_is_open_to_all_three_event_messages():
    assert {"event.subscribe", "event.unsubscribe", "event.emit"} <= _PANEL_ALLOWED_TYPES


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", ["panel", "programmer"])
async def test_an_emit_reaches_a_bus_handler_and_is_acked(stream, client_type):
    engine, ws = stream
    seen: list[tuple[str, dict]] = []
    engine.events.on("custom.*", lambda e, p: seen.append((e, p)))

    await _say(
        ws, engine,
        {"type": "event.emit", "event": "custom.occupancy", "payload": {"occupied": True}},
        client_type,
    )
    assert ws.frames("event.emit.ack") == [{"type": "event.emit.ack", "event": "custom.occupancy"}]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert seen == [("custom.occupancy", {"occupied": True})]


@pytest.mark.asyncio
async def test_an_emit_outside_custom_is_refused_and_nothing_fires(stream):
    engine, ws = stream
    seen: list = []
    engine.events.on("device.*", lambda e, p: seen.append(e))

    await _say(ws, engine, {"type": "event.emit", "event": "device.connected.p1"}, "programmer")
    await asyncio.sleep(0)
    errors = ws.frames("error")
    assert len(errors) == 1 and errors[0]["source_type"] == "event.emit"
    assert ws.frames("event.emit.ack") == []
    assert seen == []


@pytest.mark.asyncio
async def test_the_ack_is_a_receipt_a_slow_handler_does_not_delay(stream):
    engine, ws = stream
    gate = asyncio.Event()

    async def slow(_e, _p):
        await gate.wait()

    engine.events.on("custom.*", slow)
    await asyncio.wait_for(
        _say(ws, engine, {"type": "event.emit", "event": "custom.x"}), timeout=1.0
    )
    assert ws.frames("event.emit.ack")
    gate.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_subscriber_hears_its_own_emit(stream):
    """The round trip a flow relies on to confirm delivery."""
    engine, ws = stream
    await _say(ws, engine, {"type": "event.subscribe", "patterns": ["custom.*"]})
    await _say(ws, engine, {"type": "event.emit", "event": "custom.x", "payload": {"n": 1}})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await engine.ws.flush_sends()
    assert [f["payload"] for f in ws.frames("event")] == [{"n": 1}]


# --- The claim the plan rests on ---------------------------------------------


@pytest.mark.asyncio
async def test_an_emit_from_outside_fires_an_event_trigger_with_the_payload(stream):
    """An `event` trigger on `custom.*` fires from a WebSocket emit, and the
    macro is handed the payload as its trigger context -- what `$trigger.x`
    reads."""
    from openavc.core.trigger_engine import TriggerEngine

    engine, ws = stream
    macros = MagicMock()
    macros.execute = AsyncMock()
    macros.is_macro_running.return_value = False  # a MagicMock is truthy: "already running"
    triggers = TriggerEngine(engine.state, engine.events, macros)
    triggers.load_triggers([{
        "id": "on_occupancy", "name": "On occupancy", "steps": [],
        "triggers": [{"id": "t1", "type": "event", "event_pattern": "custom.occupancy.*"}],
    }])
    await triggers.start(fire_startup=False)
    try:
        await _say(ws, engine, {
            "type": "event.emit",
            "event": "custom.occupancy.changed",
            "payload": {"occupied": True},
        })
        for _ in range(50):
            if macros.execute.await_count:
                break
            await asyncio.sleep(0.01)
        macros.execute.assert_awaited_once()
        context = macros.execute.await_args.kwargs["context"]
        assert context["event"] == "custom.occupancy.changed"
        assert context["occupied"] is True
    finally:
        await triggers.stop()


# --- REST, and the parity between the two doors ------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from openavc.api import rest, ws
    from openavc.main import app
    from tests.test_api_endpoints import _make_mock_engine

    engine = _make_mock_engine()
    engine.ws = WSHub(engine.state)
    engine.panel_ui.return_value = {"pages": [], "theme": {}}
    rest.set_engine(engine)
    ws.set_engine(engine)
    yield TestClient(app), engine
    rest.set_engine(None)
    ws.set_engine(None)


def test_rest_emit_is_a_receipt_and_the_handler_runs(client):
    c, engine = client
    ran = threading.Event()
    seen: dict = {}

    def handler(event, payload):
        seen.update(payload)
        ran.set()

    engine.events.on("custom.*", handler)
    resp = c.post("/api/events", json={"event": "custom.occupancy", "payload": {"occupied": True}})
    assert resp.status_code == 202
    assert resp.json() == {"status": "emitted", "event": "custom.occupancy"}
    assert ran.wait(2.0), "the emit never reached the bus"
    assert seen == {"occupied": True}


def test_rest_refuses_outside_custom_with_the_socket_s_own_sentence(client):
    c, engine = client
    resp = c.post("/api/events", json={"event": "device.connected.p1"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == check_event_emit("device.connected.p1", None)


def test_rest_refuses_a_payload_that_is_not_an_object(client):
    c, _ = client
    resp = c.post("/api/events", json={"event": "custom.x", "payload": [1, 2]})
    assert resp.status_code == 422
    assert resp.json()["detail"] == check_event_emit("custom.x", [1, 2])


def test_connect_with_events_query_subscribes_and_disconnect_lets_go(client):
    """The stock-node route: `?events=` at connect, no subscribe message.
    Through the real endpoint, so the connect and the cleanup on disconnect
    are the ones the handler actually runs."""
    c, engine = client
    baseline = engine.events.handler_count()
    with c.websocket_connect("/ws?client=panel&events=custom.*") as sock:
        assert sock.receive_json()["type"] == "state.snapshot"
        assert sock.receive_json()["type"] == "ui.definition"
        assert engine.events.handler_count() == baseline + 1

        sock.send_json({"type": "event.emit", "event": "custom.x", "payload": {"n": 1}})
        assert sock.receive_json() == {"type": "event.emit.ack", "event": "custom.x"}
        frame = sock.receive_json()
        assert frame["type"] == "event"
        assert frame["event"] == "custom.x"
        assert frame["payload"] == {"n": 1}
    assert engine.events.handler_count() == baseline
    assert not _event_subscriptions


def test_no_second_copy_of_the_emit_policy():
    """Both doors call the one function; neither restates the prefix."""
    from pathlib import Path

    server = Path(ws_module.__file__).resolve().parents[1]
    for rel in ("api/ws.py", "api/routes/events.py"):
        src = (server / rel).read_text(encoding="utf-8")
        assert "check_event_emit(" in src, rel
        assert '"custom."' not in src, f"{rel} restates the emit prefix"
