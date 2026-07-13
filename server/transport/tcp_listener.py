"""
OpenAVC inbound TCP listener registry — shared dial-back sockets for driver
push notifications (``push: {type: tcp_listener}``).

Some devices (notably PTZ cameras) push state changes by dialing OUT to a
TCP port the controller registered with them, instead of sending frames on
the established control connection. Several device instances — and several
driver types — can share one inbound port, so listeners are shared:

- One TCP server per **port**, opened by the first subscriber and closed by
  the last (refcounted). Bound to all interfaces so the device can reach it
  on whichever address it recorded at registration. Port ``0`` binds an
  OS-assigned ephemeral port (never shared — each such subscription gets its
  own listener); the subscription's ``port`` reports the actual bound port.
- Incoming connections are demultiplexed by **source IP**: a subscription
  names its device's host and only receives data from connections that host
  opened, so two cameras dialing the same port each feed their own driver
  instance. A connection from an address no subscription claims is closed
  immediately. A subscription whose host is loopback (the simulator redirect
  points devices at 127.0.0.1) matches loopback plus any local interface
  address — the simulator's dial-outs may leave through a real interface.
- Each subscription owns a **frame parser factory**: devices in this shape
  typically push binary containers (see ``StructFrameParser``), and framing
  state is per-connection, so a fresh parser instance is created for every
  (connection, subscription) pairing. Without a factory, each read chunk is
  delivered as-is.

A port that can't be bound (in use by another process) is not fatal: the
driver stays connected and polling covers the device; the warning names the
gap. All calls must run on the event loop thread.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from server.transport.frame_parsers import FrameParser
from server.transport.multicast_listener import resolve_source_ips
from server.utils.logger import get_logger

log = get_logger(__name__)

# Per-connection read chunk size. Dial-back notification frames are small
# (Panasonic caps payloads at 504 bytes); 4 KB keeps reads cheap.
_READ_CHUNK = 4096


def _log_task_exception(task: asyncio.Task) -> None:
    """Surface failures from fire-and-forget callback tasks (mirrors udp.py)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(
            "Unhandled exception in TCP push callback: %s", exc, exc_info=exc
        )


class TcpListenerSubscription:
    """Handle returned by :func:`subscribe`; ``close()`` detaches it."""

    def __init__(
        self,
        listener: "_PortListener",
        source_ips: set[str] | None,
        callback: Callable[[bytes, tuple[str, int]], Any],
        frame_parser_factory: Callable[[], FrameParser | None] | None,
        name: str,
    ) -> None:
        self._listener = listener
        # None means "any local source" (loopback-host subscription where the
        # local interface set could not be determined); otherwise the exact
        # source addresses this subscription accepts.
        self._source_ips = source_ips
        self._callback = callback
        self._frame_parser_factory = frame_parser_factory
        self.name = name
        self._closed = False

    @property
    def port(self) -> int:
        """The actual bound port (resolves port-0 ephemeral binds)."""
        return self._listener.port

    def matches_source(self, src_ip: str) -> bool:
        if self._source_ips is None:
            return True
        if src_ip in self._source_ips:
            return True
        # A loopback-host subscription accepts the whole loopback net: the
        # exact source address of a local dial-out varies by OS.
        return "127.0.0.1" in self._source_ips and src_ip.startswith("127.")

    def new_parser(self) -> FrameParser | None:
        if self._frame_parser_factory is None:
            return None
        try:
            return self._frame_parser_factory()
        except Exception:
            log.exception("[%s] Push frame-parser factory failed", self.name)
            return None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _registry.unsubscribe(self)


