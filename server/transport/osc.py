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

OSC over TCP: pass ``tcp=True`` to back the transport with a TCP
connection framed with SLIP (RFC 1055 double-END) instead of UDP. This
is QLab's reliable, large-reply path — replies come back over the same
connection, so ``listen_port`` is irrelevant in TCP mode. UDP stays the
default; existing UDP OSC drivers are untouched.
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from typing import Any

from server.transport.osc_codec import osc_encode_message
from server.transport.udp import UDPTransport, _expected_source_ip
from server.utils.logger import get_logger
from .types import Callback

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget on_data tasks: surface failures that
    would otherwise vanish as a 'Task exception was never retrieved' GC warning."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in OSC on_data task: %s", exc, exc_info=exc)


class OSCTransport:
    """Async OSC transport over UDP (default) or TCP+SLIP."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        listen_port: int = 0,
        on_data: Callback[[bytes], None] | None = None,
        on_disconnect: Callback[[], None] | None = None,
        inter_command_delay: float = 0.0,
        name: str | None = None,
        tcp: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._listen_port = listen_port
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._inter_command_delay = inter_command_delay
        self._name = name or "osc"
        self._tcp_mode = tcp

        self._udp: UDPTransport | None = None
        self._listen_transport: asyncio.DatagramTransport | None = None
        self._listen_protocol: _OSCListenProtocol | None = None

        # TCP+SLIP mode state.
        self._tcp: Any = None  # TCPTransport, imported lazily
        self._tcp_last_data: float = 0.0

    async def open(self, local_addr: str | None = None) -> None:
        """Open the send socket(s).

        UDP (default): a send socket and optionally a dedicated listen
        socket. TCP+SLIP: a single TCP connection whose replies arrive on
        the same socket.
        """
        if self._tcp_mode:
            await self._open_tcp(local_addr)
            return

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

    async def _open_tcp(self, local_addr: str | None = None) -> None:
        """Open a TCP connection framed with SLIP (RFC 1055) for OSC 1.1."""
        from server.transport.frame_parsers import SlipFrameParser
        from server.transport.tcp import TCPTransport

        if not self._host or not self._port:
            raise ConnectionError(
                f"[{self._name}] OSC-over-TCP requires a host and port"
            )

        self._tcp = await TCPTransport.create(
            host=self._host,
            port=self._port,
            on_data=self._on_tcp_data,
            on_disconnect=self._on_disconnect or (lambda: None),
            delimiter=None,
            frame_parser=SlipFrameParser(),
            inter_command_delay=self._inter_command_delay,
            name=self._name,
            local_addr=(local_addr, 0) if local_addr else None,
        )
        log.info(f"[{self._name}] OSC-over-TCP (SLIP) connected to "
                 f"{self._host}:{self._port}")

    def _on_tcp_data(self, data: bytes) -> Any:
        """Receive a SLIP-deframed OSC packet from the TCP transport.

        The SlipFrameParser has already stripped framing, so ``data`` is a
        raw OSC message. Stamp the receive time (for last_data_received) and
        hand it to the same on_data callback the UDP path uses.
        """
        self._tcp_last_data = time.monotonic()
        if self._on_data is not None:
            return self._on_data(data)
        return None

    async def send(self, data: bytes) -> None:
        """Send raw bytes via the active socket (SLIP-framed in TCP mode)."""
        if self._tcp_mode:
            if self._tcp is None or not self._tcp.connected:
                raise ConnectionError("OSC transport not open")
            from server.transport.frame_parsers import slip_encode
            await self._tcp.send(slip_encode(data))
            return
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

        When a separate listen socket is configured (``listen_port > 0``),
        the device may reply to the dedicated feedback port and never to
        the send socket (e.g. ETC Eos consoles, which emit
        ``/eos/out/...`` continuously to their configured OSC TX port).
        In that case the send-socket ``send_and_wait`` is raced against
        polling the listen socket's last-data timestamp; either path
        counts as a verified connection.
        """
        # TCP+SLIP: a completed TCP handshake already proves the host is
        # listening on the OSC port, which is the reachability signal we
        # need. (QLab won't reply to an unsolicited /info without the
        # connection being set up first anyway.)
        if self._tcp_mode:
            return self._tcp is not None and self._tcp.connected

        if self._udp is None or not self._udp.host or not self._udp.port:
            return False

        probe = osc_encode_message("/info")
        listen_active = self._listen_port > 0
        # Snapshot the listen-socket baseline BEFORE sending so earlier
        # unrelated traffic can't satisfy the race retroactively.
        baseline = (
            getattr(self, "_listen_last_data", 0.0) if listen_active else 0.0
        )

        async def _send_and_wait() -> bool:
            try:
                await self._udp.send_and_wait(probe, timeout=timeout)
                return True
            except (asyncio.TimeoutError, OSError):
                return False

        if not listen_active:
            return await _send_and_wait()

        async def _listen_for_reply() -> bool:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                if getattr(self, "_listen_last_data", 0.0) > baseline:
                    return True
                await asyncio.sleep(0.02)
            return False

        send_task = asyncio.create_task(_send_and_wait())
        listen_task = asyncio.create_task(_listen_for_reply())
        try:
            done, pending = await asyncio.wait(
                {send_task, listen_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return any(task.result() for task in done)
        finally:
            # Defensive: make sure neither task leaks if asyncio.wait
            # raised for any reason.
            for task in (send_task, listen_task):
                if not task.done():
                    task.cancel()

    async def close(self) -> None:
        """Close all sockets."""
        if self._tcp:
            await self._tcp.close()
            self._tcp = None

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
        """Monotonic timestamp of last incoming data (from any socket)."""
        if self._tcp_mode:
            return self._tcp_last_data
        udp_ts = self._udp.last_data_received if self._udp else 0.0
        listen_ts = self._listen_last_data if hasattr(self, "_listen_last_data") else 0.0
        return max(udp_ts, listen_ts)

    @property
    def connected(self) -> bool:
        """True if the active transport is open and ready."""
        if self._tcp_mode:
            return self._tcp is not None and self._tcp.connected
        return self._udp is not None and self._udp.connected

    @property
    def last_error(self) -> str:
        """Last error string from the active transport (for the
        connection-fault classifier)."""
        if self._tcp_mode:
            return self._tcp.last_error if self._tcp is not None else ""
        return self._udp.last_error if self._udp is not None else ""


class _OSCListenProtocol(asyncio.DatagramProtocol):
    """Dedicated listen socket that routes incoming data to the on_data callback."""

    def __init__(
        self,
        on_data: Callback[[bytes], None] | None,
        name: str,
        parent: OSCTransport | None = None,
    ) -> None:
        self._on_data = on_data
        self._name = name
        self._parent = parent
        # Same source filter as the send socket: only accept feedback from the
        # configured device (fail open for hostname/multicast/broadcast targets).
        self._expected_src = _expected_source_ip(parent._host if parent else None)
        # Strong refs to fire-and-forget async on_data tasks so they aren't
        # garbage-collected mid-flight; cleared by their own done-callback.
        self._bg_tasks: set[asyncio.Task] = set()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        import time
        if self._expected_src is not None:
            try:
                from_target = ipaddress.ip_address(addr[0]) == self._expected_src
            except ValueError:
                from_target = False
            if not from_target:
                log.debug(
                    f"[{self._name}] dropping OSC feedback from unexpected "
                    f"source {addr[0]} (expected {self._expected_src})"
                )
                return
        if self._parent is not None:
            self._parent._listen_last_data = time.monotonic()
        log.debug(f"[{self._name}] RX: ({len(data)} bytes) <- {addr[0]}:{addr[1]}")
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
                log.exception("Error in OSC listen on_data callback")

    def error_received(self, exc: Exception) -> None:
        log.warning(f"[{self._name}] OSC listen error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            log.debug(f"[{self._name}] OSC listen socket closed: {exc}")
