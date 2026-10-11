"""
Capacity — how much a simulated device can take in, and what it does when more
arrives than it can hold.

A real device reads what it receives into a buffer of fixed size and works
through it one message at a time. Bytes that arrive while that buffer is full
are lost, which garbles the command they belonged to. Without this model a
simulator reads only when it is ready and leaves the rest waiting in the
operating system, so it never loses a byte: a driver that floods a device
passes every simulator test and fails on the hardware.

Three numbers describe it (:class:`CapacitySettings`): ``rx_buffer``, the bytes
the device holds before it has read them; ``process_ms``, the time it spends on
each message before it takes the next; and ``on_overflow``, what it does when
bytes are lost (``discard`` them, or ``silence``: stop answering). A driver may
declare them (``simulator.capacity`` in YAML, ``SIMULATOR_INFO["capacity"]`` in
a Python simulator); a device whose driver declares nothing gets
:data:`DEFAULT_SETTINGS`, the simulator's own numbers. Either way the model is
off until it is switched on per device (Simulator UI, or
``POST /api/devices/{id}/capacity``), and a change applies at once, to
connections already open.

``process_ms`` is not ``delays.command_response``. That one is how long a reply
takes; this one is how soon the device takes its next message. A simulator
answers one message at a time, so when both are set a message occupies the
device for the longer of the two (plus any network latency).

Where it sits: one :class:`DeviceCapacity` per simulator. A TCP connection
reads through a :class:`ReceiveLink`, which drains the socket into the bounded
buffer as bytes arrive and hands the read loop one message per read, so the
bound is exact and the simulator's own framing rule decides what a message is.
Datagram simulators keep a bounded queue of whole datagrams instead
(``datagram_server.py``). The network conditions (drop, latency, instability)
still act per message after the buffer, as they always have; a message the
network drops has already taken its room in the buffer.

``silence`` lasts until that TCP connection closes (the next one starts
answering again), and on a datagram device, which has no connection, until
the model is switched off. HTTP, MQTT and WebSocket simulators have no
capacity model: each request is its own exchange.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from openavc.transport.pacing import pacing_clock, sleep_at_least

logger = logging.getLogger(__name__)

#: What a device does when bytes arrive while its buffer is full.
ON_OVERFLOW = ("discard", "silence")

#: The largest buffer and the longest per-message time the model accepts. Far
#: past any device; they keep a typing slip from asking for gigabytes.
MAX_RX_BUFFER = 1024 * 1024
MAX_PROCESS_MS = 60_000.0

#: With the model off, a connection buffers up to this much and then stops
#: reading the socket until the read loop catches up, which is what a plain
#: asyncio stream does too. Nothing is lost with the model off.
_OFF_HIGH_WATER = 128 * 1024

#: The bytes-lost count reaches the Simulator UI at most this often.
_NOTIFY_INTERVAL_S = 0.25


@dataclass(frozen=True)
class CapacitySettings:
    rx_buffer: int
    process_ms: float
    on_overflow: str = "discard"

    def to_dict(self) -> dict:
        return {
            "rx_buffer": self.rx_buffer,
            "process_ms": self.process_ms,
            "on_overflow": self.on_overflow,
        }


#: The simulator's own numbers, for a device whose driver declares none. They
#: are not any device's: a buffer and a pace small enough that an unpaced burst
#: of poll lines overflows and lines sent the platform's default 50 ms apart
#: do not.
DEFAULT_SETTINGS = CapacitySettings(rx_buffer=128, process_ms=20.0, on_overflow="discard")


def capacity_problems(value: Any) -> list[str]:
    """Why ``value`` is not a capacity declaration, or [] when it is one.

    The same rule the driver contract states for ``simulator.capacity``; this
    copy checks a Python simulator's ``SIMULATOR_INFO["capacity"]``, which no
    schema reads, and a change sent to the simulator API.
    """
    if not isinstance(value, dict):
        return ["capacity must be a mapping with rx_buffer and process_ms"]
    problems: list[str] = []
    unknown = sorted(set(value) - {"rx_buffer", "process_ms", "on_overflow"})
    if unknown:
        problems.append(f"capacity has unknown field(s): {', '.join(unknown)}")
    for key in ("rx_buffer", "process_ms"):
        if key not in value:
            problems.append(f"capacity.{key} is required")
    rx = value.get("rx_buffer")
    if "rx_buffer" in value and (
        isinstance(rx, bool) or not isinstance(rx, int) or not 1 <= rx <= MAX_RX_BUFFER
    ):
        problems.append(f"capacity.rx_buffer must be a whole number of bytes from 1 to {MAX_RX_BUFFER}")
    ms = value.get("process_ms")
    if "process_ms" in value and (
        isinstance(ms, bool) or not isinstance(ms, (int, float)) or not 0 <= ms <= MAX_PROCESS_MS
    ):
        problems.append(f"capacity.process_ms must be a number of milliseconds from 0 to {MAX_PROCESS_MS:g}")
    if "on_overflow" in value and value["on_overflow"] not in ON_OVERFLOW:
        problems.append("capacity.on_overflow must be discard or silence")
    return problems


def settings_from(value: Any) -> CapacitySettings:
    """A checked declaration as settings (call :func:`capacity_problems` first)."""
    return CapacitySettings(
        rx_buffer=int(value["rx_buffer"]),
        process_ms=float(value["process_ms"]),
        on_overflow=value.get("on_overflow", "discard"),
    )


class DeviceCapacity:
    """One simulated device's capacity: what is declared, what is set, whether
    it is on, and how many bytes it has lost since it was switched on."""

    def __init__(
        self,
        device_id: str,
        declared: Any = None,
        notify: Callable[[str, dict], None] | None = None,
    ):
        self.device_id = device_id
        self._notify = notify
        self.declared: CapacitySettings | None = None
        self.settings: CapacitySettings = DEFAULT_SETTINGS
        self.enabled = False
        self.bytes_lost = 0
        #: A datagram device that lost bytes with on_overflow: silence. A TCP
        #: device keeps this per connection (ReceiveLink.silenced).
        self.datagram_silenced = False
        self._notify_handle: asyncio.TimerHandle | None = None
        self._last_notify = 0.0
        if declared is not None:
            self.declare(declared)

    def declare(self, value: Any) -> None:
        """Take a driver's declaration. A malformed one is logged and ignored:
        the validator reports it to the driver's author."""
        problems = capacity_problems(value)
        if problems:
            logger.warning(
                "%s: ignoring the declared capacity (%s)", self.device_id, "; ".join(problems)
            )
            return
        self.declared = settings_from(value)
        self.settings = self.declared

    @property
    def active(self) -> CapacitySettings | None:
        """The settings in force, or None while the model is off."""
        return self.settings if self.enabled else None

    def update(
        self,
        *,
        enabled: bool | None = None,
        rx_buffer: int | None = None,
        process_ms: float | None = None,
        on_overflow: str | None = None,
    ) -> None:
        """Change the settings and/or switch the model; the caller checked them.

        Switching it on starts the lost count again. Switching it on or off
        ends a datagram device's silence; a TCP connection's silence lasts
        until that connection closes.
        """
        self.settings = CapacitySettings(
            rx_buffer=self.settings.rx_buffer if rx_buffer is None else int(rx_buffer),
            process_ms=self.settings.process_ms if process_ms is None else float(process_ms),
            on_overflow=self.settings.on_overflow if on_overflow is None else on_overflow,
        )
        if enabled is not None and enabled != self.enabled:
            self.enabled = enabled
            self.datagram_silenced = False
            if enabled:
                self.bytes_lost = 0
        self._send_notify()

    def note_lost(self, count: int) -> None:
        self.bytes_lost += count
        self._schedule_notify()

    def to_info(self) -> dict:
        if self.declared is not None and self.settings == self.declared:
            source = "driver"
        elif self.declared is None and self.settings == DEFAULT_SETTINGS:
            source = "simulator"
        else:
            source = "edited"
        return {
            "enabled": self.enabled,
            **self.settings.to_dict(),
            "source": source,
            "declared": self.declared.to_dict() if self.declared else None,
            "bytes_lost": self.bytes_lost,
        }

    # ── Telling the UI ──

    def _schedule_notify(self) -> None:
        if self._notify is None or self._notify_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._send_notify()
            return
        wait = self._last_notify + _NOTIFY_INTERVAL_S - loop.time()
        if wait <= 0:
            self._send_notify()
        else:
            self._notify_handle = loop.call_later(wait, self._send_notify)

    def _send_notify(self) -> None:
        if self._notify_handle is not None:
            self._notify_handle.cancel()
            self._notify_handle = None
        try:
            self._last_notify = asyncio.get_running_loop().time()
        except RuntimeError:
            pass
        if self._notify is not None:
            self._notify("capacity", {"device_id": self.device_id, **self.to_info()})


