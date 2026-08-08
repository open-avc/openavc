"""Tests for Inter-System Communication (ISC)."""

import asyncio
import json

import pytest

from openavc.core.isc import (
    MAX_AUTH_FAIL_ENTRIES,
    ISCManager,
    PeerConnection,
    PeerInfo,
    _client_proof,
    _get_local_ip,
    _ISCAuthRejected,
    _parse_peer_address,
    _server_proof,
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
    # L-024: keys are deleted outright, not left as ghost None entries in the
    # snapshot every subscriber sees.
    snap = state.snapshot()
    assert "isc.peer1.power" not in snap
    assert "isc.peer2.active" not in snap
    assert "device.proj1.power" in snap


# ---------------------------------------------------------------------------
# Inbound connection acceptance
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Minimal mock of a FastAPI WebSocket for testing accept_inbound."""

    def __init__(self, auth_key: str = ""):
        self.sent: list[str] = []
        self._closed = False
        self._auth_key = auth_key

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        """Auto-respond to the mutual-auth challenge if auth_key is set."""
        last_msg = json.loads(self.sent[-1]) if self.sent else {}
        if last_msg.get("type") == "isc.challenge" and self._auth_key:
            nonce = last_msg["nonce"]
            return json.dumps({
                "type": "isc.auth",
                "response": _client_proof(self._auth_key, nonce),
            })
        return json.dumps({"type": "isc.auth", "response": "bad"})

    async def close(self) -> None:
        self._closed = True

    def get_sent_msgs(self) -> list[dict]:
        return [json.loads(s) for s in self.sent]


async def test_accept_inbound_no_auth_configured(isc_no_auth):
    """When no auth key is configured, all inbound connections are rejected."""
    ws = FakeWebSocket(auth_key="any_value")
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-aaa",
        "name": "Lobby",
        "version": "0.1.0",
    }
    peer_id = await isc_no_auth.accept_inbound(ws, hello)
    assert peer_id is None
    msgs = ws.get_sent_msgs()
    assert msgs[0]["type"] == "isc.reject"
    assert "auth_not_configured" in msgs[0]["reason"]


async def test_accept_inbound_success(isc_with_auth):
    ws = FakeWebSocket(auth_key="secret123")
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-aaa",
        "name": "Lobby",
        "version": "0.1.0",
    }
    peer_id = await isc_with_auth.accept_inbound(ws, hello)
    assert peer_id == "peer-aaa"
    assert "peer-aaa" in isc_with_auth._connections
    assert isc_with_auth._peers["peer-aaa"].connected is True

    msgs = ws.get_sent_msgs()
    assert any(m["type"] == "isc.challenge" for m in msgs)
    assert any(m["type"] == "isc.welcome" for m in msgs)


async def test_accept_inbound_missing_id(isc):
    ws = FakeWebSocket()
    hello = {"type": "isc.hello", "instance_id": "", "name": "Bad"}
    peer_id = await isc.accept_inbound(ws, hello)
    assert peer_id is None
    msgs = ws.get_sent_msgs()
    assert msgs[0]["type"] == "isc.reject"


async def test_accept_inbound_auth_mismatch(isc_with_auth):
    ws = FakeWebSocket(auth_key="wrong_key")
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-bad",
        "name": "Hacker",
    }
    peer_id = await isc_with_auth.accept_inbound(ws, hello)
    assert peer_id is None
    msgs = ws.get_sent_msgs()
    assert any(m["type"] == "isc.reject" for m in msgs)
    assert any("auth_failed" in m.get("reason", "") for m in msgs)


async def test_accept_inbound_auth_success(isc_with_auth):
    ws = FakeWebSocket(auth_key="secret123")
    hello = {
        "type": "isc.hello",
        "instance_id": "peer-ok",
        "name": "Friend",
    }
    peer_id = await isc_with_auth.accept_inbound(ws, hello)
    assert peer_id == "peer-ok"


async def test_inbound_auth_fail_dedup_counter_tracks_repeats(isc_with_auth):
    """A57 — repeated auth failures from the same peer increment the
    dedupe counter; the first failure logs at WARNING, the rest at DEBUG."""
    hello = {"type": "isc.hello", "instance_id": "peer-flood", "name": "Bad"}
    for _ in range(3):
        ws = FakeWebSocket(auth_key="wrong_key")
        peer_id = await isc_with_auth.accept_inbound(ws, hello)
        assert peer_id is None
    assert isc_with_auth._inbound_auth_fails["peer-flood"] == 3


async def test_inbound_auth_fail_counter_clears_on_success(isc_with_auth):
    """A57 — once the peer auths successfully, the counter resets."""
    hello = {"type": "isc.hello", "instance_id": "peer-recovers", "name": "Recovers"}
    # Two failures
    for _ in range(2):
        ws = FakeWebSocket(auth_key="wrong_key")
        await isc_with_auth.accept_inbound(ws, hello)
    assert isc_with_auth._inbound_auth_fails["peer-recovers"] == 2
    # Successful auth resets
    ws = FakeWebSocket(auth_key="secret123")
    peer_id = await isc_with_auth.accept_inbound(ws, hello)
    assert peer_id == "peer-recovers"
    assert "peer-recovers" not in isc_with_auth._inbound_auth_fails


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

async def test_handle_state_message(isc, state):
    """isc.state message should apply remote state."""
    # First register a peer
    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-x", "name": "X",     })

    await isc.handle_message("peer-x", {
        "type": "isc.state",
        "changes": {"device.proj1.power": "off", "var.mode": "standby"},
    })

    assert state.get("isc.peer-x.device.proj1.power") == "off"
    assert state.get("isc.peer-x.var.mode") == "standby"


async def test_handle_command_message(isc, devices):
    """isc.command should execute on local DeviceManager and send result —
    when the command is permitted by the allowlist (H-023)."""
    isc._allowed_remote_commands = ["proj1.*"]
    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-cmd", "name": "Cmd",     })
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


async def test_remote_command_denied_by_default(isc, devices):
    """H-023: with an empty allowlist a peer cannot run any device command;
    the request is refused without ever reaching the DeviceManager."""
    assert isc._allowed_remote_commands == []
    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-deny", "name": "Deny",     })
    ws.sent.clear()

    await isc.handle_message("peer-deny", {
        "type": "isc.command", "id": "req-x", "device": "proj1",
        "command": "power_on", "params": {},
    })

    assert devices.commands == []  # never executed
    result = next(m for m in ws.get_sent_msgs() if m["type"] == "isc.command_result")
    assert result["id"] == "req-x"
    assert result["success"] is False
    assert "not authorized" in result["error"]


async def test_remote_command_allowlist_is_scoped(isc, devices):
    """H-023: a specific allowlist entry permits only its target, not siblings."""
    isc._allowed_remote_commands = ["proj1.power_off"]
    assert isc._is_remote_command_allowed("proj1", "power_off") is True
    assert isc._is_remote_command_allowed("proj1", "power_on") is False
    assert isc._is_remote_command_allowed("proj2", "power_off") is False

    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-scope", "name": "Scope",     })
    ws.sent.clear()
    # A non-allowed command on an allowed device is still refused.
    await isc.handle_message("peer-scope", {
        "type": "isc.command", "id": "req-y", "device": "proj1",
        "command": "power_on", "params": {},
    })
    assert devices.commands == []
    result = next(m for m in ws.get_sent_msgs() if m["type"] == "isc.command_result")
    assert result["success"] is False


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

    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-ev", "name": "Ev",     })

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
    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-ping", "name": "P",     })
    ws.sent.clear()

    await isc.handle_message("peer-ping", {"type": "isc.ping"})

    msgs = ws.get_sent_msgs()
    assert any(m["type"] == "isc.pong" for m in msgs)


# ---------------------------------------------------------------------------
# Peer disconnect
# ---------------------------------------------------------------------------

async def test_peer_disconnected(isc, events):
    """Disconnecting a peer should update tracking and emit event."""
    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-dc", "name": "DC",     })
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


async def test_reload_drops_removed_manual_peers_pre_handshake(isc):
    """A10: removing a manual peer that hasn't completed handshake yet should
    cancel its connect attempt, close it, and remove it from _peers.
    """
    # Seed two manual peers as if start() had just been called and neither
    # has completed handshake — their keys are still "manual:host:port".
    isc._manual_peers = ["192.0.2.10:8080", "192.0.2.11:8080"]
    isc._peers["manual:192.0.2.10:8080"] = PeerInfo(
        instance_id="manual:192.0.2.10:8080", name="192.0.2.10:8080",
        host="192.0.2.10", port=8080, source="manual",
    )
    isc._peers["manual:192.0.2.11:8080"] = PeerInfo(
        instance_id="manual:192.0.2.11:8080", name="192.0.2.11:8080",
        host="192.0.2.11", port=8080, source="manual",
    )

    # Reload with only one peer kept.
    await isc.reload(
        shared_state_patterns=["var.*"],
        auth_key=isc._auth_key,
        manual_peers=["192.0.2.10:8080"],
    )

    assert "manual:192.0.2.10:8080" in isc._peers
    assert "manual:192.0.2.11:8080" not in isc._peers


async def test_reload_drops_removed_manual_peers_after_handshake(isc):
    """A10: a manual peer re-keyed to its real instance_id after handshake
    is still considered manual; removing its address should drop it too.
    """
    # Simulate completed handshake — the manual peer was re-keyed to the
    # real instance_id but kept source="manual" + host/port.
    real_id = "zzzz-9999"
    isc._manual_peers = ["192.0.2.20:8080"]
    isc._peers[real_id] = PeerInfo(
        instance_id=real_id, name="Remote", host="192.0.2.20", port=8080,
        source="manual", connected=True,
    )

    class FakeConn:
        async def close(self):
            self.closed = True
        closed = False

    fake = FakeConn()
    isc._connections[real_id] = fake

    await isc.reload(
        shared_state_patterns=["var.*"],
        auth_key=isc._auth_key,
        manual_peers=[],
    )

    assert real_id not in isc._peers, "removed manual peer must be dropped"
    assert real_id not in isc._connections, "connection must be closed"
    assert fake.closed, "_close_peer should have awaited conn.close()"


async def test_reload_auth_key_change_disconnects_existing_peers(isc):
    """A10: rotating auth_key must force-disconnect every active connection
    so they re-handshake with the new key. Existing sockets still hold the
    old key on both sides — leaving them up keeps using stale auth.
    """
    # Two connections: one manual (no entry in _manual_peers because it was
    # discovered via mDNS), one discovered.
    class FakeConn:
        def __init__(self):
            self.closed = False
        async def close(self):
            self.closed = True

    conn_a = FakeConn()
    conn_b = FakeConn()
    isc._peers["peer-a"] = PeerInfo(
        instance_id="peer-a", name="A", host="10.0.0.1", port=8080,
        source="mdns", connected=True,
    )
    isc._peers["peer-b"] = PeerInfo(
        instance_id="peer-b", name="B", host="10.0.0.2", port=8080,
        source="manual", connected=True,
    )
    isc._manual_peers = ["10.0.0.2:8080"]
    isc._connections["peer-a"] = conn_a
    isc._connections["peer-b"] = conn_b

    await isc.reload(
        shared_state_patterns=["var.*"],
        auth_key="rotated-key",
        manual_peers=["10.0.0.2:8080"],
    )

    assert conn_a.closed
    assert conn_b.closed
    assert "peer-a" not in isc._connections
    assert "peer-b" not in isc._connections
    assert isc._auth_key == "rotated-key"


async def test_reload_unchanged_auth_key_keeps_connections(isc):
    """A10: when auth_key doesn't change, existing connections stay up."""
    class FakeConn:
        def __init__(self):
            self.closed = False
        async def close(self):
            self.closed = True

    conn = FakeConn()
    isc._peers["peer-x"] = PeerInfo(
        instance_id="peer-x", name="X", host="10.0.0.3", port=8080,
        source="mdns", connected=True,
    )
    isc._connections["peer-x"] = conn

    await isc.reload(
        shared_state_patterns=["var.*"],
        auth_key=isc._auth_key,  # same key
        manual_peers=[],
    )

    assert not conn.closed
    assert "peer-x" in isc._connections


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

    ws_in = FakeWebSocket(auth_key="testkey")
    result = await isc.accept_inbound(ws_in, {
        "type": "isc.hello", "instance_id": "zzzz-9999", "name": "Z",     })
    assert result is None  # Rejected
    msgs = ws_in.get_sent_msgs()
    assert any(m["type"] == "isc.reject" and m["reason"] == "duplicate" for m in msgs)

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

    ws_in = FakeWebSocket(auth_key="testkey")
    result = await isc.accept_inbound(ws_in, {
        "type": "isc.hello", "instance_id": "0000-0000", "name": "Zero",     })
    assert result == "0000-0000"  # Accepted
    # New inbound connection replaces old outbound
    assert isc._connections["0000-0000"].direction == "inbound"


async def test_duplicate_connection_outbound_in_flight_smaller_id_rejects_inbound(isc):
    """A54: an in-flight outbound (still in handshake, not yet in _connections)
    must also count as a duplicate. With smaller local id, reject the inbound
    so the outbound can win the race."""
    # isc has id "aaaa-1111", peer "zzzz-9999" — local id is smaller, so the
    # outbound is the canonical direction.
    async def never_completes():
        await asyncio.sleep(60)

    fake_task = asyncio.create_task(never_completes())
    isc._connect_tasks["zzzz-9999"] = fake_task
    try:
        ws_in = FakeWebSocket(auth_key="testkey")
        result = await isc.accept_inbound(ws_in, {
            "type": "isc.hello", "instance_id": "zzzz-9999", "name": "Z",
        })
        assert result is None  # Rejected
        msgs = ws_in.get_sent_msgs()
        assert any(m["type"] == "isc.reject" and m["reason"] == "duplicate" for m in msgs)
        # Outbound task still scheduled and untouched
        assert "zzzz-9999" in isc._connect_tasks
        assert not fake_task.cancelled()
    finally:
        fake_task.cancel()
        try:
            await fake_task
        except asyncio.CancelledError:
            pass


async def test_duplicate_connection_outbound_in_flight_larger_id_cancels_outbound(isc):
    """A54: an in-flight outbound with larger local id loses to the inbound.
    accept_inbound must cancel the outbound task and accept the inbound."""
    # isc has id "aaaa-1111", peer "0000-0000" — local id is larger, so the
    # inbound is the canonical direction.
    async def never_completes():
        await asyncio.sleep(60)

    fake_task = asyncio.create_task(never_completes())
    isc._connect_tasks["0000-0000"] = fake_task

    ws_in = FakeWebSocket(auth_key="testkey")
    result = await isc.accept_inbound(ws_in, {
        "type": "isc.hello", "instance_id": "0000-0000", "name": "Zero",
    })
    assert result == "0000-0000"  # Accepted
    assert isc._connections["0000-0000"].direction == "inbound"
    # Outbound task cancelled and removed from tracking
    assert "0000-0000" not in isc._connect_tasks
    # Give the event loop a tick to process the cancellation
    await asyncio.sleep(0)
    assert fake_task.cancelled()


async def test_stale_disconnect_ignored_when_connection_replaced(isc, events):
    """A55: a stale orphan's late peer_disconnected must NOT pop the live
    replacement connection at the same peer_id."""
    # Set up: peer "peer-x" has a live connection.
    ws_live = FakeWebSocket()
    live_conn = PeerConnection(ws_live, "inbound")
    isc._connections["peer-x"] = live_conn
    isc._peers["peer-x"] = PeerInfo(
        instance_id="peer-x", name="X", host="1.2.3.4", port=8080,
        connected=True,
    )

    # A different (older, orphaned) PeerConnection wraps a dead socket.
    ws_orphan = FakeWebSocket()
    orphan_conn = PeerConnection(ws_orphan, "outbound")
    assert orphan_conn.connection_id != live_conn.connection_id

    disconnected = []
    events.on("isc.peer_disconnected", lambda e, p: disconnected.append(p))

    # The orphan fires peer_disconnected. It must not kill the live conn.
    await isc.peer_disconnected("peer-x", conn=orphan_conn)

    assert "peer-x" in isc._connections
    assert isc._connections["peer-x"] is live_conn
    assert isc._peers["peer-x"].connected is True
    assert len(disconnected) == 0  # No disconnect event emitted


async def test_stale_disconnect_closes_orphan_socket(isc):
    """A55: even when ignored, the orphan's socket should still be closed
    so it doesn't leak resources."""
    ws_live = FakeWebSocket()
    live_conn = PeerConnection(ws_live, "inbound")
    isc._connections["peer-y"] = live_conn
    isc._peers["peer-y"] = PeerInfo(
        instance_id="peer-y", name="Y", host="1.2.3.4", port=8080,
        connected=True,
    )

    ws_orphan = FakeWebSocket()
    orphan_conn = PeerConnection(ws_orphan, "outbound")

    await isc.peer_disconnected("peer-y", conn=orphan_conn)

    # Live socket untouched, orphan socket closed
    assert ws_live._closed is False
    assert ws_orphan._closed is True


async def test_disconnect_with_matching_conn_pops_entry(isc, events):
    """Sanity: when the conn matches the tracked entry, normal disconnect
    behavior still applies — pop, close, emit."""
    ws = FakeWebSocket()
    conn = PeerConnection(ws, "inbound")
    isc._connections["peer-z"] = conn
    isc._peers["peer-z"] = PeerInfo(
        instance_id="peer-z", name="Z", host="1.2.3.4", port=8080,
        connected=True,
    )

    disconnected = []
    events.on("isc.peer_disconnected", lambda e, p: disconnected.append(p))

    await isc.peer_disconnected("peer-z", conn=conn)

    assert "peer-z" not in isc._connections
    assert isc._peers["peer-z"].connected is False
    assert ws._closed is True
    assert len(disconnected) == 1


async def test_peer_connection_has_monotonic_id():
    """Sanity: each PeerConnection gets a unique increasing connection_id."""
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()
    c1 = PeerConnection(ws1, "outbound")
    c2 = PeerConnection(ws2, "inbound")
    assert c1.connection_id < c2.connection_id
    assert c1.connection_id != c2.connection_id


# ---------------------------------------------------------------------------
# TLS-aware discovery (HTTPS plan §6)
# ---------------------------------------------------------------------------


def test_beacon_omits_scheme_when_tls_off(isc, monkeypatch):
    """A v1-shape beacon — no scheme/tls_port — is emitted when TLS is off."""
    from openavc import config
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    raw = isc._build_beacon()
    from openavc.core.isc import DISCOVERY_MAGIC
    payload = json.loads(raw[len(DISCOVERY_MAGIC):])
    assert "scheme" not in payload
    assert "tls_port" not in payload
    assert payload["instance_id"] == "aaaa-1111"
    assert payload["port"] == 8080


def test_beacon_emits_scheme_when_tls_on(isc, monkeypatch):
    """TLS-on beacons advertise scheme=https + tls_port."""
    from openavc import config
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    raw = isc._build_beacon()
    from openavc.core.isc import DISCOVERY_MAGIC
    payload = json.loads(raw[len(DISCOVERY_MAGIC):])
    assert payload["scheme"] == "https"
    assert payload["tls_port"] == 8443


def test_handle_beacon_v1_defaults_to_http(isc, monkeypatch):
    """A beacon with no scheme field is treated as http (backward compat)."""
    captured = []
    monkeypatch.setattr(isc, "_schedule_connect", lambda *args: captured.append(args))
    from openavc.core.isc import DISCOVERY_MAGIC
    payload = DISCOVERY_MAGIC + json.dumps({
        "instance_id": "zzzz-9999",
        "name": "Old Peer",
        "port": 8080,
        "version": "0.10.0",
        "protocol": "1",
    }).encode()
    isc._handle_beacon(payload, ("192.168.1.50", 51000))

    assert "zzzz-9999" in isc._peers
    peer = isc._peers["zzzz-9999"]
    assert peer.scheme == "http"
    assert peer.port == 8080
    assert captured == [("zzzz-9999", "192.168.1.50", 8080, "http")]


def test_handle_beacon_v2_with_https_uses_tls_port(isc, monkeypatch):
    """A beacon with scheme=https + tls_port routes outbound to the TLS port."""
    captured = []
    monkeypatch.setattr(isc, "_schedule_connect", lambda *args: captured.append(args))
    from openavc.core.isc import DISCOVERY_MAGIC
    payload = DISCOVERY_MAGIC + json.dumps({
        "instance_id": "yyyy-8888",
        "name": "New Peer",
        "port": 8080,
        "version": "0.12.0",
        "protocol": "1",
        "scheme": "https",
        "tls_port": 8443,
    }).encode()
    isc._handle_beacon(payload, ("192.168.1.51", 51000))

    peer = isc._peers["yyyy-8888"]
    assert peer.scheme == "https"
    assert peer.port == 8443  # tls_port wins
    assert captured == [("yyyy-8888", "192.168.1.51", 8443, "https")]


def test_handle_beacon_https_falls_back_to_port_when_tls_port_missing(isc, monkeypatch):
    """scheme=https but no tls_port: fall back to the regular port field."""
    captured = []
    monkeypatch.setattr(isc, "_schedule_connect", lambda *args: captured.append(args))
    from openavc.core.isc import DISCOVERY_MAGIC
    payload = DISCOVERY_MAGIC + json.dumps({
        "instance_id": "xxxx-7777",
        "name": "Mid-Migration Peer",
        "port": 8443,
        "scheme": "https",
    }).encode()
    isc._handle_beacon(payload, ("192.168.1.52", 51000))

    peer = isc._peers["xxxx-7777"]
    assert peer.scheme == "https"
    assert peer.port == 8443


# ---------------------------------------------------------------------------
# Mutual auth (M-037) — inbound side
# ---------------------------------------------------------------------------


async def test_inbound_challenge_includes_server_proof(isc_with_auth):
    """M-037: the acceptor proves key possession over the peer's client_nonce
    in the challenge, so the peer can verify us before disclosing its HMAC."""
    ws = FakeWebSocket(auth_key="secret123")
    peer_id = await isc_with_auth.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-mp", "name": "MP",
        "client_nonce": "abc123",
    })
    assert peer_id == "peer-mp"
    challenge = next(m for m in ws.get_sent_msgs() if m["type"] == "isc.challenge")
    assert challenge["server_proof"] == _server_proof("secret123", "abc123")


