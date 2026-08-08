"""The shared child local-id coercion rule and how each surface fails.

A child id arrives untyped from a URL path, a command parameter, or the
wire, and every surface has to turn it into the kind the driver declared.
The rule lives in one place; what a bad id *means* is the caller's call.
These tests pin both halves, because a surface quietly disagreeing about
what counts as a valid id is how a command lands on the wrong child.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from openavc.api.routes.devices import _coerce_local_id
from openavc.core.device_manager import DeviceManager
from openavc.drivers.base import CommandParamError
from openavc.drivers.child_ids import child_id_kind, coerce_child_local_id

INT_TYPE = {"id_format": {"type": "integer"}}
STR_TYPE = {"id_format": {"type": "string"}}


# --- the shared rule ---------------------------------------------------------

@pytest.mark.parametrize("type_def, expected", [
    (None, "integer"),
    ({}, "integer"),
    ({"id_format": {}}, "integer"),
    ({"id_format": None}, "integer"),
    ({"id_format": "nonsense"}, "integer"),
    ({"id_format": {"type": 7}}, "integer"),
    (INT_TYPE, "integer"),
    (STR_TYPE, "string"),
])
def test_undeclared_or_malformed_id_format_means_integer(type_def, expected):
    """An undeclared format is the default, not an error — drivers that
    never write an id_format still get integer children."""
    assert child_id_kind(type_def) == expected


@pytest.mark.parametrize("raw, expected", [
    (3, 3),
    ("3", 3),
    ("003", 3),          # the padded wire form the UI sends back
    ("  7  ", 7),
    (" 003 ", 3),
    ("-2", -2),          # range is the child registry's business, not ours
])
def test_integer_ids_parse_to_int(raw, expected):
    assert coerce_child_local_id(INT_TYPE, raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "abc", "1.5", "3a", None, [], {}])
def test_non_numeric_integer_ids_are_refused(raw):
    assert coerce_child_local_id(INT_TYPE, raw) is None


@pytest.mark.parametrize("raw", [True, False])
def test_bools_are_never_a_child_id(raw):
    """``int(True)`` is 1, so an unguarded bool silently lands on child 1 —
    the same trap BaseDriver._format_child_id refuses at registration."""
    assert coerce_child_local_id(INT_TYPE, raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("gain_1", "gain_1"),
    ("  gain_1  ", "gain_1"),
    (7, "7"),
])
def test_string_ids_are_stripped_text(raw, expected):
    """Valid string ids are [A-Za-z0-9_-] only, so surrounding whitespace is
    never part of a real id and stripping it can only help."""
    assert coerce_child_local_id(STR_TYPE, raw) == expected


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_string_ids_are_refused(raw):
    assert coerce_child_local_id(STR_TYPE, raw) is None


# --- REST: a bad id is a 404 -------------------------------------------------

def test_rest_integer_path_id_coerces():
    assert _coerce_local_id({"outlet": INT_TYPE}, "outlet", "003") == 3


def test_rest_string_path_id_passes_through():
    assert _coerce_local_id({"ch": STR_TYPE}, "ch", "gain_1") == "gain_1"


def test_rest_undeclared_type_defaults_to_integer():
    assert _coerce_local_id({}, "unknown", "5") == 5


def test_rest_bad_integer_path_id_is_404():
    """Not a 400: an id that can't name a child is indistinguishable from
    one that names a child which isn't there."""
    with pytest.raises(HTTPException) as exc:
        _coerce_local_id({"outlet": INT_TYPE}, "outlet", "abc")
    assert exc.value.status_code == 404
    assert "abc" in exc.value.detail
    assert "outlet" in exc.value.detail


# --- command dispatch: a bad id is a rejected parameter ----------------------

def _driver(child_type_def, param_type="child_id"):
    class _D:
        DRIVER_INFO = {
            "commands": {
                "set_level": {"params": {
                    "outlet": {"type": param_type, "child_type": "outlet"},
                    "level": {"type": "integer"},
                }},
            },
            "child_entity_types": {"outlet": child_type_def},
        }
    return _D()


def test_dispatch_coerces_padded_id_and_leaves_other_params():
    out = DeviceManager._coerce_child_id_params(
        _driver(INT_TYPE), "set_level", {"outlet": "003", "level": "50"},
    )
    assert out == {"outlet": 3, "level": "50"}


def test_dispatch_leaves_the_caller_dict_alone():
    params = {"outlet": "003"}
    DeviceManager._coerce_child_id_params(_driver(INT_TYPE), "set_level", params)
    assert params == {"outlet": "003"}


