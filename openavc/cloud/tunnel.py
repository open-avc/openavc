"""
OpenAVC Cloud — Tunnel handler for remote UI access.

Manages secondary WebSocket connections to the cloud for proxying
HTTP and WebSocket traffic between the cloud and local UI services.

THE end of a tunnel on this side. A tunnel ends here when the cloud closes it
(tunnel_close), when its data socket closes or fails, when this system's
connection to the cloud drops, and when the agent stops. Each ending discards
the tunnel's session secret before anything that can wait, so a secret never
outlives the tunnel it was minted for. An ending the cloud did not ask for is
told to it (tunnel_closed): at once when the connection is up, and once it is
connected again when it is not. The cloud's own close is not answered.
"""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI, InvalidHandshake

from openavc.cloud.protocol import (
    HTTP_CANCEL, HTTP_REQUEST, TUNNEL_CLOSE, TUNNEL_CLOSED, TUNNEL_CONNECTION_LOST,
    TUNNEL_DATA_MAX_BYTES, TUNNEL_DATA_SOCKET_CLOSED, TUNNEL_OPEN,
    TUNNEL_STREAM_IDLE_SECONDS, WS_CLOSE, WS_CLOSE_LOCAL_UNREACHABLE, WS_FRAME, WS_OPEN,
    build_http_body, build_http_body_end, build_http_response, build_http_response_start,
    build_tunnel_closed_payload, build_ws_close, build_ws_frame, close_reason, read_as,
    read_tunnel_data, sendable_close_code, ws_close_code,
)
from openavc.utils.logger import get_logger
from openavc.utils.outbound import internet_tls_kwargs
from openavc.utils.request_origin import CLOUD_SESSION_HEADER, TUNNEL_HEADER

if TYPE_CHECKING:
    from openavc.cloud.agent import CloudAgent

log = get_logger(__name__)

# Forwarded headers describe the CLOUD's front door, and they must not survive
# the last hop into the local server.
#
# Every listener runs uvicorn's proxy_headers with forwarded_allow_ips pinned to
# loopback (UVICORN_FORWARDED_ALLOW_IPS in openavc/utils/request_origin.py), and
# this proxy connects to localhost -- so uvicorn trusts whatever arrives and
# rewrites request.client.host to the address in X-Forwarded-For. That is the
# public IP of whoever called the cloud. Every loopback check on a tunnelled
# request was therefore being evaluated against a value the caller supplied:
# `is_tunneled_request` came back False on real tunnel traffic, so tunnelled
# requests were never put in the tunnel's shared rate-limit bucket, and the
# marker that is supposed to be believed "only from a loopback peer" was in
# practice believed from nowhere at all.
#
# Nothing was exploitable, because both users of the peer check fail closed --
# console trust is refused, and the rate limiter just meters the caller as an
# ordinary remote IP. But it meant the mechanism documented in
# openavc/utils/request_origin.py was not the one running, and the support
# session (which GRANTS on a loopback peer) could never authenticate.
#
# Stripping them makes the peer true again. The cloud's own logs still hold the
# caller's address; the local server has no use for it and every reason not to
# be told it by the caller.
FORWARDED_HEADERS = frozenset({
    "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
    "x-forwarded-port", "x-forwarded-server", "forwarded", "x-real-ip",
})

# The largest response sent to the cloud as one message. The buffered path holds
# the whole body in memory, and one message of it must fit the cloud's socket
# after base64. Anything larger goes up in pieces when the cloud can take them,
# and is refused only for a cloud that cannot.
MAX_BUFFERED_RESPONSE = 10 * 1024 * 1024


