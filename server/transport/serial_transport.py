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
from typing import Any

from server.core.connection_fault import INVALID_CONFIG, ConnectionFaultError
from server.transport.frame_parsers import DelimiterFrameParser, FrameParser
from server.utils.logger import get_logger
from .types import Callback

log = get_logger(__name__)

# Try to import pyserial-asyncio; fall back gracefully
try:
    import serial_asyncio  # type: ignore[import-untyped]

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


def _serial_port_label(
    device: str, description: str, manufacturer: str, serial_number: str
) -> str:
    """Compose a human label for the connection picker (e.g.
    "USB Serial Port (COM3) - FTDI / FT4VABCD"). Avoids duplicating the device
    path or manufacturer when the OS description already contains them."""
    label = description or device
    if device not in label:
        label = f"{label} ({device})"
    extras = [
        e
        for e in (manufacturer, serial_number)
        if e and e.lower() not in label.lower()
    ]
    return f"{label} - {' / '.join(extras)}" if extras else label


def list_serial_ports() -> list[dict[str, Any]]:
    """Enumerate the serial ports present on this host (the machine running the
    server), for the device connection picker.

    Each entry carries the OS device path plus whatever USB-adapter identity
    pyserial can read (vendor / product / serial number). ``serial_number`` is
    what the connection binds to for a stable identity across reboot/replug —
    cheap CH340-class adapters often expose none, in which case the integrator
    binds to the path and accepts that it may move. Returns an empty list if
    pyserial's platform enumeration backend can't be imported.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        log.warning("serial.tools.list_ports unavailable; cannot enumerate serial ports")
        return []

    ports: list[dict[str, Any]] = []
    for p in list_ports.comports():
        vid = getattr(p, "vid", None)
        pid = getattr(p, "pid", None)
        serial_number = (getattr(p, "serial_number", None) or "").strip()
        manufacturer = (getattr(p, "manufacturer", None) or "").strip()
        description = (getattr(p, "description", None) or "").strip()
        # pyserial reports "n/a" for ports it can't describe (common on Linux
        # for built-in UARTs) — fall back to the device path so the label reads.
        if description.lower() in ("", "n/a"):
            description = p.device
        ports.append(
            {
                "device": p.device,
                "description": description,
                "manufacturer": manufacturer,
                "vid": vid,
                "pid": pid,
                "serial_number": serial_number,
                "hwid": (getattr(p, "hwid", None) or "").strip(),
                "usb": vid is not None,
                "label": _serial_port_label(
                    p.device, description, manufacturer, serial_number
                ),
            }
        )
    # USB adapters first (the common case), then by device path for stability.
    ports.sort(key=lambda x: (not x["usb"], x["device"]))
    return ports


def resolve_serial_port_by_serial(usb_serial: str) -> str | None:
    """Return the live OS device path of the attached adapter whose USB serial
    number matches ``usb_serial``, or ``None`` if no attached port matches.

    Used by the connection resolver to turn a stored, stable ``usb_serial`` into
    the volatile path (COM3 / /dev/ttyUSB0) the OS assigns it this boot.
    """
    if not usb_serial:
        return None
    for p in list_serial_ports():
        if p["serial_number"] and p["serial_number"] == usb_serial:
            return p["device"]
    return None


def resolve_usb_binding(config: dict, driver_transport: str = "") -> dict:
    """Rewrite a USB-serial device's volatile port from its stable adapter id.

    A directly-attached USB-to-serial adapter is given a port name by the OS
    (COM3 / /dev/ttyUSB0) that is not stable across reboot or replug. When the
    connection stored the adapter's ``usb_serial`` (USB serial number), resolve
    it to whatever path the OS assigned that adapter right now, so the device
    follows its cable instead of a fixed name. Called wherever a serial device
    is (re)dialed: startup/reload/edit config resolution and each reconnect
    attempt — the path can change mid-run when the adapter is replugged.

    Only applies to a real local serial connection: a bridge-bound serial
    device has been rewritten to ``transport=tcp`` by the bridge resolver and
    is left alone, and an explicit network transport is skipped. If no attached
    adapter carries that serial (unplugged, or a clone that exposes none), the
    stored ``port`` is left as-is — the device then fails to connect with the
    normal serial open error rather than silently dialing the wrong port.

    ``driver_transport`` is the device's driver-declared transport
    (``DRIVER_INFO['transport']``). When the saved config omits ``transport``,
    it stands in — so a stray ``usb_serial`` left on a tcp/http device (a hand
    edit the IDE would have pruned) can't rewrite its numeric port to a serial
    path just because a matching adapter happens to be attached.

    Returns the same dict when nothing changed, a copy with ``port`` rewritten
    when it did.
    """
    usb_serial = config.get("usb_serial")
    transport = config.get("transport") or driver_transport
    if not usb_serial or config.get("bridge") or transport not in ("", "serial"):
        return config

    live = resolve_serial_port_by_serial(usb_serial)
    if live and live != config.get("port"):
        resolved = dict(config)
        resolved["port"] = live
        return resolved
    return config


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget on_data tasks: surface failures that
    would otherwise vanish as a 'Task exception was never retrieved' GC warning."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in serial on_data task: %s", exc, exc_info=exc)


class SerialTransport:
    """Async serial transport with pluggable message framing."""

    # Posted to the response queue to wake a parked send_and_wait when the link
    # drops or is closed, so it fails fast instead of blocking the full timeout.
    _DISCONNECT_SENTINEL = object()

    def __init__(
        self,
        port: str,
        baudrate: int,
        on_data: Callback[[bytes], None],
        on_disconnect: Callback[[], None],
        frame_parser: FrameParser | None = None,
        delimiter: bytes | None = b"\r",
        timeout: float = 5.0,
        inter_command_delay: float = 0.0,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        rtscts: bool = False,
        xonxoff: bool = False,
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
        self._rtscts = rtscts
        self._xonxoff = xonxoff
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
        # Last open/IO error string, for the connection-fault classifier.
        self._last_error = ""

        # For send_and_wait. May also carry _DISCONNECT_SENTINEL to wake a
        # parked waiter on disconnect/close.
        self._response_queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=100)
        self._waiting_for_response = False

        # Strong refs to fire-and-forget async on_data tasks so they aren't
        # garbage-collected mid-flight; cleared by their own done-callback.
        self._bg_tasks: set[asyncio.Task] = set()

        # Simulation state
        self._sim_rx_queue: asyncio.Queue[bytes] = asyncio.Queue()

    @classmethod
    async def create(
        cls,
        port: str,
        baudrate: int = 9600,
        on_data: Callback[[bytes], None] = lambda d: None,
        on_disconnect: Callback[[], None] = lambda: None,
        frame_parser: FrameParser | None = None,
        delimiter: bytes | None = b"\r",
        timeout: float = 5.0,
        inter_command_delay: float = 0.0,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        rtscts: bool = False,
        xonxoff: bool = False,
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
            rtscts: Enable hardware (RTS/CTS) flow control. Default off.
            xonxoff: Enable software (XON/XOFF) flow control. Default off.
            simulate: Force simulation mode.
            name: Optional label for log messages (e.g. device_id).
                  Defaults to port path.

        Returns:
            Connected SerialTransport instance.
        """
        transport = cls(
            port, baudrate, on_data, on_disconnect, frame_parser,
            delimiter, timeout, inter_command_delay, bytesize, parity,
            stopbits, rtscts, xonxoff, simulate, name,
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
                    rtscts=self._rtscts,
                    xonxoff=self._xonxoff,
                ),
                timeout=self._timeout,
            )
            self._connected = True
            if self._frame_parser is not None:
                self._frame_parser.reset()
            self._reader_task = asyncio.create_task(self._reader_loop())
            log.info(f"Serial connected to {self.port} @ {self.baudrate}")
        except ValueError as e:
            # pyserial raises ValueError for an out-of-range setting (bad
            # parity/baudrate/data bits) — a PERMANENT config error, never valid
            # on retry. Without catching it here it escapes the ConnectionError
            # wrapping and is handled as a transient disconnect, leaving the
            # device offline with a generic "reconnecting" message that hides the
            # real cause. Type it as invalid_config so the card names the fix.
            self._last_error = str(e) or type(e).__name__
            raise ConnectionFaultError(
                f"Invalid serial settings for {self.port}: {e}. Check the baud "
                f"rate, parity, data bits, and stop bits.",
                code=INVALID_CONFIG,
            ) from e
        except (OSError, asyncio.TimeoutError) as e:
            self._last_error = str(e) or type(e).__name__
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
            log.debug(f"[{self._name}] TX: {self._format_data(data)}")
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)
            return

        try:
            self._writer.write(data)
            await self._writer.drain()
            log.debug(f"[{self._name}] TX: {self._format_data(data)}")
            if self._inter_command_delay > 0:
                await asyncio.sleep(self._inter_command_delay)
        except (OSError, ConnectionError) as e:
            self._last_error = str(e) or type(e).__name__
            log.error(f"Serial send error: {e}")
            await self._handle_disconnect()
            raise

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        """Send data and wait for the next complete response.

        The lock is held through the entire send+wait cycle to prevent
        concurrent sends from interleaving with the response.
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
                    self._response_queue.get(), timeout=timeout,
                )
                if response is self._DISCONNECT_SENTINEL:
                    raise ConnectionError(
                        f"Connection lost while waiting for response on {self.port}"
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
        self._wake_response_waiter()
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

    @property
    def last_error(self) -> str:
        """Last open/IO error string (for the connection-fault classifier)."""
        return self._last_error

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
            self._last_error = str(e) or type(e).__name__
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
        log.debug(f"[{self._name}] RX: {self._format_data(data)}")

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
            log.exception("Error in serial on_data callback — continuing (transport still connected)")

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
        """Handle unexpected disconnection."""
        if not self._connected:
            return
        self._connected = False
        self._wake_response_waiter()
        log.warning(f"Serial connection lost on {self.port}")
        try:
            self._on_disconnect()
        except Exception:  # Catch-all: isolates driver callback errors from transport
            log.exception("Error in serial on_disconnect callback")
