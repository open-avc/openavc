"""Tests for the agent-side TunnelHandler."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return agent


@pytest.fixture
def tunnel_handler(mock_agent):
    from server.cloud.tunnel import TunnelHandler
    return TunnelHandler(mock_agent)


@pytest.mark.asyncio
async def test_handle_tunnel_open_sends_ready(tunnel_handler, mock_agent):
    """tunnel_open should connect secondary WS and send tunnel_ready."""
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_ws.close = AsyncMock()

    msg = {
        "type": "tunnel_open",
        "payload": {
            "tunnel_id": "t-123",
            "target_port": 8080,
            "tunnel_token": "tok-abc",
            "tunnel_data_url": "ws://localhost:9999/tunnel-data/t-123",
        },
    }

    with patch("server.cloud.tunnel.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
        await tunnel_handler.handle_tunnel_open(msg)

    # Should have sent tunnel_ready
    mock_agent.send_message.assert_called_once()
    call_args = mock_agent.send_message.call_args
    assert call_args[0][0] == "tunnel_ready"
    assert call_args[0][1]["tunnel_id"] == "t-123"

    # Tunnel should be tracked
    assert "t-123" in tunnel_handler._tunnels

    # Cleanup
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_handle_tunnel_close(tunnel_handler, mock_agent):
    """tunnel_close should clean up the tunnel."""
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_ws.close = AsyncMock()

    msg_open = {
        "type": "tunnel_open",
        "payload": {
            "tunnel_id": "t-456",
            "target_port": 8080,
            "tunnel_token": "tok-xyz",
            "tunnel_data_url": "ws://localhost:9999/tunnel-data/t-456",
        },
    }

    with patch("server.cloud.tunnel.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
        await tunnel_handler.handle_tunnel_open(msg_open)

    assert "t-456" in tunnel_handler._tunnels

    msg_close = {
        "type": "tunnel_close",
        "payload": {"tunnel_id": "t-456"},
    }
    await tunnel_handler.handle_tunnel_close(msg_close)

    assert "t-456" not in tunnel_handler._tunnels
    mock_ws.close.assert_called()


@pytest.mark.asyncio
async def test_handle_tunnel_open_missing_fields(tunnel_handler, mock_agent):
    """tunnel_open with missing fields should not crash."""
    msg = {
        "type": "tunnel_open",
        "payload": {},
    }
    await tunnel_handler.handle_tunnel_open(msg)
    assert len(tunnel_handler._tunnels) == 0
    mock_agent.send_message.assert_not_called()


# ===========================================================================
# A20 — Agent must honor target_port from tunnel_open instead of hardcoding
# its own HTTP_PORT. Spec §13.12 line 1859: target_port: 8080 in the payload.
# Without this, plugin / alt-service tunneling is impossible.
# ===========================================================================


@pytest.mark.asyncio
async def test_handle_tunnel_open_honors_target_port(tunnel_handler, mock_agent):
    """Agent should record the cloud-requested target_port on the TunnelConnection."""
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_ws.close = AsyncMock()

    msg = {
        "type": "tunnel_open",
        "payload": {
            "tunnel_id": "t-port",
            "target_port": 9090,  # NOT the default HTTP_PORT
            "tunnel_token": "tok",
            "tunnel_data_url": "ws://localhost:9999/tunnel-data/t-port",
        },
    }
    with patch("server.cloud.tunnel.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
        await tunnel_handler.handle_tunnel_open(msg)

    assert tunnel_handler._tunnels["t-port"].target_port == 9090
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_handle_tunnel_open_falls_back_to_http_port(tunnel_handler, mock_agent):
    """target_port missing from payload should fall back to config.HTTP_PORT."""
    from server import config

    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_ws.close = AsyncMock()

    msg = {
        "type": "tunnel_open",
        "payload": {
            "tunnel_id": "t-default",
            # target_port omitted on purpose
            "tunnel_token": "tok",
            "tunnel_data_url": "ws://localhost:9999/tunnel-data/t-default",
        },
    }
    with patch("server.cloud.tunnel.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
        await tunnel_handler.handle_tunnel_open(msg)

    assert tunnel_handler._tunnels["t-default"].target_port == config.HTTP_PORT
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_handle_tunnel_open_invalid_port_falls_back(tunnel_handler, mock_agent):
    """Invalid target_port (out of range or wrong type) should fall back."""
    from server import config

    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_ws.close = AsyncMock()

    for bad_port in (0, -1, 70000, "8080", None):
        tid = f"t-bad-{bad_port}"
        msg = {
            "type": "tunnel_open",
            "payload": {
                "tunnel_id": tid,
                "target_port": bad_port,
                "tunnel_token": "tok",
                "tunnel_data_url": f"ws://localhost:9999/tunnel-data/{tid}",
            },
        }
        with patch("server.cloud.tunnel.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await tunnel_handler.handle_tunnel_open(msg)
        assert tunnel_handler._tunnels[tid].target_port == config.HTTP_PORT, (
            f"target_port={bad_port!r} should have fallen back to HTTP_PORT"
        )

    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_stop_closes_all_tunnels(tunnel_handler, mock_agent):
    """stop() should close all active tunnels."""
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_ws.close = AsyncMock()

    for tid in ["t-a", "t-b"]:
        msg = {
            "type": "tunnel_open",
            "payload": {
                "tunnel_id": tid,
                "target_port": 8080,
                "tunnel_token": f"tok-{tid}",
                "tunnel_data_url": f"ws://localhost:9999/tunnel-data/{tid}",
            },
        }
        with patch("server.cloud.tunnel.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await tunnel_handler.handle_tunnel_open(msg)

    assert len(tunnel_handler._tunnels) == 2

    await tunnel_handler.stop()
    assert len(tunnel_handler._tunnels) == 0


@pytest.mark.asyncio
async def test_http_request_proxied(tunnel_handler, mock_agent):
    """HTTP requests should be proxied to localhost and response sent back."""
    from server.cloud.tunnel import TunnelConnection

    # Create a tunnel connection manually
    mock_data_ws = AsyncMock()
    mock_data_ws.send = AsyncMock()
    mock_data_ws.close = AsyncMock()

    conn = TunnelConnection(tunnel_id="t-http", target_port=8080, data_ws=mock_data_ws)
    tunnel_handler._tunnels["t-http"] = conn

    # Mock httpx response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.content = b"<html>Hello</html>"

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_response)
    tunnel_handler._http_client = mock_client

    msg = {
        "type": "http_request",
        "id": "req-1",
        "method": "GET",
        "path": "/panel",
        "headers": {},
        "body": "",
    }

    await tunnel_handler._handle_http_request(conn, msg)

    # Should have sent http_response
    mock_data_ws.send.assert_called_once()
    sent = json.loads(mock_data_ws.send.call_args[0][0])
    assert sent["type"] == "http_response"
    assert sent["id"] == "req-1"
    assert sent["status"] == 200
    assert base64.b64decode(sent["body"]) == b"<html>Hello</html>"

    await tunnel_handler.stop()


# ===========================================================================
# A45 — HTTP query strings must survive the tunnel hop. The cloud now bakes
# the query into `path` and the agent passes the path through to httpx, which
# preserves it when building the local URL.
# ===========================================================================


@pytest.mark.asyncio
async def test_http_request_preserves_query_string(tunnel_handler, mock_agent):
    """Query string baked into `path` should be passed to the local server."""
    from server.cloud.tunnel import TunnelConnection

    mock_data_ws = AsyncMock()
    mock_data_ws.send = AsyncMock()

    conn = TunnelConnection(tunnel_id="t-q", target_port=8080, data_ws=mock_data_ws)
    tunnel_handler._tunnels["t-q"] = conn

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.content = b"ok"

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_response)
    tunnel_handler._http_client = mock_client

    msg = {
        "type": "http_request",
        "id": "req-q",
        "method": "GET",
        "path": "/api/scripts?enabled=true&owner=aaron",
        "headers": {},
        "body": "",
    }
    await tunnel_handler._handle_http_request(conn, msg)

    # The httpx call should have included the query string in the URL.
    call_kwargs = mock_client.request.call_args.kwargs
    url = call_kwargs["url"]
    assert "?enabled=true&owner=aaron" in url
    assert url == "http://localhost:8080/api/scripts?enabled=true&owner=aaron"

    await tunnel_handler.stop()


# ===========================================================================
# A47 — WebSocket subprotocols and filtered headers must flow through ws_open
# to the agent's localhost connect. Hop-by-hop headers stay stripped.
# ===========================================================================


@pytest.mark.asyncio
async def test_ws_open_passes_subprotocols_and_headers(tunnel_handler, mock_agent):
    """ws_open with subprotocols + custom headers should reach websockets.connect."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch, AsyncMock as _AsyncMock

    mock_data_ws = AsyncMock()
    mock_data_ws.send = AsyncMock()

    conn = TunnelConnection(tunnel_id="t-ws", target_port=8080, data_ws=mock_data_ws)
    tunnel_handler._tunnels["t-ws"] = conn

    mock_local_ws = AsyncMock()
    mock_local_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_local_ws.close = AsyncMock()
    # async iterator over zero frames so _forward_local_ws exits cleanly
    mock_local_ws.__aiter__ = lambda self: self
    mock_local_ws.__anext__ = _AsyncMock(side_effect=StopAsyncIteration)

    msg = {
        "type": "ws_open",
        "id": "ws-1",
        "path": "/programmer/ws?session=abc",
        "headers": {
            "x-app-key": "value",
            "host": "tunnel.example.com",
            "connection": "Upgrade",
            "upgrade": "websocket",
            "sec-websocket-key": "abc",
            "sec-websocket-version": "13",
            "sec-websocket-protocol": "openavc.v1",
        },
        "subprotocols": ["openavc.v1", "openavc.v2"],
    }

    with patch("server.cloud.tunnel.websockets.connect", new=_AsyncMock(return_value=mock_local_ws)) as p:
        await tunnel_handler._handle_ws_open(conn, msg)
        # Brief yield so the forward task touches __aiter__ before we stop()
        await asyncio.sleep(0)

        call_kwargs = p.call_args.kwargs
        # Subprotocols flow through as a structured list
        assert call_kwargs["subprotocols"] == ["openavc.v1", "openavc.v2"]
        # Query string preserved in the connect URL
        assert "?session=abc" in p.call_args.args[0]
        # additional_headers includes app headers, strips hop-by-hop + WS handshake
        hdr_pairs = call_kwargs["additional_headers"]
        hdr_keys = {k.lower() for k, _ in hdr_pairs}
        assert "x-app-key" in hdr_keys
        for forbidden in (
            "host", "connection", "upgrade",
            "sec-websocket-key", "sec-websocket-version",
            "sec-websocket-protocol",
        ):
            assert forbidden not in hdr_keys, f"{forbidden} should have been stripped"

    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_ws_open_no_subprotocols_no_headers(tunnel_handler, mock_agent):
    """ws_open without subprotocols passes None to websockets.connect (default behavior)."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch, AsyncMock as _AsyncMock

    mock_data_ws = AsyncMock()
    conn = TunnelConnection(tunnel_id="t-bare", target_port=8080, data_ws=mock_data_ws)
    tunnel_handler._tunnels["t-bare"] = conn

    mock_local_ws = AsyncMock()
    mock_local_ws.__aiter__ = lambda self: self
    mock_local_ws.__anext__ = _AsyncMock(side_effect=StopAsyncIteration)

    msg = {
        "type": "ws_open",
        "id": "ws-bare",
        "path": "/programmer/ws",
        "headers": {},
    }
    with patch("server.cloud.tunnel.websockets.connect", new=_AsyncMock(return_value=mock_local_ws)) as p:
        await tunnel_handler._handle_ws_open(conn, msg)
        call_kwargs = p.call_args.kwargs
        assert call_kwargs["subprotocols"] is None
        assert call_kwargs["additional_headers"] is None

    await tunnel_handler.stop()


# ===========================================================================
# A48 — Frames received during the local-WS open are queued in
# `pending_ws_opens` and drained after the connect completes, in FIFO order.
# Previously the receive loop dropped them silently.
# ===========================================================================


@pytest.mark.asyncio
async def test_ws_frames_queued_during_open_are_drained_in_order(tunnel_handler, mock_agent):
    """Frames seeded before _handle_ws_open runs must be delivered in FIFO order."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch, AsyncMock as _AsyncMock

    conn = TunnelConnection(tunnel_id="t-race", target_port=8080, data_ws=AsyncMock())
    tunnel_handler._tunnels["t-race"] = conn

    # Simulate the receive loop having already reserved the slot and queued
    # two frames before the open completes.
    conn.pending_ws_opens["ws-1"] = [
        {"type": "ws_frame", "id": "ws-1",
         "data": base64.b64encode(b"first").decode(), "binary": False},
        {"type": "ws_frame", "id": "ws-1",
         "data": base64.b64encode(b"second").decode(), "binary": False},
    ]

    sent_to_local: list = []

    class _StubLocal:
        async def send(self, data):
            sent_to_local.append(data)

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    with patch(
        "server.cloud.tunnel.websockets.connect",
        new=_AsyncMock(return_value=_StubLocal()),
    ):
        await tunnel_handler._handle_ws_open(conn, {
            "type": "ws_open",
            "id": "ws-1",
            "path": "/ws",
            "headers": {},
        })
        # Give the spawned forward task a turn so it exits cleanly
        await asyncio.sleep(0)

    assert sent_to_local == ["first", "second"]
    assert "ws-1" not in conn.pending_ws_opens

    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_ws_close_queued_during_open_consumed_after_drain(tunnel_handler, mock_agent):
    """A ws_close queued behind frames closes the local WS and stops further drain."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch, AsyncMock as _AsyncMock

    conn = TunnelConnection(tunnel_id="t-close", target_port=8080, data_ws=AsyncMock())
    tunnel_handler._tunnels["t-close"] = conn

    conn.pending_ws_opens["ws-c"] = [
        {"type": "ws_frame", "id": "ws-c",
         "data": base64.b64encode(b"hello").decode(), "binary": False},
        {"type": "ws_close", "id": "ws-c"},
        # Anything queued behind a close is stale — should be skipped.
        {"type": "ws_frame", "id": "ws-c",
         "data": base64.b64encode(b"stale").decode(), "binary": False},
    ]

    sent: list = []
    closed = False

    class _StubLocal:
        async def send(self, data):
            sent.append(data)

        async def close(self):
            nonlocal closed
            closed = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    with patch(
        "server.cloud.tunnel.websockets.connect",
        new=_AsyncMock(return_value=_StubLocal()),
    ):
        await tunnel_handler._handle_ws_open(conn, {
            "type": "ws_open",
            "id": "ws-c",
            "path": "/ws",
            "headers": {},
        })

    # First frame delivered, close consumed, stale frame dropped.
    assert sent == ["hello"]
    assert closed is True
    assert "ws-c" not in conn.pending_ws_opens

    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_ws_open_failure_drops_queued_frames(tunnel_handler, mock_agent):
    """If the local connect fails, queued frames are discarded and the cloud
    is notified with ws_close."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch, AsyncMock as _AsyncMock

    mock_data_ws = AsyncMock()
    mock_data_ws.send = AsyncMock()
    conn = TunnelConnection(tunnel_id="t-fail", target_port=8080, data_ws=mock_data_ws)
    tunnel_handler._tunnels["t-fail"] = conn

    conn.pending_ws_opens["ws-bad"] = [
        {"type": "ws_frame", "id": "ws-bad",
         "data": base64.b64encode(b"never").decode(), "binary": False},
    ]

    with patch(
        "server.cloud.tunnel.websockets.connect",
        new=_AsyncMock(side_effect=OSError("connection refused")),
    ):
        await tunnel_handler._handle_ws_open(conn, {
            "type": "ws_open",
            "id": "ws-bad",
            "path": "/ws",
            "headers": {},
        })

    assert "ws-bad" not in conn.pending_ws_opens
    # Cloud was told the WS is closed
    mock_data_ws.send.assert_called_once()
    sent = json.loads(mock_data_ws.send.call_args[0][0])
    assert sent == {"type": "ws_close", "id": "ws-bad"}

    await tunnel_handler.stop()


