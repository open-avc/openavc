"""
UDPSimulator — async UDP server base for device simulators.

Subclasses implement handle_command() to define protocol behavior, same
interface as TCPSimulator. The server itself — socket lifecycle, datagram
routing, network conditions, error modes — is
:class:`~simulator.datagram_server.DatagramServerMixin`, shared with the OSC
base and the YAML auto-generator.

Used for AV devices that communicate via UDP datagrams (video wall
splicers, some lighting controllers, etc.).
"""

from __future__ import annotations

import logging
from abc import abstractmethod

from openavc.simulator.base import BaseSimulator
from openavc.simulator.datagram_server import DatagramServerMixin

logger = logging.getLogger(__name__)


class UDPSimulator(DatagramServerMixin, BaseSimulator):
    """UDP protocol simulator. You implement handle_command(); the framework does the rest."""

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
        await self.start_datagram_server(port)

    async def stop(self) -> None:
        """Stop the UDP server."""
        await self.stop_datagram_server()
