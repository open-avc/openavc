"""A command's send template read backwards, for the simulator and its validator.

The runtime fills a command's ``send`` template with ``safe_substitute``
(``openavc.drivers.compiled_protocol``) and puts the result on the wire. The
auto-generated simulator (``yaml_auto``) has to recognize that line when it
arrives, and the validator (``validate``) checks that it would: both read the
template backwards through this module. ``send_regex`` is the regex that
matches what the substitution sends; ``send_param_groups`` and
``send_param_specs`` say which capture holds each parameter and the spec it was
written with, so the captured text can be decoded (``spec_int_base``);
``is_passthrough_send`` names a template with nothing in it to recognize.

The substitution and its inversion must agree, so a change to the placeholder
token or to a format spec ``safe_substitute`` honors is a change here too.
Every reader here walks the one token (``_ANY_PLACEHOLDER``, the contract's
``spec.PLACEHOLDER``), so a name that is not an identifier is literal text to
the inversion as it is to the substitution. ``placeholder_pattern`` is the
template read as a regex with each placeholder written as its caller says, for
the simulator's other matchers. ``tests/test_send_patterns_pinned.py``
round-trips each parameter type and spec through both, and
``tests/test_placeholder_token.py`` runs every reader over the same templates.

Standard library only (``tests/test_compiled_protocol_purity.py``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


# ── Send-template inversion ──
#
# The decode twin of ``safe_substitute``: what the runtime puts on the wire for
# a command's send template, the inversion of that template matches. The
# auto-generated simulator recognizes an incoming command line by it and the
# validator checks the simulator's coverage with it.


#: Any {name} / {name:spec} token whose name is an identifier, declared or not:
#: the driver contract's ``spec.PLACEHOLDER``, written out again because this
#: module imports nothing but the standard library
#: (``tests/test_placeholder_token.py`` holds the two equal).
_ANY_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}")


def _placeholders_of(text: str, names: Any) -> list[re.Match[str]]:
    """Each placeholder of ``text`` whose name is in ``names``, in order."""
    return [m for m in _ANY_PLACEHOLDER.finditer(text) if m.group(1) in names]


def placeholder_pattern(template: str, token: Callable[[re.Match[str]], str]) -> str:
    """``template`` as a regex: its literal text escaped (``re.escape``) and
    each placeholder written as ``token`` returns for its match. What a
    simulator matcher that is not a command's inversion (an HTTP poll, a
    registration command) reads a template with."""
    out: list[str] = []
    pos = 0
    for m in _ANY_PLACEHOLDER.finditer(template):
        out.append(re.escape(template[pos:m.start()]))
        out.append(token(m))
        pos = m.end()
    out.append(re.escape(template[pos:]))
    return "".join(out)


def declared_placeholders(text: str, params: dict[str, Any]) -> list[str]:
    """The declared params ``text``'s placeholders name, in order, one per
    occurrence: the captures ``send_regex`` makes of it."""
    return [m.group(1) for m in _placeholders_of(text, params)]


def rename_placeholder(text: str, name: str, new_name: str) -> str:
    """``text`` with every ``{name}`` / ``{name:spec}`` written as
    ``new_name``, each keeping its spec."""

    def renamed(m: re.Match[str]) -> str:
        if m.group(1) != name:
            return m.group(0)
        spec = "" if m.group(2) is None else ":" + m.group(2)
        return "{" + new_name + spec + "}"

    return _ANY_PLACEHOLDER.sub(renamed, text)


#: A line ending written as an escape at the end of a template: ``\r``,
#: ``\n``, or either in hex (``\x0D``, ``\x0a``, any case), which the driver
#: sends as the same byte.
_ESCAPED_LINE_ENDING = re.compile(r"(?:\\[rn]|\\x0[aAdD])$")


def without_escaped_line_endings(text: str) -> str:
    """``text`` stripped, with every line-ending escape at its end dropped
    (``_ESCAPED_LINE_ENDING``) and the whitespace before each: the line the
    simulator's dispatcher reads, which it splits on the line ending and
    strips."""
    text = text.strip()
    while True:
        shorter = _ESCAPED_LINE_ENDING.sub("", text)
        if shorter == text:
            return text
        text = shorter.rstrip()


def _without_terminators(template: str) -> str:
    """A send template as the consumers read a line: stripped, its trailing
    terminators dropped (real CR/LF and escaped ones,
    ``without_escaped_line_endings``). They match against delimiter-split,
    whitespace-stripped lines, so a pattern that kept its terminator could
    never match anything."""
    return without_escaped_line_endings(template)


def spec_int_base(spec: str) -> int | None:
    """Numeric base an integer format spec puts on the wire.

    16 for ``x``/``X``, 8 for ``o``, 2 for ``b``, None for plain decimal —
    the decode twin of the capture class ``send_regex`` chooses, so a
    consumer that captured a formatted value can turn it back into the
    number the sender started from.
    """
    return {"x": 16, "X": 16, "o": 8, "b": 2}.get(spec[-1:] if spec else "")


_BASE_CAPTURES = {16: r"([0-9a-fA-F]+)", 8: r"([0-7]+)", 2: r"([01]+)"}

#: The decimal capture of a param that goes on the wire as digits. An integer
#: may be negative (a level in dB, a converted value). A child id is an integer
#: child-entity id, optionally zero-padded, so digits only: a greedy (.+) would
#: over-match and break parity with the driver's send shape. A boolean under a
#: numeric spec sends 1/0 ({flag:d}) or 01/00 ({flag:02d}), how a fixed-width
#: protocol writes a flag byte.
_DIGIT_CAPTURES = {"integer": r"(-?\d+)", "child_id": r"(\d+)", "boolean": r"(\d+)"}

#: The capture of a param that goes on the wire as it is written; any other
#: type captures the rest of the field, ``(.+)``.
_WORD_CAPTURES = {"number": r"(-?[\d.]+)", "boolean": r"(true|false|0|1)"}


def _of_type(table: dict[str, str], param_type: Any) -> str | None:
    """``table``'s capture for ``param_type``, the type compared with each
    name, never hashed: a hand-written type that is not a name (a list, an
    object) matches nothing rather than raising."""
    return next((capture for name, capture in table.items() if param_type == name), None)


def _digit_capture(param_type: Any, spec: str) -> str | None:
    """The decimal capture of a param whose placeholder puts digits on the
    wire, or None: an integer or a child id always, a boolean only under a
    numeric spec (its word spellings could never match what that send
    produced)."""
    if param_type == "boolean" and not _writes_a_number(spec):
        return None
    return _of_type(_DIGIT_CAPTURES, param_type)


def _writes_a_number(spec: str) -> bool:
    """A format spec whose presentation type writes a whole number (decimal,
    hex, octal or binary)."""
    return bool(spec) and spec[-1] in "dxXob"


def _capture_for(param_type: Any, spec: str) -> str:
    """The capture class one param placeholder inverts to. Digits take the
    digit set the spec's base puts on the wire ({addr:02X} emits hex, which
    (\\d+) could never match back), else the type's decimal capture."""
    digits = _digit_capture(param_type, spec)
    if digits is None:
        return _of_type(_WORD_CAPTURES, param_type) or r"(.+)"
    base = spec_int_base(spec)
    return _BASE_CAPTURES[base] if base else digits


