"""
OSC (Open Sound Control) message encoding and decoding.

Pure functions with no transport logic — usable by ConfigurableDriver,
Python drivers, and the OSCTransport class.

OSC 1.0 spec: opensoundcontrol.stanford.edu/spec-1_0.html
No external dependencies — all encoding uses the struct module.
"""

from __future__ import annotations

import struct
from typing import Any


def osc_encode_message(address: str, args: list[tuple[str, Any]] | None = None) -> bytes:
    """
    Encode an OSC message.

    Args:
        address: OSC address pattern (e.g., "/ch/01/mix/fader").
        args: List of (type_tag, value) tuples. Supported types:
              "f" (float32), "i" (int32), "s" (string), "h" (int64),
              "d" (float64), "b" (blob/bytes), "T" (True), "F" (False),
              "N" (Nil).

    Returns:
        Raw OSC message bytes ready to send over UDP.
    """
    args = args or []
    msg = _encode_string(address)

    type_tags = ","
    arg_data = b""
    for tag, value in args:
        type_tags += tag
        if tag == "f":
            arg_data += struct.pack(">f", float(value))
        elif tag == "i":
            arg_data += struct.pack(">i", int(value))
        elif tag == "s":
            arg_data += _encode_string(str(value))
        elif tag == "h":
            arg_data += struct.pack(">q", int(value))
        elif tag == "d":
            arg_data += struct.pack(">d", float(value))
        elif tag == "b":
            blob = value if isinstance(value, bytes) else str(value).encode("utf-8")
            arg_data += _encode_blob(blob)
        elif tag in ("T", "F", "N"):
            pass  # No argument data

    msg += _encode_string(type_tags)
    msg += arg_data
    return msg


def osc_decode_message(data: bytes) -> tuple[str, list[tuple[str, Any]]]:
    """
    Decode a single OSC message.

    If the data is a bundle, decodes the first message in the bundle.
    Use osc_decode_bundle() to get all messages from a bundle.

    Returns:
        (address, args) where args is a list of (type_tag, value) tuples.
    """
    if len(data) < 4:
        raise ValueError("OSC message too short")

    if data[:8] == b"#bundle\x00":
        messages = osc_decode_bundle(data)
        if not messages:
            raise ValueError("Empty OSC bundle")
        return messages[0]

    return _decode_single_message(data, 0, len(data))


def osc_decode_bundle(data: bytes) -> list[tuple[str, list[tuple[str, Any]]]]:
    """
    Decode an OSC bundle into a list of (address, args) tuples.

    Handles nested bundles by flattening all messages.
    """
    if data[:8] != b"#bundle\x00":
        return [_decode_single_message(data, 0, len(data))]

    messages: list[tuple[str, list[tuple[str, Any]]]] = []
    # Skip "#bundle\0" (8 bytes) + timetag (8 bytes)
    offset = 16

    while offset + 4 <= len(data):
        elem_size = struct.unpack_from(">i", data, offset)[0]
        offset += 4
        if elem_size <= 0 or offset + elem_size > len(data):
            break

        elem_data = data[offset : offset + elem_size]
        if elem_data[:8] == b"#bundle\x00":
            messages.extend(osc_decode_bundle(elem_data))
        else:
            try:
                messages.append(_decode_single_message(elem_data, 0, len(elem_data)))
            except (ValueError, struct.error):
                pass  # Skip malformed elements
        offset += elem_size

    return messages


def _decode_single_message(
    data: bytes, start: int, end: int
) -> tuple[str, list[tuple[str, Any]]]:
    """Decode a single (non-bundle) OSC message from a byte range."""
    buf = data[start:end]
    offset = 0

    address, offset = _read_string(buf, offset)
    if not address.startswith("/"):
        raise ValueError(f"Invalid OSC address: {address!r}")

    if offset >= len(buf):
        return address, []

    type_tags, offset = _read_string(buf, offset)
    if not type_tags.startswith(","):
        raise ValueError(f"Invalid OSC type tags: {type_tags!r}")

    tags = type_tags[1:]
    args: list[tuple[str, Any]] = []

    for tag in tags:
        if tag == "f":
            value = struct.unpack_from(">f", buf, offset)[0]
            offset += 4
            args.append(("f", round(value, 6)))
        elif tag == "i":
            value = struct.unpack_from(">i", buf, offset)[0]
            offset += 4
            args.append(("i", value))
        elif tag == "s":
            value, offset = _read_string(buf, offset)
            args.append(("s", value))
        elif tag == "h":
            value = struct.unpack_from(">q", buf, offset)[0]
            offset += 8
            args.append(("h", value))
        elif tag == "d":
            value = struct.unpack_from(">d", buf, offset)[0]
            offset += 8
            args.append(("d", value))
        elif tag == "b":
            blob_len = struct.unpack_from(">i", buf, offset)[0]
            offset += 4
            value = buf[offset : offset + blob_len]
            offset += blob_len
            remainder = blob_len % 4
            if remainder:
                offset += 4 - remainder
            args.append(("b", value))
        elif tag == "T":
            args.append(("T", True))
        elif tag == "F":
            args.append(("F", False))
        elif tag == "N":
            args.append(("N", None))
        else:
            break  # Unknown type tag — stop parsing

    return address, args


# --- Internal helpers ---


def _pad(data: bytes) -> bytes:
    """Pad data to a 4-byte boundary with null bytes."""
    remainder = len(data) % 4
    if remainder:
        data += b"\x00" * (4 - remainder)
    return data


def _encode_string(s: str) -> bytes:
    """Encode an OSC string: null-terminated, padded to 4-byte boundary."""
    return _pad(s.encode("utf-8") + b"\x00")


def _encode_blob(data: bytes) -> bytes:
    """Encode an OSC blob: int32 length prefix + data, padded to 4 bytes."""
    return struct.pack(">i", len(data)) + _pad(data)


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read a null-terminated, 4-byte-padded OSC string."""
    try:
        end = data.index(b"\x00", offset)
    except ValueError:
        raise ValueError(f"Unterminated OSC string at offset {offset}")
    s = data[offset:end].decode("utf-8", errors="replace")
    length = end - offset + 1
    remainder = length % 4
    if remainder:
        length += 4 - remainder
    return s, offset + length
