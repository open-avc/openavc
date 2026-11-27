"""What a WebSocket client is sent of a secret: its exit's row, read at the hub.

``core/ws_hub.MESSAGE_AUDIENCE`` says which client types a push reaches, and
``utils/secret_audience.MAY_CARRY_SECRET`` whether each may carry a secret.
The hub reads both for every client it sends to, on the broadcast and on the
event stream: a panel and the Programmer may not carry one, so a plugin's
start failure, a macro step's exception and a script's traceback reach the
Programmer with every registered secret and an address's password masked, as
the log already writes them. What this system's panels draw leaves as it is:
the live state (a driver's stream address with its login) and the pages the
project defines (an element's address as its author typed it). So does an
event someone published (a project's, a panel's entry, a driver's or a
plugin's own); the platform's own failure events are masked as their pushes
are.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any

import pytest

from openavc.api.ws import _cleanup_event_subscription, _subscribe_events
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.core.ws_hub import (
    MESSAGE_AUDIENCE,
    PANEL,
    PROGRAMMER,
    SECRET_EXITS,
    WSHub,
    secret_exit,
)
from openavc.utils.log_redaction import REDACTED, get_secret_registry
from openavc.utils.secret_audience import MAY_CARRY_SECRET, Exit, may_carry_secret

PASSWORD = "Widget-Pass-9931"
USERNAME = "acme_operator"
ADDRESS = "https://operator:Url-Pass-4417@10.0.0.9/api"
ADDRESS_SHOWN = f"https://operator:{REDACTED}@10.0.0.9/api"


@pytest.fixture
def registry():
    reg = get_secret_registry()
    reg.clear()
    reg.set_config_secrets("acme_widget", {PASSWORD}, {USERNAME})
    yield reg
    reg.clear()


class _Socket:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    @property
    def received(self) -> list[dict[str, Any]]:
        return [json.loads(text) for text in self.texts]


async def _broadcast(*messages: dict[str, Any]) -> tuple[_Socket, _Socket]:
    """What a panel and the Programmer each receive of ``messages``."""
    hub = WSHub(StateStore())
    panel, programmer = _Socket(), _Socket()
    hub.add_client(panel, client_type=PANEL)
    hub.add_client(programmer, client_type=PROGRAMMER)
    for message in messages:
        await hub.broadcast(message)
    await hub.flush_sends()
    hub.remove_client(panel)
    hub.remove_client(programmer)
    return panel, programmer


# --- The Programmer's pushes ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_plugin_start_failure_reaches_the_programmer_with_its_secrets_masked(registry):
    # A plugin's start() raised quoting the device's password and an address
    # with its login, and its error is pushed as the loader emits it.
    panel, programmer = await _broadcast({
        "type": "plugin.error", "plugin_id": "acme_sync",
        "error": f"login {PASSWORD} refused by {ADDRESS}",
    })
    assert panel.received == []
    assert programmer.received == [{
        "type": "plugin.error", "plugin_id": "acme_sync",
        "error": f"login {REDACTED} refused by {ADDRESS_SHOWN}",
    }]


@pytest.mark.asyncio
async def test_a_macro_step_s_exception_reaches_the_programmer_masked_and_a_panel_as_its_sentence(
        registry):
    failure = {
        "macro_id": "lobby_on", "call_chain": ["lobby_on"], "step_index": 0,
        "total_steps": 1, "action": "acme_sync.push", "device": "", "group": "",
        "command": "", "error": f"push failed: {PASSWORD}",
        "message": "That didn't work. Try again.", "description": "Push the widget",
    }
    panel, programmer = await _broadcast({"type": "macro.step_error", **failure})
    assert programmer.received == [{
        "type": "macro.step_error", **failure, "error": f"push failed: {REDACTED}",
    }]
    assert panel.received == [{
        "type": "macro.step_error", **failure,
        "error": "That didn't work. Try again.",
    }]


@pytest.mark.asyncio
async def test_a_script_s_traceback_reaches_the_programmer_masked(registry):
    traceback = (
        'Traceback (most recent call last):\n'
        f'  File "lobby.py", line 4, in on_press\n    connect("{ADDRESS}", "{PASSWORD}")\n'
        f'RuntimeError: {PASSWORD}\n'
    )
    _panel, programmer = await _broadcast({
        "type": "script.error", "script_id": "lobby", "handler": "on_press",
        "event": "ui.press.b1", "error": f"RuntimeError: {PASSWORD}",
        "traceback": traceback,
    })
    [pushed] = programmer.received
    assert PASSWORD not in programmer.texts[0]
    assert "Url-Pass-4417" not in programmer.texts[0]
    assert pushed["error"] == f"RuntimeError: {REDACTED}"
    assert pushed["script_id"] == "lobby"


@pytest.mark.asyncio
async def test_a_secret_json_would_escape_is_masked_before_it_is_encoded(registry):
    registry.set_config_secrets("acme_quote", {'Pa"ss\\9931'}, set())
    _panel, programmer = await _broadcast({
        "type": "plugin.error", "plugin_id": "acme_sync", "error": 'bad login Pa"ss\\9931',
    })
    assert programmer.received[0]["error"] == f"bad login {REDACTED}"


# --- What the panels draw -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_project_s_pages_reach_every_client_as_the_author_typed_them(registry):
    # A video element plays the camera's stream, its login typed into the
    # address; the camera's password is a registered secret.
    stream = f"rtsp://{USERNAME}:{PASSWORD}@10.0.0.5/stream"
    definition = {
        "type": "ui.definition",
        "ui": {"pages": [{"id": "main", "elements": [
            {"id": "cam", "type": "video_stream", "url": stream}]}]},
        "macro_queue_max_wait_seconds": 30,
    }
    panel, programmer = await _broadcast(definition, {"type": "ui.navigate", "page_id": "main"})
    for sock in (panel, programmer):
        assert sock.texts == [json.dumps(definition),
                              json.dumps({"type": "ui.navigate", "page_id": "main"})]


@pytest.mark.asyncio
async def test_a_page_named_like_a_secret_is_still_navigated_to(registry):
    registry.set_config_secrets("acme_short", {"lobby"}, set())
    panel, _programmer = await _broadcast({"type": "ui.navigate", "page_id": "lobby"})
    assert panel.received == [{"type": "ui.navigate", "page_id": "lobby"}]


@pytest.mark.asyncio
async def test_the_names_a_client_routes_a_frame_by_stay_when_a_password_is_spelled_the_same(
        registry):
    # A password spelled like the macro's id and like a message type: the
    # panel still finds the run it started, and the frame keeps its type.
    registry.set_config_secrets("acme_short", {"lobby_on", "macro.step_error"}, set())
    frame = {
        "type": "macro.step_error", "macro_id": "lobby_on", "call_chain": ["lobby_on"],
        "request_id": "lobby_on", "error": "lobby_on failed", "message": "Try again.",
    }
    panel, programmer = await _broadcast(frame)
    [mine] = programmer.received
    assert (mine["type"], mine["macro_id"], mine["call_chain"], mine["request_id"]) == (
        "macro.step_error", "lobby_on", ["lobby_on"], "lobby_on")
    assert mine["error"] == f"{REDACTED} failed"
    [room] = panel.received
    assert (room["macro_id"], room["call_chain"]) == ("lobby_on", ["lobby_on"])


@pytest.mark.asyncio
async def test_live_state_reaches_a_panel_whole(registry):
    address = f"rtsp://{USERNAME}:{PASSWORD}@10.0.0.5/stream"
    panel, programmer = await _broadcast(
        {"type": "state.update", "changes": {"device.acme_widget.stream_url": address}})
    for sock in (panel, programmer):
        assert sock.received == [
            {"type": "state.update", "changes": {"device.acme_widget.stream_url": address}}]


# --- The event stream -------------------------------------------------------------


def _subscriber(bus: EventBus, hub: WSHub, client_type: str, *patterns: str) -> _Socket:
    sock = _Socket()
    hub.add_client(sock, client_type=client_type)
    engine = types.SimpleNamespace(events=bus, ws=hub)
    _subscribe_events(sock, engine, patterns, client_type)
    return sock


async def _heard(*subscriptions: tuple[str, tuple[str, ...]],
                 emits: list[tuple[str, Any]]) -> list[_Socket]:
    """What each ``(client type, patterns)`` subscriber hears of ``emits``."""
    bus, hub = EventBus(), WSHub(StateStore())
    engine = types.SimpleNamespace(events=bus, ws=hub)
    sockets = [_subscriber(bus, hub, client_type, *patterns)
               for client_type, patterns in subscriptions]
    try:
        for event, payload in emits:
            await bus.emit(event, payload)
        await hub.flush_sends()
    finally:
        for sock in sockets:
            _cleanup_event_subscription(id(sock), engine)
            hub.remove_client(sock)
    return sockets


@pytest.mark.asyncio
async def test_the_platform_s_failure_events_reach_the_event_stream_masked(registry):
    failure = {"plugin_id": "acme_sync", "error": f"login {PASSWORD} refused by {ADDRESS}"}
    step = {"macro_id": "lobby_on", "call_chain": ["lobby_on"], "error": f"push failed: {PASSWORD}",
            "message": "Try again."}
    device = {"device_id": "acme_widget", "error": f"auth {PASSWORD} rejected"}
    [programmer] = await _heard(
        (PROGRAMMER, ("plugin.*", "macro.*", "device.*")),
        emits=[("plugin.error", failure), ("macro.step_error.lobby_on", step),
               ("device.error.acme_widget", device)])
    assert [frame["payload"] for frame in programmer.received] == [
        {"plugin_id": "acme_sync", "error": f"login {REDACTED} refused by {ADDRESS_SHOWN}"},
        {**step, "error": f"push failed: {REDACTED}"},
        {"device_id": "acme_widget", "error": f"auth {REDACTED} rejected"},
    ]
    # The bus hands every subscriber the same payload: a trigger reading
    # ``$trigger.error`` still has the text.
    assert failure["error"] == f"login {PASSWORD} refused by {ADDRESS}"


@pytest.mark.asyncio
async def test_an_event_someone_published_reaches_its_subscribers_as_published(registry):
    # A keypad's code is the lock's saved PIN, and a script hands a custom
    # control a camera's address with its login: the flow and the control
    # get each as it was published, as they would from a variable.
    entry = {"element_id": "keypad", "value": PASSWORD}
    note = {"stream": f"rtsp://{USERNAME}:{PASSWORD}@10.0.0.5/stream", "seen": [PASSWORD, 3]}
    driver = {"device_id": "acme_widget", "token": PASSWORD}
    panel, programmer = await _heard(
        (PANEL, ("ui.*", "custom.*", "device.*")),
        (PROGRAMMER, ("ui.*", "custom.*", "device.*", "plugin.*")),
        emits=[("ui.submit.keypad", entry), ("custom.acme_note", note),
               ("device.acme_widget.paired", driver),
               ("plugin.acme_sync.synced", {"note": f"sent {PASSWORD}"})])
    published = [entry, note, driver]
    assert [frame["payload"] for frame in panel.received] == published
    assert [frame["payload"] for frame in programmer.received] == [
        *published, {"note": f"sent {PASSWORD}"}]


@pytest.mark.asyncio
async def test_a_failure_value_that_is_not_json_is_masked_as_its_text(registry):
    [programmer] = await _heard(
        (PROGRAMMER, ("device.*",)),
        emits=[("device.error.acme_widget", {"exc": RuntimeError(f"bad login {PASSWORD}")})])
    assert programmer.received[0]["payload"] == {"exc": f"bad login {REDACTED}"}


@pytest.mark.asyncio
async def test_a_routing_name_is_kept_only_as_a_frame_s_own_text(registry):
    # Deeper in a frame, or holding anything but text, a field spelled like a
    # routing name is masked like any other.
    _panel, programmer = await _broadcast({
        "type": "plugin.error", "plugin_id": "acme_sync",
        "error": {"command": f"login {PASSWORD}"}, "command": {"text": PASSWORD},
    })
    assert programmer.received == [{
        "type": "plugin.error", "plugin_id": "acme_sync",
        "error": {"command": f"login {REDACTED}"}, "command": {"text": REDACTED},
    }]


@pytest.mark.asyncio
async def test_the_log_view_s_stream_is_masked_as_its_history_is(registry):
    from unittest.mock import patch

    from openavc.api.ws import _log_stream_task

    class _Entry:
        def to_dict(self) -> dict[str, Any]:
            return {"level": "ERROR", "message": f"sync failed at {ADDRESS}"}

    hub = WSHub(StateStore())
    sock = _AskingSocket()
    hub.add_client(sock, client_type=PROGRAMMER)
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(_Entry())
    try:
        with patch("openavc.api._engine._engine", types.SimpleNamespace(ws=hub)):
            task = asyncio.create_task(_log_stream_task(sock, queue))
            for _ in range(100):
                if sock.sent:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
    finally:
        hub.remove_client(sock)
    assert sock.sent == [
        {"type": "log.entry", "level": "ERROR", "message": f"sync failed at {ADDRESS_SHOWN}"}]


# --- The table ----------------------------------------------------------------------


def test_every_push_leaves_by_an_exit_the_secret_table_has_a_row_for():
    for msg_type, reach in MESSAGE_AUDIENCE.items():
        for client_type in reach:
            where = secret_exit({"type": msg_type}, client_type)
            assert Exit(where) in MAY_CARRY_SECRET, msg_type


def test_only_what_the_panels_draw_leaves_carrying_a_secret():
    carried = {msg_type for msg_type, where in SECRET_EXITS.items() if may_carry_secret(where)}
    assert carried == {
        "state.snapshot", "state.update", "state.delete", "ui.definition", "ui.navigate"}
    assert not may_carry_secret(Exit.PANEL)
    assert not may_carry_secret(Exit.PROGRAMMER)


# --- A reply to the client's own message ------------------------------------------


class _AskingSocket:
    """A socket the WebSocket handler answers directly (``send_json``) and the
    hub queues pushes to (``send_text``)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(json.loads(json.dumps(data)))

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.mark.asyncio
async def test_a_macro_s_ending_reaches_the_programmer_that_asked_masked(registry):
    from unittest.mock import MagicMock, patch

    from openavc.api.ws import _handle_message
    from openavc.core.macro_engine import MacroEngine

    engine = MagicMock()
    engine.state = StateStore()
    engine.events = EventBus()
    engine.state.set_event_bus(engine.events)
    devices = MagicMock()
    engine.macros = MacroEngine(engine.state, engine.events, devices)

    async def push(_params: dict[str, Any], _context: Any) -> None:
        raise RuntimeError(f"login {PASSWORD} refused by {ADDRESS}")

    # A plugin's action, a step with no device: its exception is the
    # Programmer's text.
    engine.macros.register_plugin_action("acme_sync.push", push, "acme_sync", "Push")
    engine.macros.load_macros([{"id": "lobby_on", "name": "Lobby On", "steps": [
        {"action": "acme_sync.push"}]}])
    engine.isc = None
    engine.ws = WSHub(engine.state)
    asker = _AskingSocket()
    engine.ws.add_client(asker, client_type=PROGRAMMER)
    try:
        with patch("openavc.api._engine._engine", engine):
            await _handle_message(
                asker, {"type": "macro.execute", "macro_id": "lobby_on"}, PROGRAMMER)
            for _ in range(200):
                if any(m.get("type") == "macro.execute.result" for m in asker.sent):
                    break
                await asyncio.sleep(0.01)
    finally:
        engine.ws.remove_client(asker)
    [result] = [m for m in asker.sent if m.get("type") == "macro.execute.result"]
    assert result["macro_id"] == "lobby_on"
    assert result["step_errors"], result
    assert PASSWORD not in json.dumps(result)
    assert "Url-Pass-4417" not in json.dumps(result)
    assert result["step_errors"][0]["error"] == f"login {REDACTED} refused by {ADDRESS_SHOWN}"
