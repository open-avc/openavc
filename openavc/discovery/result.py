"""Discovery result models.

Every device carries:

- An ``identification`` (``IdentificationMatch``) — the deterministic
  state (``identified`` / ``possible`` / ``unknown``) produced by
  ``TierMatcher.match()`` over the device's evidence log.
- An ``evidence_log`` of ``Evidence`` records, one per signal observed
  during the scan. This is the audit trail behind the UI's "Why?"
  reveal and the data plumbing for future catalog-growth telemetry.
- ``identified_by``: the driver it was identified as, named as the engine
  read it when it matched (``DiscoveryEngine.identify``).

THE rule for what a found device is identified as and called is
``identity_of``: its name (the heading of its Discovery card, the name its
sort reads, the name it is added to the project with), the maker, model and
category shown for it. Every record carries it (``to_dict``), so the REST
results, the live feed, the export, the assistant's results and the Device
Audit's network check all say the same; the add asks it for the name
(``api/discovery._discovered_device_name``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class DeviceState(str, Enum):
    """Identification state for a discovered device.

    The state itself is the confidence — there is no separate score.

    - ``identified``: a fingerprint match (mDNS / SSDP / AMX DDP /
      TCP probe / UDP probe / Python companion) identified a driver
      deterministically. ``IdentificationMatch.driver_id`` is set and
      the UI offers one-click Add.
    - ``possible``: no fingerprint matched, but a hint (an OUI, an SNMP
      enterprise number, a host name, a declared open port, a
      manufacturer name) points at 1+ candidate drivers. Requires user
      confirmation before adding.
    - ``unknown``: the device was found (ping, ARP, an announcement) but
      no signal matched a driver.
      The UI shows what we know (IP, MAC, OUI vendor, open ports) and
      lets the user pick a driver manually or hide the device.
    """

    IDENTIFIED = "identified"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


class SignalTier(str, Enum):
    """Kind of discovery signal that produced a piece of evidence.

    The serialized values are the user-facing labels — they appear in
    the API response (``Evidence.to_dict()['tier']``) and feed the
    "Why?" reveal in the discovery UI.

    - ``passive_listener``: announcements the device emits on its own
      that we listen for (mDNS service advertisements, SSDP NOTIFY,
      AMX DDP beacons).
    - ``broadcast_probe``: a query we broadcast on the network that
      the device chose to answer.
    - ``active_probe``: a query we sent directly to the device
      (TCP connect-and-read, TCP send/expect, Python companion
      per-host exchange).
    - ``enrichment``: secondary signals that narrow candidates but
      can't identify on their own (OUI, host name from reverse DNS or
      NetBIOS, SNMP enterprise number, a declared open port, a
      manufacturer name from a probe reply).
    """

    PASSIVE_LISTENER = "passive_listener"
    BROADCAST_PROBE = "broadcast_probe"
    ACTIVE_PROBE = "active_probe"
    ENRICHMENT = "enrichment"


@dataclass
class Evidence:
    """A single piece of evidence collected during discovery.

    Every observed signal — mDNS service announcement, broadcast probe
    response, active TCP probe response, SNMP PEN match, OUI lookup —
    appends one Evidence record to the device. This is the audit trail
    behind the "Why?" UI link and the data plumbing for future
    catalog-growth telemetry.

    ``tier`` carries the kind of signal observed (see ``SignalTier``).
    ``source`` is a stable, human-readable identifier for the signal
    (e.g. ``"mdns:_netaudio-cmc._udp."``, ``"broadcast:custom_<id>_udp"``,
    ``"probe:custom_<id>_tcp"``, ``"snmp_pen:17049"``).
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
    """The deterministic identification result for a single device.

    There is exactly one IdentificationMatch per device.

    Fields by state:
    - ``identified``: ``driver_id`` is set; ``candidates`` is empty.
      ``source`` references the signal that put ``driver_id`` first.
      ``alternatives`` lists the other driver_ids the user can switch
      to: every other driver a fingerprint identified (two drivers
      whose probes both answered, two filters on one shared
      announcement, an announcement and a probe naming different
      drivers), and the ``cross_vendor: true`` drivers when an
      enrichment signal put a vendor-specific peer ahead of them.
      Empty when one driver alone matched.
    - ``possible``: ``driver_id`` is None; ``candidates`` has 1+ ids;
      ``source`` references the enrichment signal with the fewest
      candidates (``oui:00:11:22``, ``snmp_pen:17049``). ``alternatives``
      is unused — the dropdown reads from ``candidates``.
    - ``unknown``: ``driver_id`` is None; ``candidates`` is empty;
      ``reason`` explains why nothing matched.

    ``cross_vendor`` (``identified`` only) is True when the fingerprint
    that said what the device is was a ``cross_vendor`` one, a protocol
    many makers' equipment speaks, whichever driver leads (one an
    enrichment signal put ahead of it included). It names the device
    (``identity_of``) and is not sent itself.

    ``evidence`` is the part of the device's log that fed this result:
    the fingerprint records that matched for ``identified``, the best
    match's first (plus the enrichment records when a cross-vendor match
    was demoted), the
    enrichment records for
    ``possible``, and the whole log for ``unknown``. The full audit trail
    the "Why?" UI link reveals is ``DiscoveredDevice.evidence_log``;
    ``discovery/explain.py`` lists every driver each of its records
    points at.
    """

    state: DeviceState
    driver_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    source: str = ""
    reason: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    cross_vendor: bool = False

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

    def through_a_shared_protocol(self) -> "IdentificationMatch":
        """This identification, marked as said by a ``cross_vendor``
        fingerprint (``cross_vendor``)."""
        return replace(self, cross_vendor=True)

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


