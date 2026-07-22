"""Shared pure helpers for the protocol interpreters.

"Interpret a driver's protocol" happens in three places: the driver runtime
(``server.drivers.configurable``), the auto-generated device simulator
(``simulator.yaml_auto``), and the driver/simulator validator
(``simulator.validate``). The helpers here are the pieces those interpreters
must agree on byte-for-byte — placeholder substitution and its inversion
back to a matching regex, value coercion, send_frame packet framing, and
delimiter decoding. Each interpreter used to
carry its own copy, and a copy that drifts shows up as a simulator that
answers a command differently than the real device would — so they live here
once instead.

``compile_driver()`` turns a driver definition's receive side into a
``CompiledProtocol`` — the pre-compiled response tables the runtime matches
incoming data against. The other interpreters reason about the same rules,
so the compile lives here with the helpers rather than inside the runtime.

Pure on purpose: stdlib plus ``server.transport.binary_helpers`` only — the
same contract as ``server.drivers.inline_protocol``, and for the same reason:
the simulator runs as a separate process and imports this module directly, so
it must not pull in the driver runtime or transport stack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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


# ── Send-template inversion ──


def _placeholder_re(name: str) -> re.Pattern[str]:
    # {name} or {name:spec} — the same token shape safe_substitute expands.
    return re.compile(r"\{" + re.escape(name) + r"(?::([^{}]*))?\}")


def spec_int_base(spec: str) -> int | None:
    """Numeric base an integer format spec puts on the wire.

    16 for ``x``/``X``, 8 for ``o``, 2 for ``b``, None for plain decimal —
    the decode twin of the capture class ``send_regex`` chooses, so a
    consumer that captured a formatted value can turn it back into the
    number the sender started from.
    """
    return {"x": 16, "X": 16, "o": 8, "b": 2}.get(spec[-1:] if spec else "")


_BASE_CAPTURES = {16: r"([0-9a-fA-F]+)", 8: r"([0-7]+)", 2: r"([01]+)"}


def _capture_for(param_type: str, spec: str) -> str:
    """The capture class one param placeholder inverts to."""
    if param_type in ("integer", "child_id"):
        # The spec's presentation type decides the digit set on the wire:
        # {addr:02X} emits hex, which (\d+) could never match back.
        base = spec_int_base(spec)
        if base:
            return _BASE_CAPTURES[base]
        # child_id values are integer child-entity IDs (optionally
        # zero-padded), so capture digits — not a greedy (.+) that
        # over-matches and breaks parity with the driver's send shape.
        return r"(\d+)"
    if param_type == "number":
        return r"([\d.]+)"
    if param_type == "boolean":
        return r"(true|false|0|1)"
    return r"(.+)"


def send_regex(template: str, params: dict[str, Any]) -> str:
    """Invert a command send template into the regex source that matches
    the wire form ``safe_substitute`` produces on the send path.

    Used by the auto-generated simulator and the driver/simulator validator
    to recognize an incoming command line. Placeholders for declared params
    ({name} and {name:spec} alike) become typed capture groups — one group
    per occurrence, in template order; everything else matches literally.
    Trailing terminators are dropped (real CR/LF and literal ``\\r``/``\\n``
    escape sequences): the consumers match against delimiter-split,
    whitespace-stripped lines, so a pattern that kept its terminator could
    never match anything.
    """
    result = template.strip()
    while result.endswith(("\\r", "\\n")):
        result = result[:-2].rstrip()
    for name, pdef in params.items():
        ptype = pdef.get("type", "string") if isinstance(pdef, dict) else "string"
        result = _placeholder_re(name).sub(
            lambda m, _t=ptype: _capture_for(_t, m.group(1) or ""), result
        )
    # Escape regex specials outside the capture groups just inserted.
    escaped = ""
    depth = 0
    for char in result:
        if char == "(":
            depth += 1
            escaped += char
        elif char == ")":
            depth -= 1
            escaped += char
        elif depth > 0:
            escaped += char
        elif char in r"*+?.[]{}|^$":
            escaped += "\\" + char
        else:
            escaped += char
    return escaped


def send_param_specs(template: str, params: dict[str, Any]) -> dict[str, str]:
    """Map each declared param to the format spec its placeholder carries
    in the template ('' when bare or absent)."""
    specs: dict[str, str] = {}
    for name in params:
        m = _placeholder_re(name).search(template)
        specs[name] = (m.group(1) or "") if m else ""
    return specs


def send_param_groups(template: str, params: dict[str, Any]) -> dict[str, int]:
    """Map each declared param to the capture-group index its first
    placeholder occurrence gets in ``send_regex``'s inversion.

    ``send_regex`` turns every placeholder occurrence into one capture
    group, numbered in template order — which is not necessarily the
    order the params dict declares them in. A consumer resolving a
    declared ``sets: {var: "{param}"}`` reference needs the group that
    actually holds the param's wire value."""
    occurrences: list[tuple[int, str]] = []
    for name in params:
        for m in _placeholder_re(name).finditer(template):
            occurrences.append((m.start(), name))
    occurrences.sort()
    groups: dict[str, int] = {}
    for idx, (_, name) in enumerate(occurrences, start=1):
        groups.setdefault(name, idx)
    return groups


