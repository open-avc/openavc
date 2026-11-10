"""Shared pure helpers for the protocol interpreters.

"Interpret a driver's protocol" happens in three places: the driver runtime
(``openavc.drivers.configurable``), the auto-generated device simulator
(``openavc.simulator.yaml_auto``), and the driver/simulator validator
(``openavc.simulator.validate``). The helpers here are the pieces those interpreters
must agree on byte-for-byte — placeholder substitution, a declared state
variable's default, reading a device's JSON, value coercion and conversion, a
value's map word, send_frame packet framing, and delimiter decoding. Each
interpreter used to carry its own copy, and a copy that drifts shows up as a
simulator that answers a command differently than the real device would — so
they live here once instead.

``compile_driver()`` turns a driver definition's receive side into a
``CompiledProtocol`` — the pre-compiled response tables the runtime matches
incoming data against. The other interpreters reason about the same rules,
so the compile lives here with the helpers rather than inside the runtime.
What a rule's ``child_set:`` and ``child_list:`` compile to, in each wire
form, is ``openavc.drivers.child_routes``, which the compile calls; what a json
rule's value spec compiles to, in a flat ``set:`` and in a child alike, is
``openavc.drivers.json_value_spec``, which both call.

What only the simulator and its validator need lives beside them in
``openavc.simulator``: the reply text a response pattern matches
(``reply_templates``), a send template read backwards to the line it sends
(``send_templates``, the twin of this module's substitution), what a command's
name says when it declares nothing (``command_names``), and the then_query
and read_back queries the simulator answers (``reread_queries``).

Pure on purpose: stdlib plus ``openavc.transport.binary_helpers``, the driver
contract's tables (``openavc.drivers.spec``, directly and through ``child_routes``),
``json_value_spec`` and the logger only — the same contract as
``openavc.drivers.inline_protocol``, and for the same reason: the simulator
runs as a separate process and imports this
module directly, so it must not pull in the driver runtime or transport stack
(``tests/test_compiled_protocol_purity.py``).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from openavc.drivers.child_routes import (
    compile_child_list,
    compile_child_set,
    compile_json_child_set,
    compile_osc_child_set,
)
from openavc.drivers.json_value_spec import FLAT, json_value_mapping
from openavc.drivers.spec import CONVERSION_KEYS as CONVERSION_KEYS
from openavc.transport.binary_helpers import (
    DEFAULT_MAX_BUFFER,
    encode_escape_sequences,
    pack_length_prefix,
)
from openavc.utils.logger import get_logger

log = get_logger(__name__)


# ── Placeholder substitution ──


#: A {name} or {name:spec} placeholder, the token ``safe_substitute`` fills;
#: the spec excludes braces so it can't span tokens.
_SUBSTITUTION_RE = re.compile(r"\{(\w+)(?::([^{}]*))?\}")


def safe_substitute(template: str, params: dict[str, Any]) -> str:
    """
    Substitute {name} and {name:spec} placeholders with values from params.

    Only replaces {name} where name is a key in params. An optional
    ``:format_spec`` (Python format-spec mini-language) formats the value —
    e.g. ``{preset:02d}`` zero-pads and ``{addr:04X}`` hex-formats, both
    common in device protocols. A numeric spec applied to a numeric string
    coerces it first, so a param that arrives as ``"5"`` still pads to
    ``"05"``. Literal JSON braces and unknown placeholders are left
    untouched, and an invalid spec leaves the placeholder verbatim rather
    than raising — this avoids the problem with Python's str.format()
    choking on JSON body strings.
    """
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        if key not in params:
            return match.group(0)  # Leave unmatched {name} as-is
        spec = match.group(2)
        if not spec:
            return str(params[key])
        formatted = _formatted(params[key], spec)
        # Unformattable — leave the placeholder verbatim so a bad spec
        # is visible to the author instead of crashing the send.
        return match.group(0) if formatted is None else formatted

    return _SUBSTITUTION_RE.sub(replacer, template)


def _formatted(value: Any, spec: str) -> str | None:
    """``value`` formatted by ``spec``, else the first number it stands for
    that the spec takes (``_spec_retries``); None when none does. The retries
    run while the first refusal is being handled, so anything one of them
    raises is chained to it."""
    try:
        return format(value, spec)
    except (ValueError, TypeError):
        return _first_retry_taken(value, spec)


def _first_retry_taken(value: Any, spec: str) -> str | None:
    """The first of ``_spec_retries(value)`` that ``spec`` formats, or None."""
    for number in _spec_retries(value):
        try:
            return format(number, spec)
        except (ValueError, TypeError):
            continue
    return None


def _spec_retries(value: Any) -> Iterator[Any]:
    """What a format spec that refused ``value`` is tried with next, in order.

    An integer spec ('d'/'x'/'X'/'o'/'b') rejects a float even when it's
    whole (26.0), which is exactly what a scaled slider value is, so a
    whole-number float is tried as an int and {vol:d} renders "26". A numeric
    spec applied to a numeric string is tried with it as an int, then as a
    float, so '02d' works on "5". A conversion that fails is skipped."""
    if isinstance(value, float) and value.is_integer():
        conversions: tuple[Any, ...] = (int,)
    elif isinstance(value, str):
        conversions = (int, float)
    else:
        return
    for conv in conversions:
        try:
            number = conv(value)
        except (ValueError, TypeError):
            continue
        yield number


def _placeholder_names(template: str) -> list[str]:
    """The name in each placeholder ``safe_substitute`` would fill in
    ``template``, in order, repeats kept."""
    return [m.group(1) for m in _SUBSTITUTION_RE.finditer(template)]


def derive_config(config: dict[str, Any], derived: Any) -> None:
    """Populate ``config`` in place with values derived from other fields.

    ``derived`` is the driver's optional top-level ``config_derived`` map of
    ``{name: template}``. Each template is substituted against config; if any
    ``{field}`` it references resolves to an empty/missing value, the derived
    value is ``""`` — so an optional prefixed segment simply disappears.
    Entries are processed in declaration order, so a later template may
    reference an earlier derived name.
    """
    if not isinstance(derived, dict):
        return
    for name, template in derived.items():
        if not isinstance(template, str):
            continue
        refs = _placeholder_names(template)
        if any(not str(config.get(f, "") or "").strip() for f in refs):
            config[name] = ""
        else:
            config[name] = safe_substitute(template, config)


# ── Declared state-variable defaults ──


def _min_as_number(var_def: dict[str, Any]) -> float | None:
    """A state variable's declared ``min`` as a float, or None if it isn't a
    number. Booleans are not numbers here: ``min: true`` is an authoring
    mistake, and ``float(True)`` would quietly seed 1."""
    raw = var_def.get("min")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def state_var_default(var_def: dict[str, Any]) -> Any:
    """The value a declared state variable would hold, for a consumer that has
    to produce one.

    One rule for every consumer that needs it: the auto-generated simulator
    seeding the wire values it will serve, the validator working out what a
    variable would hold, and the platform clearing a stale ``last_error`` back
    to the shape its driver's own seeding produced. They used to agree by hand
    and had drifted three ways.

    An ``integer`` variable with a fractional ``min`` rounds UP, so the value is
    never below the minimum the driver declared; a non-numeric ``min`` is an
    authoring bug that falls back to 0 rather than crashing.

    **The driver runtime is deliberately not a caller.** It used to be, through
    a ``number_from_min`` flag that made a fader declaring ``min: -80`` start at
    -80.0 — a value nothing downstream could tell from a reading. A device that
    has not spoken has no reading, so the runtime records ``None`` and every
    consumer that judges a reading already knows what that means. See
    ``BaseDriver._default_for_var_def``.
    """
    var_type = var_def.get("type", "string") if isinstance(var_def, dict) else "string"
    if var_type == "boolean":
        return False
    if var_type == "integer":
        min_num = _min_as_number(var_def)
        if min_num is None:
            _warn_unusable_min(var_def)
            return 0
        return math.ceil(min_num)
    if var_type in ("number", "float"):
        # 'float' is an accepted type alias for 'number' (driver loader +
        # schema). Both give a numeric 0.0, not '' — a consumer reading the
        # var must not get a string where a number is expected.
        #
        # Unlike the integer branch above, this one does not consult `min`: a
        # simulator seeds the WIRE value it will put on the socket, which is a
        # different namespace from the logical value a driver's response rules
        # map it to. That asymmetry predates the runtime leaving; it is the
        # behaviour every remaining caller already had.
        return 0.0
    if var_type == "enum":
        values = var_def.get("values", [])
        return values[0] if values else ""
    return ""


def _warn_unusable_min(var_def: dict[str, Any]) -> None:
    """Warn only when a ``min`` was actually authored and can't be read.
    A variable with no ``min`` at all is the normal case, not a mistake."""
    if var_def.get("min") is not None:
        log.warning(
            "state variable declares a non-numeric 'min' %r; defaulting to 0",
            var_def.get("min"),
        )


# ── Reading a device's JSON ──


def _put_merging(obj: dict[str, Any], name: str, value: Any) -> None:
    """Put ``value`` under ``name``, merged into an object already held there."""
    held = obj.get(name)
    if isinstance(held, dict) and isinstance(value, dict):
        for inner_name, inner_value in value.items():
            _put_merging(held, inner_name, inner_value)
    else:
        obj[name] = value


def _merge_repeated_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that keeps the values under every copy of a name.

    Some devices bundle several addresses into one reply by repeating a name,
    each copy holding another part: ``{"ch1":{"mute":true},"ch1":{"gain":4}}``.
    Plain ``json.loads`` keeps only the last copy, so the first half of that
    reply would be lost without a word. Two objects under one name are merged,
    recursively, the later copy winning on a name both hold. Every other repeat
    (a scalar, null, an array, an object and a non-object in either order)
    keeps the last value, exactly as ``json.loads`` does, and names stay in
    the order they first appeared. Arrays are never merged: their elements are
    separate values however alike they look.

    The decoder calls this innermost object first, so every value here is
    already a fresh dict with its own repeats merged, and merging into the
    earlier copy in place touches nothing else.
    """
    obj = dict(pairs)
    if len(obj) == len(pairs):
        return obj
    obj = {}
    for name, value in pairs:
        _put_merging(obj, name, value)
    return obj