@dataclass(frozen=True)
class IdentifyingDriver:
    """The driver a device was identified as, as the scan names the device
    by it: the driver's name and maker, and whether the fingerprint that
    identified it is one many makers' equipment shares (``cross_vendor``),
    which says what protocol the device speaks rather than what it is."""

    name: str
    manufacturer: str = ""
    cross_vendor: bool = False

    def maker(self) -> str | None:
        """The device's maker as this driver says it, or None: a driver
        identified through a shared fingerprint names a protocol's maker
        ("Generic" and the like), not the device's."""
        if self.cross_vendor:
            return None
        return self.manufacturer or None


def _maker_and_model(manufacturer: str | None, model: str) -> str:
    """The model, with its maker in front unless the model already says it."""
    maker = manufacturer or ""
    if maker and maker.lower() not in model.lower():
        return f"{maker} {model}"
    return model


@dataclass(frozen=True)
class DeviceIdentity:
    """What a found device is called and shown as: ``name`` (its card's
    heading, the name its sort reads and the name it is added with), the
    ``manufacturer``, ``model`` and ``category`` shown for it."""

    name: str
    manufacturer: str | None
    model: str | None
    category: str | None


def identity_of(device: "DiscoveredDevice", adding_with: str | None = None) -> DeviceIdentity:
    """What ``device`` is called and shown as, from what the scan read and
    the driver its match identified it as (``identified_by``).

    The name: a device identified as a driver is called by the driver's name,
    which stays put while the model string a scan reads can change from one
    scan to the next. One identified only through a fingerprint many makers
    share keeps the maker and model it reported, the driver's name taking
    over only when it reported no model. A device nobody identified is called
    by its maker and model, else ``adding_with`` (the name of the driver it
    is being added with), else the name it gave itself (a factory default is
    common there, so it comes late), else its maker.

    The maker is the one the scan read, else the identifying driver's, unless
    that driver was identified through a fingerprint many makers share (its
    maker is a protocol's, "Generic" and the like). The model and category
    are as the scan holds them.
    """
    by = device.identified_by
    return DeviceIdentity(
        name=_name_of(device, adding_with),
        manufacturer=device.manufacturer or (by.maker() if by is not None else None),
        model=device.model,
        category=device.category,
    )


def _name_of(device: "DiscoveredDevice", adding_with: str | None) -> str:
    """``identity_of``'s name."""
    by = device.identified_by
    if by is not None and not by.cross_vendor:
        return by.name
    if device.model:
        return _maker_and_model(device.manufacturer, device.model)
    named = by.name if by is not None else adding_with
    return named or _self_named(device)


