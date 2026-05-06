"""Discovery result models.

Phase 6 deterministic types only. Every device carries:

- An ``identification`` (``IdentificationMatch``) — the deterministic
  state (``identified`` / ``possible`` / ``unknown``) produced by
  ``TierMatcher.match()`` over the device's evidence log.
- An ``evidence_log`` of ``Evidence`` records, one per signal observed
  during the scan. This is the audit trail behind the UI's "Why?"
  reveal and the data plumbing for future catalog-growth telemetry.

The legacy heuristic types (``DriverMatch``, ``compute_confidence``,
``CONFIDENCE_WEIGHTS``, ``DiscoveredDevice.matched_drivers/sources/
confidence``) were removed in the Phase 6 engine swap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceState(str, Enum):
    """Identification state for a discovered device.

    The state itself is the confidence — there is no separate score.

    - ``identified``: a Tier 1, 2, or 3 probe matched a driver
      deterministically. ``IdentificationMatch.driver_id`` is set and the
      UI offers one-click Add.
    - ``possible``: one ambiguous strong signal (an OUI-only match, a
      generic UPnP MediaRenderer, etc). May offer 1+ candidate drivers,
      but requires user confirmation before adding.
    - ``unknown``: host responded to ping/ARP but no driver matched.
      The UI shows what we know (IP, MAC, OUI vendor, open ports) and
      lets the user pick a driver manually or hide the device.
    """

    IDENTIFIED = "identified"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


class SignalTier(str, Enum):
    """Which discovery tier produced a signal.

    Used in ``Evidence.tier`` so the UI's "Why?" reveal can show
    the user *how* a device was identified.
    """

    PASSIVE_LISTENER = "tier1"     # mDNS, SSDP, AMX DDP
    BROADCAST_PROBE = "tier2"      # PJLink Class 2, Crestron CIP, ONVIF, etc.
    ACTIVE_PROBE = "tier3"         # PJLink Class 1, Extron SIS, Samsung MDC, etc.
    ENRICHMENT = "tier4"           # SNMP, OUI, NetBIOS, reverse DNS


@dataclass
class Evidence:
    """A single piece of evidence collected during discovery.

    Every observed signal — mDNS service announcement, broadcast probe
    response, active TCP probe response, SNMP PEN match, OUI lookup —
    appends one Evidence record to the device. This is the audit trail
    behind the "Why?" UI link and the data plumbing for future
    catalog-growth telemetry.

    ``tier`` is the discovery tier (see ``SignalTier``).
    ``source`` is a stable, human-readable identifier for the signal
    (e.g. ``"mdns:_netaudio-cmc._udp"``, ``"broadcast:crestron_cip"``,
    ``"probe:pjlink_class1"``, ``"snmp:pen:21317"``).
    ``data`` is whatever raw evidence the signal produced (TXT records,
    response bytes, parsed sysObjectID, etc).
    ``at`` is a unix timestamp for ordering and audit.
    """

    tier: SignalTier
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "source": self.source,
            "data": self.data,
            "at": self.at,
        }


@dataclass
class IdentificationMatch:
    """The tier-based identification result for a single device.

    There is exactly one IdentificationMatch per device.

    Fields by state:
    - ``identified``: ``driver_id`` is set; ``candidates`` is empty.
      ``source`` references the deterministic signal that matched.
      ``alternatives`` may list additional driver_ids the user can
      switch to — populated when a generic strong probe (PJLink,
      unfiltered ONVIF) won the strong-tier race but a vendor-specific
      driver also matched on a Tier 4 soft signal. Empty in the common
      vendor-specific case.
    - ``possible``: ``driver_id`` is None; ``candidates`` has 1+ ids;
      ``source`` references the soft signal (OUI, generic UPnP, etc).
      ``alternatives`` is unused — the dropdown reads from
      ``candidates``.
    - ``unknown``: ``driver_id`` is None; ``candidates`` is empty;
      ``reason`` explains why nothing matched.

    All states carry the full ``evidence`` list — the audit trail of
    every signal observed for the device, regardless of whether any
    matched a driver. This is what the "Why?" UI link reveals.
    """

    state: DeviceState
    driver_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    source: str = ""
    reason: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    @classmethod
    def identified(
        cls,
        driver_id: str,
        source: str,
        evidence: list[Evidence] | None = None,
        alternatives: list[str] | None = None,
    ) -> "IdentificationMatch":
        return cls(
            state=DeviceState.IDENTIFIED,
            driver_id=driver_id,
            source=source,
            alternatives=list(alternatives or []),
            evidence=list(evidence or []),
        )

    @classmethod
    def possible(
        cls,
        candidates: list[str],
        source: str,
        evidence: list[Evidence] | None = None,
    ) -> "IdentificationMatch":
        return cls(
            state=DeviceState.POSSIBLE,
            candidates=list(candidates),
            source=source,
            evidence=list(evidence or []),
        )

    @classmethod
    def unknown(
        cls,
        reason: str = "no_signal_matched",
        evidence: list[Evidence] | None = None,
    ) -> "IdentificationMatch":
        return cls(
            state=DeviceState.UNKNOWN,
            reason=reason,
            evidence=list(evidence or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "driver_id": self.driver_id,
            "candidates": list(self.candidates),
            "alternatives": list(self.alternatives),
            "source": self.source,
            "reason": self.reason,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class DiscoveredDevice:
    """A device found during network discovery."""

    ip: str
    mac: str | None = None
    hostname: str | None = None

    # Identification (accumulated from multiple sources)
    manufacturer: str | None = None
    model: str | None = None
    device_name: str | None = None
    firmware: str | None = None
    serial_number: str | None = None

    # Network info
    open_ports: list[int] = field(default_factory=list)
    banners: dict[int, str] = field(default_factory=dict)  # port -> banner text

    # Protocol identification (legacy display field — populated by probes,
    # consumed by the UI as a comma-separated tag list).
    protocols: list[str] = field(default_factory=list)

    # mDNS / SSDP info
    mdns_services: list[str] = field(default_factory=list)
    ssdp_info: dict[str, Any] | None = None

    # SNMP info
    snmp_info: dict[str, Any] | None = None

    # Category hint (from OUI or protocol)
    category: str | None = None

    # Responding status
    alive: bool = True

    # Phase 6 deterministic identification.
    identification: IdentificationMatch | None = None
    evidence_log: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON API response."""
        return {
            "ip": self.ip,
            "mac": self.mac,
            "hostname": self.hostname,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "device_name": self.device_name,
            "firmware": self.firmware,
            "serial_number": self.serial_number,
            "open_ports": self.open_ports,
            "banners": {str(k): v for k, v in self.banners.items()},
            "protocols": self.protocols,
            "mdns_services": self.mdns_services,
            "ssdp_info": self.ssdp_info,
            "snmp_info": self.snmp_info,
            "category": self.category,
            "alive": self.alive,
            "identification": (
                self.identification.to_dict() if self.identification else None
            ),
            "evidence_log": [e.to_dict() for e in self.evidence_log],
        }


