"""ONVIF WS-Discovery probe.

Sends a SOAP-over-UDP Probe to the WS-Discovery multicast group
(239.255.255.250:3702) and parses ProbeMatch responses. ONVIF cameras,
some encoders, and certain video distribution devices respond with a
``Scopes`` URI list that deterministically identifies manufacturer
and (often) hardware model.

Stdlib only — no python-ws-discovery dependency. The Probe envelope is
~700 bytes of fixed XML; responses are parsed with the defusedxml
fast safe parser already in the platform requirements.

Scope URI format (per ONVIF Network Specification §7):
    onvif://www.onvif.org/<category>/<value>
For example:
    onvif://www.onvif.org/manufacturer/Axis
    onvif://www.onvif.org/hardware/M3045-V
    onvif://www.onvif.org/location/UK/London
    onvif://www.onvif.org/Profile/S
The manufacturer scope is the load-bearing field for driver matching.

Reference (MIT) for envelope shape: andreikop/python-ws-discovery.
We do not import or copy code from that project; only the on-the-wire
format (which is dictated by the OASIS WS-Discovery 1.1 spec) is
reproduced here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any

from defusedxml.ElementTree import fromstring as _safe_fromstring
from defusedxml.ElementTree import ParseError as _XMLParseError

from server.discovery.result import Evidence, SignalTier
from server.discovery.tier_matcher import KIND_BROADCAST

log = logging.getLogger("discovery.onvif")

WSD_GROUP = "239.255.255.250"
WSD_PORT = 3702

# Filter to NetworkVideoTransmitter (cameras + most ONVIF AV devices).
# Use ``dn:Device`` instead to match every WS-Discovery responder
# including printers and NAS — too noisy for AV discovery.
_PROBE_TYPES = "dn:NetworkVideoTransmitter"

# XML namespace prefixes used in the response. Match against URIs
# rather than prefixes because devices vary in their prefix choice.
_NS_ENVELOPE = "http://www.w3.org/2003/05/soap-envelope"
_NS_DISCOVERY = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
_NS_ADDRESSING = "http://schemas.xmlsoap.org/ws/2004/08/addressing"

# Match an ONVIF scope URI: onvif://www.onvif.org/<category>/<value...>
_SCOPE_RE = re.compile(
    r"onvif://www\.onvif\.org/([A-Za-z][A-Za-z0-9]*)/(.+)",
    re.IGNORECASE,
)


@dataclass
class ONVIFResult:
    """A device discovered via ONVIF WS-Discovery."""

    ip: str
    types: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    xaddrs: list[str] = field(default_factory=list)
    endpoint_reference: str = ""

    @property
    def manufacturer(self) -> str | None:
        return self._scope_value("manufacturer")

    @property
    def hardware(self) -> str | None:
        return self._scope_value("hardware")

    @property
    def location(self) -> str | None:
        return self._scope_value("location")

    @property
    def name(self) -> str | None:
        return self._scope_value("name")

    def _scope_value(self, category: str) -> str | None:
        target = category.lower()
        for scope in self.scopes:
            match = _SCOPE_RE.match(scope.strip())
            if match and match.group(1).lower() == target:
                return match.group(2).strip()
        return None

    def to_evidence(self) -> Evidence:
        # The matcher disambiguates per-vendor ONVIF drivers via a
        # ``manufacturer`` filter. Pack the parsed scope value into the
        # ``txt`` dict so SignalIndex.find_strong() can pick the right
        # driver among any that claim ``onvif:``.
        txt: dict[str, str] = {}
        if self.manufacturer:
            txt["manufacturer"] = self.manufacturer
        if self.hardware:
            txt["hardware"] = self.hardware
        data: dict[str, Any] = {
            "kind": KIND_BROADCAST,
            "source_id": "onvif",
            "scopes": list(self.scopes),
        }
        if self.manufacturer:
            data["manufacturer"] = self.manufacturer
        if self.hardware:
            data["hardware"] = self.hardware
        if txt:
            data["txt"] = txt
        return Evidence(
            tier=SignalTier.BROADCAST_PROBE,
            source="broadcast:onvif",
            data=data,
        )

    def to_device_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"protocols": ["onvif"]}
        if self.manufacturer:
            info["manufacturer"] = self.manufacturer
        if self.hardware:
            info["model"] = self.hardware
        if self.name:
            info["device_name"] = self.name
        if self.endpoint_reference:
            info["serial_number"] = self.endpoint_reference
        # Best-guess category based on type: NetworkVideoTransmitter is a camera.
        for t in self.types:
            if "NetworkVideoTransmitter" in t:
                info["category"] = "camera"
                break
        return info


def _build_probe_envelope() -> bytes:
    """Build a fresh ONVIF WS-Discovery Probe SOAP envelope.

    Each call generates a new MessageID UUID per spec — duplicate
    MessageIDs are rejected by stricter ONVIF firmware.
    """
    message_id = "urn:uuid:" + str(uuid.uuid4())
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="{_NS_ENVELOPE}"
            xmlns:w="{_NS_ADDRESSING}"
            xmlns:d="{_NS_DISCOVERY}"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>{message_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>{_PROBE_TYPES}</d:Types>
      <d:Scopes/>
    </d:Probe>
  </e:Body>
</e:Envelope>"""
    return envelope.encode("utf-8")


