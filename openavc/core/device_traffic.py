"""THE per-device traffic recorder: what each device's transports moved.

Every platform transport reports the bytes it sends and receives here, keyed
by the device id it already carries as its ``name``. Two readers want them:
the device page's communication monitor (scrollback, then a live stream while
somebody is watching) and a device audit (every byte, for the report).

- **Always records, bounded.** Each device keeps its newest ``RING_ENTRIES``
  entries, and no more than ``RING_BYTES`` of payload, so opening a monitor
  has scrollback and a device that polls a large web page cannot grow memory
  without limit. Rings are kept for at most ``MAX_DEVICES`` names; the least
  recently used one without a subscriber goes first (test connections record
  under a ``host:port`` name that no device removal ever clears).
- **Streams only to subscribers.** ``subscribe`` delivers each new entry for
  one device to one reader, by callback or a bounded queue, so a busy room
  costs nothing on the wire while nobody is watching.
- **Raw bytes, formatted late.** An entry keeps the exact bytes; hex, text
  and redaction happen in :func:`serialize_entry`, so recording is an append.
- **Raw receive chunks** (what a TCP or serial read returned, before the
  frame parser split it) are recorded only while a subscriber asks for them.
  A framing bug, like a checksum byte that equals the delimiter, is invisible
  once the frames are cut.
- **Secrets are redacted when an entry is written out**, from the device's
  registered credentials (``utils/log_redaction.py``) or a set the caller
  holds (an audit keeps its own, since removing a device forgets its secrets).
  An ``Authorization`` header is masked at capture instead: it carries the
  password base64-encoded, which no literal match can find.

Channels name the path an entry took: the transport (``tcp``, ``serial``,
``udp``, ``osc``, ``snmp``, ``http``, ``ssh``, ``mqtt``), a push channel
(``sse``, ``multicast``, ``tcp_listener``, ``http_listener``), or ``driver``
for a driver that owns its connection and reports its own traffic
(``BaseDriver.record_traffic``). Transports keep their DEBUG ``TX``/``RX`` log
lines; this is the in-app record.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from openavc.utils.log_redaction import (
    compile_secret_bytes_pattern,
    compile_secret_pattern,
    get_secret_registry,
)

log = logging.getLogger(__name__)

TX = "tx"
RX = "rx"

# The newest entries a device keeps, and the payload bytes they may hold.
RING_ENTRIES = 500
RING_BYTES = 512 * 1024
# Rings kept at once (least recently used without a subscriber goes first).
MAX_DEVICES = 1024
# An HTTP body, a push request body: the part recorded.
BODY_LIMIT = 64 * 1024
# A queue subscriber's backlog before new entries are dropped (and counted).
QUEUE_SIZE = 1000

# Headers whose value is a credential in a form redaction cannot recognise.
_MASKED_HEADERS = frozenset({"authorization", "proxy-authorization"})

_seq = itertools.count(1)


@dataclass(slots=True)
class TrafficEntry:
    """One payload that crossed a device's wire.

    ``chunk`` marks a raw receive chunk (before framing); ``meta`` carries
    what the bytes alone do not say: the peer of a datagram, an HTTP request's
    method, target, headers and status, an MQTT topic.
    """

    t: float
    device_id: str
    direction: str
    channel: str
    data: bytes
    chunk: bool = False
    meta: dict[str, Any] | None = None
    seq: int = 0


@dataclass(eq=False)
class TrafficSubscription:
    """One reader of one device's traffic."""

    device_id: str
    chunks: bool = False
    callback: Callable[[TrafficEntry], None] | None = None
    queue: asyncio.Queue | None = None
    dropped: int = 0
    closed: bool = field(default=False)

    def deliver(self, entry: TrafficEntry) -> None:
        if self.closed:
            return
        if self.callback is not None:
            try:
                self.callback(entry)
            except Exception:  # a reader's failure is its own
                log.debug("Traffic subscriber failed", exc_info=True)
            return
        if self.queue is not None:
            try:
                self.queue.put_nowait(entry)
            except asyncio.QueueFull:
                self.dropped += 1