@dataclass
class TunnelConnection:
    """State for a single active tunnel."""
    tunnel_id: str
    target_port: int
    # Set when the cloud authorized this tunnel to act as a signed-in
    # Programmer client -- a support grant, or an owner who turned on
    # password-free remote programming. The value is the secret minted by
    # openavc/api/cloud_session.py, stamped on everything proxied here so the
    # Programmer accepts the session without the instance's password. Empty on
    # every ordinary tunnel.
    session_secret: str = ""
    # True when the cloud said it can receive a response in pieces. Sent on
    # tunnel_open; absent on an older cloud, which is why it defaults off --
    # streaming at a cloud that doesn't understand it would be answered by
    # nothing at all.
    stream_responses: bool = False
    data_ws: Any = None  # websockets.WebSocketClientProtocol
    local_ws_connections: dict[str, Any] = field(default_factory=dict)  # ws_id -> websockets conn
    # ws_id -> list of queued ws_frame / ws_close messages received from the
    # cloud while the local WS is still being opened.  Drained by
    # _handle_ws_open after the local connection is established (A48).
    pending_ws_opens: dict[str, list[dict]] = field(default_factory=dict)
    recv_task: asyncio.Task | None = None
    # Both task sets self-prune via add_done_callback(discard) so a long-lived
    # tunnel doesn't accumulate one dead Task per connection/request.
    _forward_tasks: set[asyncio.Task] = field(default_factory=set)
    _data_tasks: set[asyncio.Task] = field(default_factory=set)
    # request_id -> the task proxying it, so an http_cancel can stop reading a
    # response the cloud is no longer delivering. Same self-pruning.
    http_tasks: dict[str, asyncio.Task] = field(default_factory=dict)


