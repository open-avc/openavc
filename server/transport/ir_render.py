"""
Vendor-neutral infrared protocol renderer.

Turns a compact ``(protocol, device, subdevice, function)`` code — the notation
external IR databases use to store remotes space-efficiently — into an emittable
:class:`~server.transport.ir_codec.IRCode`, then Pronto hex. Pure functions:
no transport, no vendor wire formats, no network. Reusable by any IR feature.

Each protocol is expressed the way its published timing spec defines it: a
carrier frequency, a *unit* time, a per-bit on/off encoding, and a frame layout.
Durations are built up in microseconds and converted to the carrier-period
counts Pronto stores only at the very end, in :func:`_counts`. The frequency the
Pronto word encodes may differ from the nominal carrier by a fraction of a
percent (the Pronto oscillator is quantized); that is well within IR receiver
tolerance and does not change which bits a device decodes.

A code that uses a protocol this module does not implement raises
:class:`UnsupportedProtocolError` — the caller reports the protocol name so the
integrator can fall back to learning the code from the physical remote.

No external dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from server.transport.ir_codec import IRCode, build_pronto


class UnsupportedProtocolError(ValueError):
    """Raised for a protocol name the renderer does not implement."""


# ── pulse-train builder ───────────────────────────────────────────────────────
#
# A frame is accumulated as a list of signed microsecond durations: positive is
# a carrier flash (mark), negative is a gap (space). Bits are emitted through a
# per-'0'/'1' encoding expressed in *units* (the protocol's base time). MSB- or
# LSB-first bit order is a per-frame property. ``fill()`` implements the IRP
# "^Nm" frame-period notation: it stretches the trailing gap so the whole frame
# (since the last fill/mark reset) lasts a fixed total.


class _Frame:
    def __init__(
        self,
        freq_hz: int,
        unit_us: float,
        zero: Sequence[int],
        one: Sequence[int],
        msb: bool = False,
    ) -> None:
        self.freq_hz = freq_hz
        self.unit = unit_us
        self._zero = tuple(zero)
        self._one = tuple(one)
        self.msb = msb
        self.stream: list[float] = []
        self._mark = 0  # start of the current frame, for fill()

    def flash(self, units: float) -> None:
        self.stream.append(+units * self.unit)

    def gap(self, units: float) -> None:
        self.stream.append(-units * self.unit)

    def flash_us(self, us: float) -> None:
        self.stream.append(+us)

    def gap_us(self, us: float) -> None:
        self.stream.append(-us)

    def bit(self, b: int) -> None:
        for d in self._one if b else self._zero:
            self.stream.append(d * self.unit)

    def bits(self, value: int, n: int, msb: bool | None = None) -> None:
        order = self.msb if msb is None else msb
        indices = range(n - 1, -1, -1) if order else range(n)
        for i in indices:
            self.bit((value >> i) & 1)

    def fill(self, total_us: float) -> None:
        """Stretch the trailing gap so this frame lasts ``total_us`` (IRP ^Nm)."""
        used = sum(abs(d) for d in self.stream[self._mark :])
        self.gap_us(total_us - used)
        self._mark = len(self.stream)


def _counts(stream: list[float], freq_hz: int) -> tuple[int, ...]:
    """Merge same-sign runs and quantize to carrier-period counts."""
    merged: list[float] = []
    for d in stream:
        if d == 0:
            continue
        if merged and (merged[-1] > 0) == (d > 0):
            merged[-1] += d
        else:
            merged.append(d)
    if not merged:
        raise ValueError("empty IR frame")
    if merged[0] < 0:
        raise ValueError("IR frame must start with a flash")
    counts = [max(1, round(abs(d) * freq_hz / 1_000_000)) for d in merged]
    if len(counts) % 2:  # every frame ends in a gap; guard anyway
        counts.append(1)
    return tuple(counts)


def _code(freq_hz: int, once: list[float], repeat: list[float]) -> IRCode:
    once_c = _counts(once, freq_hz) if once else ()
    repeat_c = _counts(repeat, freq_hz) if repeat else ()
    return IRCode(
        frequency=freq_hz,
        bursts=once_c + repeat_c,
        repeat_offset=len(once_c),
    )


def _default_sub(subdevice: int, device: int) -> int:
    """Most NEC-family protocols default an unspecified subdevice to ~device."""
    return subdevice if subdevice >= 0 else (255 - (device & 0xFF))


# ── NEC family (pulse distance: 1 unit mark; 1-unit gap = 0, 3-unit gap = 1) ──


def _nec_frame(
    fr: _Frame, device: int, sub: int, function: int, header_on: int, header_off: int
) -> None:
    fr.flash(header_on)
    fr.gap(header_off)
    fr.bits(device & 0xFF, 8)
    fr.bits(_default_sub(sub, device) & 0xFF, 8)
    fr.bits(function & 0xFF, 8)
    fr.bits((~function) & 0xFF, 8)
    fr.flash(1)  # stop bit
    fr.fill(108_000)


def _nec_variant(
    device: int,
    sub: int,
    function: int,
    *,
    freq: int = 38400,
    header_on: int = 16,
    header_off: int = 8,
    ditto: bool = False,
) -> IRCode:
    unit, zero, one = 564, (1, -1), (1, -3)
    main = _Frame(freq, unit, zero, one)
    _nec_frame(main, device, sub, function, header_on, header_off)
    if not ditto:
        # Whole frame repeats on hold (once section empty).
        return _code(freq, [], main.stream)
    # Lead-in frame once, then a short "ditto" frame on hold.
    rep = _Frame(freq, unit, zero, one)
    rep.flash(header_on)
    rep.gap(4)
    rep.flash(1)
    rep.fill(108_000)
    return _code(freq, main.stream, rep.stream)


def _render_nec1(d: int, s: int, f: int) -> IRCode:
    return _nec_variant(d, s, f, header_on=16, header_off=8, ditto=True)


def _render_nec2(d: int, s: int, f: int) -> IRCode:
    return _nec_variant(d, s, f, header_on=16, header_off=8, ditto=False)


def _render_necx2(d: int, s: int, f: int) -> IRCode:
    # Samsung-style NEC: 4.5 ms / 4.5 ms header, whole frame repeats.
    return _nec_variant(d, s, f, header_on=8, header_off=8, ditto=False)


def _render_necx1(d: int, s: int, f: int) -> IRCode:
    freq, unit, zero, one = 38400, 564, (1, -1), (1, -3)
    main = _Frame(freq, unit, zero, one)
    _nec_frame(main, d, s, f, 8, 8)
    rep = _Frame(freq, unit, zero, one)
    rep.flash(8)
    rep.gap(8)
    rep.bit((~d) & 1)  # ~D:1
    rep.flash(1)
    rep.fill(108_000)
    return _code(freq, main.stream, rep.stream)


def _render_pioneer(d: int, s: int, f: int) -> IRCode:
    # NEC framing at a 40 kHz carrier.
    return _nec_variant(d, s, f, freq=40000, header_on=16, header_off=8, ditto=False)


# ── Sony SIRC (pulse width: 1-unit mark = 0, 2-unit mark = 1; 1-unit gaps) ────


def _sony(d: int, s: int, f: int, *, dev_bits: int, use_sub: bool) -> IRCode:
    freq, unit = 40000, 600
    fr = _Frame(freq, unit, zero=(1, -1), one=(2, -1))
    fr.flash(4)  # header
    fr.gap(1)
    fr.bits(f & 0x7F, 7)
    fr.bits(d & ((1 << dev_bits) - 1), dev_bits)
    if use_sub:
        fr.bits((s if s >= 0 else 0) & 0xFF, 8)
    fr.fill(45_000)
    return _code(freq, [], fr.stream)


def _render_sony12(d: int, s: int, f: int) -> IRCode:
    return _sony(d, s, f, dev_bits=5, use_sub=False)


def _render_sony15(d: int, s: int, f: int) -> IRCode:
    return _sony(d, s, f, dev_bits=8, use_sub=False)


def _render_sony20(d: int, s: int, f: int) -> IRCode:
    return _sony(d, s, f, dev_bits=5, use_sub=True)


# ── Philips RC5 / RC6 (bi-phase / Manchester) ─────────────────────────────────


def _render_rc5(d: int, s: int, f: int) -> IRCode:
    # {36k,msb,889} start '1', ~F:1:6 (7th command bit, inverted), T=0, D:5, F:6.
    freq, unit = 36000, 889
    fr = _Frame(freq, unit, zero=(1, -1), one=(-1, 1), msb=True)
    fr.flash(1)  # leading start-bit mark
    fr.bit(((~f) >> 6) & 1)  # field bit = inverted command bit 6 (extends F to 127)
    fr.bit(0)  # toggle
    fr.bits(d & 0x1F, 5)
    fr.bits(f & 0x3F, 6)
    fr.fill(114_000)
    return _code(freq, [], fr.stream)


def _render_rc6(d: int, s: int, f: int) -> IRCode:
    # {36k,444,msb} leader 6,-2; start '1'; mode 0:3; double-wide toggle; D:8; F:8.
    freq, unit = 36000, 444
    fr = _Frame(freq, unit, zero=(-1, 1), one=(1, -1), msb=True)
    fr.flash(6)  # leader
    fr.gap(2)
    fr.bit(1)  # start bit
    for _ in range(3):
        fr.bit(0)  # mode 0
    # Toggle bit is double-width bi-phase; T=0 -> gap 2u, flash 2u.
    fr.gap(2)
    fr.flash(2)
    fr.bits(d & 0xFF, 8)
    fr.bits(f & 0xFF, 8)
    fr.fill(107_000)
    return _code(freq, [], fr.stream)


# ── Panasonic / Kaseikyo, and the older Panasonic ─────────────────────────────


def _render_panasonic(d: int, s: int, f: int) -> IRCode:
    # {37k,432} header 8,-4; bytes 02 20 D S F (D^S^F); stop; 173-unit lead-out.
    freq, unit = 37000, 432
    fr = _Frame(freq, unit, zero=(1, -1), one=(1, -3))
    fr.flash(8)
    fr.gap(4)
    dd, ss, ff = d & 0xFF, (s if s >= 0 else 0) & 0xFF, f & 0xFF
    for byte in (0x02, 0x20, dd, ss, ff, (dd ^ ss ^ ff) & 0xFF):
        fr.bits(byte, 8)
    fr.flash(1)
    fr.gap(173)
    return _code(freq, [], fr.stream)


def _render_panasonic_old(d: int, s: int, f: int) -> IRCode:
    # {57.6k,833} header 4,-4; D:5 F:6 ~D:5 ~F:6; stop; 44 ms lead-out.
    freq, unit = 57600, 833
    fr = _Frame(freq, unit, zero=(1, -1), one=(1, -3))
    fr.flash(4)
    fr.gap(4)
    fr.bits(d & 0x1F, 5)
    fr.bits(f & 0x3F, 6)
    fr.bits((~d) & 0x1F, 5)
    fr.bits((~f) & 0x3F, 6)
    fr.flash(1)
    fr.gap_us(44_000)
    return _code(freq, [], fr.stream)


# ── JVC (header only on the first frame; bare D/F frames repeat) ──────────────


def _render_jvc(d: int, s: int, f: int) -> IRCode:
    freq, unit, zero, one = 37900, 527, (1, -1), (1, -3)
    lead = _Frame(freq, unit, zero, one)
    lead.flash(16)
    lead.gap(8)
    lead.bits(d & 0xFF, 8)
    lead.bits(f & 0xFF, 8)
    lead.flash(1)
    lead.fill(59_080)
    rep = _Frame(freq, unit, zero, one)
    rep.bits(d & 0xFF, 8)
    rep.bits(f & 0xFF, 8)
    rep.flash(1)
    rep.fill(46_420)
    return _code(freq, lead.stream, rep.stream)


# ── Denon / Sharp (a normal frame followed by an inverted-command frame) ──────


def _denon_sharp(d: int, s: int, f: int, *, normal: int, invert: int) -> IRCode:
    freq, unit, zero, one = 38000, 264, (1, -3), (1, -7)

    def normal_frame(fr: _Frame) -> None:
        fr.bits(d & 0x1F, 5)
        fr.bits(f & 0xFF, 8)
        fr.bits(normal, 2)
        fr.flash(1)
        fr.fill(67_000)

    def invert_frame(fr: _Frame) -> None:
        fr.bits(d & 0x1F, 5)
        fr.bits((~f) & 0xFF, 8)
        fr.bits(invert, 2)
        fr.flash(1)
        fr.fill(67_000)

    lead = _Frame(freq, unit, zero, one)
    normal_frame(lead)
    rep = _Frame(freq, unit, zero, one)
    invert_frame(rep)
    normal_frame(rep)
    return _code(freq, lead.stream, rep.stream)


def _render_denon(d: int, s: int, f: int) -> IRCode:
    return _denon_sharp(d, s, f, normal=0, invert=3)


def _render_sharp(d: int, s: int, f: int) -> IRCode:
    return _denon_sharp(d, s, f, normal=1, invert=2)


# ── Samsung (older 20-bit and 36-bit variants; most Samsung TVs are NECx2) ────


def _render_samsung20(d: int, s: int, f: int) -> IRCode:
    freq, unit = 38400, 564
    fr = _Frame(freq, unit, zero=(1, -1), one=(1, -3))
    fr.flash(8)
    fr.gap(8)
    fr.bits(d & 0x3F, 6)
    fr.bits((s if s >= 0 else 0) & 0x3F, 6)
    fr.bits(f & 0xFF, 8)
    fr.flash(1)
    fr.fill(100_000)
    return _code(freq, [], fr.stream)


def _render_samsung36(d: int, s: int, f: int) -> IRCode:
    freq, unit = 37900, 560
    fr = _Frame(freq, unit, zero=(1, -1), one=(1, -3))
    fr.flash_us(4500)
    fr.gap_us(4500)
    fr.bits(d & 0xFF, 8)
    fr.bits((s if s >= 0 else 0) & 0xFF, 8)
    fr.flash(1)
    fr.gap(9)
    fr.bits(0, 4)  # extension nibble (not carried by the database notation)
    fr.bits(f & 0xFF, 8)
    fr.bits((~f) & 0xFF, 8)
    fr.flash(1)
    fr.fill(108_000)
    return _code(freq, [], fr.stream)


# ── RCA (MSB, 58 kHz) ─────────────────────────────────────────────────────────


def _render_rca(d: int, s: int, f: int) -> IRCode:
    freq, unit = 58000, 460
    fr = _Frame(freq, unit, zero=(1, -2), one=(1, -4), msb=True)
    fr.flash(8)
    fr.gap(8)
    fr.bits(d & 0x0F, 4)
    fr.bits(f & 0xFF, 8)
    fr.bits((~d) & 0x0F, 4)
    fr.bits((~f) & 0xFF, 8)
    fr.flash(1)
    fr.gap(16)
    return _code(freq, [], fr.stream)


# ── registry ──────────────────────────────────────────────────────────────────
#
# Keys are lowercased. A trailing "{n}" alternate-decode marker (e.g. "sharp{2}")
# is stripped before lookup — those denote the same signal. Hyphen-suffixed names
# (e.g. "rca-38", "nec1-y1") are genuinely different parameterizations and are
# intentionally NOT aliased; they fall through to UnsupportedProtocolError.

_RENDERERS: dict[str, Callable[[int, int, int], IRCode]] = {
    "nec": _render_nec1,
    "nec1": _render_nec1,
    "nec2": _render_nec2,
    "necx": _render_necx2,
    "necx1": _render_necx1,
    "necx2": _render_necx2,
    "pioneer": _render_pioneer,
    "sony12": _render_sony12,
    "sony15": _render_sony15,
    "sony20": _render_sony20,
    "rc5": _render_rc5,
    "rc6": _render_rc6,
    "panasonic": _render_panasonic,
    "kaseikyo": _render_panasonic,
    "panasonic_old": _render_panasonic_old,
    "panasonicold": _render_panasonic_old,
    "jvc": _render_jvc,
    "denon": _render_denon,
    "sharp": _render_sharp,
    "samsung20": _render_samsung20,
    "samsung36": _render_samsung36,
    "rca": _render_rca,
}


def _normalize(protocol: str) -> str:
    name = protocol.strip().strip("\"'").lower()
    if name.endswith("}") and "{" in name:
        name = name[: name.rindex("{")]
    return name


def is_supported(protocol: str) -> bool:
    """Whether :func:`render` can produce a code for this protocol name."""
    return _normalize(protocol) in _RENDERERS


def supported_protocols() -> list[str]:
    """Sorted canonical protocol names the renderer implements."""
    return sorted({fn.__name__[8:] for fn in _RENDERERS.values()})


def render(protocol: str, device: int, subdevice: int, function: int) -> IRCode:
    """Render a database code to a vendor-neutral :class:`IRCode`.

    Args:
        protocol: The database protocol name (e.g. ``"NECx2"``, ``"Sony12"``).
        device: Device (address) number, 0-255.
        subdevice: Subdevice number, or -1 when the database leaves it
            unspecified (the protocol's own default is then applied).
        function: Function (command) number, 0-255.

    Raises:
        UnsupportedProtocolError: if the protocol is not implemented.
        ValueError: if a parameter is out of range for the protocol.
    """
    renderer = _RENDERERS.get(_normalize(protocol))
    if renderer is None:
        raise UnsupportedProtocolError(
            f"IR protocol '{protocol}' is not supported by the renderer"
        )
    device, subdevice, function = int(device), int(subdevice), int(function)
    if function < 0:
        raise ValueError(f"function must be >= 0, got {function}")
    if device < 0:
        raise ValueError(f"device must be >= 0, got {device}")
    return renderer(device, subdevice, function)


def render_pronto(protocol: str, device: int, subdevice: int, function: int) -> str:
    """Render a database code straight to canonical Pronto hex."""
    return build_pronto(render(protocol, device, subdevice, function))