class _Ring:
    __slots__ = ("entries", "bytes")

    def __init__(self) -> None:
        self.entries: deque[TrafficEntry] = deque()
        self.bytes = 0


class DeviceTrafficRecorder:
    """Per-device rings of recent traffic, and the readers of each device."""

    def __init__(
        self,
        ring_entries: int = RING_ENTRIES,
        ring_bytes: int = RING_BYTES,
        max_devices: int = MAX_DEVICES,
    ) -> None:
        self._ring_entries = ring_entries
        self._ring_bytes = ring_bytes
        self._max_devices = max_devices
        self._rings: OrderedDict[str, _Ring] = OrderedDict()
        self._subs: dict[str, list[TrafficSubscription]] = {}
        # Devices with at least one subscriber that wants raw chunks.
        self._chunk_readers: dict[str, int] = {}

    # -- recording ----------------------------------------------------------

    def record(
        self,
        device_id: str,
        direction: str,
        data: bytes,
        *,
        channel: str,
        meta: dict[str, Any] | None = None,
    ) -> TrafficEntry | None:
        """Keep one payload in ``device_id``'s ring and hand it to its readers."""
        if not device_id:
            return None
        entry = TrafficEntry(
            t=time.time(), device_id=device_id, direction=direction,
            channel=channel, data=bytes(data), meta=meta, seq=next(_seq),
        )
        self._append(entry)
        for sub in tuple(self._subs.get(device_id, ())):
            sub.deliver(entry)
        return entry

    def wants_chunks(self, device_id: str) -> bool:
        """True while a reader of this device asked for raw receive chunks."""
        return bool(self._chunk_readers.get(device_id))

    def record_chunk(self, device_id: str, data: bytes, *, channel: str) -> None:
        """A raw receive chunk, for the readers that asked (never kept)."""
        if not self.wants_chunks(device_id):
            return
        entry = TrafficEntry(
            t=time.time(), device_id=device_id, direction=RX, channel=channel,
            data=bytes(data), chunk=True, seq=next(_seq),
        )
        for sub in tuple(self._subs.get(device_id, ())):
            if sub.chunks:
                sub.deliver(entry)

    def _append(self, entry: TrafficEntry) -> None:
        ring = self._rings.get(entry.device_id)
        if ring is None:
            ring = self._rings[entry.device_id] = _Ring()
            self._evict()
        else:
            self._rings.move_to_end(entry.device_id)
        ring.entries.append(entry)
        ring.bytes += len(entry.data)
        while ring.entries and (
            len(ring.entries) > self._ring_entries or ring.bytes > self._ring_bytes
        ):
            ring.bytes -= len(ring.entries.popleft().data)

    def _evict(self) -> None:
        while len(self._rings) > self._max_devices:
            for name in self._rings:
                if not self._subs.get(name):
                    del self._rings[name]
                    break
            else:
                return

    # -- reading ------------------------------------------------------------

    def get_recent(self, device_id: str, count: int | None = None) -> list[TrafficEntry]:
        """The device's newest entries, oldest first (all kept when no count)."""
        ring = self._rings.get(device_id)
        if ring is None:
            return []
        entries = list(ring.entries)
        return entries[-count:] if count else entries

    def subscribe(
        self,
        device_id: str,
        *,
        callback: Callable[[TrafficEntry], None] | None = None,
        chunks: bool = False,
        queue_size: int = QUEUE_SIZE,
    ) -> TrafficSubscription:
        """Deliver each new entry for ``device_id`` until unsubscribed.

        With ``callback`` it is called inline and must not block; without, the
        entries collect in ``subscription.queue`` (bounded; an overflowing
        entry is dropped and counted in ``subscription.dropped``). ``chunks``
        adds the raw receive chunks.
        """
        sub = TrafficSubscription(
            device_id=device_id, chunks=chunks, callback=callback,
            queue=None if callback is not None else asyncio.Queue(maxsize=queue_size),
        )
        self._subs.setdefault(device_id, []).append(sub)
        if chunks:
            self._chunk_readers[device_id] = self._chunk_readers.get(device_id, 0) + 1
        return sub

    def unsubscribe(self, sub: TrafficSubscription) -> None:
        if sub.closed:
            return
        sub.closed = True
        subs = self._subs.get(sub.device_id)
        if subs and sub in subs:
            subs.remove(sub)
            if not subs:
                del self._subs[sub.device_id]
        if sub.chunks:
            left = self._chunk_readers.get(sub.device_id, 0) - 1
            if left > 0:
                self._chunk_readers[sub.device_id] = left
            else:
                self._chunk_readers.pop(sub.device_id, None)

    def forget(self, device_id: str) -> None:
        """Drop a device's ring (its readers stay subscribed)."""
        self._rings.pop(device_id, None)

    def clear(self) -> None:
        self._rings.clear()
        for subs in list(self._subs.values()):
            for sub in list(subs):
                self.unsubscribe(sub)


