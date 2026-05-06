"""Phase 6 ``discovery:`` schema parser + ``SignalIndex`` builder.

The new schema is opinionated: every driver declares at least one
strong (Tier 1/2/3) signal or sets ``manual_only: true``. Validation
happens at driver-load time; collisions raise from
``SignalIndex.add_rule``. The matcher is deterministic — there is no
score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from server.discovery.tier_matcher import (
    SignalIndex,
    SignalRule,
)

log = logging.getLogger("discovery.hints")


# Tier 2 broadcast probe IDs. Each driver opting in declares the boolean
# flag (``pjlink_class2: true`` etc.) or the ``onvif: {manufacturer: X}``
# disambiguator.
ALLOWED_BROADCAST_PROBES: frozenset[str] = frozenset({
    "pjlink_class2",
    "crestron_cip",
    "onvif",
    "hiqnet",
    "symetrix",
})

# Tier 3 active probe IDs. Match the keys in
# ``protocol_prober.py::_PROBE_ID_FOR_PROTOCOL``.
ALLOWED_ACTIVE_PROBES: frozenset[str] = frozenset({
    "pjlink_class1",
    "extron_sis",
    "tesira_ttp",
    "qrc",
    "kramer_p3000",
    "shure_dcs",
    "samsung_mdc",
    "visca",
    "crestron_cip_tcp",
    "yamaha_rcp",
})

# Drivers whose IDs start with these prefixes are templates, not real
# devices. They opt out of the discovery match entirely.
_TEMPLATE_PREFIXES: tuple[str, ...] = ("generic_",)

# Ports too generic to use as a soft enrichment signal — every web /
# admin / SSH device on the network would match. AV-specific ports
# (1710, 4352, 23 for telnet-on-AV-gear, etc.) are fine.
DISALLOWED_OPEN_PORTS: frozenset[int] = frozenset({22, 80, 443})


@dataclass
class DiscoveryHint:
    """The Phase 6 deterministic discovery hints for a single driver."""

    driver_id: str
    driver_name: str = ""
    manufacturer: str = ""
    category: str = ""
    transport: str = "tcp"
    manual_only: bool = False

    # Strong (Tier 1) signals.
    mdns_services: list[dict[str, Any]] = field(default_factory=list)
    ssdp_device_types: list[str] = field(default_factory=list)
    amx_ddp: dict[str, str] | None = None  # {"make": ..., "model_pattern": ...}

    # Strong (Tier 2) deterministic broadcast probe opt-ins.
    broadcast_probes: list[str] = field(default_factory=list)
    onvif_manufacturer: str | None = None  # extra disambiguation when ``onvif`` is on

    # Strong (Tier 3) active probes.
    active_probes: list[str] = field(default_factory=list)

    # Soft (Tier 4) enrichment hints.
    snmp_pen: int | None = None
    oui_prefixes: list[str] = field(default_factory=list)
    hostname_patterns: list[str] = field(default_factory=list)
    open_ports: list[int] = field(default_factory=list)
    # Manufacturer/make strings the driver claims when a strong-tier
    # probe response carries that field (PJLink ``%1MNFR?``, ONVIF
    # ``Manufacturer``, etc.). Stored lowercased + stripped; case-
    # insensitive exact-match at lookup time.
    vendor_aliases: list[str] = field(default_factory=list)


class DiscoveryHintError(ValueError):
    """Raised when a driver's ``discovery:`` block is structurally invalid."""


