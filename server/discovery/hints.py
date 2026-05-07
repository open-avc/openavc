"""Phase 6 ``discovery:`` schema parser + ``SignalIndex`` builder.

The new schema is opinionated: every driver declares at least one
strong (Tier 1/2/3) signal or sets ``manual_only: true``. Validation
happens at driver-load time; collisions raise from
``SignalIndex.add_rule``. The matcher is deterministic — there is no
score.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from server.discovery.tier_matcher import (
    SignalIndex,
    SignalRule,
)

log = logging.getLogger("discovery.hints")


# Phase 9: the built-in opt-ins (``pjlink_class2: true`` etc. for
# broadcasts, named entries in ``active_probes:`` for active probes)
# are still gated by explicit core handlers, but unknown probe IDs no
# longer raise. A driver that declares an active_probe ID core doesn't
# implement is a silent no-op rather than a hard error — driver-
# declared probes (``udp_broadcast_probe:`` / ``tcp_active_probe:``)
# carry the wire format directly and don't need a registry.

# Drivers whose IDs start with these prefixes are templates, not real
# devices. They opt out of the discovery match entirely.
_TEMPLATE_PREFIXES: tuple[str, ...] = ("generic_",)

# Ports too generic to use as a soft enrichment signal — every web /
# admin / SSH device on the network would match. AV-specific ports
# (1710, 4352, 23 for telnet-on-AV-gear, etc.) are fine.
DISALLOWED_OPEN_PORTS: frozenset[int] = frozenset({22, 80, 443})

# Phase 9: ports owned by built-in handlers — drivers declaring a
# ``udp_broadcast_probe`` or ``tcp_active_probe`` cannot collide on
# them. Drivers participating in those protocols use the named opt-in
# (``pjlink_class2: true`` etc.) instead of a custom probe.
DISALLOWED_UDP_BROADCAST_PROBE_PORTS: frozenset[int] = frozenset({
    1900,   # SSDP
    3702,   # ONVIF WS-Discovery
    4352,   # PJLink Class 2 broadcast
    5353,   # mDNS
    9131,   # AMX DDP
    41794,  # Crestron CIP broadcast
})
DISALLOWED_TCP_ACTIVE_PROBE_PORTS: frozenset[int] = frozenset({
    23,     # Telnet (Extron SIS, Tesira TTP, Shure DCS)
    1515,   # Samsung MDC
    1688,   # Crestron CIP TCP
    1710,   # Q-SYS QRC
    4352,   # PJLink Class 1
    10500,  # VISCA-IP
    49280,  # Yamaha RCP
})

# Phase 9: cap on how long a driver-declared probe is allowed to wait
# for a reply. Anything longer than this would stretch the scan budget
# unreasonably; community drivers don't get to opt out.
MAX_PROBE_TIMEOUT_MS: int = 10000

# Reserved ``extract:`` keys whose values feed the Phase 8.6 vendor_string
# tier 4 path. The probe runner lifts these into the top-level evidence
# response/txt dict so ``extract_vendor_strings`` finds them.
RESERVED_EXTRACT_KEYS: frozenset[str] = frozenset({"manufacturer", "make"})


@dataclass(frozen=True)
class ResponseMatch:
    """Compiled matchers for one response-match block.

    All matchers AND together; at least one must be present at parse
    time.
    """

    starts_with: bytes | None = None
    contains: str | None = None
    regex: re.Pattern | None = None
    regex_source: str = ""


@dataclass(frozen=True)
class ExtractRule:
    """One ``extract:`` field. Either a static value or a regex+group."""

    field_name: str
    value: str | None = None        # static literal
    regex: re.Pattern | None = None  # dynamic
    regex_source: str = ""
    group: int = 1


@dataclass(frozen=True)
class CustomProbeSpec:
    """Driver-declared UDP broadcast or TCP active probe.

    The probe ID at runtime is ``custom_<driver_id>_<kind>``; the
    matcher's existing ``KIND_BROADCAST`` / ``KIND_ACTIVE_PROBE``
    lookups accept it as-is — no allow-list needed.
    """

    driver_id: str
    kind: str           # "udp" | "tcp"
    port: int
    send: bytes
    response_match: ResponseMatch
    timeout_ms: int
    generic: bool
    extract: tuple[ExtractRule, ...]

    @property
    def probe_id(self) -> str:
        return f"custom_{self.driver_id}_{self.kind}"


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

    # Phase 9: driver-declared UDP broadcast / TCP active probes.
    # These produce ``custom_<driver_id>_udp`` / ``custom_<driver_id>_tcp``
    # signal IDs and replace the need for vendor-specific Python probes
    # in core for simple "send these bytes, look for this in the
    # response" cases.
    udp_broadcast_probe: CustomProbeSpec | None = None
    tcp_active_probe: CustomProbeSpec | None = None

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


def _parse_send(driver_id: str, kind: str, raw: Any) -> bytes:
    """Parse a probe ``send:`` block into raw bytes.

    Exactly one of ``hex`` or ``ascii`` must be present.
    """
    if not isinstance(raw, dict):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.send must be a mapping with "
            "exactly one of 'hex' or 'ascii'"
        )
    has_hex = "hex" in raw and raw["hex"] is not None
    has_ascii = "ascii" in raw and raw["ascii"] is not None
    if has_hex and has_ascii:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.send must declare exactly one "
            "of 'hex' or 'ascii', not both"
        )
    if not has_hex and not has_ascii:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.send must declare one of "
            "'hex' or 'ascii'"
        )
    if has_hex:
        hex_str = raw["hex"]
        if not isinstance(hex_str, str):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.send.hex must be a string"
            )
        cleaned = hex_str.replace(" ", "").replace(":", "")
        try:
            return bytes.fromhex(cleaned)
        except ValueError as exc:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.send.hex is not valid hex: {exc}"
            ) from exc
    ascii_str = raw["ascii"]
    if not isinstance(ascii_str, str):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.send.ascii must be a string"
        )
    return ascii_str.encode("utf-8")


def _parse_response_match(driver_id: str, kind: str, raw: Any) -> ResponseMatch:
    """Parse a ``response_match:`` block into a compiled ResponseMatch.

    At least one of {starts_with_hex, contains, regex} must be present.
    """
    if not isinstance(raw, dict):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.response_match must be a "
            "mapping (at least one of starts_with_hex, contains, regex)"
        )

    starts_with: bytes | None = None
    if "starts_with_hex" in raw and raw["starts_with_hex"] is not None:
        s = raw["starts_with_hex"]
        if not isinstance(s, str):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.response_match.starts_with_hex "
                "must be a string"
            )
        cleaned = s.replace(" ", "").replace(":", "")
        try:
            starts_with = bytes.fromhex(cleaned)
        except ValueError as exc:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.response_match.starts_with_hex "
                f"is not valid hex: {exc}"
            ) from exc

    contains: str | None = None
    if "contains" in raw and raw["contains"] is not None:
        c = raw["contains"]
        if not isinstance(c, str) or not c:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.response_match.contains "
                "must be a non-empty string"
            )
        contains = c

    regex: re.Pattern | None = None
    regex_source = ""
    if "regex" in raw and raw["regex"] is not None:
        r = raw["regex"]
        if not isinstance(r, str) or not r:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.response_match.regex "
                "must be a non-empty string"
            )
        try:
            regex = re.compile(r)
        except re.error as exc:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.response_match.regex "
                f"failed to compile: {exc}"
            ) from exc
        regex_source = r

    if starts_with is None and contains is None and regex is None:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.response_match needs at least "
            "one of starts_with_hex, contains, regex"
        )

    return ResponseMatch(
        starts_with=starts_with,
        contains=contains,
        regex=regex,
        regex_source=regex_source,
    )


def _parse_extract(driver_id: str, kind: str, raw: Any) -> tuple[ExtractRule, ...]:
    """Parse an ``extract:`` block into ExtractRule tuples.

    Each entry is either a literal string (static value) or a mapping
    ``{regex: ..., group: N}`` (dynamic capture). Reserved keys
    (``manufacturer``, ``make``) get lifted by the runner into the
    top-level response/txt dict for vendor_string evidence.
    """
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.extract must be a mapping of "
            "field name to literal string or {regex, group} mapping"
        )
    rules: list[ExtractRule] = []
    for field_name, spec in raw.items():
        if not isinstance(field_name, str) or not field_name:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{kind}.extract field names must "
                "be non-empty strings"
            )
        if isinstance(spec, str):
            rules.append(ExtractRule(field_name=field_name, value=spec))
            continue
        if isinstance(spec, dict):
            pattern_str = spec.get("regex")
            if not isinstance(pattern_str, str) or not pattern_str:
                raise DiscoveryHintError(
                    f"{driver_id}: discovery.{kind}.extract.{field_name} "
                    "mapping requires a non-empty 'regex' string"
                )
            try:
                pattern = re.compile(pattern_str)
            except re.error as exc:
                raise DiscoveryHintError(
                    f"{driver_id}: discovery.{kind}.extract.{field_name}.regex "
                    f"failed to compile: {exc}"
                ) from exc
            group = spec.get("group", 1)
            if not isinstance(group, int) or isinstance(group, bool) or group < 0:
                raise DiscoveryHintError(
                    f"{driver_id}: discovery.{kind}.extract.{field_name}.group "
                    "must be a non-negative integer"
                )
            rules.append(ExtractRule(
                field_name=field_name,
                regex=pattern,
                regex_source=pattern_str,
                group=group,
            ))
            continue
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{kind}.extract.{field_name} must be a "
            "literal string or a {regex, group} mapping"
        )
    return tuple(rules)


def _parse_custom_probe(
    driver_id: str,
    kind: str,                       # "udp" | "tcp"
    raw: Any,
    *,
    default_timeout_ms: int,
    disallowed_ports: frozenset[int],
) -> CustomProbeSpec:
    """Parse one ``udp_broadcast_probe:`` / ``tcp_active_probe:`` block."""
    if not isinstance(raw, dict):
        block_name = "udp_broadcast_probe" if kind == "udp" else "tcp_active_probe"
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{block_name} must be a mapping"
        )

    block_name = "udp_broadcast_probe" if kind == "udp" else "tcp_active_probe"

    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or port < 1 or port > 65535:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{block_name}.port must be an integer "
            "in [1, 65535]"
        )
    if port in disallowed_ports:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{block_name}.port {port} is reserved "
            f"for a built-in handler. Use the named opt-in instead. "
            f"Disallowed: {sorted(disallowed_ports)}"
        )

    send = _parse_send(driver_id, block_name, raw.get("send"))
    response_match = _parse_response_match(
        driver_id, block_name, raw.get("response_match"),
    )

    timeout_ms = raw.get("timeout_ms", default_timeout_ms)
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms < 1:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{block_name}.timeout_ms must be a "
            "positive integer"
        )
    if timeout_ms > MAX_PROBE_TIMEOUT_MS:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{block_name}.timeout_ms exceeds the "
            f"max of {MAX_PROBE_TIMEOUT_MS} ms"
        )

    generic_raw = raw.get("generic", False)
    if not isinstance(generic_raw, bool):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{block_name}.generic must be a bool"
        )

    extract = _parse_extract(driver_id, block_name, raw.get("extract"))

    return CustomProbeSpec(
        driver_id=driver_id,
        kind=kind,
        port=port,
        send=send,
        response_match=response_match,
        timeout_ms=timeout_ms,
        generic=generic_raw,
        extract=extract,
    )


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
        hint.active_probes.append(probe_id)

    # Phase 9: driver-declared probes. Both blocks are optional; either,
    # neither, or both may be present.
    if "udp_broadcast_probe" in discovery:
        hint.udp_broadcast_probe = _parse_custom_probe(
            driver_id,
            "udp",
            discovery["udp_broadcast_probe"],
            default_timeout_ms=2000,
            disallowed_ports=DISALLOWED_UDP_BROADCAST_PROBE_PORTS,
        )
    if "tcp_active_probe" in discovery:
        hint.tcp_active_probe = _parse_custom_probe(
            driver_id,
            "tcp",
            discovery["tcp_active_probe"],
            default_timeout_ms=3000,
            disallowed_ports=DISALLOWED_TCP_ACTIVE_PROBE_PORTS,
        )

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
        or hint.udp_broadcast_probe is not None
        or hint.tcp_active_probe is not None
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

        # Phase 9: register driver-declared probe IDs against the same
        # KIND_BROADCAST / KIND_ACTIVE_PROBE namespaces. The schema's
        # ``generic: bool`` flag flows through directly — these IDs are
        # not in ``_GENERIC_STRONG_PROBE_IDS`` so the factory's default
        # of ``False`` would be wrong for a driver that opts in.
        if hint.udp_broadcast_probe is not None:
            spec = hint.udp_broadcast_probe
            index.add_rule(SignalRule.for_broadcast(
                hint.driver_id,
                spec.probe_id,
                generic=spec.generic,
            ))
        if hint.tcp_active_probe is not None:
            spec = hint.tcp_active_probe
            index.add_rule(SignalRule.for_active_probe(
                hint.driver_id,
                spec.probe_id,
                generic=spec.generic,
            ))

        if hint.snmp_pen is not None:
            index.add_rule(SignalRule.for_snmp_pen(hint.driver_id, hint.snmp_pen))
        for prefix in hint.oui_prefixes:
            index.add_rule(SignalRule.for_oui(hint.driver_id, prefix))
        for pattern in hint.hostname_patterns:
            index.add_rule(SignalRule.for_hostname(hint.driver_id, pattern))
        for port in hint.open_ports:
            index.add_rule(SignalRule.for_open_port(hint.driver_id, port))
        for alias in hint.vendor_aliases:
            index.add_rule(SignalRule.for_vendor_string(hint.driver_id, alias))

    log.info(
        "Built signal index covering %d driver(s)",
        index.driver_count(),
    )
    return index
