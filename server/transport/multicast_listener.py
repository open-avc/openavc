"""
OpenAVC multicast listener registry — shared inbound UDP sockets for driver
push notifications (``push: {type: multicast}``).

Some devices (automixers, DSPs) multicast state-change frames to a group:port
that every controller on the segment joins, instead of pushing them on the
established control connection. Several device instances — and several driver
types — may need the same group/port, so sockets are shared:

- One UDP socket per **port**, opened by the first subscriber and closed by
  the last. Bound to INADDR_ANY with address/port reuse so it coexists with
  other listeners (and other processes) on the same port.
- Multicast **groups** are joined on that socket per interface, refcounted per
  group, via the shared discovery join helper — which works on hosts whose
  routing table has no multicast route and honors the
  ``network.control_interface`` pin.
- Incoming datagrams are demultiplexed by **source IP**: a subscription names
  its device's host and only receives frames from that host, so two devices
  multicasting to the same group each feed their own driver instance. A
  subscription whose host is loopback (the simulator redirect points devices
  at 127.0.0.1) instead matches loopback plus any local interface address —
  the simulator's frames leave through a real interface.

A failed group join is not fatal: the subscription is kept (polling still
covers the device) and a warning names the gap. All calls must run on the
event loop thread.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
from typing import Any, Callable

# is_multicast_group lives in the shared driver-contract module (the
# definition validator needs it too); re-exported here for its
# transport-side callers.
from server.drivers.spec import is_multicast_group as is_multicast_group
from server.utils.logger import get_logger

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Surface failures from fire-and-forget callback tasks (mirrors udp.py)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(
            "Unhandled exception in multicast push callback: %s", exc, exc_info=exc
        )


class MulticastSubscription:
    """Handle returned by :func:`subscribe`; ``close()`` detaches it."""

    def __init__(
        self,
        listener: "_PortListener",
        group: str,
        source_ips: set[str] | None,
        callback: Callable[[bytes, tuple[str, int]], Any],
        name: str,
    ) -> None:
        self._listener = listener
        self.group = group
        # None means "any local source" (loopback-host subscription where the
        # local interface set could not be determined); otherwise the exact
        # source addresses this subscription accepts.
        self._source_ips = source_ips
        self._callback = callback
        self.name = name
        self._closed = False

    @property
    def port(self) -> int:
        return self._listener.port

    def matches_source(self, src_ip: str) -> bool:
        if self._source_ips is None:
            return True
        if src_ip in self._source_ips:
            return True
        # A loopback-host subscription accepts the whole loopback net: the
        # exact source of a looped-back multicast datagram varies by OS.
        return "127.0.0.1" in self._source_ips and src_ip.startswith("127.")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _registry.unsubscribe(self)


class _PortListener:
    """One shared UDP socket for a port, with refcounted group joins."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.transport: asyncio.DatagramTransport | None = None
        self._sock: socket.socket | None = None
        # group -> (refcount, interface IPs the join succeeded on)
        self.groups: dict[str, tuple[int, list[str]]] = {}
        self.subscriptions: list[MulticastSubscription] = []
        # Strong refs to in-flight async callback tasks (GC safety).
        self._bg_tasks: set[asyncio.Task] = set()

    async def open(self) -> None:
        from server.discovery.multicast import set_shared_port_reuse

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            set_shared_port_reuse(sock)
            sock.setblocking(False)
            sock.bind(("", self.port))
        except OSError:
            sock.close()
            raise
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _ListenerProtocol(self), sock=sock
        )
        self._sock = sock
        log.debug("Multicast listener opened on port %d", self.port)

    def join_group(self, group: str, control_ip: str, name: str) -> None:
        """Join ``group`` (refcounted); warn when no interface join succeeds."""
        if group in self.groups:
            count, joined = self.groups[group]
            self.groups[group] = (count + 1, joined)
            return
        from server.discovery.multicast import join_group_on_interfaces

        joined = join_group_on_interfaces(self._sock, group, control_ip=control_ip)
        self.groups[group] = (1, joined)
        if not joined:
            log.warning(
                "[%s] Could not join multicast group %s on any interface — "
                "push notifications will not be received (polling still "
                "covers the device)",
                name,
                group,
            )
        else:
            log.info(
                "[%s] Joined multicast group %s:%d on %s",
                name,
                group,
                self.port,
                ", ".join(joined),
            )

    def _leave_group(self, group: str) -> None:
        _count, joined = self.groups.pop(group, (0, []))
        if self._sock is None:
            return
        from server.discovery.multicast import ANY_INTERFACE

        for iface in joined:
            try:
                mreq = struct.pack(
                    "4s4s",
                    socket.inet_aton(group),
                    socket.inet_aton(
                        "0.0.0.0" if iface == ANY_INTERFACE else iface
                    ),
                )
                self._sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq
                )
            except OSError:
                # Best-effort; the membership dies with the socket anyway.
                pass

    def remove(self, sub: MulticastSubscription) -> None:
        """Detach one subscription (registry lock held by the caller)."""
        if sub in self.subscriptions:
            self.subscriptions.remove(sub)
        count, joined = self.groups.get(sub.group, (0, []))
        if count <= 1:
            self._leave_group(sub.group)
        else:
            self.groups[sub.group] = (count - 1, joined)

    def deliver(self, data: bytes, addr: tuple[str, int]) -> None:
        src_ip = addr[0]
        delivered = False
        for sub in list(self.subscriptions):
            if not sub.matches_source(src_ip):
                continue
            delivered = True
            try:
                result = sub._callback(data, addr)
                if asyncio.iscoroutine(result):
                    task = asyncio.create_task(result)
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)
                    task.add_done_callback(_log_task_exception)
            except Exception:
                log.exception(
                    "[%s] Error in multicast push callback", sub.name
                )
        if not delivered:
            log.debug(
                "Multicast datagram from unmatched source %s:%d on port %d "
                "dropped (%d bytes)",
                src_ip,
                addr[1],
                self.port,
                len(data),
            )

    def close_transport(self) -> None:
        for group in list(self.groups):
            self._leave_group(group)
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self._sock = None
        log.debug("Multicast listener on port %d closed", self.port)


