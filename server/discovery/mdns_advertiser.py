"""mDNS service advertiser for OpenAVC.

Advertises this OpenAVC instance as _openavc._tcp.local. using multicast DNS
so mobile panel apps can auto-discover the server on the LAN.

Custom implementation using the same stdlib-only DNS wire format as mdns_scanner.py.
No dependency on zeroconf (LGPL).

References:
  - RFC 6762: Multicast DNS
  - RFC 6763: DNS-Based Service Discovery
  - RFC 1035: DNS wire format
"""

from __future__ import annotations

import asyncio
import re
import socket
import struct

from server.discovery.mdns_scanner import (
    DNS_CLASS_IN,
    DNS_TYPE_A,
    DNS_TYPE_PTR,
    DNS_TYPE_SRV,
    DNS_TYPE_TXT,
    MDNS_ADDR,
    MDNS_PORT,
    decode_dns_name,
    encode_dns_name,
)
from server.utils.logger import get_logger

log = get_logger(__name__)

# Service type for OpenAVC panel discovery
SERVICE_TYPE = "_openavc._tcp.local."

# RFC 6762 Section 8.3: send multiple initial announcements
ANNOUNCEMENT_COUNT = 3
ANNOUNCEMENT_INTERVAL = 1.0  # seconds between initial announcements

# Periodic re-announcement interval
RE_ANNOUNCE_INTERVAL = 60.0  # seconds

# Default TTL for mDNS records (75 minutes)
DEFAULT_TTL = 4500


# --- Helpers ---