def send_regex(template: str, params: dict[str, Any]) -> str:
    """Invert a command send template into the regex source that matches
    the wire form ``safe_substitute`` produces on the send path.

    Used by the auto-generated simulator and the driver/simulator validator
    to recognize an incoming command line. Placeholders for declared params
    ({name} and {name:spec} alike) become typed capture groups — one group
    per occurrence, in template order; everything else matches literally.
    Trailing terminators are dropped (``_without_terminators``).
    """
    result = _without_terminators(template)
    # Escape the literal text BEFORE the capture groups go in, so a literal
    # parenthesis in the protocol (a display whose backlight code is '(')
    # is matched as itself rather than read as a group of its own. Only a
    # placeholder naming a declared param becomes a capture; any other
    # {token} is literal text, braces included.
    types = {
        name: (pdef.get("type", "string") if isinstance(pdef, dict) else "string")
        for name, pdef in params.items()
    }
    out: list[str] = []
    pos = 0
    for m in _ANY_PLACEHOLDER.finditer(result):
        out.append(_escape_literal(result[pos:m.start()]))
        name = m.group(1)
        if name in types:
            out.append(_capture_for(types[name], m.group(2) or ""))
        else:
            out.append(_escape_literal(m.group(0)))
        pos = m.end()
    out.append(_escape_literal(result[pos:]))
    return "".join(out)


def is_passthrough_send(template: str, params: dict[str, Any]) -> bool:
    """True when a send template is one declared parameter and nothing else
    (``"{command}\\r"``): a raw passthrough that puts whatever the caller
    typed on the wire. It describes no protocol, so there is nothing to
    recognize an incoming line by; inverted, it would match every line."""
    m = _ANY_PLACEHOLDER.fullmatch(_without_terminators(template))
    return bool(m and m.group(1) in params)


def _escape_literal(text: str) -> str:
    """Regex-escape the characters a send template can carry literally."""
    return "".join("\\" + c if c in r"()*+?.[]{}|^$" else c for c in text)


def send_param_specs(template: str, params: dict[str, Any]) -> dict[str, str]:
    """Map each declared param to the format spec its placeholder carries
    in the template ('' when bare or absent)."""
    specs = dict.fromkeys(params, "")
    for m in reversed(_placeholders_of(template, params)):
        specs[m.group(1)] = m.group(2) or ""
    return specs


def send_param_groups(template: str, params: dict[str, Any]) -> dict[str, int]:
    """Map each declared param to the capture-group index its first
    placeholder occurrence gets in ``send_regex``'s inversion.

    ``send_regex`` turns every placeholder occurrence into one capture
    group, numbered in template order — which is not necessarily the
    order the params dict declares them in. A consumer resolving a
    declared ``sets: {var: "{param}"}`` reference needs the group that
    actually holds the param's wire value."""
    groups: dict[str, int] = {}
    for idx, m in enumerate(_placeholders_of(template, params), start=1):
        groups.setdefault(m.group(1), idx)
    return groups
