"""Discovery result models.

Two generations of types live here during the discovery redesign:

1. Legacy: ``DriverMatch`` and ``DiscoveredDevice.confidence``/``sources`` —
   the additive-scoring heuristic system. Still wired into the running
   engine and UI until the redesign reaches the orchestrator swap.

2. New: ``DeviceState``, ``IdentificationMatch``, and ``Evidence`` —
   deterministic three-state identification. Populated alongside legacy
   fields as new tier-based probes land. The UI reads whichever is
   present; once every probe writes the new types, the legacy fields
   are removed.

See discovery-redesign-plan.md for the full architecture.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# New deterministic types (discovery redesign)
# ---------------------------------------------------------------------------


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

    Replaces the legacy ``DriverMatch`` list of (driver_id, confidence)
    tuples. There is exactly one IdentificationMatch per device.

    Fields by state:
    - ``identified``: ``driver_id`` is set; ``candidates`` is empty;
      ``source`` references the deterministic signal that matched.
    - ``possible``: ``driver_id`` is None; ``candidates`` has 1+ ids;
      ``source`` references the soft signal (OUI, generic UPnP, etc).
    - ``unknown``: ``driver_id`` is None; ``candidates`` is empty;
      ``reason`` explains why nothing matched.

    All states carry the full ``evidence`` list — the audit trail of
    every signal observed for the device, regardless of whether any
    matched a driver. This is what the "Why?" UI link reveals.
    """

    state: DeviceState
    driver_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    source: str = ""
    reason: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    @classmethod
    def identified(
        cls,
        driver_id: str,
        source: str,
        evidence: list[Evidence] | None = None,
    ) -> "IdentificationMatch":
        return cls(
            state=DeviceState.IDENTIFIED,
            driver_id=driver_id,
            source=source,
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
            "source": self.source,
            "reason": self.reason,
            "evidence": [e.to_dict() for e in self.evidence],
        }


# ---------------------------------------------------------------------------
# Legacy types (kept until orchestrator swap)
# ---------------------------------------------------------------------------


@dataclass
class DriverMatch:
    """A potential driver match for a discovered device."""

    driver_id: str
    driver_name: str
    confidence: float  # 0.0 to 1.0
    match_reasons: list[str] = field(default_factory=list)
    suggested_config: dict[str, Any] = field(default_factory=dict)
    source: str = "installed"  # "installed" or "community"
    description: str = ""  # For community drivers, from index.json


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

    # Discovery sources that contributed info
    sources: list[str] = field(default_factory=list)

    # Protocol identification
    protocols: list[str] = field(default_factory=list)

    # mDNS / SSDP info (Chunk 4)
    mdns_services: list[str] = field(default_factory=list)
    ssdp_info: dict[str, Any] | None = None

    # SNMP info (Chunk 5)
    snmp_info: dict[str, Any] | None = None

    # Driver matching (Chunk 3)
    matched_drivers: list[DriverMatch] = field(default_factory=list)

    # Overall confidence (0.0 to 1.0)
    confidence: float = 0.0

    # Category hint (from OUI or protocol)
    category: str | None = None

    # Responding status
    alive: bool = True

    # ---- Discovery redesign: deterministic identification fields ----
    # Populated alongside legacy fields as new tier-based probes land.
    # See ``IdentificationMatch`` and ``Evidence`` above.
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
            "sources": self.sources,
            "protocols": self.protocols,
            "mdns_services": self.mdns_services,
            "ssdp_info": self.ssdp_info,
            "snmp_info": self.snmp_info,
            "matched_drivers": [
                {
                    "driver_id": m.driver_id,
                    "driver_name": m.driver_name,
                    "confidence": m.confidence,
                    "match_reasons": m.match_reasons,
                    "suggested_config": m.suggested_config,
                    "source": m.source,
                    "description": m.description,
                }
                for m in self.matched_drivers
            ],
            "confidence": self.confidence,
            "category": self.category,
            "alive": self.alive,
            "identification": (
                self.identification.to_dict() if self.identification else None
            ),
            "evidence_log": [e.to_dict() for e in self.evidence_log],
        }


# Confidence weights — each successful identification step adds to the score.
# This additive heuristic system is being replaced by deterministic tier-based
# matching (see discovery-redesign-plan.md). Dead entries that no code ever
# set (favicon_matched, hint_matched) and entries from the removed
# non-deterministic probes (tls_cert_matched, ssh_identified, smb_identified,
# www_auth_matched) have been deleted.
CONFIDENCE_WEIGHTS = {
    "alive": 0.05,
    "mac_known": 0.05,
    "oui_av_mfg": 0.15,
    "av_port_open": 0.10,
    "banner_matched": 0.15,
    "probe_confirmed": 0.20,
    "snmp_identified": 0.10,
    "mdns_advertised": 0.10,
    "ssdp_identified": 0.10,
    "model_known": 0.10,
    "driver_matched": 0.20,
    "netbios_resolved": 0.10,
    "entity_mib_found": 0.10,
}


def compute_confidence(sources: list[str]) -> float:
    """Compute confidence score from a list of source tags.

    Each source tag maps to a weight. Score is capped at 1.0.
    """
    score = 0.0
    for source in sources:
        score += CONFIDENCE_WEIGHTS.get(source, 0.0)
    return min(score, 1.0)


def merge_device_info(
    existing: DiscoveredDevice,
    new_info: dict[str, Any],
    source: str,
) -> None:
    """Merge new information into an existing device record.

    Rules:
      - Never overwrite with None (only enrich)
      - More specific info wins (longer strings)
      - Track all sources that contributed
      - Recalculate confidence after merge
    """
    if source not in existing.sources:
        existing.sources.append(source)

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

    # Recalculate confidence
    existing.confidence = compute_confidence(existing.sources)
