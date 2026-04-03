"""
OpenAVC UDP Transport — async UDP client for broadcast and datagram protocols.

Provides a minimal UDP transport for protocols that use datagrams instead of
streams. Used by Wake-on-LAN and similar broadcast-based protocols.
"""

from __future__ import annotations

import asyncio
from server.utils.logger import get_logger

log = get_logger(__name__)


class UDPTransport:
    """Minimal async UDP transport for broadcast and unicast datagrams."""

    def __init__(self, name: str | None = None) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _UDPProtocol | None = None
        self._name = name or "udp"

    async def open(self, allow_broadcast: bool = True) -> None:
        """Open a UDP socket."""
        loop = asyncio.get_running_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self._name),
            local_addr=("0.0.0.0", 0),
            allow_broadcast=allow_broadcast,
        )
        log.debug(f"[{self._name}] UDP socket opened")

    async def send(self, data: bytes, host: str, port: int) -> None:
        """Send a UDP datagram to a specific host and port."""
        if self._transport is None:
            raise ConnectionError("UDP socket not open")
        try:
            self._transport.sendto(data, (host, port))
        except OSError as e:
            log.error(f"[{self._name}] UDP send failed to {host}:{port}: {e}")
            raise
        log.info(f"[{self._name}] TX: {_format_data(data)} -> {host}:{port}")

    async def broadcast(self, data: bytes, port: int) -> None:
        """Send a UDP broadcast datagram."""
        await self.send(data, "255.255.255.255", port)

    def close(self) -> None:
        """Close the UDP socket."""
        if self._transport:
            self._transport.close()
            self._transport = None
            log.debug(f"[{self._name}] UDP socket closed")

    @property
    def is_open(self) -> bool:
        return self._transport is not None


def _format_data(data: bytes) -> str:
    """Format data for logging — decoded text or hex for binary."""
    try:
        text = data.decode("ascii").strip()
        if text.isprintable():
            return text
    except (UnicodeDecodeError, ValueError):
        pass
    return data.hex()


class _UDPProtocol(asyncio.DatagramProtocol):
    """Internal protocol handler for UDP transport."""

    def __init__(self, name: str = "udp") -> None:
        self._name = name

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        log.info(f"[{self._name}] RX: {_format_data(data)} <- {addr[0]}:{addr[1]}")

    def error_received(self, exc: Exception) -> None:
        log.warning(f"UDP error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            log.debug(f"UDP connection lost: {exc}")