_recorder = DeviceTrafficRecorder()


def get_traffic_recorder() -> DeviceTrafficRecorder:
    """The process-wide recorder every transport reports to."""
    return _recorder


def record_traffic(
    device_id: str | None,
    direction: str,
    data: bytes,
    *,
    channel: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record one payload. Never raises: a recorder fault must not cost a send."""
    if not device_id:
        return
    try:
        _recorder.record(device_id, direction, data, channel=channel, meta=meta)
    except Exception:
        log.debug("Traffic record failed", exc_info=True)


def record_chunk(device_id: str | None, data: bytes, *, channel: str) -> None:
    """Record one raw receive chunk if a reader asked. Never raises."""
    if not device_id or not _recorder.wants_chunks(device_id):
        return
    try:
        _recorder.record_chunk(device_id, data, channel=channel)
    except Exception:
        log.debug("Traffic chunk record failed", exc_info=True)


def header_pairs(headers: Any) -> list[list[str]]:
    """A request's or response's headers as ``[name, value]`` pairs, in order,
    with credential headers masked down to their scheme word."""
    try:
        items = headers.multi_items() if hasattr(headers, "multi_items") else list(headers.items())
    except Exception:
        return []
    out: list[list[str]] = []
    for name, value in items:
        name, value = str(name), str(value)
        if name.lower() in _MASKED_HEADERS:
            scheme = value.split(" ", 1)[0] if " " in value else ""
            value = f"{scheme} ***" if scheme else "***"
        out.append([name, value])
    return out


def body_part(body: bytes) -> tuple[bytes, bool]:
    """The recorded part of a body, and whether it was cut."""
    if len(body) > BODY_LIMIT:
        return body[:BODY_LIMIT], True
    return body, False


# ---------------------------------------------------------------------------
# Writing entries out
# ---------------------------------------------------------------------------


class TrafficRedactor:
    """Masks a set of secrets in an entry's bytes and in its metadata."""

    def __init__(self, secrets: Iterable[str]) -> None:
        values = [s for s in secrets if isinstance(s, str)]
        self._bytes = compile_secret_bytes_pattern(values)
        self._text = compile_secret_pattern(values)

    def data(self, data: bytes) -> bytes:
        return data if self._bytes is None else self._bytes.sub(b"***", data)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return value if self._text is None else self._text.sub("***", value)
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(v) for v in value]
        return value


def serialize_entry(
    entry: TrafficEntry,
    redactor: TrafficRedactor | None = None,
) -> dict[str, Any]:
    """An entry as JSON: ``hex`` and ``text`` (latin-1, so every byte
    survives) of the redacted bytes. Without a redactor, the device's
    registered secrets are used."""
    if redactor is None:
        redactor = TrafficRedactor(get_secret_registry().secrets_for(entry.device_id))
    data = redactor.data(entry.data)
    out: dict[str, Any] = {
        "seq": entry.seq,
        "t": entry.t,
        "direction": entry.direction,
        "channel": entry.channel,
        "hex": data.hex(),
        "text": data.decode("latin-1"),
    }
    if entry.chunk:
        out["chunk"] = True
    if entry.meta:
        out["meta"] = redactor.value(entry.meta)
    return out
