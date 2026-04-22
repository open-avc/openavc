"""
OSCSimulator — async UDP server base for OSC device simulators.

Parallel to TCPSimulator and HTTPSimulator. Handles UDP server lifecycle,
OSC message decoding, and response encoding. Subclasses implement
handle_message() to define device behavior.

Example subclass:
    class X32Simulator(OSCSimulator):
        SIMULATOR_INFO = {
            "driver_id": "behringer_x32",
            "name": "X32 Simulator",
            "transport": "osc",
            "default_port": 10023,
            "initial_state": {"ch01_fader": 0.75, "ch01_mute": False},
        }

        def handle_message(self, address, args):
            if address == "/ch/01/mix/fader" and args:
                self.set_state("ch01_fader", args[0][1])
                return [(address, args)]
            if address == "/ch/01/mix/fader":
                return [(address, [("f", self.state["ch01_fader"])])]
            return None
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from typing import Any

from simulator.base import BaseSimulator

logger = logging.getLogger(__name__)


class OSCSimulator(BaseSimulator):
    """OSC protocol simulator over UDP.

    Subclasses implement handle_message(address, args) which receives
    decoded OSC messages and returns a list of (address, args) response
    tuples to send back to the client.
    """

    def __init__(self, device_id: str, config: dict | None = None):
        super().__init__(device_id, config)
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._last_client_addr: tuple[str, int] | None = None

    @abstractmethod
    def handle_message(
        self, address: str, args: list[tuple[str, Any]]
    ) -> list[tuple[str, list[tuple[str, Any]]]] | None:
        """Handle an incoming OSC message.

        Args:
            address: The OSC address pattern (e.g., "/ch/01/mix/fader").
            args: List of (type_tag, value) tuples.

        Returns:
            List of (address, args) response tuples to send back, or None.
            Each response is encoded as an OSC message and sent to the client.
        """

    async def start(self, port: int) -> None:
        """Start the UDP/OSC server."""
        self._port = port
        loop = asyncio.get_running_loop()
        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: _OSCSimProtocol(self),
            local_addr=("127.0.0.1", port),
        )
        self._running = True
        logger.info(
            "%s started on UDP port %d (driver: %s)",
            self.name, port, self.driver_id,
        )

    async def stop(self) -> None:
        """Stop the UDP/OSC server."""
        self._running = False
        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
        logger.info("%s stopped", self.name)

    async def push_message(
        self, address: str, args: list[tuple[str, Any]] | None = None
    ) -> None:
        """Send an unsolicited OSC message to the last known client."""
        if not self._udp_transport or not self._last_client_addr:
            return
        from server.transport.osc_codec import osc_encode_message
        data = osc_encode_message(address, args)
        self._udp_transport.sendto(data, self._last_client_addr)
        self.log_protocol("out", data)

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process an incoming UDP datagram as an OSC message."""
        self._last_client_addr = addr
        self.log_protocol("in", data)

        if self._network_layer and self._network_layer.should_drop(self.device_id):
            return
        if self.has_error_behavior("no_response"):
            return

        asyncio.ensure_future(self._handle_datagram_async(data, addr))

    async def _handle_datagram_async(
        self, data: bytes, addr: tuple[str, int]
    ) -> None:
        """Async handler for OSC datagram processing."""
        from server.transport.osc_codec import osc_decode_bundle, osc_encode_message

        if self._network_layer:
            await self._network_layer.apply_latency(self.device_id)

        delay = self._delays.get("command_response", 0)
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            messages = osc_decode_bundle(data)
        except (ValueError, Exception) as e:
            logger.warning("%s: OSC decode error: %s", self.name, e)
            return

        for osc_address, osc_args in messages:
            try:
                responses = self.handle_message(osc_address, osc_args)
            except Exception:
                logger.exception("%s: error in handle_message", self.name)
                responses = None

            if responses and self._udp_transport:
                for resp_addr, resp_args in responses:
                    resp_data = osc_encode_message(resp_addr, resp_args)
                    if self.has_error_behavior("corrupt_response"):
                        resp_data = _corrupt_bytes(resp_data)
                    self._udp_transport.sendto(resp_data, addr)
                    self.log_protocol("out", resp_data)


class _OSCSimProtocol(asyncio.DatagramProtocol):
    """Internal UDP protocol handler for OSCSimulator."""

    def __init__(self, simulator: OSCSimulator):
        self._simulator = simulator

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._simulator._handle_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("OSC simulator error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.debug("OSC simulator connection lost: %s", exc)


def _corrupt_bytes(data: bytes) -> bytes:
    """Randomly corrupt some bytes for error simulation."""
    import random
    ba = bytearray(data)
    if len(ba) > 0:
        for _ in range(min(3, len(ba))):
            idx = random.randint(0, len(ba) - 1)
            ba[idx] = random.randint(0, 255)
    return bytes(ba)
