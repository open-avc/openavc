"""SSDP/UPnP scanner for device discovery.

Sends M-SEARCH multicast on 239.255.255.250:1900 and collects responses.
Optionally fetches UPnP device description XML for rich identification.
Uses only stdlib: asyncio, socket, http, xml.

References:
  - UPnP Device Architecture 1.0 (SSDP section)
  - HTTP/1.1 (RFC 2616) — SSDP response format
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import struct
from dataclasses import dataclass, field as dataclass_field
from typing import Any
from defusedxml.ElementTree import fromstring as _safe_xml_fromstring, ParseError as _XMLParseError
from xml.etree import ElementTree

from server.discovery.multicast import (
    ANY_INTERFACE,
    join_group_on_interfaces,
    send_per_interface,
    set_shared_port_reuse,
)
from server.discovery.network_scanner import get_interface_ips

log = logging.getLogger("discovery.ssdp")

# SSDP constants
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900

# M-SEARCH request template
M_SEARCH_TEMPLATE = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: {search_target}\r\n"
    "MX: 3\r\n"
    "\r\n"
)

# Search targets — general + AV-relevant types
SEARCH_TARGETS = [
    "ssdp:all",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:device:Basic:1",
]

# UPnP XML namespaces
UPNP_NS = {"upnp": "urn:schemas-upnp-org:device-1-0"}


# A device answering ``ssdp:all`` sends one response per advertised type
# (rootdevice, embedded devices, services) — bound how many distinct types
# one IP may accumulate so a chatty or hostile stack can't grow the record
# without limit.
MAX_DEVICE_TYPES_PER_IP = 32

# Cap on distinct sender IPs recorded per scan window — mirrors
# amx_ddp_scanner.MAX_BEACON_SOURCES. A spoofed responder can emit an
# endless stream of distinct source IPs; without this the ``_results`` dict
# (and the description fan-out below) would grow without limit.
MAX_SSDP_SOURCES = 512

# UPnP description fetches run concurrently but capped. A scan window can
# accumulate hundreds of responders and — unlike a bounded ping/port sweep —
# each fetch opens an outbound TCP connection, so an uncapped gather would be
# a connection-flood / internal-port-scan amplifier.
MAX_DESCRIPTION_FETCHES = 10


@dataclass
class SSDPResult:
    """A device discovered via SSDP/UPnP."""
    ip: str
    port: int | None = None
    usn: str | None = None         # Unique Service Name
    st: str | None = None          # Search Target (most recent response)
    location: str | None = None    # URL to UPnP device description XML
    server: str | None = None      # Server header (often has manufacturer info)
    # Every distinct UPnP type observed for this IP, in arrival order:
    # response ST headers, USN suffixes (uuid:X::<type>), and the
    # devdesc.xml <deviceType>. A device answering ssdp:all responds once
    # per advertised type, and drivers fingerprint the family device-type
    # URN — which is NOT necessarily the last response to arrive, so a
    # single last-writer-wins ``st`` cannot carry the match.
    device_types: list[str] = dataclass_field(default_factory=list)
    # Fields populated from UPnP XML description
    friendly_name: str | None = None
    manufacturer: str | None = None
    model_name: str | None = None
    model_number: str | None = None
    serial_number: str | None = None
    udn: str | None = None         # Unique Device Name

    def note_device_type(self, value: str | None) -> None:
        """Record one observed UPnP type identifier, deduplicated.

        Per-unit ``uuid:*`` identifiers are skipped — no driver rule can
        meaningfully claim one. ``upnp:rootdevice`` is kept: paired with a
        description filter it is a legitimate fingerprint for devices that
        advertise nothing more specific.
        """
        if not value:
            return
        v = value.strip()
        if not v or v.lower().startswith("uuid:"):
            return
        if v in self.device_types:
            return
        if len(self.device_types) >= MAX_DEVICE_TYPES_PER_IP:
            return
        self.device_types.append(v)

    def to_device_info(self) -> dict[str, Any]:
        """Convert to a dict suitable for merge_device_info()."""
        info: dict[str, Any] = {}

        if self.friendly_name:
            info["device_name"] = self.friendly_name
        if self.manufacturer:
            info["manufacturer"] = self.manufacturer

        # Use model_name, append model_number if both present
        if self.model_name:
            model = self.model_name
            if self.model_number and self.model_number not in model:
                model = f"{model} {self.model_number}"
            info["model"] = model
        elif self.model_number:
            info["model"] = self.model_number

        if self.serial_number:
            info["serial_number"] = self.serial_number

        # Build ssdp_info dict for the device record
        ssdp_info: dict[str, Any] = {}
        if self.usn:
            ssdp_info["usn"] = self.usn
        if self.st:
            ssdp_info["st"] = self.st
        if self.server:
            ssdp_info["server"] = self.server
        if self.location:
            ssdp_info["location"] = self.location
        if self.udn:
            ssdp_info["udn"] = self.udn
        if ssdp_info:
            info["ssdp_info"] = ssdp_info

        # Try to infer category from ST
        category = _st_to_category(self.st)
        if category:
            info["category"] = category

        return info

    def to_evidence_records(self) -> list:
        """Emit one passive_listener Evidence record per observed UPnP type.

        A driver's ``ssdp:`` fingerprint names the family device-type URN,
        which is just one of the several types a device advertises — every
        distinct observed type gets a record so the matcher can find the
        claimed one regardless of response arrival order. Empty when no
        type was observed at all (e.g. a response with only USN/Location).
        """
        from server.discovery.result import Evidence, SignalTier
        from server.discovery.tier_matcher import KIND_SSDP

        # Direct constructions (tests, companions) may set ``st`` without
        # going through note_device_type — fall back to it.
        types = self.device_types or ([self.st] if self.st else [])

        # Device-description fields double as the matcher's observed-field
        # map, so ssdp rules can filter on model/manufacturer the way mdns
        # rules filter on TXT records.
        txt = {
            key: value
            for key, value in (
                ("model", self.model_name),
                ("manufacturer", self.manufacturer),
                ("friendly_name", self.friendly_name),
            )
            if value
        }

        records = []
        for device_type in types:
            data = {
                "kind": KIND_SSDP,
                "source_id": device_type,
            }
            if self.manufacturer:
                data["manufacturer"] = self.manufacturer
            if self.model_name:
                data["model"] = self.model_name
            if self.friendly_name:
                data["friendly_name"] = self.friendly_name
            if self.server:
                data["server"] = self.server
            if txt:
                data["txt"] = dict(txt)
            records.append(Evidence(
                tier=SignalTier.PASSIVE_LISTENER,
                source=f"ssdp:{device_type}",
                data=data,
            ))
        return records


def _st_to_category(st: str | None) -> str | None:
    """Map SSDP search target to device category.

    MediaRenderer and MediaServer are too broad to categorize reliably
    (a wireless audio speaker is a MediaRenderer but is audio, not
    display). Category is better determined by manufacturer, protocol
    probes, and driver matching.
    """
    # Intentionally returns None — broad UPnP types are ambiguous.
    # Specific category comes from OUI, protocol probes, or driver match.
    return None


class SSDPScanner:
    """SSDP/UPnP device scanner.

    Sends M-SEARCH multicast and collects responses. Optionally fetches
    UPnP device description XML for rich device identification.
    Uses only stdlib (socket, asyncio, xml).
    """

    def __init__(self, control_ip: str = "") -> None:
        """``control_ip``: bind outbound multicast to this interface IP.
        Empty = OS default route. Required for the multi-NIC AV scenario
        where the control VLAN is not the default route.
        """
        self._sock: socket.socket | None = None
        self._running = False
        self._results: dict[str, SSDPResult] = {}  # keyed by IP
        self._cap_warned = False
        self._control_ip = control_ip
        # Interface IPs M-SEARCH goes out on (one send per entry; responses
        # come back unicast to the bound port regardless of interface).
        self._send_ifaces: list[str] = []
        # Environment failure that kept the scanner from working at all —
        # surfaced as a scan warning (see MDNSScanner.env_error).
        self.env_error: str | None = None

    @property
    def results(self) -> dict[str, SSDPResult]:
        return dict(self._results)

    async def scan(
        self,
        timeout: float = 5.0,
        fetch_descriptions: bool = True,
    ) -> dict[str, SSDPResult]:
        """Send M-SEARCH and collect responses.

        Args:
            timeout: How long to listen for responses.
            fetch_descriptions: Whether to fetch UPnP XML descriptions.

        Returns:
            Dict of IP -> SSDPResult for discovered devices.
        """
        self._results.clear()
        self._cap_warned = False
        self._running = True
        self.env_error = None

        try:
            self._sock = _create_ssdp_socket(control_ip=self._control_ip)
        except OSError as e:
            log.warning("Could not create SSDP socket: %s", e)
            self.env_error = f"SSDP scanner unavailable: {e}"
            return {}

        # M-SEARCH goes out once per interface so it reaches every attached
        # network even without a multicast route in the main table. With a
        # control interface configured, that one interface is the list.
        if self._control_ip:
            self._send_ifaces = [self._control_ip]
        else:
            self._send_ifaces = get_interface_ips() or [ANY_INTERFACE]

        try:
            # Send M-SEARCH for each search target
            await self._send_searches()

            # Listen for responses
            await self._listen(timeout)

            # Optionally fetch UPnP device description XML
            if fetch_descriptions:
                await self._fetch_descriptions()
        except asyncio.CancelledError:
            log.debug("SSDP scan cancelled")
        except Exception:
            log.warning("SSDP scan error", exc_info=True)
        finally:
            self._running = False
            self._close_socket()

        log.info("SSDP scan found %d devices", len(self._results))
        return dict(self._results)

    async def stop(self) -> None:
        """Stop the SSDP scanner."""
        self._running = False
        self._close_socket()

    async def _send_searches(self) -> None:
        """Send M-SEARCH for each search target, once per interface."""
        if not self._sock:
            return

        total_sent = 0
        loop = asyncio.get_event_loop()
        for target in SEARCH_TARGETS:
            try:
                message = M_SEARCH_TEMPLATE.format(search_target=target)
                total_sent += await loop.run_in_executor(
                    None, send_per_interface,
                    self._sock, message.encode("utf-8"),
                    (SSDP_ADDR, SSDP_PORT), self._send_ifaces,
                )
            except OSError as e:
                log.debug("Failed to send M-SEARCH for %s: %s", target, e)

            await asyncio.sleep(0.1)

        if total_sent == 0:
            # Every send on every interface failed — devices were never
            # asked, so an empty result set is an environment problem.
            self.env_error = "SSDP M-SEARCH could not be sent on any interface"
            log.warning(self.env_error)

    async def _listen(self, timeout: float) -> None:
        """Listen for SSDP responses."""
        if not self._sock:
            return

        loop = asyncio.get_event_loop()
        end_time = loop.time() + timeout

        while self._running and loop.time() < end_time:
            remaining = end_time - loop.time()
            if remaining <= 0:
                break

            try:
                self._sock.settimeout(min(remaining, 0.5))
                data, addr = await loop.run_in_executor(
                    None, lambda: self._sock.recvfrom(4096)
                )
                self._process_response(data, addr[0])
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    log.debug("SSDP socket error during listen", exc_info=True)
                break

    def _process_response(self, data: bytes, sender_ip: str) -> None:
        """Parse an SSDP response."""
        try:
            text = data.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, LookupError):
            return

        headers = parse_ssdp_response(text)
        if not headers:
            return

        # Skip if this is our own M-SEARCH being echoed back
        if text.startswith("M-SEARCH"):
            return

        # Unsolicited NOTIFY beacons carry the advertised type in NT (not ST,
        # which only M-SEARCH replies use) and announce departures via NTS.
        # An ssdp:byebye means the device is leaving — drop any record for it.
        if headers.get("nts") == "ssdp:byebye":
            self._results.pop(sender_ip, None)
            return

        # Create or update result for this IP, subject to the distinct-source
        # cap so a spoofed responder can't grow the dict without limit.
        if sender_ip not in self._results:
            if len(self._results) >= MAX_SSDP_SOURCES:
                if not self._cap_warned:
                    self._cap_warned = True
                    log.warning(
                        "SSDP scanner hit the %d distinct-source cap; "
                        "ignoring new sources for the rest of the scan window",
                        MAX_SSDP_SOURCES,
                    )
                return
            self._results[sender_ip] = SSDPResult(ip=sender_ip)

        result = self._results[sender_ip]
        result.usn = headers.get("usn", result.usn)
        # ST is present on M-SEARCH replies; NT carries the type on NOTIFY.
        type_urn = headers.get("st") or headers.get("nt")
        if type_urn:
            result.st = type_urn
        result.server = headers.get("server", result.server)

        # Accumulate every advertised type — an ssdp:all responder sends
        # one response per type, and the family device-type URN a driver
        # fingerprints is rarely the last to arrive.
        result.note_device_type(type_urn)
        usn = headers.get("usn") or ""
        if "::" in usn:
            result.note_device_type(usn.split("::", 1)[1])

        location = headers.get("location")
        if location:
            result.location = location
            # Extract port from location URL
            port = _extract_port_from_url(location)
            if port:
                result.port = port

    async def _fetch_descriptions(self) -> None:
        """Fetch UPnP device description XML for each discovered device.

        Concurrency-capped: a scan window can accumulate many responders and
        each fetch opens an outbound TCP connection, so an uncapped gather
        would be a connection-flood amplifier. The per-fetch SSRF guard (only
        the responder's own LOCATION is honored) lives in
        ``_fetch_single_description``.
        """
        sem = asyncio.Semaphore(MAX_DESCRIPTION_FETCHES)

        async def fetch_guarded(result: SSDPResult) -> None:
            async with sem:
                await self._fetch_single_description(result)

        tasks = [
            fetch_guarded(result)
            for result in self._results.values()
            if result.location
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_single_description(self, result: SSDPResult) -> None:
        """Fetch and parse a single UPnP device description.

        A UPnP device serves its own description, so a legitimate LOCATION
        host is the responder's own IP. A spoofed SSDP responder can put any
        host:port in LOCATION — honoring an arbitrary host would turn
        discovery into a request-forgery / internal-port-scan primitive — so
        the fetch only proceeds when the LOCATION host is an IP literal equal
        to the sender IP.
        """
        if not result.location:
            return

        if not _location_host_is_sender(result.location, result.ip):
            log.debug(
                "Skipping UPnP description fetch for %s: LOCATION host in %s "
                "is not the responder's own IP (possible SSRF attempt)",
                result.ip, result.location,
            )
            return

        try:
            xml_text = await _http_get(result.location, timeout=3.0)
            if xml_text:
                _parse_upnp_xml(result, xml_text)
        except Exception:
            log.debug(
                "Failed to fetch UPnP description from %s",
                result.location, exc_info=True,
            )

    def _close_socket(self) -> None:
        """Safely close the socket.

        ``shutdown`` before ``close``: the listen loop runs ``recvfrom`` in
        the default executor, and closing the socket from another thread
        does not wake a thread blocked in ``recvfrom`` on Linux/Windows —
        only ``shutdown`` does. Without it, a cancel/stop leaves that pool
        thread parked until its socket timeout, briefly starving the shared
        executor when many scanners cancel at once. ``shutdown`` raises on
        an unconnected socket (harmless), so its error is ignored.
        """
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# --- SSDP Response Parsing ---


def parse_ssdp_response(text: str) -> dict[str, str] | None:
    """Parse an SSDP HTTP-like response into headers dict.

    SSDP responses look like:
        HTTP/1.1 200 OK
        CACHE-CONTROL: max-age=1800
        LOCATION: http://192.168.1.50:49152/description.xml
        SERVER: Linux/3.0, UPnP/1.0, ExampleVendor/1.0
        ST: urn:schemas-upnp-org:device:MediaRenderer:1
        USN: uuid:abc123::urn:schemas-upnp-org:device:MediaRenderer:1

    Also handles NOTIFY messages:
        NOTIFY * HTTP/1.1
        HOST: 239.255.255.250:1900
        ...

    Returns header dict with lowercase keys, or None if not parseable.
    """
    lines = text.strip().split("\r\n")
    if not lines:
        return None

    # First line should be HTTP response or NOTIFY
    first = lines[0]
    if not (first.startswith("HTTP/") or first.startswith("NOTIFY")):
        return None

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()

    return headers if headers else None


def _extract_port_from_url(url: str) -> int | None:
    """Extract port number from a URL like 'http://192.168.1.50:8080/desc.xml'."""
    match = re.search(r"https?://[^/:]+:(\d+)", url)
    if match:
        return int(match.group(1))
    return None


def _location_host_is_sender(location: str, sender_ip: str) -> bool:
    """True only when the LOCATION URL host is an IP literal equal to sender_ip.

    UPnP devices serve their own description, so a legitimate LOCATION host is
    the responder's own address. Rejecting anything else — a different IP, or
    a hostname that would have to be resolved (a DNS-rebinding vector) — keeps
    the description fetch from being pointed at arbitrary internal hosts by a
    spoofed responder.
    """
    match = re.match(r"https?://([^/:]+)", location)
    if not match:
        return False
    try:
        return ipaddress.ip_address(match.group(1)) == ipaddress.ip_address(sender_ip)
    except ValueError:
        return False


# --- UPnP XML Parsing ---


def _parse_upnp_xml(result: SSDPResult, xml_text: str) -> None:
    """Parse UPnP device description XML and populate the SSDPResult.

    Expected format (simplified):
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <device>
            <friendlyName>Living Room TV</friendlyName>
            <manufacturer>Acme Display Co</manufacturer>
            <modelName>Foo-55</modelName>
            <modelNumber>FOO-55-1000</modelNumber>
            <serialNumber>ABC123</serialNumber>
            <UDN>uuid:abc-def-123</UDN>
          </device>
        </root>
    """
    try:
        root = _safe_xml_fromstring(xml_text)
    except (_XMLParseError, Exception):
        log.debug("Failed to parse UPnP XML")
        return

    # Find device element — try with namespace first, then without
    device = root.find("upnp:device", UPNP_NS)
    if device is None:
        device = root.find("device")
    if device is None:
        # Try searching all descendants
        for elem in root.iter():
            tag = elem.tag
            if isinstance(tag, str) and tag.endswith("}device"):
                device = elem
                break
    if device is None:
        return

    # Extract fields — try with namespace, then without
    result.friendly_name = _get_xml_text(device, "friendlyName")
    result.manufacturer = _get_xml_text(device, "manufacturer")
    result.model_name = _get_xml_text(device, "modelName")
    result.model_number = _get_xml_text(device, "modelNumber")
    result.serial_number = _get_xml_text(device, "serialNumber")
    result.udn = _get_xml_text(device, "UDN")
    # The description's deviceType is the definitive device-type URN —
    # present even when the matching M-SEARCH response was lost or
    # arrived under a generic ST like upnp:rootdevice.
    result.note_device_type(_get_xml_text(device, "deviceType"))


def _get_xml_text(parent: ElementTree.Element, tag: str) -> str | None:
    """Get text content of a child element, handling namespaces."""
    # Try with UPnP namespace
    elem = parent.find(f"upnp:{tag}", UPNP_NS)
    if elem is None:
        # Try without namespace
        elem = parent.find(tag)
    if elem is None:
        # Try with wildcard namespace
        for child in parent:
            child_tag = child.tag
            if isinstance(child_tag, str) and child_tag.endswith(f"}}{tag}"):
                elem = child
                break
    if elem is not None and elem.text:
        return elem.text.strip()
    return None


# --- HTTP Fetch ---


async def _http_get(url: str, timeout: float = 3.0) -> str | None:
    """Minimal HTTP GET using raw sockets. No external dependencies.

    Only supports http:// (not https) — UPnP descriptions are always HTTP.
    """
    match = re.match(r"http://([^/:]+)(?::(\d+))?(/.*)$", url)
    if not match:
        return None

    host = match.group(1)
    port = int(match.group(2)) if match.group(2) else 80
    path = match.group(3) or "/"

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, OSError):
        return None

    try:
        request = (
            f"GET {path} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(request.encode("utf-8"))
        await writer.drain()

        response = await asyncio.wait_for(
            reader.read(16384),
            timeout=timeout,
        )
        writer.close()

        text = response.decode("utf-8", errors="replace")

        # Skip HTTP headers — body starts after \r\n\r\n
        header_end = text.find("\r\n\r\n")
        if header_end >= 0:
            return text[header_end + 4:]
        return text
    except (asyncio.TimeoutError, OSError):
        return None
    finally:
        try:
            writer.close()
        except OSError:
            pass


# --- Socket Creation ---


def _create_ssdp_socket(control_ip: str = "") -> socket.socket:
    """Create a UDP socket that both M-SEARCHes and listens for NOTIFY beacons.

    Binding to the well-known SSDP port and joining 239.255.255.250 lets the
    socket receive unsolicited NOTIFY (ssdp:alive / ssdp:byebye) announcements
    in addition to unicast M-SEARCH replies — matching the mDNS and AMX DDP
    listeners (without the group join, devices that only beacon and never
    answer M-SEARCH for the queried STs are missed). When ``control_ip`` is
    set the group is joined via that interface only; otherwise once per
    interface with INADDR_ANY as the fallback (see ``discovery.multicast``).
    The outbound multicast interface is pinned per send (see
    ``_send_searches``). Raises OSError when the group could not be joined on
    any interface.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    set_shared_port_reuse(sock)

    try:
        # Bind to the well-known port so multicast NOTIFY beacons are heard;
        # unicast M-SEARCH replies arrive here too.
        sock.bind(("", SSDP_PORT))

        joined = join_group_on_interfaces(sock, SSDP_ADDR, control_ip=control_ip)
        if not joined:
            raise OSError(f"could not join {SSDP_ADDR} on any interface")

        # TTL for outbound M-SEARCH multicast.
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
            struct.pack("b", 4),
        )

        sock.setblocking(False)
    except OSError:
        sock.close()
        raise

    return sock
