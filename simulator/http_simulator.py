"""
HTTPSimulator — async HTTP server base for device simulators.

Handles server lifecycle. Subclasses implement handle_request()
to define API behavior. Used for REST/JSON, JSON-RPC, and SOAP/XML
device protocols.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import abstractmethod

from aiohttp import web

from simulator.base import BaseSimulator

logger = logging.getLogger(__name__)


class HTTPSimulator(BaseSimulator):
    """HTTP protocol simulator. You implement handle_request(); the framework does the rest."""

    # Event-stream endpoints (push: {type: sse}): set this to the URL paths
    # a driver may subscribe to. A GET on one of them with
    # Accept: text/event-stream is held open, and push_sse_event() delivers
    # events to every open subscription. Requests without that Accept header
    # still route through handle_request() normally.
    sse_paths: list[str] = []

    def __init__(self, device_id: str, config: dict | None = None):
        super().__init__(device_id, config)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._sse_clients: set[asyncio.Queue] = set()

    # ── Override point for subclasses ──

    @abstractmethod
    def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: str,
    ) -> tuple[int, dict | str]:
        """Handle an incoming HTTP request from the driver.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: Request path (e.g., "/api/power")
            headers: Request headers as dict
            body: Request body as string (empty for GET)

        Returns:
            (status_code, response_body)
            response_body can be a dict (auto-serialized to JSON) or a string.

        Use self.state to read current state, self.set_state(k, v) to update it.
        Use self.active_errors to check for injected error conditions.
        """

    # ── Lifecycle ──

    async def start(self, port: int) -> None:
        """Start the HTTP server."""
        self._port = port
        self._app = web.Application()
        # Catch-all route — forwards everything to handle_request
        self._app.router.add_route("*", "/{path:.*}", self._handle)

        # handler_cancellation: an event-stream subscriber that disconnects
        # must release its handler — without it the handler blocks on its
        # queue until the next event write fails.
        self._runner = web.AppRunner(self._app, handler_cancellation=True)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", port)
        await self._site.start()
        self._running = True
        logger.info(
            "%s started on port %d (driver: %s)",
            self.name, port, self.driver_id,
        )

    async def stop(self) -> None:
        """Stop the HTTP server."""
        self._running = False
        # Unblock held event-stream handlers first — cleanup() waits for
        # active handlers, and an SSE subscription blocks on its queue.
        for queue in list(self._sse_clients):
            queue.put_nowait(None)
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._site = None
        logger.info("%s stopped", self.name)

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

    async def _handle(self, request: web.Request) -> web.Response:
        """Route all HTTP requests through handle_request."""
        method = request.method
        path = "/" + request.match_info.get("path", "")
        if request.query_string:
            path += "?" + request.query_string

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

        # Apply command response delay. An explicit 0 means an instant reply;
        # only an unset command_response falls back to the request_response
        # alias.
        delay = self._delays.get("command_response")
        if delay is None:
            delay = self._delays.get("request_response", 0)
        if delay > 0:
            await asyncio.sleep(delay)

        # Handle the request
        try:
            status_code, response_body = self.handle_request(method, path, headers, body)
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
        )
