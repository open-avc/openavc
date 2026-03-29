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
import logging
import re
import socket
import struct
from dataclasses import dataclass
from typing import Any
from defusedxml.ElementTree import fromstring as _safe_xml_fromstring, ParseError as _XMLParseError
from xml.etree import ElementTree

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


@dataclass
class SSDPResult:
    """A device discovered via SSDP/UPnP."""
    ip: str
    port: int | None = None
    usn: str | None = None         # Unique Service Name
    st: str | None = None          # Search Target (device type)
    location: str | None = None    # URL to UPnP device description XML
    server: str | None = None      # Server header (often has manufacturer info)
    # Fields populated from UPnP XML description
    friendly_name: str | None = None
    manufacturer: str | None = None
    model_name: str | None = None
    model_number: str | None = None
    serial_number: str | None = None
    udn: str | None = None         # Unique Device Name

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


def _st_to_category(st: str | None) -> str | None:
    """Map SSDP search target to device category.

    MediaRenderer and MediaServer are too broad to categorize reliably
    (Sonos is a MediaRenderer but is audio, not display). Category is
    better determined by manufacturer, protocol probes, and driver matching.
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

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._running = False
        self._results: dict[str, SSDPResult] = {}  # keyed by IP

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
        self._running = True

        try:
            self._sock = _create_ssdp_socket()
        except OSError as e:
            log.warning("Could not create SSDP socket: %s", e)
            return {}

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
        """Send M-SEARCH for each search target."""
        if not self._sock:
            return

        loop = asyncio.get_event_loop()
        for target in SEARCH_TARGETS:
            try:
                message = M_SEARCH_TEMPLATE.format(search_target=target)
                await loop.run_in_executor(
                    None,
                    lambda m=message: self._sock.sendto(
                        m.encode("utf-8"), (SSDP_ADDR, SSDP_PORT)
                    )
                )
            except OSError as e:
                log.debug("Failed to send M-SEARCH for %s: %s", target, e)

            await asyncio.sleep(0.1)

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

        # Create or update result for this IP
        if sender_ip not in self._results:
            self._results[sender_ip] = SSDPResult(ip=sender_ip)

        result = self._results[sender_ip]
        result.usn = headers.get("usn", result.usn)
        result.st = headers.get("st", result.st)
        result.server = headers.get("server", result.server)

        location = headers.get("location")
        if location:
            result.location = location
            # Extract port from location URL
            port = _extract_port_from_url(location)
            if port:
                result.port = port

    async def _fetch_descriptions(self) -> None:
        """Fetch UPnP device description XML for each discovered device."""
        tasks = []
        for ip, result in self._results.items():
            if result.location:
                tasks.append(self._fetch_single_description(result))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_single_description(self, result: SSDPResult) -> None:
        """Fetch and parse a single UPnP device description."""
        if not result.location:
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
        """Safely close the socket."""
        if self._sock:
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
        SERVER: Linux/3.0, UPnP/1.0, Samsung/1.0
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


# --- UPnP XML Parsing ---


def _parse_upnp_xml(result: SSDPResult, xml_text: str) -> None:
    """Parse UPnP device description XML and populate the SSDPResult.

    Expected format (simplified):
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <device>
            <friendlyName>Living Room TV</friendlyName>
            <manufacturer>Samsung</manufacturer>
            <modelName>UE55</modelName>
            <modelNumber>UN55NU8000</modelNumber>
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


def _create_ssdp_socket() -> socket.socket:
    """Create a UDP socket for SSDP M-SEARCH.

    Cross-platform: works on both Windows and Linux.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    # Allow address reuse
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bind to any address — we send to the multicast group
    sock.bind(("", 0))

    # Set TTL for multicast
    sock.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
        struct.pack("b", 4),
    )

    # Non-blocking
    sock.setblocking(False)

    return sock
