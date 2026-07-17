"""
OpenAVC Binary Protocol Helpers — utilities for binary AV control protocols.

Many AV devices — displays, switchers, control processors — use binary
protocols with checksums, escape sequences, and hex-encoded data. These
helpers provide the building blocks so drivers don't reinvent the wheel.
"""

from __future__ import annotations

import re

# Escape sequences recognized in driver delimiter/command strings
_ESCAPE_MAP = {
    r"\r": "\r",
    r"\n": "\n",
    r"\t": "\t",
    r"\\": "\\",
}


def encode_escape_sequences(s: str) -> bytes:
    r"""Convert a string with escape sequences (\r, \n, \t, \xHH) to bytes.

    Only safe, known sequences are processed; unknown backslash sequences are
    passed through literally. ``\xHH`` yields the single raw byte 0xHH, so
    binary delimiters and headers (e.g. ``\xFE`` / ``\xAA``) come out exact.
    All other text is encoded as UTF-8, so an on-screen message, label, or
    user string variable containing non-Latin-1 characters (an em dash, curly
    quotes, accented or CJK text) is sent instead of raising
    UnicodeEncodeError on a normal control path.

    Used by drivers and frame parsers.
    """
    # Build the byte stream directly: an escaped ``\xHH`` must stay a single
    # byte, but a plain-latin-1 ``.encode()`` of the whole string would raise
    # on any character above U+00FF. So encode literal spans as UTF-8 and emit
    # ``\xHH`` as its raw byte.
    result = bytearray()
    pos = 0
    for m in re.finditer(r'\\(?:r|n|t|\\|x[0-9a-fA-F]{2})', s):
        result += s[pos:m.start()].encode("utf-8")
        seq = m.group(0)
        if seq in _ESCAPE_MAP:
            # \r \n \t \\ resolve to single ASCII control bytes.
            result += _ESCAPE_MAP[seq].encode("utf-8")
        else:
            # \xHH — the regex guarantees exactly two hex digits (0x00-0xFF).
            result.append(int(seq[2:], 16))
        pos = m.end()
    result += s[pos:].encode("utf-8")
    return bytes(result)


def pack_length_prefix(value: int, size: int, endian: str = "big") -> bytes:
    """Pack an integer length into a fixed-width big/little-endian field.

    The send-side counterpart to :class:`LengthPrefixFrameParser` — used by a
    driver's ``send_frame`` block to build the computed data-length field of a
    binary packet header (e.g. an AV receiver protocol's 4-byte big-endian
    length that a static ``command_prefix`` can't express, since it varies
    per message). ``size`` is
    the field width in bytes; ``endian`` is "big" (default) or "little". A value
    too large for the field raises OverflowError — a genuine protocol error the
    author should see, not silently truncate.
    """
    if size < 1:
        raise ValueError("length field size must be >= 1")
    order = "little" if endian == "little" else "big"
    return int(value).to_bytes(size, order)


def checksum_xor(data: bytes) -> int:
    """XOR all bytes together. Common in display control protocols."""
    result = 0
    for b in data:
        result ^= b
    return result


def checksum_sum(data: bytes, mask: int = 0xFF) -> int:
    """Sum all bytes, masked to fit in one byte. Common in many protocols."""
    return sum(data) & mask


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    """
    CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF).

    Used by some advanced AV control protocols and industrial devices.
    """
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc


def hex_dump(data: bytes, width: int = 16) -> str:
    """
    Format bytes as a hex dump string for logging.

    Example output::

        00: AA 11 FE 01 00 00 01 11  |........|
        08: 0D 0A                    |..|
    """
    lines: list[str] = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:04X}: {hex_part:<{width * 3}}  |{ascii_part}|")
    return "\n".join(lines)


def _require_escape_char_mapped(
    escape_char: int,
    special: dict[int, int] | None,
) -> dict[int, int]:
    """Resolve ``special``, enforcing that ``escape_char`` is one of its keys.

    A framed binary protocol must escape its own escape byte, otherwise a raw
    escape byte in the payload is indistinguishable from an escape prefix on
    the way back out. ``None`` gets the self-escaping default; a caller-supplied
    map missing ``escape_char`` is a corruption-prone mistake, so raise.
    """
    if special is None:
        return {escape_char: escape_char}
    if escape_char not in special:
        raise ValueError(
            f"escape_char 0x{escape_char:02X} must be a key in `special` "
            f"(map it to itself, e.g. {{0x{escape_char:02X}: 0x{escape_char:02X}}}); "
            f"without it a raw escape byte is left unescaped and corrupts the "
            f"stream on the round-trip"
        )
    return special


def escape_bytes(
    data: bytes,
    escape_char: int = 0xFE,
    special: dict[int, int] | None = None,
) -> bytes:
    """
    Escape special bytes by prefixing with an escape character.

    Args:
        data: Raw bytes to escape.
        escape_char: The escape prefix byte.
        special: Map of byte_value -> escaped_value. If None, defaults to
                 escaping the escape_char itself (0xFE -> 0xFE 0xFE).

    ``escape_char`` MUST be a key in ``special`` (map it to itself). If it is
    not, a raw ``escape_char`` byte in ``data`` is emitted unescaped, and
    ``unescape_bytes`` then reads it as an escape prefix and silently corrupts
    the stream — so a missing key raises ValueError rather than corrupting.
    """
    special = _require_escape_char_mapped(escape_char, special)
    result = bytearray()
    for b in data:
        if b in special:
            result.append(escape_char)
            result.append(special[b])
        else:
            result.append(b)
    return bytes(result)


def unescape_bytes(
    data: bytes,
    escape_char: int = 0xFE,
    special: dict[int, int] | None = None,
) -> bytes:
    """
    Reverse of escape_bytes — remove escape prefixes.

    Args:
        data: Escaped bytes.
        escape_char: The escape prefix byte.
        special: Map of escaped_value -> original_byte. If None, defaults to
                 unescaping the escape_char itself (0xFE 0xFE -> 0xFE).

    ``escape_char`` MUST be a key in ``special`` (map it to itself) so it
    mirrors ``escape_bytes`` exactly; a missing key raises ValueError rather
    than silently mis-decoding an escaped escape byte.
    """
    special = _require_escape_char_mapped(escape_char, special)
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == escape_char and i + 1 < len(data):
            next_byte = data[i + 1]
            if next_byte in special:
                result.append(special[next_byte])
                i += 2
                continue
        result.append(data[i])
        i += 1
    return bytes(result)
