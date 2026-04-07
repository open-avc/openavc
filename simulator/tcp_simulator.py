"""
TCPSimulator — async TCP server base for device simulators.

Handles server lifecycle, client connections, and message framing.
Subclasses implement handle_command() to define protocol behavior.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import abstractmethod

from simulator.base import BaseSimulator

logger = logging.getLogger(__name__)


class TCPSimulator(BaseSimulator):
    """TCP protocol simulator. You implement handle_command(); the framework does the rest."""

    def __init__(self, device_id: str, config: dict | None = None):
        super().__init__(device_id, config)
        self._server: asyncio.Server | None = None
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._delimiter: bytes | None = None

        # Determine delimiter from SIMULATOR_INFO or config
        delim = self.SIMULATOR_INFO.get("delimiter") or self.config.get("delimiter")
        if delim:
            self._delimiter = delim.encode() if isinstance(delim, str) else delim

    # ── Override points for subclasses ──

    async def on_client_connected(self, client_id: str) -> bytes | None:
        """Called when a new client connects. Return greeting bytes or None.

        Override for protocols that send a banner on connect (e.g., PJLink).
        """
        return None

    @abstractmethod
    def handle_command(self, data: bytes) -> bytes | None:
        """Handle incoming data from the driver, return response bytes or None.

        This is the main method to implement. The framework calls it once per
        received message (line-delimited for text protocols, or raw chunks
        for binary protocols when no delimiter is set).

        Use self.state to read current state, self.set_state(k, v) to update it.
        Use self.active_errors to check for injected error conditions.
        """

    async def push(self, data: bytes) -> None:
        """Send unsolicited data to all connected clients.

        Use for push notifications (e.g., state change notifications,
        subscription updates).
        """
        dead = []
        for client_id, writer in self._clients.items():
            try:
                writer.write(data)
                await writer.drain()
                self.log_protocol("out", data, client_id)
            except (ConnectionError, OSError):
                dead.append(client_id)
        for cid in dead:
            self._clients.pop(cid, None)

    async def push_to(self, client_id: str, data: bytes) -> None:
        """Send data to a specific client."""
        writer = self._clients.get(client_id)
        if writer:
            try:
                writer.write(data)
                await writer.drain()
                self.log_protocol("out", data, client_id)
            except (ConnectionError, OSError):
                self._clients.pop(client_id, None)

    # ── Lifecycle ──

    async def start(self, port: int) -> None:
        """Start the TCP server."""
        self._port = port
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=port,
        )
        self._running = True
        logger.info(
            "%s started on port %d (driver: %s)",
            self.name, port, self.driver_id,
        )

    async def stop(self) -> None:
        """Stop the TCP server and disconnect all clients."""
        self._running = False
        # Close all client connections
        for client_id, writer in list(self._clients.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()
        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("%s stopped", self.name)

    # ── Internal client handler ──

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        client_id = uuid.uuid4().hex[:8]
        self._clients[client_id] = writer
        peer = writer.get_extra_info("peername")
        logger.info("%s: client connected from %s (id=%s)", self.name, peer, client_id)

        try:
            # Send greeting if defined
            greeting = await self.on_client_connected(client_id)
            if greeting:
                writer.write(greeting)
                await writer.drain()
                self.log_protocol("out", greeting, client_id)

            # Read loop
            while self._running:
                if self._delimiter:
                    try:
                        data = await asyncio.wait_for(
                            reader.readuntil(self._delimiter),
                            timeout=30.0,
                        )
                    except asyncio.IncompleteReadError:
                        break
                    except asyncio.TimeoutError:
                        continue
                else:
                    # Binary mode — read available data
                    try:
                        data = await asyncio.wait_for(
                            reader.read(4096),
                            timeout=30.0,
                        )
                    except asyncio.TimeoutError:
                        continue
                    if not data:
                        break

                self.log_protocol("in", data, client_id)

                # Network conditions: check for drop
                if self._network_layer and self._network_layer.should_drop(self.device_id):
                    continue

                # Network conditions: check for random disconnect
                if self._network_layer and self._network_layer.should_disconnect(self.device_id):
                    logger.info("%s: network instability — dropping connection", self.name)
                    break

                # Check for no_response error behavior
                if self.has_error_behavior("no_response"):
                    continue

                # Apply network latency (before device response delay)
                if self._network_layer:
                    await self._network_layer.apply_latency(self.device_id)

                # Apply command response delay
                delay = self._delays.get("command_response", 0)
                if delay > 0:
                    await asyncio.sleep(delay)

                # Handle the command
                try:
                    response = self.handle_command(data)
                except Exception:
                    logger.exception("%s: error in handle_command", self.name)
                    response = None

                # Check for corrupt_response error behavior
                if response and self.has_error_behavior("corrupt_response"):
                    response = _corrupt_bytes(response)

                if response:
                    writer.write(response)
                    await writer.drain()
                    self.log_protocol("out", response, client_id)

        except (ConnectionError, OSError):
            pass
        finally:
            self._clients.pop(client_id, None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("%s: client disconnected (id=%s)", self.name, client_id)


def _corrupt_bytes(data: bytes) -> bytes:
    """Randomly corrupt some bytes for error simulation."""
    import random
    ba = bytearray(data)
    if len(ba) > 0:
        # Corrupt 1-3 bytes
        for _ in range(min(3, len(ba))):
            idx = random.randint(0, len(ba) - 1)
            ba[idx] = random.randint(0, 255)
    return bytes(ba)
