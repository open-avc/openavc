"""
HTTPSimulator — async HTTP server base for device simulators.

Subclasses implement handle_request() to define API behavior. Used for
REST/JSON, JSON-RPC, and SOAP/XML device protocols.

The server itself — aiohttp lifecycle, optional TLS, event streams, the
network-condition and delay preamble every request goes through — is
:class:`HTTPServerMixin`, shared with the YAML auto-generator so an HTTP
driver gets the same server whether its behavior is written in Python or
generated from its .avcdriver definition.

Optional TLS: set ``"tls": True`` in SIMULATOR_INFO (or config) and the server
mints an ephemeral self-signed cert and serves https instead of http. Devices
whose API is HTTPS-only (a Crestron NVX, a Dante Director) have drivers with an
https:// base URL and verification off; this lets those drivers connect to the
simulator exactly as they do to the real device, instead of needing a
plain-HTTP mode that never runs in the field. See
``simulator/self_signed_tls.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import abstractmethod

from aiohttp import web

from openavc.simulator.base import BaseSimulator
from openavc.simulator.self_signed_tls import build_optional_tls, remove_cert_files

logger = logging.getLogger(__name__)


class HTTPServerMixin:
    """The aiohttp server every HTTP simulator runs on.

    Owns the lifecycle, the optional TLS termination, the event-stream
    subscriptions and the preamble each request goes through (log it, honor
    drop / no_response, apply latency and the device's response delay).
    What a request *means* is the one thing that differs, so that is the
    override point: :meth:`respond_http`.

    Mix into a :class:`~simulator.base.BaseSimulator`. Nothing here is named
    ``start`` or ``stop``, so a simulator that decides its transport at
    construction time can carry this alongside another server.
    """

    # Event-stream endpoints (push: {type: sse}): set this to the URL paths
    # a driver may subscribe to. A GET on one of them with
    # Accept: text/event-stream is held open, and push_sse_event() delivers
    # events to every open subscription. Requests without that Accept header
    # still route through respond_http() normally.
    sse_paths: list[str] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._sse_clients: set[asyncio.Queue] = set()
        self._tls_files: tuple[str, str] | None = None
        # Peer address of the request being handled — the dial-back target for
        # a device whose subscription protocol registers "wherever I came from".
        self._last_peer_ip = "127.0.0.1"

    # ── Lifecycle ──

    async def start_http_server(self, port: int) -> None:
        """Start the HTTP server (https when this simulator opted into TLS)."""
        self._port = port
        self._app = web.Application()
        # Catch-all route — forwards everything to the request handler
        self._app.router.add_route("*", "/{path:.*}", self._handle_http_request)

        # handler_cancellation: an event-stream subscriber that disconnects
        # (driver reconnect, network drop) must release its handler — without
        # it the handler blocks on its queue until the next event write fails.
        self._runner = web.AppRunner(self._app, handler_cancellation=True)
        await self._runner.setup()
        ssl_ctx, self._tls_files = build_optional_tls(
            self.SIMULATOR_INFO, self.config, self.name
        )
        self._site = web.TCPSite(self._runner, "127.0.0.1", port, ssl_context=ssl_ctx)
        await self._site.start()
        self._running = True
        logger.info(
            "%s started on HTTP port %d (driver: %s, tls=%s)",
            self.name, port, self.driver_id, ssl_ctx is not None,
        )

    async def stop_http_server(self) -> None:
        """Stop the HTTP server."""
        self._running = False
        self._cancel_state_machine_timers()
        # Unblock held event-stream handlers first — cleanup() waits for
        # active handlers, and an SSE subscription blocks on its queue.
        self._close_sse_clients()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._site = None
        remove_cert_files(self._tls_files)
        self._tls_files = None
        logger.info("%s stopped", self.name)

    # ── Override points ──

    async def respond_http(
        self,
        request: web.Request,
        method: str,
        path: str,
        headers: dict[str, str],
        body: str,
    ) -> web.Response:
        """Answer one request. The preamble has already run by the time it's called."""
        raise NotImplementedError

    def decode_request_path(self, path: str) -> str:
        """Last chance to rewrite the request path before it is matched.

        Default is verbatim. The YAML auto-generator URL-decodes here, because
        its handlers are regexes built from the driver's own ``path:`` fields,
        which are written un-encoded.
        """
        return path

    # ── HTTP-callback push (push: {type: http_listener}) ──

    async def post_http_callback(
        self,
        url: str,
        body: str | bytes,
        headers: dict[str, str] | None = None,
        method: str = "POST",
    ) -> int | None:
        """Deliver one push notification to a registered callback URL.

        For simulators of devices that dial OUT to a subscriber (webhooks,
        UPnP GENA NOTIFY — pass ``method="NOTIFY"`` and the GENA headers).
        Returns the response status, or None when delivery failed — a real
        device silently drops feedback its subscriber stopped answering, so
        failures log at debug level only.
        """
        from aiohttp import ClientSession, ClientTimeout

        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as session:
                async with session.request(
                    method, url, data=data, headers=headers or {}
                ) as resp:
                    preview = data[:200].decode("utf-8", errors="replace")
                    self.log_protocol(
                        "out", f"{method} {url} -> {resp.status} | {preview}"
                    )
                    return resp.status
        except Exception as e:
            logger.debug("%s: callback %s to %s failed: %s", self.name, method, url, e)
            return None

    # ── Server-Sent Events (push: {type: sse}) ──

    def push_sse_event(self, data: str) -> None:
        """Deliver one event to every open event-stream subscription.

        ``data`` is the event's payload (typically a JSON document); it is
        framed as ``data: <payload>\\n\\n`` on the wire. No-op with no
        subscribers — device state is authoritative either way, the driver
        resyncs by polling.
        """
        if not self._sse_clients:
            return
        for queue in list(self._sse_clients):
            queue.put_nowait(data)
        self.log_protocol("out", f"data: {data[:200]}")

    def _close_sse_clients(self) -> None:
        """Unblock every open event-stream handler so stop can finish."""
        for queue in list(self._sse_clients):
            queue.put_nowait(None)

    async def _serve_sse(self, request: web.Request, path: str) -> web.StreamResponse:
        """Hold an event-stream subscription open until the client leaves
        or the simulator stops (None sentinel)."""
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self._sse_clients.add(queue)
        self.log_protocol("in", f"GET {path} (event-stream subscribed)")
        try:
            while self._running:
                data = await queue.get()
                if data is None:
                    break
                await response.write(f"data: {data}\n\n".encode("utf-8"))
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._sse_clients.discard(queue)
            self.log_protocol("in", f"GET {path} (event-stream closed)")
        return response

    # ── Internal request handler ──

    def _response_delay(self) -> float:
        """Resolve the response delay: ``command_response`` when the author set
        it (0 included — an explicit 0 means an instant reply), falling back to
        the ``request_response`` alias, then no delay."""
        delay = self._delays.get("command_response")
        if delay is None:
            delay = self._delays.get("request_response", 0)
        return delay

    async def _handle_http_request(self, request: web.Request) -> web.StreamResponse:
        """Run the shared preamble, then hand the request to respond_http."""
        method = request.method
        path = "/" + request.match_info.get("path", "")
        if request.query_string:
            path += "?" + request.query_string
        path = self.decode_request_path(path)

        # Event-stream subscription: a declared SSE path requested with
        # Accept: text/event-stream is held open instead of answered.
        if (
            method == "GET"
            and self.sse_paths
            and path.split("?")[0] in self.sse_paths
            and "text/event-stream" in request.headers.get("Accept", "")
        ):
            return await self._serve_sse(request, path)

        headers = dict(request.headers)
        body = await request.text()
        # Dial-back registrations record the requester as the push target.
        self._last_peer_ip = str(request.remote or "127.0.0.1")

        # Log incoming request
        log_text = f"{method} {path}"
        if body:
            log_text += f" | {body[:200]}"
        self.log_protocol("in", log_text)

        # Network conditions: check for drop (return timeout)
        if self._network_layer and self._network_layer.should_drop(self.device_id):
            await asyncio.sleep(30)
            return web.Response(status=504, text="Gateway Timeout")

        # Check for no_response error behavior
        if self.has_error_behavior("no_response"):
            await asyncio.sleep(30)
            return web.Response(status=504, text="Gateway Timeout")

        # Apply network latency
        if self._network_layer:
            await self._network_layer.apply_latency(self.device_id)

        delay = self._response_delay()
        if delay > 0:
            await asyncio.sleep(delay)

        return await self.respond_http(request, method, path, headers, body)


class HTTPSimulator(HTTPServerMixin, BaseSimulator):
    """HTTP protocol simulator. You implement handle_request(); the framework does the rest."""

    # ── Override point for subclasses ──

    @abstractmethod
    def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: str,
    ) -> tuple[int, dict | str] | tuple[int, dict | str, dict[str, str]]:
        """Handle an incoming HTTP request from the driver.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE — and any other verb
                the device's protocol uses, e.g. UPnP's SUBSCRIBE)
            path: Request path (e.g., "/api/power")
            headers: Request headers as dict
            body: Request body as string (empty for GET)

        Returns:
            (status_code, response_body) — or (status_code, response_body,
            response_headers) for protocols whose answer carries meaningful
            headers (a UPnP SUBSCRIBE reply's SID / TIMEOUT, for instance).
            response_body can be a dict (auto-serialized to JSON) or a string.

        Use self.state to read current state, self.set_state(k, v) to update it.
        Use self.active_errors to check for injected error conditions.
        """

    # ── Lifecycle ──

    async def start(self, port: int) -> None:
        """Start the HTTP server."""
        await self.start_http_server(port)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        await self.stop_http_server()

    # ── Request handling ──

    async def respond_http(
        self,
        request: web.Request,
        method: str,
        path: str,
        headers: dict[str, str],
        body: str,
    ) -> web.Response:
        """Route the request through handle_request and wrap what it returns."""
        # A 3-tuple carries response headers (a protocol like UPnP answers a
        # SUBSCRIBE with its SID and TIMEOUT there).
        response_headers: dict[str, str] = {}
        try:
            result = self.handle_request(method, path, headers, body)
            if len(result) == 3:
                status_code, response_body, response_headers = result
            else:
                status_code, response_body = result
        except Exception:
            logger.exception("%s: error in handle_request", self.name)
            status_code = 500
            response_body = {"error": "Internal simulator error"}

        # Build response
        if isinstance(response_body, dict):
            response_text = json.dumps(response_body)
            content_type = "application/json"
        else:
            response_text = str(response_body)
            content_type = "text/plain"

        # Log outgoing response
        self.log_protocol("out", f"{status_code} | {response_text[:200]}")

        return web.Response(
            status=status_code,
            text=response_text,
            content_type=content_type,
            headers=response_headers or None,
        )