async def test_inbound_tolerates_missing_client_nonce(isc_with_auth):
    """A hello without client_nonce still authenticates (proof over empty)."""
    ws = FakeWebSocket(auth_key="secret123")
    peer_id = await isc_with_auth.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-old", "name": "Old",
    })
    assert peer_id == "peer-old"
    challenge = next(m for m in ws.get_sent_msgs() if m["type"] == "isc.challenge")
    assert challenge["server_proof"] == _server_proof("secret123", "")


# ---------------------------------------------------------------------------
# Outbound handshake harness (H-022, M-037, M-040)
# ---------------------------------------------------------------------------


class FakeClientWS:
    """Scripts the *server* side of an outbound handshake.

    ``_outbound_connect`` does ``import websockets; async with
    websockets.connect(url) as ws``. We monkeypatch ``websockets.connect`` to
    return one of these. It records what the client sends, derives the
    server_proof/welcome from the client's hello, and can raise mid-loop to
    simulate an abnormal disconnect.
    """

    def __init__(self, auth_key, server_id="srv-0000", server_name="Server",
                 good_server_proof=True, loop_raises=None):
        self.sent: list[dict] = []
        self._auth_key = auth_key
        self._server_id = server_id
        self._server_name = server_name
        self._good_proof = good_server_proof
        self._loop_raises = loop_raises
        self.closed = False
        self._stage = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        self._stage += 1
        if self._stage == 1:
            # Answer the hello with a challenge proving the key over client_nonce.
            client_nonce = self.sent[-1].get("client_nonce", "")
            proof = _server_proof(self._auth_key, client_nonce)
            if not self._good_proof:
                proof = "deadbeef"  # a keyless/rogue server can't produce this
            return json.dumps({
                "type": "isc.challenge",
                "nonce": "server-nonce-fixed",
                "server_proof": proof,
            })
        # Answer the auth with a welcome.
        return json.dumps({
            "type": "isc.welcome",
            "instance_id": self._server_id,
            "name": self._server_name,
            "version": "9.9.9",
            "protocol": "1",
        })

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._loop_raises is not None:
            exc = self._loop_raises
            self._loop_raises = None
            raise exc
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


