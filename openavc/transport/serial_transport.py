"""
OpenAVC Serial Transport — async serial port client with message framing.

Provides a managed serial connection with:
- Pluggable frame parser (same as TCP transport)
- Same interface as TCPTransport: send(), send_and_wait(), close(), connected

A port is opened or refused, never stood in for: a ``SIM:<name>`` port and a
system without pyserial-asyncio are refused before anything opens
(``serial_port_refusal``). The Simulator is the one way to run a serial device
without the equipment: it redirects the device to its TCP stand-in
(``core/simulation.py``). The in-process stand-in here opens only for a caller
that passes ``simulate=True``, which is how the transport's tests drive it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openavc.core.connection_fault import (
    CLIENT_MISSING,
    INVALID_CONFIG,
    UNREACHABLE,
    ConnectionFault,
    ConnectionFaultError,
    typed_fault_from_exc,
    usb_adapter_missing_message,
)
from openavc.core.bridge_binding import bridge_binding
from openavc.core.device_traffic import RX, TX, record_chunk, record_traffic
from openavc.drivers.transport_choice import configured_transport
from openavc.transport.frame_parsers import DelimiterFrameParser, FrameParser
from openavc.transport.wire_log import format_wire_data, wire_held
from openavc.transport.write_drain import close_or_abandon, drain_or_stalled
from openavc.transport.handler_runs import HandlerRuns
from openavc.utils.logger import get_logger
from openavc.transport.pacing import SendLock, sleep_at_least
from .types import Callback

log = get_logger(__name__)

# Try to import pyserial-asyncio; fall back gracefully
try:
    import serial  # type: ignore[import-untyped]
    import serial_asyncio  # type: ignore[import-untyped]

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


#: The open of each port still under way in a worker thread. One at a time:
#: a thread cannot be stopped, so a retry against an adapter whose open never
#: returns would otherwise start another each time, until the loop's shared
#: worker threads are all stuck and everything else that uses them waits.
_OPENING: dict[str, asyncio.Future] = {}


def _forget_open(port: str, opening: asyncio.Future) -> None:
    if _OPENING.get(port) is opening:
        del _OPENING[port]


def _close_if_opened(opening: asyncio.Future) -> None:
    """A port the worker thread opened after its connect gave up (a timeout,
    a cancel): closed as it lands, so the adapter is not held open by a
    transport nobody has. An open that failed is read here, so it is not
    reported as never retrieved."""
    if opening.cancelled() or opening.exception() is not None:
        return
    try:
        opening.result().close()
    except OSError:
        pass


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
    pyserial's platform enumeration backend can't be imported or its listing
    fails.
    """
    ports = _attached_ports()
    if ports is None:
        log.warning("Could not list this host's serial ports")
        return []
    return ports


def _attached_ports() -> list[dict[str, Any]] | None:
    """The ports :func:`list_serial_ports` lists, or ``None`` when this host
    cannot enumerate its ports at all, which is not the same as having none:
    pyserial missing, or its listing raising."""
    found = _listed_ports()
    if found is None:
        return None
    ports = [_port_entry(p) for p in found]
    # USB adapters first (the common case), then by device path for stability.
    ports.sort(key=lambda x: (not x["usb"], x["device"]))
    return ports