def _self_named(device: "DiscoveredDevice") -> str:
    """A device's name with no model and no driver to go by: the name it gave
    itself, else its maker's."""
    if device.device_name:
        return device.device_name
    return f"{device.manufacturer} Device" if device.manufacturer else "Unknown Device"


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

    # Deterministic identification result + per-device evidence log.
    identification: IdentificationMatch | None = None
    evidence_log: list[Evidence] = field(default_factory=list)
    # The driver ``identification`` names, set beside it on every match
    # (None when it names none), so the record's name follows the match.
    identified_by: IdentifyingDriver | None = None

    # What each port received from the scan's identification checks, by port
    # (``port_guard.PortGuard.record``): a port a connected project device
    # holds, whether the port's greeting asked for a login, and how each
    # check reached it.
    port_probes: dict[int, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON API response. ``name``, ``manufacturer``,
        ``model`` and ``category`` are the device's identity
        (``identity_of``)."""
        identity = identity_of(self)
        return {
            "ip": self.ip,
            "name": identity.name,
            "mac": self.mac,
            "hostname": self.hostname,
            "manufacturer": identity.manufacturer,
            "model": identity.model,
            "device_name": self.device_name,
            "firmware": self.firmware,
            "serial_number": self.serial_number,
            "open_ports": self.open_ports,
            "banners": {str(k): v for k, v in self.banners.items()},
            "protocols": self.protocols,
            "mdns_services": self.mdns_services,
            "ssdp_info": self.ssdp_info,
            "snmp_info": self.snmp_info,
            "category": identity.category,
            "alive": self.alive,
            "identification": (
                self.identification.to_dict() if self.identification else None
            ),
            "evidence_log": [e.to_dict() for e in self.evidence_log],
            "port_probes": {str(k): v for k, v in self.port_probes.items()},
        }


def merge_device_info(
    existing: DiscoveredDevice,
    new_info: dict[str, Any],
    source: str,  # kept for API compatibility — not stored on the device
    *,
    fill_only: bool = False,
) -> None:
    """Merge new information into an existing device record.

    Rules:
      - Never overwrite with None (only enrich)
      - More specific info wins (longer strings)
      - ``fill_only=True`` (driver-probe / companion / ARP-OUI sources):
        only set an identity field that is still empty; never overwrite a
        value an earlier, higher-trust source already provided. Driver-
        declared ``tcp_probe``/``udp_probe`` text and community Python
        companions run *after* passive collection (mDNS/SSDP/AMX/SNMP), so
        the length-only rule would otherwise let their text clobber
        authoritative passive identity. The ARP/OUI merges use it too: OUI
        names the NIC vendor (often not the device vendor), the late ARP
        harvest runs after passive collection, and on a re-scan the phase-4
        pass sees identity carried over from the previous scan. They all
        still enrich fields nothing else populated.
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
        elif fill_only:
            # Lower-trust source: keep the existing authoritative value.
            continue
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


# Keys ``device_info_from_evidence`` lifts out of probe evidence into a
# dict that ``merge_device_info`` knows how to absorb.
_PROBE_DEVICE_INFO_KEYS: tuple[str, ...] = (
    "mac", "hostname", "manufacturer", "model", "device_name",
    "firmware", "serial_number", "category",
)


def device_info_from_evidence(ev: Evidence) -> dict[str, Any]:
    """Pull merge-able device info out of a probe-emitted evidence record.

    Probe evidence shows up in three layouts depending on who built it:

    - **UDP broadcast** (``probe_runner.run_udp_broadcast_probe``): the
      device-info fields land in ``data["txt"]`` at top level —
      ``response`` only holds ``{"ip": sender}``.
    - **TCP active** (``probe_runner.run_tcp_active_probe``): reserved
      keys (``manufacturer`` / ``make``) live at the top of
      ``data["response"]``; everything else from ``extract:`` rules
      goes under ``data["response"]["extracted"]``. ``response.text``
      is the raw payload and is ignored here.
    - **Companion** (``ProbeContext.emit_broadcast`` /
      ``emit_active``): the companion author chooses the shape — info
      may sit at ``data["response"]`` top level (typical), under
      ``response["extracted"]`` if they mirror the runner, or in
      ``data["txt"]`` if they mirror the UDP runner.

    All three layouts are scanned; first non-None value per key wins.
    The probe-runner ``make`` alias is folded into ``manufacturer`` so
    the device card renders a single vendor regardless of which key
    the driver extracted.
    """
    info: dict[str, Any] = {}

    def _absorb(src: Any) -> None:
        if not isinstance(src, dict):
            return
        for key in _PROBE_DEVICE_INFO_KEYS:
            val = src.get(key)
            if val is not None and key not in info:
                info[key] = val
        if "manufacturer" not in info:
            make = src.get("make")
            if make is not None:
                info["manufacturer"] = make

    response = ev.data.get("response")
    _absorb(response)
    if isinstance(response, dict):
        _absorb(response.get("extracted"))
    _absorb(ev.data.get("txt"))
    return info
