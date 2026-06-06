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


# --- Hardening regressions (overflow / framing safety) ---


def test_callable_parser_no_forward_progress_does_not_hang():
    """H-064: a parse_fn that returns a message without consuming the buffer
    must not spin forever (it would wedge the whole event loop)."""
    calls = {"n": 0}

    def parse(buf):
        calls["n"] += 1
        # Always claims a message but never shrinks the buffer.
        return b"x", buf

    p = CallableFrameParser(parse)
    msgs = p.feed(b"data")
    # Emits the message it found, then stops — no infinite loop.
    assert msgs == [b"x"]
    assert calls["n"] == 1


def test_callable_parser_buffer_grows_is_stopped():
    """A parse_fn that returns MORE buffer than it got is also no-progress."""
    def parse(buf):
        return b"m", buf + b"!"  # buffer grows, never shrinks

    p = CallableFrameParser(parse)
    assert p.feed(b"ab") == [b"m"]


def test_fixed_length_overflow_clears_not_trims():
    """M-108: on overflow a fixed-length parser clears (resyncs on the next
    whole frame) rather than keeping a misaligned tail that corrupts every
    subsequent frame."""
    length = 7
    p = FixedLengthFrameParser(length, max_buffer=20)
    # Feed > max_buffer of data that is NOT a multiple of length. The old code
    # trimmed to the last max_buffer bytes (20 % 7 = 6 -> misaligned).
    p.feed(b"\x00" * 25)
    # After the overflow clear, a clean run of whole frames parses aligned.
    msgs = p.feed(b"A" * length + b"B" * length)
    assert msgs == [b"A" * length, b"B" * length]


def test_length_prefix_bogus_length_clears_no_byte_walk():
    """M-109: a claimed frame larger than max_buffer clears the buffer instead
    of walking it one byte at a time (O(n^2) on the event loop)."""
    p = LengthPrefixFrameParser(header_size=2, max_buffer=1024)
    # Header claims 60000 bytes — far over max_buffer.
    bogus = (60000).to_bytes(2, "big") + b"garbage" * 100
    assert p.feed(bogus) == []
    # Buffer was cleared, so a valid frame right after parses cleanly.
    good = (3).to_bytes(2, "big") + b"abc"
    assert p.feed(good) == [b"abc"]


def test_length_prefix_stalled_frame_stays_bounded():
    """L-074: a stalled partial frame (header received, payload never
    completes) keeps the buffer bounded by max_buffer — symmetric with the
    other parsers, never silently pinning more than the cap."""
    p = LengthPrefixFrameParser(header_size=2, max_buffer=64)
    # Header claims 50 bytes of payload; only 10 arrive, then the device
    # goes silent. The buffer waits but stays within the cap.
    assert p.feed((50).to_bytes(2, "big") + b"x" * 10) == []
    assert len(p._buffer) <= p._max_buffer
    # More dribbles, still no completion — still bounded, never over-cap.
    assert p.feed(b"y" * 5) == []
    assert len(p._buffer) <= p._max_buffer
