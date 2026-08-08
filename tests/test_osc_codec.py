"""Tests for OSC codec decode hardening against malformed / hostile bytes.

Synthetic OSC payloads only — no real device. Covers the blob-length and
bundle-nesting guards so untrusted device bytes reject cleanly (ValueError)
instead of silently mis-decoding or raising RecursionError.
"""

import struct

import pytest

from openavc.transport.osc_codec import (
    _MAX_BUNDLE_DEPTH,
    osc_decode_bundle,
    osc_decode_message,
    osc_encode_message,
)


# --- Helpers: build raw OSC bytes ---


def _osc_string(s: str) -> bytes:
    b = s.encode("ascii") + b"\x00"
    pad = (-len(b)) % 4
    return b + b"\x00" * pad


def _bundle(*elements: bytes) -> bytes:
    out = b"#bundle\x00" + b"\x00" * 8  # 8-byte (ignored) timetag
    for el in elements:
        out += struct.pack(">i", len(el)) + el
    return out


def _nested_bundle(depth: int, inner: bytes) -> bytes:
    b = inner
    for _ in range(depth):
        b = _bundle(b)
    return b


# --- M-257: blob length sign / bounds ---


def test_negative_blob_length_raises_valueerror():
    # blob length is a signed int32 on the wire; -4 used to run the offset
    # backward and silently mis-decode following args with no exception.
    data = _osc_string("/x") + _osc_string(",b") + struct.pack(">i", -4)
    with pytest.raises(ValueError):
        osc_decode_message(data)


def test_oversized_blob_length_raises_valueerror():
    data = _osc_string("/x") + _osc_string(",b") + struct.pack(">i", 1000) + b"\x01\x02"
    with pytest.raises(ValueError):
        osc_decode_message(data)


def test_truncated_int_arg_raises_valueerror_not_struct_error():
    # Only 2 of the 4 int bytes present. Pre-fix this surfaced as struct.error,
    # which a caller catching only the documented ValueError would miss.
    data = _osc_string("/x") + _osc_string(",i") + b"\x00\x01"
    with pytest.raises(ValueError):
        osc_decode_message(data)


def test_valid_blob_roundtrips():
    msg = osc_encode_message("/x", [("b", b"\xde\xad\xbe\xef")])
    addr, args = osc_decode_message(msg)
    assert addr == "/x"
    assert args == [("b", b"\xde\xad\xbe\xef")]


def test_valid_multi_arg_message_still_decodes():
    msg = osc_encode_message("/mix", [("i", 5), ("f", 1.5), ("s", "hi")])
    addr, args = osc_decode_message(msg)
    assert addr == "/mix"
    assert args[0] == ("i", 5)
    assert args[2] == ("s", "hi")


# --- M-258: bundle nesting depth cap ---


def test_deeply_nested_bundle_no_recursion_error():
    # Far beyond Python's recursion limit; pre-fix this raised RecursionError
    # (a RuntimeError subclass) that escaped the ValueError/struct.error catch.
    inner = osc_encode_message("/deep", [("i", 1)])
    data = _nested_bundle(2000, inner)
    result = osc_decode_bundle(data)  # must not raise
    assert result == []  # inner is beyond the depth cap, skipped non-fatally


def test_bundle_skips_deep_nested_but_keeps_sibling():
    # A hostile deep-nested element must not take down its valid sibling.
    good = osc_encode_message("/ok", [("i", 7)])
    deep = _nested_bundle(2000, osc_encode_message("/deep", [("i", 1)]))
    data = _bundle(deep, good)
    result = osc_decode_bundle(data)
    assert [addr for addr, _ in result] == ["/ok"]


def test_moderately_nested_bundle_flattens():
    m1 = osc_encode_message("/a", [("i", 1)])
    m2 = osc_encode_message("/b", [("f", 2.0)])
    data = _bundle(_bundle(m1, m2))  # 1 level of nesting, under the cap
    result = osc_decode_bundle(data)
    assert [addr for addr, _ in result] == ["/a", "/b"]


def test_nesting_just_under_cap_still_decodes():
    # Legitimate nesting below the cap must still flatten to the inner message
    # (the cap only rejects pathologically deep bundles).
    inner = osc_encode_message("/edge", [("i", 9)])
    data = _nested_bundle(_MAX_BUNDLE_DEPTH - 1, inner)
    result = osc_decode_bundle(data)
    assert [addr for addr, _ in result] == ["/edge"]
