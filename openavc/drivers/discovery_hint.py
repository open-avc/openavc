"""What a driver's ``discovery:`` block reads as: the ``DiscoveryHint`` the
discovery engine and matcher consume, and the fingerprints and probes it holds.

``discovery_block.read_discovery_block`` builds one from a block, and every
reader of a hint (the probe runner, the matcher, the explanations, the
catalog's checks across drivers) reads these shapes. So is the canonical form
of an OUI hint (``normalize_oui_prefix``), which the OUI table and the
matcher's lookup share. Standard library only;
vendored into the community catalog with the driver contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field




@dataclass(frozen=True)
class MdnsFingerprint:
    """One mDNS service-type declaration."""

    service: str  # normalized lowercase, trailing dot
    txt: tuple[tuple[str, str], ...] = ()  # required TXT k/v pairs (lowercased keys)
    cross_vendor: bool = False


@dataclass(frozen=True)
class SsdpFingerprint:
    """One SSDP/UPnP device-type declaration.

    ``fields`` optionally filters on the responder's UPnP device
    description (model / manufacturer / friendly_name, matched
    case-insensitively), so several drivers can share one device-type
    URN — vendor UPnP stacks commonly advertise a single family-wide
    URN and only the description XML names the exact model.
    """

    device_type: str
    fields: tuple[tuple[str, str], ...] = ()
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


@dataclass(frozen=True)
class ExtractRule:
    """One ``extract:`` field. Either a static value or a regex+group."""

    field_name: str
    value: str | None = None        # static literal
    regex: re.Pattern | None = None  # dynamic
    regex_source: str = ""
    group: int = 1


@dataclass(frozen=True)
class ProbeFollowUp:
    """A second exchange on the same connection, after the first one matched.

    It exists for identification by ABSENCE. Some product families are nested
    -- the smaller model's parameter set is a strict subset of the larger
    one's -- so no question the small one answers is unique to it, and the only
    thing that separates them is a question the LARGER model answers and the
    smaller does not. A single send/expect pair cannot say that.

    ``expect_silence`` inverts the step: the probe matches only when the device
    does NOT answer. Otherwise the step carries its own matcher and both halves
    must pass.
    """

    send: bytes
    expect_silence: bool
    response_match: ResponseMatch
    timeout_ms: int


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
    tls: bool = False   # tcp only: wrap the connection in TLS before send/read
                        # (no cert verification — devices ship self-signed certs)
    # tcp + tls only: a regex matched against the peer certificate's subject
    # (RFC4514 string, e.g. "CN=DM-NVX-E20-...,O=Crestron Electronics"). Lets a
    # driver identify a device by its self-signed cert's own name before any
    # login — the strongest pre-auth signal for gear that ships an identifying
    # cert but no discovery beacon. `extract` rules also run against this
    # string, so the model can be pulled from the CN.
    cert_subject: re.Pattern | None = None
    cert_subject_source: str = ""
    # A second exchange on the same connection (tcp only). See ProbeFollowUp.
    follow_up: "ProbeFollowUp | None" = None
    # udp only: the port the answer comes back on, for a device that answers
    # by broadcast to a fixed port instead of to the port the probe came from.
    # None = the reply comes back to the sending socket, as before.
    reply_port: int | None = None

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

    def has_any_signal(self) -> bool:
        """Whether anything here can take part in matching."""
        listed = (self.mdns, self.ssdp, self.amx_ddp, self.oui, self.hostname,
                  self.port_open, self.manufacturer_alias)
        single = (self.tcp_probe, self.udp_probe, self.python_probe, self.snmp_pen)
        return any(listed) or any(part is not None for part in single)


class DiscoveryHintError(ValueError):
    """Raised when a driver's ``discovery:`` block is structurally invalid."""


# ---------------------------------------------------------------------------
# OUI prefixes
# ---------------------------------------------------------------------------

_NON_HEX = re.compile(r"[^0-9a-f]")


def hex_digits(value: str) -> str:
    """The hex digits of an OUI or MAC, lower-cased, separators dropped."""
    return _NON_HEX.sub("", value.strip().lower())


def colon_form(digits: str) -> str:
    """``1866961`` -> ``18:66:96:1``: pairs, then any odd digit on its own."""
    return ":".join(digits[i : i + 2] for i in range(0, len(digits), 2))


def normalize_oui_prefix(value: str) -> str | None:
    """Canonicalize an OUI hint or MAC to its lowercase colon-separated prefix.

    Accepts any common style — colon, dash, dot, or no separators. Exactly
    7 or 9 hex digits name an IEEE MA-M or MA-S block and keep their length
    (``18-66-96-1`` -> ``18:66:96:1``). Anything else with at least 6 — a
    bare 3-octet OUI (``001122``, ``00-11-22``, ``0011.22``) or a full MAC
    (``00:11:22:33:44:55``, ``0011.2233.4455``) — keeps the first three
    octets. Returns ``None`` when fewer than three octets of hex are
    present, so callers can warn instead of silently registering a key that
    can never match an observed MAC.

    Shared by this reader, the OUI table (``add_prefix``) and the tier
    matcher's OUI rules, so registration and lookup always agree on the key
    whatever separator style the hint or the observed MAC uses.
    """
    digits = hex_digits(value)
    if len(digits) < 6:
        return None
    if len(digits) in (7, 9):
        return colon_form(digits)
    return colon_form(digits[:6])