def parse_json_reply(text: str | bytes) -> Any:
    """Parse JSON a device sent, for the rules that read it.

    ``json.loads``, raising what it raises, except that an object repeating a
    name keeps the values under every copy (``_merge_repeated_names``). A
    reply with no repeated name parses to exactly what ``json.loads`` gives.
    """
    return json.loads(text, object_pairs_hook=_merge_repeated_names)


# ── Value coercion ──
#
# A value the device sent, read as the type its state variable declares: one
# reading per wire form (text, a parsed JSON body, an OSC argument), each a
# ``_WireForm`` row below. The boolean token is the one rule all three share.

_TRUE_WORDS = frozenset({"true", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "no", "off"})


def is_bool_token(raw: Any) -> bool:
    """True when ``coerce_bool_token`` reads ``raw`` rather than defaulting:
    a true/false word or a number. Anything else becomes False unannounced."""
    text = str(raw).strip().lower()
    if text in _TRUE_WORDS or text in _FALSE_WORDS:
        return True
    try:
        float(text)
    except ValueError:
        return False
    return True


def coerce_bool_token(raw: Any) -> bool:
    """Read a device's boolean token.

    Devices spell a flag three ways: a word (``on``), a bare digit (``1``),
    or a fixed-width field (``01``). The padded form is normal in protocols
    whose fields are all the same width, so a numeric token is read by its
    value rather than matched against a list of spellings — matching would
    make ``01`` false, the opposite of what the device said, with nothing
    logged and nothing to notice.
    """
    text = str(raw).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    try:
        return float(text) != 0.0
    except ValueError:
        return False


