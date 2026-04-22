"""
OSC Transport — async UDP transport with OSC message encoding/decoding.

Wraps UDPTransport and adds:
- send_message(address, args) for sending encoded OSC messages
- Optional separate listen port for devices that send feedback to a
  different port (e.g., some lighting desks, custom show control rigs)
- Raw send(bytes) passthrough for backward compat

Most OSC devices (Behringer X32, QLab, ETC Eos) reply to the sender's
port, so listen_port=0 (default) is usually correct. Set listen_port
only when the device documentation specifies a separate feedback port.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from server.transport.osc_codec import osc_encode_message
from server.transport.udp import UDPTransport
from server.utils.logger import get_logger

log = get_logger(__name__)


class OSCTransport:
    """Async OSC transport over UDP with optional dual-socket support."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        listen_port: int = 0,
        on_data: Callable[[bytes], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        inter_command_delay: float = 0.0,
        name: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._listen_port = listen_port
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._inter_command_delay = inter_command_delay
        self._name = name or "osc"

        self._udp: UDPTransport | None = None
        self._listen_transport: asyncio.DatagramTransport | None = None
        self._listen_protocol: _OSCListenProtocol | None = None

    async def open(self, local_addr: str | None = None) -> None:
        """Open the send socket and optionally a dedicated listen socket."""
        bind_addr = local_addr or "0.0.0.0"

        self._udp = UDPTransport(
            host=self._host,
            port=self._port,
            on_data=self._on_data,
            on_disconnect=self._on_disconnect,
            inter_command_delay=self._inter_command_delay,
            name=self._name,
        )
        await self._udp.open(local_addr=bind_addr)

        if self._listen_port > 0:
            loop = asyncio.get_running_loop()
            self._listen_last_data: float = 0.0
            self._listen_transport, self._listen_protocol = (
                await loop.create_datagram_endpoint(
                    lambda: _OSCListenProtocol(self._on_data, self._name, parent=self),
                    local_addr=(bind_addr, self._listen_port),
                )
            )
            log.info(
                f"[{self._name}] OSC listen socket on port {self._listen_port}"
            )

    async def send(self, data: bytes) -> None:
        """Send raw bytes via the send socket."""
        if self._udp is None:
            raise ConnectionError("OSC transport not open")
        await self._udp.send(data)

    async def send_message(
        self, address: str, args: list[tuple[str, Any]] | None = None
    ) -> None:
        """Encode an OSC message and send it."""
        data = osc_encode_message(address, args)
        await self.send(data)

    async def verify(self, timeout: float = 3.0) -> bool:
        """Verify the remote OSC device is reachable.

        Sends an OSC /info query (no args) and waits for any UDP response.
        Most OSC devices respond to /info with console metadata. Returns
        True if any datagram arrives back, False on timeout.
        """
        if self._udp is None or not self._udp.host or not self._udp.port:
            return False

        probe = osc_encode_message("/info")

        self._udp._waiting_for_response = True
        while not self._udp._response_queue.empty():
            self._udp._response_queue.get_nowait()

        try:
            self._udp._transport.sendto(probe, (self._udp.host, self._udp.port))
        except OSError:
            self._udp._waiting_for_response = False
            return False

        try:
            await asyncio.wait_for(
                self._udp._response_queue.get(), timeout=timeout
            )
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._udp._waiting_for_response = False

    async def close(self) -> None:
        """Close all sockets."""
        if self._listen_transport:
            self._listen_transport.close()
            self._listen_transport = None
            self._listen_protocol = None

        if self._udp:
            await self._udp.close()
            self._udp = None

        log.debug(f"[{self._name}] OSC transport closed")

    @property
    def last_data_received(self) -> float:
        """Monotonic timestamp of last incoming data (from either socket)."""
        udp_ts = self._udp.last_data_received if self._udp else 0.0
        listen_ts = self._listen_last_data if hasattr(self, "_listen_last_data") else 0.0
        return max(udp_ts, listen_ts)

    @property
    def connected(self) -> bool:
        """True if the send socket is open and ready."""
        return self._udp is not None and self._udp.connected


class _OSCListenProtocol(asyncio.DatagramProtocol):
    """Dedicated listen socket that routes incoming data to the on_data callback."""

    def __init__(
        self,
        on_data: Callable[[bytes], None] | None,
        name: str,
        parent: OSCTransport | None = None,
    ) -> None:
        self._on_data = on_data
        self._name = name
        self._parent = parent

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        import time
        if self._parent is not None:
            self._parent._listen_last_data = time.monotonic()
        log.info(f"[{self._name}] RX: ({len(data)} bytes) <- {addr[0]}:{addr[1]}")
        if self._on_data is not None:
            try:
                result = self._on_data(data)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                log.exception("Error in OSC listen on_data callback")

    def error_received(self, exc: Exception) -> None:
        log.warning(f"[{self._name}] OSC listen error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            log.debug(f"[{self._name}] OSC listen socket closed: {exc}")
