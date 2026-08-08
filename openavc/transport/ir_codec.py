"""
Infrared code representation and Pronto-hex conversion.

Pure functions with no transport or vendor logic — the platform's universal IR
code representation, usable by any IR bridge driver and the code-set editor.

An IR code is stored canonically as **Pronto hex** (the de-facto vendor-neutral
interchange format: what third-party databases export and what integrators paste
from remote-code sites). A bridge driver converts an ``IRCode`` to and from its
own wire format at emit / learn time; the platform never sees a wire format.

Both Pronto burst values and most emitter wire formats express durations as a
**count of carrier-frequency periods**, so converting between them is a matter of
the carrier frequency and the preamble/repeat split — the durations themselves
pass through unchanged.

Pronto "learned" format (first word ``0000``):

    word0 = 0x0000            raw/oscillated code with a real carrier
    word1 = frequency divisor N; carrier_Hz = 1_000_000 / (N * 0.241246)
    word2 = burst-pair count of the once/lead-in sequence
    word3 = burst-pair count of the repeat sequence
    then 2*(word2+word3) burst values, each a count of carrier periods

No external dependencies.
"""

from __future__ import annotations

from typing import NamedTuple

# Period of the Pronto reference oscillator, in microseconds. carrier_Hz =
# 1_000_000 / (word * PRONTO_CLOCK_US); e.g. word 0x006D (109) -> ~38 kHz.
PRONTO_CLOCK_US = 0.241246


class IRCode(NamedTuple):
    """A vendor-neutral infrared code.

    Attributes:
        frequency: Carrier frequency in Hz.
        bursts: Alternating on/off durations, each a count of carrier-frequency
            periods (the same unit Pronto and most emitter wire formats use).
            Always an even number of values (on/off pairs).
        repeat_offset: 0-based index into ``bursts`` where the repeating body
            starts; the values before it are the preamble sent once. 0 means the
            whole sequence repeats (no distinct preamble). Always even.
    """

    frequency: int
    bursts: tuple[int, ...]
    repeat_offset: int = 0


def pronto_word_to_frequency(word: int) -> int:
    """Convert a Pronto frequency word to a carrier frequency in Hz."""
    if word <= 0:
        raise ValueError(f"Pronto frequency word must be positive, got {word}")
    return round(1_000_000 / (word * PRONTO_CLOCK_US))


def frequency_to_pronto_word(frequency: int) -> int:
    """Convert a carrier frequency in Hz to a Pronto frequency word.

    The result is quantized to the Pronto oscillator grid, so a round trip
    through Pronto may shift the frequency by a fraction of a percent — well
    within IR receiver tolerance.
    """
    if frequency <= 0:
        raise ValueError(f"Carrier frequency must be positive, got {frequency}")
    return round(1_000_000 / (frequency * PRONTO_CLOCK_US))


def parse_pronto(text: str) -> IRCode:
    """Parse a Pronto-hex string into an :class:`IRCode`.

    Accepts whitespace-separated 4-hex-digit words, case-insensitive. Only the
    learned/raw format (leading ``0000``) carries emittable timing; a preset
    format (e.g. ``0100``) references an external codebook and is rejected.

    Raises:
        ValueError: if the string is malformed, not the learned format, or the
            burst-count header does not match the number of values.
    """
    try:
        words = [int(tok, 16) for tok in text.split()]
    except ValueError as e:
        raise ValueError(f"Invalid Pronto hex: {e}") from None
    if len(words) < 4:
        raise ValueError("Pronto code too short (need at least 4 words)")
    if any(w < 0 or w > 0xFFFF for w in words):
        raise ValueError("Pronto words must be 16-bit (0000-FFFF)")
    if words[0] != 0x0000:
        raise ValueError(
            f"Only learned Pronto codes (leading 0000) are supported, got "
            f"{words[0]:04X}"
        )
    once_pairs, repeat_pairs = words[2], words[3]
    bursts = words[4:]
    expected = 2 * (once_pairs + repeat_pairs)
    if len(bursts) != expected:
        raise ValueError(
            f"Pronto burst count mismatch: header declares {once_pairs}+"
            f"{repeat_pairs} pairs ({expected} values) but found {len(bursts)}"
        )
    return IRCode(
        frequency=pronto_word_to_frequency(words[1]),
        bursts=tuple(bursts),
        repeat_offset=2 * once_pairs,
    )


def build_pronto(code: IRCode) -> str:
    """Render an :class:`IRCode` as a normalized Pronto-hex string.

    Words are 4 uppercase hex digits, space-separated.
    """
    if len(code.bursts) % 2 != 0:
        raise ValueError("IRCode bursts must be an even number of values")
    if code.repeat_offset % 2 != 0 or not 0 <= code.repeat_offset <= len(code.bursts):
        raise ValueError(f"Invalid repeat_offset {code.repeat_offset}")
    once_pairs = code.repeat_offset // 2
    repeat_pairs = (len(code.bursts) - code.repeat_offset) // 2
    words = [0x0000, frequency_to_pronto_word(code.frequency), once_pairs, repeat_pairs]
    words.extend(code.bursts)
    return " ".join(f"{w:04X}" for w in words)


def normalize_pronto(text: str) -> str:
    """Validate and canonicalize a Pronto-hex string (parse then re-render)."""
    return build_pronto(parse_pronto(text))
