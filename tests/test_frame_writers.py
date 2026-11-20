"""Each frame parser type writes a payload back into the frame it reads.

A frame parser cuts frames out of a byte stream; its ``frame`` is the inverse,
the bytes a device sends for one payload, so the simulator can answer in the
framing the driver reads. The byte counts a frame carries around its length
field and payload (a magic, a reserved region) are written as zero bytes: the
parser skips them unread. A payload the frame cannot carry (a fixed-length
frame of another size, a length its field cannot hold) is refused, never
written as a frame the parser would read wrong.

``build_frame_parser`` builds each type from ``framing_checks.FRAME_TYPES``:
the fields it reads, the value each falls back to, and the rule each is held
to, so a value the checks refuse is refused here in the same words.
"""
from __future__ import annotations

import pytest

from openavc.drivers.framing_checks import FRAME_TYPES
from openavc.transport.frame_parsers import (
    FixedLengthFrameParser,
    LengthPrefixFrameParser,
    StructFrameParser,
    build_frame_parser,
    write_frame,
)

PAYLOAD = b"PWR1\r"

ROUND_TRIPS = {
    "length_prefix defaults": {"type": "length_prefix"},
    "length_prefix one byte": {"type": "length_prefix", "header_size": 1},
    "length_prefix counts its header": {
        "type": "length_prefix", "header_size": 2, "header_offset": -2,
    },
    "length_prefix behind a header": {
        "type": "length_prefix", "length_offset": 8, "header_size": 4,
        "header_extra": 4, "length_endian": "little",
    },
    "fixed_length": {"type": "fixed_length", "length": len(PAYLOAD)},
    "struct_frame defaults": {"type": "struct_frame"},
    "struct_frame reserved regions": {
        "type": "struct_frame", "header_reserve": 22, "length_size": 2,
        "length_adjust": -8, "mid_reserve": 4, "trailer_reserve": 24,
    },
    "struct_frame little endian": {
        "type": "struct_frame", "length_size": 4, "length_endian": "little",
        "length_adjust": 1,
    },
}


@pytest.mark.parametrize("config", ROUND_TRIPS.values(), ids=list(ROUND_TRIPS))
def test_the_parser_reads_back_what_its_writer_wrote(config: dict) -> None:
    framed = build_frame_parser(config).frame(PAYLOAD)
    assert build_frame_parser(config).feed(framed) == [PAYLOAD]
    assert write_frame(config, PAYLOAD) == framed


@pytest.mark.parametrize("config", ROUND_TRIPS.values(), ids=list(ROUND_TRIPS))
def test_two_frames_back_to_back_read_as_two(config: dict) -> None:
    writer = build_frame_parser(config)
    stream = writer.frame(PAYLOAD) + writer.frame(PAYLOAD)
    assert build_frame_parser(config).feed(stream) == [PAYLOAD, PAYLOAD]


def test_every_type_the_table_holds_has_a_round_trip() -> None:
    assert {config["type"] for config in ROUND_TRIPS.values()} == set(FRAME_TYPES)


def test_the_bytes_each_writer_puts_around_the_payload() -> None:
    assert LengthPrefixFrameParser(header_size=2).frame(b"abc") == b"\x00\x03abc"
    assert LengthPrefixFrameParser(
        header_size=4, length_offset=2, header_extra=1, length_endian="little",
    ).frame(b"abc") == b"\x00\x00\x03\x00\x00\x00\x00abc"
    assert LengthPrefixFrameParser(header_size=1, header_offset=-1).frame(b"ab") == b"\x03ab"
    assert FixedLengthFrameParser(3).frame(b"abc") == b"abc"
    assert StructFrameParser(
        header_reserve=2, length_size=2, length_endian="little",
        length_adjust=1, mid_reserve=1, trailer_reserve=1,
    ).frame(b"abc") == b"\x00\x00\x02\x00\x00abc\x00"


def test_a_frame_that_reads_its_header_too_reads_back_whole() -> None:
    config = {"type": "length_prefix", "header_size": 1, "include_header": True}
    framed = build_frame_parser(config).frame(b"abc")
    assert framed == b"\x03abc"
    assert build_frame_parser(config).feed(framed) == [framed]


@pytest.mark.parametrize(
    "parser, payload, words",
    [
        (FixedLengthFrameParser(4), b"abc", "exactly 4 bytes"),
        (FixedLengthFrameParser(2), b"abc", "exactly 2 bytes"),
        (LengthPrefixFrameParser(header_size=1), bytes(256), "1-byte length"),
        (LengthPrefixFrameParser(header_size=2, header_offset=5), b"abc", "length -2"),
        (StructFrameParser(length_adjust=9), b"abc", "length -6"),
        (StructFrameParser(length_size=1, length_adjust=-300), b"abc", "1-byte length"),
    ],
)
def test_a_payload_the_frame_cannot_carry_is_refused(parser, payload, words) -> None:
    with pytest.raises(ValueError, match=words):
        parser.frame(payload)


def test_a_config_with_no_type_the_table_knows_writes_the_payload_as_it_is() -> None:
    assert write_frame(None, b"abc") == b"abc"
    assert write_frame({}, b"abc") == b"abc"
    assert write_frame({"type": "delimiter"}, b"abc") == b"abc"
    assert write_frame({"type": ["fixed_length"]}, b"abc") == b"abc"


# --- the builder reads the table --------------------------------------------------


def test_the_builder_passes_every_field_the_table_holds() -> None:
    parser = build_frame_parser(
        {"type": "length_prefix", "header_size": 1, "include_header": True}
    )
    assert parser.feed(b"\x02ab") == [b"\x02ab"]


@pytest.mark.parametrize(
    "config, words",
    [
        ({"type": "fixed_length", "length": 0}, "length must be a positive integer (got 0)"),
        ({"type": "fixed_length", "length": -2}, "length must be a positive integer (got -2)"),
        ({"type": "length_prefix", "length_offset": -1},
         "length_offset must be a non-negative integer (got -1)"),
        ({"type": "length_prefix", "header_extra": -1},
         "header_extra must be a non-negative integer (got -1)"),
        ({"type": "length_prefix", "header_offset": "x"},
         "header_offset must be an integer (got 'x')"),
        ({"type": "length_prefix", "header_size": 3}, "header_size must be 1, 2, or 4 (got 3)"),
        ({"type": "struct_frame", "mid_reserve": -1},
         "mid_reserve must be a non-negative integer (got -1)"),
    ],
)
def test_a_value_the_checks_refuse_is_refused_by_the_builder_in_their_words(
    config: dict, words: str,
) -> None:
    with pytest.raises(ValueError) as refused:
        build_frame_parser(config)
    assert words in str(refused.value)
    with pytest.raises(ValueError):
        write_frame(config, b"abc")


def test_the_builder_names_every_refused_field() -> None:
    with pytest.raises(ValueError) as refused:
        build_frame_parser({"type": "length_prefix", "length_offset": -1, "header_extra": -2})
    assert "length_offset" in str(refused.value)
    assert "header_extra" in str(refused.value)