def _get_local_ip() -> str:
    """Detect the primary local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _sanitize_instance_name(name: str) -> str:
    """Sanitize a project name for use as a DNS service instance name.

    Replaces spaces with hyphens, strips invalid characters,
    and truncates to the 63-byte DNS label limit.
    """
    sanitized = name.replace(" ", "-")
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "", sanitized)
    sanitized = sanitized.strip("-")
    # Truncate to 63 bytes (DNS label limit per RFC 1035)
    encoded = sanitized.encode("utf-8")[:63]
    sanitized = encoded.decode("utf-8", errors="ignore").strip("-")
    return sanitized or "OpenAVC"


def _sanitize_hostname(name: str) -> str:
    """Sanitize a hostname for DNS use."""
    sanitized = re.sub(r"[^a-zA-Z0-9\-]", "", name.replace(" ", "-"))
    sanitized = sanitized.strip("-")
    encoded = sanitized.encode("utf-8")[:63]
    sanitized = encoded.decode("utf-8", errors="ignore").strip("-")
    return sanitized or "openavc"


# --- DNS Wire Format ---


def encode_txt_rdata(pairs: dict[str, str]) -> bytes:
    """Encode key=value pairs as DNS TXT record rdata.

    Inverse of ``_parse_txt_rdata`` in mdns_scanner.py.
    Each pair becomes a length-prefixed UTF-8 string.
    """
    result = b""
    for key, value in pairs.items():
        entry = f"{key}={value}".encode("utf-8")
        if len(entry) > 255:
            entry = entry[:255]
        result += struct.pack("B", len(entry)) + entry
    if not result:
        # RFC 6763 Section 6.1: empty TXT must have single zero byte
        result = b"\x00"
    return result


def _build_resource_record(
    name: str, rtype: int, rclass: int, ttl: int, rdata: bytes
) -> bytes:
    """Build a single DNS resource record in wire format."""
    name_bytes = encode_dns_name(name)
    return name_bytes + struct.pack("!HHIH", rtype, rclass, ttl, len(rdata)) + rdata


def build_announcement_records(
    instance_name: str,
    service_type: str,
    hostname: str,
    ip: str,
    port: int,
    txt_pairs: dict[str, str],
    ttl: int = DEFAULT_TTL,
) -> list[bytes]:
    """Build the full DNS-SD record set for service announcement.

    Returns a list of 4 resource records (PTR, SRV, TXT, A).
    """
    instance_fqdn = f"{instance_name}.{service_type}"
    hostname_fqdn = f"{hostname}.local."

    # PTR: _openavc._tcp.local. -> <instance>._openavc._tcp.local.
    ptr_rdata = encode_dns_name(instance_fqdn)
    ptr_record = _build_resource_record(
        service_type, DNS_TYPE_PTR, DNS_CLASS_IN, ttl, ptr_rdata
    )

    # SRV: <instance>._openavc._tcp.local. -> <hostname>.local. port <port>
    srv_rdata = struct.pack("!HHH", 0, 0, port) + encode_dns_name(hostname_fqdn)
    srv_record = _build_resource_record(
        instance_fqdn, DNS_TYPE_SRV, DNS_CLASS_IN, ttl, srv_rdata
    )

    # TXT: <instance>._openavc._tcp.local. -> key=value metadata
    txt_rdata = encode_txt_rdata(txt_pairs)
    txt_record = _build_resource_record(
        instance_fqdn, DNS_TYPE_TXT, DNS_CLASS_IN, ttl, txt_rdata
    )

    # A: <hostname>.local. -> IPv4 address
    a_rdata = socket.inet_aton(ip)
    a_record = _build_resource_record(
        hostname_fqdn, DNS_TYPE_A, DNS_CLASS_IN, ttl, a_rdata
    )

    return [ptr_record, srv_record, txt_record, a_record]


def build_dns_response(records: list[bytes]) -> bytes:
    """Build a complete mDNS response packet from resource records.

    Sets QR=1 (response) and AA=1 (authoritative) in flags.
    """
    header = struct.pack(
        "!HHHHHH",
        0,             # Transaction ID (0 for mDNS)
        0x8400,        # Flags: QR=1, AA=1
        0,             # Question count
        len(records),  # Answer count
        0,             # Authority count
        0,             # Additional count
    )
    return header + b"".join(records)


def _parse_query_questions(data: bytes) -> list[tuple[str, int]]:
    """Parse questions from a DNS query packet.

    Returns list of (name, qtype) tuples.
    Only returns results for standard queries (QR bit = 0).
    """
    if len(data) < 12:
        return []

    flags = struct.unpack("!H", data[2:4])[0]
    if flags & 0x8000:  # QR bit set = response, not query
        return []

    qdcount = struct.unpack("!H", data[4:6])[0]
    offset = 12
    questions: list[tuple[str, int]] = []

    for _ in range(qdcount):
        if offset >= len(data):
            break
        try:
            name, offset = decode_dns_name(data, offset)
        except Exception:
            break
        if offset + 4 > len(data):
            break
        qtype, _qclass = struct.unpack("!HH", data[offset : offset + 4])
        offset += 4
        questions.append((name, qtype))

    return questions


# --- Socket ---


def _create_advertiser_socket() -> socket.socket:
    """Create a UDP socket for mDNS advertisement.

    Similar to _create_mdns_socket() in mdns_scanner.py but with
    IP_MULTICAST_LOOP disabled to avoid receiving own announcements.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock.bind(("", MDNS_PORT))

    # Join multicast group (required to receive queries)
    mreq = struct.pack(
        "4s4s",
        socket.inet_aton(MDNS_ADDR),
        socket.inet_aton("0.0.0.0"),
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    # mDNS uses TTL=255
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)

    # Disable loopback so we don't receive our own packets
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)

    sock.setblocking(False)
    return sock


# --- Advertiser ---


