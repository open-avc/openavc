"""Observe delivered bytes through the real hub and direct-send adapter."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from openavc.core.state_store import StateStore
from openavc.core.ws_hub import WSHub


class Socket:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


@pytest.mark.asyncio
async def test_fanout_and_direct_sends_apply_same_recipient_policy():
    hub = WSHub(StateStore())
    panel, preview, programmer = Socket(), Socket(), Socket()
    for ws, kind in ((panel, "panel"), (preview, "preview"), (programmer, "programmer")):
        hub.add_client(ws, client_type=kind, authority=lambda: True)
    try:
        await hub.broadcast({"type": "macro.progress", "macro_id": "acme", "step_index": 1,
                             "total_steps": 2, "description": "Select input",
                             "device_results": [{"error": "private-marker"}]})
        # A producer's audience hint cannot grant a panel the diagnostic view.
        await hub.broadcast({"type": "script.error", "error": "private-marker"}, client_type="panel")
        for ws in (panel, preview, programmer):
            await hub.send_immediate(ws, {"type": "log.entry", "message": "private-marker"})
            await hub.send_immediate(ws, {"type": "state.snapshot", "state": {"var.volume": -12}})
        await hub.flush_sends()
        for ws in (panel, preview):
            assert "private-marker" not in json.dumps(ws.messages)
            assert {m["type"] for m in ws.messages} == {"state.snapshot", "macro.progress"}
        assert "private-marker" in json.dumps(programmer.messages)
    finally:
        for ws in (panel, preview, programmer):
            hub.remove_client(ws)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_revocation_while_queue_held_discards_already_projected_bytes():
    hub, ws = WSHub(StateStore()), Socket()
    valid = True
    hub.add_client(ws, defer_delivery=True, client_type="programmer", authority=lambda: valid)
    try:
        await hub.broadcast({"type": "script.error", "error": "private-marker"})
        valid = False
        hub.mark_client_ready(ws)
        await hub.flush_sends()
        await hub.send_immediate(ws, {"type": "state.snapshot", "state": {"var.x": 1}})
        assert ws.messages == []
    finally:
        hub.remove_client(ws)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_replaced_subscription_does_not_release_its_old_audit_queue():
    hub, ws = WSHub(StateStore()), Socket()
    hub.add_client(ws, defer_delivery=True, client_type="programmer", authority=lambda: True)
    try:
        old = hub.open_scope(ws)
        hub.send(ws, {"type": "audit.state", "session_id": "old", "state": {"private": "old"}}, scope=old)
        hub.close_scope(ws, old)
        new = hub.open_scope(ws)
        hub.send(ws, {"type": "audit.state", "session_id": "new", "state": {"private": "new"}}, scope=new)
        hub.mark_client_ready(ws)
        await hub.flush_sends()
        assert [m["session_id"] for m in ws.messages] == ["new"]
    finally:
        hub.remove_client(ws)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_initial_snapshot_precedes_buffered_state_and_audio_updates():
    hub, ws = WSHub(StateStore()), Socket()
    hub.add_client(ws, defer_delivery=True, authority=lambda: True)
    try:
        await hub.broadcast({"type": "state.update", "changes": {"var.x": 2}})
        await hub.send_immediate(ws, {"type": "state.snapshot", "state": {"var.x": 1}})
        hub.mark_client_ready(ws)
        await hub.flush_sends()
        assert ws.messages == [{"type": "state.snapshot", "state": {"var.x": 1}},
                               {"type": "state.update", "changes": {"var.x": 2}}]
    finally:
        hub.remove_client(ws)
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["command", "ui.press"])
async def test_runtime_diagnostic_reaches_only_the_requesting_programmer(tmp_path, monkeypatch, door):
    from openavc.api import ws as endpoint
    from openavc.core.engine import Engine
    from openavc.core.project_loader import load_project

    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0", "project": {"id": "sample", "name": "Sample"},
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [{
            "id": "power", "type": "button", "bindings": {"do": {"press": [{
                "action": "device.command", "device": "acme", "command": "power_on",
            }]}},
        }]}]},
    }))
    engine = Engine(str(path))
    engine.project = load_project(path)
    engine.devices.send_command = AsyncMock(side_effect=RuntimeError("nested-private-marker"))
    monkeypatch.setattr(endpoint, "get_engine_optional", lambda: engine)
    sockets = [(Socket(), kind) for kind in ("panel", "preview", "programmer", "programmer")]
    for ws, kind in sockets:
        engine.ws.add_client(ws, client_type=kind, authority=lambda: True)
    try:
        # The internal result keeps useful diagnostics independently of its
        # room projection. No exception string is considered safe guidance.
        result = await engine.handle_ui_event("press", "power")
        assert "nested-private-marker" in result[0]["error"]
        for ws, kind in sockets[:3]:
            await endpoint._handle_message(ws, {
                "type": door, "element_id": "power", "device_id": "acme", "command": "power_on",
            }, "panel" if kind == "preview" else kind)
        for ws, kind in sockets[:3]:
            wire = json.dumps(ws.messages)
            assert ("nested-private-marker" in wire) is (kind == "programmer")
            assert ws.messages
        assert sockets[3][0].messages == []
    finally:
        for ws, _kind in sockets:
            engine.ws.remove_client(ws)
        await asyncio.sleep(0)
