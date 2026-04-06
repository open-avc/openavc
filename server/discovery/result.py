"""Discovery result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
            "banners": self.banners,
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
        }


# Confidence weights — each successful identification step adds to the score
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
    "hint_matched": 0.15,
    # New sources from scan depth techniques
    "tls_cert_matched": 0.15,
    "ssh_identified": 0.05,
    "netbios_resolved": 0.10,
    "smb_identified": 0.10,
    "entity_mib_found": 0.10,
    "www_auth_matched": 0.10,
    "favicon_matched": 0.10,
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
