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
from openavc.core.ws_hub import (
    CLIENT_TYPES,
    MESSAGE_AUDIENCE,
    PANEL,
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
