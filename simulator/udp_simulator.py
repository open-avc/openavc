"""
UDPSimulator — async UDP server base for device simulators.

Handles server lifecycle and datagram routing. Subclasses implement
handle_command() to define protocol behavior, same interface as TCPSimulator.

Used for AV devices that communicate via UDP datagrams (video wall
splicers, some lighting controllers, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod

from simulator.base import BaseSimulator

logger = logging.getLogger(__name__)


class UDPSimulator(BaseSimulator):
    """UDP protocol simulator. You implement handle_command(); the framework does the rest."""

    def __init__(self, device_id: str, config: dict | None = None):
        super().__init__(device_id, config)
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _SimUDPProtocol | None = None

    # ── Override point for subclasses ──

    @abstractmethod
    def handle_command(self, data: bytes) -> bytes | None:
        """Handle incoming datagram from the driver, return response bytes or None.

        This is the main method to implement. The framework calls it once per
        received datagram.

        Use self.state to read current state, self.set_state(k, v) to update it.
        Use self.active_errors to check for injected error conditions.
        """

    # ── Lifecycle ──

    async def start(self, port: int) -> None:
        """Start the UDP server."""
        self._port = port
        loop = asyncio.get_running_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: _SimUDPProtocol(self),
            local_addr=("127.0.0.1", port),
        )
        self._running = True
        logger.info(
            "%s started on UDP port %d (driver: %s)",
            self.name, port, self.driver_id,
        )

    async def stop(self) -> None:
        """Stop the UDP server."""
        self._running = False
        if self._transport:
            self._transport.close()
            self._transport = None
        self._protocol = None
        logger.info("%s stopped", self.name)

    # ── Internal ──

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process an incoming datagram and send response if any."""
        self.log_protocol("in", data)

        # Network conditions: check for drop
        if self._network_layer and self._network_layer.should_drop(self.device_id):
            return

        # Check for no_response error behavior
        if self.has_error_behavior("no_response"):
            return

        # Schedule the async handling
        asyncio.ensure_future(self._handle_datagram_async(data, addr))

    async def _handle_datagram_async(self, data: bytes, addr: tuple[str, int]) -> None:
        """Async handler for datagram processing (supports delays)."""
        # Apply network latency
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

        if response and self._transport:
            self._transport.sendto(response, addr)
            self.log_protocol("out", response)


class _SimUDPProtocol(asyncio.DatagramProtocol):
    """Internal protocol handler that routes datagrams to the simulator."""

    def __init__(self, simulator: UDPSimulator):
        self._simulator = simulator

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._simulator._handle_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP simulator error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.debug("UDP simulator connection lost: %s", exc)


def _corrupt_bytes(data: bytes) -> bytes:
    """Randomly corrupt some bytes for error simulation."""
    import random
    ba = bytearray(data)
    if len(ba) > 0:
        for _ in range(min(3, len(ba))):
            idx = random.randint(0, len(ba) - 1)
            ba[idx] = random.randint(0, 255)
    return bytes(ba)
