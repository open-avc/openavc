"""Tests for frame parsers."""

import pytest

from server.transport.frame_parsers import (
    CallableFrameParser,
    DelimiterFrameParser,
    FixedLengthFrameParser,
    LengthPrefixFrameParser,
)


# --- DelimiterFrameParser ---


def test_delimiter_single_message():
    p = DelimiterFrameParser(b"\r")
    assert p.feed(b"hello\r") == [b"hello"]


def test_delimiter_multiple_messages():
    p = DelimiterFrameParser(b"\r")
    assert p.feed(b"msg1\rmsg2\r") == [b"msg1", b"msg2"]


def test_delimiter_partial_then_complete():
    p = DelimiterFrameParser(b"\r")
    assert p.feed(b"hel") == []
    assert p.feed(b"lo\r") == [b"hello"]


def test_delimiter_multi_byte():
    p = DelimiterFrameParser(b"\r\n")
    assert p.feed(b"line1\r\nline2\r\n") == [b"line1", b"line2"]


def test_delimiter_skips_empty():
    p = DelimiterFrameParser(b"\r")
    assert p.feed(b"\r\r") == []


def test_delimiter_reset():
    p = DelimiterFrameParser(b"\r")
    p.feed(b"partial")
    p.reset()
    assert p.feed(b"fresh\r") == [b"fresh"]


def test_delimiter_empty_raises():
    with pytest.raises(ValueError):
        DelimiterFrameParser(b"")


# --- LengthPrefixFrameParser ---


def test_length_prefix_basic():
    p = LengthPrefixFrameParser(header_size=2)
    # 2-byte big-endian length = 5, then 5 bytes of payload
    data = b"\x00\x05hello"
    assert p.feed(data) == [b"hello"]


def test_length_prefix_partial():
    p = LengthPrefixFrameParser(header_size=2)
    assert p.feed(b"\x00\x05hel") == []
    assert p.feed(b"lo") == [b"hello"]


def test_length_prefix_multiple():
    p = LengthPrefixFrameParser(header_size=1)
    data = b"\x02hi\x03bye"
    assert p.feed(data) == [b"hi", b"bye"]


def test_length_prefix_with_offset():
    # header_offset=-2 means the length includes the 2-byte header itself
    p = LengthPrefixFrameParser(header_size=2, header_offset=-2)
    data = b"\x00\x07hello"  # length=7, minus 2 = 5 bytes payload
    assert p.feed(data) == [b"hello"]


def test_length_prefix_include_header():
    p = LengthPrefixFrameParser(header_size=2, include_header=True)
    data = b"\x00\x03abc"
    assert p.feed(data) == [b"\x00\x03abc"]


def test_length_prefix_invalid_size():
    with pytest.raises(ValueError):
        LengthPrefixFrameParser(header_size=3)


def test_length_prefix_reset():
    p = LengthPrefixFrameParser(header_size=1)
    p.feed(b"\x05he")  # partial
    p.reset()
    assert p.feed(b"\x02ok") == [b"ok"]


# --- FixedLengthFrameParser ---


def test_fixed_length_basic():
    p = FixedLengthFrameParser(4)
    assert p.feed(b"abcd") == [b"abcd"]


def test_fixed_length_multiple():
    p = FixedLengthFrameParser(3)
    assert p.feed(b"abcdef") == [b"abc", b"def"]


def test_fixed_length_partial():
    p = FixedLengthFrameParser(5)
    assert p.feed(b"ab") == []
    assert p.feed(b"cde") == [b"abcde"]


def test_fixed_length_invalid():
    with pytest.raises(ValueError):
        FixedLengthFrameParser(0)


def test_fixed_length_reset():
    p = FixedLengthFrameParser(3)
    p.feed(b"ab")
    p.reset()
    assert p.feed(b"xyz") == [b"xyz"]


# --- CallableFrameParser ---


def test_callable_parser():
    def parse(buf):
        if b"\n" in buf:
            msg, rest = buf.split(b"\n", 1)
            return msg, rest
        return None, buf

    p = CallableFrameParser(parse)
    assert p.feed(b"line1\nline2\n") == [b"line1", b"line2"]


def test_callable_parser_partial():
    def parse(buf):
        if len(buf) >= 4:
            return buf[:4], buf[4:]
        return None, buf

    p = CallableFrameParser(parse)
    assert p.feed(b"ab") == []
    assert p.feed(b"cdef") == [b"abcd"]
    # "ef" is left in buffer, add 2 more to complete
    assert p.feed(b"gh") == [b"efgh"]


def test_callable_parser_reset():
    def parse(buf):
        if b";" in buf:
            msg, rest = buf.split(b";", 1)
            return msg, rest
        return None, buf

    p = CallableFrameParser(parse)
    p.feed(b"partial")
    p.reset()
    assert p.feed(b"new;") == [b"new"]
