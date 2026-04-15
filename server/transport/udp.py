"""
OpenAVC UDP Transport — async UDP client for datagram protocols.

Supports two modes:

1. **Targeted mode** (host + port): Persistent target for request-response
   protocols like Novastar video wall splicers. Provides send(), send_and_wait(),
   and incoming data callbacks — behaves like TCPTransport but over datagrams.

2. **Ad-hoc mode** (no host/port): For broadcast-only protocols like
   Wake-on-LAN. Use send_to() and broadcast() with explicit addresses.

Each UDP packet is a complete message — no framing or delimiters needed.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from server.utils.logger import get_logger

log = get_logger(__name__)


class UDPTransport:
    """Async UDP transport for datagram-based AV protocols."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        on_data: Callable[[bytes], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        inter_command_delay: float = 0.0,
        name: str | None = None,
    ) -> None:
        """
        Args:
            host: Default target IP for send() and send_and_wait().
                  None for ad-hoc/broadcast-only mode.
            port: Default target port for send() and send_and_wait().
            on_data: Callback for incoming datagrams (targeted mode).
            on_disconnect: Called on socket errors (for BaseDriver compat).
            inter_command_delay: Seconds to wait between sends.
            name: Label for log messages.
        """
        self.host = host
        self.port = port
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._inter_command_delay = inter_command_delay
        self._name = name or "udp"

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _UDPProtocol | None = None
        self._connected = False
        self._send_lock = asyncio.Lock()

        # For send_and_wait: queue to capture the next response
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._waiting_for_response = False

    async def open(
        self,
        allow_broadcast: bool = True,
        local_addr: str | None = None,
    ) -> None:
        """Open a UDP socket and start listening for responses.

        Args:
            allow_broadcast: Allow sending broadcast datagrams.
            local_addr: Optional IP to bind to a specific network adapter.
        """
        loop = asyncio.get_running_loop()
        bind_addr = local_addr or "0.0.0.0"
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(
                name=self._name,
                on_data=self._deliver_message,
            ),
            local_addr=(bind_addr, 0),
            allow_broadcast=allow_broadcast,
        )
        self._connected = True
        log.debug(f"[{self._name}] UDP socket opened (bound to {bind_addr})")

    async def send(self, data: bytes) -> None:
        """Send a UDP datagram to the default target host and port.

        Requires host and port to be set (targeted mode).
        Uses a lock to serialize sends and respects inter_command_delay.
        """
        if not self.host or not self.port:
            raise ConnectionError(
                "UDP send() requires a default host and port. "
                "Use send_to() for ad-hoc sends."
            )
        await self.send_to(data, self.host, self.port)

    async def send_to(self, data: bytes, host: str, port: int) -> None:
        """Send a UDP datagram to a specific host and port."""
        if self._transport is None:
            raise ConnectionError("UDP socket not open")

        async with self._send_lock:
            try:
                self._transport.sendto(data, (host, port))
            except OSError as e:
                log.error(f"[{self._name}] UDP send failed to {host}:{port}: {e}")
                raise
            log.info(f"[{self._name}] TX: {_format_data(data)} -> {host}:{port}")
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)

    async def send_and_wait(self, data: bytes, timeout: float = 2.0) -> bytes:
        """Send a datagram and wait for the next response.

        Used for query/response protocols. Sends to the default target
        and waits for any incoming datagram as the response.

        Args:
            data: The datagram payload to send.
            timeout: Seconds to wait for a response.

        Returns:
            The response datagram bytes.

        Raises:
            asyncio.TimeoutError: If no response arrives within timeout.
            ConnectionError: If host/port not set or socket not open.
        """
        if not self.host or not self.port:
            raise ConnectionError(
                "UDP send_and_wait() requires a default host and port."
            )
        if self._transport is None:
            raise ConnectionError("UDP socket not open")

        async with self._send_lock:
            self._waiting_for_response = True
            # Clear any stale responses
            while not self._response_queue.empty():
                self._response_queue.get_nowait()
            try:
                self._transport.sendto(data, (self.host, self.port))
            except OSError as e:
                self._waiting_for_response = False
                log.error(
                    f"[{self._name}] UDP send failed to "
                    f"{self.host}:{self.port}: {e}"
                )
                raise
            log.info(
                f"[{self._name}] TX: {_format_data(data)} -> "
                f"{self.host}:{self.port}"
            )

        try:
            response = await asyncio.wait_for(
                self._response_queue.get(), timeout=timeout
            )
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)
            return response
        except asyncio.TimeoutError:
            log.warning(
                f"[{self._name}] UDP send_and_wait timeout "
                f"({self.host}:{self.port})"
            )
            raise
        finally:
            self._waiting_for_response = False

    async def broadcast(self, data: bytes, port: int) -> None:
        """Send a UDP broadcast datagram."""
        await self.send_to(data, "255.255.255.255", port)

    async def close(self) -> None:
        """Close the UDP socket."""
        self._connected = False
        if self._transport:
            self._transport.close()
            self._transport = None
            log.debug(f"[{self._name}] UDP socket closed")

    @property
    def connected(self) -> bool:
        """True if the UDP socket is open and ready to send."""
        return self._connected and self._transport is not None

    @property
    def is_open(self) -> bool:
        """Alias for connected (backward compat with ad-hoc usage)."""
        return self._transport is not None

    def _deliver_message(self, data: bytes, addr: tuple[str, int]) -> None:
        """Route an incoming datagram to the response queue and/or callback."""
        log.info(f"[{self._name}] RX: {_format_data(data)} <- {addr[0]}:{addr[1]}")

        # If someone is waiting for a response, put it in the queue
        if self._waiting_for_response:
            self._response_queue.put_nowait(data)

        # Always call the data callback if set
        if self._on_data is not None:
            try:
                result = self._on_data(data)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                log.exception(
                    "Error in UDP on_data callback — "
                    "not fatal for UDP (no connection to lose)"
                )


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

    def __init__(
        self,
        name: str = "udp",
        on_data: Callable[[bytes, tuple[str, int]], None] | None = None,
    ) -> None:
        self._name = name
        self._on_data = on_data

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._on_data is not None:
            self._on_data(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.warning(f"[{self._name}] UDP error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            log.debug(f"[{self._name}] UDP connection lost: {exc}")