def _numbers(
    integer: Callable[[Any], Any], real: Callable[[Any], Any]
) -> dict[str, Callable[[Any], Any]]:
    """A wire form's number readers by declared type: ``float`` is an accepted
    alias for ``number`` (driver loader + schema)."""
    return {"integer": integer, "float": real, "number": real}


@dataclass(frozen=True)
class _WireForm:
    """How one wire form's values are read as a state variable's declared
    type. The three forms share the type names and the boolean token, and
    differ on purpose in what each field below holds."""

    #: The reader for each number type.
    numbers: dict[str, Callable[[Any], Any]]
    #: What a number reader raises that the form absorbs; anything else
    #: escapes to the caller.
    absorbs: tuple[type[Exception], ...]
    #: What a number that will not convert becomes, given (value, type).
    unconverted: Callable[[Any, str], Any]
    boolean: Callable[[Any], Any]
    #: A string, an enum, or a type the form has no reader for.
    text: Callable[[Any], Any]


def _coerce(value: Any, value_type: str, form: _WireForm) -> Any:
    """``value`` read as ``value_type`` the way ``form`` reads it. The type is
    compared, never hashed: a hand-written type that is not a name (a list)
    reads as text rather than raising."""
    if value_type == "boolean":
        return form.boolean(value)
    reader = next((read for name, read in form.numbers.items() if value_type == name), None)
    if reader is None:
        return form.text(value)
    try:
        return reader(value)
    except form.absorbs:
        return form.unconverted(value, value_type)


def _kept_and_said(raw: Any, value_type: str) -> Any:
    log.warning("Cannot coerce %r to %s, returning raw string", raw, value_type)
    return raw


#: Text: a regex capture or a static value.
_TEXT = _WireForm(
    numbers=_numbers(int, float),
    absorbs=(ValueError,),
    unconverted=_kept_and_said,
    boolean=coerce_bool_token,
    text=lambda raw: raw,
)


def coerce_value(raw: str, value_type: str) -> Any:
    """Convert a raw string (a regex capture or static value) to the type."""
    return _coerce(raw, value_type, _TEXT)


def _json_integer(value: Any) -> int:
    """A JSON number or numeric string as an int: a float, or a string of
    one, is cut toward zero ("5.7" -> 5)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(float(value))


def _json_text(value: Any) -> str:
    """JSON ``true`` / ``false`` as those words, anything else as ``str``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _text_and_said(value: Any, value_type: str) -> str:
    log.warning("Cannot coerce %r to %s, returning string", value, value_type)
    return str(value)


#: A parsed JSON body: a real JSON bool, int or float is kept as it is,
#: rather than round-tripped through ``str`` (JSON ``true`` would become the
#: string "True").
_JSON = _WireForm(
    numbers=_numbers(_json_integer, float),
    absorbs=(TypeError, ValueError),
    unconverted=_text_and_said,
    boolean=lambda value: value if isinstance(value, bool) else coerce_bool_token(value),
    text=_json_text,
)


def coerce_json_value(value: Any, value_type: str) -> Any:
    """Coerce a native JSON value to the declared state type.

    Unlike ``coerce_value`` (string in), this keeps real JSON bools / ints /
    floats instead of round-tripping through ``str`` (which would turn JSON
    ``true`` into the string ``"True"``). A JSON null stays None.
    """
    if value is None:
        return None
    return _coerce(value, value_type, _JSON)


