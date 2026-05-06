"""Lightweight mDNS/DNS-SD scanner for device discovery.

Custom implementation using raw UDP multicast sockets + DNS wire format.
No dependency on zeroconf (LGPL). Only uses stdlib: asyncio, socket, struct.

References:
  - RFC 6762: Multicast DNS
  - RFC 6763: DNS-Based Service Discovery
  - RFC 1035: DNS wire format
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("discovery.mdns")

# mDNS constants
MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353

# DNS record types
DNS_TYPE_A = 1       # IPv4 address
DNS_TYPE_PTR = 12    # Domain name pointer (service discovery)
DNS_TYPE_TXT = 16    # Text records (key=value metadata)
DNS_TYPE_SRV = 33    # Service location (host + port)
DNS_TYPE_AAAA = 28   # IPv6 address (parsed but not used for discovery)

DNS_CLASS_IN = 1

# AV-relevant mDNS service types to query.
#
# This list was overhauled in the discovery redesign. Three former entries
# were removed because the service types do not exist in the wild:
#   - _amx-beacon._udp.local. - AMX uses DDP multicast on
#     239.255.250.250:9131 instead. See amx_ddp_scanner.py.
#   - _crestron._tcp.local. - Crestron primary discovery is the CIP UDP
#     41794 probe. AirMedia receivers advertise as _airplay._tcp.
#   - _lutron._tcp.local. - actual Lutron service type is _leap._tcp.
# Three more were unverified hard-coded guesses removed in favor of
# enumeration via _services._dns-sd._udp.local.:
#   - _qsc._tcp.local., _shure._tcp.local., _tesira._tcp.local.
# When a Q-SYS Core, Shure mixer, or Tesira processor advertises *any*
# service that contains "qsc"/"shure"/"tesira" or carries a matching
# manufacturer TXT record, the new TierMatcher will identify it via
# the catch-all enumeration plus a TXT filter declared by the driver.
# DNS-SD meta-query that enumerates every service type advertised on
# the network. Always included regardless of which drivers are loaded
# so unknown service types surface to the user for catalog growth.
DNS_SD_META_QUERY = "_services._dns-sd._udp.local."

# Generic web UIs and consumer endpoints that have no specific driver
# but enrich already-identified devices. The engine includes these in
# the browse list as a baseline alongside whatever the loaded drivers
# declare in their ``mdns_services:`` blocks.
BASELINE_SERVICE_TYPES = (
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_airplay._tcp.local.",
    "_googlecast._tcp.local.",
    "_raop._tcp.local.",
    "_roku._tcp.local.",
)


# --- DNS Wire Format ---


def encode_dns_name(name: str) -> bytes:
    """Encode a DNS name as wire format labels.

    Example: 'example.local.' -> b'\\x07example\\x05local\\x00'
    """
    result = b""
    # Strip trailing dot if present
    if name.endswith("."):
        name = name[:-1]
    for label in name.split("."):
        encoded = label.encode("utf-8")
        result += struct.pack("B", len(encoded)) + encoded
    result += b"\x00"  # Root label
    return result


def decode_dns_name(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a DNS wire format name, handling compression pointers.

    Returns (name_string, new_offset) where new_offset points past the name
    in the original data (after the compression pointer if one was used).
    """
    labels: list[str] = []
    # return_offset tracks where to resume in the original data stream.
    # It's set the first time we encounter a compression pointer.
    return_offset: int | None = None
    max_jumps = 20  # Prevent infinite loops from malformed packets
    visited: set[int] = set()  # Track visited pointer offsets to detect cycles

    for _ in range(max_jumps):
        if offset >= len(data):
            break

        length = data[offset]

        if length == 0:
            # End of name
            if return_offset is None:
                return_offset = offset + 1
            break
        elif (length & 0xC0) == 0xC0:
            # Compression pointer (2 bytes)
            if offset + 1 >= len(data):
                break
            if return_offset is None:
                # Save where to continue reading after this name
                return_offset = offset + 2
            pointer = struct.unpack("!H", data[offset:offset + 2])[0] & 0x3FFF
            if pointer in visited:
                break  # Cycle detected — return what we have
            visited.add(pointer)
            offset = pointer
        else:
            # Normal label
            offset += 1
            if offset + length > len(data):
                break
            label = data[offset:offset + length].decode("utf-8", errors="replace")
            labels.append(label)
            offset += length

    name = ".".join(labels)
    return name, return_offset if return_offset is not None else offset


