"""Tests for binary protocol helpers."""

from server.transport.binary_helpers import (
    checksum_sum,
    checksum_xor,
    crc16_ccitt,
    escape_bytes,
    hex_dump,
    unescape_bytes,
)


def test_checksum_xor():
    assert checksum_xor(b"\x01\x02\x03") == 0x01 ^ 0x02 ^ 0x03
    assert checksum_xor(b"") == 0
    assert checksum_xor(b"\xFF") == 0xFF


def test_checksum_sum():
    assert checksum_sum(b"\x01\x02\x03") == 6
    assert checksum_sum(b"\xFF\x01") == 0  # (255 + 1) & 0xFF = 0
    assert checksum_sum(b"") == 0


def test_checksum_sum_custom_mask():
    assert checksum_sum(b"\xFF\x01", mask=0xFFFF) == 256


def test_crc16_ccitt_known_value():
    # "123456789" -> CRC-16/CCITT-FALSE = 0x29B1
    data = b"123456789"
    assert crc16_ccitt(data) == 0x29B1


def test_crc16_ccitt_empty():
    assert crc16_ccitt(b"") == 0xFFFF  # init value unchanged


def test_hex_dump_basic():
    data = b"\xAA\x11\xFE\x01"
    result = hex_dump(data)
    assert "AA 11 FE 01" in result
    assert "|" in result


def test_hex_dump_multiline():
    data = bytes(range(32))
    result = hex_dump(data, width=16)
    lines = result.strip().split("\n")
    assert len(lines) == 2


def test_escape_bytes_default():
    data = bytes([0x01, 0xFE, 0x02])
    result = escape_bytes(data)
    # 0xFE should become 0xFE 0xFE
    assert result == bytes([0x01, 0xFE, 0xFE, 0x02])


def test_escape_bytes_custom():
    special = {0xAA: 0x01, 0xFE: 0xFE}
    data = bytes([0xAA, 0x55, 0xFE])
    result = escape_bytes(data, escape_char=0xFE, special=special)
    assert result == bytes([0xFE, 0x01, 0x55, 0xFE, 0xFE])


def test_unescape_bytes_default():
    escaped = bytes([0x01, 0xFE, 0xFE, 0x02])
    result = unescape_bytes(escaped)
    assert result == bytes([0x01, 0xFE, 0x02])


def test_unescape_roundtrip():
    original = bytes([0x00, 0xFE, 0xFF, 0xFE, 0x01])
    escaped = escape_bytes(original)
    assert unescape_bytes(escaped) == original


def test_unescape_bytes_custom():
    special = {0x01: 0xAA, 0xFE: 0xFE}
    escaped = bytes([0xFE, 0x01, 0x55, 0xFE, 0xFE])
    result = unescape_bytes(escaped, escape_char=0xFE, special=special)
    assert result == bytes([0xAA, 0x55, 0xFE])
