"""How a value a device sent becomes the type its state variable declares.

Three coercers, one per wire form: ``coerce_value`` for text (a regex capture
or a static value), ``coerce_json_value`` for a parsed JSON body and
``coerce_osc_value`` for an OSC argument. They agree on the type names
(``integer``, ``float`` / ``number``, ``boolean``, anything else as text) and
differ on purpose at the edges: what each does with a value that will not
convert, which exceptions it absorbs, whether it warns, and what a bool or a
None becomes. These pin every branch of all three, their warnings, the
boolean-token reader they share, and the inverse of a setting's ``map``
(``value_for_map_word``). On an invented Acme device.
"""

from __future__ import annotations

import logging
import math

import pytest

from openavc.drivers.compiled_protocol import (
    coerce_bool_token,
    coerce_json_value,
    coerce_osc_value,
    coerce_value,
    is_bool_token,
    value_for_map_word,
)

_LOGGER = "openavc.drivers.compiled_protocol"


def _typed(value):
    return (value, type(value).__name__)


# ── Text (a regex capture or a static value) ──


@pytest.mark.parametrize(
    "raw, value_type, expected",
    [
        ("5", "integer", (5, "int")),
        (" 5 ", "integer", (5, "int")),
        ("1e3", "float", (1000.0, "float")),
        ("2.5", "number", (2.5, "float")),
        ("01", "boolean", (True, "bool")),
        ("on", "boolean", (True, "bool")),
        ("abc", "boolean", (False, "bool")),
        ("abc", "string", ("abc", "str")),
        ("abc", "enum", ("abc", "str")),
        ("abc", "acme_kind", ("abc", "str")),
        # Anything not text passes through untouched as a string or enum.
        (5, "string", (5, "int")),
        (None, "string", (None, "NoneType")),
    ],
)
def test_text_is_read_as_its_declared_type(raw, value_type, expected):
    assert _typed(coerce_value(raw, value_type)) == expected


