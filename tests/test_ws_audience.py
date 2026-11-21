"""Who each server push reaches: one table, read by every broadcast.

``core/ws_hub.MESSAGE_AUDIENCE`` names, for every message type the server
pushes, the client types it reaches (the Programmer, a panel). A type with no
row reaches the Programmer only. No sender passes an audience of its own.

Two sweeps keep the table whole: every type a sender writes in the code, and
every type the engine forwards from its event bus, has a row. A new push
with no row fails here before it ships.
"""

from __future__ import annotations

import ast
import json
import logging
import types
from pathlib import Path
from typing import Any

import pytest

from openavc.core.engine import Engine
from openavc.core.event_bus import event_visible
from openavc.core.ws_hub import (
    CLIENT_TYPES,
    EVENT_AUDIENCE,
    MESSAGE_AUDIENCE,
    PANEL,
    PANEL_COPIES,
    PANEL_EVENT_NAMESPACES,
    PROGRAMMER,
    WSHub,
)
from openavc.core.state_store import StateStore

PACKAGE = Path(__file__).resolve().parent.parent / "openavc"


class _Socket:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.received.append(json.loads(text))


async def _sockets_reached(message: dict[str, Any]) -> set[str]:
    hub = WSHub(StateStore())
    sockets = {PANEL: _Socket(), PROGRAMMER: _Socket()}
    for client_type, sock in sockets.items():
        hub.add_client(sock, client_type=client_type)
    await hub.broadcast(message)
    await hub.flush_sends()
    for sock in sockets.values():
        hub.remove_client(sock)
    return {client_type for client_type, sock in sockets.items() if sock.received}


def test_every_row_names_only_client_types_a_socket_can_be():
    for msg_type, reach in MESSAGE_AUDIENCE.items():
        assert reach, f"'{msg_type}' reaches nobody"
        assert reach <= CLIENT_TYPES, f"'{msg_type}' names {sorted(reach - CLIENT_TYPES)}"


@pytest.mark.asyncio
@pytest.mark.parametrize("msg_type", sorted(MESSAGE_AUDIENCE))
async def test_each_type_reaches_exactly_its_row(msg_type):
    message = {"type": msg_type, "changes": {"var.a": 1}, "keys": ["var.a"]}
    assert await _sockets_reached(message) == MESSAGE_AUDIENCE[msg_type]


@pytest.mark.asyncio
async def test_a_type_with_no_row_reaches_only_the_programmer_and_is_logged_once(
        caplog, unrowed_push_allowed):
    with caplog.at_level(logging.WARNING, logger="openavc.core.ws_hub"):
        hub = WSHub(StateStore())
        panel, programmer = _Socket(), _Socket()
        hub.add_client(panel, client_type=PANEL)
        hub.add_client(programmer, client_type=PROGRAMMER)
        await hub.broadcast({"type": "acme.widget_moved"})
        await hub.broadcast({"type": "acme.widget_moved"})
        await hub.flush_sends()
    assert panel.received == []
    assert programmer.received == [{"type": "acme.widget_moved"}] * 2
    lines = [r.getMessage() for r in caplog.records if "acme.widget_moved" in r.getMessage()]
    assert len(lines) == 1


# --- The sweeps ---------------------------------------------------------------


class _Bridge:
    """Stands in for the engine when its bus forwarders are wired: records
    each subscription, and the type of every message a forwarder sends."""

    def __init__(self) -> None:
        self._event_sub_ids: list[int] = []
        self.subscriptions: list[tuple[str, Any]] = []
        self.sent: list[Any] = []
        self.events = types.SimpleNamespace(on=self._on)

    def _on(self, pattern: str, handler: Any) -> int:
        self.subscriptions.append((pattern, handler))
        return len(self.subscriptions)

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        self.sent.append(message.get("type"))

    def __getattr__(self, name: str) -> Any:
        return types.MethodType(getattr(Engine, name), self)


@pytest.mark.asyncio
async def test_every_type_the_engine_forwards_from_the_bus_has_a_row():
    bridge = _Bridge()
    Engine._bridge_events_to_ws(bridge)
    assert bridge.subscriptions
    for pattern, handler in bridge.subscriptions:
        await handler(pattern.replace("*", "acme"), {})
    missing = sorted({t for t in bridge.sent if t not in MESSAGE_AUDIENCE})
    assert not missing, f"forwarded with no row in MESSAGE_AUDIENCE: {missing}"


# The calls a message is pushed through: the engine's door, the hub, and the
# callbacks handed one of them (a macro's, discovery's).
_DOORS = {"broadcast_ws", "_broadcast_ws", "broadcast", "_broadcast_fn",
          "_send_to_programmers", "_emit"}


