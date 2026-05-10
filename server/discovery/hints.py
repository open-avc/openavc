"""Driver discovery declaration parser.

Reads each driver's ``discovery:`` block (YAML drivers) or
``DRIVER_INFO['discovery']`` (Python drivers) into a deterministic
``DiscoveryHint`` that the engine and matcher consume.

Two kinds of declarations:

- **Fingerprints** identify the driver alone. mDNS service, SSDP
  device type, AMX DDP beacon, TCP/UDP probe response, or a Python
  escape-hatch (``python: ./foo_discovery.py``). One fingerprint match
  is enough to identify the device.
- **Hints** narrow candidates. OUI, hostname pattern, observed open
  port, manufacturer alias, SNMP enterprise number. Multiple hints
  combined produce a ``possible`` candidate list.

Each fingerprint may carry a ``cross_vendor: true`` flag. When such a
fingerprint matches, the matcher consults peer drivers' hints; if a
vendor-specific peer matches, it wins and the cross-vendor driver
demotes to alternative.

Schema reference: ``discovery-rewrite-plan.md`` (workspace root).
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


# Drivers whose IDs start with these prefixes are templates, not real
# devices. They opt out of the discovery match entirely.
_TEMPLATE_PREFIXES: tuple[str, ...] = ("generic_",)

# Ports too generic to use as a hint — every web / admin / SSH device
# on the network would match. AV-specific ports (1710, 4352, etc.) are
# fine. Generic safety rule, not vendor-specific.
#
# 8000 / 8080 / 8443 / 8888 are admin-UI alternates that show up on
# routers, IoT, dev servers, NAS, and most web-management consoles —
# the same false-positive class as 80/443. A driver that only matches
# on these is matching every web admin UI on the LAN.
DISALLOWED_OPEN_PORTS: frozenset[int] = frozenset({22, 80, 443, 8000, 8080, 8443, 8888})

# Cap on how long a probe is allowed to wait for a reply. Anything
# longer would stretch the scan budget unreasonably.
MAX_PROBE_TIMEOUT_MS: int = 10000

# Reserved ``extract:`` keys whose values feed the manufacturer-alias
# hint path. The probe runner lifts these into the top-level evidence
# response/txt dict so ``extract_vendor_strings`` finds them.
RESERVED_EXTRACT_KEYS: frozenset[str] = frozenset({"manufacturer", "make"})


# ---------------------------------------------------------------------------
# Compiled fingerprint and hint shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MdnsFingerprint:
    """One mDNS service-type declaration."""

    service: str  # normalized lowercase, trailing dot
    txt: tuple[tuple[str, str], ...] = ()  # required TXT k/v pairs (lowercased keys)
    cross_vendor: bool = False


@dataclass(frozen=True)
class SsdpFingerprint:
    """One SSDP/UPnP device-type declaration."""

    device_type: str
    cross_vendor: bool = False


@dataclass(frozen=True)
class AmxDdpFingerprint:
    """One AMX-DDP beacon declaration."""

    make: str
    model_pattern: str = "*"
    cross_vendor: bool = False


@dataclass(frozen=True)
class ResponseMatch:
    """Compiled matchers for a probe response.

    All declared matchers AND together; at least one must be present
    at parse time.
    """

    starts_with: bytes | None = None
    contains: str | None = None
    regex: re.Pattern | None = None
    regex_source: str = ""


def describe_response_match(match: ResponseMatch) -> str:
    """Return a short ``kind:value`` description of the matcher.

    Used by the probe runner to bake the matched pattern into evidence
    records so the scan-results "Why?" reveal can render lines like
    "UDP probe on port 6454 matched regex:NovaStar". Format is stable
    enough for the React UI to split on the first colon and render
    "kind = value" with appropriate styling.

    Empty string when the matcher has no declared sub-matcher (a
    connect-only TCP probe — banner-grab style).
    """
    parts: list[str] = []
    if match.starts_with is not None:
        parts.append(f"hex:{match.starts_with.hex()}")
    if match.regex is not None and match.regex_source:
        parts.append(f"regex:{match.regex_source}")
    if match.contains is not None:
        parts.append(f"contains:{match.contains}")
    return ", ".join(parts)


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
    """A driver-declared TCP or UDP probe.

    ``kind`` is ``"udp"`` or ``"tcp"``. The synthetic probe ID at
    runtime is ``custom_<driver_id>_<kind>``; the matcher's
    ``KIND_BROADCAST`` / ``KIND_ACTIVE_PROBE`` lookups accept it
    as-is — no allow-list needed.

    ``cross_vendor`` mirrors the YAML schema field. It flows to
    ``SignalRule.generic`` at index-build time so the matcher's
    cross-vendor demotion logic activates when this probe wins a
    match but a vendor-specific peer driver matches via hints.
    """

    driver_id: str
    kind: str           # "udp" | "tcp"
    port: int
    send: bytes         # may be empty for connect-only TCP probes
    response_match: ResponseMatch
    timeout_ms: int
    cross_vendor: bool
    extract: tuple[ExtractRule, ...]

    @property
    def probe_id(self) -> str:
        return f"custom_{self.driver_id}_{self.kind}"


@dataclass(frozen=True)
class PythonProbe:
    """A driver-declared Python escape-hatch.

    The companion file does the actual probing — multi-step
    handshakes, encrypted payloads, big-endian bitfield framing, or
    any wire format too dynamic for the declarative ``tcp_probe:`` /
    ``udp_probe:`` blocks. The companion's path is relative to the
    driver file. The runtime loads the file and invokes its
    ``async def probe(ctx) -> None`` function.

    The parser auto-registers two ``SignalRule`` records — one
    broadcast, one active — under canonical synthetic IDs:

      ``custom_<driver_id>_companion_udp``  (broadcast)
      ``custom_<driver_id>_companion_tcp``  (active)

    The companion emits evidence under those IDs by default. The
    ``_companion_*`` suffix is preserved as the wire-level synthetic
    ID so existing ``ProbeContext.companion_*_probe_id`` defaults in
    ``_discovery.py`` files keep working unchanged.
    """

    driver_id: str
    file_path: str
    cross_vendor: bool

    @property
    def broadcast_probe_id(self) -> str:
        return f"custom_{self.driver_id}_companion_udp"

    @property
    def active_probe_id(self) -> str:
        return f"custom_{self.driver_id}_companion_tcp"


@dataclass
class DiscoveryHint:
    """Parsed discovery declaration for one driver."""

    driver_id: str
    driver_name: str = ""
    manufacturer: str = ""
    category: str = ""
    transport: str = "tcp"

    # Fingerprints — one alone identifies this driver.
    mdns: list[MdnsFingerprint] = field(default_factory=list)
    ssdp: list[SsdpFingerprint] = field(default_factory=list)
    amx_ddp: list[AmxDdpFingerprint] = field(default_factory=list)
    tcp_probe: CustomProbeSpec | None = None
    udp_probe: CustomProbeSpec | None = None
    python_probe: PythonProbe | None = None

    # Hints — combine to narrow candidates.
    oui: list[str] = field(default_factory=list)
    hostname: list[str] = field(default_factory=list)
    port_open: list[int] = field(default_factory=list)
    manufacturer_alias: list[str] = field(default_factory=list)
    snmp_pen: int | None = None


class DiscoveryHintError(ValueError):
    """Raised when a driver's ``discovery:`` block is structurally invalid."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    """Normalize a single-or-list input to a list. ``None`` → ``[]``."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _ensure_dict(driver_id: str, where: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} must be a mapping, got {type(raw).__name__}"
        )
    return raw


