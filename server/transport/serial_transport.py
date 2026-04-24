"""
OpenAVC Serial Transport — async serial port client with message framing.

Provides a managed serial connection with:
- Pluggable frame parser (same as TCP transport)
- Graceful simulation fallback when pyserial-asyncio is unavailable
- Same interface as TCPTransport: send(), send_and_wait(), close(), connected

Simulation mode is automatically activated when pyserial-asyncio is not
installed or when a port path starts with "SIM:" — useful for development
on machines without serial hardware.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from server.transport.frame_parsers import DelimiterFrameParser, FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)

# Try to import pyserial-asyncio; fall back gracefully
try:
    import serial_asyncio  # type: ignore[import-untyped]

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


class SerialTransport:
    """Async serial transport with pluggable message framing."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        on_data: Callable[[bytes], None],
        on_disconnect: Callable[[], None],
        frame_parser: FrameParser | None = None,
        delimiter: bytes | None = b"\r",
        timeout: float = 5.0,
        inter_command_delay: float = 0.0,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        simulate: bool = False,
        name: str | None = None,
    ):
        self.port = port
        self.baudrate = baudrate
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._timeout = timeout
        self._inter_command_delay = inter_command_delay
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._name = name or port

        # Determine if we should simulate
        self._simulate = simulate or port.startswith("SIM:") or not HAS_SERIAL

        # Resolve frame parser (same logic as TCP)
        if frame_parser is not None:
            self._frame_parser: FrameParser | None = frame_parser
        elif delimiter is not None:
            self._frame_parser = DelimiterFrameParser(delimiter)
        else:
            self._frame_parser = None

        self._reader: Any = None
        self._writer: Any = None
        self._reader_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._connected = False

        # For send_and_wait
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._waiting_for_response = False

        # Simulation state
        self._sim_rx_queue: asyncio.Queue[bytes] = asyncio.Queue()

    @classmethod
    async def create(
        cls,
        port: str,
        baudrate: int = 9600,
        on_data: Callable[[bytes], None] = lambda d: None,
        on_disconnect: Callable[[], None] = lambda: None,
        frame_parser: FrameParser | None = None,
        delimiter: bytes | None = b"\r",
        timeout: float = 5.0,
        inter_command_delay: float = 0.0,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        simulate: bool = False,
        name: str | None = None,
    ) -> "SerialTransport":
        """
        Factory method. Creates a SerialTransport and opens the port.

        Args:
            port: Serial port path (e.g., "COM3", "/dev/ttyUSB0").
                  Use "SIM:<name>" to force simulation mode.
            baudrate: Baud rate (default 9600).
            on_data: Called with each complete message.
            on_disconnect: Called when the port is lost.
            frame_parser: Optional FrameParser instance.
            delimiter: Fallback delimiter if no frame_parser given.
            timeout: Connection timeout in seconds.
            inter_command_delay: Seconds to wait between sends.
            bytesize: Data bits (5, 6, 7, 8). Default 8.
            parity: Parity ('N', 'E', 'O'). Default 'N'.
            stopbits: Stop bits (1, 1.5, 2). Default 1.
            simulate: Force simulation mode.
            name: Optional label for log messages (e.g. device_id).
                  Defaults to port path.

        Returns:
            Connected SerialTransport instance.
        """
        transport = cls(
            port, baudrate, on_data, on_disconnect, frame_parser,
            delimiter, timeout, inter_command_delay, bytesize, parity,
            stopbits, simulate, name,
        )
        await transport._connect()
        return transport

    async def _connect(self) -> None:
        """Open the serial port and start the reader loop."""
        if self._simulate:
            self._connected = True
            if self._frame_parser is not None:
                self._frame_parser.reset()
            self._reader_task = asyncio.create_task(self._sim_reader_loop())
            log.info(f"Serial SIMULATED connection on {self.port} @ {self.baudrate}")
            return

        if not HAS_SERIAL:
            raise ImportError(
                "pyserial-asyncio is required for real serial connections. "
                "Install it with: pip install pyserial-asyncio"
            )

        try:
            self._reader, self._writer = await asyncio.wait_for(
                serial_asyncio.open_serial_connection(
                    url=self.port,
                    baudrate=self.baudrate,
                    bytesize=self._bytesize,
                    parity=self._parity,
                    stopbits=self._stopbits,
                ),
                timeout=self._timeout,
            )
            self._connected = True
            if self._frame_parser is not None:
                self._frame_parser.reset()
            self._reader_task = asyncio.create_task(self._reader_loop())
            log.info(f"Serial connected to {self.port} @ {self.baudrate}")
        except (OSError, asyncio.TimeoutError) as e:
            raise ConnectionError(
                f"Failed to open serial port {self.port}: {e}"
            ) from e

    async def send(self, data: bytes) -> None:
        """Send data to the serial port."""
        if not self._connected:
            raise ConnectionError("Not connected")

        async with self._send_lock:
            await self._send_unlocked(data)

    async def _send_unlocked(self, data: bytes) -> None:
        """Send data without acquiring the lock. Caller must hold _send_lock."""
        if not self._connected:
            raise ConnectionError("Not connected")

        if self._simulate:
            log.info(f"[{self._name}] TX: {self._format_data(data)}")
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)
            return

        try:
            self._writer.write(data)
            await self._writer.drain()
            log.info(f"[{self._name}] TX: {self._format_data(data)}")
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)
        except (OSError, ConnectionError) as e:
            log.error(f"Serial send error: {e}")
            await self._handle_disconnect()
            raise

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        """Send data and wait for the next complete response."""
        async with self._send_lock:
            # Atomically: set flag, clear queue, send — all under lock
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
                self._response_queue.get(), timeout=timeout,
            )
            return response
        except asyncio.TimeoutError:
            log.warning(f"Serial send_and_wait timeout on {self.port}")
            raise
        finally:
            self._waiting_for_response = False

    async def close(self) -> None:
        """Close the serial port gracefully."""
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._writer and not self._simulate:
            try:
                self._writer.close()
                if hasattr(self._writer, "wait_closed"):
                    await self._writer.wait_closed()
            except (OSError, AttributeError):
                pass
        log.info(f"Serial disconnected from {self.port}")

    @property
    def connected(self) -> bool:
        return self._connected

    # --- Simulation helpers ---

    def sim_receive(self, data: bytes) -> None:
        """Inject data into the simulated serial port (for testing)."""
        if self._simulate and self._connected:
            self._sim_rx_queue.put_nowait(data)

    # --- Internal ---

    async def _reader_loop(self) -> None:
        """Background reader for real serial ports."""
        try:
            while self._connected and self._reader:
                data = await self._reader.read(4096)
                if not data:
                    break
                self._process_data(data)
        except asyncio.CancelledError:
            return
        except (OSError, ConnectionError) as e:
            log.debug(f"Serial reader error: {e}")
        finally:
            if self._connected:
                await self._handle_disconnect()

    async def _sim_reader_loop(self) -> None:
        """Background reader for simulated serial port."""
        try:
            while self._connected:
                data = await self._sim_rx_queue.get()
                self._process_data(data)
        except asyncio.CancelledError:
            return

    def _process_data(self, data: bytes) -> None:
        """Feed data through frame parser and deliver messages."""
        if self._frame_parser is None:
            self._deliver_message(data)
        else:
            for msg in self._frame_parser.feed(data):
                self._deliver_message(msg)

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
        """Deliver a complete message to callback and/or response queue."""
        log.info(f"[{self._name}] RX: {self._format_data(data)}")

        if self._waiting_for_response:
            self._response_queue.put_nowait(data)

        try:
            result = self._on_data(data)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception:
            log.exception("Error in serial on_data callback — continuing (transport still connected)")

    async def _handle_disconnect(self) -> None:
        """Handle unexpected disconnection."""
        if not self._connected:
            return
        self._connected = False
        log.warning(f"Serial connection lost on {self.port}")
        try:
            self._on_disconnect()
        except Exception:  # Catch-all: isolates driver callback errors from transport
            log.exception("Error in serial on_disconnect callback")