def merge_device_info(
    existing: DiscoveredDevice,
    new_info: dict[str, Any],
    source: str,  # kept for API compatibility — not stored on the device
) -> None:
    """Merge new information into an existing device record.

    Rules:
      - Never overwrite with None (only enrich)
      - More specific info wins (longer strings)
    """
    del source  # legacy parameter, retained so call sites keep their breadcrumb

    for key in ("mac", "hostname", "manufacturer", "model", "device_name",
                "firmware", "serial_number", "category"):
        new_val = new_info.get(key)
        if new_val is None:
            continue
        old_val = getattr(existing, key, None)
        if old_val is None:
            setattr(existing, key, new_val)
        elif isinstance(new_val, str) and isinstance(old_val, str):
            # More specific (longer) string wins
            if len(new_val) > len(old_val):
                setattr(existing, key, new_val)

    # Merge list fields (deduplicate)
    for key in ("open_ports", "protocols", "mdns_services"):
        new_items = new_info.get(key, [])
        existing_list = getattr(existing, key)
        for item in new_items:
            if item not in existing_list:
                existing_list.append(item)

    # Merge banners dict
    for port, banner in new_info.get("banners", {}).items():
        if port not in existing.banners:
            existing.banners[port] = banner

    # Merge SNMP / SSDP dicts
    if new_info.get("snmp_info") and not existing.snmp_info:
        existing.snmp_info = new_info["snmp_info"]
    if new_info.get("ssdp_info") and not existing.ssdp_info:
        existing.ssdp_info = new_info["ssdp_info"]
