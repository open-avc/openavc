"""
OpenAVC inbound UDP listener registry — shared unicast receive ports for
driver push notifications (``push: {type: udp_listener}``).

Some devices send each change as a UDP packet to an address and port set on
the device itself, with no subscription and no connection: an SNMP agent's
traps go to port 162 on a manager address, a lighting processor's status
triggers go wherever its configuration names. Every device of that kind on
the network sends to the same port, so listeners are shared:

- One UDP socket per **port**, opened by the first subscriber and closed by
  the last (refcounted). Bound to all IPv4 interfaces, so a device reaches it
  on whichever address it was set to. The port is always a fixed number: the
  person setting up the device types it in, so an OS-assigned port would be
  one nobody could enter.
- The socket takes the port **exclusively** (no SO_REUSEADDR/SO_REUSEPORT,
  unlike the multicast registry). A unicast datagram reaches only one of
  several sockets sharing a port, so sharing it with another program would
  lose packets silently; a port already in use fails the bind instead, and
  the driver's warning names it.
- Incoming datagrams are demultiplexed by **source IP**, as the multicast and
  TCP dial-back registries do: a subscription names its device's host and
  receives only what that host sent, so two devices sending to one port each
  feed their own driver instance. A datagram from an address no subscription
  claims is dropped. A loopback-host subscription (the simulator redirect
  points devices at 127.0.0.1) accepts loopback plus every local interface
  address, since a local sender may leave through a real interface.
- Each datagram is delivered whole, as one ``bytes`` object: a datagram is
  already one message, so there is no framing here. The driver splits it on
  its delimiter (``BaseDriver._handle_push_datagram``) or decodes it itself.

A port that can't be bound (in use, or below 1024 without permission) is not
fatal: the driver stays connected and polling covers the device. All calls
must run on the event loop thread.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any, Callable

from openavc.core.device_traffic import RX, record_traffic
from openavc.transport.multicast_listener import resolve_source_ips
from openavc.utils.logger import get_logger

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Surface failures from fire-and-forget callback tasks (mirrors udp.py)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(
            "Unhandled exception in UDP push callback: %s", exc, exc_info=exc
        )


class UdpListenerSubscription:
    """Handle returned by :func:`subscribe`; ``close()`` detaches it."""

    def __init__(
        self,
        listener: "_PortListener",
        source_ips: set[str],
        callback: Callable[[bytes, tuple[str, int]], Any],
        name: str,
    ) -> None:
        self._listener = listener
        # The exact source addresses this subscription accepts.
        self._source_ips = source_ips
        self._callback = callback
        self.name = name
        self._closed = False

    @property
    def port(self) -> int:
        return self._listener.port

    def matches_source(self, src_ip: str) -> bool:
        # No accepted set means no accepted sender. An unresolvable device
        # host must never read as "accept the whole segment".
        if not self._source_ips:
            return False
        if src_ip in self._source_ips:
            return True
        # A loopback-host subscription accepts the whole loopback net: the
        # exact source address of a local sender varies by OS.
        return "127.0.0.1" in self._source_ips and src_ip.startswith("127.")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _registry.unsubscribe(self)


class _PortListener:
    """One shared UDP socket for a port, refcounted by subscription."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.transport: asyncio.DatagramTransport | None = None
        self._sock: socket.socket | None = None
        self.subscriptions: list[UdpListenerSubscription] = []
        # Strong refs to in-flight async callback tasks (GC safety).
        self._bg_tasks: set[asyncio.Task] = set()

    async def open(self) -> None:
        # Built by hand so nothing sets SO_REUSEADDR: the port is ours alone
        # or the bind fails (see the module docstring).
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Windows lets a later socket that asks for SO_REUSEADDR bind a
            # port already held; this refuses it, as Linux and macOS do.
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            sock.setblocking(False)
            sock.bind(("0.0.0.0", self.port))
        except OSError:
            sock.close()
            raise
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _ListenerProtocol(self), sock=sock
        )
        self._sock = sock
        log.debug("UDP push listener opened on port %d", self.port)

    def deliver(self, data: bytes, addr: tuple[str, int]) -> None:
        src_ip = str(addr[0])
        delivered = False
        for sub in list(self.subscriptions):
            if sub._closed or not sub.matches_source(src_ip):
                continue
            delivered = True
            record_traffic(
                sub.name, RX, data, channel="udp_listener",
                meta={"peer": f"{addr[0]}:{addr[1]}", "port": self.port},
            )
            try:
                result = sub._callback(data, addr)
                if asyncio.iscoroutine(result):
                    task = asyncio.create_task(result)
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)
                    task.add_done_callback(_log_task_exception)
            except Exception:
                log.exception("[%s] Error in UDP push callback", sub.name)
        if not delivered:
            # Normal on a shared port (every agent on the network sends traps
            # to 162), so debug only.
            log.debug(
                "UDP push datagram from unmatched source %s:%d on port %d "
                "dropped (%d bytes)",
                src_ip,
                addr[1],
                self.port,
                len(data),
            )

    def remove(self, sub: UdpListenerSubscription) -> None:
        """Detach one subscription (registry lock held by the caller)."""
        if sub in self.subscriptions:
            self.subscriptions.remove(sub)

    def close_transport(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        # The transport closes its socket a loop turn later, and with no
        # address reuse the port stays taken until then: a device that drops
        # and reconnects at once would find its own old socket in the way.
        # close() has already stopped reading, so the socket goes now.
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        log.debug("UDP push listener on port %d closed", self.port)


class _ListenerProtocol(asyncio.DatagramProtocol):
    def __init__(self, listener: _PortListener) -> None:
        self._listener = listener

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._listener.deliver(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.debug(
            "UDP push listener on port %d error: %s", self._listener.port, exc
        )


class _Registry:
    """Process-wide port-listener registry (single event loop)."""

    def __init__(self) -> None:
        self._listeners: dict[int, _PortListener] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        port: int,
        source_ip: str,
        callback: Callable[[bytes, tuple[str, int]], Any],
        name: str,
    ) -> UdpListenerSubscription:
        if isinstance(port, bool) or not isinstance(port, int) or not (0 < port < 65536):
            raise ValueError(f"UDP push listener port must be 1-65535, not {port!r}")
        async with self._lock:
            listener = self._listeners.get(port)
            if listener is None or listener.transport is None:
                listener = _PortListener(port)
                await listener.open()
                self._listeners[port] = listener

            source_ips = await resolve_source_ips(source_ip, name)
            sub = UdpListenerSubscription(listener, source_ips, callback, name)
            listener.subscriptions.append(sub)
            return sub

    async def unsubscribe(self, sub: UdpListenerSubscription) -> None:
        # Same lock as subscribe, so a teardown can't race a new subscription
        # onto a socket that is being closed.
        async with self._lock:
            listener = sub._listener
            listener.remove(sub)
            if not listener.subscriptions:
                if self._listeners.get(listener.port) is listener:
                    del self._listeners[listener.port]
                listener.close_transport()

    async def close_all(self) -> None:
        """Tear down every listener (tests / shutdown)."""
        async with self._lock:
            for listener in list(self._listeners.values()):
                for sub in list(listener.subscriptions):
                    sub._closed = True
                listener.subscriptions.clear()
                listener.close_transport()
            self._listeners.clear()


_registry = _Registry()


async def subscribe(
    port: int,
    source_ip: str,
    callback: Callable[[bytes, tuple[str, int]], Any],
    name: str,
) -> UdpListenerSubscription:
    """Listen on UDP ``port`` and deliver the datagrams ``source_ip`` sends.

    Returns a handle; ``await handle.close()`` detaches it (the shared socket
    is closed when the last subscriber leaves). Raises ``OSError`` when the
    port cannot be bound and ``ValueError`` for a port outside 1-65535. The
    callback receives ``(data, (src_ip, src_port))``, one datagram a call,
    and may be sync or async.
    """
    return await _registry.subscribe(port, source_ip, callback, name)


async def close_all() -> None:
    """Tear down every shared listener (used by tests and engine shutdown)."""
    await _registry.close_all()
