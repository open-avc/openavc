"""The runtime gate for command parameters and device-setting values, called
directly: what each declared type takes, what it is normalized to, and the
words a refusal uses.

The doors that reach it (Send Command, a settings write, the audit) are
tested where they live; this pins the rules themselves, including the cases
no door happens to send.
"""

from __future__ import annotations

import pytest

from openavc.drivers.base import (
    CommandParamError,
    DeviceSettingValueError,
    missing_required_params,
    normalize_and_validate_command_params,
    validate_device_setting_value,
)


# --- Device settings ---


def test_a_setting_without_a_schema_passes_its_value_through() -> None:
    marker = object()
    assert validate_device_setting_value("k", None, marker) is marker
    assert validate_device_setting_value("k", "text", marker) is marker


def test_a_setting_refuses_no_value() -> None:
    with pytest.raises(DeviceSettingValueError, match=r"^'k': a value is required$"):
        validate_device_setting_value("k", {"type": "string"}, None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True), (False, False), (1, True), (0, False), (1.0, True),
        (" YES ", True), ("On", True), ("1", True), ("no", False), (" off", False),
        ("0", False), ("FALSE", False),
    ],
)
def test_a_boolean_setting_takes_the_usual_spellings(value, expected) -> None:
    assert validate_device_setting_value("k", {"type": "boolean"}, value) is expected


@pytest.mark.parametrize("value", [2, -1, 0.5, "maybe", "", [1], {"a": 1}])
def test_a_boolean_setting_refuses_anything_else(value) -> None:
    with pytest.raises(DeviceSettingValueError) as caught:
        validate_device_setting_value("k", {"type": "boolean"}, value)
    assert str(caught.value) == f"'k' must be true or false, got {value!r}"


@pytest.mark.parametrize(
    ("stype", "value", "expected", "kind"),
    [
        ("integer", 5, 5, int),
        ("integer", 5.0, 5, int),
        ("integer", " 7 ", 7, int),
        ("integer", "7.0", 7, int),
        ("number", 2, 2, int),
        ("number", " 2.5 ", 2.5, float),
        ("number", "3", 3.0, float),
        ("float", 1.25, 1.25, float),
    ],
)
def test_a_numeric_setting_is_written_as_its_type(stype, value, expected, kind) -> None:
    out = validate_device_setting_value("k", {"type": stype}, value)
    assert out == expected
    assert type(out) is kind


@pytest.mark.parametrize(
    ("stype", "value", "kind"),
    [
        ("integer", 5.5, "whole number"),
        ("integer", "5.5", "whole number"),
        ("integer", True, "whole number"),
        ("integer", "abc", "whole number"),
        ("number", "abc", "number"),
        ("number", "", "number"),
        ("number", [1], "number"),
        ("float", False, "number"),
    ],
)
def test_a_numeric_setting_refuses_what_is_not_a_number(stype, value, kind) -> None:
    with pytest.raises(DeviceSettingValueError) as caught:
        validate_device_setting_value("k", {"type": stype}, value)
    assert str(caught.value) == f"'k' must be a {kind}, got {value!r}"


def test_a_numeric_setting_honours_its_range_inclusively() -> None:
    sdef = {"type": "integer", "min": 1, "max": 10}
    assert validate_device_setting_value("k", sdef, 1) == 1
    assert validate_device_setting_value("k", sdef, 10) == 10
    with pytest.raises(DeviceSettingValueError, match=r"^'k' must be at least 1, got 0$"):
        validate_device_setting_value("k", sdef, 0)
    with pytest.raises(DeviceSettingValueError, match=r"^'k' must be at most 10, got 11$"):
        validate_device_setting_value("k", sdef, "11")
    with pytest.raises(DeviceSettingValueError, match=r"^'k' must be at most 1.5, got 1.75$"):
        validate_device_setting_value("k", {"type": "number", "max": 1.5}, 1.75)


def test_a_range_bound_that_is_not_a_number_is_ignored() -> None:
    sdef = {"type": "integer", "min": "1", "max": None}
    assert validate_device_setting_value("k", sdef, -50) == -50