class _PortListener:
    """One shared TCP server for a port, refcounted by subscription."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.server: asyncio.Server | None = None
        self.subscriptions: list[TcpListenerSubscription] = []
        # Strong refs to in-flight async callback tasks (GC safety).
        self._bg_tasks: set[asyncio.Task] = set()

    async def open(self) -> None:
        self.server = await asyncio.start_server(
            self._handle_connection, "", self.port
        )
        if self.server.sockets:
            # Resolve an ephemeral bind (port 0) to the OS-assigned port.
            self.port = self.server.sockets[0].getsockname()[1]
        log.debug("TCP push listener opened on port %d", self.port)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        src_ip = str(peer[0])
        # Pair each matching subscription with its own parser instance —
        # framing state is per-connection and per-driver.
        matched = [
            (sub, sub.new_parser())
            for sub in list(self.subscriptions)
            if sub.matches_source(src_ip)
        ]
        if not matched:
            log.debug(
                "TCP push connection from unmatched source %s on port %d "
                "closed",
                src_ip,
                self.port,
            )
            writer.close()
            return
        try:
            while True:
                data = await reader.read(_READ_CHUNK)
                if not data:
                    break
                for sub, parser in matched:
                    if sub._closed:
                        continue
                    frames = parser.feed(data) if parser else [data]
                    for frame in frames:
                        self._dispatch(sub, frame, peer)
        except (ConnectionError, OSError) as exc:
            log.debug(
                "TCP push connection from %s on port %d dropped: %s",
                src_ip,
                self.port,
                exc,
            )
        finally:
            writer.close()

    def _dispatch(
        self,
        sub: TcpListenerSubscription,
        frame: bytes,
        addr: tuple[str, int],
    ) -> None:
        try:
            result = sub._callback(frame, addr)
            if asyncio.iscoroutine(result):
                task = asyncio.create_task(result)
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
                task.add_done_callback(_log_task_exception)
        except Exception:
            log.exception("[%s] Error in TCP push callback", sub.name)

    def remove(self, sub: TcpListenerSubscription) -> None:
        """Detach one subscription (registry lock held by the caller)."""
        if sub in self.subscriptions:
            self.subscriptions.remove(sub)

    def close_server(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None
        log.debug("TCP push listener on port %d closed", self.port)


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
        frame_parser_factory: Callable[[], FrameParser | None] | None = None,
    ) -> TcpListenerSubscription:
        async with self._lock:
            # Port 0 is an ephemeral bind: always a fresh listener, keyed by
            # the port the OS actually assigned.
            listener = self._listeners.get(port) if port else None
            if listener is None or listener.server is None:
                listener = _PortListener(port)
                await listener.open()
                self._listeners[listener.port] = listener

            source_ips = await resolve_source_ips(source_ip, name)
            sub = TcpListenerSubscription(
                listener, source_ips, callback, frame_parser_factory, name
            )
            listener.subscriptions.append(sub)
            return sub

    async def unsubscribe(self, sub: TcpListenerSubscription) -> None:
        # Same lock as subscribe, so a teardown can't race a new subscription
        # onto a server that is being closed.
        async with self._lock:
            listener = sub._listener
            listener.remove(sub)
            if not listener.subscriptions:
                if self._listeners.get(listener.port) is listener:
                    del self._listeners[listener.port]
                listener.close_server()

    async def close_all(self) -> None:
        """Tear down every listener (tests / shutdown)."""
        async with self._lock:
            for listener in list(self._listeners.values()):
                for sub in list(listener.subscriptions):
                    sub._closed = True
                listener.subscriptions.clear()
                listener.close_server()
            self._listeners.clear()


_registry = _Registry()


async def subscribe(
    port: int,
    source_ip: str,
    callback: Callable[[bytes, tuple[str, int]], Any],
    name: str,
    frame_parser_factory: Callable[[], FrameParser | None] | None = None,
) -> TcpListenerSubscription:
    """Listen on ``port`` and deliver frames from connections ``source_ip`` opens.

    Returns a handle; ``await handle.close()`` detaches it (the shared server
    is closed when the last subscriber leaves). ``port=0`` binds an ephemeral
    port — read the actual one from ``handle.port``. Each connection's bytes
    run through a fresh parser from ``frame_parser_factory`` (or arrive as raw
    read chunks without one); the callback receives
    ``(frame, (src_ip, src_port))`` and may be sync or async.
    """
    return await _registry.subscribe(
        port, source_ip, callback, name, frame_parser_factory
    )


async def close_all() -> None:
    """Tear down every shared listener (used by tests and engine shutdown)."""
    await _registry.close_all()