def _ensure_str(driver_id: str, where: str, raw: Any) -> str:
    if not isinstance(raw, str) or not raw:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} must be a non-empty string"
        )
    return raw


def _ensure_bool(driver_id: str, where: str, raw: Any, *, default: bool = False) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} must be a bool"
        )
    return raw


def _ensure_int(
    driver_id: str,
    where: str,
    raw: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} must be an integer"
        )
    if minimum is not None and raw < minimum:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} must be >= {minimum} (got {raw})"
        )
    if maximum is not None and raw > maximum:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} must be <= {maximum} (got {raw})"
        )
    return raw


def _normalize_service_type(service: str) -> str:
    s = service.strip()
    if not s.endswith("."):
        s = s + "."
    return s.lower()


def _hex_to_bytes(driver_id: str, where: str, raw: str) -> bytes:
    cleaned = raw.replace(" ", "").replace(":", "")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} is not valid hex: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Fingerprint parsers
# ---------------------------------------------------------------------------


def _parse_mdns_entry(driver_id: str, raw: Any) -> MdnsFingerprint:
    """Accept a bare service-type string or a ``{service, txt, cross_vendor}`` mapping."""
    if isinstance(raw, str):
        return MdnsFingerprint(
            service=_normalize_service_type(raw), txt=(), cross_vendor=False,
        )
    mapping = _ensure_dict(driver_id, "mdns entry", raw)
    service = _ensure_str(driver_id, "mdns.service", mapping.get("service"))
    txt_raw = mapping.get("txt") or {}
    if not isinstance(txt_raw, dict):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.mdns.txt must be a mapping"
        )
    txt = tuple(sorted(
        (str(k).lower(), str(v)) for k, v in txt_raw.items()
    ))
    cross_vendor = _ensure_bool(driver_id, "mdns.cross_vendor", mapping.get("cross_vendor"))
    return MdnsFingerprint(
        service=_normalize_service_type(service),
        txt=txt,
        cross_vendor=cross_vendor,
    )