def build_dns_query(name: str, qtype: int = DNS_TYPE_PTR) -> bytes:
    """Build a DNS query packet for a service type.

    Args:
        name: The DNS name to query (e.g., '_http._tcp.local.')
        qtype: Query type (default: PTR for service discovery)

    Returns:
        Complete DNS packet bytes ready to send.
    """
    # Header: ID=0 (mDNS), flags=0 (standard query), 1 question, 0 answers
    header = struct.pack("!HHHHHH",
                         0,      # Transaction ID (0 for mDNS)
                         0,      # Flags: standard query
                         1,      # Questions count
                         0,      # Answer count
                         0,      # Authority count
                         0)      # Additional count

    # Question section
    qname = encode_dns_name(name)
    question = qname + struct.pack("!HH", qtype, DNS_CLASS_IN)

    return header + question


@dataclass
class DNSRecord:
    """A parsed DNS resource record."""
    name: str
    rtype: int
    rclass: int
    ttl: int
    rdata: bytes
    # Parsed fields (set based on rtype)
    ip: str | None = None          # A record
    target: str | None = None      # PTR or SRV target
    port: int | None = None        # SRV port
    priority: int | None = None    # SRV priority
    weight: int | None = None      # SRV weight
    txt: dict[str, str] = field(default_factory=dict)  # TXT key=value pairs


def parse_dns_packet(data: bytes) -> tuple[list[DNSRecord], list[DNSRecord]]:
    """Parse a DNS response packet.

    Returns (questions_skipped, resource_records).
    Resource records include answers, authority, and additional sections.
    """
    if len(data) < 12:
        return [], []

    # Parse header
    (tx_id, flags, qdcount, ancount, nscount, arcount) = struct.unpack(
        "!HHHHHH", data[:12]
    )

    offset = 12
    records: list[DNSRecord] = []

    # Skip question section
    for _ in range(qdcount):
        if offset >= len(data):
            break
        _name, offset = decode_dns_name(data, offset)
        offset += 4  # Skip QTYPE + QCLASS

    # Parse all resource record sections (answer + authority + additional)
    total_rr = ancount + nscount + arcount
    for _ in range(total_rr):
        if offset >= len(data):
            break

        name, offset = decode_dns_name(data, offset)

        if offset + 10 > len(data):
            break

        rtype, rclass, ttl, rdlength = struct.unpack(
            "!HHIH", data[offset:offset + 10]
        )
        offset += 10

        if offset + rdlength > len(data):
            break

        rdata = data[offset:offset + rdlength]
        record = DNSRecord(
            name=name, rtype=rtype, rclass=rclass & 0x7FFF,
            ttl=ttl, rdata=rdata,
        )

        # Parse rdata based on type
        if rtype == DNS_TYPE_A and rdlength == 4:
            record.ip = socket.inet_ntoa(rdata)
        elif rtype == DNS_TYPE_PTR:
            record.target, _ = decode_dns_name(data, offset)
        elif rtype == DNS_TYPE_SRV and rdlength >= 6:
            record.priority, record.weight, record.port = struct.unpack(
                "!HHH", rdata[:6]
            )
            record.target, _ = decode_dns_name(data, offset + 6)
        elif rtype == DNS_TYPE_TXT:
            record.txt = _parse_txt_rdata(rdata)

        records.append(record)
        offset += rdlength

    return [], records


def _parse_txt_rdata(rdata: bytes) -> dict[str, str]:
    """Parse TXT record rdata into key=value pairs.

    TXT records are a series of length-prefixed strings.
    Each string is typically 'key=value' format.
    """
    result: dict[str, str] = {}
    pos = 0
    while pos < len(rdata):
        length = rdata[pos]
        pos += 1
        if pos + length > len(rdata):
            break
        text = rdata[pos:pos + length].decode("utf-8", errors="replace")
        pos += length
        if "=" in text:
            key, _, value = text.partition("=")
            result[key] = value
        elif text:
            result[text] = ""
    return result


# --- mDNS Result ---


