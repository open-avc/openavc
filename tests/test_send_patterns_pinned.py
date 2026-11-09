"""What the send-template inversion does at the edges the simulator tests never reach.

``send_regex`` turns a command's ``send`` template back into the regex the
auto-generated simulator matches an incoming line with, and the validator
checks its coverage with. It is the decode twin of ``safe_substitute``: what
the runtime puts on the wire for a template, the inversion of that template
must match. These pin that agreement for each parameter type and format spec,
the capture class each one inverts to (a boolean with a numeric spec among
them), the terminators dropped, the passthrough test, which spec and which
capture group a parameter gets, and where the two disagree today (a
placeholder name that is not an identifier). On an invented Acme device.
"""

from __future__ import annotations

import re

import pytest

from openavc.drivers.compiled_protocol import safe_substitute
from openavc.simulator.send_templates import (
    is_passthrough_send,
    send_param_groups,
    send_param_specs,
    send_regex,
    spec_int_base,
)


def _param(ptype: str) -> dict:
    return {"v": {"type": ptype}}


# ── The round trip: what safe_substitute sends, send_regex matches ──


@pytest.mark.parametrize(
    "template, ptype, value",
    [
        ("VOL {v}", "integer", 26),
        ("VOL {v}", "integer", -12),
        ("VOL {v:03d}", "integer", 7),
        ("MVL{v:02X}", "integer", 26),
        ("MVL{v:x}", "integer", 255),
        ("OCT{v:o}", "integer", 8),
        ("BIN{v:08b}", "integer", 5),
        ("CH{v}", "child_id", 3),
        ("CH{v:02d}", "child_id", 3),
        ("CH{v:X}", "child_id", 12),
        ("LVL {v}", "number", -7.5),
        ("LVL {v:.1f}", "number", 2.25),
        ("FLAG{v:d}", "boolean", True),
        ("FLAG{v:02d}", "boolean", False),
        ("FLAG{v:02X}", "boolean", True),
        ("FLAG{v}", "boolean", "true"),
        ("FLAG{v}", "boolean", 0),
        ("NAME {v}", "string", "Lead (Vox)"),
        ("s({v})*", "string", "a.b"),
        ("SEL{v}", "enum", "HDMI1"),
    ],
)
def test_the_inversion_matches_what_the_substitution_sends(template, ptype, value):
    params = _param(ptype)
    wire = safe_substitute(template, {"v": value})
    assert re.fullmatch(send_regex(template, params), wire)


@pytest.mark.parametrize(
    "template, ptype, value, wire",
    [
        # A Python bool fills a bare placeholder as "True": the inversion's
        # word set is lower case, so the line goes unmatched.
        ("FLAG{v}", "boolean", True, "FLAGTrue"),
        # An integer captured as a run of digits never takes a sign, and a
        # hex value with a sign is not a run of hex digits.
        ("CH{v}", "child_id", -3, "CH-3"),
        ("MVL{v:02X}", "integer", -26, "MVL-1A"),
        # Exponent notation is not a run of digits and dots.
        ("LVL {v:e}", "number", 1.5, "LVL 1.500000e+00"),
    ],
)
def test_where_the_inversion_does_not_match_the_substitution(template, ptype, value, wire):
    assert safe_substitute(template, {"v": value}) == wire
    assert not re.fullmatch(send_regex(template, _param(ptype)), wire)


# ── The capture class each parameter type and spec inverts to ──


@pytest.mark.parametrize(
    "spec, capture",
    [
        ("", r"(true|false|0|1)"),
        ("d", r"(\d+)"),
        ("02d", r"(\d+)"),
        ("x", r"([0-9a-fA-F]+)"),
        ("02X", r"([0-9a-fA-F]+)"),
        ("#x", r"([0-9a-fA-F]+)"),
        ("o", r"([0-7]+)"),
        ("08b", r"([01]+)"),
        ("s", r"(true|false|0|1)"),
        (">3", r"(true|false|0|1)"),
        ("c", r"(true|false|0|1)"),
        ("e", r"(true|false|0|1)"),
    ],
)
def test_a_boolean_inverts_by_its_spec(spec, capture):
    template = "F{v:" + spec + "}" if spec else "F{v}"
    assert send_regex(template, _param("boolean")) == "F" + capture


def test_an_empty_spec_is_a_bare_placeholder():
    assert send_regex("F{v:}", _param("boolean")) == r"F(true|false|0|1)"
    assert send_regex("F{v:}", _param("integer")) == r"F(-?\d+)"


@pytest.mark.parametrize(
    "ptype, captures",
    [
        ("integer", [r"(-?\d+)", r"([0-9a-fA-F]+)", r"([0-7]+)", r"([01]+)", r"(-?\d+)", r"(-?\d+)"]),
        ("child_id", [r"(\d+)", r"([0-9a-fA-F]+)", r"([0-7]+)", r"([01]+)", r"(\d+)", r"(\d+)"]),
        ("number", [r"(-?[\d.]+)"] * 6),
        ("string", [r"(.+)"] * 6),
        ("enum", [r"(.+)"] * 6),
        ("acme_kind", [r"(.+)"] * 6),
    ],
)
def test_each_type_inverts_by_its_spec(ptype, captures):
    specs = ["", "X", "o", "b", "03d", ".1f"]
    got = [send_regex("{v:" + s + "}" if s else "{v}", _param(ptype)) for s in specs]
    assert got == captures


