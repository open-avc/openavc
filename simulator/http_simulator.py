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

    def __init__(self, device_id: str, config: dict | None = None):
        super().__init__(device_id, config)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

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

        self._runner = web.AppRunner(self._app)
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
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._site = None
        logger.info("%s stopped", self.name)

    # ── Internal request handler ──

    async def _handle(self, request: web.Request) -> web.Response:
        """Route all HTTP requests through handle_request."""
        method = request.method
        path = "/" + request.match_info.get("path", "")
        if request.query_string:
            path += "?" + request.query_string
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

        # Apply command response delay
        delay = self._delays.get("command_response") or self._delays.get("request_response", 0)
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
