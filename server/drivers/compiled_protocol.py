"""Shared pure helpers for the protocol interpreters.

"Interpret a driver's protocol" happens in three places: the driver runtime
(``server.drivers.configurable``), the auto-generated device simulator
(``simulator.yaml_auto``), and the driver/simulator validator
(``simulator.validate``). The helpers here are the pieces those interpreters
must agree on byte-for-byte — placeholder substitution, value coercion,
send_frame packet framing, and delimiter decoding. Each interpreter used to
carry its own copy, and a copy that drifts shows up as a simulator that
answers a command differently than the real device would — so they live here
once instead.

Pure on purpose: stdlib plus ``server.transport.binary_helpers`` only — the
same contract as ``server.drivers.inline_protocol``, and for the same reason:
the simulator runs as a separate process and imports this module directly, so
it must not pull in the driver runtime or transport stack.
"""

from __future__ import annotations

import re
from typing import Any

from server.transport.binary_helpers import encode_escape_sequences, pack_length_prefix
from server.utils.logger import get_logger

log = get_logger(__name__)


# ── Placeholder substitution ──


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
        value = params[key]
        if not spec:
            return str(value)
        try:
            return format(value, spec)
        except (ValueError, TypeError):
            # An integer spec ('d'/'x'/'X'/'o'/'b') rejects a float even when
            # it's whole (26.0), which is exactly what a scaled slider value
            # is. Coerce a whole-number float to int and retry so {vol:d}
            # renders "26".
            if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
                try:
                    return format(int(value), spec)
                except (ValueError, TypeError):
                    pass
            # A numeric spec applied to a numeric string: coerce, then
            # format. int first so '02d' works on "5".
            if isinstance(value, str):
                for conv in (int, float):
                    try:
                        return format(conv(value), spec)
                    except (ValueError, TypeError):
                        continue
            # Unformattable — leave the placeholder verbatim so a bad spec
            # is visible to the author instead of crashing the send.
            return match.group(0)

    # {name} or {name:spec}; the spec excludes braces so it can't span tokens.
    return re.sub(r"\{(\w+)(?::([^{}]*))?\}", replacer, template)


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
        refs = re.findall(r"\{(\w+)(?::[^{}]*)?\}", template)
        if any(not str(config.get(f, "") or "").strip() for f in refs):
            config[name] = ""
        else:
            config[name] = safe_substitute(template, config)


# ── Value coercion ──


def coerce_value(raw: str, value_type: str) -> Any:
    """Convert a raw string (a regex capture or static value) to the type."""
    if value_type == "integer":
        try:
            return int(raw)
        except ValueError:
            log.warning("Cannot coerce %r to integer, returning raw string", raw)
            return raw
    elif value_type in ("float", "number"):
        try:
            return float(raw)
        except ValueError:
            log.warning("Cannot coerce %r to %s, returning raw string", raw, value_type)
            return raw
    elif value_type == "boolean":
        return raw.lower() in ("1", "true", "yes", "on")
    return raw  # string or enum


def coerce_json_value(value: Any, value_type: str) -> Any:
    """Coerce a native JSON value to the declared state type.

    Unlike ``coerce_value`` (string in), this keeps real JSON bools / ints /
    floats instead of round-tripping through ``str`` (which would turn JSON
    ``true`` into the string ``"True"``).
    """
    if value is None:
        return None
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if value_type == "integer":
        if isinstance(value, bool):
            return int(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                log.warning("Cannot coerce %r to integer, returning string", value)
                return str(value)
    if value_type in ("float", "number"):
        try:
            return float(value)
        except (TypeError, ValueError):
            log.warning("Cannot coerce %r to %s, returning string", value, value_type)
            return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def coerce_osc_value(value: Any, value_type: str) -> Any:
    """Convert an already-typed OSC value to the declared state type."""
    if value_type in ("float", "number"):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    elif value_type == "integer":
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    elif value_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).lower() in ("1", "true", "yes", "on")
    return str(value) if value is not None else None


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


def split_send_frames(sf: dict[str, Any], buffer: bytearray) -> list[bytes]:
    """Strip complete send_frame packets off the front of ``buffer``.

    Consumes each complete frame (fixed header + computed-length body) from
    the buffer in place and returns the bare bodies, leaving any partial
    trailing frame for the next read — exactly what the receive side's
    length-prefix frame parser does on the other end of the wire.
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
        if len(buffer) < total:
            break
        messages.append(bytes(buffer[total_header:total]))
        del buffer[:total]
    return messages