# ===========================================================================
# Tunnel close must cancel in-flight data-message handlers, and a ws_open
# that outlives the close must not re-populate the dead tunnel. Forward
# tasks self-prune so a long-lived tunnel doesn't grow one dead Task per
# WS connection.
# ===========================================================================


class _StubLocalWS:
    """Local WS stub: records close, yields zero frames to the forwarder."""

    def __init__(self):
        self.closed = False

    async def send(self, data):
        pass

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _ScriptedDataWS:
    """Data WS stub: hands out scripted messages, then blocks until cancelled."""

    def __init__(self, messages):
        self._messages = list(messages)

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.Event().wait()

    async def send(self, data):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_close_tunnel_cancels_inflight_ws_open(tunnel_handler, mock_agent):
    """Closing a tunnel while a ws_open handler is still awaiting its local
    connect must cancel that handler — it must not complete later and
    register a socket + forwarder on the closed tunnel."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch

    conn = TunnelConnection(
        tunnel_id="t-race-close", target_port=8080,
        data_ws=_ScriptedDataWS([json.dumps({
            "type": "ws_open", "id": "ws-1", "path": "/ws", "headers": {},
        })]),
    )
    tunnel_handler._tunnels["t-race-close"] = conn

    gate = asyncio.Event()
    local_ws = _StubLocalWS()

    async def slow_connect(*args, **kwargs):
        await gate.wait()
        return local_ws

    with patch("server.cloud.tunnel.websockets.connect", new=slow_connect):
        conn.recv_task = asyncio.create_task(
            tunnel_handler._data_receive_loop(conn)
        )
        # Let the receive loop spawn the ws_open handler, which parks at
        # the gated connect.
        for _ in range(3):
            await asyncio.sleep(0)
        assert len(conn._data_tasks) == 1
        pending = next(iter(conn._data_tasks))

        await tunnel_handler._close_tunnel("t-race-close")
        assert pending.cancelled()

        # Even if the connect had been about to resolve, nothing may
        # re-populate the closed tunnel.
        gate.set()
        for _ in range(3):
            await asyncio.sleep(0)

    assert conn.local_ws_connections == {}
    assert not conn._forward_tasks


@pytest.mark.asyncio
async def test_ws_open_completing_after_close_does_not_register(tunnel_handler, mock_agent):
    """A ws_open handler whose connect resolves after the tunnel was removed
    closes the fresh socket and registers nothing (liveness re-check)."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch

    conn = TunnelConnection(tunnel_id="t-late", target_port=8080, data_ws=AsyncMock())
    tunnel_handler._tunnels["t-late"] = conn

    gate = asyncio.Event()
    local_ws = _StubLocalWS()

    async def slow_connect(*args, **kwargs):
        await gate.wait()
        return local_ws

    with patch("server.cloud.tunnel.websockets.connect", new=slow_connect):
        conn.pending_ws_opens["ws-9"] = []
        handler_task = asyncio.create_task(tunnel_handler._handle_ws_open(conn, {
            "type": "ws_open", "id": "ws-9", "path": "/ws", "headers": {},
        }))
        await asyncio.sleep(0)

        # Tunnel goes away while the handler is parked at the connect.
        tunnel_handler._tunnels.pop("t-late")
        gate.set()
        await handler_task

    assert local_ws.closed
    assert conn.local_ws_connections == {}
    assert not conn._forward_tasks
    assert "ws-9" not in conn.pending_ws_opens


@pytest.mark.asyncio
async def test_forward_tasks_self_prune(tunnel_handler, mock_agent):
    """Finished forwarders drop out of _forward_tasks instead of accumulating
    for the tunnel's lifetime."""
    from server.cloud.tunnel import TunnelConnection
    from unittest.mock import patch, AsyncMock as _AsyncMock

    conn = TunnelConnection(tunnel_id="t-churn", target_port=8080, data_ws=AsyncMock())
    tunnel_handler._tunnels["t-churn"] = conn

    with patch(
        "server.cloud.tunnel.websockets.connect",
        new=_AsyncMock(side_effect=lambda *a, **k: _StubLocalWS()),
    ):
        for i in range(3):
            await tunnel_handler._handle_ws_open(conn, {
                "type": "ws_open", "id": f"ws-{i}", "path": "/ws", "headers": {},
            })
    # Let the (immediately-exhausted) forwarders finish.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(conn._forward_tasks) == 0

    await tunnel_handler.stop()
