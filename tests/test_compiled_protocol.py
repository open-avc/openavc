"""Direct tests for the shared protocol-interpreter helpers.

Most of these helpers are exercised end-to-end through the driver and
simulator suites; the cases here pin the pieces whose call sites are thin
wrappers around this module — delimiter decoding and send_frame packet
framing — so a regression points straight at the shared implementation.
"""
from __future__ import annotations

from server.drivers.compiled_protocol import (
    apply_send_frame,
    build_send_frame,
    decode_delimiter,
    split_send_frames,
)


# ── decode_delimiter ──


def test_decode_delimiter_passes_real_characters_through():
    # YAML double-quoted scalars already carry real control characters.
    assert decode_delimiter("\r\n") == "\r\n"
    assert decode_delimiter("#") == "#"


def test_decode_delimiter_decodes_backslash_escapes():
    assert decode_delimiter("\\r\\n") == "\r\n"
    assert decode_delimiter("\\t") == "\t"
    assert decode_delimiter("\\x03") == "\x03"
    assert decode_delimiter("\\\\") == "\\"


def test_decode_delimiter_leaves_unknown_sequences_alone():
    assert decode_delimiter("\\q") == "\\q"
    assert decode_delimiter("") == ""


# ── send_frame build / apply / split ──

_EISCP_CFG = {
    "type": "length_prefix",
    "header": "ISCP\\x00\\x00\\x00\\x10",
    "length_size": 4,
    "length_endian": "big",
}


def test_build_send_frame_decodes_header_bytes():
    sf = build_send_frame(_EISCP_CFG)
    assert sf == {
        "header": b"ISCP\x00\x00\x00\x10",
        "after_length": b"",
        "length_size": 4,
        "length_endian": "big",
    }


def test_build_send_frame_rejects_unknown_type_and_non_dict():
    assert build_send_frame({"type": "crc_frame"}) is None
    assert build_send_frame(None) is None
    assert build_send_frame("length_prefix") is None


def test_apply_send_frame_wraps_and_noops_without_config():
    sf = build_send_frame(_EISCP_CFG)
    framed = apply_send_frame(sf, b"!1PWR01\r")
    assert framed == b"ISCP\x00\x00\x00\x10" + (8).to_bytes(4, "big") + b"!1PWR01\r"
    assert apply_send_frame(None, b"!1PWR01\r") == b"!1PWR01\r"


def test_split_send_frames_round_trips_and_keeps_partial_tail():
    sf = build_send_frame(_EISCP_CFG)
    frame_a = apply_send_frame(sf, b"!1PWR01\r")
    frame_b = apply_send_frame(sf, b"!1MVL20\r")
    buffer = bytearray(frame_a + frame_b + frame_a[:5])

    messages = split_send_frames(sf, buffer)

    assert messages == [b"!1PWR01\r", b"!1MVL20\r"]
    # The incomplete third frame stays buffered for the next read.
    assert bytes(buffer) == frame_a[:5]

    buffer.extend(frame_a[5:])
    assert split_send_frames(sf, buffer) == [b"!1PWR01\r"]
    assert not buffer