def _osc_boolean(value: Any) -> bool:
    """An OSC argument as a flag: a bool as it is, a number by its value,
    anything else as a boolean token."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return coerce_bool_token(value)


#: An OSC argument, already typed by its type tag: a number that will not
#: convert is passed on as it came, and nothing is said.
_OSC = _WireForm(
    numbers=_numbers(int, float),
    absorbs=(ValueError, TypeError),
    unconverted=lambda value, _value_type: value,
    boolean=_osc_boolean,
    text=lambda value: str(value) if value is not None else None,
)


def coerce_osc_value(value: Any, value_type: str) -> Any:
    """Convert an already-typed OSC value to the declared state type."""
    return _coerce(value, value_type, _OSC)


# ── Value conversion (scale / offset / unknown) ──
#
# A device that packs a real value into its own number (gain sent as 000-060
# for -18..+42 dB, a fader in hundredths of a dB) declares the conversion on
# the state variable that holds the value and on the command parameter that
# sets it: real = raw * scale + offset. ``unknown`` lists the raw values that
# mean "no reading", stored as None. The runtime, the simulator and the
# validator all convert through these two functions. The keys themselves are
# the contract's (spec.CONVERSION_KEYS), which the validator reads too.


def conversion_of(decl: Any) -> dict[str, Any] | None:
    """The conversion keys a state variable or param declares, or None."""
    if not isinstance(decl, dict):
        return None
    found = {k: decl[k] for k in CONVERSION_KEYS if decl.get(k) is not None}
    return found or None


def _finite(raw: Any) -> float | None:
    """``raw`` as a finite float, or None. Booleans are not numbers here."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        number = float(raw)
    else:
        try:
            number = float(str(raw).strip())
        except ValueError:
            return None
    return number if math.isfinite(number) else None


def _whole(number: float) -> int:
    """Nearest whole number, halves away from zero (not banker's rounding)."""
    return int(math.floor(abs(number) + 0.5)) * (1 if number >= 0 else -1)


def _scale_offset(decl: dict[str, Any]) -> tuple[float, float]:
    scale = _finite(decl.get("scale"))
    offset = _finite(decl.get("offset"))
    return (1.0 if scale is None or scale == 0 else scale), (offset or 0.0)


def is_unknown_reading(raw: Any, decl: Any) -> bool:
    """True when ``raw`` is one of the declaration's ``unknown`` values. A
    number matches by value (``255`` matches ``"0255"``); a string exactly."""
    codes = decl.get("unknown") if isinstance(decl, dict) else None
    if not isinstance(codes, list):
        return False
    text = str(raw).strip()
    number = _finite(raw)
    for code in codes:
        if isinstance(code, str):
            if text == code:
                return True
        elif number is not None and _finite(code) == number:
            return True
    return False


def reading_from_wire(raw: Any, decl: Any, value_type: str) -> tuple[bool, Any]:
    """What a state variable stores for the device's ``raw`` value.

    ``(True, value)`` when the declaration decides it: None for an
    ``unknown`` value, else raw * scale + offset typed by ``value_type``
    (``integer`` rounds to a whole number). ``(False, None)`` when it
    declares nothing for this value, or the value is not a number, and the
    caller's own coercion applies unchanged.
    """
    if not isinstance(decl, dict):
        return False, None
    if is_unknown_reading(raw, decl):
        return True, None
    if decl.get("scale") is None and decl.get("offset") is None:
        return False, None
    number = _finite(raw)
    if number is None:
        return False, None
    scale, offset = _scale_offset(decl)
    # round(): 7760 * 0.01 is 77.60000000000001, and 77.6 is what was meant.
    real = round(number * scale + offset, 9)
    if value_type == "integer":
        return True, _whole(real)
    return True, real


def value_to_wire(value: Any, decl: Any) -> Any:
    """The device's raw number for a real ``value``: (value - offset) / scale,
    rounded to a whole number. None goes out as the first ``unknown`` value
    when one is declared. Anything the declaration does not convert (no
    scale or offset, a value that is not a number) is returned unchanged."""
    if not isinstance(decl, dict):
        return value
    if value is None:
        codes = decl.get("unknown")
        return codes[0] if isinstance(codes, list) and codes else None
    if decl.get("scale") is None and decl.get("offset") is None:
        return value
    number = _finite(value)
    if number is None:
        return value
    scale, offset = _scale_offset(decl)
    return _whole(round((number - offset) / scale, 9))


# ── A value's own word on the wire (map) ──
#
# A device setting or a command parameter may declare ``map``: the word the
# device expects for each value (``{"true": "ON", "false": "OFF"}``). The
# runtime writes the word in place of the value; the simulator reads a
# setting's word back to the value. All of them go through these functions so
# a key matches the same way everywhere.


def _map_key_matches(key: Any, value: Any) -> bool:
    """Does a map key name ``value``? A boolean value matches the key true /
    True / "true" alike (a YAML bare key, a quoted one, the IDE's JSON bool);
    anything else matches on its text, so an unquoted integer key matches."""
    if isinstance(value, bool):
        return str(key).strip().lower() == ("true" if value else "false")
    return str(key) == str(value)


def map_word(value: Any, value_map: Any) -> str | None:
    """The device's word for ``value`` from a setting's or parameter's
    ``map``, as text, or None when the map has no entry for it (the caller
    writes the value as it is)."""
    if not isinstance(value_map, dict):
        return None
    for key, word in value_map.items():
        if _map_key_matches(key, value):
            return str(word)
    return None


