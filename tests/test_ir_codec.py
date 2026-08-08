"""Tests for the vendor-neutral IR codec (Pronto hex <-> IRCode).

Synthetic codes only — the codec is platform math, exercised with hand-built
values, never a captured real-device code.
"""

from __future__ import annotations

import pytest

from openavc.transport.ir_codec import (
    IRCode,
    build_pronto,
    frequency_to_pronto_word,
    normalize_pronto,
    parse_pronto,
    pronto_word_to_frequency,
)

# A synthetic learned code: 38 kHz (word 0x006D), 2 once-pairs + 1 repeat-pair.
#   header = 0000 006D 0002 0001, then 2*(2+1)=6 burst values.
SYNTH = "0000 006D 0002 0001 0158 00AB 0015 0041 0015 0596"


# --- frequency word math ---


def test_frequency_word_round_trips_common_values():
    assert pronto_word_to_frequency(0x006D) == 38029  # classic ~38 kHz
    assert frequency_to_pronto_word(38000) == 0x006D
    assert frequency_to_pronto_word(40000) == 0x0068


def test_frequency_word_rejects_nonpositive():
    with pytest.raises(ValueError):
        pronto_word_to_frequency(0)
    with pytest.raises(ValueError):
        frequency_to_pronto_word(-1)


# --- parse / build ---


def test_parse_pronto_extracts_frequency_bursts_and_offset():
    code = parse_pronto(SYNTH)
    assert code.frequency == 38029
    assert code.bursts == (0x0158, 0x00AB, 0x0015, 0x0041, 0x0015, 0x0596)
    # 2 once-pairs -> the repeat body starts at burst index 4.
    assert code.repeat_offset == 4


def test_build_pronto_renders_normalized_uppercase():
    code = IRCode(frequency=38029, bursts=(0x0158, 0x00AB, 0x0015, 0x0596), repeat_offset=0)
    # 0 once-pairs, 2 repeat-pairs.
    assert build_pronto(code) == "0000 006D 0000 0002 0158 00AB 0015 0596"


def test_pronto_round_trips_byte_identical():
    assert normalize_pronto(SYNTH) == SYNTH


def test_whole_sequence_repeat_when_no_preamble():
    # offset 1 (Pronto once-count 0) means the entire sequence is the repeat body.
    code = parse_pronto("0000 006D 0000 0003 0015 0015 0015 0041 0015 0596")
    assert code.repeat_offset == 0
    assert len(code.bursts) == 6


# --- validation errors ---


def test_parse_rejects_preset_format():
    with pytest.raises(ValueError, match="learned"):
        parse_pronto("0100 006D 0000 0002 0015 0596 0015 0596")


def test_parse_rejects_burst_count_mismatch():
    # header claims 2 once-pairs (4 values) but only 2 are present.
    with pytest.raises(ValueError, match="mismatch"):
        parse_pronto("0000 006D 0002 0000 0015 0596")


def test_parse_rejects_short_and_bad_hex():
    with pytest.raises(ValueError):
        parse_pronto("0000 006D")
    with pytest.raises(ValueError):
        parse_pronto("0000 XYZW 0000 0001 0015 0596")


def test_parse_rejects_out_of_range_word():
    with pytest.raises(ValueError, match="16-bit"):
        parse_pronto("0000 1006D 0000 0001 0015 0596")


def test_build_rejects_odd_bursts_and_bad_offset():
    with pytest.raises(ValueError):
        build_pronto(IRCode(frequency=38000, bursts=(1, 2, 3), repeat_offset=0))
    with pytest.raises(ValueError):
        build_pronto(IRCode(frequency=38000, bursts=(1, 2, 3, 4), repeat_offset=3))