@dataclass
class MDNSResult:
    """A device discovered via mDNS/DNS-SD."""
    ip: str
    hostname: str | None = None
    port: int | None = None
    service_type: str | None = None       # e.g., "_http._tcp.local."
    instance_name: str | None = None      # e.g., "NEC PA1004UL"
    txt_records: dict[str, str] = field(default_factory=dict)

    def to_device_info(self) -> dict[str, Any]:
        """Convert to a dict suitable for merge_device_info()."""
        info: dict[str, Any] = {}
        if self.hostname:
            info["hostname"] = self.hostname

        # Extract manufacturer/model from instance name or TXT records
        if self.instance_name:
            info["device_name"] = self.instance_name

        # Common TXT record keys used by AV devices
        txt = self.txt_records
        if "manufacturer" in txt:
            info["manufacturer"] = txt["manufacturer"]
        elif "mf" in txt:
            info["manufacturer"] = txt["mf"]
        if "model" in txt:
            info["model"] = txt["model"]
        elif "md" in txt:
            info["model"] = txt["md"]
        if "firmware" in txt:
            info["firmware"] = txt["firmware"]
        elif "fw" in txt:
            info["firmware"] = txt["fw"]
        if "serialNumber" in txt:
            info["serial_number"] = txt["serialNumber"]
        elif "sn" in txt:
            info["serial_number"] = txt["sn"]

        # Map service type to protocol
        protocols = []
        if self.service_type:
            info["mdns_services"] = [self.service_type]
            proto = _service_type_to_protocol(self.service_type)
            if proto:
                protocols.append(proto)
        if protocols:
            info["protocols"] = protocols

        # Map service type to category
        category = _service_type_to_category(self.service_type)
        if category:
            info["category"] = category

        # Include port in open_ports if set
        if self.port and self.port not in (80, 443):
            info["open_ports"] = [self.port]

        return info

    def to_evidence(self):
        """Emit a Tier 1 Evidence record for the deterministic matcher.

        Returns ``None`` if this MDNSResult does not carry a service type
        (e.g. an A-record-only resolution). The caller should drop those.

        Imports happen locally to avoid a circular import: ``tier_matcher``
        imports from ``result``, and ``result`` is imported by everything.
        """
        if not self.service_type:
            return None
        from server.discovery.tier_matcher import evidence_mdns

        return evidence_mdns(
            service_type=self.service_type,
            txt=self.txt_records or None,
            instance_name=self.instance_name,
        )


def _service_type_to_protocol(service_type: str | None) -> str | None:
    """Map mDNS service type to OpenAVC protocol name.

    Only entries with documented vendor-specific service types are
    included. Generic types (`_http._tcp`) and unverified guesses
    (`_qsc._tcp`, `_shure._tcp`) are not mapped here — driver matching
    via TXT records (Phase 6) handles those.
    """
    if not service_type:
        return None
    # Normalize: ensure trailing dot for lookup
    key = service_type if service_type.endswith(".") else service_type + "."
    mapping = {
        "_pjlink._tcp.local.": "pjlink",
        "_ndi._tcp.local.": "ndi",
        "_leap._tcp.local.": "lutron_leap",
        # Dante - any of the Audinate _netaudio-* services indicates Dante.
        "_netaudio-cmc._udp.local.": "dante",
        "_netaudio-arc._udp.local.": "dante",
        "_netaudio-chan._udp.local.": "dante",
        "_netaudio-dbc._udp.local.": "dante",
        "_workgroup._udp.local.": "dante",
        # NMOS / IPMX
        "_nmos-node._tcp.local.": "nmos",
        "_nmos-register._tcp.local.": "nmos",
        "_nmos-query._tcp.local.": "nmos",
        "_nmos-registration._tcp.local.": "nmos",
        # Sennheiser SSC
        "_ssc._udp.local.": "sennheiser_ssc",
        "_ssc._tcp.local.": "sennheiser_ssc",
        # Roku ECP
        "_roku._tcp.local.": "roku_ecp",
    }
    return mapping.get(key)


