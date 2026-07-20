"""Direct tests for the shared protocol-interpreter helpers.

Most of these helpers are exercised end-to-end through the driver and
simulator suites; the cases here pin the pieces whose call sites are thin
wrappers around this module — delimiter decoding, send_frame packet framing,
and the receive-side compile — so a regression points straight at the shared
implementation.
"""
from __future__ import annotations

from server.drivers.compiled_protocol import (
    apply_send_frame,
    build_send_frame,
    compile_driver,
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


# ── compile_driver (receive side) ──


def test_compile_driver_builds_all_three_tables():
    definition = {
        "state_variables": {
            "power": {"type": "boolean"},
            "volume": {"type": "integer"},
        },
        "child_entity_types": {
            "zone": {"state_variables": {"level": {"type": "integer"}}},
        },
        "responses": [
            {"match": "PWR(0|1) {unit}", "set": {"power": "$1"}, "throttle": 2},
            {
                "match": "ZLV(\\d+),(\\d+)",
                "child_set": [
                    {"type": "zone", "id": "$1", "state": {"level": "$2"}},
                ],
            },
            {"address": "/dev/{unit}/level", "mappings": [{"arg": 0, "state": "volume"}]},
            {"json": True, "require": "serial", "set": {"volume": "vol"}},
        ],
    }
    compiled = compile_driver(definition, {"unit": "A"}, device_id="acme_1")

    assert len(compiled.responses) == 2
    pattern, mappings, child_mappings, throttle = compiled.responses[0]
    assert pattern.pattern == "PWR(0|1) A"  # config substituted at compile time
    assert mappings == [{"group": 1, "state": "power", "type": "boolean"}]
    assert child_mappings == []
    assert throttle == {"window": 2.0, "last": {}}
    zone_routing = compiled.responses[1][2]
    assert zone_routing == [
        {
            "type": "zone",
            "id": ("group", 1),
            "id_map": None,
            "props": [{"prop": "level", "group": 2, "type": "integer"}],
        }
    ]

    addr, osc_mappings, _osc_children, _throttle = compiled.osc_responses[0]
    assert addr == "/dev/A/level"
    assert osc_mappings == [{"arg": 0, "state": "volume"}]

    json_mappings, _throttle2, require = compiled.json_responses[0]
    assert require == ("serial",)
    assert json_mappings == [{"state": "volume", "key": "vol", "type": "integer"}]


def test_compile_driver_copies_mapping_lists_per_call():
    # The mapping lists come from the (class-shared) definition; each compile
    # must hand out its own copies so one instance's edits can't leak into
    # another instance of the same driver type.
    definition = {
        "responses": [{"match": "X(\\d+)", "mappings": [{"group": 1, "state": "v"}]}],
    }
    a = compile_driver(definition, {})
    b = compile_driver(definition, {})
    assert a.responses[0][1] == b.responses[0][1]
    assert a.responses[0][1] is not b.responses[0][1]
    assert a.responses[0][1] is not definition["responses"][0]["mappings"]


def test_compile_driver_skips_invalid_regex_and_keeps_rule_order():
    definition = {
        "state_variables": {"v": {"type": "integer"}},
        "responses": [
            {"match": "([bad"},
            {"match": "OK(\\d+)", "set": {"v": "$1"}},
        ],
    }
    compiled = compile_driver(definition, {})
    assert [p.pattern for p, _m, _c, _t in compiled.responses] == ["OK(\\d+)"]
