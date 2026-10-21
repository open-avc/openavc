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
import socket
from typing import Callable

from openavc.core.device_traffic import RX, TX, record_traffic
from openavc.transport.wire_log import format_wire_data, wire_held
from openavc.utils.logger import get_logger
from openavc.transport.pacing import sleep_at_least
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


def _is_multicast(host: str) -> bool:
    """True for an IPv4 multicast group (224.0.0.0/4)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and ip.is_multicast


# Where a multicast send's copies go, as the log says it.
_THROUGH_EVERY = "every connected adapter"
_THROUGH_CHOSEN = "the adapter chosen in Reach devices through"


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
        traffic_channel: str = "udp",
    ) -> None:
        """
        Args:
            host: Default target IP for send() and send_and_wait().
                  None for ad-hoc/broadcast-only mode.
            port: Default target port for send() and send_and_wait().
            on_data: Callback for incoming datagrams (targeted mode).
            on_disconnect: Called on socket errors (for BaseDriver compat).
            inter_command_delay: Seconds to wait between sends.
            name: Label for log messages, and the device id the traffic
                  recorder files datagrams under (none recorded without it).
            traffic_channel: The recorder's channel ("osc", "snmp" for the
                  transports built on this one).
        """
        self.host = host
        self.port = port
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._inter_command_delay = inter_command_delay
        self._name = name or "udp"
        self._traffic_name = name or ""
        self._traffic_channel = traffic_channel
        # Source to accept datagrams from in targeted mode. None = don't filter
        # (ad-hoc/broadcast/hostname target). Guards send_and_wait responses and
        # the on_data matcher against forged datagrams from other hosts.
        self._expected_src = _expected_source_ip(host)

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _UDPProtocol | None = None
        # The socket itself, which this transport opens and asyncio wraps: a
        # multicast send to every adapter goes from it directly (send_to).
        self._sock: socket.socket | None = None
        self._local_addr: str | None = None
        self._multicast_sources: list[str] | None = None
        # The chosen adapter a multicast was last not sent for (its name).
        self._unavailable_adapter = ""
        self._connected = False
        self._send_lock = asyncio.Lock()
        self.last_data_received: float = 0.0
        # Datagrams sent, and datagrams accepted from the target, for the life
        # of this socket: the poll loop compares them across a cycle to notice
        # a peer that answers nothing (BaseDriver._poll_loop).
        self.send_count = 0
        self.receive_count = 0
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
                Multicast sends leave through the same adapter: the bound
                address alone does not choose the interface for a multicast
                group, so it is also set as the socket's multicast interface.
                Without one, a multicast send goes out through every
                connected adapter (:meth:`send_to`).
        """
        loop = asyncio.get_running_loop()
        bind_addr = local_addr or "0.0.0.0"
        # Opened here rather than by create_datagram_endpoint so this
        # transport holds the socket: the copies of a multicast send to every
        # adapter go from it directly (send_to says why).
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if allow_broadcast:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((bind_addr, 0))
            sock.setblocking(False)
            if local_addr:
                try:
                    sock.setsockopt(
                        socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                        socket.inet_aton(local_addr),
                    )
                except OSError as exc:  # unicast and broadcast still bound
                    log.debug(f"[{self._name}] Multicast interface not set: {exc}")
            self._transport, self._protocol = await loop.create_datagram_endpoint(
                lambda: _UDPProtocol(
                    name=self._name,
                    on_data=self._deliver_message,
                    on_error=self._record_error,
                    on_lost=self._wake_response_waiter,
                ),
                sock=sock,
            )
        except BaseException:
            sock.close()
            raise
        self._sock = sock
        self._local_addr = local_addr or None
        self._multicast_sources = None
        self._connected = True
        log.debug(f"[{self._name}] UDP socket opened (bound to {bind_addr})")

    async def wait_idle(self) -> None:
        """Return once every write already under way or waiting has gone out,
        the ``inter_command_delay`` after each included (the lock is fair, so
        taking it once waits for all of them). A caller about to decide
        whether to send (a driver's state-gated poll line) waits here first,
        so the answer to a command that just went out has had its chance to
        arrive."""
        async with self._send_lock:
            pass

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
        """Send a UDP datagram to a specific host and port.

        A datagram to a multicast group, from a socket opened with no
        adapter (nothing chosen in Reach devices through), goes out once
        through every connected adapter (``reach_devices.broadcast_sources``),
        the multicast interface set for each copy: left to the OS, a group's
        adapter is the one with the lowest interface metric on Windows, which
        on a computer with WSL or Hyper-V is the virtual switch, and the
        default route's on Linux, so the stream would reach one network or
        none. A copy that cannot be sent is skipped; with none sent, it goes
        once, as the OS picks. With an adapter chosen in Reach devices through
        after the socket opened, it goes through that adapter alone, and not
        at all while that adapter has no address (logged once, not raised: a
        stream sends many times a second). A socket opened bound to the chosen
        adapter's address sends from it, and not at all while that adapter
        isn't connected (logged once too). A limited broadcast is not fanned
        out here: that needs a socket bound per adapter
        (``BaseDriver.send_udp``).
        """
        if self._transport is None:
            raise ConnectionError("UDP socket not open")

        fan_out = await self._fan_out_sources(host)
        if fan_out is None:
            return
        sources, label = fan_out

        async with self._send_lock:
            if self._transport is None:
                raise ConnectionError("UDP socket not open")
            try:
                self._send_fanned_out(data, host, port, sources, label)
            except OSError as e:
                self._last_error = str(e) or type(e).__name__
                log.error(f"[{self._name}] UDP send failed to {host}:{port}: {e}")
                raise
            self.send_count += 1
            if not wire_held(self._name, TX):
                log.debug(
                    f"[{self._name}] TX: {_format_data(data, self._name, TX)} "
                    f"-> {host}:{port}"
                )
            record_traffic(
                self._traffic_name, TX, data, channel=self._traffic_channel,
                meta={"peer": f"{host}:{port}"},
            )
            if self._inter_command_delay > 0:
                await sleep_at_least(self._inter_command_delay)

    async def _fan_out_sources(self, host: str) -> tuple[list[str], str] | None:
        """The adapters a datagram to ``host`` goes out through one copy
        each, and how the log names them: for a multicast group from a socket
        opened with no address, every connected adapter, or with an adapter
        chosen in Reach devices through since the socket opened, that adapter
        alone; otherwise none (one send, as the OS or the bound address
        picks). None: an adapter is chosen and it has no address, so the
        datagram is not sent at all, never through another adapter
        (``reach_devices.pinned``); or the socket was opened bound to the
        chosen adapter's address and that adapter isn't connected."""
        if self._sock is None or not _is_multicast(host):
            return [], ""
        from openavc import reach_devices

        if self._local_addr is not None:
            # Unplugged, the chosen adapter keeps its address for connections,
            # and the OS refuses every datagram sent from it.
            if reach_devices.is_chosen():
                chosen = await self._chosen_adapter()
                if chosen is not None and not chosen.connected and chosen.address == self._local_addr:
                    self._note_not_sent(host, chosen.adapter_name)
                    return None
            if self._multicast_sources == []:
                log.info(f"[{self._name}] Multicast to {host} goes out through {self._local_addr} again")
            self._multicast_sources = None
            return [], ""

        if reach_devices.is_chosen():
            chosen = await self._chosen_adapter()
            if chosen is not None:
                if not chosen.broadcast_source:
                    self._note_not_sent(host, chosen.adapter_name)
                    return None
                return [chosen.broadcast_source], _THROUGH_CHOSEN
        return await reach_devices.kept_broadcast_sources(), _THROUGH_EVERY

    @staticmethod
    async def _chosen_adapter():
        """The adapter chosen in Reach devices through, resolved. From the
        kept answer when there is one, on the event loop: a stream numbers
        each datagram as it builds it (sACN's sequence byte), so a send that
        stopped here would let the next one be built with the same number,
        which the receiver drops. Off the loop only when the adapters must be
        read again."""
        from openavc import reach_devices

        if reach_devices.pinned_is_kept():
            return reach_devices.pinned()
        return await asyncio.to_thread(reach_devices.pinned)

    def _note_not_sent(self, host: str, adapter_name: str) -> None:
        """Say once, not per datagram, that a multicast stream stopped."""
        self._unavailable_adapter = adapter_name
        if self._multicast_sources == []:
            return
        self._multicast_sources = []
        log.warning(
            f"[{self._name}] Multicast to {host} not sent: "
            f"{adapter_name or 'the adapter chosen in Reach devices through'} "
            f"isn't connected"
        )

    def _send_fanned_out(
        self, data: bytes, host: str, port: int, sources: list[str], label: str,
    ) -> None:
        """Send through ``sources`` when there are any, otherwise once as the
        OS or the bound address picks. With every adapter, a datagram no
        copy of which went out still goes once that way; through the chosen
        adapter it does not, which would let the OS pick another: the send
        fails instead."""
        if sources and self._send_per_adapter(data, host, port, sources, label):
            return
        if sources and label == _THROUGH_CHOSEN:
            raise ConnectionError(
                f"Multicast to {host}:{port} through {sources[0]} failed"
            )
        self._transport.sendto(data, (host, port))

    def _send_per_adapter(
        self, data: bytes, host: str, port: int, sources: list[str], label: str,
    ) -> bool:
        """One copy through each of ``sources``, the multicast interface set
        before each, sent from this transport's own socket. Not through
        asyncio's transport: on Windows it queues a datagram while the one
        before is still going out and sends it later, under whatever interface
        is set by then, so every copy could leave through the last adapter. A
        UDP send on a non-blocking socket goes or fails at once. The interface
        goes back to the OS's choice after. Returns whether any copy went."""
        sock = self._sock
        sent: list[str] = []
        for source in sources:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(source),
                )
                sock.sendto(data, (host, port))
                sent.append(source)
            except OSError as exc:
                log.debug(
                    f"[{self._name}] Multicast to {host}:{port} through {source} "
                    f"failed: {exc}"
                )
        try:
            sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("0.0.0.0"),
            )
        except OSError:
            pass
        if not sent:
            self._multicast_sources = None
            return False
        if sent != self._multicast_sources:
            self._multicast_sources = sent
            log.info(
                f"[{self._name}] Multicast to {host}:{port} goes out through "
                f"{', '.join(sent)} ({label})"
            )
        return True

    async def send_and_wait(self, data: bytes, timeout: float = 2.0) -> bytes:
        """Send a datagram and wait for the next response.

        Used for query/response protocols. Sends to the default target
        and waits for any incoming datagram as the response. A multicast
        target goes out once through every connected adapter, or through
        the adapter chosen in Reach devices through, as :meth:`send_to` says;
        while that adapter has no address it is not sent (ConnectionError).

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
        fan_out = await self._fan_out_sources(self.host)
        if fan_out is None:
            from openavc import reach_devices

            raise reach_devices.AdapterUnavailable(self._unavailable_adapter)
        sources, label = fan_out

        async with self._send_lock:
            if self._transport is None:
                raise ConnectionError("UDP socket not open")
            self._waiting_for_response = True
            # Clear any stale responses
            while not self._response_queue.empty():
                self._response_queue.get_nowait()
            try:
                self._send_fanned_out(data, self.host, self.port, sources, label)
            except OSError as e:
                self._waiting_for_response = False
                log.error(
                    f"[{self._name}] UDP send failed to "
                    f"{self.host}:{self.port}: {e}"
                )
                raise
            self.send_count += 1
            if not wire_held(self._name, TX):
                log.info(
                    f"[{self._name}] TX: {_format_data(data, self._name, TX)} -> "
                    f"{self.host}:{self.port}"
                )
            record_traffic(
                self._traffic_name, TX, data, channel=self._traffic_channel,
                meta={"peer": f"{self.host}:{self.port}"},
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
                    await sleep_at_least(self._inter_command_delay)
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
        self._sock = None

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
        self.receive_count += 1
        if not wire_held(self._name, RX):
            log.debug(
                f"[{self._name}] RX: {_format_data(data, self._name, RX)} "
                f"<- {addr[0]}:{addr[1]}"
            )
        record_traffic(
            self._traffic_name, RX, data, channel=self._traffic_channel,
            meta={"peer": f"{addr[0]}:{addr[1]}"},
        )

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


def _format_data(
    data: bytes, device_id: str | None = None, direction: str | None = None,
) -> str:
    """Format a payload for a TX/RX line, masking that device's secrets."""
    return format_wire_data(data, device_id, direction)


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