def _service_type_to_category(service_type: str | None) -> str | None:
    """Map mDNS service type to device category."""
    if not service_type:
        return None
    # Normalize: ensure trailing dot for lookup
    key = service_type if service_type.endswith(".") else service_type + "."
    mapping = {
        "_pjlink._tcp.local.": "projector",
        "_ndi._tcp.local.": "video",
        "_leap._tcp.local.": "control",
        "_netaudio-cmc._udp.local.": "audio",
        "_netaudio-arc._udp.local.": "audio",
        "_netaudio-chan._udp.local.": "audio",
        "_netaudio-dbc._udp.local.": "audio",
        "_workgroup._udp.local.": "audio",
        "_ssc._udp.local.": "audio",
        "_ssc._tcp.local.": "audio",
        "_airplay._tcp.local.": "display",
        "_googlecast._tcp.local.": "display",
        "_raop._tcp.local.": "audio",
        "_roku._tcp.local.": "display",
    }
    return mapping.get(key)


# --- mDNS Scanner ---


class MDNSScanner:
    """Lightweight mDNS/DNS-SD listener for device discovery.

    Listens for multicast DNS advertisements and sends targeted queries
    for AV-relevant service types. Uses only stdlib (socket, struct, asyncio).
    """

    def __init__(
        self,
        control_ip: str = "",
        service_types: list[str] | None = None,
    ) -> None:
        """``control_ip``: bind multicast group join to this interface IP.
        Empty string means INADDR_ANY (default route, all interfaces).
        Required for the multi-NIC AV scenario where the control VLAN
        is not the default route.

        ``service_types``: the list of mDNS service types to PTR-query.
        The engine populates this from the union of every loaded
        driver's ``mdns_services:`` block plus a small baseline of
        consumer-AV types. ``None`` falls back to baseline + DNS-SD
        meta-query, matching the pre-Phase-9 behavior for callers
        that haven't been threaded through.
        """
        self._sock: socket.socket | None = None
        self._running = False
        self._results: dict[str, MDNSResult] = {}  # keyed by IP
        # Hostname -> IP resolution (from A records)
        self._hostname_to_ip: dict[str, str] = {}
        # Instance -> partial data (before we have IP)
        self._pending: dict[str, dict[str, Any]] = {}
        # Service types observed via _services._dns-sd._udp.local.
        # enumeration that no loaded driver claims. Surfaced for
        # catalog-growth telemetry and the unknown-state UI.
        self._unknown_service_types: set[str] = set()
        self._control_ip = control_ip
        if service_types is None:
            self._service_types: list[str] = list(BASELINE_SERVICE_TYPES) + [DNS_SD_META_QUERY]
        else:
            # Always include the DNS-SD meta-query so unknown types
            # surface for catalog growth even when drivers aren't yet
            # declaring much.
            seen: set[str] = set()
            ordered: list[str] = []
            for st in list(service_types) + [DNS_SD_META_QUERY]:
                norm = st.strip()
                if not norm:
                    continue
                if not norm.endswith("."):
                    norm = norm + "."
                key = norm.lower()
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(norm)
            self._service_types = ordered
        self._known_service_types_lower: frozenset[str] = frozenset(
            s.lower() for s in self._service_types
        )

    @property
    def results(self) -> dict[str, MDNSResult]:
        return dict(self._results)

    async def start(self, duration: float = 10.0) -> dict[str, MDNSResult]:
        """Run mDNS discovery for the specified duration.

        1. Creates UDP multicast socket on 224.0.0.251:5353
        2. Sends PTR queries for each AV service type
        3. Listens for responses, parsing DNS records
        4. Returns discovered devices keyed by IP

        Args:
            duration: How long to listen in seconds.

        Returns:
            Dict of IP -> MDNSResult for discovered devices.
        """
        self._results.clear()
        self._hostname_to_ip.clear()
        self._pending.clear()
        self._running = True

        try:
            self._sock = _create_mdns_socket(control_ip=self._control_ip)
        except OSError as e:
            log.warning("Could not create mDNS socket: %s", e)
            return {}

        try:
            # Send queries for all AV service types
            await self._send_queries()

            # Listen for responses
            await self._listen(duration)
        except asyncio.CancelledError:
            log.debug("mDNS scan cancelled")
        except Exception:
            log.warning("mDNS scan error", exc_info=True)
        finally:
            self._running = False
            self._close_socket()

        log.info("mDNS scan found %d devices", len(self._results))
        return dict(self._results)

    async def stop(self) -> None:
        """Stop the mDNS listener."""
        self._running = False
        self._close_socket()

    async def _send_queries(self) -> None:
        """Send PTR queries for every configured service type."""
        if not self._sock:
            return

        loop = asyncio.get_event_loop()
        for service_type in self._service_types:
            try:
                packet = build_dns_query(service_type, DNS_TYPE_PTR)
                await loop.run_in_executor(
                    None,
                    lambda p=packet: self._sock.sendto(p, (MDNS_ADDR, MDNS_PORT))
                )
            except OSError as e:
                log.debug("Failed to send mDNS query for %s: %s", service_type, e)

            # Small delay between queries to avoid flooding
            await asyncio.sleep(0.05)

    async def _listen(self, duration: float) -> None:
        """Listen for mDNS responses for the specified duration."""
        if not self._sock:
            return

        loop = asyncio.get_event_loop()
        end_time = loop.time() + duration

        while self._running and loop.time() < end_time:
            remaining = end_time - loop.time()
            if remaining <= 0:
                break

            try:
                # Use a short timeout so we can check _running flag
                self._sock.settimeout(min(remaining, 0.5))
                data, addr = await loop.run_in_executor(
                    None, lambda: self._sock.recvfrom(4096)
                )
                self._process_response(data, addr[0])
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    log.debug("mDNS socket error during listen", exc_info=True)
                break

    def _process_response(self, data: bytes, sender_ip: str) -> None:
        """Process a received mDNS response packet."""
        try:
            _, records = parse_dns_packet(data)
        except Exception:
            log.debug("Failed to parse mDNS packet from %s", sender_ip)
            return

        if not records:
            return

        # First pass: collect A records for hostname resolution
        for rec in records:
            if rec.rtype == DNS_TYPE_A and rec.ip:
                self._hostname_to_ip[rec.name.lower()] = rec.ip

        # Second pass: process service records
        touched: set[str] = set()  # Keys modified by this packet
        for rec in records:
            if rec.rtype == DNS_TYPE_PTR and rec.target:
                # PTR: service_type -> instance_name
                service_type = rec.name
                instance_name = rec.target

                # Catch-all enumeration: responses to
                # _services._dns-sd._udp.local. carry advertised service
                # types in the PTR target. Capture unknowns for catalog
                # growth, but don't create a pending entry for them.
                if service_type.lower().rstrip(".").endswith(
                    "_services._dns-sd._udp.local"
                ):
                    self._track_unknown_service_type(instance_name)
                    continue

                # Extract human-readable name (everything before the service type)
                readable = _extract_instance_name(instance_name, service_type)

                key = instance_name.lower()
                touched.add(key)
                if key not in self._pending:
                    self._pending[key] = {
                        "service_type": service_type,
                        "instance_name": readable,
                    }
                else:
                    self._pending[key]["service_type"] = service_type
                    if readable:
                        self._pending[key]["instance_name"] = readable

            elif rec.rtype == DNS_TYPE_SRV and rec.target:
                # SRV: instance_name -> hostname + port
                key = rec.name.lower()
                touched.add(key)
                if key not in self._pending:
                    self._pending[key] = {}
                self._pending[key]["hostname"] = rec.target
                self._pending[key]["port"] = rec.port

                # Try to resolve hostname to IP
                hostname_lower = rec.target.lower().rstrip(".")
                if hostname_lower in self._hostname_to_ip:
                    self._pending[key]["ip"] = self._hostname_to_ip[hostname_lower]
                # Also check with trailing dot
                if rec.target.lower() in self._hostname_to_ip:
                    self._pending[key]["ip"] = self._hostname_to_ip[rec.target.lower()]

            elif rec.rtype == DNS_TYPE_TXT and rec.txt:
                key = rec.name.lower()
                touched.add(key)
                if key not in self._pending:
                    self._pending[key] = {}
                self._pending[key].setdefault("txt_records", {}).update(rec.txt)

            elif rec.rtype == DNS_TYPE_A and rec.ip:
                # A records already processed above, but also check
                # if any pending entries reference this hostname
                hostname_lower = rec.name.lower().rstrip(".")
                for key, pending in self._pending.items():
                    target = pending.get("hostname", "")
                    if isinstance(target, str):
                        target_lower = target.lower().rstrip(".")
                        if target_lower == hostname_lower:
                            pending["ip"] = rec.ip

        # Resolve pending entries into results where we have IPs.
        # Entries from this packet that can't resolve via hostname
        # fall back to the sender's IP (in mDNS, devices respond
        # about themselves from their own address).
        self._resolve_pending(sender_ip, touched)

    def _resolve_pending(
        self, sender_ip: str, touched: set[str] | None = None,
    ) -> None:
        """Try to resolve pending entries into MDNSResult objects."""
        resolved_keys: list[str] = []

        for key, pending in self._pending.items():
            ip = pending.get("ip")
            if not ip:
                # If we have a hostname, try resolving it
                hostname = pending.get("hostname", "")
                if isinstance(hostname, str):
                    hostname_lower = hostname.lower().rstrip(".")
                    ip = self._hostname_to_ip.get(hostname_lower)
                if not ip and touched and key in touched:
                    # In mDNS, devices respond about themselves from their
                    # own IP. Use the sender's address for entries that
                    # were part of this packet but couldn't be resolved.
                    ip = sender_ip
                if not ip:
                    continue

            if not ip:
                continue

            # Create or update result
            if ip not in self._results:
                self._results[ip] = MDNSResult(ip=ip)

            result = self._results[ip]
            if pending.get("hostname"):
                hostname_str = pending["hostname"]
                if isinstance(hostname_str, str):
                    # Strip .local. suffix for cleaner display
                    clean = hostname_str.rstrip(".")
                    if clean.endswith(".local"):
                        clean = clean[:-6]
                    result.hostname = clean
            if pending.get("port"):
                result.port = pending["port"]
            if pending.get("service_type"):
                result.service_type = pending["service_type"]
            if pending.get("instance_name"):
                result.instance_name = pending["instance_name"]
            if pending.get("txt_records"):
                result.txt_records.update(pending["txt_records"])

            resolved_keys.append(key)

        # Remove resolved entries
        for key in resolved_keys:
            del self._pending[key]

    def _close_socket(self) -> None:
        """Safely close the multicast socket."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _track_unknown_service_type(self, service_type: str) -> None:
        """Record a service type from `_services._dns-sd._udp.` enumeration.

        Filters out hardcoded types we already query so the unknown set
        only contains genuinely new advertisements that may be worth
        adding to the driver catalog.
        """
        normalized = service_type.lower().rstrip(".") + "."
        if normalized in self._known_service_types_lower:
            return
        if normalized in self._unknown_service_types:
            return
        self._unknown_service_types.add(normalized)
        log.debug("mDNS enumeration discovered unknown service type: %s", normalized)

    @property
    def unknown_service_types(self) -> set[str]:
        """Service types observed via DNS-SD enumeration that we don't query.

        Surfaced for catalog-growth telemetry and the unknown-state UI.
        Caller receives a copy.
        """
        return set(self._unknown_service_types)


def _create_mdns_socket(control_ip: str = "") -> socket.socket:
    """Create a UDP socket configured for mDNS multicast reception.

    Cross-platform: works on both Windows and Linux. When ``control_ip``
    is set, the multicast group join binds to that interface only,
    so on a multi-homed host (corporate / AV / control VLANs all on one
    machine) we only receive announcements from the chosen network.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    # Allow multiple processes to bind to the same port
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock.bind(("", MDNS_PORT))

    # Join the mDNS multicast group on the chosen interface (or all interfaces).
    iface = control_ip or "0.0.0.0"
    mreq = struct.pack(
        "4s4s",
        socket.inet_aton(MDNS_ADDR),
        socket.inet_aton(iface),
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    # When a control interface is selected, also pin outbound multicast
    # so our PTR queries leave through that adapter.
    if control_ip:
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_IF,
            socket.inet_aton(control_ip),
        )

    # Set TTL for multicast packets (mDNS uses TTL=255)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)

    # Non-blocking for asyncio compatibility
    sock.setblocking(False)

    return sock


def _extract_instance_name(full_name: str, service_type: str) -> str | None:
    """Extract the human-readable instance name from a PTR target.

    Example:
        full_name='NEC PA1004UL._pjlink._tcp.local.'
        service_type='_pjlink._tcp.local.'
        returns='NEC PA1004UL'
    """
    # Normalize: strip trailing dots
    full = full_name.rstrip(".")
    stype = service_type.rstrip(".")

    if full.lower().endswith("." + stype.lower()):
        prefix = full[:-(len(stype) + 1)]
        return prefix if prefix else None
    return full if full != stype else None