def _parse_ssdp_entry(driver_id: str, raw: Any) -> SsdpFingerprint:
    """Accept a bare device-type string or a ``{device_type, cross_vendor}`` mapping."""
    if isinstance(raw, str):
        return SsdpFingerprint(device_type=raw, cross_vendor=False)
    mapping = _ensure_dict(driver_id, "ssdp entry", raw)
    device_type = _ensure_str(driver_id, "ssdp.device_type", mapping.get("device_type"))
    cross_vendor = _ensure_bool(driver_id, "ssdp.cross_vendor", mapping.get("cross_vendor"))
    return SsdpFingerprint(device_type=device_type, cross_vendor=cross_vendor)


def _parse_amx_ddp_entry(driver_id: str, raw: Any) -> AmxDdpFingerprint:
    """Accept a ``{make, model_pattern, cross_vendor}`` mapping."""
    mapping = _ensure_dict(driver_id, "amx_ddp entry", raw)
    make = _ensure_str(driver_id, "amx_ddp.make", mapping.get("make"))
    model_pattern = mapping.get("model_pattern", "*")
    if not isinstance(model_pattern, str):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.amx_ddp.model_pattern must be a string"
        )
    cross_vendor = _ensure_bool(driver_id, "amx_ddp.cross_vendor", mapping.get("cross_vendor"))
    return AmxDdpFingerprint(
        make=make, model_pattern=str(model_pattern), cross_vendor=cross_vendor,
    )


def _parse_send(driver_id: str, where: str, raw: dict[str, Any]) -> bytes:
    """Pull ``send_hex:`` or ``send_ascii:`` out of a probe block.

    Returns empty bytes if neither is present (connect-only TCP probe).
    Raises if both are present.
    """
    has_hex = "send_hex" in raw and raw["send_hex"] is not None
    has_ascii = "send_ascii" in raw and raw["send_ascii"] is not None
    if has_hex and has_ascii:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} declares both send_hex and "
            "send_ascii — pick one"
        )
    if has_hex:
        if not isinstance(raw["send_hex"], str):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.send_hex must be a string"
            )
        return _hex_to_bytes(driver_id, f"{where}.send_hex", raw["send_hex"])
    if has_ascii:
        if not isinstance(raw["send_ascii"], str):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.send_ascii must be a string"
            )
        return raw["send_ascii"].encode("utf-8")
    return b""