def _callee(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _pushed_message(call: ast.Call) -> ast.expr | None:
    """The message argument of a push, or None when ``call`` is not one."""
    if _callee(call.func) in _DOORS and call.args:
        return call.args[0]
    if _callee(call.func) == "partial" and len(call.args) >= 2 and _callee(call.args[0]) in _DOORS:
        return call.args[1]
    return None


def _type_value(message: ast.Dict) -> ast.expr | None:
    for key, value in zip(message.keys, message.values):
        if isinstance(key, ast.Constant) and key.value == "type":
            return value
    return None


def _bound_dicts(scope: ast.AST, name: str) -> list[ast.Dict]:
    found = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                found.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                found.append(node.value)
    return found


def _module_constants(tree: ast.Module) -> dict[str, str]:
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
    return constants


def _types_sent_in(tree: ast.Module) -> set[str]:
    """Every literal ``type`` this module pushes through a door. A type built
    at run time is the bus forwarders' sweep's, above."""
    constants = _module_constants(tree)
    sent: set[str] = set()
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(scope):
            message = _pushed_message(call) if isinstance(call, ast.Call) else None
            if isinstance(message, ast.Dict):
                dicts = [message]
            elif isinstance(message, ast.Name):
                dicts = _bound_dicts(scope, message.id)
            else:
                continue
            for value in filter(None, map(_type_value, dicts)):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    sent.add(value.value)
                elif isinstance(value, ast.Name) and value.id in constants:
                    sent.add(constants[value.id])
    return sent


def _types_sent_in_the_code() -> dict[str, list[str]]:
    where: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "simulator" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for msg_type in _types_sent_in(tree):
            where.setdefault(msg_type, []).append(str(path.relative_to(PACKAGE.parent)))
    return where


def test_every_type_a_sender_names_in_the_code_has_a_row():
    sent = _types_sent_in_the_code()
    missing = {t: files for t, files in sent.items() if t not in MESSAGE_AUDIENCE}
    assert not missing, f"pushed with no row in MESSAGE_AUDIENCE: {missing}"


def test_the_sweep_reads_the_senders_it_is_meant_to():
    """Proof the source sweep is looking: types written each way it reads
    (a literal at the call, a name bound to the message, a module constant,
    a ``partial``, a callback) are all found."""
    sent = _types_sent_in_the_code()
    for msg_type in ("ui.navigate", "drivers.changed", "panel.devices.changed",
                     "project.reloaded", "discovery.update", "action.progress"):
        assert msg_type in sent, msg_type


# --- The event stream answers by the same table ---------------------------------
#
# A client subscribed to the bus by name hears an event the engine also pushes
# exactly when it would get that push, and any other event by its namespace's
# row; a panel's copy of either is the table's too.


async def _bridged_pushes() -> list[tuple[str, str]]:
    """Each bus event the engine forwards, named once, with the type it is
    pushed as."""
    bridge = _Bridge()
    Engine._bridge_events_to_ws(bridge)
    found = []
    for pattern, handler in bridge.subscriptions:
        event = pattern.replace("*", "acme")
        bridge.sent.clear()
        await handler(event, {})
        (msg_type,) = bridge.sent
        found.append((event, msg_type))
    return found


@pytest.mark.asyncio
async def test_a_bus_event_the_engine_pushes_reaches_a_client_on_both_paths_or_neither():
    for event, msg_type in await _bridged_pushes():
        for client_type in CLIENT_TYPES:
            on_the_stream = event_visible(event, panel=client_type == PANEL)
            pushed = client_type in MESSAGE_AUDIENCE[msg_type]
            assert on_the_stream == pushed, (event, client_type)


@pytest.mark.asyncio
async def test_the_event_stream_reads_a_pushed_type_s_row(monkeypatch):
    monkeypatch.setitem(MESSAGE_AUDIENCE, "macro.started", frozenset({PROGRAMMER}))
    assert not event_visible("macro.started.acme", panel=True)
    assert event_visible("macro.started.acme", panel=False)
    monkeypatch.setitem(MESSAGE_AUDIENCE, "script.error", frozenset({PANEL, PROGRAMMER}))
    assert event_visible("script.error", panel=True)


def test_the_event_stream_reads_a_namespace_s_row(monkeypatch):
    monkeypatch.setitem(EVENT_AUDIENCE, "custom.", frozenset({PROGRAMMER}))
    assert not event_visible("custom.acme", panel=True)
    assert event_visible("custom.acme", panel=False)
    monkeypatch.setitem(EVENT_AUDIENCE, "acme.", frozenset({PANEL, PROGRAMMER}))
    assert event_visible("acme.widget_moved", panel=True)


# The event stream's own rule before it read the table, kept here so the move
# is proven to have changed nothing a subscriber hears.
_BEFORE_EXCLUDED = ("state.",)
_BEFORE_PANEL = ("custom.", "ui.", "device.", "macro.", "system.")


def _heard_before(event: str, *, panel: bool) -> bool:
    if event.startswith(_BEFORE_EXCLUDED):
        return False
    return event.startswith(_BEFORE_PANEL) if panel else True


@pytest.mark.asyncio
async def test_what_a_subscriber_hears_is_what_it_heard_before_the_table():
    names = {event for event, _ in await _bridged_pushes()} | {
        "plugin.acme.moved", "plugin.error", "plugin.errorx", "plugin", "trigger.fired",
        "trigger.firedx", "script.error", "script.errors", "log.plugin", "cloud.x",
        "cloud.command", "isc.peer.custom.x", "ai.device_command", "project.reloaded",
        "error", "discovery.update", "drivers.changed", "macrox.y", "macro", "macro.x",
        "macro.started", "macro.started.a.b", "state.x", "state.changed.var.a",
        "custom.x", "custom", "ui.press.b", "ui.definition", "device.error.d",
        "device.connected.d", "system.started", "system.project.reloaded",
        "schedule.morning", "", ".",
    }
    for event in sorted(names):
        for panel in (True, False):
            assert event_visible(event, panel=panel) == _heard_before(event, panel=panel), (
                event, panel)


def test_the_node_red_package_and_the_server_name_the_same_namespaces():
    assert PANEL_EVENT_NAMESPACES == _BEFORE_PANEL


@pytest.mark.asyncio
async def test_a_panel_copy_in_the_table_is_what_a_panel_gets_of_a_push(monkeypatch):
    def worded(name, payload, hub):
        return {"type": name, "said": "The widget moved."}

    monkeypatch.setitem(MESSAGE_AUDIENCE, "acme.widget_moved", frozenset({PANEL, PROGRAMMER}))
    monkeypatch.setitem(PANEL_COPIES, "acme.widget_moved", worded)
    hub = WSHub(StateStore())
    panel, programmer = _Socket(), _Socket()
    hub.add_client(panel, client_type=PANEL)
    hub.add_client(programmer, client_type=PROGRAMMER)
    message = {"type": "acme.widget_moved", "error": "Traceback: /home/acme/secret.py"}
    await hub.broadcast(message)
    await hub.flush_sends()
    assert panel.received == [{"type": "acme.widget_moved", "said": "The widget moved."}]
    assert programmer.received == [message]


def test_a_pushed_bus_event_s_panel_copy_is_its_type_s(monkeypatch):
    def worded(name, payload, hub):
        return {"said": f"{name} ended"}

    monkeypatch.setitem(PANEL_COPIES, "macro.error", worded)
    hub = WSHub(StateStore())
    assert hub.panel_copy("macro.error.acme", {"error": "raw"}) == {
        "said": "macro.error.acme ended"}
    assert hub.panel_copy("macro.started.acme", {"macro_id": "acme"}) == {"macro_id": "acme"}


def test_a_device_error_s_panel_copy_names_the_device():
    store = StateStore()
    store.set("device.d1.name", "Lobby Widget", source="test")
    hub = WSHub(store)
    copy = hub.panel_copy(
        "device.error.d1", {"device_id": "d1", "error": "raw driver text", "source": "poll"})
    assert copy == {
        "device_id": "d1",
        "error": "Lobby Widget reported an error. Contact support.",
        "source": "poll",
    }


def test_the_panel_copies_name_only_rows_the_table_has():
    for key in PANEL_COPIES:
        assert key in MESSAGE_AUDIENCE or key.endswith("."), key


def test_an_event_the_engine_does_not_push_is_answered_without_walking_its_patterns(monkeypatch):
    """Every subscriber's check runs inside every emit on the bus, a state
    change included, so a name no pushed pattern can match is answered by one
    match against all of them, not one match per pattern."""
    from openavc.core import ws_hub

    calls = []
    real = ws_hub.fnmatch
    monkeypatch.setattr(ws_hub, "fnmatch", lambda name, pat: calls.append(pat) or real(name, pat))
    for event in ("state.changed.var.a", "device.connected.p1", "custom.x", "plugin.acme.moved"):
        event_visible(event, panel=True)
    assert calls == []
    assert ws_hub.bus_push_type("macro.started.acme") == "macro.started"
    assert ws_hub.bus_push_type("plugin.error") == "plugin.error"
