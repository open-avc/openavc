"""What the reply-text reconstruction does with the patterns nobody else tests.

The auto-generated simulator answers a query by rebuilding the text a driver's
response pattern would match (``emit_template``, ``emit_template_multi``,
``emit_literal``) and filling the result in (``fill_template``). The driver
suites hand it ordinary patterns; these pin each regex construct it models, the
ones it refuses (``None``, so the simulator falls back rather than sending text
the driver would reject), how capture groups are numbered and which one becomes
the slot, the zero-pad width a fixed run of digits carries, and how a width slot
is filled. Patterns are invented, not taken from any driver.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.compiled_protocol import (
    emit_literal,
    emit_template,
    emit_template_multi,
    fill_template,
)


@pytest.mark.parametrize(
    "pattern, group, expected",
    [
        # Flag groups: a global flag emits nothing and opens no group; a scoped
        # one is a non-capturing group of its own.
        (r"(?i)PWR(\d+)", 1, "PWR{value}"),
        (r"(?x)a(\d+)", 1, "a{value}"),
        (r"(?i:x)(\d+)", 1, "x{value}"),
        (r"(?ms:a)(\d+)", 1, "a{value}"),
        # A named group captures, and counts.
        (r"(?P<lvl>\d+) dB", 1, "{value} dB"),
        (r"(?P<lvl\d+)", 1, None),
        # Lookarounds, back-references by name and comments are not modeled.
        (r"(?P=x)(\d+)", 1, None),
        (r"(?<=x)(\d+)", 1, None),
        (r"(?#c)(\d+)", 1, None),
        # Escapes.
        (r"\x41(\d+)", 1, "A{value}"),
        (r"\x4G(\d+)", 1, "x4G{value}"),
        (r"\t\f\v\r\n(\d+)", 1, "\t\x0c\x0b\r\n{value}"),
        (r"\S\s\w\d(\d+)", 1, "0 00{value}"),
        (r"\.\*\#(\d+)", 1, ".*#{value}"),
        (r"a.b(\d+)", 1, "axb{value}"),
        (r"\D(\d+)", 1, None),
        (r"\W(\d+)", 1, None),
        (r"\A(\d+)\Z", 1, None),
        (r"(\D+)", 1, "{value}"),
        (r"(\d+)\\", 1, "{value}\\"),
        ("(\\d+)\\", 1, None),
        # Anchors anywhere emit nothing.
        (r"^A$B^(\d+)", 1, "AB{value}"),
    ],
)
def test_constructs_outside_a_class(pattern: str, group: int, expected: Any) -> None:
    assert emit_template(pattern, group) == expected


@pytest.mark.parametrize(
    "pattern, expected",
    [
        (r"[abc](\d+)", "a{value}"),
        (r"[\d](\d+)", "0{value}"),
        (r"[\s](\d+)", " {value}"),
        (r"[\n](\d+)", "\n{value}"),
        (r"[\t](\d+)", "\t{value}"),
        (r"[\.](\d+)", ".{value}"),
        (r"[\D](\d+)", None),
        (r"[\W](\d+)", None),
        # A leading ] is a member, negated or not.
        (r"[]a](\d+)", "]{value}"),
        (r"[]](\d+)", "]{value}"),
        (r"[^]a](\d+)", None),
        # An escaped ] does not close the class.
        (r"[a\]b](\d+)", "a{value}"),
        # A class that never closes.
        (r"[abc(\d+)", None),
        (r"[](\d+)", None),
        (r"[\](\d+)", None),
    ],
)
def test_a_class_emits_its_first_member(pattern: str, expected: Any) -> None:
    assert emit_template(pattern) == expected


@pytest.mark.parametrize(
    "pattern, expected",
    [
        (r"a{3}(\d+)", "aaa{value}"),
        (r"a{2,}(\d+)", "aa{value}"),
        (r"a{2,5}(\d+)", "aa{value}"),
        (r"a{8}(\d+)", "aaaaaaaa{value}"),
        (r"a{9}(\d+)", None),
        # A brace that is not a quantifier is a literal.
        (r"a{,3}(\d+)", "a{,3}{value}"),
        (r"a{(\d+)", "a{{value}"),
        # Lazy forms repeat their minimum too.
        (r"a*?b+?c??(\d+)", "b{value}"),
        (r"a{2}?(\d+)", "aa{value}"),
        (r"(?:ab){2}(\d+)", "abab{value}"),
        # Repeated zero times, an unrepresentable atom is dropped; once, it
        # survives and sinks the emission.
        (r"[^x]{0}(\d+)", "{value}"),
        (r"[^x]?(\d+)", "{value}"),
        (r"[^x]+(\d+)", None),
    ],
)
def test_quantifiers_repeat_their_minimum(pattern: str, expected: Any) -> None:
    assert emit_template(pattern) == expected


@pytest.mark.parametrize(
    "pattern, expected",
    [
        # A target that may be absent, or repeated at least once, is kept once.
        (r"(\d+)?", "{value}"),
        (r"(\d+)*", "{value}"),
        (r"(\d+)+", "{value}"),
        (r"(\d+){0}", "{value}"),
        (r"(\d+){1}", "{value}"),
        (r"(?:x(\d+))?y", "x{value}y"),
        # One that must repeat would put the field in twice.
        (r"(\d+){2}", None),
        (r"(?:x(\d+)){2}", None),
    ],
)
def test_a_target_under_a_quantifier(pattern: str, expected: Any) -> None:
    assert emit_template(pattern) == expected


@pytest.mark.parametrize(
    "pattern, group, expected",
    [
        (r"(\d+))", 1, None),
        (r"((\d+)", 1, None),
        (r"x)", 1, None),
        ("", 1, None),
        (r"(\d+)", 0, None),
        (r"x(\d+)", -1, None),
        # Alternation: the branch carrying the slot, else the first one that
        # can be emitted, else nothing.
        (r"A|(\d+)", 1, "{value}"),
        (r"(?:x|y)(\d+)", 1, "x{value}"),
        (r"[^a]|b(\d+)", 1, "b{value}"),
        (r"(?:[^a]|[^b])(\d+)", 1, None),
        (r"a||b(\d+)", 1, "b{value}"),
        (r"(|x)", 1, "{value}"),
        (r"(x|)", 1, "{value}"),
        (r"()", 1, "{value}"),
        # Found, but in a branch that was not chosen.
        (r"[^a](\d+)|b", 1, None),
        # Groups are numbered by their opening parenthesis, across branches.
        (r"(a)|(\d+)", 2, "{value}"),
        (r"((\d+) (\w+))", 1, "{value}"),
        (r"((\d+) (\w+))", 2, "{value} 0"),
        (r"((\d+) (\w+))", 3, "0 {value}"),
    ],
)
def test_structure_and_group_numbering(pattern: str, group: int, expected: Any) -> None:
    assert emit_template(pattern, group) == expected


@pytest.mark.parametrize(
    "pattern, expected",
    [
        (r"(\d)", "{value}"),
        (r"(\d{2}\d)", "{value:03d}"),
        (r"([0-9]\d{2})", "{value:03d}"),
        (r"(01)", "{value:02d}"),
        (r"(0)", "{value}"),
        (r"(\d{0})", "{value}"),
        (r"(\d{2}x)", "{value}"),
        (r"(?:(\d{3}))", "{value:03d}"),
        (r"(?P<a>\d{4})", "{value:04d}"),
    ],
)
def test_the_width_a_fixed_run_of_digits_carries(pattern: str, expected: Any) -> None:
    assert emit_template(pattern) == expected


@pytest.mark.parametrize(
    "pattern, placeholders, expected",
    [
        (r"Out(\d+) In(\d+)", {1: "{child_id}", 2: "{value}"}, "Out{child_id} In{value}"),
        (r"Out(\d+) In(\d+)", {1: "{child_id}", 3: "{value}"}, None),
        (r"Out(\d{2}) In(\d{3})", {1: "07", 2: "{value}"}, "Out07 In{value:03d}"),
        (r"Out(\d{2}) In(\d{3})", {1: "{x:1}", 2: "{value}"}, "Out{x:1} In{value:03d}"),
        (r"L(\d{3})(\d{2})", {2: "{value}", 1: "{child_id}"}, "L{child_id:03d}{value:02d}"),
        (r"(\d{2})", {1: "{value}{value}"}, "{value}{value}"),
        # With no slots, a group emits a representative.
        (r"Out(\d+)", {}, "Out0"),
        (r"Out", {}, "Out"),
        # Both slots must survive; branches carry one each here.
        (r"(?:A(\d+)|B(\d+))", {1: "{a}", 2: "{b}"}, None),
    ],
)
def test_several_slots_in_one_emission(
    pattern: str, placeholders: dict[int, str], expected: Any
) -> None:
    assert emit_template_multi(pattern, placeholders) == expected


def test_a_slot_dict_is_not_changed() -> None:
    placeholders = {1: "{child_id}", 2: "{value}"}
    emit_template_multi(r"Out(\d{2}) In(\d{3})", placeholders)
    assert placeholders == {1: "{child_id}", 2: "{value}"}


@pytest.mark.parametrize(
    "pattern, expected",
    [
        (r"(?:x)y", "xy"),
        (r"(?i)abc", "abc"),
        (r"a|b", "a"),
        (r"a|[^b]", "a"),
        (r"x{2}y", "xxy"),
        (r"a\\", "a\\"),
        (r"\x00\x7f", "\x00\x7f"),
        ("", ""),
        # A named group captures, so a fixed literal cannot stand for it.
        (r"(?P<a>x)", None),
        (r"[^a]b", None),
        (r"(x", None),
        (r"x)", None),
        (r"a{9}", None),
        (r"\1", None),
    ],
)
def test_emit_literal(pattern: str, expected: Any) -> None:
    assert emit_literal(pattern) == expected


@pytest.mark.parametrize(
    "template, values, expected",
    [
        ("GAIN {value:03d}", {"value": True}, "GAIN True"),
        ("GAIN {value:03d}", {"value": "-5"}, "GAIN -05"),
        ("GAIN {value:03d}", {"value": -7}, "GAIN -07"),
        ("GAIN {value:03d}", {"value": 2.5}, "GAIN 2.5"),
        ("{value:03d}", {"value": " 5"}, " 5"),
        ("{value:03d}", {"value": "+5"}, "+5"),
        ("{value:03d}", {"value": float("nan")}, "nan"),
        ("{value:00d}", {"value": 5}, "5"),
        ("{value:010d}", {"value": 5}, "0000000005"),
        # Only a {name:0Nd} spec is a width slot; any other spec is text.
        ("GAIN {value:3d}", {"value": 2}, "GAIN {value:3d}"),
        ("GAIN {value:03x}", {"value": 2}, "GAIN {value:03x}"),
        ("{a}{b}", {"a": 1, "b": None}, "1None"),
        ("{value}", {"value": False}, "False"),
        ("{ value }", {"value": 1}, "{ value }"),
    ],
)
def test_fill_template(template: str, values: dict[str, Any], expected: str) -> None:
    assert fill_template(template, values) == expected