# ── Command semantics: name-inference fallback ──
#
# When a command declares no `sets:` / `query_for:`, consumers fall back to
# inferring the target state variable from the command's name. This is the
# ONE shared copy of that heuristic (the auto-generated simulator uses it;
# declared semantics always win over it).


def infer_state_var(cmd_name: str, state_vars: set[str]) -> str | None:
    """Infer which state variable a command targets from its name.

    Examples:
        "set_volume" → "volume"
        "mute_on" → "mute"
        "power_off" → "power"
        "query_input" → "input"
        "route_all" → "input" (if "input" in state_vars, best guess for routing)
    """
    # Strip common prefixes/suffixes
    name = cmd_name
    for prefix in ("set_", "get_", "query_", "enable_", "disable_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    for suffix in ("_on", "_off", "_toggle"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break

    # Direct match
    if name in state_vars:
        return name

    # Common AV aliases
    aliases = {
        "route": "input",
        "route_all": "input",
        "unmute": "mute",
        "vol": "volume",
        "video_mute": "video_mute",
        "audio_mute": "mute",
    }
    alias_target = aliases.get(name) or aliases.get(cmd_name)
    if alias_target and alias_target in state_vars:
        return alias_target

    return None


# ── Emit-template inversion (response regex → reply text) ──
#
# The auto-generated simulator answers a query by reconstructing the reply
# text a driver's response pattern would match. That means walking the regex
# source and emitting one representative string: literals stay, anchors and
# word boundaries vanish, a class or shorthand becomes one member character,
# an alternation takes its first branch, an optional atom is dropped, and
# the requested capture group becomes the {value} placeholder. Anything the
# walk can't faithfully model returns None so the caller falls back instead
# of putting garbage on the wire.


class _Unsupported(Exception):
    """Regex construct the emit reconstruction cannot model."""


_SHORTHAND_REPS = {"d": "0", "w": "0", "s": " ", "S": "0"}
_ESCAPE_CHARS = {"r": "\r", "n": "\n", "t": "\t", "f": "\f", "v": "\v"}
_QUANT_RE = re.compile(r"\{(\d+)(?:,(\d*))?\}")
_FLAG_GROUP_RE = re.compile(r"\(\?([aiLmsux]+)([:)])")


def _class_emit(body: str) -> str | None:
    """One representative character for a [...] class body, or None.

    The first member decides: a literal char, a range's start, a shorthand's
    representative. A negated class has no safe representative.
    """
    if not body or body.startswith("^"):
        return None
    if body[0] == "\\" and len(body) > 1:
        c = body[1]
        if c in _SHORTHAND_REPS:
            return _SHORTHAND_REPS[c]
        if c in _ESCAPE_CHARS:
            return _ESCAPE_CHARS[c]
        if c in ("D", "W"):
            return None
        return c
    return body[0]


def _class_end(src: str, i: int) -> int:
    """Index of the ']' closing the class opened at src[i] == '['."""
    j = i + 1
    if j < len(src) and src[j] == "^":
        j += 1
    if j < len(src) and src[j] == "]":
        j += 1  # leading ] is a literal member
    while j < len(src):
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == "]":
            return j
        j += 1
    raise _Unsupported


def _parse_atom(src: str, i: int, ctx: dict) -> tuple[str | None, int]:
    """Emit one atom starting at src[i]. None means unrepresentable —
    fatal only if the atom survives into the final emission."""
    c = src[i]
    if c in "^$":
        return "", i + 1
    if c == ".":
        return "x", i + 1
    if c == "\\":
        if i + 1 >= len(src):
            raise _Unsupported
        e = src[i + 1]
        if e in _SHORTHAND_REPS:
            return _SHORTHAND_REPS[e], i + 2
        if e in _ESCAPE_CHARS:
            return _ESCAPE_CHARS[e], i + 2
        if e in "bB":
            return "", i + 2
        if e == "x" and re.match(r"[0-9a-fA-F]{2}", src[i + 2 : i + 4]):
            return chr(int(src[i + 2 : i + 4], 16)), i + 4
        if e.isdigit():
            return None, i + 2  # backreference
        if e in "DWAZ":
            return None, i + 2
        return e, i + 2  # escaped literal (punctuation)
    if c == "[":
        j = _class_end(src, i)
        return _class_emit(src[i + 1 : j]), j + 1
    if c == "(":
        return _parse_group(src, i, ctx)
    return c, i + 1


def _parse_group(src: str, i: int, ctx: dict) -> tuple[str | None, int]:
    """Emit the group opened at src[i] == '('."""
    capturing = True
    j = i + 1
    if src.startswith("(?:", i):
        capturing = False
        j = i + 3
    elif src.startswith("(?P<", i):
        k = src.find(">", i)
        if k < 0:
            raise _Unsupported
        j = k + 1
    elif src.startswith("(?", i):
        m = _FLAG_GROUP_RE.match(src, i)
        if not m:
            raise _Unsupported  # lookarounds, conditionals, (?P=...)
        if m.group(2) == ")":
            return "", m.end()  # global flags like (?i)
        capturing = False
        j = m.end()
    this_no = 0
    if capturing:
        ctx["n"] += 1
        this_no = ctx["n"]
    emission, j = _parse_branches(src, j, ctx, depth=1)
    if j >= len(src) or src[j] != ")":
        raise _Unsupported
    j += 1
    if capturing and this_no in ctx["targets"]:
        ctx["found"].add(this_no)
        return ctx["targets"][this_no], j
    return emission, j


def _parse_quantifier(src: str, i: int) -> tuple[int, int] | None:
    """Parse a quantifier at src[i]; return (min_repeats, next_i) or None."""
    if i >= len(src):
        return None
    c = src[i]
    if c in "?*":
        n, j = 0, i + 1
    elif c == "+":
        n, j = 1, i + 1
    elif c == "{":
        m = _QUANT_RE.match(src, i)
        if not m:
            return None  # a bare '{' is a literal, not a quantifier
        n, j = int(m.group(1)), m.end()
        if n > 8:
            raise _Unsupported
    else:
        return None
    if j < len(src) and src[j] == "?":
        j += 1  # lazy variant
    return n, j


def _parse_seq(src: str, i: int, ctx: dict, depth: int) -> tuple[str | None, int]:
    """Emit atoms until '|', a closing ')' (inside a group), or the end."""
    pieces: list[str | None] = []
    while i < len(src):
        c = src[i]
        if c == "|":
            break
        if c == ")":
            if depth == 0:
                raise _Unsupported  # unbalanced
            break
        piece, i = _parse_atom(src, i, ctx)
        quant = _parse_quantifier(src, i)
        reps = 1
        if quant:
            reps, i = quant
        if piece is not None and _has_target(piece, ctx):
            if reps != 1:
                if reps > 1:
                    raise _Unsupported  # a repeated target would duplicate the field
                reps = 1  # never drop a target capture
        if reps == 0:
            continue
        pieces.append(piece if reps == 1 else (None if piece is None else piece * reps))
    if any(p is None for p in pieces):
        return None, i
    return "".join(pieces), i  # type: ignore[arg-type]


def _has_target(piece: str, ctx: dict) -> bool:
    """True when a targeted group's placeholder text survives in ``piece``."""
    return any(ph in piece for ph in ctx["targets"].values())


def _parse_branches(src: str, i: int, ctx: dict, depth: int) -> tuple[str | None, int]:
    """Emit an alternation: all branches are parsed (group numbering must
    stay exact), the emission comes from the first branch carrying a target
    placeholder, else the first representable branch."""
    branches: list[str | None] = []
    while True:
        emission, i = _parse_seq(src, i, ctx, depth)
        branches.append(emission)
        if i < len(src) and src[i] == "|":
            i += 1
            continue
        break
    with_value = [b for b in branches if b is not None and _has_target(b, ctx)]
    if with_value:
        return with_value[0], i
    for b in branches:
        if b is not None:
            return b, i
    return None, i


def emit_template_multi(pattern: str, placeholders: dict[int, str]) -> str | None:
    """Reconstruct the reply text a response pattern matches, substituting
    each capture group in ``placeholders`` with its placeholder text.

    The multi-slot core behind ``emit_template``: a child-addressed reply
    needs the child id group AND the value group templated in one emission
    (``'Out(\\d+) In(\\d+) Vid'`` with ``{1: '{child_id}', 2: '{value}'}`` →
    ``'Out{child_id} In{value} Vid'``). Every targeted group must survive
    into the final emission; returns None when the pattern can't be modeled.
    """
    ctx = {"n": 0, "targets": dict(placeholders), "found": set()}
    try:
        emission, i = _parse_branches(pattern, 0, ctx, depth=0)
    except _Unsupported:
        return None
    if i != len(pattern) or emission is None:
        return None
    if ctx["found"] != set(placeholders):
        return None
    if any(ph not in emission for ph in placeholders.values()):
        return None
    return emission


def emit_template(pattern: str, group: int = 1) -> str | None:
    """Reconstruct the reply text a response pattern matches, with capture
    group ``group`` as a ``{value}`` placeholder.

    ``'In(\\d+) All'`` → ``'In{value} All'``; the group number comes from
    the response's ``$N`` reference, so a multi-group pattern places the
    value where that state key actually reads it. Returns None when the
    pattern can't be modeled — the caller falls back rather than emitting
    garbage the driver's own regex would reject.
    """
    return emit_template_multi(pattern, {group: "{value}"})


def emit_literal(pattern: str) -> str | None:
    """Reconstruct the literal reply text a capture-less response pattern
    matches (``'^Amt1$'`` → ``'Amt1'``). None when the pattern contains a
    capturing group (a fixed literal can't represent a captured field) or
    a construct the reconstruction can't model.
    """
    ctx = {"n": 0, "targets": {}, "found": set()}
    try:
        emission, i = _parse_branches(pattern, 0, ctx, depth=0)
    except _Unsupported:
        return None
    if i != len(pattern) or emission is None or ctx["n"]:
        return None
    return emission


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


# ── Receive-side compile ──


@dataclass
class CompiledProtocol:
    """The pre-compiled receive side of one driver instance.

    Three separate tables:

    1. ``responses`` — regex rules for TCP/serial/UDP/HTTP data
       (pattern, flat state mappings, compiled child_set entries, throttle)
    2. ``osc_responses`` — OSC address rules (address, mappings, child_set,
       throttle)
    3. ``json_responses`` — JSON-body rules applied together from one parsed
       object (mappings, throttle, require-keys scope)

    Every entry carries an optional throttle state ({window, last} or None) —
    a rule with ``throttle: <seconds>`` skips re-fires inside its window
    (drop-style; built for continuous push telemetry like audio level meters,
    where every skipped frame is superseded by the next).

    Compiled per driver INSTANCE, not per driver class: the tables embed the
    instance's config substitutions, and their mapping lists are mutable
    per-instance copies — sharing them across instances would let one
    instance's edits leak into every other instance of the same driver type.
    """

    responses: list[
        tuple[
            re.Pattern[str],
            list[dict[str, Any]],
            list[dict[str, Any]],
            dict[str, Any] | None,
        ]
    ] = field(default_factory=list)
    osc_responses: list[
        tuple[
            str,
            list[dict[str, Any]],
            list[dict[str, Any]],
            dict[str, Any] | None,
        ]
    ] = field(default_factory=list)
    json_responses: list[
        tuple[list[dict[str, Any]], dict[str, Any] | None, tuple[str, ...]]
    ] = field(default_factory=list)


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


def compile_child_set(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Compile a response entry's ``child_set:`` list — route regex
    captures into child-entity state. Each entry is
    ``{type, id: $N | literal | {group, map}, state: {prop: $N | literal
    | {group/value/map/type}}}``.
    Value coercion uses the child type's declared ``state_variables``,
    mirroring the flat ``set:`` shorthand (static values coerce too).
    Malformed entries are skipped with a warning; the loader validates
    the same shape up-front so a catalog driver never gets here wrong.
    """
    raw = resp.get("child_set")
    if not isinstance(raw, list):
        return []
    compiled: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ctype = entry.get("type")
        tdef = child_types.get(ctype)
        if not isinstance(tdef, dict):
            log.warning(
                f"[{device_id}] child_set: unknown child type "
                f"{ctype!r}; skipping entry"
            )
            continue
        cvars = tdef.get("state_variables") or {}
        cid = entry.get("id")
        idspec: tuple[str, Any]
        id_map: dict[str, Any] | None = None
        if isinstance(cid, dict):
            # Long form: {group: N | $N, map: {wire: local_id}} — route
            # by capture ref, translating the captured wire id to the
            # local child id (0-based protocols, letter codes). A wire
            # id the map doesn't cover skips the entry at apply time.
            gref = cid.get("group")
            if isinstance(gref, str) and gref.startswith("$"):
                gref = gref[1:]
            try:
                idspec = ("group", int(gref))
            except (TypeError, ValueError):
                log.warning(
                    f"[{device_id}] child_set: id group "
                    f"{cid.get('group')!r} is not a capture ref; "
                    f"skipping entry"
                )
                continue
            raw_map = cid.get("map")
            if isinstance(raw_map, dict) and raw_map:
                id_map = {str(k): v for k, v in raw_map.items()}
        elif isinstance(cid, str) and cid.startswith("$"):
            try:
                idspec = ("group", int(cid[1:]))
            except ValueError:
                log.warning(
                    f"[{device_id}] child_set: id {cid!r} is not a "
                    f"numeric capture ref ($1, $2, ...); skipping entry"
                )
                continue
        elif cid is not None:
            idspec = ("literal", cid)
        else:
            log.warning(
                f"[{device_id}] child_set: missing 'id'; skipping entry"
            )
            continue
        state_map = entry.get("state")
        if not isinstance(state_map, dict):
            continue
        props: list[dict[str, Any]] = []
        for prop, expr in state_map.items():
            var_def = cvars.get(prop, {})
            var_type = (
                var_def.get("type", "string")
                if isinstance(var_def, dict)
                else "string"
            )
            if isinstance(expr, dict):
                pm: dict[str, Any] = {"prop": prop, "type": expr.get("type", var_type)}
                if "group" in expr:
                    try:
                        pm["group"] = int(expr["group"])
                    except (TypeError, ValueError):
                        continue
                elif "value" in expr:
                    pm["value"] = expr["value"]
                else:
                    continue
                if isinstance(expr.get("map"), dict):
                    pm["map"] = expr["map"]
                props.append(pm)
            elif isinstance(expr, str) and expr.startswith("$"):
                try:
                    group = int(expr[1:])
                except ValueError:
                    log.warning(
                        f"[{device_id}] child_set: state '{prop}' ref "
                        f"{expr!r} is not a numeric capture ref; skipping"
                    )
                    continue
                props.append({"prop": prop, "group": group, "type": var_type})
            else:
                # Static value — coerce by declared type like flat set:.
                props.append({"prop": prop, "value": expr, "type": var_type})
        if props:
            compiled.append(
                {
                    "type": ctype,
                    "id": idspec,
                    "id_map": id_map,
                    "props": props,
                }
            )
    return compiled


def compile_osc_child_set(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Compile an OSC response entry's ``child_set:`` list — route an
    address-matched message into child-entity state. OSC has no capture
    groups, so the child id comes from an **address segment**
    (``id: {segment: N}``, 0-based over the /-split address — in
    ``/ch/07/mix/fader`` segment 1 is ``"07"``) or a literal, and prop
    values come from **positional args** (``{arg: N}``) or literals.
    ``map:`` semantics (id translation with unmapped-skip, per-prop value
    maps) mirror the regex path. Malformed entries are skipped with a
    warning; the loader validates the same shape up-front.
    """
    raw = resp.get("child_set")
    if not isinstance(raw, list):
        return []
    compiled: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ctype = entry.get("type")
        tdef = child_types.get(ctype)
        if not isinstance(tdef, dict):
            log.warning(
                f"[{device_id}] child_set: unknown child type "
                f"{ctype!r}; skipping entry"
            )
            continue
        cvars = tdef.get("state_variables") or {}
        cid = entry.get("id")
        idspec: tuple[str, Any]
        id_map: dict[str, Any] | None = None
        if isinstance(cid, dict):
            try:
                idspec = ("segment", int(cid.get("segment")))
            except (TypeError, ValueError):
                log.warning(
                    f"[{device_id}] child_set: OSC id needs a "
                    f"'segment' index (got {cid!r}); skipping entry"
                )
                continue
            raw_map = cid.get("map")
            if isinstance(raw_map, dict) and raw_map:
                id_map = {str(k): v for k, v in raw_map.items()}
        elif cid is not None and not (
            isinstance(cid, str) and cid.startswith("$")
        ):
            idspec = ("literal", cid)
        else:
            log.warning(
                f"[{device_id}] child_set: OSC rules have no capture "
                f"groups — id must be {{segment: N}} or a literal "
                f"(got {cid!r}); skipping entry"
            )
            continue
        state_map = entry.get("state")
        if not isinstance(state_map, dict):
            continue
        props: list[dict[str, Any]] = []
        for prop, expr in state_map.items():
            var_def = cvars.get(prop, {})
            var_type = (
                var_def.get("type", "string")
                if isinstance(var_def, dict)
                else "string"
            )
            if isinstance(expr, dict):
                pm: dict[str, Any] = {
                    "prop": prop,
                    "type": expr.get("type", var_type),
                }
                if "arg" in expr:
                    try:
                        pm["arg"] = int(expr["arg"])
                    except (TypeError, ValueError):
                        continue
                elif "value" in expr:
                    pm["value"] = expr["value"]
                else:
                    continue
                if isinstance(expr.get("map"), dict):
                    pm["map"] = expr["map"]
                props.append(pm)
            elif isinstance(expr, str) and expr.startswith("$"):
                log.warning(
                    f"[{device_id}] child_set: state '{prop}' — OSC "
                    f"rules have no capture groups; use {{arg: N}}. Skipping"
                )
                continue
            else:
                # Static value — coerce by declared type like flat set:.
                props.append({"prop": prop, "value": expr, "type": var_type})
        if props:
            compiled.append(
                {
                    "type": ctype,
                    "id": idspec,
                    "id_map": id_map,
                    "props": props,
                }
            )
    return compiled


def build_json_mappings(
    resp: dict[str, Any], state_variables: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build {state, key, type, map} mappings for a ``json: true`` response.

    Accepts the detailed ``mappings`` list or the friendly ``set`` map. In a
    json rule a ``set`` value is the JSON key to read (string shorthand) or a
    ``{key/path, type, map}`` spec — not a regex capture ref. Types default
    to the matching state variable's declared type.
    """
    mappings: list[dict[str, Any]] = list(resp.get("mappings", []))
    set_map = resp.get("set")
    if not mappings and isinstance(set_map, dict):
        for state_key, spec in set_map.items():
            var_def = state_variables.get(state_key, {})
            default_type = (
                var_def.get("type", "string") if isinstance(var_def, dict) else "string"
            )
            if isinstance(spec, dict):
                mappings.append({
                    "state": state_key,
                    "key": spec.get("key", spec.get("path", state_key)),
                    "type": spec.get("type", default_type),
                    "map": spec.get("map"),
                })
            else:
                mappings.append({
                    "state": state_key, "key": str(spec), "type": default_type,
                })
    return mappings


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
    child_types = definition.get("child_entity_types") or {}
    for resp in definition.get("responses", []):
        # OSC responses use "address" key instead of "pattern"/"match"
        if "address" in resp:
            addr = safe_substitute(resp["address"], config)
            mappings = list(resp.get("mappings", []))
            compiled.osc_responses.append(
                (
                    addr,
                    mappings,
                    compile_osc_child_set(resp, child_types, device_id),
                    build_throttle(resp),
                )
            )
            continue

        # JSON-body response: parse the whole body once and map many keys
        # at a time. Additive — does not change the regex first-match path.
        # Optional `require:` scopes the rule to bodies carrying the named
        # key(s) — different endpoints on one REST device often reuse a
        # field name (`status`, `id`, `serialNumber`) with different
        # meanings, and an unscoped rule would misapply across them.
        if resp.get("json"):
            raw_require = resp.get("require")
            if isinstance(raw_require, str):
                require = (raw_require,)
            elif isinstance(raw_require, list):
                require = tuple(str(k) for k in raw_require if k)
            else:
                require = ()
            compiled.json_responses.append(
                (
                    build_json_mappings(resp, definition.get("state_variables", {})),
                    build_throttle(resp),
                    require,
                )
            )
            continue

        try:
            # Canonical key is "match"; "pattern" remains accepted as an alias.
            raw_pattern = resp.get("match", "") or resp.get("pattern", "")
            if not raw_pattern:
                continue
            resolved = safe_substitute(raw_pattern, config)
            pattern = re.compile(resolved)

            # Accept both "mappings" (detailed) and "set" (shorthand) formats.
            mappings = list(resp.get("mappings", []))
            if not mappings and "set" in resp:
                # Convert shorthand: {"set": {"input": "$1", "mute": "true"}}
                # to mappings: [{"group": 1, "state": "input"}, ...]
                state_vars = definition.get("state_variables", {})
                for state_key, value_expr in resp["set"].items():
                    var_def = state_vars.get(state_key, {})
                    var_type = var_def.get("type", "string") if isinstance(var_def, dict) else "string"
                    if isinstance(value_expr, str) and value_expr.startswith("$"):
                        try:
                            group = int(value_expr[1:])
                        except ValueError:
                            # A non-numeric $-reference is an author typo.
                            # Silently capturing group 0 (the whole match)
                            # would write a wrong value with no warning, so
                            # surface it and skip this mapping. Use "$0"
                            # explicitly if the whole match is intended.
                            log.warning(
                                "[%s] set-shorthand reference %r for state "
                                "'%s' is not a numeric group ($1, $2, ...); "
                                "skipping this mapping",
                                device_id, value_expr, state_key,
                            )
                            continue
                        mappings.append({"group": group, "state": state_key, "type": var_type})
                    else:
                        # Static values coerce by the state var's declared
                        # type too — without it a boolean var fed
                        # `set: {mute: "true"}` stored the string "True".
                        mappings.append({
                            "group": 0,
                            "state": state_key,
                            "value": value_expr,
                            "type": var_type,
                        })

            child_mappings = compile_child_set(resp, child_types, device_id)
            compiled.responses.append(
                (pattern, mappings, child_mappings, build_throttle(resp))
            )
        except re.error as e:
            log.warning(
                f"[{device_id}] Invalid response pattern "
                f"'{resp.get('match', resp.get('pattern', ''))}': {e}"
            )
    return compiled