class _ListenerProtocol(asyncio.DatagramProtocol):
    def __init__(self, listener: _PortListener) -> None:
        self._listener = listener

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._listener.deliver(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.debug(
            "Multicast listener on port %d error: %s", self._listener.port, exc
        )


class _Registry:
    """Process-wide port-listener registry (single event loop)."""

    def __init__(self) -> None:
        self._listeners: dict[int, _PortListener] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        group: str,
        port: int,
        source_ip: str,
        callback: Callable[[bytes, tuple[str, int]], Any],
        name: str,
    ) -> MulticastSubscription:
        async with self._lock:
            listener = self._listeners.get(port)
            if listener is None or listener.transport is None:
                listener = _PortListener(port)
                await listener.open()
                self._listeners[port] = listener

            from server.system_config import get_system_config

            control_ip = get_system_config().get("network", "control_interface")
            listener.join_group(group, control_ip or "", name)

            source_ips = await resolve_source_ips(source_ip, name)
            sub = MulticastSubscription(listener, group, source_ips, callback, name)
            listener.subscriptions.append(sub)
            return sub

    async def unsubscribe(self, sub: MulticastSubscription) -> None:
        # Same lock as subscribe, so a teardown can't race a new subscription
        # onto a transport that is being closed.
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


async def resolve_source_ips(source_ip: str, name: str) -> set[str] | None:
    """Compute the accepted source-address set for a subscription.

    Public because the HTTP push listener (``transport/http_listener.py``)
    gates inbound callbacks by the same source semantics.

    A loopback host means the device is redirected to the local simulator, so
    the frames' real source is whichever local interface the simulator's
    sender socket used — accept loopback plus every local interface address.
    A hostname is resolved once at subscribe time; resolution failure falls
    back to the literal (never matches, but the warning tells the user why).
    Shared by the multicast and inbound-TCP push listener registries.
    """
    host = (source_ip or "").strip()
    if not host:
        log.warning(
            "[%s] Push subscription has no device host; no frames will match",
            name,
        )
        return set()
    if host in ("localhost",) or host.startswith("127."):
        ips = {"127.0.0.1", host if host != "localhost" else "127.0.0.1"}
        try:
            from server.discovery.network_scanner import get_interface_ips

            ips.update(get_interface_ips())
        except Exception:
            # Interface enumeration failed — accept any local source rather
            # than silently breaking simulation.
            return None
        return ips
    try:
        ipaddress.IPv4Address(host)
        return {host}
    except (ipaddress.AddressValueError, ValueError):
        pass
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET)
        resolved = {info[4][0] for info in infos}
        if resolved:
            return resolved
    except OSError as exc:
        log.warning(
            "[%s] Could not resolve device host %r for push source matching: %s",
            name,
            host,
            exc,
        )
    return {host}


_registry = _Registry()


async def subscribe(
    group: str,
    port: int,
    source_ip: str,
    callback: Callable[[bytes, tuple[str, int]], Any],
    name: str,
) -> MulticastSubscription:
    """Join ``group``:``port`` and deliver datagrams from ``source_ip``.

    Returns a handle; ``await handle.close()`` detaches it (the shared socket
    and group membership are released when the last subscriber leaves). The
    callback receives ``(data, (src_ip, src_port))`` and may be sync or async.
    """
    return await _registry.subscribe(group, port, source_ip, callback, name)


async def close_all() -> None:
    """Tear down every shared listener (used by tests and engine shutdown)."""
    await _registry.close_all()