def parse_driver_discovery(driver_info: dict[str, Any]) -> DiscoveryHint | None:
    """Parse one driver-registry entry into a ``DiscoveryHint``.

    Returns ``None`` for template drivers (``generic_*``); otherwise a
    populated ``DiscoveryHint``. Raises ``DiscoveryHintError`` if the
    ``discovery:`` block is malformed or declares no strong signal and
    no ``manual_only`` flag.
    """
    driver_id = str(driver_info.get("id") or "").strip()
    if not driver_id:
        raise DiscoveryHintError("Driver missing required 'id' field")

    if any(driver_id.startswith(p) for p in _TEMPLATE_PREFIXES):
        return None

    discovery = driver_info.get("discovery") or {}
    if not isinstance(discovery, dict):
        raise DiscoveryHintError(
            f"{driver_id}: 'discovery' must be a mapping, got {type(discovery).__name__}"
        )

    hint = DiscoveryHint(
        driver_id=driver_id,
        driver_name=str(driver_info.get("name") or driver_id),
        manufacturer=str(driver_info.get("manufacturer") or ""),
        category=str(driver_info.get("category") or ""),
        transport=str(driver_info.get("transport") or "tcp"),
    )

    hint.manual_only = bool(discovery.get("manual_only", False))

    # mdns_services: list of either bare strings (treated as a service
    # type with no TXT filter) or dicts {service: ..., txt_match: {...}}.
    raw_mdns = discovery.get("mdns_services") or []
    if not isinstance(raw_mdns, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.mdns_services must be a list"
        )
    for entry in raw_mdns:
        if isinstance(entry, str):
            hint.mdns_services.append({"service": entry, "txt_match": {}})
        elif isinstance(entry, dict):
            service = entry.get("service")
            if not isinstance(service, str) or not service:
                raise DiscoveryHintError(
                    f"{driver_id}: mdns_services entry missing 'service' string"
                )
            txt_match = entry.get("txt_match") or {}
            if not isinstance(txt_match, dict):
                raise DiscoveryHintError(
                    f"{driver_id}: mdns_services entry 'txt_match' must be a mapping"
                )
            hint.mdns_services.append({
                "service": service,
                "txt_match": {str(k): str(v) for k, v in txt_match.items()},
            })
        else:
            raise DiscoveryHintError(
                f"{driver_id}: mdns_services entries must be strings or "
                f"{{service, txt_match}} mappings"
            )

    raw_ssdp = discovery.get("ssdp_device_types") or []
    if not isinstance(raw_ssdp, list) or not all(isinstance(s, str) for s in raw_ssdp):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.ssdp_device_types must be a list of strings"
        )
    hint.ssdp_device_types = list(raw_ssdp)

    if "amx_ddp" in discovery:
        amx = discovery["amx_ddp"]
        if not isinstance(amx, dict):
            raise DiscoveryHintError(f"{driver_id}: discovery.amx_ddp must be a mapping")
        make = amx.get("make")
        model_pattern = amx.get("model_pattern", "*")
        if not isinstance(make, str) or not make:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.amx_ddp.make is required"
            )
        hint.amx_ddp = {"make": make, "model_pattern": str(model_pattern)}

    # Tier 2 probe opt-ins.
    if discovery.get("pjlink_class2"):
        hint.broadcast_probes.append("pjlink_class2")
    if discovery.get("crestron_cip"):
        hint.broadcast_probes.append("crestron_cip")
    if "onvif" in discovery:
        onvif_block = discovery["onvif"]
        if onvif_block is True:
            hint.broadcast_probes.append("onvif")
        elif isinstance(onvif_block, dict):
            hint.broadcast_probes.append("onvif")
            mfg = onvif_block.get("manufacturer")
            if mfg:
                hint.onvif_manufacturer = str(mfg)
        elif onvif_block is not False and onvif_block is not None:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.onvif must be a bool or {{manufacturer: ...}} mapping"
            )
    if discovery.get("hiqnet"):
        hint.broadcast_probes.append("hiqnet")
    if discovery.get("symetrix"):
        hint.broadcast_probes.append("symetrix")
    for probe_id in hint.broadcast_probes:
        if probe_id not in ALLOWED_BROADCAST_PROBES:
            raise DiscoveryHintError(
                f"{driver_id}: unknown Tier 2 broadcast probe {probe_id!r}; "
                f"allowed: {sorted(ALLOWED_BROADCAST_PROBES)}"
            )

    raw_probes = discovery.get("active_probes") or []
    if not isinstance(raw_probes, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.active_probes must be a list"
        )
    for entry in raw_probes:
        if isinstance(entry, str):
            probe_id = entry
        elif isinstance(entry, dict):
            probe_id = entry.get("probe")
            if not isinstance(probe_id, str) or not probe_id:
                raise DiscoveryHintError(
                    f"{driver_id}: active_probes entry missing 'probe' string"
                )
        else:
            raise DiscoveryHintError(
                f"{driver_id}: active_probes entries must be strings or "
                f"{{probe, port}} mappings"
            )
        if probe_id not in ALLOWED_ACTIVE_PROBES:
            raise DiscoveryHintError(
                f"{driver_id}: unknown Tier 3 active probe {probe_id!r}; "
                f"allowed: {sorted(ALLOWED_ACTIVE_PROBES)}"
            )
        hint.active_probes.append(probe_id)

    if "snmp_pen" in discovery:
        pen = discovery["snmp_pen"]
        if not isinstance(pen, int) or isinstance(pen, bool) or pen < 1:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.snmp_pen must be a positive integer"
            )
        hint.snmp_pen = pen

    raw_oui = discovery.get("oui_prefixes") or []
    if not isinstance(raw_oui, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.oui_prefixes must be a list"
        )
    for prefix in raw_oui:
        if not isinstance(prefix, str) or not prefix:
            raise DiscoveryHintError(
                f"{driver_id}: oui_prefixes entries must be non-empty strings"
            )
        hint.oui_prefixes.append(prefix)

    raw_host = discovery.get("hostname_patterns") or []
    if not isinstance(raw_host, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.hostname_patterns must be a list"
        )
    for pat in raw_host:
        if not isinstance(pat, str) or not pat:
            raise DiscoveryHintError(
                f"{driver_id}: hostname_patterns entries must be non-empty strings"
            )
        hint.hostname_patterns.append(pat)

    raw_aliases = discovery.get("vendor_aliases") or []
    if not isinstance(raw_aliases, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.vendor_aliases must be a list"
        )
    seen_aliases: set[str] = set()
    for alias in raw_aliases:
        if not isinstance(alias, str):
            raise DiscoveryHintError(
                f"{driver_id}: vendor_aliases entries must be strings, got {alias!r}"
            )
        normalized = alias.strip().lower()
        if not normalized:
            raise DiscoveryHintError(
                f"{driver_id}: vendor_aliases entries must be non-empty strings"
            )
        if normalized in seen_aliases:
            continue
        seen_aliases.add(normalized)
        hint.vendor_aliases.append(normalized)

    raw_ports = discovery.get("open_ports") or []
    if not isinstance(raw_ports, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.open_ports must be a list"
        )
    for port in raw_ports:
        # Reject bools (which are int subclasses in Python), strings, and
        # anything else non-integer.
        if not isinstance(port, int) or isinstance(port, bool):
            raise DiscoveryHintError(
                f"{driver_id}: open_ports entries must be integers, got {port!r}"
            )
        if port < 1 or port > 65535:
            raise DiscoveryHintError(
                f"{driver_id}: open_ports entry {port} out of range [1, 65535]"
            )
        if port in DISALLOWED_OPEN_PORTS:
            raise DiscoveryHintError(
                f"{driver_id}: open_ports entry {port} is disallowed "
                f"(too generic — would match every web/SSH device). "
                f"Disallowed: {sorted(DISALLOWED_OPEN_PORTS)}"
            )
        hint.open_ports.append(port)

    has_any_signal = (
        bool(hint.mdns_services)
        or bool(hint.ssdp_device_types)
        or hint.amx_ddp is not None
        or bool(hint.broadcast_probes)
        or bool(hint.active_probes)
        or hint.snmp_pen is not None
        or bool(hint.oui_prefixes)
        or bool(hint.hostname_patterns)
        or bool(hint.open_ports)
        or bool(hint.vendor_aliases)
    )
    if not has_any_signal and not hint.manual_only:
        # A driver with no signals at all and no manual_only flag can never
        # match anything — almost certainly a mistake. Warn so the author
        # notices, but don't reject: the matcher silently ignores it, and
        # `manual_only: true` is no longer required for this case.
        log.warning(
            "%s: discovery block declares no signals (strong or soft); "
            "this driver will never participate in matching. Add "
            "oui_prefixes, hostname_patterns, open_ports, vendor_aliases, "
            "or a Tier 1/2/3 signal — or set manual_only: true to silence "
            "this warning.",
            driver_id,
        )

    return hint


