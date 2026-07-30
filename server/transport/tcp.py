"""
OpenAVC TCP Transport — async TCP client with message framing.

Provides a managed TCP connection with:
- Pluggable frame parser (delimiter, length-prefix, fixed-length, or custom)
- Backward-compatible delimiter mode (auto-creates DelimiterFrameParser)
- Raw mode (no parser, callback on any data)
- Optional TLS/SSL support
- Send queue to prevent command interleaving
- Inter-command delay for slow devices
- send_and_wait for query/response protocols
"""

from __future__ import annotations

import asyncio
import socket as socket_module
import ssl as ssl_module

from server.transport.frame_parsers import DelimiterFrameParser, FrameParser
from server.utils.logger import get_logger
from .types import Callback

log = get_logger(__name__)

# Best-effort TCP keepalive tuning applied when a driver opts in (config
# `tcp_keepalive: true`): start probing after 60s of idle, probe every 10s,
# give up after 3 misses — a dead peer is declared in ~90s instead of the OS
# default (~2 hours). Option constants vary by platform (Linux exposes
# TCP_KEEPIDLE, macOS calls the idle knob TCP_KEEPALIVE, some platforms lack
# the tuning knobs entirely); missing ones are skipped and SO_KEEPALIVE alone
# still applies with OS-default timing.
KEEPALIVE_IDLE_S = 60
KEEPALIVE_PROBE_INTERVAL_S = 10
KEEPALIVE_PROBE_COUNT = 3


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget on_data tasks: surface failures that
    would otherwise vanish as a 'Task exception was never retrieved' GC warning."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in TCP on_data task: %s", exc, exc_info=exc)