def test_a_text_setting_is_trimmed_and_stringified() -> None:
    assert validate_device_setting_value("k", {"type": "string"}, "  hi  ") == "hi"
    assert validate_device_setting_value("k", {}, 42) == "42"


def test_a_setting_list_takes_a_label_and_writes_the_wire_value() -> None:
    sdef = {"type": "string", "values": [{"value": "0f", "label": "Stereo"}, "mono"]}
    assert validate_device_setting_value("k", sdef, " Stereo ") == "0f"
    assert validate_device_setting_value("k", sdef, "0f") == "0f"
    assert validate_device_setting_value("k", sdef, "mono") == "mono"
    with pytest.raises(DeviceSettingValueError) as caught:
        validate_device_setting_value("k", sdef, "surround")
    assert str(caught.value) == "'k' must be one of: 0f, mono — got 'surround'"


def test_a_setting_list_wins_over_a_declared_format() -> None:
    sdef = {"type": "string", "values": ["a"], "regex": "b"}
    assert validate_device_setting_value("k", sdef, "a") == "a"


@pytest.mark.parametrize("key", ["regex", "pattern"])
def test_a_text_setting_must_match_its_format(key) -> None:
    sdef = {"type": "string", key: r"[0-9]{3}"}
    assert validate_device_setting_value("k", sdef, " 123 ") == "123"
    with pytest.raises(DeviceSettingValueError) as caught:
        validate_device_setting_value("k", sdef, "12a")
    assert str(caught.value) == (
        "'k' value '12a' does not match the required format ([0-9]{3})"
    )


def test_a_blank_text_setting_skips_its_format() -> None:
    assert validate_device_setting_value("k", {"regex": r"[0-9]+"}, "   ") == ""


def test_a_malformed_setting_format_refuses_nothing() -> None:
    assert validate_device_setting_value("k", {"regex": "("}, "anything") == "anything"


# --- Command parameters ---


def test_params_without_a_schema_come_back_as_they_were() -> None:
    params = {"a": " x "}
    assert normalize_and_validate_command_params("c", None, params) is params
    assert normalize_and_validate_command_params("c", ["a"], params) is params


def test_the_first_missing_required_param_is_named() -> None:
    defs = {"a": {"required": True}, "b": {"required": True, "type": "integer"}}
    assert missing_required_params(defs, {"b": " "}) == ["a", "b"]
    with pytest.raises(CommandParamError, match=r"^c: A is required\.$"):
        normalize_and_validate_command_params("c", defs, {"b": " "})


def test_every_unsupplied_optional_param_gets_its_default() -> None:
    defs = {
        "a": {"type": "integer", "default": 3},
        "b": {"type": "string", "default": "x"},
        "c": {"type": "integer", "default": 9},
        "d": {"type": "integer"},
        "e": {"type": "integer", "default": 1, "required": True},
        "f": "not a definition",
    }
    params = {"c": 4, "e": 2}
    out = normalize_and_validate_command_params("cmd", defs, params)
    assert out == {"a": 3, "b": "x", "c": 4, "e": 2}
    assert params == {"c": 4, "e": 2}
    assert normalize_and_validate_command_params("cmd", {"a": {"default": 1}}, None) == {"a": 1}
    assert normalize_and_validate_command_params("cmd", {"a": {}}, None) is None
    assert normalize_and_validate_command_params("cmd", {"a": {}}, {}) == {}


def test_a_none_value_and_an_undeclared_param_are_left_alone() -> None:
    defs = {"a": {"type": "integer", "min": 5}, "b": "not a definition"}
    out = normalize_and_validate_command_params("c", defs, {"a": None, "b": " y ", "z": " q "})
    assert out == {"a": None, "b": " y ", "z": " q "}


def test_text_is_trimmed_unless_the_param_says_not_to() -> None:
    defs = {"a": {"type": "string"}, "b": {"type": "string", "trim": False}}
    out = normalize_and_validate_command_params("c", defs, {"a": "  x ", "b": " y\r\n"})
    assert out == {"a": "x", "b": " y\r\n"}


