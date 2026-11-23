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

THE rule for which source a device's maker, model and names are taken from
when a scan's sources disagree is ``merge_device_info``'s, by the source's
trust (``SOURCE_TRUST``), never by which string is longer; and the model a
device identified through an mDNS service shows is that service's
(``identity_of``).
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

    ``record`` (``identified`` only) is the record that said what the
    device is: the best fingerprint match's (a cross-vendor one when a hint
    put a peer driver ahead of it), or the connected project device's. It
    is ``evidence``'s first, so it is not sent itself; ``identity_of`` reads
    the model off it.

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
    record: Evidence | None = None

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

    def said_by(self, record: Evidence) -> "IdentificationMatch":
        """This identification, naming the record that said what the device
        is (``record``)."""
        return replace(self, record=record)

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
    maker is a protocol's, "Generic" and the like). The model is the one the
    mDNS service that identified the device gave (``_model_of``), else the
    scan's; the category is the scan's, where the identifying driver's ranks
    over a probe's and the MAC lookup's (``SOURCE_TRUST``).
    """
    by = device.identified_by
    model = _model_of(device)
    return DeviceIdentity(
        name=_name_of(device, model, adding_with),
        manufacturer=device.manufacturer or (by.maker() if by is not None else None),
        model=model,
        category=device.category,
    )


def _model_of(device: "DiscoveredDevice") -> str | None:
    """The model shown for ``device``: the one the mDNS service that
    identified it gave, else the one the scan holds.

    A device's services can each name a model (its product code on the
    service a driver knows it by, a board code on another), and the scan
    holds one of them. The service that said what the device is says which,
    and a re-match to another driver reads its own service's.
    """
    from openavc.discovery.tier_matcher import KIND_MDNS

    match = device.identification
    record = match.record if match is not None else None
    if record is not None and record.data.get("kind") == KIND_MDNS:
        model = mdns_txt_identity(record.data.get("txt") or {}).get("model")
        if model:
            return model
    return device.model


def _name_of(device: "DiscoveredDevice", model: str | None, adding_with: str | None) -> str:
    """``identity_of``'s name, ``model`` being the one it shows."""
    by = device.identified_by
    if by is not None and not by.cross_vendor:
        return by.name
    if model:
        return _maker_and_model(device.manufacturer, model)
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

    # The kind of equipment: the identifying driver's, else a probe's, else
    # the MAC lookup's (``SOURCE_TRUST``)
    category: str | None = None

    # Responding status
    alive: bool = True

    # Deterministic identification result + per-device evidence log.
    identification: IdentificationMatch | None = None
    evidence_log: list[Evidence] = field(default_factory=list)
    # The driver ``identification`` names, set beside it on every match
    # (None when it names none), so the record's name follows the match.
    identified_by: IdentifyingDriver | None = None
    # The source each identity field was last taken from (``SOURCE_TRUST``),
    # by field. Kept across scans with the fields themselves; not sent.
    field_sources: dict[str, str] = field(default_factory=dict)

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


# The sources a device's identity fields are read from, least trusted first.
# The first three only fill a field the device and its network said nothing
# of, and among themselves the more trusted replaces the less, whatever order
# the scan reads them in: the maker and category registered for the device's
# MAC address (the network card's maker, and the category of whichever
# driver registered its prefix first); then what a driver's probe or
# companion read from its reply (the driver's own literals left out,
# ``device_info_from_evidence``; a companion's category is its own driver's,
# a shared protocol's included); then the category the driver that
# identified the device declares (``DiscoveryEngine.identify``). Then what
# the device and its network say of it: an mDNS service's TXT keys and
# instance name, whose meaning each service type sets for itself; the
# network's names for the address (reverse DNS, NetBIOS) and the ARP table's
# MAC; its SNMP agent (sysName, the Entity MIB); its AMX beacon (Make,
# Model), written for control systems; and its UPnP description
# (manufacturer, modelName, friendlyName), written for a control point to
# show.
SOURCE_TRUST: tuple[str, ...] = (
    "oui", "probe", "driver_category",
    "mdns", "arp", "snmp", "amx_ddp", "ssdp",
)
_FILLS_ONLY = 3  # the first three
# A value set with no source recorded (built directly): any of the sources
# that read the device replaces it, none of those that only fill does.
_UNRECORDED = _FILLS_ONLY - 0.5