def _listed_ports() -> list[Any] | None:
    """pyserial's own listing of this host's ports, or ``None`` when it is
    missing or the listing raises."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    try:
        return list(list_ports.comports())
    except Exception:
        log.debug("Could not list the serial ports", exc_info=True)
        return None


def _port_entry(p: Any) -> dict[str, Any]:
    """One listed port, as the connection picker and the USB binding read it."""
    vid = getattr(p, "vid", None)
    serial_number = (getattr(p, "serial_number", None) or "").strip()
    manufacturer = (getattr(p, "manufacturer", None) or "").strip()
    description = (getattr(p, "description", None) or "").strip()
    # pyserial reports "n/a" for ports it can't describe (common on Linux
    # for built-in UARTs) — fall back to the device path so the label reads.
    if description.lower() in ("", "n/a"):
        description = p.device
    return {
        "device": p.device,
        "description": description,
        "manufacturer": manufacturer,
        "vid": vid,
        "pid": getattr(p, "pid", None),
        "serial_number": serial_number,
        "hwid": (getattr(p, "hwid", None) or "").strip(),
        "usb": vid is not None,
        "label": _serial_port_label(p.device, description, manufacturer, serial_number),
    }


def require_usb_adapter(config: dict, driver_transport: str = "") -> dict:
    """The config a USB-serial device dials now, or a refusal when its adapter
    is not plugged in.

    THE rule for a device bound to a USB-to-serial adapter. The OS gives a
    directly-attached adapter a port name (COM3 / /dev/ttyUSB0) that is not
    stable across reboot or replug. When the connection stored the adapter's
    ``usb_serial`` (USB serial number), the device follows its cable: the port
    becomes whatever path the OS assigned that adapter right now. Asked before
    every dial (the device manager's connect, the connection test), since the
    path can change mid-run when the adapter is replugged.

    When no attached adapter carries that serial number, the stored port is
    not dialled: the OS hands a free name to the next adapter it enumerates,
    so opening it would reach another adapter's equipment. The refusal is an
    ``unreachable`` fault, so the device keeps retrying and connects once the
    adapter is back. The stored port is dialled only when nothing can tell
    the adapter there from the bound one: the port there reports no serial
    number, or this host cannot list its ports.

    When more than one attached adapter reports the serial number (clone
    adapters can ship one fixed number), the number cannot tell them apart:
    the stored port is dialled when it is one of them, and otherwise the dial
    is refused naming them all, never guessing (``_port_carrying``).

    Only applies to a real local serial connection: a device set to go
    through a bridge (``core.bridge_binding``, resolved or not) is not on this
    computer's port and is left alone, and an explicit network transport is
    skipped (the simulation redirect sets ``tcp`` and leaves ``usb_serial`` in
    place).

    ``driver_transport`` is the device's driver-declared transport
    (``DRIVER_INFO['transport']``). When the saved config omits ``transport``,
    it stands in — so a stray ``usb_serial`` left on a tcp/http device (a hand
    edit the IDE would have pruned) can't rewrite its numeric port to a serial
    path just because a matching adapter happens to be attached.

    Returns the same dict when nothing changed, a copy with ``port`` rewritten
    when it did. Raises :class:`ConnectionFaultError` when the adapter is not
    plugged in.
    """
    usb_serial = _bound_usb_serial(config, driver_transport)
    ports = _attached_ports() if usb_serial else None
    if ports is None:
        return config
    live = _port_carrying(ports, usb_serial, config.get("port"))
    if live is not None:
        return _with_port(config, live)
    if _unidentified_port_at(ports, config.get("port")):
        return config
    raise ConnectionFaultError(
        usb_adapter_missing_message(str(usb_serial)), code=UNREACHABLE,
    )


def _bound_usb_serial(config: dict, driver_transport: str) -> str:
    """The adapter serial number a local serial device is bound to, or ""
    when its port is not the adapter's to decide (a bridge, a network
    transport, no ``usb_serial``)."""
    if bridge_binding(config).bound:
        return ""
    # The device's own choice, else the driver's (transport_choice); "" is a
    # driver not installed, whose stored adapter binding still counts.
    if (configured_transport(config) or driver_transport) not in ("", "serial"):
        return ""
    return config.get("usb_serial") or ""


def _port_carrying(ports: list[dict[str, Any]], usb_serial: str, stored: object) -> str | None:
    """The path of the attached adapter whose serial number is ``usb_serial``,
    or None. Several adapters reporting it: the stored port when it is one of
    them, else a refusal naming them (``require_usb_adapter``)."""
    from openavc.core.connection_fault import usb_adapters_alike_message
    from openavc.core.device_config import same_serial_port

    alike = [p["device"] for p in ports if p["serial_number"] == usb_serial]
    if len(alike) <= 1:
        return alike[0] if alike else None
    at_stored = next((path for path in alike if same_serial_port(path, str(stored or ""))), None)
    if at_stored is not None:
        return at_stored
    raise ConnectionFaultError(usb_adapters_alike_message(usb_serial, alike), code=UNREACHABLE)


def _with_port(config: dict, port: str) -> dict:
    """``config`` dialling ``port``: the same dict when it already does."""
    if port == config.get("port"):
        return config
    return {**config, "port": port}


def _unidentified_port_at(ports: list[dict[str, Any]], port: object) -> bool:
    """True when an attached port at ``port`` reports no serial number."""
    from openavc.core.device_config import same_serial_port

    stored = str(port or "")
    return bool(stored) and any(
        same_serial_port(p["device"], stored) and not p["serial_number"]
        for p in ports
    )


def resolve_usb_binding(config: dict, driver_transport: str = "") -> dict:
    """The port a USB-serial device is set to, for its resolved record.

    :func:`require_usb_adapter`'s answer, except that an adapter that is not
    plugged in leaves the stored ``port`` as it is: the record says what the
    device is set to, and the dial, which asks :func:`require_usb_adapter`
    again, refuses it.
    """
    try:
        return require_usb_adapter(config, driver_transport)
    except ConnectionFaultError:
        return config


SIM_PORT_MESSAGE = (
    "SIM: is not a serial port. To test without the device, save the driver, "
    "add a device that uses it, then Start Simulation."
)
SERIAL_SUPPORT_MISSING_MESSAGE = (
    "This system cannot open serial ports because the pyserial-asyncio "
    "package is not installed. Install it with pip install pyserial-asyncio, "
    "then restart OpenAVC."
)


def serial_port_refusal(port: str) -> ConnectionFaultError | None:
    """Why this system will not open ``port`` before trying it, or ``None``.

    THE rule for a serial port that is not one to open, and its sentences,
    which the device card, Live Test and the connection test all show. A port
    typed as ``SIM:<name>`` is a setting error (``invalid_config``): the
    Simulator is the one way to run a serial device without the equipment, so
    the sentence names Start Simulation. A system without pyserial-asyncio can
    open no serial port at all (``client_missing``). Asked by the transport
    before it opens anything, which the connect and Live Test's raw Send
    build, and by the connection test, which opens the port with pyserial
    itself.
    """
    if port.strip().upper().startswith("SIM:"):
        return ConnectionFaultError(SIM_PORT_MESSAGE, code=INVALID_CONFIG)
    if not HAS_SERIAL:
        return ConnectionFaultError(SERIAL_SUPPORT_MISSING_MESSAGE, code=CLIENT_MISSING)
    return None


def _require_openable(port: object, simulate: bool) -> None:
    """Refuse a port the transport cannot open, before anything is built.

    A serial port is a device path ("COM3", "/dev/ttyUSB0"), never a number.
    A numeric value means the device was configured with a TCP port: a
    permanent config error that used to die on a string operation with a
    bare AttributeError, leaving the device card showing a generic reconnect
    instead of the fix. Then :func:`serial_port_refusal`, unless the caller
    asked for the stand-in.
    """
    if not isinstance(port, str):
        raise ConnectionFaultError(
            f"Serial port must be a device path such as COM3 or "
            f"/dev/ttyUSB0, not {port!r}. Set the device's port to the "
            f"serial device path (or use a TCP transport for a network "
            f"device).",
            code=INVALID_CONFIG,
        )
    refused = None if simulate else serial_port_refusal(port)
    if refused is not None:
        raise refused


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
        # Recorded (core/device_traffic.py) under the device id a caller
        # names, never the port-name fallback.
        self._traffic_name = name or ""

        # The stand-in (sends recorded, nothing answers unless sim_receive
        # feeds it) only when the caller asks for it: a SIM: port or a missing
        # serial package is refused, never a device that reads connected.
        _require_openable(port, simulate)
        self._simulate = simulate

        # Resolve frame parser (same logic as TCP, empty delimiter included)
        if frame_parser is not None:
            self._frame_parser: FrameParser | None = frame_parser
        elif delimiter:
            self._frame_parser = DelimiterFrameParser(delimiter)
        else:
            self._frame_parser = None
        # Attribute overflow warnings to this device (see TCPTransport).
        if self._frame_parser is not None:
            self._frame_parser.device_label = self._name

        self._reader: Any = None
        self._writer: Any = None
        self._reader_task: asyncio.Task | None = None
        self._send_lock = SendLock()
        self._connected = False
        # Last open/IO error string, for the connection-fault classifier.
        self._last_error = ""
        # A typed fault the classifier should prefer over matching the string
        # above — set when the failure already knows its own code (a stalled
        # write). Cleared on every connect so a healed link can't report it.
        self._last_fault: ConnectionFault | None = None

        # For send_and_wait. May also carry _DISCONNECT_SENTINEL to wake a
        # parked waiter on disconnect/close.
        self._response_queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=100)
        self._waiting_for_response = False

        # The receive handler's runs: one message at a time, in arrival order,
        # each held until it ends (handler_runs).
        self._handler_runs = HandlerRuns(_log_task_exception)

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
            port: Serial port path (e.g., "COM3", "/dev/ttyUSB0"). A
                  ``SIM:<name>`` port, or any port on a system without
                  pyserial-asyncio, is refused (:func:`serial_port_refusal`).
            baudrate: Baud rate (default 9600).
            on_data: Called with each complete message.
            on_disconnect: Called when the port is lost.
            frame_parser: Optional FrameParser instance.
            delimiter: Fallback delimiter if no frame_parser given. None
                or empty selects raw mode.
            timeout: Connection timeout in seconds.
            inter_command_delay: Seconds to wait between sends.
            bytesize: Data bits (5, 6, 7, 8). Default 8.
            parity: Parity letter, as pyserial takes it (N, E, O, M or S).
                Default 'N'. A device's config is read into these by the
                driver contract's line-setting table (serial_line.read_line_setting).
            stopbits: Stop bits (1, 1.5, 2). Default 1.
            rtscts: Enable hardware (RTS/CTS) flow control. Default off.
            xonxoff: Enable software (XON/XOFF) flow control. Default off.
            simulate: Open the stand-in instead of a port, for tests: it
                reads connected, records sends and receives only what
                :meth:`sim_receive` feeds it.
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
        # Clean slate: a previous attempt's typed fault must not be read as
        # this one's cause.
        self._last_fault = None
        if self._simulate:
            self._connected = True
            if self._frame_parser is not None:
                self._frame_parser.reset()
            self._reader_task = asyncio.create_task(self._sim_reader_loop())
            log.info(f"Serial SIMULATED connection on {self.port} @ {self.baudrate}")
            return

        try:
            port = await self._open_port()
            self._reader, self._writer = await self._stream_on(port)
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

    async def _open_port(self) -> Any:
        """The OS open of the port, in a worker thread: an adapter whose open
        stalls (a USB adapter mid-enumeration, a driver that blocks) would
        otherwise hold the event loop, and every device, panel and timer
        with it, out of reach of the timeout. The thread cannot be stopped,
        so a port it opens after the timeout is closed as it lands, and while
        an earlier open of the same port has not returned no other starts."""
        earlier = _OPENING.get(self.port)
        if earlier is not None and not earlier.done():
            raise OSError(f"the last attempt to open {self.port} has not returned yet")
        opening = asyncio.ensure_future(asyncio.to_thread(
            serial.serial_for_url,
            self.port,
            baudrate=self.baudrate,
            bytesize=self._bytesize,
            parity=self._parity,
            stopbits=self._stopbits,
            rtscts=self._rtscts,
            xonxoff=self._xonxoff,
        ))
        _OPENING[self.port] = opening
        opening.add_done_callback(lambda done, port=self.port: _forget_open(port, done))
        try:
            return await asyncio.wait_for(asyncio.shield(opening), timeout=self._timeout)
        except BaseException:
            opening.add_done_callback(_close_if_opened)
            raise

    @staticmethod
    async def _stream_on(port: Any) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """The reader and writer on an opened port, built on the loop as
        pyserial-asyncio's own open builds them."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await serial_asyncio.connection_for_serial(loop, lambda: protocol, port)
        return reader, asyncio.StreamWriter(transport, protocol, reader, loop)

    async def wait_idle(self) -> None:
        """Return once no write is under way or waiting, the
        ``inter_command_delay`` after each included, a write queued behind
        this wait as well (``SendLock.wait_idle``). A caller about to decide
        whether to send (a driver's state-gated poll line) waits here first,
        so the answer to a command that just went out has had its chance to
        arrive."""
        await self._send_lock.wait_idle()

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
            if not wire_held(self._name, TX):
                log.debug(f"[{self._name}] TX: {self._format_data(data, TX)}")
            record_traffic(self._traffic_name, TX, data, channel="serial")
            if self._inter_command_delay > 0:
                await sleep_at_least(self._inter_command_delay)
            return

        try:
            self._writer.write(data)
            await drain_or_stalled(self._writer, self._name)
            if not wire_held(self._name, TX):
                log.debug(f"[{self._name}] TX: {self._format_data(data, TX)}")
            record_traffic(self._traffic_name, TX, data, channel="serial")
            if self._inter_command_delay > 0:
                await sleep_at_least(self._inter_command_delay)
        except (OSError, ConnectionError) as e:
            self._last_error = str(e) or type(e).__name__
            self._last_fault = typed_fault_from_exc(e, port=self.port)
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
        # The handler runs still to come were the closed port's.
        self._handler_runs.close()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._writer and not self._simulate:
            await close_or_abandon(self._writer, self._name)
        log.info(f"Serial disconnected from {self.port}")

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        """Last open/IO error string (for the connection-fault classifier)."""
        return self._last_error

    @property
    def last_fault(self) -> ConnectionFault | None:
        """A typed fault for this failure, when the transport knew its own
        cause. Preferred over string-matching ``last_error``."""
        return self._last_fault

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
            record_chunk(self._traffic_name, data, channel="serial")
            for msg in self._frame_parser.feed(data):
                self._deliver_message(msg)

    def _format_data(self, data: bytes, direction: str | None = None) -> str:
        """Format a payload for a TX/RX line, masking this device's secrets."""
        return format_wire_data(data, self._name, direction)

    def _deliver_message(self, data: bytes) -> None:
        """Deliver a complete message to callback and/or response queue."""
        if not wire_held(self._name, RX):
            log.debug(f"[{self._name}] RX: {self._format_data(data, RX)}")
        record_traffic(self._traffic_name, RX, data, channel="serial")

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
            # A coroutine runs once the one before it has ended (handler_runs).
            self._handler_runs.run(self._on_data(data))
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