class ReceiveLink:
    """The device's receive side of one TCP connection.

    Stands in for the connection's ``asyncio.StreamReader`` and offers the
    same reads (``read``, ``readline``, ``readuntil``, ``readexactly``,
    ``at_eof``), so a simulator's read loop, sign-in and own overrides work
    unchanged. A task drains the socket into a buffer as bytes arrive. With
    the model off the buffer only grows to ``_OFF_HIGH_WATER`` and nothing is
    lost, as with a plain stream. With it on, the buffer holds ``rx_buffer``
    bytes and drops what does not fit, and every read hands out one message,
    as ``first_message_len`` frames it, no sooner than ``process_ms`` after
    the one before.

    Every wait comes before any byte is taken, so a read cancelled by its
    caller's timeout loses nothing.
    """

    def __init__(
        self,
        raw: asyncio.StreamReader,
        capacity: DeviceCapacity,
        first_message_len: Callable[[bytearray], int | None],
        client_id: str = "",
        on_overflow: Callable[[ReceiveLink, int], None] | None = None,
    ):
        self._raw = raw
        self._capacity = capacity
        self._first_message_len = first_message_len
        self._on_overflow = on_overflow
        self.client_id = client_id
        self._buf = bytearray()
        self._eof = False
        self._exc: BaseException | None = None
        self._changed = asyncio.Event()
        self._room = asyncio.Event()
        self._next_release = 0.0
        # True after a read handed out only part of a message: the rest is the
        # same message and is not paced again.
        self._continuing = False
        #: True once this connection lost bytes with on_overflow: silence. The
        #: device then reads and answers nothing more on it.
        self.silenced = False
        self.silence_announced = False
        self.bytes_lost = 0
        self._task = asyncio.ensure_future(self._drain())

    # ── The socket side ──

    async def _drain(self) -> None:
        try:
            while True:
                if self._capacity.active is None and len(self._buf) >= _OFF_HIGH_WATER:
                    self._room.clear()
                    await self._room.wait()
                    continue
                chunk = await self._raw.read(65536)
                if not chunk:
                    self._eof = True
                    self._changed.set()
                    return
                self._take_in(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # the connection failed: the reader sees it next
            self._exc = e
            self._eof = True
            self._changed.set()

    def _take_in(self, chunk: bytes) -> None:
        active = self._capacity.active
        if active is None:
            self._buf += chunk
        else:
            room = max(0, active.rx_buffer - len(self._buf))
            self._buf += chunk[:room]
            lost = len(chunk) - room
            if lost > 0:
                self.bytes_lost += lost
                if active.on_overflow == "silence":
                    self.silenced = True
                self._capacity.note_lost(lost)
                if self._on_overflow is not None:
                    self._on_overflow(self, lost)
        self._changed.set()

    def close(self) -> None:
        """Stop draining the socket (the connection is closing)."""
        self._task.cancel()

    # ── The reader side ──

    async def _wait_until(self, ready: Callable[[], bool]) -> None:
        while not ready() and not self._eof:
            self._changed.clear()
            await self._changed.wait()

    async def _pace(self) -> None:
        """Wait until the device is ready for its next message."""
        active = self._capacity.active
        if active is None or self._continuing or active.process_ms <= 0:
            return
        wait = self._next_release - pacing_clock()
        if wait > 0:
            await sleep_at_least(wait)

    def _release(self, size: int, whole: bool) -> bytes:
        data = bytes(self._buf[:size])
        del self._buf[:size]
        self._room.set()
        active = self._capacity.active
        if active is not None:
            self._continuing = not whole
            if whole:
                self._next_release = pacing_clock() + active.process_ms / 1000.0
        return data

    def _raise_if_failed(self) -> None:
        if self._exc is not None and not self._buf:
            raise self._exc

    async def read(self, n: int = -1) -> bytes:
        if n == 0:
            return b""
        await self._wait_until(lambda: bool(self._buf))
        self._raise_if_failed()
        if not self._buf:
            return b""
        if self._capacity.active is None:
            size = len(self._buf) if n < 0 else min(n, len(self._buf))
            return self._release(size, whole=True)
        await self._pace()
        first = self._first_message_len(self._buf)
        size = first if first else len(self._buf)
        if n >= 0:
            size = min(size, n)
        return self._release(size, whole=first is not None and size == first)

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        await self._wait_until(lambda: separator in self._buf)
        if separator not in self._buf:
            self._raise_if_failed()
            partial = self._release(len(self._buf), whole=False)
            raise asyncio.IncompleteReadError(partial, None)
        await self._pace()
        size = self._buf.find(separator) + len(separator)
        return self._release(size, whole=True)

    async def readline(self) -> bytes:
        try:
            return await self.readuntil(b"\n")
        except asyncio.IncompleteReadError as e:
            return e.partial

    async def readexactly(self, n: int) -> bytes:
        await self._wait_until(lambda: len(self._buf) >= n)
        if len(self._buf) < n:
            self._raise_if_failed()
            partial = self._release(len(self._buf), whole=False)
            raise asyncio.IncompleteReadError(partial, n)
        await self._pace()
        return self._release(n, whole=True)

    def at_eof(self) -> bool:
        return self._eof and not self._buf


def first_line_len(buf: bytearray) -> int | None:
    """Through the first line ending (CR, LF or CRLF, and any run of them), or
    None when no line is complete."""
    cr = buf.find(b"\r")
    lf = buf.find(b"\n")
    ends = [i for i in (cr, lf) if i >= 0]
    if not ends:
        return None
    end = min(ends) + 1
    while end < len(buf) and buf[end] in (0x0D, 0x0A):
        end += 1
    return end


def first_delimited_len(buf: bytearray, delimiter: bytes) -> int | None:
    """Through the first ``delimiter``, or None when none has arrived."""
    i = buf.find(delimiter)
    return None if i < 0 else i + len(delimiter)
