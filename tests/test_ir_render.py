"""Tests for the vendor-neutral IR protocol renderer (server/transport/ir_render.py).

These exercise a platform capability (turning a compact protocol/device/function
code into emittable Pronto), so they belong in core. They are self-contained: no
real product is named and no fixtures are read — each case renders a synthetic
code, decodes the emitted timing back with an *independent* decoder written here,
and checks the recovered carrier, bit pattern, and frame period against the
protocol's published spec. That way a bug in the renderer's frame builder cannot
hide behind the same code in the test.
"""

import pytest

from server.transport.ir_codec import parse_pronto
from server.transport.ir_render import (
    UnsupportedProtocolError,
    is_supported,
    render,
    render_pronto,
    supported_protocols,
)

# ── independent decoders ──────────────────────────────────────────────────────


def _to_us(code):
    """Burst counts -> microseconds, using the code's own quantized carrier."""
    per = 1_000_000 / code.frequency
    return [round(c * per) for c in code.bursts]


def _sections(code):
    us = _to_us(code)
    return us[: code.repeat_offset], us[code.repeat_offset :]


def _pairs(us, start=0, count=None):
    out = [(us[i], us[i + 1]) for i in range(start, len(us) - 1, 2)]
    return out if count is None else out[:count]


def _bits_by_gap(pairs, thresh):
    """Pulse-distance: constant mark, a long gap is a 1."""
    return [1 if off > thresh else 0 for _on, off in pairs]


def _bits_by_mark(pairs, thresh):
    """Pulse-width: constant gap, a long mark is a 1."""
    return [1 if on > thresh else 0 for on, _off in pairs]


def _lsb(value, n):
    return [(value >> i) & 1 for i in range(n)]


def _msb(value, n):
    return [(value >> i) & 1 for i in range(n - 1, -1, -1)]


def _to_slots(us, unit):
    """Resample an alternating on/off stream to unit-wide polarity slots."""
    slots = []
    for i, d in enumerate(us):
        pol = 1 if i % 2 == 0 else 0  # even index = mark, odd = space
        slots.extend([pol] * max(1, round(d / unit)))
    return slots


def _manchester(slots, offset, nbits, one_pattern):
    """Decode nbits bi-phase cells (2 slots each), MSB-first, from offset."""
    out = []
    for k in range(nbits):
        cell = slots[offset + 2 * k : offset + 2 * k + 2]
        out.append(1 if cell == list(one_pattern) else 0)
    return out


def _value(bits_msb):
    v = 0
    for b in bits_msb:
        v = (v << 1) | b
    return v


# ── NEC family ────────────────────────────────────────────────────────────────


def test_nec1_header_bits_and_ditto():
    d, f = 4, 10
    code = render("NEC1", d, -1, f)
    assert abs(code.frequency - 38400) / 38400 < 0.02
    once, rep = _sections(code)
    # Header ~9 ms mark / 4.5 ms space.
    assert abs(once[0] - 9024) < 400 and abs(once[1] - 4512) < 300
    data = _pairs(once, start=2, count=32)  # 4 bytes, skip header pair
    bits = _bits_by_gap(data, 1000)
    expected = _lsb(d, 8) + _lsb(255 - d, 8) + _lsb(f, 8) + _lsb((~f) & 0xFF, 8)
    assert bits == expected
    # First frame full command, then a short ditto on the repeat.
    assert code.repeat_offset > 0
    assert len(rep) == 4  # header mark, short space, stop, lead-out


def test_nec2_whole_frame_repeats():
    d, f = 0, 7
    code = render("NEC2", d, -1, f)
    once, rep = _sections(code)
    assert once == []  # nothing sent once; the frame itself repeats
    data = _pairs(rep, start=2, count=32)
    assert _bits_by_gap(data, 1000) == (
        _lsb(d, 8) + _lsb(255 - d, 8) + _lsb(f, 8) + _lsb((~f) & 0xFF, 8)
    )


def test_necx2_short_header_and_explicit_subdevice():
    d, s, f = 7, 7, 2
    code = render("NECx2", d, s, f)
    _once, rep = _sections(code)
    # Samsung-style 4.5 ms / 4.5 ms header.
    assert abs(rep[0] - 4512) < 300 and abs(rep[1] - 4512) < 300
    data = _pairs(rep, start=2, count=32)
    assert _bits_by_gap(data, 1000) == (
        _lsb(d, 8) + _lsb(s, 8) + _lsb(f, 8) + _lsb((~f) & 0xFF, 8)
    )