def _parse_probe_match(data: bytes, sender_ip: str) -> ONVIFResult | None:
    """Parse a WS-Discovery ProbeMatch response.

    Returns None for non-ProbeMatch traffic on the multicast port.
    Tolerant of namespace prefix variation across firmware.
    """
    try:
        text = data.decode("utf-8", errors="replace")
        root = _safe_fromstring(text)
    except (_XMLParseError, ValueError):
        return None

    # Verify this is an Envelope. Some firmware sends Action=ProbeMatches,
    # some sends Action=Hello on join. Both contain ProbeMatch elements.
    if not _localname(root.tag) == "Envelope":
        return None

    body = _find_child(root, "Body")
    if body is None:
        return None

    probe_matches_root = _find_child(body, "ProbeMatches")
    if probe_matches_root is None:
        # Some firmware emits a single ProbeMatch directly. Fall back.
        first_match = _find_child(body, "ProbeMatch")
        if first_match is None:
            return None
        return _parse_single_probe_match(first_match, sender_ip)

    first_match = _find_child(probe_matches_root, "ProbeMatch")
    if first_match is None:
        return None
    return _parse_single_probe_match(first_match, sender_ip)


def _parse_single_probe_match(elem, sender_ip: str) -> ONVIFResult | None:
    """Extract fields from a single <ProbeMatch> element."""
    result = ONVIFResult(ip=sender_ip)

    types_elem = _find_child(elem, "Types")
    if types_elem is not None and types_elem.text:
        result.types = types_elem.text.split()

    scopes_elem = _find_child(elem, "Scopes")
    if scopes_elem is not None and scopes_elem.text:
        result.scopes = scopes_elem.text.split()

    xaddrs_elem = _find_child(elem, "XAddrs")
    if xaddrs_elem is not None and xaddrs_elem.text:
        result.xaddrs = xaddrs_elem.text.split()

    epr_elem = _find_child(elem, "EndpointReference")
    if epr_elem is not None:
        addr_elem = _find_child(epr_elem, "Address")
        if addr_elem is not None and addr_elem.text:
            result.endpoint_reference = addr_elem.text.strip()

    if not result.scopes and not result.xaddrs:
        return None
    return result


def _localname(tag: str) -> str:
    """Strip XML namespace from a tag name: '{ns}local' -> 'local'."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _find_child(elem, localname: str):
    """Find first child element by local name (any namespace)."""
    for child in elem:
        if _localname(child.tag) == localname:
            return child
    return None


async def probe_onvif(
    duration: float = 4.0,
    control_ip: str = "",
    target: str | None = None,
) -> dict[str, ONVIFResult]:
    """Run a WS-Discovery probe and collect ProbeMatch responses.

    Args:
        duration: Listen window after sending the Probe. ONVIF spec
            recommends 1-5s; cameras with slow firmware may delay.
        control_ip: Source IP for the multicast send. Empty = OS default.
        target: Optional override of the multicast target (testing only).

    Returns:
        dict keyed by responder IP -> ONVIFResult.
    """
    sock = _make_onvif_socket(control_ip)
    if sock is None:
        return {}

    results: dict[str, ONVIFResult] = {}
    addr = (target or WSD_GROUP, WSD_PORT)

    try:
        loop = asyncio.get_event_loop()
        envelope = _build_probe_envelope()
        try:
            await loop.run_in_executor(
                None, lambda: sock.sendto(envelope, addr),
            )
            log.debug("ONVIF Probe sent to %s:%d", addr[0], addr[1])
        except OSError as exc:
            log.debug("ONVIF Probe send failed: %s", exc)
            return {}

        # Listen for ProbeMatch responses. Spec mandates 5s window;
        # we follow that as default.
        end = loop.time() + duration
        while loop.time() < end:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                sock.settimeout(min(remaining, 0.5))
                data, sender = await loop.run_in_executor(
                    None, lambda: sock.recvfrom(8192),
                )
            except socket.timeout:
                continue
            except OSError as exc:
                log.debug("ONVIF socket error: %s", exc)
                break

            result = _parse_probe_match(data, sender[0])
            if result:
                # First reply wins on duplicate IPs (some cameras send
                # both ProbeMatches and a Hello within the window).
                results.setdefault(result.ip, result)
                log.debug(
                    "ONVIF reply from %s mfg=%s hw=%s",
                    result.ip, result.manufacturer, result.hardware,
                )
    finally:
        try:
            sock.close()
        except OSError:
            pass

    log.info("ONVIF WS-Discovery found %d device(s)", len(results))
    return results


def _make_onvif_socket(control_ip: str = "") -> socket.socket | None:
    """Create a UDP socket for WS-Discovery on the chosen interface."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((control_ip or "", 0))

        # Pin outbound multicast interface when set.
        if control_ip:
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_IF,
                socket.inet_aton(control_ip),
            )

        # WS-Discovery spec recommends TTL 1; some routed networks need
        # higher, but TTL 4 is a safe ceiling that won't escape the LAN.
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
            struct.pack("b", 4),
        )

        sock.setblocking(False)
        return sock
    except OSError as exc:
        log.warning("Could not create ONVIF socket: %s", exc)
        return None
