"""
OpenAVC Cloud — Tunnel handler for remote UI access.

Manages secondary WebSocket connections to the cloud for proxying
HTTP and WebSocket traffic between the cloud and local UI services.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI, InvalidHandshake

from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.cloud.agent import CloudAgent

log = get_logger(__name__)


@dataclass
class TunnelConnection:
    """State for a single active tunnel."""
    tunnel_id: str
    target_port: int
    data_ws: Any = None  # websockets.WebSocketClientProtocol
    local_ws_connections: dict[str, Any] = field(default_factory=dict)  # ws_id -> websockets conn
    # ws_id -> list of queued ws_frame / ws_close messages received from the
    # cloud while the local WS is still being opened.  Drained by
    # _handle_ws_open after the local connection is established (A48).
    pending_ws_opens: dict[str, list[dict]] = field(default_factory=dict)
    recv_task: asyncio.Task | None = None
    _forward_tasks: list[asyncio.Task] = field(default_factory=list)


class TunnelHandler:
    """Handles tunnel_open/tunnel_close messages and proxies traffic."""

    def __init__(self, agent: CloudAgent):
        self._agent = agent
        self._tunnels: dict[str, TunnelConnection] = {}
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def handle_tunnel_open(self, msg: dict[str, Any]) -> None:
        """Handle a tunnel_open message from the cloud."""
        from server import config

        payload = msg.get("payload", {})
        tunnel_id = payload.get("tunnel_id", "")
        tunnel_token = payload.get("tunnel_token", "")
        tunnel_data_url = payload.get("tunnel_data_url", "")

        # Per spec §13.12, target_port is the local port to proxy to. Honor
        # what the cloud requested so it can tunnel to plugin or alt-service
        # ports, not just the main HTTP port. Fall back to HTTP_PORT when
        # the field is missing (older cloud builds) or invalid.
        requested_port = payload.get("target_port")
        if isinstance(requested_port, int) and 1 <= requested_port <= 65535:
            target_port = requested_port
        else:
            if requested_port is not None:
                log.warning(
                    f"Tunnel open: invalid target_port {requested_port!r}, "
                    f"falling back to HTTP_PORT {config.HTTP_PORT}"
                )
            target_port = config.HTTP_PORT

        if not tunnel_id or not tunnel_data_url:
            log.error("Tunnel open: missing tunnel_id or tunnel_data_url")
            return

        if not tunnel_data_url.startswith(("wss://", "ws://")):
            log.error("Tunnel open: invalid tunnel_data_url scheme (expected wss:// or ws://)")
            return

        log.info(f"Tunnel open: {tunnel_id} → localhost:{target_port}")

        conn = TunnelConnection(tunnel_id=tunnel_id, target_port=target_port)

        try:
            # Connect secondary WebSocket to cloud
            from urllib.parse import urlencode
            ws_url = f"{tunnel_data_url}?{urlencode({'token': tunnel_token})}"
            conn.data_ws = await websockets.connect(
                ws_url,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=10,
                # 16 MB ceiling (A46). The previous 1 MB ceiling broke any
                # upload past ~750 KB after base64 inflation, killing driver
                # / plugin / asset / theme uploads through a remote tunnel.
                # 16 MB matches the typical FastAPI/Starlette default on the
                # cloud receive side and keeps memory bounded.
                max_size=2**24,
            )

            self._tunnels[tunnel_id] = conn

            # Start receive loop
            conn.recv_task = asyncio.create_task(self._data_receive_loop(conn))

            # Send tunnel_ready on main WS
            from server.cloud.protocol import TUNNEL_READY
            await self._agent.send_message(TUNNEL_READY, {"tunnel_id": tunnel_id})

            log.info(f"Tunnel {tunnel_id} ready")

        except Exception as e:
            # Catch-all: tunnel setup involves WS connect + protocol; any failure should be isolated
            log.exception(f"Failed to open tunnel {tunnel_id}")
            # Clean up partial state
            self._tunnels.pop(tunnel_id, None)
            # Notify cloud so it doesn't show the tunnel as active
            try:
                from server.cloud.protocol import TUNNEL_FAILED
                await self._agent.send_message(TUNNEL_FAILED, {
                    "tunnel_id": tunnel_id,
                    "reason": str(e),
                })
            except Exception:
                pass  # Best-effort notification

    async def handle_tunnel_close(self, msg: dict[str, Any]) -> None:
        """Handle a tunnel_close message from the cloud."""
        payload = msg.get("payload", {})
        tunnel_id = payload.get("tunnel_id", "")
        await self._close_tunnel(tunnel_id)

    async def _close_tunnel(self, tunnel_id: str) -> None:
        """Close a single tunnel and all its connections."""
        conn = self._tunnels.pop(tunnel_id, None)
        if not conn:
            return

        log.info(f"Closing tunnel {tunnel_id}")

        # Cancel receive task
        if conn.recv_task and not conn.recv_task.done():
            conn.recv_task.cancel()
            try:
                await conn.recv_task
            except asyncio.CancelledError:
                pass

        # Cancel forward tasks
        for task in conn._forward_tasks:
            if not task.done():
                task.cancel()

        # Close all local WS connections
        for ws_id, ws in list(conn.local_ws_connections.items()):
            try:
                await ws.close()
            except (ConnectionClosed, OSError):
                pass  # Best-effort close during tunnel teardown
        conn.local_ws_connections.clear()

        # Close secondary data WS
        if conn.data_ws:
            try:
                await conn.data_ws.close()
            except (ConnectionClosed, OSError):
                pass  # Best-effort close during tunnel teardown
            conn.data_ws = None

    async def stop(self) -> None:
        """Close all tunnels (agent shutdown)."""
        tunnel_ids = list(self._tunnels.keys())
        for tid in tunnel_ids:
            await self._close_tunnel(tid)
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _data_receive_loop(self, conn: TunnelConnection) -> None:
        """Receive messages from the secondary data WS and dispatch."""
        try:
            while conn.data_ws:
                raw = await conn.data_ws.recv()
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "http_request":
                    task = asyncio.create_task(self._handle_http_request(conn, msg))
                    conn._data_tasks = getattr(conn, '_data_tasks', set())
                    conn._data_tasks.add(task)
                    task.add_done_callback(conn._data_tasks.discard)
                elif msg_type == "ws_open":
                    # A48: reserve the ws_id BEFORE awaiting anything. Frames
                    # that race ahead of the local connect get queued behind
                    # this entry rather than being dropped.
                    ws_id = msg.get("id", "")
                    if ws_id:
                        conn.pending_ws_opens.setdefault(ws_id, [])
                    task = asyncio.create_task(self._handle_ws_open(conn, msg))
                    conn._data_tasks = getattr(conn, '_data_tasks', set())
                    conn._data_tasks.add(task)
                    task.add_done_callback(conn._data_tasks.discard)
                elif msg_type == "ws_frame":
                    ws_id = msg.get("id", "")
                    if ws_id in conn.pending_ws_opens:
                        # Local WS still opening — queue for drain (A48).
                        conn.pending_ws_opens[ws_id].append(msg)
                    else:
                        await self._handle_ws_frame(conn, msg)
                elif msg_type == "ws_close":
                    ws_id = msg.get("id", "")
                    if ws_id in conn.pending_ws_opens:
                        # Local WS still opening — queue the close behind any
                        # pending frames so they drain in order, then the close
                        # fires after.
                        conn.pending_ws_opens[ws_id].append(msg)
                    else:
                        await self._handle_ws_close(conn, msg)
                else:
                    log.warning(f"Tunnel {conn.tunnel_id}: unknown data msg type: {msg_type}")

        except ConnectionClosed:
            log.info(f"Tunnel {conn.tunnel_id}: data WS closed")
        except asyncio.CancelledError:
            return
        except Exception:
            # Catch-all: isolates unexpected data-channel errors from crashing the tunnel
            log.exception(f"Tunnel {conn.tunnel_id}: error in data receive loop")

    async def _handle_http_request(self, conn: TunnelConnection, msg: dict) -> None:
        """Proxy an HTTP request to the local service."""
        request_id = msg.get("id", "")
        method = msg.get("method", "GET")
        path = msg.get("path", "/")
        headers = msg.get("headers", {})
        body_b64 = msg.get("body", "")

        url = f"http://localhost:{conn.target_port}{path}"

        try:
            client = await self._get_http_client()
            body = base64.b64decode(body_b64) if body_b64 else None

            # Filter headers
            req_headers = {}
            skip = {"host", "connection", "upgrade", "transfer-encoding"}
            for k, v in headers.items():
                if k.lower() not in skip:
                    req_headers[k] = v

            max_response_size = 10 * 1024 * 1024  # 10MB
            response = await client.request(
                method=method,
                url=url,
                headers=req_headers,
                content=body,
            )

            # Reject oversized responses to prevent memory exhaustion
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_response_size:
                raise ValueError(f"Response too large: {content_length} bytes")
            if len(response.content) > max_response_size:
                raise ValueError(f"Response too large: {len(response.content)} bytes")

            # Build response message — rewrite Location headers so redirects
            # stay within the tunnel instead of pointing to localhost
            resp_headers = dict(response.headers)
            local_origin = f"http://localhost:{conn.target_port}"
            if "location" in resp_headers:
                loc = resp_headers["location"]
                if loc.startswith(local_origin):
                    # Strip the local origin, keep just the path
                    resp_headers["location"] = loc[len(local_origin):]
                elif loc.startswith("/"):
                    pass  # Already relative, fine
            resp_msg = {
                "type": "http_response",
                "id": request_id,
                "status": response.status_code,
                "headers": resp_headers,
                "body": base64.b64encode(response.content).decode(),
            }

        except (httpx.HTTPError, OSError, ValueError) as e:
            log.warning(f"Tunnel {conn.tunnel_id}: HTTP proxy error: {e}")
            resp_msg = {
                "type": "http_response",
                "id": request_id,
                "status": 502,
                "headers": {},
                "body": base64.b64encode(b"Bad Gateway").decode(),
            }

        if conn.data_ws:
            try:
                await conn.data_ws.send(json.dumps(resp_msg))
            except (ConnectionClosed, OSError):
                log.warning(f"Tunnel {conn.tunnel_id}: failed to send http_response")

    async def _handle_ws_open(self, conn: TunnelConnection, msg: dict) -> None:
        """Open a local WebSocket connection for proxying."""
        ws_id = msg.get("id", "")
        # `path` may include a query string (A47): preserve it intact so
        # endpoints that rely on `?token=…` style auth still authenticate.
        path = msg.get("path", "/")
        url = f"ws://localhost:{conn.target_port}{path}"

        # Subprotocols arrive as a structured list; pass them to the
        # `websockets` client so it can negotiate with the local server.
        subprotocols = msg.get("subprotocols") or None
        # Forward only application-level headers; drop hop-by-hop and any
        # WebSocket-handshake headers the client library generates itself.
        raw_headers = msg.get("headers") or {}
        drop = {
            "host", "connection", "upgrade", "sec-websocket-key",
            "sec-websocket-version", "sec-websocket-extensions",
            "sec-websocket-protocol", "content-length", "transfer-encoding",
        }
        additional_headers = [
            (k, v) for k, v in raw_headers.items() if k.lower() not in drop
        ]

        try:
            local_ws = await websockets.connect(
                url,
                ping_interval=None,
                ping_timeout=None,
                subprotocols=subprotocols,
                additional_headers=additional_headers or None,
            )
            conn.local_ws_connections[ws_id] = local_ws

            # Drain any frames / close that arrived during the connect (A48).
            # The receive loop keeps appending to this queue until we pop the
            # ws_id from pending_ws_opens, so we loop until truly empty —
            # late arrivals during draining are picked up here, preserving
            # FIFO order.
            queue = conn.pending_ws_opens.get(ws_id, [])
            while queue:
                queued_msg = queue.pop(0)
                qtype = queued_msg.get("type")
                if qtype == "ws_frame":
                    await self._handle_ws_frame(conn, queued_msg)
                elif qtype == "ws_close":
                    await self._handle_ws_close(conn, queued_msg)
                    # Close consumed; anything still queued behind it is stale.
                    queue.clear()
                    break
            conn.pending_ws_opens.pop(ws_id, None)

            # Start forwarding local → cloud
            task = asyncio.create_task(self._forward_local_ws(conn, ws_id, local_ws))
            conn._forward_tasks.append(task)

        except (ConnectionClosed, OSError, InvalidURI, InvalidHandshake) as e:
            log.warning(f"Tunnel {conn.tunnel_id}: WS open to {url} failed: {e}")
            # Drop any queued frames; the local socket never came up.
            conn.pending_ws_opens.pop(ws_id, None)
            # Send ws_close back to cloud
            if conn.data_ws:
                try:
                    await conn.data_ws.send(json.dumps({"type": "ws_close", "id": ws_id}))
                except (ConnectionClosed, OSError):
                    pass  # Best-effort notification to cloud

    async def _forward_local_ws(self, conn: TunnelConnection, ws_id: str, local_ws) -> None:
        """Forward frames from local WebSocket to cloud data WS."""
        try:
            async for message in local_ws:
                if isinstance(message, bytes):
                    data_b64 = base64.b64encode(message).decode()
                    is_binary = True
                else:
                    data_b64 = base64.b64encode(message.encode("utf-8")).decode()
                    is_binary = False

                frame = {
                    "type": "ws_frame",
                    "id": ws_id,
                    "data": data_b64,
                    "binary": is_binary,
                }
                if conn.data_ws:
                    await conn.data_ws.send(json.dumps(frame))
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            return
        except (OSError, ValueError):
            log.exception(f"Tunnel {conn.tunnel_id}: forward local WS error")
        finally:
            # Clean up and notify cloud
            conn.local_ws_connections.pop(ws_id, None)
            if conn.data_ws:
                try:
                    await conn.data_ws.send(json.dumps({"type": "ws_close", "id": ws_id}))
                except (ConnectionClosed, OSError):
                    pass  # Best-effort notification to cloud

    async def _handle_ws_frame(self, conn: TunnelConnection, msg: dict) -> None:
        """Forward a WebSocket frame from cloud to local."""
        ws_id = msg.get("id", "")
        local_ws = conn.local_ws_connections.get(ws_id)
        if not local_ws:
            return

        data = base64.b64decode(msg.get("data", ""))
        try:
            if msg.get("binary"):
                await local_ws.send(data)
            else:
                await local_ws.send(data.decode("utf-8", errors="replace"))
        except (ConnectionClosed, OSError):
            log.warning(f"Tunnel {conn.tunnel_id}: failed to forward ws_frame to local")

    async def _handle_ws_close(self, conn: TunnelConnection, msg: dict) -> None:
        """Close a local WebSocket connection."""
        ws_id = msg.get("id", "")
        local_ws = conn.local_ws_connections.pop(ws_id, None)
        if local_ws:
            try:
                await local_ws.close()
            except (ConnectionClosed, OSError):
                pass  # Best-effort close