def test_pioneer_is_nec_at_40khz():
    code = render("Pioneer", 165, -1, 20)
    assert abs(code.frequency - 40000) / 40000 < 0.02


# ── Sony SIRC ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "proto,dev_bits,use_sub", [("Sony12", 5, False), ("Sony15", 8, False), ("Sony20", 5, True)]
)
def test_sony_pulse_width(proto, dev_bits, use_sub):
    d, s, f = 1, 9, 18
    code = render(proto, d, s if use_sub else -1, f)
    assert abs(code.frequency - 40000) / 40000 < 0.02
    _once, rep = _sections(code)
    assert abs(rep[0] - 2400) < 200 and abs(rep[1] - 600) < 120  # 4-unit header
    nbits = 7 + dev_bits + (8 if use_sub else 0)
    data = _pairs(rep, start=2, count=nbits)
    bits = _bits_by_mark(data, 900)
    expected = _lsb(f, 7) + _lsb(d, dev_bits) + (_lsb(s, 8) if use_sub else [])
    assert bits == expected


# ── Philips RC5 / RC6 (bi-phase) ──────────────────────────────────────────────


def test_rc5_recovers_device_and_function():
    d, f = 5, 53
    code = render("RC5", d, -1, f)
    assert abs(code.frequency - 36000) / 36000 < 0.03
    _once, rep = _sections(code)
    # Prepend a virtual space so the leading lone mark becomes a full "1" cell.
    slots = [0] + _to_slots(rep, 889)
    # Cells: S1, field(~F6), T, D:5, F:6.  RC5 '1' = space,mark = [0,1].
    bits = _manchester(slots, 0, 14, one_pattern=(0, 1))
    assert bits[0] == 1  # start bit
    assert _value(bits[3:8]) == d
    f6 = 1 - bits[1]  # field bit is the inverted 7th command bit
    assert ((f6 << 6) | _value(bits[8:14])) == f


def test_rc6_recovers_device_and_function():
    d, f = 4, 12
    code = render("RC6", d, -1, f)
    assert abs(code.frequency - 36000) / 36000 < 0.03
    _once, rep = _sections(code)
    slots = _to_slots(rep, 444)
    # leader 6 mark + 2 space = 8 slots; start(2) + mode 3 cells(6) + toggle(4).
    d_off = 8 + 2 + 6 + 4
    # RC6 '1' = mark,space = [1,0].
    dbits = _manchester(slots, d_off, 8, one_pattern=(1, 0))
    fbits = _manchester(slots, d_off + 16, 8, one_pattern=(1, 0))
    assert _value(dbits) == d
    assert _value(fbits) == f


# ── Panasonic / Kaseikyo ──────────────────────────────────────────────────────


def test_panasonic_bytes_and_checksum():
    d, s, f = 8, 32, 20
    code = render("Panasonic", d, s, f)
    assert abs(code.frequency - 37000) / 37000 < 0.02
    _once, rep = _sections(code)
    assert abs(rep[0] - 3456) < 250 and abs(rep[1] - 1728) < 200  # 8/4-unit header
    data = _pairs(rep, start=2, count=48)  # 6 bytes
    bits = _bits_by_gap(data, 800)
    got = [_value(list(reversed(bits[i : i + 8]))) for i in range(0, 48, 8)]
    assert got[0] == 0x02 and got[1] == 0x20
    assert got[2] == d and got[3] == s and got[4] == f
    assert got[5] == (d ^ s ^ f) & 0xFF  # checksum byte


def test_panasonic_old_recovers_device_function():
    d, f = 1, 10
    code = render("Panasonic_Old", d, -1, f)
    assert abs(code.frequency - 57600) / 57600 < 0.02
    _once, rep = _sections(code)
    data = _pairs(rep, start=2, count=22)  # D:5 F:6 ~D:5 ~F:6
    bits = _bits_by_gap(data, 1600)  # unit 833: short 833, long 2499
    assert _value(list(reversed(bits[0:5]))) == d
    assert _value(list(reversed(bits[5:11]))) == f


# ── JVC ───────────────────────────────────────────────────────────────────────


def test_jvc_header_only_on_lead_frame():
    d, f = 3, 7
    code = render("JVC", d, -1, f)
    once, rep = _sections(code)
    assert once and abs(once[0] - 8432) < 400  # 16-unit header on the lead frame
    lead_data = _pairs(once, start=2, count=16)
    rep_data = _pairs(rep, start=0, count=16)  # repeat frame has no header
    assert _bits_by_gap(lead_data, 1000) == _lsb(d, 8) + _lsb(f, 8)
    assert _bits_by_gap(rep_data, 1000) == _lsb(d, 8) + _lsb(f, 8)