async def _cancel_and_wait(tasks) -> None:
    """Cancel each unfinished task and wait for every one to finish."""
    pending = [t for t in list(tasks) if t and not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _close_quietly(ws: Any) -> None:
    """Close a socket during tunnel teardown, best effort."""
    try:
        await ws.close()
    except (ConnectionClosed, OSError):
        pass


class TunnelHandler:
    """Handles tunnel_open/tunnel_close messages, proxies traffic, and ends
    each tunnel (the module docstring has when)."""

    def __init__(self, agent: CloudAgent):
        self._agent = agent
        self._tunnels: dict[str, TunnelConnection] = {}
        self._http_client: httpx.AsyncClient | None = None
        # tunnel_id -> why: tunnels ended here while the cloud could not be
        # told, told on the next connection (start).
        self._untold: dict[str, str] = {}

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            # verify=False is the same trust model as ISC peer connections:
            # the tunnel only ever connects to localhost, and the only TLS
            # cert in play is the self-signed one we generated ourselves.
            # The flag is moot for http:// URLs (plain HTTP, no handshake).
            #
            # `read` is the gap between two pieces of a response, not the time
            # the response is allowed to take. At 30s it capped how long a
            # local endpoint could hold a connection open producing output --
            # which is every server-sent event stream, the AI assistant most
            # of all. What bounds a stalled response now is the cloud: it
            # gives up on silence and sends http_cancel, and nothing reaches
            # here at all until the local server has answered with headers.
            # This computer's own server: never through a proxy.
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, read=TUNNEL_STREAM_IDLE_SECONDS),
                verify=False, trust_env=False,
            )
        return self._http_client

    @staticmethod
    def _resolve_target_port(requested: Any) -> int:
        """Decide which local port a tunnel proxies to.

        The cloud names a port in tunnel_open, but the port an instance
        listens on is a fact only the instance holds -- nothing reports it
        upstream, so the cloud is sending a default. When that default is
        wrong, honoring it points this instance's tunnel at whatever else
        answers on that port: a second instance, or some unrelated service,
        published to the internet under the customer's tunnel URL and, in the
        panel scope, without a credential. So a requested port is honored
        only when it is a port this instance actually serves; anything else
        becomes the main HTTP port, which is what the tunnel is for.

        Asking for TLS_PORT means the main app too -- it resolves to
        HTTP_PORT so the one TLS switch in _loopback_origin decides the
        scheme, rather than a second place deciding it differently.
        """
        from openavc import config
        if requested is None:
            return config.HTTP_PORT
        valid = (
            isinstance(requested, int)
            and not isinstance(requested, bool)
            and 1 <= requested <= 65535
        )
        if not valid:
            log.warning(
                f"Tunnel open: invalid target_port {requested!r}, "
                f"using HTTP_PORT {config.HTTP_PORT}"
            )
            return config.HTTP_PORT
        if requested == config.HTTP_PORT:
            return requested
        if config.TLS_ENABLED and requested == config.TLS_PORT:
            return config.HTTP_PORT
        log.warning(
            f"Tunnel open: port {requested} is not served by this instance, "
            f"using HTTP_PORT {config.HTTP_PORT}"
        )
        return config.HTTP_PORT

    @staticmethod
    def _loopback_origin(target_port: int) -> str:
        """Return the origin (scheme://host:port) for tunneled HTTP requests.

        When TLS is enabled and the cloud is targeting the main HTTP port,
        the main app has moved to TLS_PORT — switch to https. Plugin and
        alt-service ports are untouched (they stay on http).
        """
        from openavc import config
        if config.TLS_ENABLED and target_port == config.HTTP_PORT:
            return f"https://localhost:{config.TLS_PORT}"
        return f"http://localhost:{target_port}"

    @staticmethod
    def _loopback_ws_url(target_port: int, path: str) -> tuple[str, ssl.SSLContext | None]:
        """Return (url, ssl_ctx) for tunneled WebSocket requests.

        ssl_ctx is None for ws://; for wss:// it's an unverified context (same
        trust model as the HTTP path: self-signed loopback).
        """
        from openavc import config
        if config.TLS_ENABLED and target_port == config.HTTP_PORT:
            return (
                f"wss://localhost:{config.TLS_PORT}{path}",
                ssl._create_unverified_context(),
            )
        return f"ws://localhost:{target_port}{path}", None

    async def handle_tunnel_open(self, msg: dict[str, Any]) -> None:
        """Handle a tunnel_open message from the cloud."""
        payload = read_as(TUNNEL_OPEN, msg)
        tunnel_id = payload["tunnel_id"]
        tunnel_token = payload["tunnel_token"]
        tunnel_data_url = payload["tunnel_data_url"]

        target_port = self._resolve_target_port(payload["target_port"])

        if not tunnel_id or not tunnel_data_url:
            log.error("Tunnel open: missing tunnel_id or tunnel_data_url")
            return

        if not tunnel_data_url.startswith(("wss://", "ws://")):
            log.error("Tunnel open: invalid tunnel_data_url scheme (expected wss:// or ws://)")
            return

        log.info(f"Tunnel open: {tunnel_id} → localhost:{target_port}")

        conn = TunnelConnection(
            tunnel_id=tunnel_id,
            target_port=target_port,
            stream_responses=bool(payload["stream_responses"]),
        )

        # A tunnel the cloud authorized to act as a signed-in Programmer
        # client. Neither caller holds this instance's password -- OpenAVC
        # staff never have it, and an owner should not have to type a per-room
        # one -- so without this the session reaches the sign-in screen and
        # stops. The secret lives only in this process and dies with the
        # tunnel. The reason only decides what gets logged.
        authorized = payload["authorized_session"]
        if isinstance(authorized, str) and authorized:
            from openavc.api import cloud_session
            conn.session_secret = cloud_session.open_session(tunnel_id, authorized)

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
                max_size=TUNNEL_DATA_MAX_BYTES,
                **internet_tls_kwargs(ws_url),
            )

            self._tunnels[tunnel_id] = conn

            # Start receive loop
            conn.recv_task = asyncio.create_task(self._data_receive_loop(conn))

            # Send tunnel_ready on main WS
            from openavc.cloud.protocol import TUNNEL_READY, build_tunnel_ready_payload
            await self._agent.send_message(
                TUNNEL_READY, build_tunnel_ready_payload(tunnel_id)
            )

            log.info(f"Tunnel {tunnel_id} ready")

        except Exception as e:
            # Catch-all: tunnel setup involves WS connect + protocol; any failure should be isolated
            log.exception(f"Failed to open tunnel {tunnel_id}")
            # Clean up partial state
            self._tunnels.pop(tunnel_id, None)
            if conn.session_secret:
                from openavc.api import cloud_session
                cloud_session.close_session(tunnel_id)
            # Notify cloud so it doesn't show the tunnel as active
            try:
                from openavc.cloud.protocol import TUNNEL_FAILED, build_tunnel_failed_payload
                await self._agent.send_message(
                    TUNNEL_FAILED, build_tunnel_failed_payload(tunnel_id, str(e))
                )
            except Exception:
                pass  # Best-effort notification

    async def handle_tunnel_close(self, msg: dict[str, Any]) -> None:
        """Handle a tunnel_close message from the cloud."""
        await self._close_tunnel(read_as(TUNNEL_CLOSE, msg)["tunnel_id"])

    def _detach(self, tunnel_id: str) -> TunnelConnection | None:
        """Take a tunnel out of routing and discard its secret. Waits for nothing.

        The secret goes before anything else. This is what makes a withdrawn
        authorization stop working here as well as at the cloud, and it must
        not depend on the teardown finishing cleanly, or on a dead network's
        socket close taking its time.
        """
        conn = self._tunnels.pop(tunnel_id, None)
        if conn and conn.session_secret:
            from openavc.api import cloud_session
            cloud_session.close_session(tunnel_id)
            conn.session_secret = ""
        return conn

    async def _close_tunnel(self, tunnel_id: str) -> bool:
        """Close a single tunnel and all its connections. Whether this call closed it."""
        conn = self._detach(tunnel_id)
        if not conn:
            return False
        log.info(f"Closing tunnel {tunnel_id}")
        await self._teardown(conn)
        return True

    async def _end_here(self, tunnel_id: str, reason: str) -> None:
        """End a tunnel this side can no longer carry, and tell the cloud."""
        if await self._close_tunnel(tunnel_id):
            await self._tell_closed(tunnel_id, reason)

    async def _tell_closed(self, tunnel_id: str, reason: str) -> None:
        """Tell the cloud this system ended a tunnel; kept for the next connection when it cannot hear."""
        try:
            sent = await self._agent.send_message(
                TUNNEL_CLOSED, build_tunnel_closed_payload(tunnel_id, reason)
            )
        except Exception:
            # Catch-all: the send is best effort, and the tunnel is already ended.
            log.debug(f"Tunnel {tunnel_id}: could not tell the cloud it ended", exc_info=True)
            sent = False
        if not sent:
            self._untold[tunnel_id] = reason

    async def _end_every_tunnel(self) -> list[str]:
        """End every tunnel, every secret first. The tunnels this call ended."""
        conns = [c for c in (self._detach(tid) for tid in list(self._tunnels)) if c]
        for conn in conns:
            log.info(f"Closing tunnel {conn.tunnel_id}")
            await self._teardown(conn)
        return [c.tunnel_id for c in conns]

    async def start(self) -> None:
        """The connection to the cloud is up: tell it about the tunnels that
        ended while it could not hear."""
        untold, self._untold = self._untold, {}
        for tunnel_id, reason in untold.items():
            await self._tell_closed(tunnel_id, reason)

    async def stop(self) -> None:
        """The connection to the cloud is gone, or the agent is stopping: every
        tunnel ends with it, every secret first.

        The cloud may not have noticed, because a connection that comes back
        before the old one is seen to go replaces it without the cloud's own
        cleanup, so each is told once the next connection is up (start).
        """
        for tunnel_id in await self._end_every_tunnel():
            self._untold[tunnel_id] = TUNNEL_CONNECTION_LOST
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _teardown(self, conn: TunnelConnection) -> None:
        """Stop a detached tunnel's work and close its sockets."""
        # The receive task, unless this is it: a data socket that ended ends
        # its own tunnel from inside the receive loop.
        if conn.recv_task is not asyncio.current_task():
            await _cancel_and_wait([conn.recv_task])
        # In-flight data-message handlers go BEFORE the connection maps: an
        # http_request/ws_open handler still awaiting its local connect would
        # otherwise complete after this close and re-populate
        # local_ws_connections / spawn a forwarder for a tunnel that no longer
        # exists.
        await _cancel_and_wait(conn._data_tasks)
        # Forward tasks, awaited so their cleanup finishes before the sockets
        # they reference are closed below.
        await _cancel_and_wait(conn._forward_tasks)
        for ws in list(conn.local_ws_connections.values()):
            await _close_quietly(ws)
        conn.local_ws_connections.clear()
        if conn.data_ws:
            await _close_quietly(conn.data_ws)
            conn.data_ws = None

    async def _data_receive_loop(self, conn: TunnelConnection) -> None:
        """Receive messages from the secondary data WS and dispatch."""
        try:
            while conn.data_ws:
                raw = await conn.data_ws.recv()
                msg = read_tunnel_data(json.loads(raw))
                msg_type = msg.get("type", "")

                if msg_type == HTTP_REQUEST:
                    request_id = msg["id"]
                    task = asyncio.create_task(self._handle_http_request(conn, msg))
                    conn._data_tasks.add(task)
                    task.add_done_callback(conn._data_tasks.discard)
                    if request_id:
                        conn.http_tasks[request_id] = task
                        task.add_done_callback(
                            lambda _t, rid=request_id: conn.http_tasks.pop(rid, None)
                        )
                elif msg_type == HTTP_CANCEL:
                    # Nobody is reading this response any more -- the browser
                    # went away, or the cloud gave up on it. Stop pulling it
                    # out of the local server.
                    task = conn.http_tasks.pop(msg["id"], None)
                    if task and not task.done():
                        task.cancel()
                elif msg_type == WS_OPEN:
                    # A48: reserve the ws_id BEFORE awaiting anything. Frames
                    # that race ahead of the local connect get queued behind
                    # this entry rather than being dropped.
                    ws_id = msg["id"]
                    if ws_id:
                        conn.pending_ws_opens.setdefault(ws_id, [])
                    task = asyncio.create_task(self._handle_ws_open(conn, msg))
                    conn._data_tasks.add(task)
                    task.add_done_callback(conn._data_tasks.discard)
                elif msg_type == WS_FRAME:
                    ws_id = msg["id"]
                    if ws_id in conn.pending_ws_opens:
                        # Local WS still opening — queue for drain (A48).
                        conn.pending_ws_opens[ws_id].append(msg)
                    else:
                        await self._handle_ws_frame(conn, msg)
                elif msg_type == WS_CLOSE:
                    ws_id = msg["id"]
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
            # Catch-all: isolates unexpected data-channel errors from crashing the agent
            log.exception(f"Tunnel {conn.tunnel_id}: error in data receive loop")
        # Nothing reads the data socket any more, so the tunnel can carry
        # nothing: it ends here, and its secret with it.
        await self._end_here(conn.tunnel_id, TUNNEL_DATA_SOCKET_CLOSED)

    async def _handle_http_request(self, conn: TunnelConnection, msg: dict) -> None:
        """Proxy an HTTP request to the local service."""
        msg = read_tunnel_data(msg, HTTP_REQUEST)
        request_id = msg["id"]
        method = msg["method"]
        path = msg["path"]
        headers = msg["headers"]
        body_b64 = msg["body"]

        local_origin = self._loopback_origin(conn.target_port)
        url = f"{local_origin}{path}"

        # True once this response's status and headers have gone up. After
        # that a failure can no longer be reported as a 502 -- the only honest
        # thing left is to end the body.
        started = False

        try:
            client = await self._get_http_client()
            body = base64.b64decode(body_b64) if body_b64 else None

            # Filter headers. The tunnel marker is dropped on the way in and
            # re-stamped below, so whoever called the cloud can't hand us one.
            req_headers = {}
            skip = {
                "host", "connection", "upgrade", "transfer-encoding",
                TUNNEL_HEADER, CLOUD_SESSION_HEADER,
                *FORWARDED_HEADERS,
            }
            for k, v in headers.items():
                if k.lower() not in skip:
                    req_headers[k] = v

            # Mark the request as tunneled. It is about to arrive at the local
            # server over loopback, which is the identity the console uses to
            # configure the host network without a password — this is what
            # keeps a remote caller from inheriting that. See
            # openavc/utils/request_origin.py.
            req_headers[TUNNEL_HEADER] = "1"

            # Only on a tunnel the cloud authorized, and only with the secret
            # this process minted — dropped from the inbound headers just
            # above, so whoever called the cloud cannot supply one.
            if conn.session_secret:
                req_headers[CLOUD_SESSION_HEADER] = conn.session_secret

            async with client.stream(
                method=method,
                url=url,
                headers=req_headers,
                content=body,
            ) as response:
                resp_headers = self._rewrite_location(conn, dict(response.headers))

                if not self._should_stream(conn, method, response):
                    # Reject oversized responses to prevent memory exhaustion.
                    # A declared length is now checked BEFORE the body is read
                    # rather than after: the request is streamed, so finding
                    # out how big it is no longer means holding all of it.
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_BUFFERED_RESPONSE:
                        raise ValueError(f"Response too large: {content_length} bytes")

                    await response.aread()
                    if len(response.content) > MAX_BUFFERED_RESPONSE:
                        raise ValueError(f"Response too large: {len(response.content)} bytes")

                    resp_msg = build_http_response(
                        request_id, response.status_code, resp_headers,
                        base64.b64encode(response.content).decode(),
                    )
                else:
                    # Send it as it arrives. No size ceiling here on purpose:
                    # that one exists because the buffered path holds the whole
                    # body in memory, and this path holds one chunk.
                    #
                    # Both length and encoding are dropped: the bytes below are
                    # decoded (httpx undoes any content-encoding), and the cloud
                    # re-frames the response anyway, so forwarding either header
                    # would describe a body that isn't the one being sent.
                    for header in ("content-length", "content-encoding"):
                        resp_headers.pop(header, None)
                    await self._send_data(conn, build_http_response_start(
                        request_id, response.status_code, resp_headers,
                    ))
                    started = True
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        await self._send_data(conn, build_http_body(
                            request_id, base64.b64encode(chunk).decode(),
                        ))
                    resp_msg = build_http_body_end(request_id)

        except asyncio.CancelledError:
            # The cloud asked us to stop, so it has already torn down its side
            # and needs no closing message. Just stop reading.
            raise
        except (httpx.HTTPError, OSError, ValueError) as e:
            log.warning(f"Tunnel {conn.tunnel_id}: HTTP proxy error: {e}")
            if started:
                # Status and headers are already gone; all that's left is to
                # end the body honestly rather than leave the reader waiting.
                resp_msg = build_http_body_end(request_id, str(e))
            else:
                resp_msg = build_http_response(
                    request_id, 502, {}, base64.b64encode(b"Bad Gateway").decode(),
                )

        await self._send_data(conn, resp_msg)

    @staticmethod
    def _should_stream(conn: TunnelConnection, method: str, response: Any) -> bool:
        """Whether to send this response in pieces instead of buffering it.

        Three shapes qualify. Two are cases where measuring the body first is
        the wrong move: an event stream, which is deliberately open-ended, and
        anything the local server sent without a declared length, which it is
        by definition still producing. The third is a body that declares more
        than MAX_BUFFERED_RESPONSE: a project export or a large image, which
        the buffered path could only refuse.
        """
        if not conn.stream_responses:
            return False
        if method.upper() == "HEAD" or response.status_code in (204, 304):
            return False
        content_type = response.headers.get("content-type", "").lower()
        if content_type.startswith("text/event-stream"):
            return True
        declared = response.headers.get("content-length")
        if declared is None:
            return True
        try:
            return int(declared) > MAX_BUFFERED_RESPONSE
        except ValueError:
            return False

    @staticmethod
    def _rewrite_location(conn: TunnelConnection, resp_headers: dict) -> dict:
        """Strip the loopback origin off a redirect so it stays in the tunnel.

        When TLS is on, both the redirect listener (http -> https) and the main
        TLS app emit Location values that need stripping, so both schemes/host
        variants are checked.
        """
        from openavc import config as _cfg
        local_origins = [
            f"http://localhost:{conn.target_port}",
            f"http://127.0.0.1:{conn.target_port}",
        ]
        if _cfg.TLS_ENABLED and conn.target_port == _cfg.HTTP_PORT:
            local_origins.extend([
                f"https://localhost:{_cfg.TLS_PORT}",
                f"https://127.0.0.1:{_cfg.TLS_PORT}",
            ])
        if "location" in resp_headers:
            loc = resp_headers["location"]
            for origin in local_origins:
                if loc.startswith(origin):
                    resp_headers["location"] = loc[len(origin):]
                    break
        return resp_headers

    @staticmethod
    async def _send_data(conn: TunnelConnection, msg: dict) -> None:
        """Send one message up the tunnel's data WS, if it's still there."""
        if not conn.data_ws:
            return
        try:
            await conn.data_ws.send(json.dumps(msg))
        except (ConnectionClosed, OSError):
            log.warning(
                f"Tunnel {conn.tunnel_id}: failed to send {msg.get('type', '?')}"
            )

    async def _handle_ws_open(self, conn: TunnelConnection, msg: dict) -> None:
        """Open a local WebSocket connection for proxying."""
        msg = read_tunnel_data(msg, WS_OPEN)
        ws_id = msg["id"]
        # `path` may include a query string (A47): preserve it intact so
        # endpoints that rely on `?token=…` style auth still authenticate.
        path = msg["path"]
        url, ssl_ctx = self._loopback_ws_url(conn.target_port, path)

        # Subprotocols arrive as a structured list; pass them to the
        # `websockets` client so it can negotiate with the local server.
        subprotocols = msg["subprotocols"] or None
        # Forward only application-level headers; drop hop-by-hop and any
        # WebSocket-handshake headers the client library generates itself.
        raw_headers = msg["headers"] or {}
        drop = {
            "host", "connection", "upgrade", "sec-websocket-key",
            "sec-websocket-version", "sec-websocket-extensions",
            "sec-websocket-protocol", "content-length", "transfer-encoding",
            TUNNEL_HEADER, CLOUD_SESSION_HEADER,
            *FORWARDED_HEADERS,
        }
        # Same marker as the HTTP path. Nothing on the WebSocket surface trusts
        # loopback today (client type comes from the auth level, not the peer),
        # so this is here so that stays true by construction rather than by
        # nobody having added such a check yet.
        additional_headers = [
            (k, v) for k, v in raw_headers.items() if k.lower() not in drop
        ]
        additional_headers.append((TUNNEL_HEADER, "1"))
        # The Programmer is not usable without its WebSocket, so an authorized
        # session has to authenticate here too or the IDE loads and sits empty.
        if conn.session_secret:
            additional_headers.append((CLOUD_SESSION_HEADER, conn.session_secret))

        try:
            local_ws = await websockets.connect(
                url,
                ping_interval=None,
                ping_timeout=None,
                subprotocols=subprotocols,
                additional_headers=additional_headers or None,
                ssl=ssl_ctx,
                proxy=None,  # this computer's own server: never through a proxy
            )

            # The tunnel may have been closed while we awaited the connect.
            # Registering now would put a live socket and a forwarder on a
            # conn nothing will ever clean up again.
            if self._tunnels.get(conn.tunnel_id) is not conn:
                conn.pending_ws_opens.pop(ws_id, None)
                try:
                    await local_ws.close()
                except (ConnectionClosed, OSError):
                    pass
                return

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
                if qtype == WS_FRAME:
                    await self._handle_ws_frame(conn, queued_msg)
                elif qtype == WS_CLOSE:
                    await self._handle_ws_close(conn, queued_msg)
                    # Close consumed; anything still queued behind it is stale.
                    queue.clear()
                    break
            conn.pending_ws_opens.pop(ws_id, None)

            # Start forwarding local → cloud
            task = asyncio.create_task(self._forward_local_ws(conn, ws_id, local_ws))
            conn._forward_tasks.add(task)
            task.add_done_callback(conn._forward_tasks.discard)

        except (ConnectionClosed, OSError, InvalidURI, InvalidHandshake) as e:
            log.warning(f"Tunnel {conn.tunnel_id}: WS open to {url} failed: {e}")
            # Drop any queued frames; the local socket never came up.
            conn.pending_ws_opens.pop(ws_id, None)
            # Tell the cloud this socket could not be opened here, so the
            # browser's closes with a code that says so rather than a normal
            # close it would read as a session that ended.
            if conn.data_ws:
                try:
                    await conn.data_ws.send(json.dumps(
                        build_ws_close(ws_id, WS_CLOSE_LOCAL_UNREACHABLE)
                    ))
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

                frame = build_ws_frame(ws_id, data_b64, is_binary)
                if conn.data_ws:
                    await conn.data_ws.send(json.dumps(frame))
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            return
        except (OSError, ValueError):
            log.exception(f"Tunnel {conn.tunnel_id}: forward local WS error")
        finally:
            # Clean up and pass the local server's close on to the cloud, code
            # and reason included: the panel and the Programmer decide from
            # the code whether to sign in again, reload or wait for approval,
            # and without it every close reached the browser as a normal one.
            conn.local_ws_connections.pop(ws_id, None)
            if conn.data_ws:
                code, reason = self._closed_with(local_ws)
                try:
                    await conn.data_ws.send(json.dumps(build_ws_close(ws_id, code, reason)))
                except (ConnectionClosed, OSError):
                    pass  # Best-effort notification to cloud

    @staticmethod
    def _close_arguments(msg: dict[str, Any]) -> dict[str, Any]:
        """What to close a local socket with for the cloud's ws_close: the
        browser's code and reason, or nothing when no usable code came."""
        code = sendable_close_code(msg["code"])
        if code is None:
            return {}
        reason = msg["reason"] if isinstance(msg["reason"], str) else ""
        return {"code": code, "reason": close_reason(reason)}

    @staticmethod
    def _closed_with(local_ws: Any) -> tuple[int, str]:
        """The close code and reason to pass on for a local socket that ended."""
        code = getattr(local_ws, "close_code", None)
        reason = getattr(local_ws, "close_reason", None)
        return ws_close_code(code if isinstance(code, int) else None), (
            reason if isinstance(reason, str) else ""
        )

    async def _handle_ws_frame(self, conn: TunnelConnection, msg: dict) -> None:
        """Forward a WebSocket frame from cloud to local."""
        msg = read_tunnel_data(msg, WS_FRAME)
        ws_id = msg["id"]
        local_ws = conn.local_ws_connections.get(ws_id)
        if not local_ws:
            return

        data = base64.b64decode(msg["data"])
        try:
            if msg["binary"]:
                await local_ws.send(data)
            else:
                await local_ws.send(data.decode("utf-8", errors="replace"))
        except (ConnectionClosed, OSError):
            log.warning(f"Tunnel {conn.tunnel_id}: failed to forward ws_frame to local")

    async def _handle_ws_close(self, conn: TunnelConnection, msg: dict) -> None:
        """Close a local WebSocket connection, with the browser's own code
        when the cloud passed one on and a close frame may carry it."""
        msg = read_tunnel_data(msg, WS_CLOSE)
        local_ws = conn.local_ws_connections.pop(msg["id"], None)
        if local_ws:
            try:
                await local_ws.close(**self._close_arguments(msg))
            except (ConnectionClosed, OSError):
                pass  # Best-effort close
