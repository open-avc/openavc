"""SNMP v2c wire codec — ASN.1/BER encoding and SNMP message build/parse.

Pure stdlib, no imports from the rest of the platform: this is the leaf both
the discovery scanner (`discovery/snmp_scanner.py`, read-only GET/GETNEXT) and
the control transport (`transport/snmp.py`, which adds SET) share, so there is
one BER implementation and one place a framing bug can live. Same shape as
`osc_codec.py` next door — the codec knows bytes, the transport knows sockets.

pysnmp is not used: it is not MIT-compatible for our purposes and this is a
few hundred lines of stdlib.

Two parsing surfaces, deliberately:

  - `parse_snmp_response` returns ``{oid: display string}`` and swallows an
    error-status. That is what a discovery sweep wants — a device that refuses
    one OID is still a device, and every value ends up in a UI string anyway.
  - `parse_snmp_message` returns a typed `SnmpMessage`: the error-status, the
    error-index, and varbinds carrying the SNMP type alongside the Python
    value. A control driver needs both — an integer 1 is not the string "1"
    when it is about to be written back, and a refused SET must reach the user
    as the agent's own reason rather than as silence.

References:
  - RFC 1157 (SNMP v1), RFC 3416 (SNMP v2c PDUs), ITU-T X.690 (BER)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("transport.snmp_codec")

SNMP_PORT = 161
SNMP_VERSION_2C = 1  # version field value for v2c (0=v1, 1=v2c)

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
    """Decode any BER value as a string for display. Returns (string, new_offset).

    Display-only — the type is dropped on the floor. `decode_typed_value` is
    the typed path, and this defers to it so there is one decoder: a discovery
    sweep and a driver poll must never disagree about what a byte string says.
    """
    if offset >= len(data):
        return "", offset

    if data[offset] not in TYPE_NAMES:
        # A tag we don't model. Historical best-effort: read it as text, fall
        # back to hex. Kept here rather than in decode_typed_value, which owes
        # a caller honest bytes instead of a hopeful string.
        offset += 1
        length, offset = ber_decode_length(data, offset)
        if offset + length <= len(data):
            raw = data[offset:offset + length]
            try:
                return raw.decode("utf-8", errors="replace"), offset + length
            except (UnicodeDecodeError, LookupError):
                return raw.hex(), offset + length
        return "", offset + length

    _type_name, value, new_offset = decode_typed_value(data, offset)
    return ("" if value is None else str(value)), new_offset


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




# --- SNMP v2c application types ----------------------------------------------

SNMP_SET_REQUEST = 0xA3

# Application tags (RFC 2578 §7.1). Gauge32 and Unsigned32 share tag 0x42.
SNMP_IP_ADDRESS = 0x40
SNMP_COUNTER32 = 0x41
SNMP_GAUGE32 = 0x42
SNMP_TIMETICKS = 0x43
SNMP_OPAQUE = 0x44
SNMP_COUNTER64 = 0x46

# Varbind exception markers (RFC 3416 §4.1). A v2c agent answers an OID it
# cannot supply with one of these *in place of the value*, leaving the PDU's
# error-status at noError — so a GET can succeed and still carry nothing.
SNMP_NO_SUCH_OBJECT = 0x80
SNMP_NO_SUCH_INSTANCE = 0x81
SNMP_END_OF_MIB_VIEW = 0x82

# tag -> the type name an integrator reads in a MIB. This is the vocabulary
# the driver-facing API speaks, so it stays MIB spelling, not Python spelling.
TYPE_NAMES: dict[int, str] = {
    ASN1_INTEGER: "integer",
    ASN1_OCTET_STRING: "string",
    ASN1_NULL: "null",
    ASN1_OID: "oid",
    SNMP_IP_ADDRESS: "ip_address",
    SNMP_COUNTER32: "counter32",
    SNMP_GAUGE32: "gauge32",
    SNMP_TIMETICKS: "timeticks",
    SNMP_OPAQUE: "opaque",
    SNMP_COUNTER64: "counter64",
    SNMP_NO_SUCH_OBJECT: "noSuchObject",
    SNMP_NO_SUCH_INSTANCE: "noSuchInstance",
    SNMP_END_OF_MIB_VIEW: "endOfMibView",
}
TYPE_TAGS: dict[str, int] = {
    name: tag for tag, name in TYPE_NAMES.items()
}
TYPE_TAGS["unsigned32"] = SNMP_GAUGE32  # the other spelling of tag 0x42

# Unsigned application types: decoding these with a signed reader turns any
# value over 2^31 negative, which is exactly the range a byte/packet counter
# lives in.
UNSIGNED_TAGS = frozenset({
    SNMP_COUNTER32, SNMP_GAUGE32, SNMP_TIMETICKS, SNMP_COUNTER64,
})
EXCEPTION_TAGS = frozenset({
    SNMP_NO_SUCH_OBJECT, SNMP_NO_SUCH_INSTANCE, SNMP_END_OF_MIB_VIEW,
})

# RFC 3416 §4.1 error-status values.
ERROR_STATUS_NAMES: dict[int, str] = {
    0: "noError", 1: "tooBig", 2: "noSuchName", 3: "badValue", 4: "readOnly",
    5: "genErr", 6: "noAccess", 7: "wrongType", 8: "wrongLength",
    9: "wrongEncoding", 10: "wrongValue", 11: "noCreation",
    12: "inconsistentValue", 13: "resourceUnavailable", 14: "commitFailed",
    15: "undoFailed", 16: "authorizationError", 17: "notWritable",
    18: "inconsistentName",
}

# What the user is told when an agent refuses. The agent's own reason, said
# once, in the words an integrator can act on.
ERROR_SENTENCES: dict[int, str] = {
    1: "The reply was too large for the device to send.",
    2: "The device has no such OID.",
    3: "The device rejected that value.",
    4: "That OID is read-only on this device.",
    5: "The device reported a general error.",
    6: "The community string cannot reach that OID.",
    7: "The device expected a different value type for that OID.",
    8: "The value was the wrong length for that OID.",
    9: "The device could not decode the value that was sent.",
    10: "The device rejected that value.",
    11: "That OID does not exist and the device will not create it.",
    12: "That value conflicts with the device's current configuration.",
    13: "The device does not have the resources to make that change.",
    14: "The device failed to commit the change.",
    15: "The device failed to undo a change it could not commit.",
    16: "The community string is not allowed to write.",
    17: "That OID is not writable on this device.",
    18: "That OID name is not consistent with the device's configuration.",
}


def error_sentence(error_status: int) -> str:
    """The user-facing sentence for an SNMP error-status."""
    if error_status == 0:
        return ""
    return ERROR_SENTENCES.get(
        error_status,
        f"The device refused the request (error {error_status}).",
    )


@dataclass(frozen=True)
class SnmpVarBind:
    """One OID and the value an agent returned for it, type preserved.

    `type` is the MIB spelling (`integer`, `gauge32`, `string`...) or, when
    the agent could not supply a value, the exception name (`noSuchObject`,
    `noSuchInstance`, `endOfMibView`) with `value` None.
    """

    oid: str
    type: str
    value: int | str | None

    @property
    def is_exception(self) -> bool:
        """True when the agent answered with an exception marker instead of
        a value — the OID is absent, out of view, or past the end of the MIB."""
        return self.type in ("noSuchObject", "noSuchInstance", "endOfMibView")


@dataclass(frozen=True)
class SnmpMessage:
    """A parsed SNMP response PDU."""

    request_id: int
    error_status: int
    error_index: int
    varbinds: tuple[SnmpVarBind, ...]

    @property
    def ok(self) -> bool:
        return self.error_status == 0

    @property
    def error_name(self) -> str:
        return ERROR_STATUS_NAMES.get(self.error_status, str(self.error_status))

    @property
    def error_sentence(self) -> str:
        return error_sentence(self.error_status)

    def by_oid(self) -> dict[str, SnmpVarBind]:
        """Varbinds keyed by OID. A repeated OID keeps the last binding."""
        return {vb.oid: vb for vb in self.varbinds}


# --- Typed value encoding (the SET half) -------------------------------------


def _encode_tagged_int(tag: int, value: int, max_bytes: int = 4) -> bytes:
    """Encode an integer under an arbitrary BER tag.

    Reuses ber_encode_integer's payload rule (which already emits the leading
    zero byte a positive value with its high bit set needs) and swaps the tag,
    because the application types are integers wearing a different hat.
    """
    encoded = ber_encode_integer(value, max_bytes=max_bytes)
    return bytes([tag]) + encoded[1:]


def _encode_ip_address(value: str) -> bytes:
    """Encode a dotted-quad as the 4-byte SNMP IpAddress type."""
    parts = value.strip().split(".")
    if len(parts) != 4:
        raise ValueError(f"Not an IPv4 address: {value!r}")
    try:
        octets = bytes(int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"Not an IPv4 address: {value!r}") from exc
    return bytes([SNMP_IP_ADDRESS]) + ber_encode_length(4) + octets


def encode_typed_value(type_name: str, value: int | str | bytes | None) -> bytes:
    """Encode one value for a SET varbind under its MIB type name.

    Raises ValueError on an unknown type name or a value the type cannot
    carry — the caller turns that into the message the user sees, since a
    wrong type here is an authoring mistake, not a device fault.
    """
    name = (type_name or "").strip().lower()
    if name in ("null", ""):
        return ber_encode_null()
    if name == "integer":
        return ber_encode_integer(int(value))
    if name == "string":
        if isinstance(value, bytes):
            return (
                bytes([ASN1_OCTET_STRING])
                + ber_encode_length(len(value))
                + value
            )
        return ber_encode_string("" if value is None else str(value))
    if name == "oid":
        return ber_encode_oid(str(value))
    if name == "ip_address":
        return _encode_ip_address(str(value))
    if name in ("gauge32", "unsigned32", "counter32", "timeticks"):
        ival = int(value)
        if ival < 0:
            raise ValueError(f"{name} cannot be negative: {ival}")
        return _encode_tagged_int(TYPE_TAGS[name], ival, max_bytes=5)
    if name == "counter64":
        ival = int(value)
        if ival < 0:
            raise ValueError(f"counter64 cannot be negative: {ival}")
        return _encode_tagged_int(SNMP_COUNTER64, ival, max_bytes=9)
    raise ValueError(f"Unknown SNMP value type: {type_name!r}")


def build_snmp_set(
    community: str,
    bindings: list[tuple[str, str, int | str | bytes | None]],
    request_id: int,
) -> bytes:
    """Build an SNMP v2c SET-REQUEST packet.

    Args:
        community: SNMP community string (the write community, which is
            usually not the read one).
        bindings: (oid, type_name, value) triples.
        request_id: Unique request identifier.
    """
    varbinds = [
        ber_encode_sequence([
            ber_encode_oid(oid),
            encode_typed_value(type_name, value),
        ])
        for oid, type_name, value in bindings
    ]

    pdu = ber_encode_tagged(SNMP_SET_REQUEST, [
        ber_encode_integer(request_id),
        ber_encode_integer(0),   # error-status
        ber_encode_integer(0),   # error-index
        ber_encode_sequence(varbinds),
    ])

    return ber_encode_sequence([
        ber_encode_integer(SNMP_VERSION_2C),
        ber_encode_string(community),
        pdu,
    ])


# --- Typed value decoding ----------------------------------------------------


def decode_typed_value(
    data: bytes, offset: int,
) -> tuple[str, int | str | None, int]:
    """Decode one BER value, type preserved.

    Returns (type_name, value, new_offset). An unknown tag comes back as
    ``("opaque", <hex string>, ...)`` rather than raising — an agent that
    answers with something exotic should not take the whole poll down.
    """
    if offset >= len(data):
        return "null", None, offset

    tag = data[offset]

    if tag in EXCEPTION_TAGS:
        # Exception markers carry a zero-length body.
        offset += 1
        length, offset = ber_decode_length(data, offset)
        return TYPE_NAMES[tag], None, offset + length

    if tag == ASN1_NULL:
        return "null", None, offset + 2

    if tag == ASN1_INTEGER:
        value, offset = ber_decode_integer(data, offset)
        return "integer", value, offset

    if tag == ASN1_OCTET_STRING:
        text, offset = ber_decode_string(data, offset)
        return "string", text, offset

    if tag == ASN1_OID:
        oid, offset = ber_decode_oid(data, offset)
        return "oid", oid, offset

    if tag == SNMP_IP_ADDRESS:
        offset += 1
        length, offset = ber_decode_length(data, offset)
        raw = data[offset:offset + length]
        offset += length
        if len(raw) == 4:
            return "ip_address", ".".join(str(b) for b in raw), offset
        return "ip_address", raw.hex(), offset

    if tag in UNSIGNED_TAGS:
        offset += 1
        length, offset = ber_decode_length(data, offset)
        raw = data[offset:offset + length]
        offset += length
        return TYPE_NAMES[tag], int.from_bytes(raw, "big", signed=False), offset

    # Unknown tag — hand back the bytes rather than guessing at a meaning.
    offset += 1
    length, offset = ber_decode_length(data, offset)
    raw = data[offset:offset + length]
    return "opaque", raw.hex(), offset + length


def parse_snmp_message(data: bytes) -> SnmpMessage | None:
    """Parse an SNMP response PDU into a typed message.

    Returns None when the datagram is not a parseable SNMP response — a
    truncated packet, a different protocol answering on 161, or a PDU type
    we did not ask for.
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

        request_id, offset = ber_decode_integer(data, offset)
        error_status, offset = ber_decode_integer(data, offset)
        error_index, offset = ber_decode_integer(data, offset)

        varbinds: list[SnmpVarBind] = []
        if offset < len(data) and data[offset] == ASN1_SEQUENCE:
            offset += 1
            list_len, offset = ber_decode_length(data, offset)
            end = offset + list_len
            while offset < end and offset < len(data):
                if data[offset] != ASN1_SEQUENCE:
                    break
                offset += 1
                _vb_len, offset = ber_decode_length(data, offset)
                oid, offset = ber_decode_oid(data, offset)
                type_name, value, offset = decode_typed_value(data, offset)
                if oid:
                    varbinds.append(SnmpVarBind(oid, type_name, value))

        return SnmpMessage(
            request_id=request_id,
            error_status=error_status,
            error_index=error_index,
            varbinds=tuple(varbinds),
        )
    except (ValueError, IndexError, KeyError):
        log.debug("Failed to parse SNMP message", exc_info=True)
        return None