def value_for_map_word(word: Any, value_map: Any) -> tuple[bool, Any]:
    """The setting value a device ``word`` stands for: the inverse of
    :func:`map_word`. An exact match wins, then one ignoring case.
    ``(True, key)`` when found (the key as declared; the caller coerces it to
    the state variable's type), ``(False, None)`` when the map has no such
    word."""
    if not isinstance(value_map, dict):
        return False, None
    text = str(word).strip()
    exact = _key_for(value_map, lambda mapped: mapped == text)
    if exact[0]:
        return exact
    folded = text.lower()
    return _key_for(value_map, lambda mapped: mapped.lower() == folded)


def _key_for(value_map: dict[Any, Any], matches: Callable[[str], bool]) -> tuple[bool, Any]:
    """``(True, key)`` for the first entry whose word, as text, ``matches``;
    ``(False, None)`` when none does (a key may itself be None or False)."""
    for key, mapped in value_map.items():
        if matches(str(mapped)):
            return True, key
    return False, None


# ── Delimiter decoding ──


def decode_delimiter(delim: str) -> str:
    """Decode a driver-declared delimiter string to its real characters.

    A YAML double-quoted scalar already carries real control characters
    (``"\\r\\n"`` arrives as CR LF), but a single-quoted or hand-typed value
    arrives as literal backslash escapes instead. Decodes the same escape set
    ``binary_helpers.encode_escape_sequences`` handles (``\\r``, ``\\n``,
    ``\\t``, ``\\\\``, ``\\xHH``) so both spellings mean the same bytes on the
    wire; anything else passes through untouched.
    """

    def _one(m: re.Match) -> str:
        seq = m.group(0)
        mapped = _DELIMITER_ESCAPES.get(seq)
        if mapped is not None:
            return mapped
        # \xHH — the regex guarantees exactly two hex digits (0x00-0xFF).
        return chr(int(seq[2:], 16))

    return re.sub(r"\\(?:r|n|t|\\|x[0-9a-fA-F]{2})", _one, delim)


_DELIMITER_ESCAPES = {"\\r": "\r", "\\n": "\n", "\\t": "\t", "\\\\": "\\"}


# ── send_frame packet framing ──


def build_send_frame(cfg: Any) -> dict[str, Any] | None:
    """Precompute a send_frame block's constant header bytes, or None.

    The header/after_length strings are escape-decoded once here (they carry
    raw bytes like eISCP's ``ISCP\\x00\\x00\\x00\\x10`` magic + header-size).
    Only ``length_prefix`` is supported; any other type is ignored with a
    warning so an unknown block never silently breaks sends.
    """
    if not cfg or not isinstance(cfg, dict):
        return None
    frame_type = cfg.get("type", "length_prefix")
    if frame_type != "length_prefix":
        log.warning(
            "Unsupported send_frame type %r; ignoring (only 'length_prefix')",
            frame_type,
        )
        return None
    return {
        "header": encode_escape_sequences(str(cfg.get("header", "") or "")),
        "after_length": encode_escape_sequences(str(cfg.get("after_length", "") or "")),
        "length_size": int(cfg.get("length_size", 4)),
        "length_endian": "little" if cfg.get("length_endian") == "little" else "big",
    }


def apply_send_frame(sf: dict[str, Any] | None, data: bytes) -> bytes:
    """Wrap an escape-decoded byte-stream payload in the send_frame header.

    No-op when ``sf`` is None. The data-length field is computed from
    ``len(data)`` per message — the piece a static command_prefix can't
    express. Output is ``header + packed_length + after_length + data``.
    """
    if not sf:
        return data
    length = pack_length_prefix(len(data), sf["length_size"], sf["length_endian"])
    return sf["header"] + length + sf["after_length"] + data


def split_send_frames(
    sf: dict[str, Any],
    buffer: bytearray,
    max_buffer: int = DEFAULT_MAX_BUFFER,
) -> list[bytes]:
    """Strip complete send_frame packets off the front of ``buffer``.

    Consumes each complete frame (fixed header + computed-length body) from
    the buffer in place and returns the bare bodies, leaving any partial
    trailing frame for the next read — exactly what the receive side's
    length-prefix frame parser does on the other end of the wire.

    ``max_buffer`` bounds a desync the same way that parser does, and for the
    same reason: the buffer is per-connection and persistent, so a claimed
    frame size that can never be assembled would otherwise pin the stream
    forever. A length-prefixed stream has no in-band resync point, so the
    only honest recovery is to drop what's there and start clean.
    """
    header_len = len(sf["header"])
    length_size = sf["length_size"]
    total_header = header_len + length_size + len(sf["after_length"])
    messages: list[bytes] = []
    while len(buffer) >= total_header:
        data_len = int.from_bytes(
            buffer[header_len : header_len + length_size], sf["length_endian"]
        )
        total = total_header + data_len
        if total > max_buffer:
            log.warning(
                "send_frame split: claimed frame size %d exceeds max %d; "
                "clearing desynced buffer",
                total, max_buffer,
            )
            del buffer[:]
            break
        if len(buffer) < total:
            break
        messages.append(bytes(buffer[total_header:total]))
        del buffer[:total]
    # Same defensive symmetry the receive-side parser keeps: a partial frame
    # whose payload never completes must not be retained past the cap.
    if len(buffer) > max_buffer:
        log.warning(
            "send_frame split: buffer overflow (%d bytes), clearing",
            len(buffer),
        )
        del buffer[:]
    return messages