def _parse_response_match(
    driver_id: str,
    where: str,
    raw: dict[str, Any],
    *,
    require_match: bool,
) -> ResponseMatch:
    """Pull ``expect:``, ``expect_regex:``, and ``expect_hex:`` out of a probe block.

    All declared matchers AND together. If ``require_match`` is True, at
    least one must be present — UDP probes need a matcher to filter
    junk, but a connect-only TCP probe can succeed on connect alone.
    """
    starts_with: bytes | None = None
    contains: str | None = None
    regex: re.Pattern | None = None
    regex_source = ""

    if "expect_hex" in raw and raw["expect_hex"] is not None:
        s = raw["expect_hex"]
        if not isinstance(s, str):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.expect_hex must be a string"
            )
        starts_with = _hex_to_bytes(driver_id, f"{where}.expect_hex", s)

    if "expect" in raw and raw["expect"] is not None:
        c = raw["expect"]
        if not isinstance(c, str) or not c:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.expect must be a non-empty string"
            )
        contains = c

    if "expect_regex" in raw and raw["expect_regex"] is not None:
        r = raw["expect_regex"]
        if not isinstance(r, str) or not r:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.expect_regex must be a non-empty string"
            )
        try:
            regex = re.compile(r)
        except re.error as exc:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.expect_regex failed to compile: {exc}"
            ) from exc
        regex_source = r

    if require_match and starts_with is None and contains is None and regex is None:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} needs at least one of "
            "expect, expect_regex, expect_hex"
        )

    return ResponseMatch(
        starts_with=starts_with,
        contains=contains,
        regex=regex,
        regex_source=regex_source,
    )


def _parse_extract(
    driver_id: str,
    where: str,
    raw: Any,
) -> list[ExtractRule]:
    """Parse an ``extract:`` block into ExtractRule entries.

    Each entry is either a literal string (static value) or a mapping
    ``{regex, group}`` (dynamic capture).
    """
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where}.extract must be a mapping"
        )
    rules: list[ExtractRule] = []
    for field_name, spec in raw.items():
        if not isinstance(field_name, str) or not field_name:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.extract field names must be "
                "non-empty strings"
            )
        if isinstance(spec, str):
            rules.append(ExtractRule(field_name=field_name, value=spec))
            continue
        if isinstance(spec, dict):
            pattern_str = spec.get("regex")
            if not isinstance(pattern_str, str) or not pattern_str:
                raise DiscoveryHintError(
                    f"{driver_id}: discovery.{where}.extract.{field_name} "
                    "mapping requires a non-empty 'regex' string"
                )
            try:
                pattern = re.compile(pattern_str)
            except re.error as exc:
                raise DiscoveryHintError(
                    f"{driver_id}: discovery.{where}.extract.{field_name}.regex "
                    f"failed to compile: {exc}"
                ) from exc
            group = spec.get("group", 1)
            if not isinstance(group, int) or isinstance(group, bool) or group < 0:
                raise DiscoveryHintError(
                    f"{driver_id}: discovery.{where}.extract.{field_name}.group "
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
            f"{driver_id}: discovery.{where}.extract.{field_name} must be a "
            "literal string or a {regex, group} mapping"
        )
    return rules


