"""
OpenAVC Binary Protocol Helpers — utilities for binary AV control protocols.

Many AV devices (Samsung MDC, Extron SIS, Crestron CIP, etc.) use binary
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
    """Convert a string with escape sequences (\\r, \\n, \\t, \\xHH) to bytes.

    Only safe, known sequences are processed. Unknown backslash sequences
    are passed through literally. Used by drivers and frame parsers.
    """
    def _replace(m: re.Match) -> str:
        seq = m.group(0)
        if seq in _ESCAPE_MAP:
            return _ESCAPE_MAP[seq]
        if seq.startswith(r"\x") and len(seq) == 4:
            try:
                return chr(int(seq[2:], 16))
            except ValueError:
                pass
        return seq

    processed = re.sub(r'\\(?:r|n|t|\\|x[0-9a-fA-F]{2})', _replace, s)
    return processed.encode("latin-1")


def checksum_xor(data: bytes) -> int:
    """XOR all bytes together. Common in Samsung MDC, LG, etc."""
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
    """
    if special is None:
        special = {escape_char: escape_char}
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
    """
    if special is None:
        special = {escape_char: escape_char}
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
