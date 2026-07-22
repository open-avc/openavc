"""
WebSocketSimulator — async WebSocket server base for device simulators.

Handles server lifecycle and per-client message plumbing; subclasses
implement handle_message() to define protocol behavior and call send()
/ broadcast() to reply and push. Used for devices controlled over a
WebSocket (e.g. LG webOS SSAP, JSON-over-WS control channels).

The server speaks plain ``ws://`` on 127.0.0.1:port. A real device may
use TLS (``wss://``); the platform's simulation redirect flips the
device's ``ssl`` flag off when it points the driver at the simulator
(see server/core/simulation.py :: _apply_sim_redirect), exactly as it
does for HTTPS device simulators — so the driver connects ``ws://`` here
without the simulator needing a certificate.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod

from aiohttp import WSMsgType, web

from simulator.base import BaseSimulator

logger = logging.getLogger(__name__)


class WebSocketSimulator(BaseSimulator):
    """WebSocket protocol simulator.

    You implement ``handle_message(client, message)``; the framework runs
    the server, tracks connected clients, and applies the shared network /
    error-injection layers. Reply with ``await self.send(client, text)``;
    push to every open client with ``await self.broadcast(text)``.
    """

    def __init__(self, device_id: str, config: dict | None = None):
        super().__init__(device_id, config)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        # Live client connections (one webOS control session per entry).
        self._clients: set[web.WebSocketResponse] = set()

    # ── Override points for subclasses ──

    @abstractmethod
    async def handle_message(self, client: web.WebSocketResponse, message: str) -> None:
        """Handle one text frame from a connected driver.

        ``message`` is the raw text payload (typically a JSON document).
        Read/update device state with ``self.state`` / ``self.set_state``;
        reply with ``await self.send(client, ...)`` and push unsolicited
        updates with ``await self.broadcast(...)``. Subscription tracking
        (which client wants which updates) is the subclass's concern.
        """

    async def on_client_connect(self, client: web.WebSocketResponse) -> None:
        """Optional hook: a driver just opened a control session. Default no-op."""

    async def on_client_disconnect(self, client: web.WebSocketResponse) -> None:
        """Optional hook: a control session closed. Default no-op."""

    # ── Send helpers ──

    async def send(self, client: web.WebSocketResponse, text: str) -> None:
        """Send one text frame to a single client. Silently drops if it closed."""
        if client.closed:
            return
        try:
            await client.send_str(text)
            self.log_protocol("out", text, client_id=_client_id(client))
        except (ConnectionError, RuntimeError) as e:
            logger.debug("%s: send failed: %s", self.name, e)

    async def broadcast(self, text: str) -> None:
        """Send one text frame to every open client (device-initiated push).

        No-op with no clients — device state stays authoritative and a
        reconnecting driver resyncs on its next subscribe.
        """
        for client in list(self._clients):
            await self.send(client, text)

    # ── Lifecycle ──

    async def start(self, port: int) -> None:
        """Start the WebSocket server on 127.0.0.1:port."""
        self._port = port
        self._app = web.Application()
        # Catch-all route: a device's control endpoint may sit at "/" or a
        # vendor path; every GET that carries the WebSocket upgrade is
        # handled the same way.
        self._app.router.add_route("GET", "/{path:.*}", self._handle)
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
        """Stop the server and close every open client session."""
        self._running = False
        self._cancel_state_machine_timers()
        for client in list(self._clients):
            try:
                await client.close()
            except (ConnectionError, RuntimeError):
                pass
        self._clients.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._site = None
        logger.info("%s stopped", self.name)

    # ── Internal connection handler ──

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        """Upgrade to WebSocket and pump text frames through handle_message."""
        ws = web.WebSocketResponse(heartbeat=None, max_msg_size=0)
        if not ws.can_prepare(request).ok:
            # Not a WebSocket upgrade — a probe/health GET. Answer plainly so
            # the caller sees the port is alive without a protocol error.
            return web.Response(status=426, text="Upgrade Required")
        await ws.prepare(request)
        self._clients.add(ws)
        self.log_protocol("in", "<ws connect>", client_id=_client_id(ws))
        try:
            await self.on_client_connect(ws)
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSING):
                        break
                    continue

                self.log_protocol("in", msg.data, client_id=_client_id(ws))

                # Shared network / error layers (mirror HTTPSimulator): a
                # dropped or non-responsive device simply never answers.
                if self._network_layer and self._network_layer.should_drop(self.device_id):
                    continue
                if self.has_error_behavior("no_response"):
                    continue
                if self._network_layer:
                    await self._network_layer.apply_latency(self.device_id)
                delay = self._delays.get("command_response")
                if delay is None:
                    delay = self._delays.get("request_response", 0)
                if delay and delay > 0:
                    await asyncio.sleep(delay)

                try:
                    await self.handle_message(ws, msg.data)
                except Exception:
                    logger.exception("%s: error in handle_message", self.name)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(ws)
            self.log_protocol("in", "<ws close>", client_id=_client_id(ws))
            try:
                await self.on_client_disconnect(ws)
            except Exception:
                logger.exception("%s: error in on_client_disconnect", self.name)
        return ws


def _client_id(ws: web.WebSocketResponse) -> str:
    """Short, stable-ish identifier for a client session (for the protocol log)."""
    return f"ws{id(ws) & 0xffff:04x}"