# --- The agent side ----------------------------------------------------------
# Building a request and parsing a response is the controller's half, above.
# These are the mirror: what a simulator (or a test) needs to read a request
# and answer it. Same codec, so a framing bug cannot hide by being wrong in
# both directions at once.


def oid_sort_key(oid: str) -> tuple[int, ...]:
    """Sort key putting OIDs in true agent order.

    Lexicographic string order is wrong: `...1.10.0` sorts before `...1.9.0`
    as text and after it as an OID, which is the difference between a walk
    that terminates and one that skips half a table.
    """
    parts = []
    for chunk in oid.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            return tuple(parts)
    return tuple(parts)


@dataclass(frozen=True)
class SnmpRequest:
    """A parsed SNMP request PDU."""

    pdu_type: int
    request_id: int
    community: str
    varbinds: tuple[SnmpVarBind, ...]

    @property
    def is_get(self) -> bool:
        return self.pdu_type == SNMP_GET_REQUEST

    @property
    def is_get_next(self) -> bool:
        return self.pdu_type == SNMP_GETNEXT_REQUEST

    @property
    def is_set(self) -> bool:
        return self.pdu_type == SNMP_SET_REQUEST

    @property
    def oids(self) -> tuple[str, ...]:
        return tuple(vb.oid for vb in self.varbinds)


