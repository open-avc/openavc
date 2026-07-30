"""Tests for frame parsers."""

import pytest

from server.transport.frame_parsers import (
    CallableFrameParser,
    DelimiterFrameParser,
    FixedLengthFrameParser,
    LengthPrefixFrameParser,
    SlipFrameParser,
    StructFrameParser,
    build_frame_parser,
    slip_encode,
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


def _eiscp_frame(data: bytes) -> bytes:
    # 16-byte eISCP header: "ISCP" + header-size(16) + 4-byte data length + version/reserved.
    return b"ISCP\x00\x00\x00\x10" + len(data).to_bytes(4, "big") + b"\x01\x00\x00\x00" + data


def test_length_prefix_offset_reads_eiscp_header():
    # eISCP: the 4-byte length sits at offset 8, behind magic + header-size, and
    # is followed by 4 version/reserved bytes before the data.
    p = LengthPrefixFrameParser(
        header_size=4, length_offset=8, header_extra=4, length_endian="big",
    )
    # Two different-length bodies back to back exercise the computed length.
    stream = _eiscp_frame(b"!1PWR01\r") + _eiscp_frame(b"!1PWRQSTN\r")
    assert p.feed(stream) == [b"!1PWR01\r", b"!1PWRQSTN\r"]


def test_length_prefix_offset_partial_across_reads():
    p = LengthPrefixFrameParser(header_size=4, length_offset=8, header_extra=4)
    frame = _eiscp_frame(b"!1MVL28\r")
    # Split mid-header and mid-body; the parser must reassemble.
    assert p.feed(frame[:6]) == []
    assert p.feed(frame[6:20]) == []
    assert p.feed(frame[20:]) == [b"!1MVL28\r"]


def test_length_prefix_include_header_with_offset():
    p = LengthPrefixFrameParser(
        header_size=4, length_offset=8, header_extra=4, include_header=True,
    )
    frame = _eiscp_frame(b"!1PWR01\r")
    assert p.feed(frame) == [frame]


def test_length_prefix_little_endian():
    p = LengthPrefixFrameParser(header_size=2, length_endian="little")
    assert p.feed(b"\x03\x00abc") == [b"abc"]


def test_length_prefix_negative_offset_rejected():
    with pytest.raises(ValueError):
        LengthPrefixFrameParser(header_size=4, length_offset=-1)


def test_length_prefix_defaults_unchanged():
    # New params default to the pre-existing behavior (length at offset 0).
    p = LengthPrefixFrameParser(header_size=2)
    assert p.feed(b"\x00\x03abc\x00\x01z") == [b"abc", b"z"]


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


# --- Resync: the returned buffer is authoritative on the None branch ---
#
# A binary protocol recovers from a bad frame by dropping bytes it can never
# use while reporting no message. These tests use an INVENTED framing (Acme
# widget: SOF, 1-byte length, payload, XOR checksum) because the property under
# test belongs to the platform, not to any device.

WIDGET_SOF = b"\x02\x02"


def _widget_frame(payload: bytes) -> bytes:
    """Build one Acme widget frame: SOF + len + payload + XOR checksum."""
    body = bytes([len(payload)]) + payload
    checksum = 0
    for b in body:
        checksum ^= b
    return WIDGET_SOF + body + bytes([checksum])


def _widget_parse(buffer: bytes):
    """Invented parse function exercising both kinds of None-branch drop.

    Returns (frame_payload, remaining) or (None, remaining).
    """
    start = buffer.find(WIDGET_SOF)
    if start == -1:
        # No start marker anywhere: nothing here is usable. Keep the last byte
        # in case it is the first half of a split marker.
        return None, buffer[-1:]
    if start > 0:
        buffer = buffer[start:]
    if len(buffer) < 3:
        return None, buffer
    payload_len = buffer[2]
    total = 3 + payload_len + 1
    if len(buffer) < total:
        return None, buffer
    body = buffer[2 : total - 1]
    checksum = 0
    for b in body:
        checksum ^= b
    if checksum != buffer[total - 1]:
        # Corrupt frame: skip this start marker and resync on the next one.
        return None, buffer[2:]
    return buffer[3 : total - 1], buffer[total:]


def test_callable_parser_honors_trimmed_buffer_on_none():
    """Leading garbage the parse function drops must actually be dropped."""
    p = CallableFrameParser(_widget_parse)
    assert p.feed(b"\x00" * 4096) == []
    # The parse function asked to keep one byte; the parser must not hoard the
    # other 4095. Discarding the trim is what let 64 KB accumulate silently.
    assert len(p._buffer) == 1


def test_callable_parser_delivers_good_frame_after_garbage():
    p = CallableFrameParser(_widget_parse)
    assert p.feed(b"garbage-with-no-marker") == []
    assert p.feed(_widget_frame(b"\x11\x22")) == [b"\x11\x22"]


def test_callable_parser_resyncs_past_corrupt_frame_in_one_feed():
    """A corrupt frame is skipped and a following good frame still arrives."""
    corrupt = bytearray(_widget_frame(b"\x33\x44"))
    corrupt[-1] ^= 0xFF  # break the checksum
    good = _widget_frame(b"\x55\x66")

    p = CallableFrameParser(_widget_parse)
    assert p.feed(bytes(corrupt) + good) == [b"\x55\x66"]


def test_callable_parser_keeps_delivering_after_one_corrupt_frame():
    """The F1 shape: one bad frame must not wedge the stream.

    Before the None-branch buffer was honored, a single corrupt frame stalled
    every subsequent good frame until the max_buffer guard cleared it.
    """
    # Corrupt a payload byte rather than the checksum byte: this framing has no
    # escaping, so a corrupted trailing byte can itself alias the start marker
    # and swallow the next frame. That ambiguity belongs to the invented
    # protocol, not to the parser, and would obscure what this test asserts.
    corrupt = bytearray(_widget_frame(b"\x77\x88"))
    corrupt[3] ^= 0xFF

    p = CallableFrameParser(_widget_parse)
    assert p.feed(bytes(corrupt)) == []

    delivered = []
    for i in range(200):
        delivered += p.feed(_widget_frame(bytes([i % 256])))
    assert len(delivered) == 200
    assert p._buffer == b""


def test_callable_parser_none_branch_partial_frame_still_waits():
    """Honoring the buffer must not break ordinary reassembly."""
    frame = _widget_frame(b"\x99\xaa")
    p = CallableFrameParser(_widget_parse)
    assert p.feed(frame[:3]) == []
    assert p.feed(frame[3:]) == [b"\x99\xaa"]


def test_callable_parser_none_branch_no_progress_does_not_spin():
    """A parse_fn that reports no message and consumes nothing must stop.

    This is the None-branch half of the hang guard: without it, honoring the
    returned buffer would loop forever on a parse function that never
    consumes.
    """
    calls = {"n": 0}

    def parse(buf):
        calls["n"] += 1
        return None, buf  # never a message, never consumes

    p = CallableFrameParser(parse)
    assert p.feed(b"data") == []
    assert calls["n"] == 1
    assert p._buffer == b"data"


def test_callable_parser_none_branch_growing_buffer_does_not_spin():
    """A None return whose buffer GREW is also no progress."""
    calls = {"n": 0}

    def parse(buf):
        calls["n"] += 1
        return None, buf + b"!"

    p = CallableFrameParser(parse)
    assert p.feed(b"ab") == []
    assert calls["n"] == 1


# --- Overflow warnings name the device (F15) ---


def test_callable_parser_overflow_warning_names_the_device(caplog):
    """64 KB of dropped traffic must be attributable to a device."""
    def parse(buf):
        return None, buf  # never consumes, so the buffer overflows

    p = CallableFrameParser(parse, max_buffer=16)
    p.device_label = "widget_1"
    with caplog.at_level("WARNING"):
        p.feed(b"x" * 32)
    assert "[widget_1] Callable parser buffer overflow" in caplog.text


def test_delimiter_parser_overflow_warning_names_the_device(caplog):
    """The label is shared by every parser, not just the callable one."""
    p = DelimiterFrameParser(b"\r", max_buffer=8)
    p.device_label = "widget_2"
    with caplog.at_level("WARNING"):
        p.feed(b"no delimiter here at all")
    assert "[widget_2] Delimiter parser buffer overflow" in caplog.text


def test_overflow_warning_without_a_label_is_still_readable(caplog):
    """An unstamped parser (built directly in a test or a script) must not
    emit a stray prefix."""
    p = DelimiterFrameParser(b"\r", max_buffer=8)
    with caplog.at_level("WARNING"):
        p.feed(b"no delimiter here at all")
    assert "Delimiter parser buffer overflow" in caplog.text
    assert "[]" not in caplog.text


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


# --- SlipFrameParser / slip_encode (RFC 1055 double-END, used by OSC-over-TCP) ---

_END = 0xC0
_ESC = 0xDB
_ESC_END = 0xDC
_ESC_ESC = 0xDD


def test_slip_encode_wraps_with_end_bytes():
    out = slip_encode(b"hi")
    assert out[0] == _END and out[-1] == _END
    assert out == bytes([_END]) + b"hi" + bytes([_END])


def test_slip_encode_escapes_end_and_esc():
    # A literal END inside the payload escapes to ESC ESC_END; a literal ESC
    # escapes to ESC ESC_ESC. END must not appear inside the framed payload.
    payload = bytes([_END, _ESC, ord("A")])
    out = slip_encode(payload)
    assert out == bytes([_END, _ESC, _ESC_END, _ESC, _ESC_ESC, ord("A"), _END])
    # The only END bytes are the frame delimiters at the ends.
    assert out[1:-1].count(_END) == 0


def test_slip_round_trip_plain():
    p = SlipFrameParser()
    assert p.feed(slip_encode(b"hello world")) == [b"hello world"]


def test_slip_round_trip_with_control_bytes():
    p = SlipFrameParser()
    payload = bytes([_END, _ESC, 0x00, 0xFF, _END, ord("Z")])
    assert p.feed(slip_encode(payload)) == [payload]


def test_slip_double_end_skips_empty_run():
    # Double-END framing: ...END END... yields no spurious empty message.
    p = SlipFrameParser()
    stream = (
        bytes([_END]) + b"one" + bytes([_END])
        + bytes([_END]) + b"two" + bytes([_END])
    )
    assert p.feed(stream) == [b"one", b"two"]


def test_slip_multiple_frames_one_feed():
    p = SlipFrameParser()
    stream = slip_encode(b"a") + slip_encode(b"bb") + slip_encode(b"ccc")
    assert p.feed(stream) == [b"a", b"bb", b"ccc"]


def test_slip_partial_feed_across_chunks():
    p = SlipFrameParser()
    frame = slip_encode(b"streamed")
    mid = len(frame) // 2
    # First half has no terminating END yet -> nothing emitted.
    assert p.feed(frame[:mid]) == []
    # Remainder completes the frame.
    assert p.feed(frame[mid:]) == [b"streamed"]


def test_slip_split_inside_escape_sequence():
    # Feed boundary lands between ESC and its escaped byte; the parser must
    # still reassemble the original END byte once the rest arrives.
    p = SlipFrameParser()
    frame = slip_encode(bytes([ord("x"), _END, ord("y")]))
    esc_idx = frame.index(_ESC)
    assert p.feed(frame[: esc_idx + 1]) == []  # trailing ESC held back
    assert p.feed(frame[esc_idx + 1:]) == [bytes([ord("x"), _END, ord("y")])]


def test_slip_buffer_overflow_clears():
    p = SlipFrameParser(max_buffer=64)
    # No END ever arrives; the buffer must not grow without bound.
    assert p.feed(b"x" * 200) == []
    assert len(p._buffer) <= p._max_buffer


def test_slip_carries_a_real_osc_message():
    # The actual use: an OSC packet survives SLIP framing intact.
    from server.transport.osc_codec import osc_decode_message, osc_encode_message

    packet = osc_encode_message("/workspace/ABC/cue/1/start", [("f", 1.0)])
    p = SlipFrameParser()
    frames = p.feed(slip_encode(packet))
    assert frames == [packet]
    addr, args = osc_decode_message(frames[0])
    assert addr == "/workspace/ABC/cue/1/start"
    assert args == [("f", 1.0)]


# --- StructFrameParser ---


def _acme_frame(payload: bytes, header: int = 4, mid: int = 2, trailer: int = 6,
                adjust: int = -8, endian: str = "big", size: int = 2) -> bytes:
    """Build a synthetic struct frame: zeroed reserves around a length field
    that counts len(payload) - adjust (mirroring devices whose length field
    includes constant overhead)."""
    length_value = len(payload) - adjust
    return (
        bytes(header)
        + length_value.to_bytes(size, endian)
        + bytes(mid)
        + payload
        + bytes(trailer)
    )


def _acme_parser(**kw) -> StructFrameParser:
    args = dict(header_reserve=4, length_size=2, length_endian="big",
                length_adjust=-8, mid_reserve=2, trailer_reserve=6)
    args.update(kw)
    return StructFrameParser(**args)


def test_struct_frame_basic():
    p = _acme_parser()
    assert p.feed(_acme_frame(b"\r\nNOTIFY 1\r\n")) == [b"\r\nNOTIFY 1\r\n"]


def test_struct_frame_split_across_feeds():
    p = _acme_parser()
    frame = _acme_frame(b"\r\nNOTIFY 2\r\n")
    assert p.feed(frame[:3]) == []
    assert p.feed(frame[3:9]) == []
    assert p.feed(frame[9:]) == [b"\r\nNOTIFY 2\r\n"]


def test_struct_frame_multiple_in_one_feed():
    p = _acme_parser()
    data = _acme_frame(b"A1") + _acme_frame(b"B22")
    assert p.feed(data) == [b"A1", b"B22"]


def test_struct_frame_trailer_consumed_between_frames():
    # A wrong trailer size would desync the second frame; prove the parser
    # resumes exactly at the next header.
    p = _acme_parser()
    data = _acme_frame(b"first") + _acme_frame(b"second")
    assert p.feed(data[: len(_acme_frame(b"first")) + 5]) == [b"first"]
    assert p.feed(data[len(_acme_frame(b"first")) + 5 :]) == [b"second"]


def test_struct_frame_little_endian():
    p = _acme_parser(length_endian="little")
    frame = _acme_frame(b"LE", endian="little")
    assert p.feed(frame) == [b"LE"]


def test_struct_frame_no_adjust():
    p = _acme_parser(length_adjust=0)
    frame = _acme_frame(b"RAW", adjust=0)
    assert p.feed(frame) == [b"RAW"]


def test_struct_frame_zero_reserves_is_plain_length_prefix():
    p = StructFrameParser(length_size=1)
    assert p.feed(b"\x02hi\x03bye") == [b"hi", b"bye"]


def test_struct_frame_desync_clears_buffer():
    p = _acme_parser(max_buffer=64)
    # Claimed length far beyond the cap -> buffer cleared, parser recovers.
    bogus = bytes(4) + (60000).to_bytes(2, "big") + bytes(2) + b"x"
    assert p.feed(bogus) == []
    assert p.feed(_acme_frame(b"OK")) == [b"OK"]


def test_struct_frame_negative_payload_clamped():
    # Length field smaller than the adjustment -> zero-byte payload, frame
    # still consumed (no wedge, no crash).
    p = _acme_parser()
    frame = bytes(4) + (3).to_bytes(2, "big") + bytes(2) + bytes(6)
    assert p.feed(frame) == []
    assert p.feed(_acme_frame(b"NEXT")) == [b"NEXT"]


def test_struct_frame_invalid_params_raise():
    with pytest.raises(ValueError):
        StructFrameParser(length_size=3)
    with pytest.raises(ValueError):
        StructFrameParser(header_reserve=-1)


def test_struct_frame_reset():
    p = _acme_parser()
    p.feed(_acme_frame(b"partial")[:5])
    p.reset()
    assert p.feed(_acme_frame(b"fresh")) == [b"fresh"]


# --- build_frame_parser (shared declarative builder) ---


def test_build_frame_parser_types():
    assert isinstance(
        build_frame_parser({"type": "length_prefix"}), LengthPrefixFrameParser
    )
    assert isinstance(
        build_frame_parser({"type": "fixed_length", "length": 4}),
        FixedLengthFrameParser,
    )
    assert isinstance(
        build_frame_parser({"type": "struct_frame"}), StructFrameParser
    )
    assert build_frame_parser({"type": "bogus"}) is None
    assert build_frame_parser({}) is None
    assert build_frame_parser(None) is None


def test_build_frame_parser_struct_config():
    p = build_frame_parser(
        {
            "type": "struct_frame",
            "header_reserve": 22,
            "length_size": 2,
            "length_endian": "big",
            "length_adjust": -8,
            "mid_reserve": 4,
            "trailer_reserve": 24,
        }
    )
    payload = b"\r\np1\r\n"
    frame = (
        bytes(22)
        + (len(payload) + 8).to_bytes(2, "big")
        + bytes(4)
        + payload
        + bytes(24)
    )
    assert p.feed(frame) == [payload]