def _patch_websockets(monkeypatch, fake_ws):
    """Make ``import websockets; websockets.connect(...)`` yield ``fake_ws``."""
    import sys
    import types

    mod = types.ModuleType("websockets")

    def connect(url, **kwargs):
        fake_ws.url = url
        return fake_ws

    mod.connect = connect
    monkeypatch.setitem(sys.modules, "websockets", mod)
    return fake_ws


async def test_outbound_connect_registers_and_cleans_up(isc, monkeypatch):
    """M-040 happy path + H-022 clean close: full handshake registers the peer,
    and a normal message-loop end reconciles tracking (no leak)."""
    fake = FakeClientWS(auth_key="testkey", server_id="srv-1")
    _patch_websockets(monkeypatch, fake)

    await isc._outbound_connect("srv-1", "10.0.0.5", 8080, "http")

    hello = next(m for m in fake.sent if m["type"] == "isc.hello")
    assert hello["client_nonce"]  # we sent our nonce for mutual auth
    assert any(m["type"] == "isc.auth" for m in fake.sent)
    # Message loop ended → finally ran → connection not leaked.
    assert "srv-1" not in isc._connections
    assert isc._peers["srv-1"].connected is False


async def test_outbound_aborts_on_bad_server_proof(isc, monkeypatch):
    """M-037: a server that can't prove the key gets no HMAC from us — we abort
    before sending isc.auth, denying the relay/oracle."""
    fake = FakeClientWS(auth_key="testkey", good_server_proof=False)
    _patch_websockets(monkeypatch, fake)

    with pytest.raises(_ISCAuthRejected):
        await isc._outbound_connect("srv-2", "10.0.0.6", 8080, "http")

    assert any(m["type"] == "isc.hello" for m in fake.sent)
    assert not any(m["type"] == "isc.auth" for m in fake.sent)  # never disclosed
    assert "srv-2" not in isc._connections