def parse_snmp_request(data: bytes) -> SnmpRequest | None:
    """Parse an SNMP v2c GET / GETNEXT / SET request.

    Returns None for anything that is not one of those — a truncated
    datagram, another protocol arriving on 161, or a PDU type we do not
    serve (a v1 message parses far enough to be recognised and is then
    refused here, since the version field is not v2c).
    """
    try:
        offset = 0
        if offset >= len(data) or data[offset] != ASN1_SEQUENCE:
            return None
        offset += 1
        _msg_len, offset = ber_decode_length(data, offset)

        version, offset = ber_decode_integer(data, offset)
        if version != SNMP_VERSION_2C:
            return None
        community, offset = ber_decode_string(data, offset)

        if offset >= len(data):
            return None
        pdu_type = data[offset]
        if pdu_type not in (
            SNMP_GET_REQUEST, SNMP_GETNEXT_REQUEST, SNMP_SET_REQUEST,
        ):
            return None
        offset += 1
        _pdu_len, offset = ber_decode_length(data, offset)

        request_id, offset = ber_decode_integer(data, offset)
        _error_status, offset = ber_decode_integer(data, offset)
        _error_index, offset = ber_decode_integer(data, offset)

        varbinds: list[SnmpVarBind] = []
        if offset < len(data) and data[offset] == ASN1_SEQUENCE:
            offset += 1
            list_len, offset = ber_decode_length(data, offset)
            end = offset + list_len
            while offset < end and offset < len(data):
                if data[offset] != ASN1_SEQUENCE:
                    break
                offset += 1
                _vb_len, offset = ber_decode_length(data, offset)
                oid, offset = ber_decode_oid(data, offset)
                type_name, value, offset = decode_typed_value(data, offset)
                if oid:
                    varbinds.append(SnmpVarBind(oid, type_name, value))

        return SnmpRequest(
            pdu_type=pdu_type,
            request_id=request_id,
            community=community,
            varbinds=tuple(varbinds),
        )
    except (ValueError, IndexError, KeyError):
        log.debug("Failed to parse SNMP request", exc_info=True)
        return None


def build_snmp_response(
    request_id: int,
    community: str,
    varbinds: list[tuple[str, str, int | str | bytes | None]],
    error_status: int = 0,
    error_index: int = 0,
) -> bytes:
    """Build an SNMP v2c response PDU.

    `varbinds` are (oid, type_name, value) triples. A type name of
    `noSuchObject`, `noSuchInstance` or `endOfMibView` emits that exception
    marker in place of a value, which is how a v2c agent reports an OID it
    cannot supply without failing the whole PDU.
    """
    encoded = []
    for oid, type_name, value in varbinds:
        tag = TYPE_TAGS.get(type_name)
        if tag in EXCEPTION_TAGS:
            value_bytes = bytes([tag]) + ber_encode_length(0)
        else:
            value_bytes = encode_typed_value(type_name, value)
        encoded.append(ber_encode_sequence([ber_encode_oid(oid), value_bytes]))

    pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
        ber_encode_integer(request_id),
        ber_encode_integer(error_status),
        ber_encode_integer(error_index),
        ber_encode_sequence(encoded),
    ])
    return ber_encode_sequence([
        ber_encode_integer(SNMP_VERSION_2C),
        ber_encode_string(community),
        pdu,
    ])
