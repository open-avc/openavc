"""
OSCSimulator — async UDP server base for OSC device simulators.

Parallel to TCPSimulator and HTTPSimulator. Subclasses implement
handle_message() to define device behavior; the OSC decode/encode loop and
the UDP server underneath it (:class:`OSCDispatchMixin` over
:class:`~openavc.simulator.datagram_server.DatagramServerMixin`) are shared with the
YAML auto-generator, which speaks the same protocol from a driver definition
instead of Python.

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

import logging
from abc import abstractmethod
from typing import Any

from openavc.simulator.base import BaseSimulator
from openavc.simulator.datagram_server import DatagramServerMixin

logger = logging.getLogger(__name__)


class OSCDispatchMixin(DatagramServerMixin):
    """OSC decoding and encoding over the shared datagram server.

    Mixed into both :class:`OSCSimulator` and the YAML auto-generator, which
    differ only in where their ``handle_message`` comes from — Python in one
    case, the driver's response address map in the other.
    """

    async def dispatch_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """An OSC packet can carry a bundle, so answer each message in it."""
        await self.dispatch_osc_datagram(data, addr)

    async def dispatch_osc_datagram(
        self, data: bytes, addr: tuple[str, int]
    ) -> None:
        """Decode one OSC packet, answer every message it carries."""
        from openavc.transport.osc_codec import osc_decode_bundle, osc_encode_message

        try:
            messages = osc_decode_bundle(data)
        except Exception as e:
            logger.warning("%s: OSC decode error: %s", self.name, e)
            return

        for osc_address, osc_args in messages:
            try:
                responses = self.handle_message(osc_address, osc_args)
            except Exception:
                logger.exception(
                    "%s: error handling OSC %s", self.name, osc_address
                )
                continue
            for resp_address, resp_args in responses or ():
                self.send_datagram(
                    osc_encode_message(resp_address, resp_args), addr
                )

    async def push_message(
        self, address: str, args: list[tuple[str, Any]] | None = None
    ) -> None:
        """Send an unsolicited OSC message to the last known client."""
        if not self._udp_transport or not self._last_client_addr:
            return
        from openavc.transport.osc_codec import osc_encode_message
        data = osc_encode_message(address, args)
        self._udp_transport.sendto(data, self._last_client_addr)
        self.log_protocol("out", data)


class OSCSimulator(OSCDispatchMixin, BaseSimulator):
    """OSC protocol simulator over UDP.

    Subclasses implement handle_message(address, args) which receives
    decoded OSC messages and returns a list of (address, args) response
    tuples to send back to the client.
    """

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
        await self.start_datagram_server(port)

    async def stop(self) -> None:
        """Stop the UDP/OSC server."""
        await self.stop_datagram_server()