async def test_outbound_no_leak_on_abnormal_disconnect(isc, monkeypatch):
    """H-022: an abnormal disconnect (exception out of the message loop) still
    reconciles tracking via the finally — the entry doesn't leak as connected."""
    fake = FakeClientWS(
        auth_key="testkey", server_id="srv-3",
        loop_raises=ConnectionResetError("cable pull"),
    )
    _patch_websockets(monkeypatch, fake)

    with pytest.raises(ConnectionResetError):
        await isc._outbound_connect("srv-3", "10.0.0.7", 8080, "http")

    assert "srv-3" not in isc._connections
    assert isc._peers["srv-3"].connected is False


async def test_outbound_tiebreak_preserves_inbound(isc, monkeypatch):
    """M-040: when a live inbound at a smaller peer id already exists, the
    outbound loses the tie-break (our id is larger) and must abort without
    touching the canonical inbound connection."""
    ws_in = FakeWebSocket()
    inbound = PeerConnection(ws_in, "inbound")
    isc._connections["0000-0000"] = inbound
    isc._peers["0000-0000"] = PeerInfo(
        instance_id="0000-0000", name="Zero", host="1.2.3.4", port=8080,
        connected=True,
    )
    fake = FakeClientWS(auth_key="testkey", server_id="0000-0000")
    _patch_websockets(monkeypatch, fake)

    with pytest.raises(ConnectionRefusedError, match="duplicate"):
        await isc._outbound_connect("0000-0000", "1.2.3.4", 8080, "http")

    assert isc._connections["0000-0000"] is inbound  # untouched