# ── Denon / Sharp (normal frame then inverted-command frame) ──────────────────


@pytest.mark.parametrize("proto,norm,inv", [("Denon", 0, 3), ("Sharp", 1, 2)])
def test_denon_sharp_frames(proto, norm, inv):
    d, f = 2, 10
    code = render(proto, d, -1, f)
    assert abs(code.frequency - 38000) / 38000 < 0.02
    once, rep = _sections(code)
    # once = the normal frame: D:5, F:8, then the 2-bit tag.
    data = _pairs(once, start=0, count=15)
    bits = _bits_by_gap(data, 1200)  # unit 264: short 792, long 1848
    assert _value(list(reversed(bits[0:5]))) == d
    assert _value(list(reversed(bits[5:13]))) == f
    assert _value(list(reversed(bits[13:15]))) == norm
    # repeat carries the inverted-command frame first.
    inv_data = _pairs(rep, start=0, count=15)
    inv_bits = _bits_by_gap(inv_data, 1200)
    assert _value(list(reversed(inv_bits[5:13]))) == (~f) & 0xFF
    assert _value(list(reversed(inv_bits[13:15]))) == inv


# ── Samsung / RCA ─────────────────────────────────────────────────────────────


def test_samsung20():
    d, s, f = 7, 7, 2
    code = render("Samsung20", d, s, f)
    _once, rep = _sections(code)
    data = _pairs(rep, start=2, count=20)  # D:6 S:6 F:8
    bits = _bits_by_gap(data, 1000)
    assert _value(list(reversed(bits[0:6]))) == d
    assert _value(list(reversed(bits[6:12]))) == s
    assert _value(list(reversed(bits[12:20]))) == f


def test_rca_msb_and_complements():
    d, f = 15, 20
    code = render("RCA", d, -1, f)
    assert abs(code.frequency - 58000) / 58000 < 0.02
    _once, rep = _sections(code)
    data = _pairs(rep, start=2, count=24)  # D:4 F:8 ~D:4 ~F:8, MSB
    bits = _bits_by_gap(data, 1400)  # unit 460: short 920, long 1840
    assert _value(bits[0:4]) == d
    assert _value(bits[4:12]) == f
    assert _value(bits[12:16]) == (~d) & 0x0F
    assert _value(bits[16:24]) == (~f) & 0xFF


# ── frame period / fill ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "proto,d,s,f,period_us",
    [
        ("NEC2", 0, -1, 7, 108_000),
        ("Sony12", 1, -1, 18, 45_000),
        ("RC5", 5, -1, 53, 114_000),
        ("Samsung20", 7, 7, 2, 100_000),
    ],
)
def test_frame_period_fill(proto, d, s, f, period_us):
    code = render(proto, d, s, f)
    _once, rep = _sections(code)
    assert abs(sum(rep) - period_us) < period_us * 0.03


# ── canonical output / round-trip / registry ──────────────────────────────────


def test_all_supported_render_and_reparse():
    # Every supported protocol produces valid, self-consistent Pronto.
    for proto in ["NEC1", "NEC2", "NECx1", "NECx2", "Pioneer", "Sony12", "Sony15",
                  "Sony20", "RC5", "RC6", "Panasonic", "Panasonic_Old", "JVC",
                  "Denon", "Sharp", "Samsung20", "Samsung36", "RCA"]:
        pronto = render_pronto(proto, 3, 5, 10)
        reparsed = parse_pronto(pronto)  # validates the burst-count header
        assert reparsed.frequency > 0
        assert len(reparsed.bursts) % 2 == 0


def test_alternate_decode_suffix_is_stripped():
    assert is_supported("Sharp{2}")
    assert render_pronto("Sharp{2}", 1, -1, 5) == render_pronto("Sharp", 1, -1, 5)


def test_case_insensitive():
    assert render_pronto("sony12", 1, -1, 1) == render_pronto("SONY12", 1, -1, 1)


def test_unsupported_protocol_raises():
    assert not is_supported("RCA-38")  # a genuinely different parameterization
    with pytest.raises(UnsupportedProtocolError):
        render("RCA-38", 1, -1, 1)
    with pytest.raises(UnsupportedProtocolError):
        render("TotallyMadeUp", 1, -1, 1)


def test_supported_protocols_listed():
    names = supported_protocols()
    assert "sony12" in names and "nec1" in names and "rc6" in names