def test_a_blank_optional_is_kept_blank_and_not_checked() -> None:
    defs = {"n": {"type": "integer", "min": 1}, "s": {"pattern": "[0-9]+"}}
    assert normalize_and_validate_command_params("c", defs, {"n": "  ", "s": ""}) == {
        "n": "",
        "s": "",
    }


@pytest.mark.parametrize(
    ("pdef", "value", "expected"),
    [
        ({"type": "integer"}, 26.0, 26),
        ({"type": "integer"}, "26", "26"),
        ({"type": "integer"}, " 026 ", "026"),
        ({"type": "number"}, 2.345, 2.345),
        ({"type": "number", "decimals": 2}, 2.345, 2.35),
        ({"type": "number", "decimals": 0}, 2.6, 3),
        ({"type": "number", "decimals": -1}, 2.4, 2),
        ({"type": "number", "decimals": "2"}, 2.345, 2.345),
        ({"type": "float", "decimals": 1}, 7, 7.0),
        ({"type": "number", "decimals": 1}, "2.345", "2.345"),
    ],
)
def test_a_number_is_sent_as_its_declared_type(pdef, value, expected) -> None:
    out = normalize_and_validate_command_params("c", {"p": pdef}, {"p": value})
    assert out["p"] == expected
    assert type(out["p"]) is type(expected)


@pytest.mark.parametrize(
    ("ptype", "value", "kind"),
    [
        ("integer", 5.5, "whole number"),
        ("integer", True, "whole number"),
        ("number", "abc", "number"),
        ("number", float("nan"), "number"),
        ("float", float("inf"), "number"),
        ("number", "nan", "number"),
        ("number", float("-inf"), "number"),
        ("float", "inf", "number"),
        ("number", "-inf", "number"),
    ],
)
def test_a_number_param_refuses_what_is_not_one(ptype, value, kind) -> None:
    with pytest.raises(CommandParamError) as caught:
        normalize_and_validate_command_params("c", {"p": {"type": ptype}}, {"p": value})
    assert str(caught.value) == f"'c': 'p' must be a {kind}, got {value!r}"


def test_a_number_param_honours_its_range_inclusively() -> None:
    defs = {"p": {"type": "integer", "min": 1, "max": 8}}
    assert normalize_and_validate_command_params("c", defs, {"p": 1}) == {"p": 1}
    assert normalize_and_validate_command_params("c", defs, {"p": 8}) == {"p": 8}
    with pytest.raises(CommandParamError, match=r"^'c': 'p' must be at least 1, got 0$"):
        normalize_and_validate_command_params("c", defs, {"p": 0})
    with pytest.raises(CommandParamError, match=r"^'c': 'p' must be at most 8, got 99$"):
        normalize_and_validate_command_params("c", defs, {"p": "99"})


def test_a_label_is_sent_as_its_wire_value() -> None:
    defs = {"m": {"type": "enum", "values": [{"value": "0f", "label": "Stereo"}, "mono"]}}
    assert normalize_and_validate_command_params("c", defs, {"m": " Stereo "}) == {"m": "0f"}
    assert normalize_and_validate_command_params("c", defs, {"m": "other"}) == {"m": "other"}
    assert normalize_and_validate_command_params("c", defs, {"m": 5}) == {"m": 5}


def test_a_param_must_match_its_pattern_after_its_label_is_mapped() -> None:
    defs = {"m": {"values": [{"value": "12", "label": "Twelve"}], "pattern": "[0-9]+"}}
    assert normalize_and_validate_command_params("c", defs, {"m": "Twelve"}) == {"m": "12"}
    with pytest.raises(CommandParamError) as caught:
        normalize_and_validate_command_params("c", defs, {"m": "x1"})
    assert str(caught.value) == "'c': 'm' value 'x1' does not match the required format ([0-9]+)"


def test_a_non_text_value_skips_the_pattern() -> None:
    defs = {"m": {"pattern": "[a-z]+"}}
    assert normalize_and_validate_command_params("c", defs, {"m": 7}) == {"m": 7}


def test_a_malformed_param_pattern_refuses_nothing() -> None:
    defs = {"m": {"pattern": "("}}
    assert normalize_and_validate_command_params("c", defs, {"m": "x"}) == {"m": "x"}