_IDENTITY_KEYS = ("mac", "hostname", "manufacturer", "model", "device_name",
                  "firmware", "serial_number", "category")


def _trust(source: str) -> int:
    """Where ``source`` stands in ``SOURCE_TRUST``; a source nobody ranked is refused."""
    try:
        return SOURCE_TRUST.index(source)
    except ValueError:
        raise ValueError(f"{source!r} is not a ranked identity source") from None


def merge_device_info(
    existing: DiscoveredDevice,
    new_info: dict[str, Any],
    source: str,
) -> None:
    """Merge what ``source`` read about a device into its record.

    An identity field (MAC, host name, maker, model, the name it gave itself,
    firmware, serial, category) takes the new value when it is empty, or when
    ``source`` is trusted as much as the one it was taken from (the same
    source read again, a later scan included, replaces its own value) or
    more (``SOURCE_TRUST``). A less trusted source fills only an empty field,
    so the result is the same whatever order different sources arrive in and
    whichever string is longer. Each field records its source
    (``DiscoveredDevice.field_sources``). An empty value is never taken.

    Open ports, protocols and mDNS service types are added to; a banner, the
    SNMP and the SSDP details are kept as first read.
    """
    trust: int | None = None
    for key in _IDENTITY_KEYS:
        new_val = new_info.get(key)
        if new_val is None or new_val == "":
            continue
        if trust is None:
            trust = _trust(source)
        if getattr(existing, key, None) not in (None, ""):
            held = existing.field_sources.get(key)
            if trust < (_trust(held) if held is not None else _UNRECORDED):
                continue
        setattr(existing, key, new_val)
        existing.field_sources[key] = source

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
    the driver extracted. A field the driver's own rule set to a literal
    (``driver_supplied``: ``extract_manufacturer`` naming ``manufacturer``
    or ``make``) is the driver's claim, not the device's, and is left out.
    """
    supplied = set(ev.data.get("driver_supplied") or ())
    keys = [key for key in _PROBE_DEVICE_INFO_KEYS if key not in supplied]
    fold_make = "make" not in supplied
    info: dict[str, Any] = {}
    for layout in _probe_layouts(ev):
        _absorb_layout(layout, info, keys, fold_make)
    return info


def _probe_layouts(ev: Evidence) -> tuple[Any, Any, Any]:
    """The three places probe evidence carries device info, in the order
    ``device_info_from_evidence`` reads them."""
    response = ev.data.get("response")
    extracted = response.get("extracted") if isinstance(response, dict) else None
    return response, extracted, ev.data.get("txt")


def _absorb_layout(src: Any, info: dict[str, Any], keys: list[str], fold_make: bool) -> None:
    """Add to ``info`` each of ``keys`` that ``src`` carries and ``info``
    lacks, then its ``make`` as the manufacturer when ``fold_make`` and no
    manufacturer is known yet."""
    if not isinstance(src, dict):
        return
    for key in keys:
        if src.get(key) is not None:
            info.setdefault(key, src[key])
    make = src.get("make") if fold_make else None
    if make is not None:
        info.setdefault("manufacturer", make)


# The TXT keys an mDNS service names its device by, by the field each fills,
# the long key ahead of the short one.
_MDNS_TXT_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("manufacturer", ("manufacturer", "mf")),
    ("model", ("model", "md")),
    ("firmware", ("firmware", "fw")),
    ("serial_number", ("serialNumber", "sn")),
)


def mdns_txt_identity(txt: dict[str, str]) -> dict[str, str]:
    """What one mDNS service's TXT record says its device is: the maker,
    model, firmware and serial it names, by field. A key with an empty value
    names nothing."""
    info: dict[str, str] = {}
    for name, keys in _MDNS_TXT_KEYS:
        value = next((txt[k] for k in keys if txt.get(k)), None)
        if value:
            info[name] = value
    return info