# ---------------------------------------------------------------------------
# Remote-state validation (H-024)
# ---------------------------------------------------------------------------


def test_apply_remote_state_drops_non_primitives(isc, state):
    """H-024: nested/list values and non-string keys are dropped so a peer
    can't break the flat-primitive store invariant."""
    isc._apply_remote_state("peer-bad", {
        "good": "on",
        "num": 5,
        "flag": True,
        "nested": {"a": 1},      # dropped
        "listy": [1, 2, 3],      # dropped
        123: "bad-key",          # non-str key dropped
    })
    snap = state.snapshot()
    assert snap.get("isc.peer-bad.good") == "on"
    assert snap.get("isc.peer-bad.num") == 5
    assert snap.get("isc.peer-bad.flag") is True
    assert "isc.peer-bad.nested" not in snap
    assert "isc.peer-bad.listy" not in snap


def test_apply_remote_state_ignores_non_dict(isc, state):
    """H-024: a non-dict isc.state payload is ignored without raising."""
    isc._apply_remote_state("peer-bad", ["not", "a", "dict"])  # no exception
    assert not any(k.startswith("isc.peer-bad.") for k in state.snapshot())


# ---------------------------------------------------------------------------
# Stale-state + peer pruning (M-034, M-039)
# ---------------------------------------------------------------------------