# ── Receive-side compile ──


#: A regex rule: pattern, flat mappings, child mappings (``child_set`` entries,
#: then a ``child_list``), throttle, ``only_when``, ``then_query``.
RegexRule = tuple[
    re.Pattern[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[Any] | None,
]
#: An OSC rule: address, flat mappings, child mappings, throttle, ``only_when``.
OscRule = tuple[
    str,
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
]
#: A JSON-body rule: flat mappings, throttle, ``require`` keys, ``only_when``,
#: child mappings.
JsonRule = tuple[
    list[dict[str, Any]],
    dict[str, Any] | None,
    tuple[str, ...],
    dict[str, Any] | None,
    list[dict[str, Any]],
]


@dataclass
class CompiledProtocol:
    """The pre-compiled receive side of one driver instance.

    Three separate tables:

    1. ``responses`` — regex rules for TCP/serial/UDP/HTTP data
       (pattern, flat state mappings, compiled child_set entries plus a
       compiled child_list entry, throttle)
    2. ``osc_responses`` — OSC address rules (address, mappings, child_set,
       throttle)
    3. ``json_responses`` — JSON-body rules applied together from one parsed
       object (mappings, throttle, require-keys scope, condition, compiled
       child_set entries)

    Plus one view: ``after_json_responses`` holds the same tuple objects as
    the ``responses`` entries whose rule declared ``after_json: true``, in
    authored order. A regex rule normally never sees a body a json rule has
    already read; the flagged ones do, and this is the table the runtime walks
    in that case — so first-match-wins is decided among them alone, and an
    unflagged rule cannot pick up a frame the json rules were meant to own.
    Rules appear in both tables because they are eligible in both situations.

    Every entry carries an optional throttle state ({window, last} or None) —
    a rule with ``throttle: <seconds>`` skips re-fires inside its window
    (drop-style; built for continuous push telemetry like audio level meters,
    where every skipped frame is superseded by the next).

    Every entry also carries its optional ``only_when:`` condition as authored
    (or None). It is not evaluated here — this module is stdlib-only so the
    simulator and the validator can share it, and a condition needs the state
    store, so the runtime is what asks.

    A ``responses`` entry carries one more slot, last: its ``then_query:``
    list as authored (or None), the queries the runtime sends once the rule
    has applied. Sending needs the transport, so it is the runtime's too.

    Compiled per driver INSTANCE, not per driver class: the tables embed the
    instance's config substitutions, and their mapping lists are mutable
    per-instance copies — sharing them across instances would let one
    instance's edits leak into every other instance of the same driver type.
    """

    responses: list[RegexRule] = field(default_factory=list)
    after_json_responses: list[RegexRule] = field(default_factory=list)
    osc_responses: list[OscRule] = field(default_factory=list)
    json_responses: list[JsonRule] = field(default_factory=list)


def rule_condition(resp: dict[str, Any]) -> dict[str, Any] | None:
    """A response entry's optional ``only_when:`` gate, carried through as
    authored.

    Nothing is evaluated here. This module is held to stdlib so the simulator
    and the driver validator can share it, and a condition needs the state
    store; the runtime reads this back and asks the shared evaluator. Anything
    that is not an object is dropped rather than raised on, matching how the
    rest of the compile step treats a hand-installed file: the loader is where
    a malformed driver is reported."""
    raw = resp.get("only_when")
    return raw if isinstance(raw, dict) and raw else None


def rule_follow_ups(resp: dict[str, Any]) -> list[Any] | None:
    """A regex rule's optional ``then_query:`` list, carried through as
    authored: the runtime expands and sends it the way it does a poll entry.
    Anything that is not a non-empty list is dropped, as with ``only_when``."""
    raw = resp.get("then_query")
    return list(raw) if isinstance(raw, list) and raw else None


def build_throttle(resp: dict[str, Any]) -> dict[str, Any] | None:
    """Compile a response entry's optional ``throttle: <seconds>`` into a
    per-instance {window, last} state dict (None when absent/invalid).
    ``last`` maps a throttle scope to its last-fire time. The loader validates
    the value up-front; runtime parsing stays defensive so a hand-installed
    file can't crash the driver."""
    raw = resp.get("throttle")
    if raw is None:
        return None
    try:
        window = float(raw)
    except (TypeError, ValueError):
        log.warning("Invalid response throttle %r ignored", raw)
        return None
    if window <= 0:
        return None
    return {"window": window, "last": {}}


def _json_set_mapping(
    state_key: str, spec: Any, state_variables: dict[str, Any]
) -> dict[str, Any]:
    """One json ``set:`` item as a mapping, typed by its state variable unless
    the spec says otherwise; how a flat item reads the spec is ``FLAT``, which
    leaves no item out, so this is never None."""
    var_def = state_variables.get(state_key, {})
    default_type = (
        var_def.get("type", "string") if isinstance(var_def, dict) else "string"
    )
    return json_value_mapping(state_key, spec, default_type, FLAT)


def build_json_mappings(
    resp: dict[str, Any], state_variables: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build {state, key, type, map} mappings for a ``json: true`` response.

    Accepts the detailed ``mappings`` list or the friendly ``set`` map, the
    ``set`` read only when there are no ``mappings`` (unlike a regex rule,
    ``build_regex_mappings``, which keeps both). In a json rule a ``set`` value
    is a value spec (``openavc.drivers.json_value_spec``), the JSON path to
    read — not a regex capture ref. Types default to the matching state
    variable's declared type.
    """
    mappings: list[dict[str, Any]] = list(resp.get("mappings", []))
    set_map = resp.get("set")
    if not mappings and isinstance(set_map, dict):
        mappings += [
            _json_set_mapping(state_key, spec, state_variables)
            for state_key, spec in set_map.items()
        ]
    return mappings


def _typed_mapping(mp: Any, state_vars: dict[str, Any]) -> Any:
    """A ``mappings:`` entry with no ``type:``, given its state variable's
    declared type; anything else as written.

    Without it an explicit entry took the string default even when the
    variable declares integer/boolean, so a trigger comparing it to a number
    never fired (``set:`` has always inferred the declared type). Copied rather
    than filled in place: the entry belongs to the loaded driver definition,
    shared with every other device on the driver."""
    if not isinstance(mp, dict) or "type" in mp:
        return mp
    if not mp.get("state"):
        return mp
    var_def = state_vars.get(mp["state"], {})
    if isinstance(var_def, dict) and var_def.get("type"):
        return {**mp, "type": var_def["type"]}
    return mp


def _set_mapping(
    state_key: str, value_expr: Any, state_vars: dict[str, Any], device_id: str
) -> dict[str, Any] | None:
    """One ``set:`` shorthand item as a mapping: ``$N`` reads capture N, any
    other value is a literal; either way typed by the state variable, so a
    boolean fed ``set: {mute: "true"}`` stores True, not the string."""
    var_def = state_vars.get(state_key, {})
    var_type = var_def.get("type", "string") if isinstance(var_def, dict) else "string"
    if not (isinstance(value_expr, str) and value_expr.startswith("$")):
        return {"group": 0, "state": state_key, "value": value_expr, "type": var_type}
    try:
        group = int(value_expr[1:])
    except ValueError:
        # A non-numeric $-reference is an author typo. Capturing group 0 (the
        # whole match) would write a wrong value with no warning, so it is
        # skipped, and said. "$0" is the whole match when that is meant.
        log.warning(
            "[%s] set-shorthand reference %r for state "
            "'%s' is not a numeric group ($1, $2, ...); "
            "skipping this mapping",
            device_id, value_expr, state_key,
        )
        return None
    return {"group": group, "state": state_key, "type": var_type}


def build_regex_mappings(
    resp: dict[str, Any], state_vars: dict[str, Any], device_id: str = ""
) -> list[dict[str, Any]]:
    """Build the flat state mappings of a regex (text) response rule.

    A rule may carry both ``mappings:`` (the detailed form, the only one that
    takes a value map) and the ``set:`` shorthand, and routinely needs to: the
    moment one captured field needs a map, the author writes ``mappings:`` for
    it and ``set:`` for the plain ones. Both are kept, ``mappings:`` first, so
    an explicit entry still wins for a state named in both. The json twin is
    ``build_json_mappings``.
    """
    mappings = [_typed_mapping(mp, state_vars) for mp in resp.get("mappings", [])]
    set_map = resp.get("set")
    if isinstance(set_map, dict):
        for state_key, value_expr in set_map.items():
            mapping = _set_mapping(state_key, value_expr, state_vars, device_id)
            if mapping is not None:
                mappings.append(mapping)
    return mappings


def _required_keys(resp: dict[str, Any]) -> tuple[str, ...]:
    """A json rule's optional ``require:`` scope, the key or keys a body must
    carry for the rule to read it. Endpoints of one REST device often reuse a
    field name (``status``, ``id``) with different meanings, and an unscoped
    rule would misapply across them."""
    raw_require = resp.get("require")
    if isinstance(raw_require, str):
        return (raw_require,)
    if isinstance(raw_require, list):
        return tuple(str(k) for k in raw_require if k)
    return ()


@dataclass
class _Instance:
    """One compile's inputs: the driver definition, the instance's config its
    rules are filled from, the definition's child types, and the device a
    warning names."""

    definition: dict[str, Any]
    config: dict[str, Any]
    child_types: dict[str, Any]
    device_id: str


def _osc_rule(resp: dict[str, Any], instance: _Instance) -> OscRule:
    """An OSC address rule (``address:`` in place of ``match:``)."""
    return (
        safe_substitute(resp["address"], instance.config),
        list(resp.get("mappings", [])),
        compile_osc_child_set(resp, instance.child_types, instance.device_id),
        build_throttle(resp),
        rule_condition(resp),
    )


def _json_rule(resp: dict[str, Any], instance: _Instance) -> JsonRule:
    """A JSON-body rule: the body is parsed once and many keys mapped from it.
    Additive: it does not change the regex first-match path."""
    return (
        build_json_mappings(resp, instance.definition.get("state_variables", {})),
        build_throttle(resp),
        _required_keys(resp),
        rule_condition(resp),
        compile_json_child_set(resp, instance.child_types, instance.device_id),
    )


def _regex_rule(resp: dict[str, Any], instance: _Instance) -> RegexRule | None:
    """A regex rule for TCP/serial/UDP/HTTP data, its pattern filled from the
    instance's config; None when it has no ``match:``. Raises ``re.error`` for
    a pattern that does not compile."""
    raw_pattern = resp.get("match", "")
    if not raw_pattern:
        return None
    pattern = re.compile(safe_substitute(raw_pattern, instance.config))
    device_id, child_types = instance.device_id, instance.child_types
    mappings = build_regex_mappings(
        resp, instance.definition.get("state_variables", {}), device_id
    )
    child_mappings = compile_child_set(resp, child_types, device_id)
    child_list = compile_child_list(resp, child_types, device_id)
    if child_list is not None:
        child_mappings.append(child_list)
    return (
        pattern,
        mappings,
        child_mappings,
        build_throttle(resp),
        rule_condition(resp),
        rule_follow_ups(resp),
    )


def _add_regex_rule(
    compiled: CompiledProtocol, resp: dict[str, Any], instance: _Instance
) -> None:
    """Add a regex rule to ``responses``, and to ``after_json_responses`` as
    the same tuple when it declares ``after_json: true``: the flag does not
    change how the rule matches, only whether it is still eligible once a json
    rule has read the body, and sharing the entry keeps one throttle window per
    rule however the frame reached it. A pattern that does not compile is
    skipped, with a warning."""
    try:
        entry = _regex_rule(resp, instance)
    except re.error as e:
        log.warning(
            f"[{instance.device_id}] Invalid response pattern "
            f"'{resp.get('match', '')}': {e}"
        )
        return
    if entry is None:
        return
    compiled.responses.append(entry)
    if resp.get("after_json"):
        compiled.after_json_responses.append(entry)


def compile_driver(
    definition: dict[str, Any],
    config: dict[str, Any],
    device_id: str = "",
) -> CompiledProtocol:
    """Compile a driver definition's receive side against one instance's
    config. Rule order is preserved — matching is first-match-wins on the
    runtime side. Mapping lists are copied per call: the source lists live
    in the (class-shared) definition, and aliasing them would let one
    instance's edits leak into every instance of the driver type."""
    compiled = CompiledProtocol()
    instance = _Instance(
        definition=definition,
        config=config,
        child_types=definition.get("child_entity_types") or {},
        device_id=device_id,
    )
    for resp in definition.get("responses", []):
        if "address" in resp:
            compiled.osc_responses.append(_osc_rule(resp, instance))
        elif resp.get("json"):
            compiled.json_responses.append(_json_rule(resp, instance))
        else:
            _add_regex_rule(compiled, resp, instance)
    _attach_conversions(compiled, definition)
    return compiled


def _convert_list(
    entries: list[Any], key: str, declared: Any, seen: set[int]
) -> None:
    """Give each mapping in ``entries`` that writes a converted variable its
    ``convert``, once per list. ``key`` names the variable (``state`` for a
    flat mapping, ``prop`` for a child's); static ``value:`` entries and
    ``contains`` checks are already final and get none."""
    if id(entries) in seen or not isinstance(declared, dict):
        return
    seen.add(id(entries))
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or _is_final(entry):
            continue
        conversion = conversion_of(declared.get(entry.get(key)))
        if conversion:
            entries[index] = {**entry, "convert": conversion}


def _is_final(entry: dict[str, Any]) -> bool:
    """A static ``value:`` or a ``contains`` check: nothing left to convert."""
    return "value" in entry or "contains" in entry


def _rule_mappings(compiled: CompiledProtocol) -> Iterator[tuple[Any, Any]]:
    """Every rule's (flat mappings, child mappings), regex then OSC then JSON."""
    for _pattern, mappings, child_mappings, *_rest in compiled.responses:
        yield mappings, child_mappings
    for _address, mappings, child_mappings, *_rest in compiled.osc_responses:
        yield mappings, child_mappings
    for mappings, _throttle, _require, _condition, child_mappings in compiled.json_responses:
        yield mappings, child_mappings


def _attach_conversions(compiled: CompiledProtocol, definition: dict[str, Any]) -> None:
    """Copy each state variable's scale / offset / unknown onto the compiled
    mappings that write it, as ``convert``, so a response handler converts
    without looking the variable up per message. Entries are replaced, never
    edited: the mapping dicts can be the driver definition's own, shared by
    every device on the driver."""
    state_vars = definition.get("state_variables") or {}
    child_types = definition.get("child_entity_types") or {}
    seen: set[int] = set()
    for mappings, child_mappings in _rule_mappings(compiled):
        _convert_list(mappings, "state", state_vars, seen)
        for cm in child_mappings or ():
            type_def = child_types.get(cm.get("type")) if isinstance(cm, dict) else None
            if isinstance(type_def, dict) and isinstance(cm.get("props"), list):
                _convert_list(cm["props"], "prop", type_def.get("state_variables"), seen)
