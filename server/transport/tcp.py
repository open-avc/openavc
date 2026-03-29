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
import ssl as ssl_module
from typing import Callable

from server.transport.frame_parsers import DelimiterFrameParser, FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)


class TCPTransport:
    """Async TCP transport with pluggable message framing."""

    def __init__(
        self,
        host: str,
        port: int,
        on_data: Callable[[bytes], None],
        on_disconnect: Callable[[], None],
        delimiter: bytes | None,
        timeout: float,
        inter_command_delay: float,
        frame_parser: FrameParser | None = None,
        ssl_context: ssl_module.SSLContext | None = None,
        name: str | None = None,
    ):
        self.host = host
        self.port = port
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._delimiter = delimiter
        self._timeout = timeout
        self._inter_command_delay = inter_command_delay
        self._ssl_context = ssl_context
        self._name = name or f"{host}:{port}"

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

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._connected = False

        # For send_and_wait: a queue to capture the next response
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._waiting_for_response = False

    @classmethod
    async def create(
        cls,
        host: str,
        port: int,
        on_data: Callable[[bytes], None],
        on_disconnect: Callable[[], None],
        delimiter: bytes | None = b"\r",
        timeout: float = 5.0,
        inter_command_delay: float = 0.0,
        frame_parser: FrameParser | None = None,
        ssl: bool = False,
        ssl_verify: bool = True,
        name: str | None = None,
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
            inter_command_delay, frame_parser, ssl_context, name,
        )
        await transport._connect()
        return transport

    async def _connect(self) -> None:
        """Open the TCP connection and start the reader loop."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host, self.port, ssl=self._ssl_context,
                ),
                timeout=self._timeout,
            )
            self._connected = True
            if self._frame_parser is not None:
                self._frame_parser.reset()
            self._reader_task = asyncio.create_task(self._reader_loop())
            log.info(f"TCP connected to {self.host}:{self.port}")
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            raise ConnectionError(
                f"Failed to connect to {self.host}:{self.port}: {e}"
            ) from e

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
            log.info(f"[{self._name}] TX: {self._format_data(data)}")
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)
        except (ConnectionError, OSError) as e:
            log.error(f"TCP send error: {e}")
            await self._handle_disconnect()
            raise

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        """
        Send data and wait for the next complete response message.

        Useful for query/response protocols (e.g., PJLink status queries).
        """
        async with self._send_lock:
            # Atomically: set flag, clear queue, send — all under lock
            # to prevent responses from arriving between operations
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
            return response
        except asyncio.TimeoutError:
            log.warning(f"TCP send_and_wait timeout for {self.host}:{self.port}")
            raise
        finally:
            self._waiting_for_response = False

    async def close(self) -> None:
        """Close the connection gracefully."""
        self._connected = False
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
        log.info(f"[{self._name}] RX: {self._format_data(data)}")

        # If someone is waiting for a response, put it in the queue
        if self._waiting_for_response:
            self._response_queue.put_nowait(data)

        # Always call the data callback
        try:
            result = self._on_data(data)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception:  # Catch-all: isolates driver callback errors from transport
            log.exception("Error in TCP on_data callback — triggering disconnect for recovery")
            asyncio.get_running_loop().create_task(self._handle_disconnect())

    async def _handle_disconnect(self) -> None:
        """Handle an unexpected disconnection."""
        if not self._connected:
            return
        self._connected = False
        log.warning(f"TCP connection lost to {self.host}:{self.port}")
        try:
            self._on_disconnect()
        except Exception:  # Catch-all: isolates driver callback errors from transport
            log.exception("Error in TCP on_disconnect callback")
