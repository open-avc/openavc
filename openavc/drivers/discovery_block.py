"""THE reader of a driver's ``discovery:`` block: what it declares, and every
problem in it.

``read_discovery_block`` reads one driver's block (a YAML definition, or a
Python driver's ``DRIVER_INFO``) into a ``DiscoveryHint`` (``discovery_hint``),
the fingerprints and hints the discovery engine matches devices with, and collects what is wrong
with it on the way instead of stopping at the first problem. Every door that
judges a block asks it: the definition walk (``avcdriver_semantic``), a Python
driver's verdict (``python_info``), the runtime parser
(``discovery/hints.py`` ``parse_driver_discovery``, which raises the first
error), and the community catalog's build, which runs a vendored copy.

What it finds comes in three kinds:

* ``errors``: a block no door accepts. The runtime parser has always raised on
  these, so a YAML driver carrying one is refused wherever it is loaded.
* ``authored``: what the author's doors refuse and a loading door reports
  (a ``requires`` that is no version, an empty mDNS or SSDP string, a key an
  mDNS, SSDP or AMX entry does not read, a hostname pattern that does not
  compile). The runtime reads past each of these as it always has.
* ``warnings``: an OUI entry that names no prefix (left out), and a declared
  block with nothing in it to match on. Never a refusal.

An optional key inside a probe, a ``then:`` step or an entry written with a
null value reads as not declared (a null ``timeout_ms`` is its default). A null
``tcp_probe``, ``udp_probe``, ``python``, ``snmp_pen`` or ``model_pattern`` is
refused, as it always has been.

The warnings reach the server log (``discovery/hints.py``) and the catalog's
build; the definition walk does not carry them.

``platform_version`` is the release doing the reading. A block whose
``requires:`` names a newer one is written for a parser this one predates, so
it is not read (``skipped``); one whose ``requires:`` is no version at all is
reported and not read either. With no ``platform_version`` (the catalog, which
judges with the newest parser) every block is read.

Standard library, the contract's own ``spec`` and the transport rule
(``transport_choice``) only: the catalog runs this in a job with no
``openavc`` package installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from openavc.drivers.discovery_hint import (
    AmxDdpFingerprint,
    CustomProbeSpec,
    DiscoveryHint,
    ExtractRule,
    MdnsFingerprint,
    ProbeFollowUp,
    PythonProbe,
    ResponseMatch,
    SsdpFingerprint,
    normalize_oui_prefix,
)
from openavc.drivers.spec import (
    DISALLOWED_OPEN_PORTS as _DISALLOWED_OPEN_PORTS,
    GENERIC_ID_PREFIXES,
    parse_version,
)
from openavc.drivers.transport_choice import declared_transport

# Ports too generic to use as a hint: every web / admin / SSH device on the
# network would match. AV-specific ports (1710, 4352, ...) are fine. The list
# lives in the driver contract (spec), so the published schema and the Builder
# refuse the same ports this reader does.
DISALLOWED_OPEN_PORTS: frozenset[int] = frozenset(_DISALLOWED_OPEN_PORTS)

# Cap on how long a probe may wait for a reply: anything longer stretches the
# scan budget unreasonably.
MAX_PROBE_TIMEOUT_MS: int = 10000

# Reserved ``extract:`` keys whose values feed the manufacturer-alias hint
# path. The probe runner lifts these into the top-level evidence response/txt
# dict so ``extract_vendor_strings`` finds them.
RESERVED_EXTRACT_KEYS: frozenset[str] = frozenset({"manufacturer", "make"})

# The keys the block itself may hold.
KNOWN_DISCOVERY_KEYS: frozenset[str] = frozenset({
    "requires",
    "mdns", "ssdp", "amx_ddp",
    "tcp_probe", "udp_probe", "python",
    "oui", "hostname", "port_open", "manufacturer_alias", "snmp_pen",
})

_KNOWN_PROBE_KEYS = frozenset({
    "port", "send_hex", "send_ascii",
    "expect", "expect_regex", "expect_hex",
    "cross_vendor", "timeout_ms", "tls", "cert_subject",
    "extract", "extract_manufacturer", "then", "reply_port",
})
_KNOWN_FOLLOW_UP_KEYS = frozenset({
    "send_hex", "send_ascii", "expect_silence",
    "expect", "expect_regex", "expect_hex", "timeout_ms",
})
_SSDP_FILTER_KEYS = ("model", "manufacturer", "friendly_name")
_KNOWN_MDNS_KEYS = frozenset({"service", "txt", "cross_vendor"})
_KNOWN_SSDP_KEYS = frozenset({"device_type", "cross_vendor", *_SSDP_FILTER_KEYS})
_KNOWN_AMX_DDP_KEYS = frozenset({"make", "model_pattern", "cross_vendor"})
_KNOWN_PYTHON_KEYS = frozenset({"file", "cross_vendor"})
_MATCHER_KEYS = ("expect", "expect_regex", "expect_hex")

NO_SIGNAL_WARNING = (
    "discovery block declares no fingerprints or hints; "
    "this driver will never participate in matching."
)


@dataclass
class DiscoveryRead:
    """What reading one block came to.

    ``hint`` holds every part that read cleanly, so a caller that wants what
    the block claims (the catalog's cross-driver checks) has it even beside
    an error; it is None for a template, a block that is not a mapping, or a
    block not read (``skipped``). The three lists are module docstring's.
    """

    hint: DiscoveryHint | None = None
    errors: list[str] = field(default_factory=list)
    authored: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------


class _Failed(Exception):
    """One value did not read; its error is already recorded."""


# What ``_Reader.part`` hands back for a part that did not read, where None is
# a value the part may read as (no follow-up step, no reply port).
_NOT_READ = object()

# A ``requires:`` that names no version: reported, and the block not read.
_NO_VERSION = object()

_MDNS_FORMS = "a service-type string or a {service, txt, cross_vendor} mapping"
_SSDP_FORMS = (
    "a device-type string or a {device_type, model, manufacturer, friendly_name, "
    "cross_vendor} mapping"
)
_PYTHON_FORMS = "a path string or a {file, cross_vendor} mapping"


def _is_int(value: Any) -> bool:
    """An integer that is not a bool (YAML's ``true`` is an int to Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_count(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _all_read(*values: Any) -> bool:
    return all(value is not None for value in values)


def _out_of_bounds(value: int, minimum: int | None, maximum: int | None) -> str:
    if minimum is not None and value < minimum:
        return f"must be >= {minimum} (got {value})"
    if maximum is not None and value > maximum:
        return f"must be <= {maximum} (got {value})"
    return ""


class _Reader:
    """One block's reading: each part records what is wrong with it and goes
    on, so every problem is found. A value that does not read raises
    ``_Failed`` to the part that asked, which leaves out what depends on it,
    so a later message never follows from an earlier one."""

    def __init__(self, driver_id: str) -> None:
        self.driver_id = driver_id
        self.read = DiscoveryRead()

    # --- recording -------------------------------------------------------

    def error(self, message: str) -> None:
        self.read.errors.append(message)

    def authored(self, message: str) -> None:
        self.read.authored.append(message)

    def fail(self, message: str) -> _Failed:
        self.error(message)
        return _Failed()

    def part(self, reading, *args, default=None, **kwargs):
        """``reading(*args)``, or ``default`` when it failed (its error
        recorded)."""
        try:
            return reading(*args, **kwargs)
        except _Failed:
            return default

    # --- values ----------------------------------------------------------

    def mapping(self, where: str, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise self.fail(f"discovery.{where} must be a mapping, got {type(raw).__name__}")
        return raw

    def form(self, where: str, raw: Any, forms: str) -> dict[str, Any]:
        """``raw`` as the mapping form of a value that may also be a string."""
        if not isinstance(raw, dict):
            raise self.fail(f"discovery.{where} must be {forms}, got {type(raw).__name__}")
        return raw

    def text(self, where: str, raw: Any) -> str:
        if not _is_text(raw):
            raise self.fail(f"discovery.{where} must be a non-empty string")
        return raw

    def flag(self, where: str, raw: Any) -> bool:
        if raw is None:
            return False
        if not isinstance(raw, bool):
            raise self.fail(f"discovery.{where} must be a bool")
        return raw

    def integer(self, where: str, raw: Any, *, minimum: int | None = None,
                maximum: int | None = None) -> int:
        if not _is_int(raw):
            raise self.fail(f"discovery.{where} must be an integer")
        bound = _out_of_bounds(raw, minimum, maximum)
        if bound:
            raise self.fail(f"discovery.{where} {bound}")
        return raw

    def timeout(self, where: str, raw: dict[str, Any], default: int) -> int:
        value = raw.get("timeout_ms")
        if value is None:
            return default
        return self.integer(f"{where}.timeout_ms", value, minimum=1, maximum=MAX_PROBE_TIMEOUT_MS)

    def hex_bytes(self, where: str, raw: str) -> bytes:
        try:
            return bytes.fromhex(raw.replace(" ", "").replace(":", ""))
        except ValueError as exc:
            raise self.fail(f"discovery.{where} is not valid hex: {exc}") from exc

    def pattern(self, where: str, raw: str) -> re.Pattern:
        try:
            return re.compile(raw)
        except re.error as exc:
            raise self.fail(f"discovery.{where} failed to compile: {exc}") from exc

    def unknown_keys(self, at: str, raw: dict[str, Any], known: frozenset[str]) -> None:
        unknown = set(raw.keys()) - known
        if unknown:
            self.error(f"discovery.{at} has unknown keys: {sorted(unknown)}")

    def entry_keys(self, at: str, raw: dict[str, Any], known: frozenset[str]) -> None:
        """A key a fingerprint entry does not read: refused where it is written."""
        unknown = set(raw.keys()) - known
        if unknown:
            self.authored(f"discovery.{at} entry has unknown keys: {sorted(unknown)}")


def _service(service: str) -> str:
    s = service.strip()
    if not s.endswith("."):
        s = s + "."
    return s.lower()


def _as_list(value: Any) -> list[Any]:
    """A single-or-list input as a list. ``None`` -> ``[]``."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# --- Fingerprint entries ----------------------------------------------------


def _mdns_entry(r: _Reader, raw: Any) -> MdnsFingerprint:
    """A bare service-type string or a ``{service, txt, cross_vendor}`` mapping."""
    if isinstance(raw, str):
        if not raw:
            r.authored("discovery.mdns service must be a non-empty string")
        return MdnsFingerprint(service=_service(raw))
    mapping = r.form("mdns entry", raw, _MDNS_FORMS)
    r.entry_keys("mdns", mapping, _KNOWN_MDNS_KEYS)
    service = r.part(r.text, "mdns.service", mapping.get("service"))
    txt = r.part(_txt, r, mapping.get("txt"))
    cross_vendor = r.part(r.flag, "mdns.cross_vendor", mapping.get("cross_vendor"))
    if not _all_read(service, txt, cross_vendor):
        raise _Failed()
    return MdnsFingerprint(service=_service(service), txt=txt, cross_vendor=cross_vendor)


def _txt(r: _Reader, raw: Any) -> tuple[tuple[str, str], ...]:
    """The TXT pairs an mDNS entry requires, keys lower-cased."""
    txt = raw or {}
    if not isinstance(txt, dict):
        raise r.fail("discovery.mdns.txt must be a mapping")
    return tuple(sorted((str(k).lower(), str(v)) for k, v in txt.items()))


def _ssdp_entry(r: _Reader, raw: Any) -> SsdpFingerprint:
    """A bare device-type string or a mapping.

    The mapping form takes ``device_type`` plus optional ``model`` /
    ``manufacturer`` / ``friendly_name`` filters matched exactly
    (case-insensitive) against the responder's UPnP device description,
    and ``cross_vendor``.
    """
    if isinstance(raw, str):
        if not raw:
            r.authored("discovery.ssdp device_type must be a non-empty string")
        return SsdpFingerprint(device_type=raw)
    mapping = r.form("ssdp entry", raw, _SSDP_FORMS)
    r.entry_keys("ssdp", mapping, _KNOWN_SSDP_KEYS)
    device_type = r.part(r.text, "ssdp.device_type", mapping.get("device_type"))
    filters = r.part(_ssdp_filters, r, mapping)
    cross_vendor = r.part(r.flag, "ssdp.cross_vendor", mapping.get("cross_vendor"))
    if not _all_read(device_type, filters, cross_vendor):
        raise _Failed()
    return SsdpFingerprint(device_type=device_type, fields=filters, cross_vendor=cross_vendor)


def _ssdp_filters(r: _Reader, mapping: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    filters = [
        (key, r.part(r.text, f"ssdp.{key}", mapping[key]))
        for key in _SSDP_FILTER_KEYS
        if mapping.get(key) is not None
    ]
    if not _all_read(*(value for _, value in filters)):
        raise _Failed()
    return tuple(sorted(filters))


def _amx_ddp_entry(r: _Reader, raw: Any) -> AmxDdpFingerprint:
    """A ``{make, model_pattern, cross_vendor}`` mapping."""
    mapping = r.mapping("amx_ddp entry", raw)
    r.entry_keys("amx_ddp", mapping, _KNOWN_AMX_DDP_KEYS)
    make = r.part(r.text, "amx_ddp.make", mapping.get("make"))
    model_pattern = r.part(_model_pattern, r, mapping)
    cross_vendor = r.part(r.flag, "amx_ddp.cross_vendor", mapping.get("cross_vendor"))
    if not _all_read(make, model_pattern, cross_vendor):
        raise _Failed()
    return AmxDdpFingerprint(make=make, model_pattern=model_pattern, cross_vendor=cross_vendor)


def _model_pattern(r: _Reader, mapping: dict[str, Any]) -> str:
    pattern = mapping.get("model_pattern", "*")
    if not isinstance(pattern, str):
        raise r.fail("discovery.amx_ddp.model_pattern must be a string")
    return pattern


# --- Probes -----------------------------------------------------------------


@dataclass
class _Probe:
    """The probe being read: where its messages are placed, its kind, its
    block, and its timeout once read (the ``then:`` step's default)."""

    where: str
    kind: str
    block: dict[str, Any]
    timeout_ms: int | None = None


def _send(r: _Reader, where: str, raw: dict[str, Any]) -> bytes:
    """``send_hex:`` or ``send_ascii:``; empty bytes when neither (a
    connect-only TCP probe)."""
    declared = [key for key in ("send_hex", "send_ascii") if raw.get(key) is not None]
    if len(declared) > 1:
        raise r.fail(f"discovery.{where} declares both send_hex and send_ascii; pick one")
    if not declared:
        return b""
    key = declared[0]
    value = raw[key]
    if not isinstance(value, str):
        raise r.fail(f"discovery.{where}.{key} must be a string")
    if key == "send_ascii":
        return value.encode("utf-8")
    return r.hex_bytes(f"{where}.send_hex", value)


def _declares_a_matcher(raw: dict[str, Any]) -> bool:
    return any(raw.get(key) is not None for key in _MATCHER_KEYS)


def _contains(r: _Reader, at: str, value: Any) -> ResponseMatch:
    return ResponseMatch(contains=r.text(at, value))


def _regex(r: _Reader, at: str, value: Any) -> ResponseMatch:
    source = r.text(at, value)
    return ResponseMatch(regex=r.pattern(at, source), regex_source=source)


def _starts_with(r: _Reader, at: str, value: Any) -> ResponseMatch:
    if not isinstance(value, str):
        raise r.fail(f"discovery.{at} must be a string")
    return ResponseMatch(starts_with=r.hex_bytes(at, value))


# What each matcher key reads its value as.
_MATCHERS = {"expect": _contains, "expect_regex": _regex, "expect_hex": _starts_with}


def _response_match(r: _Reader, where: str, raw: dict[str, Any], *, require_match: bool) -> ResponseMatch:
    """``expect:``, ``expect_regex:`` or ``expect_hex:``: exactly one.

    Mixing them silently AND-matches both, which is almost never the
    author's intent. With ``require_match`` exactly one must be present (a
    UDP probe needs a matcher to filter junk). A TCP probe may declare none
    (a "banner-read" probe for server-speaks-first protocols), but that is
    NOT a pure connect test: the probe runner still requires the device to
    send at least one byte, so a silent open port never matches.
    """
    declared = [key for key in _MATCHER_KEYS if raw.get(key) is not None]
    if len(declared) > 1:
        raise r.fail(
            f"discovery.{where} declares multiple matchers "
            f"({', '.join(declared)}); pick exactly one of expect, "
            "expect_regex, or expect_hex"
        )
    if require_match and not declared:
        raise r.fail(f"discovery.{where} needs exactly one of expect, expect_regex, expect_hex")
    if not declared:
        return ResponseMatch()
    key = declared[0]
    return _MATCHERS[key](r, f"{where}.{key}", raw[key])


def _extract(r: _Reader, where: str, raw: Any) -> list[ExtractRule]:
    """An ``extract:`` block: each field a literal string (a static value) or
    a ``{regex, group}`` mapping (a capture)."""
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise r.fail(f"discovery.{where}.extract must be a mapping")
    rules = [r.part(_extract_field, r, where, name, spec) for name, spec in raw.items()]
    if not _all_read(*rules):
        raise _Failed()
    return rules


def _extract_field(r: _Reader, where: str, field_name: Any, spec: Any) -> ExtractRule:
    if not _is_text(field_name):
        raise r.fail(f"discovery.{where}.extract field names must be non-empty strings")
    if isinstance(spec, str):
        return ExtractRule(field_name=field_name, value=spec)
    if not isinstance(spec, dict):
        raise r.fail(
            f"discovery.{where}.extract.{field_name} must be a "
            "literal string or a {regex, group} mapping"
        )
    return _extract_capture(r, f"{where}.extract.{field_name}", field_name, spec)


def _extract_capture(r: _Reader, at: str, field_name: str, spec: dict[str, Any]) -> ExtractRule:
    """A ``{regex, group}`` capture: a pattern that compiles, and a group number."""
    pattern_str = spec.get("regex")
    if not _is_text(pattern_str):
        raise r.fail(f"discovery.{at} mapping requires a non-empty 'regex' string")
    pattern = r.pattern(f"{at}.regex", pattern_str)
    group = spec.get("group", 1)
    if not _is_count(group):
        raise r.fail(f"discovery.{at}.group must be a non-negative integer")
    return ExtractRule(field_name=field_name, regex=pattern, regex_source=pattern_str, group=group)


def _follow_up(r: _Reader, probe: _Probe) -> ProbeFollowUp | None:
    """A probe's optional ``then:`` step:

        then:
          send_hex: "B0 63 00 B0 62 27 B0 60 7F"   # or send_ascii
          expect_silence: true                      # this device must NOT answer
          timeout_ms: 1000                          # optional; how long silence
                                                    # has to hold to count

    ...or, for a second POSITIVE step, ``expect`` / ``expect_regex`` /
    ``expect_hex`` in place of ``expect_silence``.
    """
    raw = probe.block.get("then")
    if raw is None:
        return None
    at = f"{probe.where}.then"
    if probe.kind != "tcp":
        # A follow-up is a second exchange on an established connection. UDP
        # probes are fire-and-forget broadcasts with no connection to reuse.
        raise r.fail(f"discovery.{at} is only valid on a tcp_probe")
    block = r.mapping(at, raw)
    send = r.part(_step_send, r, at, block)
    expect_silence = r.part(r.flag, f"{at}.expect_silence", block.get("expect_silence"))
    match = r.part(_step_expectation, r, at, block, expect_silence)
    timeout_ms = r.part(r.timeout, at, block, probe.timeout_ms or 3000)
    r.unknown_keys(at, block, _KNOWN_FOLLOW_UP_KEYS)
    if not _all_read(send, match, timeout_ms):
        raise _Failed()
    return ProbeFollowUp(
        send=send, expect_silence=expect_silence, response_match=match, timeout_ms=timeout_ms,
    )


def _step_send(r: _Reader, at: str, block: dict[str, Any]) -> bytes:
    send = _send(r, at, block)
    if not send:
        raise r.fail(
            f"discovery.{at} must declare send_ascii or send_hex "
            "(a follow-up step has to ask something)"
        )
    return send


def _step_expectation(r: _Reader, at: str, block: dict[str, Any], expect_silence: bool | None) -> ResponseMatch:
    """A ``then:`` step expects silence or a match, never both and never neither."""
    if expect_silence is None:
        # The flag did not read, so what the step expects cannot be said.
        raise _Failed()
    problem = _expectation_problem(expect_silence, _declares_a_matcher(block))
    if problem:
        raise r.fail(f"discovery.{at} {problem}")
    return _response_match(r, at, block, require_match=not expect_silence)


def _expectation_problem(expect_silence: bool, has_match: bool) -> str:
    if expect_silence and has_match:
        return (
            "declares both expect_silence and a matcher; a step that "
            "expects no answer cannot also match one"
        )
    if not (expect_silence or has_match):
        return (
            "declares no expectation; add expect_silence: true, "
            "or expect / expect_regex / expect_hex"
        )
    return ""


def _probe(r: _Reader, kind: str, raw: Any) -> CustomProbeSpec:
    """One ``udp_probe:`` / ``tcp_probe:`` block:

        tcp_probe:
          port: 12345
          tls: false                        # optional (tcp only): TLS-wrap the
                                            # socket before send/read, for an
                                            # HTTPS-only device. Default false.
          send_ascii: "QUERY\\r"            # exactly one of send_ascii, send_hex
          expect: "RESPONSE_PREFIX"         # one of expect, expect_regex, expect_hex
          cross_vendor: false               # optional, default false
          timeout_ms: 3000                  # optional
          extract_manufacturer: "AcmeCorp"  # optional sugar for extract.manufacturer
          extract:                          # optional metadata extraction
            model: { regex: "model=(.+)", group: 1 }

    A ``udp_probe`` may also declare ``reply_port``: the port the device's
    answer arrives on when it is not the port the probe was sent from (a
    device that answers by broadcast to a fixed port).

    Banner-read TCP probes may omit ``send_*`` and ``expect*`` entirely —
    the device must still speak first (at least one byte is required; a
    silent open port never matches). UDP probes must declare both (no
    responder filter would match every UDP-noisy host).
    """
    where = f"{kind}_probe"
    probe = _Probe(where, kind, r.mapping(where, raw))
    block = probe.block
    port = r.part(r.integer, f"{where}.port", block.get("port"), minimum=1, maximum=65535)
    send = r.part(_send, r, where, block)
    match = _probe_match(r, probe, send)
    probe.timeout_ms = r.part(r.timeout, where, block, 3000 if kind == "tcp" else 2000)
    cross_vendor = r.part(r.flag, f"{where}.cross_vendor", block.get("cross_vendor"))
    tls = r.part(_tls, r, probe)
    cert_subject = r.part(_cert_subject, r, probe, tls, default=_NOT_READ)
    extract = r.part(_probe_extract, r, probe)
    follow_up = r.part(_follow_up, r, probe, default=_NOT_READ)
    reply_port = r.part(_reply_port, r, probe, default=_NOT_READ)
    # Keys the reader does not know, so typos surface.
    r.unknown_keys(where, block, _KNOWN_PROBE_KEYS)
    read_whole = _all_read(port, send, match, probe.timeout_ms, cross_vendor, tls, extract)
    if not read_whole or _NOT_READ in (cert_subject, follow_up, reply_port):
        raise _Failed()
    pattern, source = cert_subject
    return CustomProbeSpec(
        driver_id=r.driver_id, kind=kind, port=port, send=send, response_match=match,
        timeout_ms=probe.timeout_ms, follow_up=follow_up, cross_vendor=cross_vendor,
        extract=tuple(extract), tls=tls, cert_subject=pattern,
        cert_subject_source=source, reply_port=reply_port,
    )


def _probe_match(r: _Reader, probe: _Probe, send: bytes | None) -> ResponseMatch | None:
    """The probe's matcher, after what its send asks of it: a TCP probe that
    sends needs one, and a UDP probe has to send."""
    if send is not None:
        missing = _what_the_send_needs(probe, send)
        if missing:
            r.error(missing)
            return None
    return r.part(_response_match, r, probe.where, probe.block, require_match=probe.kind == "udp")


def _what_the_send_needs(probe: _Probe, send: bytes) -> str:
    if probe.kind == "udp":
        if send:
            return ""
        return (
            f"discovery.{probe.where} must declare send_ascii or send_hex "
            "(UDP probes need a query payload)"
        )
    if send and not _declares_a_matcher(probe.block):
        # A TCP probe that sends bytes but declares no matcher would emit
        # evidence for any responding host, which is too noisy to be useful.
        return (
            f"discovery.{probe.where} sends bytes but declares no "
            "matcher; add expect, expect_regex, or expect_hex"
        )
    return ""


def _tls(r: _Reader, probe: _Probe) -> bool:
    tls = r.flag(f"{probe.where}.tls", probe.block.get("tls"))
    if tls and probe.kind != "tcp":
        # TLS over UDP would be DTLS, which the probe runner doesn't speak.
        raise r.fail(f"discovery.{probe.where}.tls is only valid on a tcp_probe")
    return tls


def _cert_subject(r: _Reader, probe: _Probe, tls: bool | None) -> tuple[re.Pattern | None, str]:
    raw = probe.block.get("cert_subject")
    if raw is None:
        return None, ""
    if tls is None:
        # The tls flag did not read, so whether this needs it cannot be said.
        raise _Failed()
    if not tls:
        raise r.fail(
            f"discovery.{probe.where}.cert_subject requires tls: true "
            "(the certificate is only read on a TLS probe)"
        )
    source = r.text(f"{probe.where}.cert_subject", raw)
    return r.pattern(f"{probe.where}.cert_subject", source), source


def _probe_extract(r: _Reader, probe: _Probe) -> list[ExtractRule]:
    """``extract:``, with ``extract_manufacturer: "AcmeCorp"`` as sugar for the
    reserved ``manufacturer`` key."""
    extract = r.part(_extract, r, probe.where, probe.block.get("extract"))
    sugar = r.part(_extract_manufacturer, r, probe, extract, default=_NOT_READ)
    if extract is None or sugar is _NOT_READ:
        raise _Failed()
    return extract + ([sugar] if sugar else [])


def _extract_manufacturer(r: _Reader, probe: _Probe, extract: list[ExtractRule] | None) -> ExtractRule | None:
    mfg = probe.block.get("extract_manufacturer")
    if mfg is None:
        return None
    if not _is_text(mfg):
        raise r.fail(f"discovery.{probe.where}.extract_manufacturer must be a non-empty string")
    if any(rule.field_name == "manufacturer" for rule in extract or ()):
        raise r.fail(
            f"discovery.{probe.where}.extract_manufacturer collides "
            "with extract.manufacturer; pick one"
        )
    return ExtractRule(field_name="manufacturer", value=mfg)


def _reply_port(r: _Reader, probe: _Probe) -> int | None:
    raw = probe.block.get("reply_port")
    if raw is None:
        return None
    if probe.kind != "udp":
        # A TCP answer comes back on the connection the probe opened.
        raise r.fail(
            f"discovery.{probe.where}.reply_port is only valid on a "
            "udp_probe (a tcp_probe's answer comes back on its own connection)"
        )
    return r.integer(f"{probe.where}.reply_port", raw, minimum=1, maximum=65535)


def _python_probe(r: _Reader, raw: Any) -> PythonProbe:
    """``python:``: a bare path string or a ``{file, cross_vendor}`` mapping.
    The path is kept as written; the runtime resolves it relative to the
    driver file."""
    if isinstance(raw, str):
        if not raw:
            raise r.fail("discovery.python path must be a non-empty string")
        return PythonProbe(driver_id=r.driver_id, file_path=raw, cross_vendor=False)
    block = r.form("python", raw, _PYTHON_FORMS)
    file_path = r.part(r.text, "python.file", block.get("file"))
    cross_vendor = r.part(r.flag, "python.cross_vendor", block.get("cross_vendor"))
    r.unknown_keys("python", block, _KNOWN_PYTHON_KEYS)
    if not _all_read(file_path, cross_vendor):
        raise _Failed()
    return PythonProbe(driver_id=r.driver_id, file_path=file_path, cross_vendor=cross_vendor)


# --- Hints ------------------------------------------------------------------


def _hint_list(r: _Reader, discovery: dict[str, Any], key: str) -> list[Any]:
    entries = discovery.get(key) or []
    if not isinstance(entries, list):
        raise r.fail(f"discovery.{key} must be a list")
    return entries


def _oui(r: _Reader, discovery: dict[str, Any]) -> list[str]:
    prefixes = []
    for prefix in _hint_list(r, discovery, "oui"):
        if not _is_text(prefix):
            r.error("discovery.oui entries must be non-empty strings")
        elif normalize_oui_prefix(prefix) is None:
            # An entry that can't yield at least 3 octets of hex would register
            # a key no observed MAC ever resolves to: it is left out, and said.
            r.read.warnings.append(
                f"discovery.oui entry {prefix!r} is not a valid OUI prefix "
                "(it needs at least 3 octets of hex, e.g. 00:11:22), so it is left out"
            )
        else:
            prefixes.append(prefix)
    return prefixes


def _hostnames(r: _Reader, discovery: dict[str, Any]) -> list[str]:
    patterns = []
    for pattern in _hint_list(r, discovery, "hostname"):
        if not _is_text(pattern):
            r.error("discovery.hostname entries must be non-empty strings")
            continue
        # The scan matches a host name case-insensitively; a pattern that does
        # not compile is dropped from the index when the scan builds it.
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            r.authored(f"discovery.hostname entry {pattern!r} failed to compile: {exc}")
        patterns.append(pattern)
    return patterns


def _port_problem(port: Any) -> str:
    """What is wrong with one ``port_open`` entry, or ""."""
    if not _is_int(port):
        return f"discovery.port_open entries must be integers, got {port!r}"
    if port < 1 or port > 65535:
        return f"discovery.port_open entry {port} out of range [1, 65535]"
    if port in DISALLOWED_OPEN_PORTS:
        return (
            f"discovery.port_open entry {port} is too generic: "
            f"it would match every web/SSH device. Disallowed: {sorted(DISALLOWED_OPEN_PORTS)}"
        )
    return ""


def _open_ports(r: _Reader, discovery: dict[str, Any]) -> list[int]:
    ports = []
    for port in _hint_list(r, discovery, "port_open"):
        problem = _port_problem(port)
        if problem:
            r.error(problem)
        else:
            ports.append(port)
    return ports


def _aliases(r: _Reader, discovery: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for alias in _hint_list(r, discovery, "manufacturer_alias"):
        normalized = alias.strip().lower() if isinstance(alias, str) else None
        if normalized is None:
            r.error(f"discovery.manufacturer_alias entries must be strings, got {alias!r}")
        elif not normalized:
            r.error("discovery.manufacturer_alias entries must be non-empty")
        elif normalized not in aliases:
            aliases.append(normalized)
    return aliases


# --- One driver's block -----------------------------------------------------


def read_discovery_block(
    driver_info: dict[str, Any], *, platform_version: str | None = None,
) -> DiscoveryRead:
    """Read one driver's ``discovery:`` block: what it declares and every
    problem in it (the module docstring has the three kinds).

    A template (an id starting ``generic_``) takes no part in discovery and
    is not read. A driver that declares no block reads as a hint with nothing
    in it and nothing to say: not being discoverable is the normal state for
    a serial device, a software endpoint, or anything an integrator builds in
    the Driver Builder without hints. The id is not judged here (the
    definition walk owns that); the runtime parser refuses a driver with none.
    """
    reader = _Reader(str(driver_info.get("id") or "").strip())
    discovery = _block_to_read(reader, driver_info, platform_version)
    if discovery is not None:
        reader.read.hint = _read_block(reader, driver_info, discovery)
    return reader.read


def discovery_refusals(driver_info: dict[str, Any]) -> list[str]:
    """Everything the author's doors refuse in a block (``errors``, then
    ``authored``), every block read whatever its ``requires:``: a Python
    driver's verdict, which never refuses a driver where it loads."""
    read = read_discovery_block(driver_info)
    return read.errors + read.authored


def _block_to_read(r: _Reader, driver_info: dict[str, Any], platform_version: str | None) -> dict[str, Any] | None:
    """The block, or None when it is not read: a template's, one that is not
    a mapping (said), or one this release does not read (``skipped``)."""
    if r.driver_id.startswith(GENERIC_ID_PREFIXES):
        return None
    discovery = driver_info.get("discovery") or {}
    if not isinstance(discovery, dict):
        r.error(f"'discovery' must be a mapping, got {type(discovery).__name__}")
        return None
    if _not_read_here(r, discovery.get("requires"), platform_version):
        r.read.skipped = True
        return None
    return discovery


def _read_block(r: _Reader, driver_info: dict[str, Any], discovery: dict[str, Any]) -> DiscoveryHint:
    unknown = set(discovery.keys()) - KNOWN_DISCOVERY_KEYS
    if unknown:
        r.error(
            f"discovery has unknown keys: {sorted(unknown)}. "
            f"Known keys: {sorted(KNOWN_DISCOVERY_KEYS)}"
        )
    hint = DiscoveryHint(
        driver_id=r.driver_id,
        driver_name=str(driver_info.get("name") or r.driver_id),
        manufacturer=str(driver_info.get("manufacturer") or ""),
        category=str(driver_info.get("category") or ""),
        transport=declared_transport(driver_info),
    )
    _read_fingerprints(r, discovery, hint)
    _read_hints(r, discovery, hint)
    if _declares_nothing(r, driver_info, hint):
        r.read.warnings.append(NO_SIGNAL_WARNING)
    return hint


def _declares_nothing(r: _Reader, driver_info: dict[str, Any], hint: DiscoveryHint) -> bool:
    """Only a declared block can "declare no fingerprints": without one the
    sentence would not even be true, and the driver never asked to match. A
    block with an error declares something, wrongly, and says so already."""
    if r.read.errors or "discovery" not in driver_info:
        return False
    return not hint.has_any_signal()


def _not_read_here(r: _Reader, requires: Any, platform_version: str | None) -> bool:
    """Whether ``requires:`` keeps this release from reading the block.

    A block that gates a future feature reads as "needs a newer platform", not
    as a structural error about the feature's (unknown) key, so this is asked
    before anything else in it.
    """
    required = r.part(_required_version, r, requires)
    if required is None or platform_version is None:
        return False
    running = parse_version(platform_version)
    return required is _NO_VERSION or running is None or required > running


def _required_version(r: _Reader, requires: Any) -> Any:
    """The version ``requires:`` names, None for none, ``_NO_VERSION`` for one
    that is no version (said)."""
    if requires is None:
        return None
    if not isinstance(requires, str) or not requires.strip():
        raise r.fail("discovery.requires must be a version string")
    required = parse_version(requires)
    if required is None:
        r.authored(f'discovery.requires must be a version string like "0.23.0", got {requires!r}')
        return _NO_VERSION
    return required


def _read_fingerprints(r: _Reader, discovery: dict[str, Any], hint: DiscoveryHint) -> None:
    for key, entry_reader, found in (
        ("mdns", _mdns_entry, hint.mdns),
        ("ssdp", _ssdp_entry, hint.ssdp),
        ("amx_ddp", _amx_ddp_entry, hint.amx_ddp),
    ):
        for entry in _as_list(discovery.get(key)):
            fingerprint = r.part(entry_reader, r, entry)
            if fingerprint is not None:
                found.append(fingerprint)
    if "tcp_probe" in discovery:
        hint.tcp_probe = r.part(_probe, r, "tcp", discovery["tcp_probe"])
    if "udp_probe" in discovery:
        hint.udp_probe = r.part(_probe, r, "udp", discovery["udp_probe"])
    if "python" in discovery:
        hint.python_probe = r.part(_python_probe, r, discovery["python"])


def _read_hints(r: _Reader, discovery: dict[str, Any], hint: DiscoveryHint) -> None:
    if "snmp_pen" in discovery:
        hint.snmp_pen = r.part(r.integer, "snmp_pen", discovery["snmp_pen"], minimum=1)
    hint.oui = r.part(_oui, r, discovery, default=[])
    hint.hostname = r.part(_hostnames, r, discovery, default=[])
    hint.port_open = r.part(_open_ports, r, discovery, default=[])
    hint.manufacturer_alias = r.part(_aliases, r, discovery, default=[])
