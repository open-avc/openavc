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