def _parse_probe(
    driver_id: str,
    kind: str,                       # "udp" | "tcp"
    raw: Any,
) -> CustomProbeSpec:
    """Parse one ``udp_probe:`` / ``tcp_probe:`` block.

    Schema:

        tcp_probe:
          port: 12345
          send_ascii: "QUERY\\r"            # exactly one of send_ascii, send_hex
          expect: "RESPONSE_PREFIX"         # one of expect, expect_regex, expect_hex
          cross_vendor: false               # optional, default false
          timeout_ms: 3000                  # optional
          extract_manufacturer: "AcmeCorp"  # optional sugar for extract.manufacturer
          extract:                          # optional metadata extraction
            model: { regex: "model=(.+)", group: 1 }

    Connect-only TCP probes may omit ``send_*`` and ``expect*`` entirely;
    UDP probes must declare both (no responder filter would match every
    UDP-noisy host).
    """
    where = f"{kind}_probe"
    block = _ensure_dict(driver_id, where, raw)

    port = _ensure_int(driver_id, f"{where}.port", block.get("port"), minimum=1, maximum=65535)
    send = _parse_send(driver_id, where, block)

    has_match = any(
        block.get(k) is not None
        for k in ("expect", "expect_regex", "expect_hex")
    )

    require_match = kind == "udp"
    if kind == "tcp" and send and not has_match:
        # A TCP probe that sends bytes but doesn't declare a matcher
        # would emit evidence for any responding host, which is too
        # noisy to be useful.
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} sends bytes but declares no "
            "matcher — add expect, expect_regex, or expect_hex"
        )
    if kind == "udp" and not send:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} must declare send_ascii or send_hex "
            "(UDP probes need a query payload)"
        )

    response_match = _parse_response_match(
        driver_id, where, block, require_match=require_match,
    )

    timeout_default = 3000 if kind == "tcp" else 2000
    timeout_ms = block.get("timeout_ms", timeout_default)
    timeout_ms = _ensure_int(
        driver_id, f"{where}.timeout_ms", timeout_ms,
        minimum=1, maximum=MAX_PROBE_TIMEOUT_MS,
    )

    cross_vendor = _ensure_bool(driver_id, f"{where}.cross_vendor", block.get("cross_vendor"))

    extract = _parse_extract(driver_id, where, block.get("extract"))

    # Sugar: extract_manufacturer: "AcmeCorp" produces an ExtractRule
    # for the reserved ``manufacturer`` key.
    if "extract_manufacturer" in block and block["extract_manufacturer"] is not None:
        mfg = block["extract_manufacturer"]
        if not isinstance(mfg, str) or not mfg:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.extract_manufacturer must be "
                "a non-empty string"
            )
        if any(r.field_name == "manufacturer" for r in extract):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.{where}.extract_manufacturer collides "
                "with extract.manufacturer — pick one"
            )
        extract.append(ExtractRule(field_name="manufacturer", value=mfg))

    # Reject keys the parser doesn't understand, so typos surface.
    known = {
        "port", "send_hex", "send_ascii",
        "expect", "expect_regex", "expect_hex",
        "cross_vendor", "timeout_ms",
        "extract", "extract_manufacturer",
    }
    unknown = set(block.keys()) - known
    if unknown:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.{where} has unknown keys: {sorted(unknown)}"
        )

    return CustomProbeSpec(
        driver_id=driver_id,
        kind=kind,
        port=port,
        send=send,
        response_match=response_match,
        timeout_ms=timeout_ms,
        cross_vendor=cross_vendor,
        extract=tuple(extract),
    )


def _parse_python_probe(driver_id: str, raw: Any) -> PythonProbe:
    """Parse the ``python:`` field.

    Accepts either a bare path string or a mapping
    ``{file: ..., cross_vendor: bool}``. The path is preserved verbatim;
    the runtime resolves it relative to the driver file at load time.
    """
    if isinstance(raw, str):
        if not raw:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.python path must be a non-empty string"
            )
        return PythonProbe(driver_id=driver_id, file_path=raw, cross_vendor=False)

    block = _ensure_dict(driver_id, "python", raw)
    file_path = _ensure_str(driver_id, "python.file", block.get("file"))
    cross_vendor = _ensure_bool(driver_id, "python.cross_vendor", block.get("cross_vendor"))

    unknown = set(block.keys()) - {"file", "cross_vendor"}
    if unknown:
        raise DiscoveryHintError(
            f"{driver_id}: discovery.python has unknown keys: {sorted(unknown)}"
        )

    return PythonProbe(
        driver_id=driver_id, file_path=file_path, cross_vendor=cross_vendor,
    )


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


# Fields the parser recognizes at the top of the ``discovery:`` block.
_KNOWN_DISCOVERY_KEYS: frozenset[str] = frozenset({
    "mdns", "ssdp", "amx_ddp",
    "tcp_probe", "udp_probe", "python",
    "oui", "hostname", "port_open", "manufacturer_alias", "snmp_pen",
})


