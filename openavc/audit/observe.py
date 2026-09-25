"""What an audit collects while a driver runs: its traffic and its contract faults.

Two sources, both the platform's own, never a second parser:

- **Traffic**, from the device traffic recorder (``core/device_traffic.py``):
  a subscription for the audited device that also takes the raw receive
  chunks, held for the whole driver test, so the recorder's per-device ring
  does not limit the report. The session keeps at most ``TRAFFIC_CAP_BYTES``
  of payload; past that it stops keeping entries and says so, so a device
  that streams meter data cannot fill memory.
- **Contract events**, from the driver's ``contract_observer``
  (``drivers/base.py``): replies no response rule matched, writes to state the
  driver does not declare, values not of their declared type, values that did
  not convert, commands the driver does not have, state for children it never
  registered. Each one is counted; the first ``EVENT_DETAIL_KEPT`` of each kind
  keep their detail.

Two faults are read from the traffic rather than reported by the driver:

- :func:`command_sent_nothing`: a command that returned success while no byte
  left for the device. ``driver-roadmap`` calls this the gate nothing catches
  (a declared command the driver's code never handles still returns success).
- :func:`replies_to_nobody`: a reply that arrived when no request had gone out
  for a while. Many devices announce changes unprompted, so this is a hint,
  never a verdict: a driver that takes such a message as the answer to its
  next request attributes every reply after it to the wrong request.

Secrets: the redaction set is snapshotted from the device's registered
credentials while the device exists (removing it forgets them), and the audit
adds the values the person typed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from openavc.core.device_traffic import (
    TX,
    TrafficEntry,
    TrafficRedactor,
    TrafficSubscription,
    get_traffic_recorder,
    serialize_entry,
)
from openavc.utils.log_redaction import get_secret_registry

# Payload bytes an audit keeps (raw chunks included).
TRAFFIC_CAP_BYTES = 20 * 1024 * 1024
# Contract events kept in full per kind; beyond it they are only counted.
EVENT_DETAIL_KEPT = 50
# How long after a request a reply still counts as its answer.
REPLY_WINDOW_SECONDS = 2.0

# Each contract event kind, in words (the wizard's timeline and the report).
CONTRACT_TEXT = {
    "unmatched_response": "A reply matched none of the driver's response rules",
    "undeclared_state": "The driver wrote a status value it does not declare",
    "type_mismatch": "A status value is not of the type the driver declares",
    "coercion_failure": "A reply's value could not be converted to its declared type",
    "unknown_command": "The driver was asked for a command it does not have",
    "child_unregistered": "The driver wrote status for a channel or zone it never registered",
}

# Channels a reply-to-nobody check reads: request/response byte streams.
# HTTP pairs every response with its request by construction, MQTT is
# publish/subscribe, and the push channels exist to deliver unprompted.
_REQUEST_REPLY_CHANNELS = frozenset({"tcp", "serial", "udp", "osc", "snmp", "ssh"})


@dataclass
class ContractEvent:
    """One contract fault as the driver reported it."""

    t: float
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"t": self.t, "kind": self.kind, "detail": dict(self.detail)}


class AuditObserver:
    """Collects one audited device's traffic and contract events."""

    def __init__(
        self,
        device_id: str,
        *,
        cap_bytes: int = TRAFFIC_CAP_BYTES,
        on_entry: Callable[[TrafficEntry], None] | None = None,
        on_event: Callable[[ContractEvent], None] | None = None,
    ) -> None:
        self.device_id = device_id
        self.traffic: list[TrafficEntry] = []
        self.traffic_bytes = 0
        self.truncated_at: float | None = None
        self.dropped_entries = 0
        self.events: list[ContractEvent] = []
        self.event_counts: dict[str, int] = {}
        self._cap = cap_bytes
        self._on_entry = on_entry
        self._on_event = on_event
        self._subscription: TrafficSubscription | None = None
        self._secrets: set[str] = set()

    # -- wiring ---------------------------------------------------------------

    def start(self) -> None:
        """Subscribe to the device's traffic, raw chunks included."""
        if self._subscription is None:
            self._subscription = get_traffic_recorder().subscribe(
                self.device_id, callback=self._on_traffic, chunks=True,
            )

    def stop(self) -> None:
        """Stop collecting (the secrets snapshot is kept for the report)."""
        self.snapshot_secrets()
        if self._subscription is not None:
            get_traffic_recorder().unsubscribe(self._subscription)
            self._subscription = None

    def attach(self, driver: Any) -> None:
        """Make this the driver's contract observer."""
        driver.contract_observer = self._on_contract

    # -- collection -----------------------------------------------------------

    def _on_traffic(self, entry: TrafficEntry) -> None:
        if self.truncated_at is not None:
            self.dropped_entries += 1
            return
        if self.traffic_bytes + len(entry.data) > self._cap:
            self.truncated_at = entry.t
            self.dropped_entries += 1
            return
        self.traffic.append(entry)
        self.traffic_bytes += len(entry.data)
        if self._on_entry is not None:
            self._on_entry(entry)

    def _on_contract(self, kind: str, detail: dict[str, Any]) -> None:
        count = self.event_counts.get(kind, 0) + 1
        self.event_counts[kind] = count
        if count > EVENT_DETAIL_KEPT:
            return
        event = ContractEvent(t=time.time(), kind=kind, detail=dict(detail))
        self.events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    # -- secrets --------------------------------------------------------------

    def snapshot_secrets(self) -> None:
        """Take the device's registered credentials while it still exists."""
        self._secrets |= get_secret_registry().secrets_for(self.device_id)

    def add_secrets(self, values: Iterable[str]) -> None:
        self._secrets |= {v for v in values if isinstance(v, str) and v}

    @property
    def secrets(self) -> set[str]:
        return set(self._secrets)

    def redactor(self) -> TrafficRedactor:
        self.snapshot_secrets()
        return TrafficRedactor(self._secrets)

    # -- reading ----------------------------------------------------------------

    def frames(self) -> list[TrafficEntry]:
        """The traffic without the raw chunks (what the driver was handed)."""
        return [e for e in self.traffic if not e.chunk]

    def serialized(self) -> list[dict[str, Any]]:
        redactor = self.redactor()
        return [serialize_entry(e, redactor) for e in self.traffic]


def command_sent_nothing(
    entries: Iterable[TrafficEntry],
    started: float,
    finished: float,
    *,
    grace: float = 0.5,
) -> bool:
    """True when no byte went to the device between ``started`` and
    ``finished`` (plus ``grace`` for a send completing after the call)."""
    end = finished + grace
    return not any(
        e.direction == TX and not e.chunk and started <= e.t <= end for e in entries
    )


def replies_to_nobody(
    entries: Iterable[TrafficEntry],
    *,
    window: float = REPLY_WINDOW_SECONDS,
) -> list[TrafficEntry]:
    """Replies on a request/response channel with no request in the
    ``window`` seconds before them (the first ones before any request
    included: a greeting, a banner, an unprompted status line)."""
    out: list[TrafficEntry] = []
    last_tx: dict[str, float] = {}
    for entry in sorted(entries, key=lambda e: (e.t, e.seq)):
        if entry.chunk or entry.channel not in _REQUEST_REPLY_CHANNELS:
            continue
        if entry.direction == TX:
            last_tx[entry.channel] = entry.t
            continue
        sent = last_tx.get(entry.channel)
        if sent is None or entry.t - sent > window:
            out.append(entry)
    return out
