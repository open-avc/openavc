"""Tests for binary protocol helpers."""

import pytest

from openavc.transport.binary_helpers import (
    checksum_sum,
    checksum_xor,
    crc16_ccitt,
    encode_escape_sequences,
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


# --- encode_escape_sequences (M-255) ---


def test_encode_escape_sequences_control_escapes():
    # \r \n \t \\ map to their single control bytes.
    assert encode_escape_sequences(r"a\r\n\t\\b") == b"a\r\n\t\\b"


def test_encode_escape_sequences_hex_stays_single_byte():
    # \xHH must yield the single raw byte, incl. high bytes used as binary
    # delimiters / headers (would be 2 bytes under a naive utf-8 encode).
    assert encode_escape_sequences(r"\xFE\xAA\x02") == bytes([0xFE, 0xAA, 0x02])
    assert encode_escape_sequences(r"PWR\x00ON") == b"PWR\x00ON"


def test_encode_escape_sequences_unknown_passthrough():
    # An unrecognized backslash sequence is left literal.
    assert encode_escape_sequences(r"\q") == b"\\q"


def test_encode_escape_sequences_non_latin1_text_is_utf8():
    # An em dash (U+2014) is not Latin-1; the old .encode("latin-1") raised
    # UnicodeEncodeError on a normal send path. It must now encode as UTF-8.
    assert encode_escape_sequences("hello — world") == "hello — world".encode("utf-8")


def test_encode_escape_sequences_mixes_utf8_text_and_hex_bytes():
    # Unicode label text alongside a raw \xFF byte: text is UTF-8, byte is raw.
    result = encode_escape_sequences(r"café\xffX")
    assert result == "café".encode("utf-8") + bytes([0xFF]) + b"X"


def test_encode_escape_sequences_latin1_high_char_no_longer_raises():
    # A bare high-Latin-1 char (é) used to encode to one byte; it now UTF-8s,
    # but the point is it does not raise and round-trips as text.
    assert encode_escape_sequences("é") == "é".encode("utf-8")


# --- escape_char-mapped invariant (M-256) ---


def test_escape_bytes_requires_escape_char_in_custom_map():
    # A custom map that forgets the escape byte would leave a raw 0xFE
    # unescaped and corrupt on the round-trip — now a clear error instead.
    with pytest.raises(ValueError, match="escape_char"):
        escape_bytes(bytes([0xFE, 0x02]), escape_char=0xFE, special={0x02: 0x82})


def test_unescape_bytes_requires_escape_char_in_custom_map():
    with pytest.raises(ValueError, match="escape_char"):
        unescape_bytes(bytes([0xFE, 0x82]), escape_char=0xFE, special={0x82: 0x02})


def test_escape_bytes_custom_map_with_escape_char_ok():
    # Including the escape byte (the invariant) still works and round-trips.
    special = {0x02: 0x82, 0xFE: 0xFE}
    inverse = {0x82: 0x02, 0xFE: 0xFE}
    raw = bytes([0xFE, 0x02, 0x10, 0xFE])
    escaped = escape_bytes(raw, escape_char=0xFE, special=special)
    assert unescape_bytes(escaped, escape_char=0xFE, special=inverse) == raw


def test_default_special_still_allowed():
    # None-default keeps the self-escaping behavior (escape_char is implicit).
    assert escape_bytes(bytes([0xFE])) == bytes([0xFE, 0xFE])
    assert unescape_bytes(bytes([0xFE, 0xFE])) == bytes([0xFE])