@pytest.mark.parametrize(
    "raw, value_type, message",
    [
        ("5.0", "integer", "Cannot coerce '5.0' to integer, returning raw string"),
        ("x", "integer", "Cannot coerce 'x' to integer, returning raw string"),
        ("x", "float", "Cannot coerce 'x' to float, returning raw string"),
        ("x", "number", "Cannot coerce 'x' to number, returning raw string"),
    ],
)
def test_text_that_will_not_convert_is_kept_and_said(raw, value_type, message, caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert _typed(coerce_value(raw, value_type)) == (raw, "str")
    assert [r.getMessage() for r in caplog.records] == [message]


@pytest.mark.parametrize("value_type", ["integer", "float"])
def test_text_only_absorbs_a_value_error(value_type):
    with pytest.raises(TypeError):
        coerce_value([1], value_type)


# ── A parsed JSON body ──


@pytest.mark.parametrize(
    "value, value_type, expected",
    [
        (None, "integer", (None, "NoneType")),
        (None, "string", (None, "NoneType")),
        (None, "boolean", (None, "NoneType")),
        (True, "boolean", (True, "bool")),
        ("01", "boolean", (True, "bool")),
        (0, "boolean", (False, "bool")),
        ("x", "boolean", (False, "bool")),
        (True, "integer", (1, "int")),
        (False, "integer", (0, "int")),
        (5, "integer", (5, "int")),
        ("5", "integer", (5, "int")),
        (" 5 ", "integer", (5, "int")),
        ("5.7", "integer", (5, "int")),
        (5.7, "integer", (5, "int")),
        (-5.7, "integer", (-5, "int")),
        (5, "float", (5.0, "float")),
        ("2.5", "number", (2.5, "float")),
        (True, "float", (1.0, "float")),
        (True, "string", ("true", "str")),
        (False, "enum", ("false", "str")),
        (5, "string", ("5", "str")),
        (2.5, "string", ("2.5", "str")),
        ({"a": 1}, "string", ("{'a': 1}", "str")),
        ([1, 2], "string", ("[1, 2]", "str")),
        ("x", "acme_kind", ("x", "str")),
    ],
)
def test_json_is_read_as_its_declared_type(value, value_type, expected):
    assert _typed(coerce_json_value(value, value_type)) == expected


@pytest.mark.parametrize(
    "value, value_type, text, message",
    [
        ("x", "integer", "x", "Cannot coerce 'x' to integer, returning string"),
        ([1], "integer", "[1]", "Cannot coerce [1] to integer, returning string"),
        (float("nan"), "integer", "nan", "Cannot coerce nan to integer, returning string"),
        ("x", "float", "x", "Cannot coerce 'x' to float, returning string"),
        ([1], "number", "[1]", "Cannot coerce [1] to number, returning string"),
    ],
)
def test_json_that_will_not_convert_becomes_text_and_is_said(value, value_type, text, message, caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert _typed(coerce_json_value(value, value_type)) == (text, "str")
    assert [r.getMessage() for r in caplog.records] == [message]


@pytest.mark.parametrize("value", [math.inf, "1e400"])
def test_json_infinity_for_an_integer_escapes_as_an_overflow(value):
    with pytest.raises(OverflowError):
        coerce_json_value(value, "integer")


# ── An OSC argument ──


@pytest.mark.parametrize(
    "value, value_type, expected",
    [
        (5, "float", (5.0, "float")),
        ("2.5", "number", (2.5, "float")),
        # A value that will not convert is returned as it came, unsaid.
        ("x", "float", ("x", "str")),
        (None, "float", (None, "NoneType")),
        ([1], "number", ([1], "list")),
        (5.9, "integer", (5, "int")),
        ("5", "integer", (5, "int")),
        ("x", "integer", ("x", "str")),
        (None, "integer", (None, "NoneType")),
        (True, "integer", (1, "int")),
        (True, "boolean", (True, "bool")),
        (False, "boolean", (False, "bool")),
        (1, "boolean", (True, "bool")),
        (0.0, "boolean", (False, "bool")),
        (2.5, "boolean", (True, "bool")),
        ("on", "boolean", (True, "bool")),
        ("01", "boolean", (True, "bool")),
        ("x", "boolean", (False, "bool")),
        (None, "boolean", (False, "bool")),
        (5, "string", ("5", "str")),
        (None, "string", (None, "NoneType")),
        (True, "string", ("True", "str")),
        (2.5, "enum", ("2.5", "str")),
        (b"ab", "string", ("b'ab'", "str")),
    ],
)
def test_osc_is_read_as_its_declared_type(value, value_type, expected, caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert _typed(coerce_osc_value(value, value_type)) == expected
    assert caplog.records == []


# ── The boolean token all three read ──


@pytest.mark.parametrize(
    "raw, is_token, value",
    [
        ("true", True, True),
        ("YES", True, True),
        (" on ", True, True),
        ("off", True, False),
        ("No", True, False),
        ("1", True, True),
        ("01", True, True),
        ("0", True, False),
        ("-0.0", True, False),
        ("1e3", True, True),
        ("nan", True, True),
        ("inf", True, True),
        ("abc", False, False),
        ("", False, False),
        (None, False, False),
        (True, True, True),
        (False, True, False),
        (5, True, True),
    ],
)
def test_the_boolean_token(raw, is_token, value):
    assert is_bool_token(raw) is is_token
    assert coerce_bool_token(raw) is value


# ── A device's word back to the setting value (the inverse of map) ──


_MAP = {"true": "ON", "false": "OFF", "Lo": "low", "LO": "LOW", 1: "One", "x": "Mid"}


@pytest.mark.parametrize(
    "word, expected",
    [
        ("ON", (True, "true")),
        ("on", (True, "true")),
        (" ON ", (True, "true")),
        # An exact match anywhere beats a match ignoring case earlier.
        ("low", (True, "Lo")),
        ("LOW", (True, "LO")),
        ("Low", (True, "Lo")),
        ("One", (True, 1)),
        ("one", (True, 1)),
        ("mid", (True, "x")),
        ("nope", (False, None)),
        (1, (False, None)),
    ],
)
def test_a_word_back_to_its_setting_value(word, expected):
    assert value_for_map_word(word, _MAP) == expected


def test_a_word_against_no_map_or_several_matches():
    assert value_for_map_word("a", None) == (False, None)
    assert value_for_map_word("a", []) == (False, None)
    assert value_for_map_word("A", {"k1": "a", "k2": "A"}) == (True, "k2")
    assert value_for_map_word("a", {"k1": "A", "k2": "a"}) == (True, "k2")
    assert value_for_map_word("a", {"k1": "A", "k2": "A"}) == (True, "k1")
