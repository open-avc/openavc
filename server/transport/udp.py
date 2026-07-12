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
import ipaddress
from typing import Callable

from server.utils.logger import get_logger
from .types import Callback

log = get_logger(__name__)


def _expected_source_ip(host: str | None):
    """The address a targeted unicast peer's datagrams must originate from.

    Returns an ``ipaddress`` object to source-filter against, or ``None`` when
    the source can't be pinned — an unset host, a hostname (don't block a
    datagram handler on DNS), or a multicast/broadcast target (the reply's
    unicast source can't be predicted). In those cases datagrams are not
    filtered (fail open), matching prior behaviour.

    A device that pushes over multicast/broadcast still sends from its own
    unicast IP, so filtering against the (unicast) target host passes the
    device's own traffic — solicited replies and unsolicited push alike — while
    dropping spoofed datagrams injected by any other host on the segment.
    """
    if not host:
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None  # hostname — don't do a blocking lookup on the hot path
    if ip.is_multicast or ip.is_unspecified:
        return None
    if isinstance(ip, ipaddress.IPv4Address) and int(ip) == 0xFFFFFFFF:
        return None  # 255.255.255.255 limited broadcast
    return ip


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget on_data tasks: surface failures that
    would otherwise vanish as a 'Task exception was never retrieved' GC warning."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in UDP on_data task: %s", exc, exc_info=exc)


class UDPTransport:
    """Async UDP transport for datagram-based AV protocols."""

    # Posted to the response queue to wake a parked send_and_wait when the
    # socket is closed or lost, so it fails fast instead of blocking the timeout.
    _DISCONNECT_SENTINEL = object()

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        on_data: Callback[[bytes], None] | None = None,
        on_disconnect: Callback[[], None] | None = None,
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
        # Source to accept datagrams from in targeted mode. None = don't filter
        # (ad-hoc/broadcast/hostname target). Guards send_and_wait responses and
        # the on_data matcher against forged datagrams from other hosts.
        self._expected_src = _expected_source_ip(host)

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _UDPProtocol | None = None
        self._connected = False
        self._send_lock = asyncio.Lock()
        self.last_data_received: float = 0.0
        # Last send/socket error string, for the connection-fault classifier.
        # UDP is connectionless, so this is best-effort — an ICMP
        # port-unreachable surfaces via the protocol's error_received.
        self._last_error = ""

        # For send_and_wait: queue to capture the next response. May also
        # carry _DISCONNECT_SENTINEL to wake a parked waiter on close/loss.
        self._response_queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=100)
        self._waiting_for_response = False

        # Strong refs to fire-and-forget async on_data tasks so they aren't
        # garbage-collected mid-flight; cleared by their own done-callback.
        self._bg_tasks: set[asyncio.Task] = set()

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
                on_error=self._record_error,
                on_lost=self._wake_response_waiter,
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
                self._last_error = str(e) or type(e).__name__
                log.error(f"[{self._name}] UDP send failed to {host}:{port}: {e}")
                raise
            log.debug(f"[{self._name}] TX: {_format_data(data)} -> {host}:{port}")
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
                if response is self._DISCONNECT_SENTINEL:
                    raise ConnectionError(
                        f"UDP socket closed while waiting for response "
                        f"from {self.host}:{self.port}"
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
        self._wake_response_waiter()
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

    @property
    def last_error(self) -> str:
        """Last send/socket error string (for the connection-fault classifier)."""
        return self._last_error

    def _record_error(self, exc: Exception) -> None:
        """Record a socket error (e.g. ICMP port-unreachable) for classification."""
        self._last_error = str(exc) or type(exc).__name__

    def _wake_response_waiter(self) -> None:
        """Wake a parked send_and_wait so it fails fast on close/socket loss
        instead of blocking for the full response timeout."""
        if not self._waiting_for_response:
            return
        try:
            self._response_queue.put_nowait(self._DISCONNECT_SENTINEL)
        except asyncio.QueueFull:
            # The queue already holds a datagram the waiter will consume and
            # wake on; dropping the sentinel is harmless. Never raise from here.
            pass

    def _deliver_message(self, data: bytes, addr: tuple[str, int]) -> None:
        """Route an incoming datagram to the response queue and/or callback."""
        import time

        # Drop datagrams from any source other than the targeted peer: on a
        # shared AV LAN another host can spoof a query reply or a state push,
        # driving forged state (or a false "connected"). Rejected before the
        # liveness stamp so a spoofer can't keep a dead device looking alive.
        if self._expected_src is not None:
            try:
                from_target = ipaddress.ip_address(addr[0]) == self._expected_src
            except ValueError:
                from_target = False
            if not from_target:
                log.debug(
                    f"[{self._name}] dropping datagram from unexpected source "
                    f"{addr[0]} (expected {self._expected_src})"
                )
                return

        self.last_data_received = time.monotonic()
        log.debug(f"[{self._name}] RX: {_format_data(data)} <- {addr[0]}:{addr[1]}")

        # If someone is waiting for a response, put it in the queue
        if self._waiting_for_response:
            self._response_queue.put_nowait(data)

        # Always call the data callback if set
        if self._on_data is not None:
            try:
                result = self._on_data(data)
                if asyncio.iscoroutine(result):
                    # Hold a strong ref (GC-safety) and log any failure that an
                    # async handler would otherwise swallow.
                    task = asyncio.create_task(result)
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)
                    task.add_done_callback(_log_task_exception)
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
        on_error: Callable[[Exception], None] | None = None,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        self._name = name
        self._on_data = on_data
        self._on_error = on_error
        self._on_lost = on_lost

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._on_data is not None:
            self._on_data(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.warning(f"[{self._name}] UDP error: {exc}")
        if self._on_error is not None:
            self._on_error(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            log.debug(f"[{self._name}] UDP connection lost: {exc}")
        # Wake any parked send_and_wait so a socket lost mid-query fails fast
        # instead of blocking the full response timeout.
        if self._on_lost is not None:
            self._on_lost()