def test_prune_stale_peers_removes_silent_discovered(isc):
    """M-034: a discovered peer silent past BEACON_TTL is pruned; a connected
    peer and a manual (configured) peer are kept regardless of last_seen."""
    isc._peers["ghost"] = PeerInfo(
        instance_id="ghost", name="Ghost", host="10.0.0.1", port=8080,
        source="discovered", connected=False, last_seen=0.0,
    )
    isc._peers["live"] = PeerInfo(
        instance_id="live", name="Live", host="10.0.0.2", port=8080,
        source="discovered", connected=True, last_seen=0.0,
    )
    isc._peers["manual:x"] = PeerInfo(
        instance_id="manual:x", name="m", host="10.0.0.3", port=8080,
        source="manual", connected=False, last_seen=0.0,
    )

    isc._prune_stale_peers()

    assert "ghost" not in isc._peers
    assert "live" in isc._peers
    assert "manual:x" in isc._peers
    assert isc._peers  # sanity


def test_prune_clears_pruned_peer_state(isc, state):
    """M-034/M-039: pruning a ghost peer also clears its shared-state keys."""
    state.set("isc.ghost.device.x.power", "on", source="isc")
    isc._peers["ghost"] = PeerInfo(
        instance_id="ghost", name="Ghost", host="10.0.0.1", port=8080,
        source="discovered", connected=False, last_seen=0.0,
    )
    isc._prune_stale_peers()
    assert "isc.ghost.device.x.power" not in state.snapshot()


