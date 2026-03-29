"""Tests for Inter-System Communication (ISC)."""

import asyncio
import json

import pytest

from server.core.isc import (
    ISCManager,
    PeerConnection,
    PeerInfo,
    _get_local_ip,
    _parse_peer_address,
    get_or_create_instance_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeDeviceManager:
    """Minimal stand-in for DeviceManager in tests."""

    def __init__(self):
        self.commands: list[tuple] = []

    async def send_command(self, device_id, command, params=None):
        self.commands.append((device_id, command, params))
        return f"ok:{command}"

    def list_devices(self):
        return []


@pytest.fixture
def devices():
    return FakeDeviceManager()


@pytest.fixture
def isc_no_auth(state, events, devices):
    """ISCManager with no auth key — rejects all inbound connections."""
    state.set_event_bus(events)
    return ISCManager(
        state=state,
        events=events,
        devices=devices,
        shared_state_patterns=["device.proj1.*", "var.*"],
        auth_key="",
        instance_id="aaaa-1111",
        instance_name="Test Room A",
        http_port=8080,
        manual_peers=[],
    )


@pytest.fixture
def isc(state, events, devices):
    """ISCManager with two shared patterns and auth key."""
    state.set_event_bus(events)
    return ISCManager(
        state=state,
        events=events,
        devices=devices,
        shared_state_patterns=["device.proj1.*", "var.*"],
        auth_key="testkey",
        instance_id="aaaa-1111",
        instance_name="Test Room A",
        http_port=8080,
        manual_peers=[],
    )


@pytest.fixture
def isc_with_auth(state, events, devices):
    """ISCManager with auth key."""
    state.set_event_bus(events)
    return ISCManager(
        state=state,
        events=events,
        devices=devices,
        shared_state_patterns=["var.*"],
        auth_key="secret123",
        instance_id="bbbb-2222",
        instance_name="Test Room B",
        http_port=8081,
        manual_peers=[],
    )


# ---------------------------------------------------------------------------
# Helper parsing tests
# ---------------------------------------------------------------------------

def test_parse_peer_address_with_port():
    host, port = _parse_peer_address("192.168.1.10:9090")
    assert host == "192.168.1.10"
    assert port == 9090


def test_parse_peer_address_default_port():
    host, port = _parse_peer_address("192.168.1.10")
    assert host == "192.168.1.10"
    assert port == 8080


def test_get_local_ip():
    ip = _get_local_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0


def test_instance_id_persistence(tmp_path):
    project_file = tmp_path / "project.avc"
    project_file.write_text("{}")

    id1 = get_or_create_instance_id(project_file)
    id2 = get_or_create_instance_id(project_file)
    assert id1 == id2  # Same ID on subsequent calls
    assert len(id1) == 36  # UUID format


# ---------------------------------------------------------------------------
# ISCManager status / lifecycle
# ---------------------------------------------------------------------------

def test_initial_status(isc):
    s = isc.get_status()
    assert s["enabled"] is True
    assert s["instance_id"] == "aaaa-1111"
    assert s["instance_name"] == "Test Room A"
    assert s["peer_count"] == 0
    assert s["connected_count"] == 0


def test_get_instances_empty(isc):
    assert isc.get_instances() == []


async def test_start_stop(isc):
    """ISC should start and stop without errors."""
    await isc.start()
    assert isc._running is True
    await isc.stop()
    assert isc._running is False


# ---------------------------------------------------------------------------
# State sharing
# ---------------------------------------------------------------------------

def test_shared_key_matching(isc):
    assert isc._is_shared_key("device.proj1.power") is True
    assert isc._is_shared_key("device.proj1.input") is True
    assert isc._is_shared_key("var.room_active") is True
    assert isc._is_shared_key("var.anything") is True
    assert isc._is_shared_key("device.proj2.power") is False
    assert isc._is_shared_key("system.started") is False


def test_get_shared_state(isc, state):
    state.set("device.proj1.power", "on", source="driver")
    state.set("device.proj1.input", "hdmi1", source="driver")
    state.set("var.room_active", True, source="macro")
    state.set("device.proj2.power", "off", source="driver")
    state.set("system.started", True, source="system")

    shared = isc._get_shared_state()
    assert shared == {
        "device.proj1.power": "on",
        "device.proj1.input": "hdmi1",
        "var.room_active": True,
    }


def test_apply_remote_state(isc, state):
    """Remote state should be stored under isc.<peer_id>.<key>."""
    isc._apply_remote_state("peer-1234", {
        "device.proj1.power": "on",
        "var.room_active": True,
    })
    assert state.get("isc.peer-1234.device.proj1.power") == "on"
    assert state.get("isc.peer-1234.var.room_active") is True


def test_local_state_change_batching(isc, state):
    """Changes to shared keys should be queued in the outgoing batch."""
    # Simulate the subscription callback
    isc._on_local_state_change("device.proj1.power", None, "on", "driver")
    isc._on_local_state_change("var.room_active", None, True, "macro")
    # ISC source should be skipped
    isc._on_local_state_change("isc.peer.something", None, "x", "isc")
    isc._on_local_state_change("device.proj1.input", None, "hdmi1", "isc")

    assert isc._outgoing_batch == {
        "device.proj1.power": "on",
        "var.room_active": True,
    }


def test_clear_isc_state(isc, state):
    state.set("isc.peer1.power", "on", source="isc")
    state.set("isc.peer2.active", True, source="isc")
    state.set("device.proj1.power", "on", source="driver")

    isc._clear_isc_state()

    assert state.get("isc.peer1.power") is None
    assert state.get("isc.peer2.active") is None
    assert state.get("device.proj1.power") == "on"  # Not cleared


# ---------------------------------------------------------------------------
# Inbound connection acceptance
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Minimal mock of a FastAPI WebSocket for testing accept_inbound."""

    def __init__(self):
        self.sent: list[str] = []
        self._closed = False

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self._closed = True

    def get_sent_msgs(self) -> list[dict]:
        return [json.loads(s) for s in self.sent]


async def test_accept_inbound_no_auth_configured(isc_no_auth):
    """When no auth key is configured, all inbound connections are rejected."""
    ws = FakeWebSocket()
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-aaa",
        "name": "Lobby",
        "auth_key": "any_value",
        "version": "0.1.0",
    }
    peer_id = await isc_no_auth.accept_inbound(ws, hello)
    assert peer_id is None
    msgs = ws.get_sent_msgs()
    assert msgs[0]["type"] == "isc.reject"
    assert "auth_not_configured" in msgs[0]["reason"]


async def test_accept_inbound_success(isc_with_auth):
    ws = FakeWebSocket()
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-aaa",
        "name": "Lobby",
        "auth_key": "secret123",
        "version": "0.1.0",
    }
    peer_id = await isc_with_auth.accept_inbound(ws, hello)
    assert peer_id == "peer-aaa"
    assert "peer-aaa" in isc_with_auth._connections
    assert isc_with_auth._peers["peer-aaa"].connected is True

    msgs = ws.get_sent_msgs()
    assert any(m["type"] == "isc.welcome" for m in msgs)


async def test_accept_inbound_missing_id(isc):
    ws = FakeWebSocket()
    hello = {"type": "isc.hello", "instance_id": "", "name": "Bad"}
    peer_id = await isc.accept_inbound(ws, hello)
    assert peer_id is None
    msgs = ws.get_sent_msgs()
    assert msgs[0]["type"] == "isc.reject"


async def test_accept_inbound_auth_mismatch(isc_with_auth):
    ws = FakeWebSocket()
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-bad",
        "name": "Hacker",
        "auth_key": "wrong_key",
    }
    peer_id = await isc_with_auth.accept_inbound(ws, hello)
    assert peer_id is None
    msgs = ws.get_sent_msgs()
    assert msgs[0]["type"] == "isc.reject"
    assert "auth_key" in msgs[0]["reason"]


async def test_accept_inbound_auth_success(isc_with_auth):
    ws = FakeWebSocket()
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-ok",
        "name": "Friend",
        "auth_key": "secret123",
    }
    peer_id = await isc_with_auth.accept_inbound(ws, hello)
    assert peer_id == "peer-ok"


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

async def test_handle_state_message(isc, state):
    """isc.state message should apply remote state."""
    # First register a peer
    ws = FakeWebSocket()
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-x", "name": "X", "auth_key": "testkey",
    })

    await isc.handle_message("peer-x", {
        "type": "isc.state",
        "changes": {"device.proj1.power": "off", "var.mode": "standby"},
    })

    assert state.get("isc.peer-x.device.proj1.power") == "off"
    assert state.get("isc.peer-x.var.mode") == "standby"


async def test_handle_command_message(isc, devices):
    """isc.command should execute on local DeviceManager and send result."""
    ws = FakeWebSocket()
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-cmd", "name": "Cmd", "auth_key": "testkey",
    })
    ws.sent.clear()  # Clear welcome messages

    await isc.handle_message("peer-cmd", {
        "type": "isc.command",
        "id": "req-1",
        "device": "proj1",
        "command": "power_on",
        "params": {},
    })

    assert len(devices.commands) == 1
    assert devices.commands[0] == ("proj1", "power_on", {})

    # Check result was sent back
    msgs = ws.get_sent_msgs()
    result_msg = next(m for m in msgs if m["type"] == "isc.command_result")
    assert result_msg["id"] == "req-1"
    assert result_msg["success"] is True
    assert result_msg["result"] == "ok:power_on"


async def test_handle_command_result(isc):
    """isc.command_result should resolve a pending future."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    isc._pending_commands["req-42"] = future

    isc._handle_command_result({
        "type": "isc.command_result",
        "id": "req-42",
        "success": True,
        "result": "done",
    })

    assert future.done()
    assert future.result() == "done"


async def test_handle_command_result_failure(isc):
    """isc.command_result with success=false should set exception."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    isc._pending_commands["req-err"] = future

    isc._handle_command_result({
        "type": "isc.command_result",
        "id": "req-err",
        "success": False,
        "error": "Device not found",
    })

    assert future.done()
    with pytest.raises(RuntimeError, match="Device not found"):
        future.result()


async def test_handle_event_message(isc, events):
    """isc.event should emit on local EventBus with isc prefix."""
    received = []
    events.on("isc.peer-ev.*", lambda e, p: received.append((e, p)))

    ws = FakeWebSocket()
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-ev", "name": "Ev", "auth_key": "testkey",
    })

    await isc.handle_message("peer-ev", {
        "type": "isc.event",
        "event": "custom.alarm",
        "payload": {"zone": "all"},
    })

    assert len(received) == 1
    assert received[0][0] == "isc.peer-ev.custom.alarm"
    assert received[0][1]["zone"] == "all"
    assert received[0][1]["source_instance"] == "peer-ev"


async def test_handle_ping(isc):
    """isc.ping should respond with isc.pong."""
    ws = FakeWebSocket()
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-ping", "name": "P", "auth_key": "testkey",
    })
    ws.sent.clear()

    await isc.handle_message("peer-ping", {"type": "isc.ping"})

    msgs = ws.get_sent_msgs()
    assert any(m["type"] == "isc.pong" for m in msgs)


# ---------------------------------------------------------------------------
# Peer disconnect
# ---------------------------------------------------------------------------

async def test_peer_disconnected(isc, events):
    """Disconnecting a peer should update tracking and emit event."""
    ws = FakeWebSocket()
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-dc", "name": "DC", "auth_key": "testkey",
    })
    assert isc._peers["peer-dc"].connected is True

    disconnected = []
    events.on("isc.peer_disconnected", lambda e, p: disconnected.append(p))

    await isc.peer_disconnected("peer-dc")

    assert isc._peers["peer-dc"].connected is False
    assert "peer-dc" not in isc._connections
    assert len(disconnected) == 1


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

async def test_reload_config(isc, state):
    """reload() should update patterns and manual peers."""
    await isc.start()

    # Initially shares device.proj1.* and var.*
    assert isc._is_shared_key("device.proj1.power") is True
    assert isc._is_shared_key("device.proj2.power") is False

    # Reload with new patterns
    await isc.reload(
        shared_state_patterns=["device.*", "var.room_active"],
        auth_key="new_secret",
        manual_peers=[],
    )

    assert isc._is_shared_key("device.proj2.power") is True
    assert isc._auth_key == "new_secret"

    await isc.stop()


# ---------------------------------------------------------------------------
# Duplicate connection tie-breaking
# ---------------------------------------------------------------------------

async def test_duplicate_connection_smaller_id_rejects_inbound(isc):
    """When our ID < peer ID, reject inbound (we keep our outbound)."""
    # isc has id "aaaa-1111", peer has "zzzz-9999" (greater)
    # First simulate an existing outbound connection
    ws_out = FakeWebSocket()
    isc._connections["zzzz-9999"] = PeerConnection(ws_out, "outbound")
    isc._peers["zzzz-9999"] = PeerInfo(
        instance_id="zzzz-9999", name="Z", host="1.2.3.4", port=8080,
        connected=True,
    )

    ws_in = FakeWebSocket()
    result = await isc.accept_inbound(ws_in, {
        "type": "isc.hello", "instance_id": "zzzz-9999", "name": "Z", "auth_key": "testkey",
    })
    assert result is None  # Rejected
    msgs = ws_in.get_sent_msgs()
    assert msgs[0]["type"] == "isc.reject"
    assert msgs[0]["reason"] == "duplicate"

    # Outbound connection still there
    assert "zzzz-9999" in isc._connections


async def test_duplicate_connection_larger_id_accepts_inbound(isc):
    """When our ID > peer ID, accept inbound (close our outbound)."""
    # isc has id "aaaa-1111", peer has "0000-0000" (smaller)
    ws_out = FakeWebSocket()
    isc._connections["0000-0000"] = PeerConnection(ws_out, "outbound")
    isc._peers["0000-0000"] = PeerInfo(
        instance_id="0000-0000", name="Zero", host="1.2.3.4", port=8080,
        connected=True,
    )

    ws_in = FakeWebSocket()
    result = await isc.accept_inbound(ws_in, {
        "type": "isc.hello", "instance_id": "0000-0000", "name": "Zero", "auth_key": "testkey",
    })
    assert result == "0000-0000"  # Accepted
    # New inbound connection replaces old outbound
    assert isc._connections["0000-0000"].direction == "inbound"