def load_discovery_hints(registry: list[dict[str, Any]]) -> list[DiscoveryHint]:
    """Parse the new-schema ``discovery:`` block from every registered driver.

    Drivers with malformed blocks are logged and skipped — the loader
    does not raise. Strict validation happens earlier in
    ``driver_loader.validate_driver_definition``.
    """
    hints: list[DiscoveryHint] = []
    for driver_info in registry:
        try:
            hint = parse_driver_discovery(driver_info)
        except DiscoveryHintError as exc:
            log.warning("Skipping driver discovery hints: %s", exc)
            continue
        if hint is None:
            continue
        hints.append(hint)

    log.info("Loaded discovery hints for %d drivers (Phase 6 schema)", len(hints))
    return hints


def build_signal_index(hints: list[DiscoveryHint]) -> SignalIndex:
    """Register every strong + soft signal into a ``SignalIndex``.

    Raises ``ValueError`` (from ``SignalIndex.add_rule``) if two drivers
    declare a colliding strong signal.
    """
    index = SignalIndex()
    for hint in hints:
        # Manual-only no longer means "invisible to matcher" — it's a
        # documentation hint that the driver expects manual IP entry. Soft
        # signals (OUI / SNMP PEN / hostname) on a manual_only driver still
        # register so the device surfaces as `possible` with a candidate
        # list. A driver author who really wants the device invisible to
        # discovery declares no signals at all.
        for entry in hint.mdns_services:
            index.add_rule(SignalRule.for_mdns(
                hint.driver_id,
                entry["service"],
                txt_match=entry["txt_match"] or None,
            ))
        for st in hint.ssdp_device_types:
            index.add_rule(SignalRule.for_ssdp(hint.driver_id, st))
        if hint.amx_ddp:
            index.add_rule(SignalRule.for_amx_ddp(
                hint.driver_id,
                hint.amx_ddp["make"],
                hint.amx_ddp["model_pattern"],
            ))
        for probe_id in hint.broadcast_probes:
            txt_filter: dict[str, str] | None = None
            if probe_id == "onvif" and hint.onvif_manufacturer:
                txt_filter = {"manufacturer": hint.onvif_manufacturer}
            index.add_rule(SignalRule.for_broadcast(
                hint.driver_id, probe_id, txt_match=txt_filter,
            ))
        for probe_id in hint.active_probes:
            index.add_rule(SignalRule.for_active_probe(hint.driver_id, probe_id))

        if hint.snmp_pen is not None:
            index.add_rule(SignalRule.for_snmp_pen(hint.driver_id, hint.snmp_pen))
        for prefix in hint.oui_prefixes:
            index.add_rule(SignalRule.for_oui(hint.driver_id, prefix))
        for pattern in hint.hostname_patterns:
            index.add_rule(SignalRule.for_hostname(hint.driver_id, pattern))
        for port in hint.open_ports:
            index.add_rule(SignalRule.for_open_port(hint.driver_id, port))

    log.info(
        "Built signal index covering %d driver(s)",
        index.driver_count(),
    )
    return index