def test_a_param_with_no_type_or_no_definition_is_a_string():
    assert send_regex("A{v}", {"v": "not a definition"}) == "A(.+)"
    assert send_regex("A{v}", {"v": {}}) == "A(.+)"


def test_every_regex_special_in_the_literal_text_is_escaped_but_a_backslash():
    assert send_regex("^$.*+?|[]{}()\\", {}) == r"\^\$\.\*\+\?\|\[\]\{\}\(\)" + "\\"


def test_each_occurrence_is_its_own_capture_and_an_undeclared_token_is_text():
    params = {"a": {"type": "integer"}, "b": {"type": "string"}}
    assert send_regex("{a}{x}{b}{a}", params) == r"(-?\d+)\{x\}(.+)(-?\d+)"


# ── Terminators ──


@pytest.mark.parametrize(
    "template, expected",
    [
        ("X \\r\\n", "X"),
        ("X\r\n\\r", "X"),
        ("X\\n \\r ", "X"),
        ("\\r", ""),
        ("  ", ""),
        # Only trailing ones go; an escape mid-template stays.
        ("X\\r\\n\\rY\\r", "X\\r\\n\\rY"),
        # The two characters before the end are a backslash and an r, so they
        # go, leaving a lone backslash that no regex compiles.
        ("X\\\\r", "X\\"),
    ],
)
def test_trailing_terminators_are_dropped(template, expected):
    assert send_regex(template, {}) == expected


def test_a_template_ending_in_a_lone_backslash_inverts_to_a_pattern_that_does_not_compile():
    with pytest.raises(re.error):
        re.compile(send_regex("X\\\\r", {}))


# ── Passthrough ──


@pytest.mark.parametrize(
    "template, expected",
    [
        ("{c}\\r", True),
        (" {c:02d} \\n ", True),
        ("{c}", True),
        ("{c}\r\n", True),
        ("{d}", False),
        ("X{c}", False),
        ("{c}{c}", False),
        ("{c:{x}}", False),
        ("", False),
    ],
)
def test_a_passthrough_is_one_declared_param_and_its_terminators(template, expected):
    assert is_passthrough_send(template, {"c": {}}) is expected


# ── Which spec and which capture group a param gets ──


def test_a_param_takes_the_spec_of_its_first_occurrence():
    specs = send_param_specs("{v:02X}{v:d}{volume:03d}", {"v": {}, "volume": {}, "vol": {}})
    assert specs == {"v": "02X", "volume": "03d", "vol": ""}


def test_a_param_takes_its_first_occurrences_group_in_template_order():
    assert send_param_groups("{a}{x}{b}{a}", {"a": {}, "b": {}}) == {"a": 1, "b": 2}
    assert send_param_groups("{b}{a:02d}", {"a": {}, "b": {}}) == {"b": 1, "a": 2}


def test_an_empty_spec_still_counts_as_the_params_placeholder():
    assert send_param_specs("{v:}", {"v": {}}) == {"v": ""}
    assert send_param_groups("{a:}{b}", {"a": {}, "b": {}}) == {"a": 1, "b": 2}


def test_a_placeholder_name_that_is_not_an_identifier_is_filled_but_not_inverted():
    # safe_substitute fills any word-character name; the inversion reads only
    # an identifier as a placeholder, so the line it is sent as goes
    # unmatched, and the group numbering still counts it.
    assert safe_substitute("A{1x}B", {"1x": 5}) == "A5B"
    assert send_regex("A{1x}B", {"1x": {"type": "integer"}}) == r"A\{1x\}B"
    assert safe_substitute("A{été}B", {"été": 5}) == "A5B"
    assert send_regex("A{été}B", {"été": {"type": "integer"}}) == "A\\{été\\}B"
    assert send_param_groups("{1x}{a}", {"1x": {}, "a": {}}) == {"1x": 1, "a": 2}
    assert send_regex("{1x}{a}", {"1x": {}, "a": {}}) == r"\{1x\}(.+)"


@pytest.mark.parametrize(
    "spec, base",
    [("X", 16), ("#x", 16), ("x", 16), ("o", 8), (">8b", 2), ("d", None), ("c", None), ("", None)],
)
def test_the_base_an_integer_spec_puts_on_the_wire(spec, base):
    assert spec_int_base(spec) == base


# ── A read-back line's placeholders (the same token, read off a query) ──


def test_a_read_back_names_its_child_id_param_as_child_id_and_keeps_the_spec():
    from openavc.simulator.reread_queries import read_back_queries

    definition = {
        "child_entity_types": {"zone": {"state_variables": {"level": {"type": "integer"}}}},
        "commands": {
            "set": {
                "send": "Z{ch} {level}",
                "params": {
                    "ch": {"type": "child_id", "child_type": "zone"},
                    "level": {"type": "integer"},
                },
                "sets": {"level": "{level}"},
                "read_back": [
                    "Z{ch:}?", "Z{ch}?", "Z{ch:02d}?", "Z{ch}{ch:x}?",
                    "Z{ch:{x}}?", "Z{chx}?", "Z{1}?",
                ],
            }
        },
    }
    each = {"each_child": "zone", "query_for": "level"}
    assert read_back_queries(definition) == [
        {**each, "send": "Z{child_id:}?"},
        {**each, "send": "Z{child_id}?"},
        {**each, "send": "Z{child_id:02d}?"},
        {**each, "send": "Z{child_id}{child_id:x}?"},
        # No declared param in it: a line of its own, as written.
        "Z{ch:{x}}?",
        "Z{chx}?",
        "Z{1}?",
    ]