def test_dispatch_passes_string_id_types_through_untouched():
    """String children reach the driver exactly as sent — only the integer
    kind has a padded wire form that needs normalizing."""
    out = DeviceManager._coerce_child_id_params(
        _driver(STR_TYPE), "set_level", {"outlet": "  gain_1  "},
    )
    assert out == {"outlet": "  gain_1  "}


def test_dispatch_ignores_params_that_are_not_child_ids():
    out = DeviceManager._coerce_child_id_params(
        _driver(INT_TYPE, param_type="integer"), "set_level", {"outlet": "003"},
    )
    assert out == {"outlet": "003"}


def test_dispatch_leaves_an_absent_id_alone():
    out = DeviceManager._coerce_child_id_params(
        _driver(INT_TYPE), "set_level", {"outlet": None, "level": 1},
    )
    assert out == {"outlet": None, "level": 1}


def test_dispatch_rejects_a_non_numeric_id():
    with pytest.raises(CommandParamError) as exc:
        DeviceManager._coerce_child_id_params(
            _driver(INT_TYPE), "set_level", {"outlet": "front-left"},
        )
    assert "child id number" in str(exc.value)
    assert "front-left" in str(exc.value)


def test_dispatch_rejects_a_bool_id():
    """Previously skipped, so True reached the driver and either crashed it
    or acted on child 1. It is refused at the gate now, with a message that
    names the parameter."""
    with pytest.raises(CommandParamError) as exc:
        DeviceManager._coerce_child_id_params(
            _driver(INT_TYPE), "set_level", {"outlet": True},
        )
    assert "outlet" in str(exc.value)


# --- command dispatch: the declared range is enforced, not just the kind -----

RANGED_TYPE = {"id_format": {"type": "integer", "min": 1, "max": 32}}


@pytest.mark.parametrize("raw", [1, 32, "01", "032", 17])
def test_dispatch_accepts_ids_inside_the_declared_range(raw):
    out = DeviceManager._coerce_child_id_params(
        _driver(RANGED_TYPE), "set_level", {"outlet": raw},
    )
    assert out["outlet"] == int(raw)


@pytest.mark.parametrize("raw, expected", [(99, 99), ("99", 99), (0, 0), (-3, -3)])
def test_dispatch_rejects_ids_outside_the_declared_range(raw, expected):
    """The finding this closes: an id past the declared max coerced cleanly,
    went on the wire, and the command returned success — the device answered
    with its own error and nothing surfaced it. The bound the driver wrote
    down was the one thing the gate never read."""
    with pytest.raises(CommandParamError) as exc:
        DeviceManager._coerce_child_id_params(
            _driver(RANGED_TYPE), "set_level", {"outlet": raw},
        )
    message = str(exc.value)
    assert "outlet" in message
    assert "between 1 and 32" in message
    assert str(expected) in message


@pytest.mark.parametrize("id_format, raw", [
    # No bounds declared means unbounded, not invalid.
    ({"type": "integer"}, 9999),
    ({"type": "integer", "min": 1}, 9999),
    ({"type": "integer", "max": 32}, -50),
    # A string type's ids are names; min/max describe nothing orderable.
    ({"type": "string", "min": 1, "max": 4}, "main_gain"),
    # A malformed bound is ignored rather than allowed to reject everything.
    ({"type": "integer", "max": "thirty-two"}, 9999),
    ({"type": "integer", "max": True}, 9999),
])
def test_dispatch_only_range_checks_what_the_driver_actually_declared(
    id_format, raw,
):
    out = DeviceManager._coerce_child_id_params(
        _driver({"id_format": id_format}), "set_level", {"outlet": raw},
    )
    assert "outlet" in out


@pytest.mark.parametrize("bounds, raw, expected", [
    ({"min": 1}, 0, "must be at least 1"),
    ({"max": 8}, 9, "must be at most 8"),
])
def test_dispatch_names_only_the_bound_that_exists(bounds, raw, expected):
    """A half-bounded type must not claim a range it never declared."""
    with pytest.raises(CommandParamError) as exc:
        DeviceManager._coerce_child_id_params(
            _driver({"id_format": {"type": "integer", **bounds}}),
            "set_level",
            {"outlet": raw},
        )
    assert expected in str(exc.value)


@pytest.mark.parametrize("info", [
    {},
    {"commands": {}},
    {"commands": {"set_level": "not-a-dict"}},
    {"commands": {"set_level": {}}},
    {"commands": {"set_level": {"params": "not-a-dict"}}},
])
def test_dispatch_tolerates_drivers_without_the_declarations(info):
    class _D:
        DRIVER_INFO = info
    assert DeviceManager._coerce_child_id_params(
        _D(), "set_level", {"outlet": "003"},
    ) == {"outlet": "003"}