class MDNSAdvertiser:
    """Advertises this OpenAVC instance via mDNS/DNS-SD.

    Broadcasts ``_openavc._tcp.local.`` so mobile panel apps can
    auto-discover the server on the LAN.
    """

    def __init__(
        self,
        instance_name: str,
        instance_id: str,
        http_port: int,
        version: str,
    ):
        self._instance_name = _sanitize_instance_name(instance_name)
        self._instance_id = instance_id
        self._http_port = http_port
        self._version = version

        self._sock: socket.socket | None = None
        self._running = False
        self._responder_task: asyncio.Task | None = None
        self._local_ip: str = ""
        self._hostname: str = ""

    async def start(self) -> None:
        """Start advertising the service via mDNS."""
        self._local_ip = _get_local_ip()
        self._hostname = _sanitize_hostname(socket.gethostname())
        self._sock = _create_advertiser_socket()
        self._running = True

        # Send initial announcements (RFC 6762 Section 8.3)
        for i in range(ANNOUNCEMENT_COUNT):
            await self._send_announcement()
            if i < ANNOUNCEMENT_COUNT - 1:
                await asyncio.sleep(ANNOUNCEMENT_INTERVAL)

        # Start the responder loop
        self._responder_task = asyncio.create_task(self._responder_loop())
        self._responder_task.add_done_callback(self._on_task_done)

        log.info(
            "mDNS: Advertising %s.%s on %s:%d",
            self._instance_name,
            SERVICE_TYPE,
            self._local_ip,
            self._http_port,
        )

    async def stop(self) -> None:
        """Stop advertising and send goodbye packets."""
        self._running = False

        # Send goodbye packets (TTL=0) twice for redundancy
        if self._sock:
            for i in range(2):
                await self._send_goodbye()
                if i == 0:
                    await asyncio.sleep(0.25)

        # Cancel responder task
        if self._responder_task and not self._responder_task.done():
            self._responder_task.cancel()
            try:
                await self._responder_task
            except asyncio.CancelledError:
                pass

        # Close socket
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

        log.info("mDNS: Stopped advertising")

    def update_name(self, new_name: str) -> None:
        """Update the advertised instance name (e.g., after project rename)."""
        self._instance_name = _sanitize_instance_name(new_name)

    async def _responder_loop(self) -> None:
        """Listen for mDNS queries and respond; periodically re-announce."""
        loop = asyncio.get_running_loop()
        last_announce = loop.time()

        while self._running:
            # Periodic re-announcement
            now = loop.time()
            if now - last_announce >= RE_ANNOUNCE_INTERVAL:
                await self._send_announcement()
                last_announce = loop.time()

            # Listen for incoming queries
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(self._sock, 4096),
                    timeout=min(5.0, RE_ANNOUNCE_INTERVAL),
                )
                self._handle_query(data, addr)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            except OSError:
                if self._running:
                    log.debug("mDNS advertiser: socket error", exc_info=True)
                    await asyncio.sleep(1.0)

    def _handle_query(self, data: bytes, addr: tuple) -> None:
        """Check if an incoming query matches our service and schedule a response."""
        questions = _parse_query_questions(data)
        if not questions:
            return

        service_type_lower = SERVICE_TYPE.rstrip(".").lower()
        instance_fqdn_lower = (
            f"{self._instance_name}.{SERVICE_TYPE}".rstrip(".").lower()
        )

        for name, qtype in questions:
            name_lower = name.rstrip(".").lower()
            if qtype == DNS_TYPE_PTR and name_lower == service_type_lower:
                asyncio.create_task(self._send_announcement())
                return
            if name_lower == instance_fqdn_lower:
                asyncio.create_task(self._send_announcement())
                return

    async def _send_announcement(self) -> None:
        """Send a full mDNS announcement with current state."""
        if not self._sock:
            return
        # Re-detect IP each time (handles network interface changes)
        self._local_ip = _get_local_ip()
        records = build_announcement_records(
            instance_name=self._instance_name,
            service_type=SERVICE_TYPE,
            hostname=self._hostname,
            ip=self._local_ip,
            port=self._http_port,
            txt_pairs=self._build_txt_pairs(),
        )
        packet = build_dns_response(records)
        loop = asyncio.get_running_loop()
        try:
            await loop.sock_sendto(self._sock, packet, (MDNS_ADDR, MDNS_PORT))
        except OSError as e:
            log.debug("mDNS: Failed to send announcement: %s", e)

    async def _send_goodbye(self) -> None:
        """Send goodbye packet (TTL=0) to flush caches."""
        if not self._sock:
            return
        records = build_announcement_records(
            instance_name=self._instance_name,
            service_type=SERVICE_TYPE,
            hostname=self._hostname,
            ip=self._local_ip,
            port=self._http_port,
            txt_pairs=self._build_txt_pairs(),
            ttl=0,
        )
        packet = build_dns_response(records)
        loop = asyncio.get_running_loop()
        try:
            await loop.sock_sendto(self._sock, packet, (MDNS_ADDR, MDNS_PORT))
        except OSError as e:
            log.debug("mDNS: Failed to send goodbye: %s", e)

    def _build_txt_pairs(self) -> dict[str, str]:
        """Build TXT record key=value pairs."""
        return {
            "name": self._instance_name,
            "id": self._instance_id,
            "version": self._version,
            "path": "/panel",
        }

    @staticmethod
    def _on_task_done(task: asyncio.Task) -> None:
        """Log unhandled exceptions from the responder task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("mDNS responder task failed: %s", exc, exc_info=exc)