class TCPTransport:
    """Async TCP transport with pluggable message framing."""

    # Posted to the response queue to wake a parked send_and_wait when the link
    # drops or is closed, so it fails fast instead of blocking the full timeout.
    _DISCONNECT_SENTINEL = object()

    def __init__(
        self,
        host: str,
        port: int,
        on_data: Callback[[bytes], None],
        on_disconnect: Callback[[], None],
        delimiter: bytes | None,
        timeout: float,
        inter_command_delay: float,
        frame_parser: FrameParser | None = None,
        ssl_context: ssl_module.SSLContext | None = None,
        name: str | None = None,
        local_addr: tuple[str, int] | None = None,
        keepalive: bool = False,
    ):
        self.host = host
        self.port = port
        self._keepalive = keepalive
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._delimiter = delimiter
        self._timeout = timeout
        self._inter_command_delay = inter_command_delay
        self._ssl_context = ssl_context
        self._name = name or f"{host}:{port}"
        self._local_addr = local_addr

        # Resolve frame parser:
        # 1. Explicit frame_parser param takes priority
        # 2. If delimiter is set, auto-create DelimiterFrameParser
        # 3. None = raw mode
        if frame_parser is not None:
            self._frame_parser: FrameParser | None = frame_parser
        elif delimiter is not None:
            self._frame_parser = DelimiterFrameParser(delimiter)
        else:
            self._frame_parser = None
        # Stamp the device label so a parser's buffer-overflow warning can be
        # attributed. Driver-supplied parsers are built in the driver's own
        # _create_frame_parser() with no access to the device id, so the
        # transport that receives one fills it in here.
        if self._frame_parser is not None:
            self._frame_parser.device_label = self._name

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._connected = False
        # Last connect/IO error string, for the connection-fault classifier.
        self._last_error = ""

        # For send_and_wait: a queue to capture the next response. May also
        # carry _DISCONNECT_SENTINEL to wake a parked waiter on disconnect.
        self._response_queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=100)
        self._waiting_for_response = False

        # Strong refs to fire-and-forget async on_data tasks so they aren't
        # garbage-collected mid-flight; cleared by their own done-callback.
        self._bg_tasks: set[asyncio.Task] = set()

    @classmethod
    async def create(
        cls,
        host: str,
        port: int,
        on_data: Callback[[bytes], None],
        on_disconnect: Callback[[], None],
        delimiter: bytes | None = b"\r",
        timeout: float = 5.0,
        inter_command_delay: float = 0.0,
        frame_parser: FrameParser | None = None,
        ssl: bool = False,
        ssl_verify: bool = True,
        name: str | None = None,
        local_addr: tuple[str, int] | None = None,
        keepalive: bool = False,
    ) -> "TCPTransport":
        """
        Factory method. Creates a TCPTransport and connects.

        Args:
            host: Target IP or hostname.
            port: Target TCP port.
            on_data: Called with each complete message (delimiter stripped).
            on_disconnect: Called when connection drops.
            delimiter: Message delimiter bytes. None for raw mode.
                       Ignored when frame_parser is provided.
            timeout: Connection timeout in seconds.
            inter_command_delay: Seconds to wait between sends.
            frame_parser: Optional FrameParser instance. When provided,
                          overrides the delimiter parameter.
            ssl: Enable TLS/SSL connection.
            ssl_verify: Verify server certificate (default True).
            name: Optional label for log messages (e.g. device_id).
                  Defaults to host:port.
            local_addr: Optional (ip, port) tuple to bind the outgoing
                        connection to a specific network adapter.
            keepalive: Enable OS-level TCP keepalive (SO_KEEPALIVE) on the
                       socket, with best-effort aggressive timing (see the
                       KEEPALIVE_* module constants), so a silently-dead peer
                       is detected at the transport layer.

        Returns:
            Connected TCPTransport instance.

        Raises:
            ConnectionError: If connection fails or times out.
        """
        ssl_context = None
        if ssl:
            ssl_context = ssl_module.create_default_context()
            if not ssl_verify:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl_module.CERT_NONE

        transport = cls(
            host, port, on_data, on_disconnect, delimiter, timeout,
            inter_command_delay, frame_parser, ssl_context, name, local_addr,
            keepalive=keepalive,
        )
        await transport._connect()
        return transport

    async def _connect(self) -> None:
        """Open the TCP connection and start the reader loop."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host, self.port, ssl=self._ssl_context,
                    local_addr=self._local_addr,
                ),
                timeout=self._timeout,
            )
            self._connected = True
            if self._keepalive:
                self._enable_keepalive()
            if self._frame_parser is not None:
                self._frame_parser.reset()
            self._reader_task = asyncio.create_task(self._reader_loop())
            log.info(f"TCP connected to {self.host}:{self.port}")
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            self._last_error = str(e) or type(e).__name__
            raise ConnectionError(
                f"Failed to connect to {self.host}:{self.port}: {e}"
            ) from e

    def _enable_keepalive(self) -> None:
        """Turn on SO_KEEPALIVE with best-effort timing tuning.

        Timing knobs are platform-specific: the idle threshold is TCP_KEEPIDLE
        on Linux/Windows but TCP_KEEPALIVE on macOS; TCP_KEEPINTVL/TCP_KEEPCNT
        are absent on some platforms. Whatever isn't available is skipped —
        SO_KEEPALIVE alone still detects a dead peer, just on OS-default
        timing. Failures are logged at debug and never abort the connection.
        """
        sock = (
            self._writer.get_extra_info("socket")
            if self._writer is not None
            else None
        )
        if sock is None:
            log.debug(f"[{self._name}] No raw socket available for keepalive")
            return
        try:
            sock.setsockopt(
                socket_module.SOL_SOCKET, socket_module.SO_KEEPALIVE, 1
            )
        except OSError as e:
            log.debug(f"[{self._name}] Could not enable SO_KEEPALIVE: {e}")
            return
        idle_opt = getattr(socket_module, "TCP_KEEPIDLE", None) or getattr(
            socket_module, "TCP_KEEPALIVE", None
        )
        for opt, value in (
            (idle_opt, KEEPALIVE_IDLE_S),
            (
                getattr(socket_module, "TCP_KEEPINTVL", None),
                KEEPALIVE_PROBE_INTERVAL_S,
            ),
            (getattr(socket_module, "TCP_KEEPCNT", None), KEEPALIVE_PROBE_COUNT),
        ):
            if opt is None:
                continue
            try:
                sock.setsockopt(socket_module.IPPROTO_TCP, opt, value)
            except OSError:
                pass

    async def send(self, data: bytes) -> None:
        """
        Send data to the remote device.

        Uses a lock to serialize sends and respects inter_command_delay.
        """
        if not self._connected or self._writer is None:
            raise ConnectionError("Not connected")

        async with self._send_lock:
            await self._send_unlocked(data)

    async def _send_unlocked(self, data: bytes) -> None:
        """Send data without acquiring the lock. Caller must hold _send_lock."""
        if not self._connected or self._writer is None:
            raise ConnectionError("Not connected")
        try:
            self._writer.write(data)
            await self._writer.drain()
            log.debug(f"[{self._name}] TX: {self._format_data(data)}")
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)
        except (ConnectionError, OSError) as e:
            self._last_error = str(e) or type(e).__name__
            log.error(f"TCP send error: {e}")
            await self._handle_disconnect()
            raise

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        """
        Send data and wait for the next complete response message.

        Holds the send lock through the response wait so concurrent callers
        can't interleave requests on the same connection. This matches AV
        protocol behavior where each connection handles one query at a time.
        """
        async with self._send_lock:
            self._waiting_for_response = True
            while not self._response_queue.empty():
                self._response_queue.get_nowait()
            try:
                await self._send_unlocked(data)
            except (ConnectionError, OSError):
                self._waiting_for_response = False
                raise

            try:
                response = await asyncio.wait_for(
                    self._response_queue.get(), timeout=timeout
                )
                if response is self._DISCONNECT_SENTINEL:
                    raise ConnectionError(
                        f"Connection lost while waiting for response "
                        f"from {self.host}:{self.port}"
                    )
                return response
            except asyncio.TimeoutError:
                log.warning(f"TCP send_and_wait timeout for {self.host}:{self.port}")
                raise
            finally:
                self._waiting_for_response = False

    async def close(self) -> None:
        """Close the connection gracefully."""
        self._connected = False
        self._wake_response_waiter()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        log.info(f"TCP disconnected from {self.host}:{self.port}")

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        """Last connect/IO error string (for the connection-fault classifier)."""
        return self._last_error

    async def _reader_loop(self) -> None:
        """Background task that reads from the socket and delivers messages."""
        try:
            while self._connected and self._reader:
                data = await self._reader.read(4096)
                if not data:
                    # Connection closed by remote
                    break

                if self._frame_parser is None:
                    # Raw mode — deliver all data as-is
                    self._deliver_message(data)
                else:
                    # Frame parser mode — feed data and deliver complete messages
                    for msg in self._frame_parser.feed(data):
                        self._deliver_message(msg)
        except asyncio.CancelledError:
            return
        except (ConnectionError, OSError) as e:
            self._last_error = str(e) or type(e).__name__
            log.debug(f"TCP reader error: {e}")
        finally:
            if self._connected:
                await self._handle_disconnect()

    @staticmethod
    def _format_data(data: bytes) -> str:
        """Format data for logging — decoded text or hex for binary."""
        try:
            text = data.decode("ascii").strip()
            if text.isprintable():
                return text
        except (UnicodeDecodeError, ValueError):
            pass
        return data.hex()

    def _deliver_message(self, data: bytes) -> None:
        """Deliver a complete message to the callback and/or response queue."""
        log.debug(f"[{self._name}] RX: {self._format_data(data)}")

        # If someone is waiting for a response, put it in the queue
        if self._waiting_for_response:
            try:
                self._response_queue.put_nowait(data)
            except asyncio.QueueFull:
                # A burst of >100 frames arrived while a send_and_wait waiter is
                # active (chatty/misbehaving gear, or a slow consumer). Keep the
                # earliest frames — the real response is most likely among them —
                # and drop this overflow frame. Letting QueueFull escape here
                # would unwind the reader loop and wrongly disconnect the device.
                log.debug(f"[{self._name}] response queue full — dropping overflow RX frame")

        # Always call the data callback
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
            log.exception("Error in TCP on_data callback — continuing (transport still connected)")

    def _wake_response_waiter(self) -> None:
        """Wake a parked send_and_wait so it fails fast on disconnect/close
        instead of blocking for the full response timeout."""
        if not self._waiting_for_response:
            return
        try:
            self._response_queue.put_nowait(self._DISCONNECT_SENTINEL)
        except asyncio.QueueFull:
            # The queue already holds frames the waiter will consume and wake
            # on; dropping the sentinel is harmless. Never raise from here.
            pass

    async def _handle_disconnect(self) -> None:
        """Handle an unexpected disconnection."""
        if not self._connected:
            return
        self._connected = False
        self._wake_response_waiter()
        log.warning(f"TCP connection lost to {self.host}:{self.port}")
        try:
            self._on_disconnect()
        except Exception:  # Catch-all: isolates driver callback errors from transport
            log.exception("Error in TCP on_disconnect callback")