def parse_driver_discovery(driver_info: dict[str, Any]) -> DiscoveryHint | None:
    """Parse one driver-registry entry into a ``DiscoveryHint``.

    Returns ``None`` for template drivers (``generic_*``); otherwise a
    populated ``DiscoveryHint``. Raises ``DiscoveryHintError`` on
    structural problems.

    A driver with no fingerprints and no hints can still parse — it
    simply never participates in matching, which is logged as a
    warning.
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

    unknown = set(discovery.keys()) - _KNOWN_DISCOVERY_KEYS
    if unknown:
        raise DiscoveryHintError(
            f"{driver_id}: discovery has unknown keys: {sorted(unknown)}. "
            f"Known keys: {sorted(_KNOWN_DISCOVERY_KEYS)}"
        )

    hint = DiscoveryHint(
        driver_id=driver_id,
        driver_name=str(driver_info.get("name") or driver_id),
        manufacturer=str(driver_info.get("manufacturer") or ""),
        category=str(driver_info.get("category") or ""),
        transport=str(driver_info.get("transport") or "tcp"),
    )

    # --- Fingerprints -----------------------------------------------------

    if "mdns" in discovery:
        for entry in _as_list(discovery["mdns"]):
            hint.mdns.append(_parse_mdns_entry(driver_id, entry))

    if "ssdp" in discovery:
        for entry in _as_list(discovery["ssdp"]):
            hint.ssdp.append(_parse_ssdp_entry(driver_id, entry))

    if "amx_ddp" in discovery:
        for entry in _as_list(discovery["amx_ddp"]):
            hint.amx_ddp.append(_parse_amx_ddp_entry(driver_id, entry))

    if "tcp_probe" in discovery:
        hint.tcp_probe = _parse_probe(driver_id, "tcp", discovery["tcp_probe"])

    if "udp_probe" in discovery:
        hint.udp_probe = _parse_probe(driver_id, "udp", discovery["udp_probe"])

    if "python" in discovery:
        hint.python_probe = _parse_python_probe(driver_id, discovery["python"])

    # --- Hints ------------------------------------------------------------

    if "snmp_pen" in discovery:
        hint.snmp_pen = _ensure_int(
            driver_id, "snmp_pen", discovery["snmp_pen"], minimum=1,
        )

    raw_oui = discovery.get("oui") or []
    if not isinstance(raw_oui, list):
        raise DiscoveryHintError(f"{driver_id}: discovery.oui must be a list")
    for prefix in raw_oui:
        if not isinstance(prefix, str) or not prefix:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.oui entries must be non-empty strings"
            )
        hint.oui.append(prefix)

    raw_host = discovery.get("hostname") or []
    if not isinstance(raw_host, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.hostname must be a list"
        )
    for pat in raw_host:
        if not isinstance(pat, str) or not pat:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.hostname entries must be non-empty strings"
            )
        hint.hostname.append(pat)

    raw_ports = discovery.get("port_open") or []
    if not isinstance(raw_ports, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.port_open must be a list"
        )
    for port in raw_ports:
        if not isinstance(port, int) or isinstance(port, bool):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.port_open entries must be integers, got {port!r}"
            )
        if port < 1 or port > 65535:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.port_open entry {port} out of range [1, 65535]"
            )
        if port in DISALLOWED_OPEN_PORTS:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.port_open entry {port} is too generic — "
                f"would match every web/SSH device. Disallowed: {sorted(DISALLOWED_OPEN_PORTS)}"
            )
        hint.port_open.append(port)

    raw_aliases = discovery.get("manufacturer_alias") or []
    if not isinstance(raw_aliases, list):
        raise DiscoveryHintError(
            f"{driver_id}: discovery.manufacturer_alias must be a list"
        )
    seen_aliases: set[str] = set()
    for alias in raw_aliases:
        if not isinstance(alias, str):
            raise DiscoveryHintError(
                f"{driver_id}: discovery.manufacturer_alias entries must be strings, got {alias!r}"
            )
        normalized = alias.strip().lower()
        if not normalized:
            raise DiscoveryHintError(
                f"{driver_id}: discovery.manufacturer_alias entries must be non-empty"
            )
        if normalized in seen_aliases:
            continue
        seen_aliases.add(normalized)
        hint.manufacturer_alias.append(normalized)

    # Warn — don't reject — when a driver declares nothing. It is
    # almost certainly a mistake (the driver will never match), but
    # not structurally invalid.
    has_any_signal = (
        bool(hint.mdns)
        or bool(hint.ssdp)
        or bool(hint.amx_ddp)
        or hint.tcp_probe is not None
        or hint.udp_probe is not None
        or hint.python_probe is not None
        or hint.snmp_pen is not None
        or bool(hint.oui)
        or bool(hint.hostname)
        or bool(hint.port_open)
        or bool(hint.manufacturer_alias)
    )
    if not has_any_signal:
        log.warning(
            "%s: discovery block declares no fingerprints or hints; "
            "this driver will never participate in matching.",
            driver_id,
        )

    return hint


def load_discovery_hints(registry: list[dict[str, Any]]) -> list[DiscoveryHint]:
    """Parse the ``discovery:`` block from every registered driver.

    Drivers with malformed blocks are logged and skipped — the loader
    does not raise. Strict validation happens earlier in
    ``driver_loader.validate_driver_definition`` (and in CI via
    ``build_index.py``).
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

    log.info("Loaded discovery hints for %d drivers", len(hints))
    return hints


