"""Lightweight SNMP v2c scanner for device discovery.

Custom implementation using raw UDP sockets + ASN.1/BER encoding.
No dependency on pysnmp — uses only stdlib (asyncio, socket, struct).

Queries standard MIB-II OIDs to identify devices:
  - sysDescr    (1.3.6.1.2.1.1.1.0) — Device description
  - sysName     (1.3.6.1.2.1.1.5.0) — Admin-assigned name
  - sysObjectID (1.3.6.1.2.1.1.2.0) — Vendor OID
  - sysContact  (1.3.6.1.2.1.1.4.0) — Contact info
  - sysLocation (1.3.6.1.2.1.1.6.0) — Physical location

References:
  - RFC 1157: SNMP v1
  - RFC 3416: SNMP v2c
  - ITU-T X.690: BER encoding rules
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("discovery.snmp")

# SNMP constants
SNMP_PORT = 161
SNMP_VERSION_2C = 1  # version field value for v2c (0=v1, 1=v2c)

# Standard MIB-II OIDs
OIDS = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysContact": "1.3.6.1.2.1.1.4.0",
    "sysLocation": "1.3.6.1.2.1.1.6.0",
}

# Entity MIB column prefixes (for Standard/Thorough depth — detailed hardware
# info). The instance OID is ``<column>.<entPhysicalIndex>``; the index of the
# top-level entity (chassis) is agent-assigned and NOT necessarily 1, so it's
# located at query time by walking entPhysicalContainedIn (RFC 6933: the
# top-most entity is the row whose entPhysicalContainedIn is 0).
ENTITY_COLUMNS = {
    "entPhysicalMfgName": "1.3.6.1.2.1.47.1.1.1.1.12",
    "entPhysicalModelName": "1.3.6.1.2.1.47.1.1.1.1.13",
    "entPhysicalSerialNum": "1.3.6.1.2.1.47.1.1.1.1.11",
    "entPhysicalHardwareRev": "1.3.6.1.2.1.47.1.1.1.1.8",
    "entPhysicalFirmwareRev": "1.3.6.1.2.1.47.1.1.1.1.9",
}
ENT_PHYSICAL_CONTAINED_IN = "1.3.6.1.2.1.47.1.1.1.1.4"

# Upper bound on entPhysicalContainedIn walk steps. The top-level entity is
# almost always among the first table rows (indexes ascend from the chassis),
# so this only guards against pathological agents with huge entity tables.
ENTITY_WALK_LIMIT = 64

# BER/ASN.1 tag constants
ASN1_INTEGER = 0x02
ASN1_OCTET_STRING = 0x04
ASN1_NULL = 0x05
ASN1_OID = 0x06
ASN1_SEQUENCE = 0x30
# SNMP-specific tags
SNMP_GET_REQUEST = 0xA0
SNMP_GETNEXT_REQUEST = 0xA1
SNMP_GET_RESPONSE = 0xA2


# --- BER Encoding ---


def ber_encode_length(length: int) -> bytes:
    """Encode a length in BER format."""
    if length < 0x80:
        return bytes([length])
    elif length < 0x100:
        return bytes([0x81, length])
    elif length < 0x10000:
        return bytes([0x82, (length >> 8) & 0xFF, length & 0xFF])
    else:
        return bytes([0x83, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])


def ber_encode_integer(value: int, max_bytes: int = 4) -> bytes:
    """Encode an integer as BER INTEGER.

    Args:
        value: Integer to encode.
        max_bytes: Maximum byte length for the encoded value (default 4 for SNMP).
    """
    if value == 0:
        payload = b"\x00"
    elif value > 0:
        payload = value.to_bytes((value.bit_length() + 8) // 8, "big")
    else:
        # Negative integers (not needed for SNMP GET, but complete)
        byte_len = (value.bit_length() + 9) // 8
        payload = (value + (1 << (byte_len * 8))).to_bytes(byte_len, "big")
    if len(payload) > max_bytes:
        raise ValueError(f"Integer too large for BER encoding: {len(payload)} bytes > {max_bytes}")
    return bytes([ASN1_INTEGER]) + ber_encode_length(len(payload)) + payload


def ber_encode_string(value: str) -> bytes:
    """Encode a string as BER OCTET STRING."""
    payload = value.encode("utf-8")
    return bytes([ASN1_OCTET_STRING]) + ber_encode_length(len(payload)) + payload


def ber_encode_null() -> bytes:
    """Encode a BER NULL value."""
    return bytes([ASN1_NULL, 0x00])


def _encode_base128(value: int) -> list[int]:
    """Encode one OID subidentifier in base-128 with continuation bits."""
    if value < 0x80:
        return [value]
    encoded = [value & 0x7F]
    value >>= 7
    while value > 0:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    encoded.reverse()
    return encoded


def ber_encode_oid(oid_str: str) -> bytes:
    """Encode an OID string as BER OBJECT IDENTIFIER.

    Example: '1.3.6.1.2.1.1.1.0' -> encoded bytes
    """
    parts = [int(p) for p in oid_str.split(".")]
    if len(parts) < 2:
        return bytes([ASN1_OID, 0x00])

    # First two components combine into one subidentifier, (40 * first) +
    # second (X.690 8.19.4). Like every subidentifier it is base-128
    # encoded — under arc 2 the combined value can be >= 128.
    payload = _encode_base128(40 * parts[0] + parts[1])

    # Remaining components use base-128 encoding
    for p in parts[2:]:
        payload.extend(_encode_base128(p))

    data = bytes(payload)
    return bytes([ASN1_OID]) + ber_encode_length(len(data)) + data


def ber_encode_sequence(items: list[bytes]) -> bytes:
    """Encode items as a BER SEQUENCE."""
    payload = b"".join(items)
    return bytes([ASN1_SEQUENCE]) + ber_encode_length(len(payload)) + payload


def ber_encode_tagged(tag: int, items: list[bytes]) -> bytes:
    """Encode items with a context-specific tag (for SNMP PDU types)."""
    payload = b"".join(items)
    return bytes([tag]) + ber_encode_length(len(payload)) + payload


# --- BER Decoding ---


def ber_decode_length(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a BER length. Returns (length, new_offset)."""
    if offset >= len(data):
        return 0, offset

    first = data[offset]
    offset += 1

    if first < 0x80:
        return first, offset
    elif first == 0x81:
        if offset >= len(data):
            return 0, offset
        return data[offset], offset + 1
    elif first == 0x82:
        if offset + 1 >= len(data):
            return 0, offset
        return (data[offset] << 8) | data[offset + 1], offset + 2
    elif first == 0x83:
        if offset + 2 >= len(data):
            return 0, offset
        return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2], offset + 3
    return 0, offset