async def test_peer_state_cleared_on_disconnect(isc, state):
    """M-039: a peer's isc.<peer>.* keys are removed when it disconnects so
    stale values can't keep driving bindings/triggers."""
    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-s", "name": "S",     })
    isc._apply_remote_state("peer-s", {"device.x.power": "on", "var.y": 1})
    assert state.get("isc.peer-s.device.x.power") == "on"

    await isc.peer_disconnected("peer-s")

    assert state.get("isc.peer-s.device.x.power") is None
    assert "isc.peer-s.var.y" not in state.snapshot()


# ---------------------------------------------------------------------------
# Connect-task lifecycle (M-035) + auth-fail cap (M-036)
# ---------------------------------------------------------------------------


async def test_completed_connect_task_self_removes(isc, monkeypatch):
    """M-035: a finished outbound loop drops itself from _connect_tasks so a
    later beacon's _schedule_connect can re-dial the peer."""
    async def quick_loop(peer_id, host, port, scheme="http"):
        return

    monkeypatch.setattr(isc, "_outbound_loop", quick_loop)
    isc._schedule_connect("peer-q", "10.0.0.9", 8080)
    assert "peer-q" in isc._connect_tasks
    await asyncio.sleep(0.05)  # let the task finish + done-callback run
    assert "peer-q" not in isc._connect_tasks


def test_inbound_auth_fail_map_is_capped(isc_with_auth):
    """M-036: a flood of fresh attacker-chosen instance_ids can't grow the
    auth-fail dedupe map without bound."""
    for i in range(MAX_AUTH_FAIL_ENTRIES + 50):
        isc_with_auth._log_inbound_auth_fail(f"peer-{i}", "bad")
    assert len(isc_with_auth._inbound_auth_fails) <= MAX_AUTH_FAIL_ENTRIES


# ---------------------------------------------------------------------------
# send_to / send_command robustness (L-025, L-026)
# ---------------------------------------------------------------------------


async def test_send_to_distinguishes_known_but_unconnected(isc):
    """L-025: a known-but-not-yet-connected peer gets a precise error, distinct
    from an entirely unknown instance."""
    isc._peers["peer-known"] = PeerInfo(
        instance_id="peer-known", name="K", host="1.2.3.4", port=8080,
        connected=False,
    )
    with pytest.raises(ConnectionError, match="not connected yet"):
        await isc.send_to("peer-known", "evt", {})
    with pytest.raises(ConnectionError, match="Not connected to instance"):
        await isc.send_to("totally-unknown", "evt", {})


async def test_send_command_cleans_pending_on_send_failure(isc):
    """L-026: if conn.send() raises after the future is registered, both
    pending maps are cleaned via finally — no leaked future."""
    class BoomConn:
        async def send(self, msg):
            raise RuntimeError("socket dead")

    isc._connections["peer-boom"] = BoomConn()
    isc._peers["peer-boom"] = PeerInfo(
        instance_id="peer-boom", name="B", host="1.2.3.4", port=8080,
        connected=True,
    )

    with pytest.raises(RuntimeError, match="socket dead"):
        await isc.send_command("peer-boom", "dev", "cmd", {})

    assert isc._pending_commands == {}
    assert isc._pending_command_peers == {}


# ---------------------------------------------------------------------------
# Registration cleanup + remote-command concurrency
# ---------------------------------------------------------------------------