# ---------------------------------------------------------------------------
# Signal-index builder
# ---------------------------------------------------------------------------


def build_signal_index(hints: list[DiscoveryHint]) -> SignalIndex:
    """Register every fingerprint and hint into a ``SignalIndex``.

    Raises ``ValueError`` (from ``SignalIndex.add_rule``) if two
    drivers declare a colliding fingerprint without distinguishing
    filters.
    """
    index = SignalIndex()
    for hint in hints:
        for fp in hint.mdns:
            index.add_rule(SignalRule.for_mdns(
                hint.driver_id,
                fp.service,
                txt_match={k: v for k, v in fp.txt} or None,
                generic=fp.cross_vendor,
            ))
        for fp in hint.ssdp:
            index.add_rule(SignalRule.for_ssdp(
                hint.driver_id, fp.device_type, generic=fp.cross_vendor,
            ))
        for fp in hint.amx_ddp:
            index.add_rule(SignalRule.for_amx_ddp(
                hint.driver_id, fp.make, fp.model_pattern,
                generic=fp.cross_vendor,
            ))

        if hint.tcp_probe is not None:
            spec = hint.tcp_probe
            index.add_rule(SignalRule.for_active_probe(
                hint.driver_id, spec.probe_id, generic=spec.cross_vendor,
            ))
        if hint.udp_probe is not None:
            spec = hint.udp_probe
            index.add_rule(SignalRule.for_broadcast(
                hint.driver_id, spec.probe_id, generic=spec.cross_vendor,
            ))
        if hint.python_probe is not None:
            py = hint.python_probe
            index.add_rule(SignalRule.for_broadcast(
                hint.driver_id, py.broadcast_probe_id, generic=py.cross_vendor,
            ))
            index.add_rule(SignalRule.for_active_probe(
                hint.driver_id, py.active_probe_id, generic=py.cross_vendor,
            ))

        if hint.snmp_pen is not None:
            index.add_rule(SignalRule.for_snmp_pen(hint.driver_id, hint.snmp_pen))
        for prefix in hint.oui:
            index.add_rule(SignalRule.for_oui(hint.driver_id, prefix))
        for pattern in hint.hostname:
            index.add_rule(SignalRule.for_hostname(hint.driver_id, pattern))
        for port in hint.port_open:
            index.add_rule(SignalRule.for_open_port(hint.driver_id, port))
        for alias in hint.manufacturer_alias:
            index.add_rule(SignalRule.for_vendor_string(hint.driver_id, alias))

    log.info(
        "Built signal index covering %d driver(s)",
        index.driver_count(),
    )
    return index