def ber_decode_integer(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a BER INTEGER. Returns (value, new_offset)."""
    if offset >= len(data) or data[offset] != ASN1_INTEGER:
        return 0, offset
    offset += 1
    length, offset = ber_decode_length(data, offset)
    if offset + length > len(data):
        return 0, offset
    value = int.from_bytes(data[offset:offset + length], "big", signed=True)
    return value, offset + length


def ber_decode_string(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a BER OCTET STRING. Returns (string, new_offset)."""
    if offset >= len(data) or data[offset] != ASN1_OCTET_STRING:
        return "", offset
    offset += 1
    length, offset = ber_decode_length(data, offset)
    if offset + length > len(data):
        return "", offset
    value = data[offset:offset + length].decode("utf-8", errors="replace")
    return value, offset + length


def ber_decode_oid(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a BER OID. Returns (oid_string, new_offset)."""
    if offset >= len(data) or data[offset] != ASN1_OID:
        return "", offset
    offset += 1
    length, offset = ber_decode_length(data, offset)
    if length == 0 or offset + length > len(data):
        return "", offset

    oid_bytes = data[offset:offset + length]
    end_offset = offset + length

    # Decode base-128 subidentifiers (the first one may be multi-byte too)
    subids: list[int] = []
    i = 0
    while i < len(oid_bytes):
        value = 0
        while i < len(oid_bytes):
            byte = oid_bytes[i]
            value = (value << 7) | (byte & 0x7F)
            i += 1
            if byte & 0x80 == 0:
                break
        subids.append(value)

    # First subidentifier encodes the first two OID components as
    # (40 * first) + second; only arc 2 allows a second component >= 40
    # (X.690 8.19.4).
    first = subids[0]
    if first < 40:
        parts = [0, first]
    elif first < 80:
        parts = [1, first - 40]
    else:
        parts = [2, first - 80]
    parts.extend(subids[1:])

    return ".".join(str(p) for p in parts), end_offset


def ber_skip_tlv(data: bytes, offset: int) -> int:
    """Skip over a TLV (type-length-value) element. Returns new offset."""
    if offset >= len(data):
        return offset
    offset += 1  # Skip tag
    length, offset = ber_decode_length(data, offset)
    return offset + length


def ber_decode_any_value(data: bytes, offset: int) -> tuple[str, int]:
    """Decode any BER value as a string for display. Returns (string, new_offset)."""
    if offset >= len(data):
        return "", offset

    tag = data[offset]

    if tag == ASN1_OCTET_STRING:
        return ber_decode_string(data, offset)
    elif tag == ASN1_INTEGER:
        val, new_off = ber_decode_integer(data, offset)
        return str(val), new_off
    elif tag == ASN1_OID:
        return ber_decode_oid(data, offset)
    elif tag == ASN1_NULL:
        return "", offset + 2
    else:
        # Unknown type — skip it
        offset += 1
        length, offset = ber_decode_length(data, offset)
        if offset + length <= len(data):
            raw = data[offset:offset + length]
            # Try decoding as UTF-8 string
            try:
                return raw.decode("utf-8", errors="replace"), offset + length
            except (UnicodeDecodeError, LookupError):
                return raw.hex(), offset + length
        return "", offset + length


# --- SNMP Packet Building ---


def _build_snmp_request(
    pdu_type: int, community: str, oid_strs: list[str], request_id: int,
) -> bytes:
    """Build an SNMP v2c request packet with the given PDU type."""
    # Build variable bindings: list of (OID, NULL) pairs
    varbinds = []
    for oid_str in oid_strs:
        varbind = ber_encode_sequence([
            ber_encode_oid(oid_str),
            ber_encode_null(),
        ])
        varbinds.append(varbind)

    varbind_list = ber_encode_sequence(varbinds)

    pdu = ber_encode_tagged(pdu_type, [
        ber_encode_integer(request_id),
        ber_encode_integer(0),   # error-status
        ber_encode_integer(0),   # error-index
        varbind_list,
    ])

    # Build message: SEQUENCE { version, community, PDU }
    message = ber_encode_sequence([
        ber_encode_integer(SNMP_VERSION_2C),
        ber_encode_string(community),
        pdu,
    ])

    return message


def build_snmp_get(community: str, oid_strs: list[str], request_id: int) -> bytes:
    """Build an SNMP v2c GET-REQUEST packet.

    Args:
        community: SNMP community string (e.g., 'public')
        oid_strs: List of OID strings to query
        request_id: Unique request identifier

    Returns:
        Complete SNMP packet bytes.
    """
    return _build_snmp_request(SNMP_GET_REQUEST, community, oid_strs, request_id)


def build_snmp_getnext(community: str, oid_strs: list[str], request_id: int) -> bytes:
    """Build an SNMP v2c GETNEXT-REQUEST packet (one step of a walk)."""
    return _build_snmp_request(SNMP_GETNEXT_REQUEST, community, oid_strs, request_id)


def parse_snmp_response(data: bytes) -> dict[str, str]:
    """Parse an SNMP GET-RESPONSE and extract OID -> value pairs.

    Returns dict of {oid_string: value_string}.
    """
    result: dict[str, str] = {}

    try:
        offset = 0

        # Outer SEQUENCE
        if offset >= len(data) or data[offset] != ASN1_SEQUENCE:
            return result
        offset += 1
        _msg_len, offset = ber_decode_length(data, offset)

        # Version (INTEGER)
        _version, offset = ber_decode_integer(data, offset)

        # Community (OCTET STRING)
        _community, offset = ber_decode_string(data, offset)

        # PDU — should be GetResponse (0xA2)
        if offset >= len(data) or data[offset] != SNMP_GET_RESPONSE:
            return result
        offset += 1
        _pdu_len, offset = ber_decode_length(data, offset)

        # Request ID
        _req_id, offset = ber_decode_integer(data, offset)

        # Error status
        error_status, offset = ber_decode_integer(data, offset)
        if error_status != 0:
            return result

        # Error index
        _error_index, offset = ber_decode_integer(data, offset)

        # VarBindList (SEQUENCE)
        if offset >= len(data) or data[offset] != ASN1_SEQUENCE:
            return result
        offset += 1
        varbind_list_len, offset = ber_decode_length(data, offset)
        varbind_end = offset + varbind_list_len

        # Parse each VarBind (SEQUENCE { OID, value })
        while offset < varbind_end and offset < len(data):
            if data[offset] != ASN1_SEQUENCE:
                break
            offset += 1
            _vb_len, offset = ber_decode_length(data, offset)

            # OID
            oid_str, offset = ber_decode_oid(data, offset)

            # Value (any type)
            value_str, offset = ber_decode_any_value(data, offset)

            if oid_str:
                result[oid_str] = value_str

    except (ValueError, IndexError, KeyError):
        log.debug("Failed to parse SNMP response", exc_info=True)

    return result


def parse_snmp_request_id(data: bytes) -> int | None:
    """Extract the request-id from an SNMP GET-RESPONSE packet.

    Returns None if the packet isn't a parseable GET-RESPONSE. Used to
    match responses to in-flight requests so stale, duplicated, or
    spoofed datagrams can't be attributed to the wrong query.
    """
    try:
        offset = 0
        if offset >= len(data) or data[offset] != ASN1_SEQUENCE:
            return None
        offset += 1
        _msg_len, offset = ber_decode_length(data, offset)
        _version, offset = ber_decode_integer(data, offset)
        _community, offset = ber_decode_string(data, offset)
        if offset >= len(data) or data[offset] != SNMP_GET_RESPONSE:
            return None
        offset += 1
        _pdu_len, offset = ber_decode_length(data, offset)
        if offset >= len(data) or data[offset] != ASN1_INTEGER:
            return None
        request_id, _ = ber_decode_integer(data, offset)
        return request_id
    except (ValueError, IndexError):
        return None


# --- IANA Private Enterprise Number extraction ---
# sysObjectID format: 1.3.6.1.4.1.{PEN}.<rest>. Core does not ship a
# curated PEN→manufacturer table — drivers register PENs they care
# about via the ``snmp_pen:`` hint, which feeds the matcher's
# enrichment lookup. The plain integer extraction is a generic
# capability; the vendor binding is per-driver.


def extract_pen(sys_object_id: str) -> int | None:
    """Return the IANA Private Enterprise Number from a sysObjectID,
    or None if the OID isn't in the standard PEN form."""
    prefix = "1.3.6.1.4.1."
    if not sys_object_id.startswith(prefix):
        return None
    rest = sys_object_id[len(prefix):]
    pen_str = rest.split(".")[0] if rest else ""
    try:
        return int(pen_str)
    except ValueError:
        return None


# --- SNMP Result ---


@dataclass
class SNMPInfo:
    """SNMP information collected from a device."""
    sys_descr: str = ""
    sys_name: str = ""
    sys_object_id: str = ""
    sys_contact: str = ""
    sys_location: str = ""
    # Entity MIB fields (populated when entity_mib=True)
    entity_manufacturer: str = ""
    entity_model: str = ""
    entity_serial: str = ""
    entity_hardware_rev: str = ""
    entity_firmware_rev: str = ""

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {}
        if self.sys_descr:
            d["sysDescr"] = self.sys_descr
        if self.sys_name:
            d["sysName"] = self.sys_name
        if self.sys_object_id:
            d["sysObjectID"] = self.sys_object_id
        if self.sys_contact:
            d["sysContact"] = self.sys_contact
        if self.sys_location:
            d["sysLocation"] = self.sys_location
        if self.entity_manufacturer:
            d["entPhysicalMfgName"] = self.entity_manufacturer
        if self.entity_model:
            d["entPhysicalModelName"] = self.entity_model
        if self.entity_serial:
            d["entPhysicalSerialNum"] = self.entity_serial
        return d

    @property
    def pen(self) -> int | None:
        """Return the IANA Private Enterprise Number from sysObjectID, if any.

        sysObjectID format: ``1.3.6.1.4.1.<PEN>.<rest>``. Returns the
        PEN as an int when present; None otherwise. Used as an
        enrichment soft signal for the matcher; multiple drivers may
        register the same PEN, producing a ``possible`` state with a
        candidate list rather than a deterministic match.
        """
        return extract_pen(self.sys_object_id)

    def to_evidence(self):
        """Emit an enrichment Evidence record, or None if no PEN."""
        if self.pen is None:
            return None
        from openavc.discovery.tier_matcher import evidence_snmp_pen

        return evidence_snmp_pen(self.pen, sysdescr=self.sys_descr or None)

    def to_device_info(self) -> dict[str, Any]:
        """Convert to a dict suitable for merge_device_info().

        Core does not parse vendor strings out of sysDescr — that's
        fuzzy and inherently vendor-specific. Drivers contribute
        manufacturer recognition via ``manufacturer_alias:`` hints,
        and the engine's ``extract_vendor_strings`` finalize step
        lifts strings out of probe responses for the matcher. This
        method just surfaces the device's self-reported fields.
        """
        info: dict[str, Any] = {}

        if self.sys_name:
            info["device_name"] = self.sys_name
        if self.to_dict():
            info["snmp_info"] = self.to_dict()

        # Entity MIB fields are device self-report (entPhysical*) — not
        # vendor knowledge in core. They're authoritative when the
        # device populates them.
        if self.entity_manufacturer:
            info["manufacturer"] = self.entity_manufacturer
        if self.entity_model:
            info["model"] = self.entity_model
        if self.entity_serial:
            info["serial_number"] = self.entity_serial
        if self.entity_firmware_rev:
            info["firmware"] = self.entity_firmware_rev

        return info


# --- SNMP Scanner ---


class _SNMPQueryProtocol(asyncio.DatagramProtocol):
    """One-shot SNMP request/response exchange.

    The datagram endpoint is created with ``remote_addr`` so the socket is
    connected — the OS only delivers datagrams from the queried device's
    IP and port. On top of that, the response future only resolves for a
    datagram whose request-id matches the request; anything else (stale
    duplicates, spoofed datagrams that beat the source check) is dropped
    and the wait continues until the caller's timeout.
    """

    def __init__(
        self,
        packet: bytes,
        expected_request_id: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._packet = packet
        self._expected_request_id = expected_request_id
        self.response: asyncio.Future[bytes] = loop.create_future()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        transport.sendto(self._packet)  # type: ignore[attr-defined]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if self.response.done():
            return
        if parse_snmp_request_id(data) != self._expected_request_id:
            log.debug("Dropping SNMP datagram with unexpected request-id from %s", addr)
            return
        self.response.set_result(data)

    def error_received(self, exc: Exception) -> None:
        # ICMP errors (port unreachable etc.) — fail fast instead of
        # waiting out the timeout.
        if not self.response.done():
            self.response.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None and not self.response.done():
            self.response.set_exception(exc)


class SNMPScanner:
    """SNMP v2c device scanner.

    Queries standard MIB-II OIDs to identify devices. Uses raw UDP sockets
    with custom BER encoding — no external dependencies.
    """

    DEFAULT_COMMUNITY = "public"

    def __init__(self) -> None:
        self._results: dict[str, SNMPInfo] = {}

    @property
    def results(self) -> dict[str, SNMPInfo]:
        return dict(self._results)

    async def query_device(
        self,
        ip: str,
        community: str = DEFAULT_COMMUNITY,
        timeout: float = 2.0,
        entity_mib: bool = False,
    ) -> SNMPInfo | None:
        """Query a single device for SNMP information.

        Args:
            entity_mib: If True, also query ENTITY-MIB OIDs for detailed
                hardware info (model, serial, manufacturer).

        Returns SNMPInfo if the device responded, None otherwise.
        """
        request_id = random.randint(1, 2**31 - 1)
        oid_list = list(OIDS.values())

        packet = build_snmp_get(community, oid_list, request_id)
        response = await self._udp_query(ip, packet, timeout, request_id)
        if not response:
            return None

        values = parse_snmp_response(response)
        if not values:
            return None

        # Map OID strings back to field names
        info = SNMPInfo()
        for name, oid_str in OIDS.items():
            val = values.get(oid_str, "")
            if val:
                if name == "sysDescr":
                    info.sys_descr = val
                elif name == "sysName":
                    info.sys_name = val
                elif name == "sysObjectID":
                    info.sys_object_id = val
                elif name == "sysContact":
                    info.sys_contact = val
                elif name == "sysLocation":
                    info.sys_location = val

        # Only return if we got at least one non-empty field
        if not (info.sys_descr or info.sys_name):
            return None

        # Query Entity MIB for richer hardware info
        if entity_mib:
            await self._query_entity_mib(ip, community, timeout, info)

        return info

    async def _query_entity_mib(
        self, ip: str, community: str, timeout: float, info: SNMPInfo,
    ) -> None:
        """Query Entity MIB OIDs and populate entity fields on info.

        The entPhysicalIndex of the top-level entity is agent-assigned
        (RFC 6933), so it's located by walking entPhysicalContainedIn for
        the row whose value is 0. Falls back to index 1 (the most common
        assignment) when the walk finds nothing.
        """
        index = await self._find_chassis_index(ip, community, timeout)
        if index is None:
            index = 1

        oid_map = {name: f"{prefix}.{index}" for name, prefix in ENTITY_COLUMNS.items()}
        request_id = random.randint(1, 2**31 - 1)
        packet = build_snmp_get(community, list(oid_map.values()), request_id)

        response = await self._udp_query(ip, packet, timeout, request_id)
        if not response:
            return

        values = parse_snmp_response(response)
        if not values:
            return

        for name, oid_str in oid_map.items():
            val = values.get(oid_str, "")
            if val:
                if name == "entPhysicalMfgName":
                    info.entity_manufacturer = val
                elif name == "entPhysicalModelName":
                    info.entity_model = val
                elif name == "entPhysicalSerialNum":
                    info.entity_serial = val
                elif name == "entPhysicalHardwareRev":
                    info.entity_hardware_rev = val
                elif name == "entPhysicalFirmwareRev":
                    info.entity_firmware_rev = val

    async def _find_chassis_index(
        self, ip: str, community: str, timeout: float,
    ) -> int | None:
        """Walk entPhysicalContainedIn to find the top-level entity's index.

        The top-most physical entity (the chassis) is the row whose
        entPhysicalContainedIn is 0. Returns its entPhysicalIndex, or None
        when the agent doesn't expose the column (no Entity MIB, walk left
        the column, or the bounded walk found no top-level row).
        """
        prefix = ENT_PHYSICAL_CONTAINED_IN + "."
        current_oid = ENT_PHYSICAL_CONTAINED_IN

        for _ in range(ENTITY_WALK_LIMIT):
            request_id = random.randint(1, 2**31 - 1)
            packet = build_snmp_getnext(community, [current_oid], request_id)
            response = await self._udp_query(ip, packet, timeout, request_id)
            if not response:
                return None

            values = parse_snmp_response(response)
            if not values:
                return None

            oid_str, value = next(iter(values.items()))
            if not oid_str.startswith(prefix):
                # Walked past the entPhysicalContainedIn column
                return None
            if value == "0":
                try:
                    return int(oid_str[len(prefix):])
                except ValueError:
                    return None
            current_oid = oid_str

        return None

    async def scan_devices(
        self,
        ips: list[str],
        community: str = DEFAULT_COMMUNITY,
        timeout: float = 2.0,
        concurrency: int = 20,
        entity_mib: bool = False,
    ) -> dict[str, SNMPInfo]:
        """Query multiple devices in parallel.

        Args:
            ips: List of IP addresses to query.
            community: SNMP community string.
            timeout: Per-request timeout in seconds (each SNMP exchange
                with a device gets this long to respond).
            concurrency: Max concurrent queries.
            entity_mib: If True, also query ENTITY-MIB for detailed hardware info.

        Returns:
            Dict of {ip: SNMPInfo} for devices that responded.
        """
        self._results.clear()
        sem = asyncio.Semaphore(concurrency)

        async def query_one(ip: str) -> None:
            async with sem:
                result = await self.query_device(ip, community, timeout, entity_mib=entity_mib)
                if result:
                    self._results[ip] = result

        await asyncio.gather(
            *[query_one(ip) for ip in ips],
            return_exceptions=True,
        )

        log.info("SNMP scan: %d/%d devices responded", len(self._results), len(ips))
        return dict(self._results)

    async def _udp_query(
        self,
        ip: str,
        packet: bytes,
        timeout: float,
        expected_request_id: int,
    ) -> bytes | None:
        """Send one SNMP request and wait for the matching response.

        Uses a connected UDP socket (RFC-compliant agents reply from
        port 161) so the OS rejects datagrams from other sources, and
        only accepts a response whose request-id matches. Runs entirely
        on the event loop — no worker threads, so cancellation and
        timeout cleanly close the socket. Returns the raw response
        bytes, or None on timeout/error.
        """
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _SNMPQueryProtocol(packet, expected_request_id, loop),
                remote_addr=(ip, SNMP_PORT),
            )
        except OSError:
            return None

        try:
            return await asyncio.wait_for(protocol.response, timeout=timeout)
        except (asyncio.TimeoutError, OSError):
            return None
        finally:
            transport.close()
