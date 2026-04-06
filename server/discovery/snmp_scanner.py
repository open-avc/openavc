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
import re
import socket
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

# Entity MIB OIDs (for Standard/Thorough depth — more detailed hardware info)
ENTITY_OIDS = {
    "entPhysicalMfgName": "1.3.6.1.2.1.47.1.1.1.1.12.1",
    "entPhysicalModelName": "1.3.6.1.2.1.47.1.1.1.1.13.1",
    "entPhysicalSerialNum": "1.3.6.1.2.1.47.1.1.1.1.11.1",
    "entPhysicalHardwareRev": "1.3.6.1.2.1.47.1.1.1.1.8.1",
    "entPhysicalFirmwareRev": "1.3.6.1.2.1.47.1.1.1.1.9.1",
}

# BER/ASN.1 tag constants
ASN1_INTEGER = 0x02
ASN1_OCTET_STRING = 0x04
ASN1_NULL = 0x05
ASN1_OID = 0x06
ASN1_SEQUENCE = 0x30
# SNMP-specific tags
SNMP_GET_REQUEST = 0xA0
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


def ber_encode_oid(oid_str: str) -> bytes:
    """Encode an OID string as BER OBJECT IDENTIFIER.

    Example: '1.3.6.1.2.1.1.1.0' -> encoded bytes
    """
    parts = [int(p) for p in oid_str.split(".")]
    if len(parts) < 2:
        return bytes([ASN1_OID, 0x00])

    # First two components are encoded as (40 * first) + second
    payload = [40 * parts[0] + parts[1]]

    # Remaining components use base-128 encoding
    for p in parts[2:]:
        if p < 0x80:
            payload.append(p)
        else:
            # Multi-byte encoding
            encoded: list[int] = []
            val = p
            encoded.append(val & 0x7F)
            val >>= 7
            while val > 0:
                encoded.append(0x80 | (val & 0x7F))
                val >>= 7
            encoded.reverse()
            payload.extend(encoded)

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

    # First byte encodes first two OID components
    parts = [oid_bytes[0] // 40, oid_bytes[0] % 40]

    # Decode remaining components (base-128)
    i = 1
    while i < len(oid_bytes):
        value = 0
        while i < len(oid_bytes):
            byte = oid_bytes[i]
            value = (value << 7) | (byte & 0x7F)
            i += 1
            if byte & 0x80 == 0:
                break
        parts.append(value)

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


def build_snmp_get(community: str, oid_strs: list[str], request_id: int) -> bytes:
    """Build an SNMP v2c GET-REQUEST packet.

    Args:
        community: SNMP community string (e.g., 'public')
        oid_strs: List of OID strings to query
        request_id: Unique request identifier

    Returns:
        Complete SNMP packet bytes.
    """
    # Build variable bindings: list of (OID, NULL) pairs
    varbinds = []
    for oid_str in oid_strs:
        varbind = ber_encode_sequence([
            ber_encode_oid(oid_str),
            ber_encode_null(),
        ])
        varbinds.append(varbind)

    varbind_list = ber_encode_sequence(varbinds)

    # Build PDU: GetRequest-PDU
    pdu = ber_encode_tagged(SNMP_GET_REQUEST, [
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


# --- PEN (Private Enterprise Number) Lookup ---
# Maps IANA PEN to manufacturer name. sysObjectID format: 1.3.6.1.4.1.{PEN}.x.y.z
# Only includes AV-relevant and common enterprise vendors.

_PEN_TABLE: dict[int, str] = {
    9: "Cisco",
    11: "HP",
    43: "3Com",
    171: "D-Link",
    207: "Allied Telesis",
    236: "Samsung",
    311: "Microsoft",
    318: "APC/Schneider",
    674: "Dell",
    2435: "Sony",
    2636: "Juniper",
    3076: "Altiga/Cisco",
    3375: "F5 Networks",
    3872: "QSC",
    4413: "Ubiquiti",
    4981: "Panasonic",
    5765: "Watchguard",
    6486: "Alcatel-Lucent",
    6981: "Biamp",
    8072: "Net-SNMP",
    9148: "AudioCodes",
    10002: "Ubiquiti",
    11898: "Ruckus",
    14988: "MikroTik",
    17049: "Extron",
    21317: "Crestron",
    24717: "Atlona",
    25053: "Ruckus Wireless",
    25506: "H3C/HPE",
    32812: "Shure",
    35265: "Ericsson-LG",
    38581: "Barco",
    41916: "AMX/Harman",
    47196: "Aruba",
    52040: "Christie",
}


def lookup_pen(sys_object_id: str) -> str | None:
    """Extract the PEN from a sysObjectID and look up the manufacturer.

    sysObjectID is typically: 1.3.6.1.4.1.{PEN}.{rest}
    Returns manufacturer name or None.
    """
    prefix = "1.3.6.1.4.1."
    if not sys_object_id.startswith(prefix):
        return None
    rest = sys_object_id[len(prefix):]
    pen_str = rest.split(".")[0] if rest else ""
    try:
        pen = int(pen_str)
    except ValueError:
        return None
    return _PEN_TABLE.get(pen)


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

    def to_device_info(self) -> dict[str, Any]:
        """Convert to a dict suitable for merge_device_info()."""
        info: dict[str, Any] = {}

        if self.sys_name:
            info["device_name"] = self.sys_name
        if self.sys_descr:
            info["snmp_info"] = self.to_dict()
            # Try to extract manufacturer, model, firmware from sysDescr
            parsed = parse_sys_descr(self.sys_descr)
            if parsed.get("manufacturer"):
                info["manufacturer"] = parsed["manufacturer"]
            if parsed.get("model"):
                info["model"] = parsed["model"]
            if parsed.get("firmware"):
                info["firmware"] = parsed["firmware"]
            if parsed.get("category"):
                info["category"] = parsed["category"]
        elif self.to_dict():
            info["snmp_info"] = self.to_dict()

        # PEN lookup from sysObjectID (fallback if sysDescr didn't identify manufacturer)
        if not info.get("manufacturer") and self.sys_object_id:
            pen_mfg = lookup_pen(self.sys_object_id)
            if pen_mfg:
                info["manufacturer"] = pen_mfg

        # Entity MIB enrichment (overrides sysDescr-based values if present)
        if self.entity_manufacturer:
            info["manufacturer"] = self.entity_manufacturer
        if self.entity_model:
            info["model"] = self.entity_model
        if self.entity_serial:
            info["serial_number"] = self.entity_serial
        if self.entity_firmware_rev:
            info["firmware"] = self.entity_firmware_rev

        return info


# --- sysDescr Parsing ---

# Known patterns in sysDescr strings from AV equipment
_DESCR_PATTERNS: list[tuple[re.Pattern, dict[str, str]]] = [
    # NEC projectors: "NEC PA1004UL Projector, Firmware V1.03"
    (re.compile(r"(NEC)\s+(\S+).*?Projector.*?(?:Firmware\s+)?(\S+)?", re.I),
     {"manufacturer": "NEC", "category": "projector"}),
    # Epson projectors
    (re.compile(r"(Epson)\s+(\S+).*?Projector", re.I),
     {"manufacturer": "Epson", "category": "projector"}),
    # Extron: "Extron DTP CrossPoint 84 IPCP, V1.07.0000"
    (re.compile(r"(Extron)\s+(.+?),\s*(V\S+)", re.I),
     {"manufacturer": "Extron", "category": "switcher"}),
    # QSC: "QSC Q-SYS Core 110f, V9.5.0"
    (re.compile(r"(QSC)\s+(.+?),\s*(V\S+)", re.I),
     {"manufacturer": "QSC", "category": "audio"}),
    # Biamp: "Biamp Tesira SERVER-IO, Firmware 4.14"
    (re.compile(r"(Biamp)\s+(.+?),\s*(?:Firmware\s*)?(\S+)?", re.I),
     {"manufacturer": "Biamp", "category": "audio"}),
    # Shure: "Shure MXA910, V4.5.6"
    (re.compile(r"(Shure)\s+(\S+).*?(?:V(\S+))?", re.I),
     {"manufacturer": "Shure", "category": "audio"}),
    # Crestron: "Crestron DM-MD8X8, Version 1.500"
    (re.compile(r"(Crestron)\s+(.+?)(?:,\s*(?:Version\s*)?(\S+))?$", re.I),
     {"manufacturer": "Crestron", "category": "control"}),
    # Samsung displays
    (re.compile(r"(Samsung)\s+(.+?)(?:,\s*(\S+))?$", re.I),
     {"manufacturer": "Samsung", "category": "display"}),
    # LG displays
    (re.compile(r"(LG)\s+(.+?)(?:,\s*(\S+))?$", re.I),
     {"manufacturer": "LG", "category": "display"}),
    # Sony
    (re.compile(r"(Sony)\s+(.+?)(?:,\s*(\S+))?$", re.I),
     {"manufacturer": "Sony"}),
    # Panasonic
    (re.compile(r"(Panasonic)\s+(.+?)(?:,\s*(\S+))?$", re.I),
     {"manufacturer": "Panasonic"}),
]


def parse_sys_descr(descr: str) -> dict[str, str]:
    """Parse a sysDescr string to extract manufacturer, model, firmware, category.

    Returns dict with keys: manufacturer, model, firmware, category (any may be absent).
    """
    result: dict[str, str] = {}

    for pattern, defaults in _DESCR_PATTERNS:
        m = pattern.match(descr.strip())
        if m:
            result.update(defaults)
            groups = m.groups()
            if len(groups) >= 2 and groups[1]:
                result["model"] = groups[1].strip()
            if len(groups) >= 3 and groups[2]:
                result["firmware"] = groups[2].strip()
            return result

    # Fallback: try to extract any recognizable manufacturer name
    descr_lower = descr.lower()
    for mfg in ["extron", "crestron", "amx", "biamp", "qsc", "shure", "nec",
                 "epson", "samsung", "lg", "sony", "panasonic", "barco", "christie"]:
        if mfg in descr_lower:
            result["manufacturer"] = mfg.capitalize()
            if mfg in ("extron", "crestron", "amx"):
                result["category"] = "control" if mfg == "amx" else ("switcher" if mfg == "extron" else "control")
            break

    return result


# --- SNMP Scanner ---


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

        try:
            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(
                self._udp_query(ip, packet, loop),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, OSError):
            return None

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
        """Query Entity MIB OIDs and populate entity fields on info."""
        request_id = random.randint(1, 2**31 - 1)
        oid_list = list(ENTITY_OIDS.values())
        packet = build_snmp_get(community, oid_list, request_id)

        try:
            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(
                self._udp_query(ip, packet, loop),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, OSError):
            return

        if not response:
            return

        values = parse_snmp_response(response)
        if not values:
            return

        for name, oid_str in ENTITY_OIDS.items():
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
            timeout: Per-device timeout in seconds.
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
        loop: asyncio.AbstractEventLoop,
    ) -> bytes | None:
        """Send a UDP packet and receive the response."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)

        try:
            await loop.run_in_executor(
                None, lambda: sock.sendto(packet, (ip, SNMP_PORT))
            )
            data = await loop.run_in_executor(
                None, lambda: sock.recv(4096)
            )
            return data
        except (socket.timeout, OSError):
            return None
        finally:
            sock.close()