async def test_accept_inbound_unregisters_on_welcome_failure(isc_with_auth):
    """If the socket dies after registration (welcome send fails), the peer is
    unregistered — a leaked entry would block a legitimate reconnection."""

    class WelcomeFailWS(FakeWebSocket):
        async def send_text(self, data: str) -> None:
            if json.loads(data).get("type") == "isc.welcome":
                raise RuntimeError("socket closed")
            await super().send_text(data)

    hello = {"type": "isc.hello", "instance_id": "cccc-3333", "name": "Room C"}
    peer_id = await isc_with_auth.accept_inbound(WelcomeFailWS(auth_key="secret123"), hello)
    assert peer_id is None
    assert "cccc-3333" not in isc_with_auth._connections
    peer = isc_with_auth._peers.get("cccc-3333")
    assert peer is None or peer.connected is False

    # A reconnection from the same peer must now succeed (with a leaked
    # entry it would be rejected as a duplicate).
    peer_id = await isc_with_auth.accept_inbound(FakeWebSocket(auth_key="secret123"), hello)
    assert peer_id == "cccc-3333"


async def test_remote_command_in_flight_cap(isc, monkeypatch):
    """Concurrent isc.command executions are capped globally; over-cap
    requests get an error result instead of piling onto device I/O."""
    from openavc.core import isc as isc_mod
    monkeypatch.setattr(isc_mod, "MAX_REMOTE_COMMANDS_IN_FLIGHT", 1)

    release = asyncio.Event()

    class BlockingDevices(FakeDeviceManager):
        async def send_command(self, device_id, command, params=None):
            self.commands.append((device_id, command, params))
            await release.wait()
            return "ok"

    devices = BlockingDevices()
    isc.devices = devices
    isc._allowed_remote_commands = ["*"]

    ws = FakeWebSocket(auth_key="testkey")
    await isc.accept_inbound(ws, {
        "type": "isc.hello", "instance_id": "peer-cap", "name": "Cap",
    })
    ws.sent.clear()

    def cmd(rid):
        return {"type": "isc.command", "id": rid, "device": "proj1",
                "command": "power_on", "params": {}}

    first = asyncio.create_task(isc.handle_message("peer-cap", cmd("req-1")))
    await asyncio.sleep(0)  # let req-1 reach the blocking send_command
    assert isc._remote_commands_in_flight == 1

    await isc.handle_message("peer-cap", cmd("req-2"))  # over cap — rejected
    rejected = next(m for m in ws.get_sent_msgs() if m["type"] == "isc.command_result")
    assert rejected["id"] == "req-2"
    assert rejected["success"] is False
    assert "limit" in rejected["error"]
    assert len(devices.commands) == 1  # req-2 never reached the device

    release.set()
    await first
    done = next(m for m in ws.get_sent_msgs()
                if m["type"] == "isc.command_result" and m["id"] == "req-1")
    assert done["success"] is True
    assert isc._remote_commands_in_flight == 0

    # Capacity freed — the next command executes normally.
    await isc.handle_message("peer-cap", cmd("req-3"))
    assert len(devices.commands) == 2


# ---------------------------------------------------------------------------
# ISC WebSocket endpoint (openavc/api/isc_ws.py)
# ---------------------------------------------------------------------------


class ScriptedWS(FakeWebSocket):
    """Drives the real isc_websocket_endpoint with a scripted message list,
    auto-answering the auth challenge, then disconnecting."""

    def __init__(self, auth_key: str, script: list[dict]):
        super().__init__(auth_key)
        self._script = list(script)

    async def accept(self) -> None:
        pass

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self._closed = True

    async def receive_text(self) -> str:
        last_msg = json.loads(self.sent[-1]) if self.sent else {}
        if last_msg.get("type") == "isc.challenge" and self._auth_key:
            return json.dumps({
                "type": "isc.auth",
                "response": _client_proof(self._auth_key, last_msg["nonce"]),
            })
        if self._script:
            return json.dumps(self._script.pop(0))
        from fastapi import WebSocketDisconnect
        raise WebSocketDisconnect(1000)


async def test_isc_ws_rate_limit_survives_reconnect(isc, state, monkeypatch):
    """The per-peer message budget is keyed by peer id, not connection —
    reconnect-cycling must not reset the sliding window."""
    from openavc.api import isc_ws
    monkeypatch.setattr(isc_ws, "_ISC_MAX_MESSAGES_PER_MINUTE", 3)
    monkeypatch.setattr(isc_ws, "_isc_manager", isc)
    monkeypatch.setattr(isc_ws, "_peer_msg_times", {})

    hello = {"type": "isc.hello", "instance_id": "cccc-3333", "name": "Room C"}

    # Connection 1: burn the whole budget, then disconnect.
    ws1 = ScriptedWS("testkey", [hello] + [{"type": "isc.ping"}] * 3)
    await isc_ws.isc_websocket_endpoint(ws1)
    assert "cccc-3333" not in isc._connections  # cleanly disconnected

    # Connection 2, same peer id: still over budget — the message is dropped.
    ws2 = ScriptedWS("testkey", [hello, {"type": "isc.state", "changes": {"var.x": 42}}])
    await isc_ws.isc_websocket_endpoint(ws2)
    assert state.get("isc.cccc-3333.var.x") is None
