"""
DatagramServerMixin — the UDP socket every datagram simulator runs on.

UDP devices, OSC devices and the YAML auto-generator all need the same things
from a datagram server: bind the endpoint, log what arrives, honor the
injected network conditions and error modes, wait out the device's response
delay, and answer whoever asked. Only the step in the middle — what a payload
*means* — differs per protocol, so that is the one method a simulator
overrides (:meth:`DatagramServerMixin.dispatch_datagram`).

It is a mixin rather than a base class because a simulator may not know its
transport until it is constructed: ``YAMLAutoSimulator`` is a ``TCPSimulator``
that also carries this and the HTTP mixin, and starts whichever server the
driver's ``transport:`` field names. Nothing here defines ``start`` or
``stop``, so mixing it in never fights the class it is mixed into.

Mix into a :class:`~simulator.base.BaseSimulator` — the pipeline reads
``log_protocol``, ``has_error_behavior``, ``_delays`` and ``_network_layer``
from it.
"""

from __future__ import annotations

import asyncio
import logging

from openavc.simulator.network_conditions import corrupt_bytes

logger = logging.getLogger(__name__)


class DatagramServerMixin:
    """UDP server lifecycle plus the inbound pipeline datagram simulators share."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._udp_transport: asyncio.DatagramTransport | None = None
        # Sender of the most recent datagram — where an unsolicited push goes.
        # Remembered before the drop check: a dropped datagram still proves
        # someone is out there listening.
        self._last_client_addr: tuple[str, int] | None = None

    # ── Lifecycle ──

    async def start_datagram_server(self, port: int) -> None:
        """Bind the datagram endpoint on ``port``."""
        self._port = port
        loop = asyncio.get_running_loop()
        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: _SimDatagramProtocol(self),
            local_addr=("127.0.0.1", port),
        )
        self._running = True
        logger.info(
            "%s started on %s port %d (driver: %s)",
            self.name, self.transport.upper(), port, self.driver_id,
        )

    async def stop_datagram_server(self) -> None:
        """Close the datagram endpoint."""
        self._running = False
        self._cancel_state_machine_timers()
        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
        logger.info("%s stopped", self.name)

    # ── Sending ──

    def send_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Answer one datagram, applying the ``corrupt_response`` error mode.

        Replies go through here; unsolicited pushes send on the transport
        directly, because a device that garbles its answers still emits clean
        notifications.
        """
        if not self._udp_transport:
            return
        if self.has_error_behavior("corrupt_response"):
            data = corrupt_bytes(data)
        self._udp_transport.sendto(data, addr)
        self.log_protocol("out", data)

    # ── Override point ──

    async def dispatch_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Answer one datagram. Override for protocols that aren't one-shot.

        The default is the one-datagram-in, one-reply-out pipeline every
        command/response device uses. OSC overrides it because a single packet
        can carry a bundle of messages, each with its own answer.
        """
        await self.dispatch_command_datagram(data, addr)

    async def dispatch_command_datagram(
        self, data: bytes, addr: tuple[str, int]
    ) -> None:
        """One datagram in, one ``handle_command()`` reply out."""
        try:
            response = self.handle_command(data)
        except Exception:
            logger.exception("%s: error in handle_command", self.name)
            return
        if response:
            self.send_datagram(response, addr)

    # ── Internal ──

    def _receive_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Entry point from the asyncio protocol: log, filter, then schedule.

        Runs synchronously on the event loop's datagram callback, so the
        delays live in :meth:`_process_datagram` rather than here.
        """
        self._last_client_addr = addr
        self.log_protocol("in", data)

        if self._network_layer and self._network_layer.should_drop(self.device_id):
            return
        if self.has_error_behavior("no_response"):
            return

        asyncio.ensure_future(self._process_datagram(data, addr))

    async def _process_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Wait out network latency and the device's response delay, then dispatch."""
        if self._network_layer:
            await self._network_layer.apply_latency(self.device_id)

        delay = self._delays.get("command_response", 0)
        if delay > 0:
            await asyncio.sleep(delay)

        await self.dispatch_datagram(data, addr)


class _SimDatagramProtocol(asyncio.DatagramProtocol):
    """Routes datagrams from asyncio to the simulator that owns the socket."""

    def __init__(self, simulator: DatagramServerMixin):
        self._simulator = simulator

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._simulator._receive_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("%s simulator error: %s", self._simulator.name, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.debug(
                "%s simulator connection lost: %s", self._simulator.name, exc
            )
