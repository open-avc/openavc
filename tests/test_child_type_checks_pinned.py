"""Pins for a driver's child types and state variables, as they load.

A ``child_entity_types`` entry is checked in this order: its name (text, no
dot, no glob character), that it is a mapping, its ``id_format``, its
``state_variables``, then its ``instances:`` roster (``count_from_state``,
exactly one source, that source's own rule, then ``label``). The driver's own
``state_variables`` are checked earlier in the walk, each variable's fields in
an order of their own. After every command, the references a command's
``child_id`` parameters make into ``child_entity_types`` are checked, the
same function a Python driver's ``DRIVER_INFO`` reaches through
``python_info``.

Each YAML case is a set of top-level keys over one driver. The expected value
is every error ``validate_driver_issues`` reports for the whole definition, in
order, each with the section tag the Driver Builder anchors it to. A case that
raises is pinned by the exception's type. The reference cases record
``child_param_reference_errors``' two lists, and the Python cases what
``python_driver_info_issues`` and ``python_driver_reference_skips`` answer.

These record what the checks say today, so a restructuring that keeps the
contract keeps every message, its position and its tag.
"""
from __future__ import annotations

import datetime
from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import (
    UNEVALUATED_KEY,
    child_param_reference_errors,
    undeclared_child_type_reason,
    validate_driver_definition,
    validate_driver_issues,
)
from openavc.drivers.python_info import (
    UNEVALUATED,
    python_driver_info_issues,
    python_driver_reference_skips,
)

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "config_schema": {"zones": {"type": "integer"}, "mic_ids": {"type": "string"}},
    "default_config": {"poll_interval": 5, "outputs": 4},
    "config_derived": {"inputs": "x"},
    "commands": {"noop": {"send": "NOOP\r"}},
}


def types(**child_types: Any) -> dict[str, Any]:
    return {"child_entity_types": child_types}


def zone(**type_def: Any) -> dict[str, Any]:
    """One child type, ``zone``, declaring what the case gives it."""
    return types(zone=type_def)


def roster(id_format: Any = None, **instances: Any) -> dict[str, Any]:
    """A ``zone`` type with this roster (and this id_format, when given)."""
    type_def: dict[str, Any] = {"instances": instances}
    if id_format is not None:
        type_def["id_format"] = id_format
    return zone(**type_def)


STRING_IDS = {"type": "string"}
CHILD_ID = {"type": "child_id", "child_type": "zone", "label": "Zone"}


def param_cmds(child_types: Any = None, **params: Any) -> dict[str, Any]:
    """A ``route`` command with these params, over these child types."""
    over: dict[str, Any] = {"commands": {"route": {"send": "R\r", "params": params}}}
    if child_types is not None:
        over["child_entity_types"] = child_types
    return over


ZONE_TYPE = {"zone": {"id_format": {"type": "integer", "min": 1, "max": 16}}}

CASES: dict[str, dict[str, Any]] = {
    # --- child_entity_types itself -------------------------------------
    "types_a_list": {"child_entity_types": ["zone"]},
    "types_a_string": {"child_entity_types": "zone"},
    "types_a_number": {"child_entity_types": 3},
    "types_an_empty_list": {"child_entity_types": []},
    "types_zero": {"child_entity_types": 0},
    "types_none": {"child_entity_types": None},
    "types_empty": {"child_entity_types": {}},
    # --- a type's name --------------------------------------------------
    "name_a_number": {"child_entity_types": {5: {}, "ok": {}}},
    "name_a_boolean": {"child_entity_types": {True: {"id_format": 1}}},
    "name_empty": {"child_entity_types": {"": {"id_format": 1}}},
    "name_with_a_dot": types(**{"a.b": {}}),
    "name_with_one_glob": types(**{"a*": {}}),
    "name_with_every_glob": types(**{"[a?*": {}}),
    "name_with_a_dot_and_a_glob": types(**{"a.b?": {"id_format": 1}}),
    "name_with_a_space": types(**{"a b": {}}),
    "name_with_a_dot_and_a_bad_declaration": types(**{"a.b*": 1}),
    # --- the type's declaration ----------------------------------------
    "type_def_a_list": types(zone=["x"]),
    "type_def_none": types(zone=None),
    "type_def_a_string": types(zone="x"),
    "type_def_empty": types(zone={}),
    # --- id_format --------------------------------------------------------
    "id_format_a_list": zone(id_format=["integer"]),
    "id_format_a_string": zone(id_format="integer"),
    "id_format_none": zone(id_format=None),
    "id_format_an_empty_list": zone(id_format=[]),
    "id_format_zero": zone(id_format=0),
    "id_format_empty": zone(id_format={}),
    "id_format_unknown_type": zone(id_format={"type": "uuid"}),
    "id_format_type_a_number": zone(id_format={"type": 3}),
    "id_format_type_a_list": zone(id_format={"type": ["integer"]}),
    "id_format_type_none": zone(id_format={"type": None}),
    "id_format_string": zone(id_format={"type": "string"}),
    "id_format_min_a_float": zone(id_format={"min": 1.5}),
    "id_format_max_a_string": zone(id_format={"max": "16"}),
    "id_format_min_a_boolean": zone(id_format={"min": True, "max": 0}),
    "id_format_max_a_boolean": zone(id_format={"min": 2, "max": False}),
    "id_format_min_above_max": zone(id_format={"min": 9, "max": 2}),
    "id_format_min_equals_max": zone(id_format={"min": 2, "max": 2}),
    "id_format_both_floats": zone(id_format={"min": 9.0, "max": 2.0}),
    "id_format_negative_bounds": zone(id_format={"min": -5, "max": -9}),
    "id_format_pad_zero": zone(id_format={"pad_width": 0}),
    "id_format_pad_two": zone(id_format={"pad_width": 2}),
    "id_format_pad_negative": zone(id_format={"pad_width": -1}),
    "id_format_pad_a_boolean": zone(id_format={"pad_width": True}),
    "id_format_pad_a_float": zone(id_format={"pad_width": 2.0}),
    "id_format_pad_a_string": zone(id_format={"pad_width": "2"}),
    "id_format_everything_wrong": zone(
        id_format={"type": "uuid", "min": "a", "max": 1.5, "pad_width": -2}
    ),
    # --- a child type's state variables --------------------------------
    "child_vars_a_list": zone(state_variables=["level"]),
    "child_vars_a_string": zone(state_variables="level"),
    "child_vars_none": zone(state_variables=None),
    "child_vars_an_empty_list": zone(state_variables=[]),
    "child_vars_zero": zone(state_variables=0),
    "child_vars_empty": zone(state_variables={}),
    "child_var_not_a_mapping": zone(state_variables={"a": 1, "b": None, "c": ["x"]}),
    "child_var_unknown_type": zone(state_variables={"a": {"type": "decimal"}}),
    "child_var_type_empty": zone(state_variables={"a": {"type": ""}}),
    "child_var_type_none": zone(state_variables={"a": {"type": None}}),
    "child_var_type_a_number": zone(state_variables={"a": {"type": 3}}),
    "child_var_no_label": zone(state_variables={"a": {"type": "number"}}),
    "child_var_cloud_priority": zone(
        state_variables={"a": {"cloud_priority": "urgent"}, "b": {"cloud_priority": "low"}}
    ),
    "child_var_cloud_priority_none": zone(state_variables={"a": {"cloud_priority": None}}),
    # an empty cloud_priority is a value, not an absent one, and is refused
    "child_var_cloud_priority_empty": zone(state_variables={"a": {"cloud_priority": ""}}),
    "child_var_unit": zone(state_variables={"a": {"unit": 5}, "b": {"unit": "dB"}}),
    "child_var_unit_falsy": zone(state_variables={"a": {"unit": 0}, "b": {"unit": []}, "c": {"unit": ""}}),
    "child_var_control": zone(state_variables={"a": {"control": "yes"}, "b": {"control": True}}),
    "child_var_control_a_number": zone(state_variables={"a": {"control": 1}}),
    "child_var_everything_wrong": zone(
        state_variables={
            "a": {"type": "decimal", "unit": 5, "control": 0, "cloud_priority": "x"}
        }
    ),
    "child_var_names_not_text": zone(state_variables={2: 5, True: {"type": "x"}}),
    # --- the instances: roster ------------------------------------------
    "instances_a_list": zone(instances=["count"]),
    "instances_a_string": zone(instances="count"),
    "instances_none": zone(instances=None),
    "instances_empty": zone(instances={}),
    "instances_two_sources": roster(count=2, ids_from="mic_ids"),
    "instances_every_source": roster(count=2, count_from="zones", ids_from="mic_ids", ids=[1]),
    "instances_only_a_label": roster(label="Zone"),
    "instances_label_a_number": roster(count=2, label=5),
    "instances_label_none": roster(count=2, label=None),
    "instances_label_falsy": roster(count=2, label=0),
    "instances_label_with_no_source": roster(label=5),
    # count_from_state
    "count_from_state_declared": roster(count=2, count_from_state="power"),
    "count_from_state_undeclared": roster(count=2, count_from_state="outputs"),
    "count_from_state_empty": roster(count=2, count_from_state=""),
    "count_from_state_a_number": roster(count=2, count_from_state=4),
    "count_from_state_none": roster(count=2, count_from_state=None),
    "count_from_state_alone": roster(count_from_state="power"),
    "count_from_state_and_the_source_both_wrong": roster(count=0, count_from_state="outputs"),
    "count_from_state_over_a_list_of_variables": {
        **roster(count=2, count_from_state="power"),
        "state_variables": ["power"],
    },
    "count_from_state_over_no_variables": {
        **roster(count=2, count_from_state="power"),
        "state_variables": None,
    },
    "count_from_state_over_a_string_of_variables": {
        **roster(count=2, count_from_state="pow"),
        "state_variables": "power",
    },
    # ids
    "ids_a_string": roster(ids="1,2"),
    "ids_empty": roster(ids=[]),
    "ids_none": roster(ids=None),
    "ids_integers": roster(ids=[1, 2, 3]),
    "ids_integer_text": roster(ids=["1", " 2 ", "+3"]),
    "ids_a_boolean_first": roster(ids=[True, "x", 1.5]),
    "ids_a_float": roster(ids=[1, 2.5, None]),
    "ids_a_mapping": roster(ids=[{"id": 1}]),
    "ids_text_on_integer_ids": roster(ids=[1, "two", "three"]),
    "ids_text_on_undeclared_ids": roster(id_format={"min": 1}, ids=["two"]),
    "ids_text_on_string_ids": roster(id_format=STRING_IDS, ids=["a", "b", 3]),
    "ids_on_an_unknown_id_kind": roster(id_format={"type": "uuid"}, ids=["a"]),
    "ids_a_float_text_on_integer_ids": roster(ids=["1.0"]),
    # count
    "count_a_boolean": roster(count=True),
    "count_zero": roster(count=0),
    "count_negative": roster(count=-3),
    "count_a_float": roster(count=2.0),
    "count_a_string": roster(count="4"),
    "count_none": roster(count=None),
    "count_within_max": roster({"max": 8}, count=8),
    "count_over_max": roster({"max": 8}, count=9),
    "count_over_a_boolean_max": roster({"max": True}, count=9),
    "count_over_a_float_max": roster({"max": 8.0}, count=9),
    "count_on_string_ids": roster(STRING_IDS, count=2),
    "count_bad_on_string_ids": roster(STRING_IDS, count=0),
    "count_over_max_on_string_ids": roster({"type": "string", "max": 1}, count=3),
    "count_with_id_format_a_list": roster(["x"], count=3),
    # count_from and ids_from
    "count_from_schema_field": roster(count_from="zones"),
    "count_from_default_field": roster(count_from="outputs"),
    "count_from_derived_field": roster(count_from="inputs"),
    "count_from_undeclared": roster(count_from="zone_count"),
    "count_from_empty": roster(count_from=""),
    "count_from_a_number": roster(count_from=4),
    "count_from_none": roster(count_from=None),
    "count_from_on_string_ids": roster(STRING_IDS, count_from="zones"),
    "count_from_undeclared_on_string_ids": roster(STRING_IDS, count_from="nope"),
    "ids_from_declared": roster(ids_from="mic_ids"),
    "ids_from_undeclared": roster(ids_from="mic_list"),
    "ids_from_a_list": roster(ids_from=["a"]),
    "ids_from_on_string_ids": roster(STRING_IDS, ids_from="mic_ids"),
    "ids_from_with_no_config": {
        **roster(ids_from="mic_ids"),
        "config_schema": None,
        "default_config": [],
        "config_derived": "x",
    },
    # --- several types, in order ----------------------------------------
    "two_types_in_order": types(
        zone={"id_format": {"type": "uuid"}, "instances": {"count": 0}},
        **{"a.b": {"state_variables": {"x": {"unit": 1}}}},
        mic={"instances": {"ids": []}, "state_variables": 5},
    ),
    "a_bad_name_skips_the_rest": types(
        **{"": {"id_format": 1, "instances": 2}}, ok={"instances": 2}
    ),
    "a_bad_declaration_skips_the_rest": types(zone=1, mic={"instances": 2}),
    "one_type_wrong_everywhere": zone(
        id_format={"type": "uuid", "pad_width": -1},
        state_variables={"a": {"type": "decimal"}, "b": 1},
        instances={"count": 0, "label": 5},
    ),
    # --- the driver's own state variables -------------------------------
    "vars_a_list": {"state_variables": ["power"]},
    "vars_a_string": {"state_variables": "power"},
    "vars_none": {"state_variables": None},
    "vars_absent": {"state_variables": "<absent>"},
    "vars_empty": {"state_variables": {}},
    "var_not_a_mapping": {"state_variables": {"a": 1, "b": None, "c": ["x"]}},
    "var_unknown_type": {"state_variables": {"a": {"type": "decimal", "label": "A"}}},
    "var_type_empty": {"state_variables": {"a": {"type": "", "label": "A"}}},
    "var_type_a_number": {"state_variables": {"a": {"type": 3, "label": "A"}}},
    "var_no_label": {"state_variables": {"a": {"type": "number"}}},
    "var_label_empty": {"state_variables": {"a": {"label": ""}}},
    "var_label_a_number": {"state_variables": {"a": {"label": 5}}},
    "var_label_zero": {"state_variables": {"a": {"label": 0}}},
    "var_unit": {"state_variables": {"a": {"label": "A", "unit": 5}, "b": {"label": "B", "unit": "dB"}}},
    "var_control": {
        "state_variables": {"a": {"label": "A", "control": "yes"}, "b": {"label": "B", "control": False}}
    },
    "var_cloud_priority": {
        "state_variables": {
            "a": {"label": "A", "cloud_priority": "urgent"},
            "b": {"label": "B", "cloud_priority": "high"},
            "c": {"label": "C", "cloud_priority": None},
        }
    },
    "var_everything_wrong": {
        "state_variables": {
            "a": {"type": "decimal", "unit": 5, "control": 0, "cloud_priority": "x"}
        }
    },
    "var_names_not_text": {"state_variables": {2: {"label": "Two"}, True: 5}},
    "var_cloud_priority_empty": {"state_variables": {"a": {"label": "A", "cloud_priority": ""}}},
    "var_unit_falsy": {"state_variables": {"a": {"label": "A", "unit": False}, "b": {"label": "B", "unit": 0}}},
    "var_unit_a_date": {"state_variables": {"a": {"label": "A", "unit": datetime.date(2026, 1, 2)}}},
    "vars_and_child_vars_side_by_side": {
        "state_variables": {"a": {"type": "decimal", "unit": 5, "control": 0, "cloud_priority": "x"}},
        **zone(state_variables={"a": {"type": "decimal", "unit": 5, "control": 0, "cloud_priority": "x"}}),
    },
    # --- a command parameter's reference into child_entity_types --------
    "child_id_param_no_child_type": param_cmds(ZONE_TYPE, z={"type": "child_id"}),
    "child_id_param_child_type_none": param_cmds(ZONE_TYPE, z={"type": "child_id", "child_type": None}),
    "child_id_param_child_type_a_number": param_cmds(ZONE_TYPE, z={"type": "child_id", "child_type": 3}),
    "child_id_param_child_type_empty": param_cmds(ZONE_TYPE, z={"type": "child_id", "child_type": ""}),
    "child_id_param_undeclared": param_cmds(ZONE_TYPE, z={"type": "child_id", "child_type": "zon"}),
    "child_id_param_none_declared": param_cmds(None, z={"type": "child_id", "child_type": "zone"}),
    "child_id_param_types_a_list": param_cmds(["zone"], z=CHILD_ID),
    "child_id_param_types_an_empty_list": param_cmds([], z=CHILD_ID),
    "child_id_param_several_declared": param_cmds(
        {"zone": {}, "mic": {}, "bus": {}}, z={"type": "child_id", "child_type": "out"}
    ),
    "child_id_param_in_bounds": param_cmds(ZONE_TYPE, z={**CHILD_ID, "min": 1, "max": 16}),
    "child_id_param_below_min": param_cmds(ZONE_TYPE, z={**CHILD_ID, "min": 0}),
    "child_id_param_above_max": param_cmds(ZONE_TYPE, z={**CHILD_ID, "max": 17}),
    "child_id_param_both_outside": param_cmds(ZONE_TYPE, z={**CHILD_ID, "min": -1, "max": 99}),
    "child_id_param_float_bounds": param_cmds(ZONE_TYPE, z={**CHILD_ID, "min": 0.5, "max": 16.5}),
    "child_id_param_whole_float_bounds": param_cmds(
        {"zone": {"id_format": {"min": 2, "max": 16}}}, z={**CHILD_ID, "min": 1.0, "max": 20.0}
    ),
    "child_id_param_boolean_bounds": param_cmds(ZONE_TYPE, z={**CHILD_ID, "min": False, "max": True}),
    "child_id_param_text_bounds": param_cmds(ZONE_TYPE, z={**CHILD_ID, "min": "0", "max": "99"}),
    "child_id_param_float_format": param_cmds(
        {"zone": {"id_format": {"min": 1.5, "max": 8.5}}}, z={**CHILD_ID, "min": 1, "max": 9}
    ),
    "child_id_param_boolean_format": param_cmds(
        {"zone": {"id_format": {"min": True, "max": False}}}, z={**CHILD_ID, "min": 0, "max": 9}
    ),
    "child_id_param_no_id_format": param_cmds({"zone": {}}, z={**CHILD_ID, "min": -9, "max": 999}),
    "child_id_param_id_format_a_list": param_cmds(
        {"zone": {"id_format": [1]}}, z={**CHILD_ID, "min": -9}
    ),
    "child_id_param_type_def_a_list": param_cmds({"zone": [1]}, z={**CHILD_ID, "min": -9}),
    "child_id_param_only_min_declared": param_cmds(
        {"zone": {"id_format": {"min": 1}}}, z={**CHILD_ID, "min": 0, "max": 999}
    ),
    "child_id_params_in_order": {
        "child_entity_types": ZONE_TYPE,
        "commands": {
            "a": {"send": "A\r", "params": {"z": {"type": "child_id"}, "y": {**CHILD_ID, "max": 99}}},
            "b": "x",
            "c": {"send": "C\r", "params": ["z"]},
            "d": {"send": "D\r", "params": {"q": 5, "r": {"type": "integer", "child_type": "nope"}}},
            "e": {"send": "E\r", "params": {"z": {"type": "child_id", "child_type": "nope"}}},
        },
    },
    "child_id_commands_a_list": {"child_entity_types": ZONE_TYPE, "commands": [{"z": CHILD_ID}]},
}

_ABSENT = "<absent>"


def _definition(case: str) -> dict[str, Any]:
    definition = {**BASE, **CASES[case]}
    for key, value in list(definition.items()):
        if value == _ABSENT:
            del definition[key]
    return definition


# Definitions the walk raises on today, by the exception's type. Each reads a
# value the shape check above it has already named, through a membership
# test that cannot take it.
RAISES: dict[str, dict[str, Any]] = {
    "child_var_type_a_list": zone(state_variables={"a": {"type": ["number"]}}),
    "var_type_a_list": {"state_variables": {"a": {"type": ["number"], "label": "A"}}},
    "count_from_state_over_a_number_of_variables": {
        **roster(count=2, count_from_state="power"),
        "state_variables": 5,
    },
}


def _ref_info(commands: Any, child_types: Any = _ABSENT) -> dict[str, Any]:
    info: dict[str, Any] = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}
    if commands != _ABSENT:
        info["commands"] = commands
    if child_types != _ABSENT:
        info["child_entity_types"] = child_types
    return info


def _route(**params: Any) -> dict[str, Any]:
    return {"route": {"send": "R\r", "params": params}}


# child_param_reference_errors over a DRIVER_INFO a source reader may have
# read only in part: a computed block is the reader's UNEVALUATED object, a
# mapping with computed keys holds UNEVALUATED_KEY.
REFERENCE_CASES: dict[str, dict[str, Any]] = {
    "no_commands": _ref_info(_ABSENT, ZONE_TYPE),
    "commands_none": _ref_info(None, ZONE_TYPE),
    "commands_computed": _ref_info(UNEVALUATED, ZONE_TYPE),
    "commands_a_list": _ref_info(["route"], ZONE_TYPE),
    "commands_an_empty_list": _ref_info([], ZONE_TYPE),
    "commands_empty": _ref_info({}, ZONE_TYPE),
    "types_computed": _ref_info(_route(z=CHILD_ID), UNEVALUATED),
    "types_a_list": _ref_info(_route(z=CHILD_ID), ["zone"]),
    "types_none": _ref_info(_route(z=CHILD_ID), None),
    "types_an_empty_list": _ref_info(_route(z=CHILD_ID), []),
    "types_absent": _ref_info(_route(z=CHILD_ID)),
    "commands_partly_computed": _ref_info(
        {**_route(z={"type": "child_id"}), UNEVALUATED_KEY: UNEVALUATED}, ZONE_TYPE
    ),
    "commands_only_computed": _ref_info({UNEVALUATED_KEY: UNEVALUATED}, ZONE_TYPE),
    "a_command_named_like_the_marker": _ref_info(
        {UNEVALUATED_KEY: {"params": {"z": {"type": "child_id"}}}}, ZONE_TYPE
    ),
    "types_partly_computed_undeclared": _ref_info(
        _route(z={"type": "child_id", "child_type": "out"}),
        {**ZONE_TYPE, UNEVALUATED_KEY: UNEVALUATED},
    ),
    "types_partly_computed_declared": _ref_info(
        _route(z={**CHILD_ID, "min": 0}), {**ZONE_TYPE, UNEVALUATED_KEY: UNEVALUATED}
    ),
    "types_partly_computed_no_child_type": _ref_info(
        _route(z={"type": "child_id"}), {UNEVALUATED_KEY: UNEVALUATED}
    ),
    "child_type_computed": _ref_info(
        _route(z={"type": "child_id", "child_type": UNEVALUATED}), ZONE_TYPE
    ),
    "param_computed": _ref_info(_route(z=UNEVALUATED), ZONE_TYPE),
    "params_computed": _ref_info({"route": {"send": "R", "params": UNEVALUATED}}, ZONE_TYPE),
    "command_computed": _ref_info({"route": UNEVALUATED}, ZONE_TYPE),
    "type_def_computed": _ref_info(_route(z={**CHILD_ID, "min": 0}), {"zone": UNEVALUATED}),
    "id_format_computed": _ref_info(
        _route(z={**CHILD_ID, "min": 0}), {"zone": {"id_format": UNEVALUATED}}
    ),
    "bound_computed": _ref_info(
        _route(z={**CHILD_ID, "min": UNEVALUATED, "max": 99}), ZONE_TYPE
    ),
    "format_bound_computed": _ref_info(
        _route(z={**CHILD_ID, "min": 0, "max": 99}),
        {"zone": {"id_format": {"min": UNEVALUATED, "max": 16}}},
    ),
    "everything_at_once": _ref_info(
        {
            "a": {"params": {"z": {"type": "child_id"}, "y": {**CHILD_ID, "min": 0}}},
            UNEVALUATED_KEY: UNEVALUATED,
            "b": {"params": {"c": {"type": "child_id", "child_type": UNEVALUATED}}},
            "c": {"params": {"d": {"type": "child_id", "child_type": "out"}}},
        },
        {**ZONE_TYPE, UNEVALUATED_KEY: UNEVALUATED},
    ),
}

REASON_CASES: dict[str, tuple[str, dict[str, Any]]] = {
    "none_declared": ("zone", {}),
    "some_declared": ("zon", {"zone": {}, "bus": {}, "mic": {}}),
    "only_computed": ("zone", {UNEVALUATED_KEY: UNEVALUATED}),
    "computed_and_declared": ("out", {"zone": {}, UNEVALUATED_KEY: UNEVALUATED}),
}



EXPECTED: dict[str, list[tuple[str, str]]] = {'a_bad_declaration_skips_the_rest': [('child_entity_types.zone',
                                       'child_entity_types.zone: must be a mapping'),
                                      ('child_entity_types.mic',
                                       'child_entity_types.mic.instances: must be a mapping')],
 'a_bad_name_skips_the_rest': [('child_entity_types.',
                                "child_entity_types: type name '' must be a non-empty string"),
                               ('child_entity_types.ok',
                                'child_entity_types.ok.instances: must be a mapping')],
 'child_id_commands_a_list': [('commands', 'commands: must be a mapping')],
 'child_id_param_above_max': [('commands',
                               'commands.route.params.z: max 17 is above '
                               'child_entity_types.zone.id_format.max (16), so the gate would accept an id '
                               'the child type cannot have')],
 'child_id_param_below_min': [('commands',
                               'commands.route.params.z: min 0 is below '
                               'child_entity_types.zone.id_format.min (1), so the gate would accept an id '
                               'the child type cannot have')],
 'child_id_param_boolean_bounds': [],
 'child_id_param_boolean_format': [('child_entity_types.zone',
                                    'child_entity_types.zone.id_format: min must be an integer (got True)'),
                                   ('child_entity_types.zone',
                                    'child_entity_types.zone.id_format: max must be an integer (got False)'),
                                   ('child_entity_types.zone',
                                    'child_entity_types.zone.id_format: min (True) is greater than max '
                                    '(False)')],
 'child_id_param_both_outside': [('commands',
                                  'commands.route.params.z: min -1 is below '
                                  'child_entity_types.zone.id_format.min (1), so the gate would accept an id '
                                  'the child type cannot have'),
                                 ('commands',
                                  'commands.route.params.z: max 99 is above '
                                  'child_entity_types.zone.id_format.max (16), so the gate would accept an '
                                  'id the child type cannot have')],
 'child_id_param_child_type_a_number': [],
 'child_id_param_child_type_empty': [('commands',
                                      "commands.route.params.z: child_type '' is not a declared "
                                      'child_entity_type (declared: zone)')],
 'child_id_param_child_type_none': [('commands',
                                     "commands.route.params.z: a child_id param must declare 'child_type' "
                                     '(without it the id cannot be resolved to a child type and is coerced '
                                     'as a plain integer)')],
 'child_id_param_float_bounds': [('commands',
                                  'commands.route.params.z: min 0.5 is below '
                                  'child_entity_types.zone.id_format.min (1), so the gate would accept an id '
                                  'the child type cannot have'),
                                 ('commands',
                                  'commands.route.params.z: max 16.5 is above '
                                  'child_entity_types.zone.id_format.max (16), so the gate would accept an '
                                  'id the child type cannot have')],
 'child_id_param_float_format': [('commands',
                                  'commands.route.params.z: min 1 is below '
                                  'child_entity_types.zone.id_format.min (1.5), so the gate would accept an '
                                  'id the child type cannot have'),
                                 ('commands',
                                  'commands.route.params.z: max 9 is above '
                                  'child_entity_types.zone.id_format.max (8.5), so the gate would accept an '
                                  'id the child type cannot have'),
                                 ('child_entity_types.zone',
                                  'child_entity_types.zone.id_format: min must be an integer (got 1.5)'),
                                 ('child_entity_types.zone',
                                  'child_entity_types.zone.id_format: max must be an integer (got 8.5)')],
 'child_id_param_id_format_a_list': [('child_entity_types.zone',
                                      'child_entity_types.zone.id_format: must be a mapping')],
 'child_id_param_in_bounds': [],
 'child_id_param_no_child_type': [('commands',
                                   "commands.route.params.z: a child_id param must declare 'child_type' "
                                   '(without it the id cannot be resolved to a child type and is coerced as '
                                   'a plain integer)')],
 'child_id_param_no_id_format': [],
 'child_id_param_none_declared': [('commands',
                                   "commands.route.params.z: child_type 'zone' is not a declared "
                                   'child_entity_type (the driver declares none)')],
 'child_id_param_only_min_declared': [('commands',
                                       'commands.route.params.z: min 0 is below '
                                       'child_entity_types.zone.id_format.min (1), so the gate would accept '
                                       'an id the child type cannot have')],
 'child_id_param_several_declared': [('commands',
                                      "commands.route.params.z: child_type 'out' is not a declared "
                                      'child_entity_type (declared: bus, mic, zone)')],
 'child_id_param_text_bounds': [('commands.route', "Command 'route' param 'z': min must be a number"),
                                ('commands.route', "Command 'route' param 'z': max must be a number")],
 'child_id_param_type_def_a_list': [('child_entity_types.zone',
                                     'child_entity_types.zone: must be a mapping')],
 'child_id_param_types_a_list': [('child_entity_types', 'child_entity_types: must be a mapping')],
 'child_id_param_types_an_empty_list': [],
 'child_id_param_undeclared': [('commands',
                                "commands.route.params.z: child_type 'zon' is not a declared "
                                'child_entity_type (declared: zone)')],
 'child_id_param_whole_float_bounds': [('commands',
                                        'commands.route.params.z: min 1 is below '
                                        'child_entity_types.zone.id_format.min (2), so the gate would accept '
                                        'an id the child type cannot have'),
                                       ('commands',
                                        'commands.route.params.z: max 20 is above '
                                        'child_entity_types.zone.id_format.max (16), so the gate would '
                                        'accept an id the child type cannot have')],
 'child_id_params_in_order': [('commands.b', "Command 'b': must be a dict"),
                              ('commands',
                               "commands.a.params.z: a child_id param must declare 'child_type' (without it "
                               'the id cannot be resolved to a child type and is coerced as a plain '
                               'integer)'),
                              ('commands',
                               'commands.a.params.y: max 99 is above child_entity_types.zone.id_format.max '
                               '(16), so the gate would accept an id the child type cannot have'),
                              ('commands',
                               "commands.e.params.z: child_type 'nope' is not a declared child_entity_type "
                               '(declared: zone)')],
 'child_var_cloud_priority': [('child_entity_types.zone',
                               "child_entity_types.zone.state_variables.a: cloud_priority must be 'low' or "
                               "'high' (got 'urgent'); omit it for the default cadence")],
 'child_var_cloud_priority_empty': [('child_entity_types.zone',
                                     'child_entity_types.zone.state_variables.a: cloud_priority must be '
                                     "'low' or 'high' (got ''); omit it for the default cadence")],
 'child_var_cloud_priority_none': [],
 'child_var_control': [('child_entity_types.zone',
                        'child_entity_types.zone.state_variables.a: control must be true or false (got '
                        "'yes')")],
 'child_var_control_a_number': [('child_entity_types.zone',
                                 'child_entity_types.zone.state_variables.a: control must be true or false '
                                 '(got 1)')],
 'child_var_everything_wrong': [('child_entity_types.zone',
                                 "child_entity_types.zone.state_variables.a: unknown type 'decimal'"),
                                ('child_entity_types.zone',
                                 "child_entity_types.zone.state_variables.a: cloud_priority must be 'low' or "
                                 "'high' (got 'x'); omit it for the default cadence"),
                                ('child_entity_types.zone',
                                 'child_entity_types.zone.state_variables.a: unit must be a string (got 5)'),
                                ('child_entity_types.zone',
                                 'child_entity_types.zone.state_variables.a: control must be true or false '
                                 '(got 0)')],
 'child_var_names_not_text': [('child_entity_types.zone',
                               'child_entity_types.zone.state_variables.2: must be a mapping'),
                              ('child_entity_types.zone',
                               "child_entity_types.zone.state_variables.True: unknown type 'x'")],
 'child_var_no_label': [],
 'child_var_not_a_mapping': [('child_entity_types.zone',
                              'child_entity_types.zone.state_variables.a: must be a mapping'),
                             ('child_entity_types.zone',
                              'child_entity_types.zone.state_variables.b: must be a mapping'),
                             ('child_entity_types.zone',
                              'child_entity_types.zone.state_variables.c: must be a mapping')],
 'child_var_type_a_number': [('child_entity_types.zone',
                              "child_entity_types.zone.state_variables.a: unknown type '3'")],
 'child_var_type_empty': [],
 'child_var_type_none': [],
 'child_var_unit': [('child_entity_types.zone',
                     'child_entity_types.zone.state_variables.a: unit must be a string (got 5)')],
 'child_var_unit_falsy': [('child_entity_types.zone',
                           'child_entity_types.zone.state_variables.a: unit must be a string (got 0)'),
                          ('child_entity_types.zone',
                           'child_entity_types.zone.state_variables.b: unit must be a string (got [])')],
 'child_var_unknown_type': [('child_entity_types.zone',
                             "child_entity_types.zone.state_variables.a: unknown type 'decimal'")],
 'child_vars_a_list': [('child_entity_types.zone',
                        'child_entity_types.zone.state_variables: must be a mapping')],
 'child_vars_a_string': [('child_entity_types.zone',
                          'child_entity_types.zone.state_variables: must be a mapping')],
 'child_vars_an_empty_list': [('child_entity_types.zone',
                               'child_entity_types.zone.state_variables: must be a mapping')],
 'child_vars_empty': [],
 'child_vars_none': [],
 'child_vars_zero': [('child_entity_types.zone',
                      'child_entity_types.zone.state_variables: must be a mapping')],
 'count_a_boolean': [('child_entity_types.zone',
                      'child_entity_types.zone.instances: count must be an integer >= 1 (got True)')],
 'count_a_float': [('child_entity_types.zone',
                    'child_entity_types.zone.instances: count must be an integer >= 1 (got 2.0)')],
 'count_a_string': [('child_entity_types.zone',
                     "child_entity_types.zone.instances: count must be an integer >= 1 (got '4')")],
 'count_bad_on_string_ids': [('child_entity_types.zone',
                              'child_entity_types.zone.instances: count must be an integer >= 1 (got 0)'),
                             ('child_entity_types.zone',
                              "child_entity_types.zone.instances: 'count' requires integer ids "
                              "(id_format.type is 'string'; use 'ids_from')")],
 'count_from_a_number': [('child_entity_types.zone',
                          'child_entity_types.zone.instances: count_from must name a config field')],
 'count_from_default_field': [],
 'count_from_derived_field': [],
 'count_from_empty': [('child_entity_types.zone',
                       'child_entity_types.zone.instances: count_from must name a config field')],
 'count_from_none': [('child_entity_types.zone',
                      'child_entity_types.zone.instances: count_from must name a config field')],
 'count_from_on_string_ids': [('child_entity_types.zone',
                               "child_entity_types.zone.instances: 'count_from' requires integer ids "
                               "(id_format.type is 'string'; use 'ids_from')")],
 'count_from_schema_field': [],
 'count_from_state_a_number': [('child_entity_types.zone',
                                'child_entity_types.zone.instances: count_from_state must name a state '
                                'variable')],
 'count_from_state_alone': [('child_entity_types.zone',
                             "child_entity_types.zone.instances: declare exactly one of 'count', "
                             "'count_from', 'ids_from', 'ids'")],
 'count_from_state_and_the_source_both_wrong': [('child_entity_types.zone',
                                                 'child_entity_types.zone.instances: count_from_state '
                                                 "'outputs' is not a declared state variable"),
                                                ('child_entity_types.zone',
                                                 'child_entity_types.zone.instances: count must be an '
                                                 'integer >= 1 (got 0)')],
 'count_from_state_declared': [],
 'count_from_state_empty': [('child_entity_types.zone',
                             'child_entity_types.zone.instances: count_from_state must name a state '
                             'variable')],
 'count_from_state_none': [],
 'count_from_state_over_a_list_of_variables': [('state_variables', 'state_variables: must be a mapping')],
 'count_from_state_over_a_string_of_variables': [('state_variables', 'state_variables: must be a mapping')],
 'count_from_state_over_no_variables': [('state_variables', 'state_variables: must be a mapping'),
                                        ('child_entity_types.zone',
                                         "child_entity_types.zone.instances: count_from_state 'power' is not "
                                         'a declared state variable')],
 'count_from_state_undeclared': [('child_entity_types.zone',
                                  "child_entity_types.zone.instances: count_from_state 'outputs' is not a "
                                  'declared state variable')],
 'count_from_undeclared': [('child_entity_types.zone',
                            "child_entity_types.zone.instances: count_from 'zone_count' is not a declared "
                            'config field (config_schema / default_config / config_derived)')],
 'count_from_undeclared_on_string_ids': [('child_entity_types.zone',
                                          "child_entity_types.zone.instances: count_from 'nope' is not a "
                                          'declared config field (config_schema / default_config / '
                                          'config_derived)'),
                                         ('child_entity_types.zone',
                                          "child_entity_types.zone.instances: 'count_from' requires integer "
                                          "ids (id_format.type is 'string'; use 'ids_from')")],
 'count_negative': [('child_entity_types.zone',
                     'child_entity_types.zone.instances: count must be an integer >= 1 (got -3)')],
 'count_none': [('child_entity_types.zone',
                 'child_entity_types.zone.instances: count must be an integer >= 1 (got None)')],
 'count_on_string_ids': [('child_entity_types.zone',
                          "child_entity_types.zone.instances: 'count' requires integer ids (id_format.type "
                          "is 'string'; use 'ids_from')")],
 'count_over_a_boolean_max': [('child_entity_types.zone',
                               'child_entity_types.zone.id_format: max must be an integer (got True)')],
 'count_over_a_float_max': [('child_entity_types.zone',
                             'child_entity_types.zone.id_format: max must be an integer (got 8.0)')],
 'count_over_max': [('child_entity_types.zone',
                     'child_entity_types.zone.instances: count (9) exceeds id_format.max (8)')],
 'count_over_max_on_string_ids': [('child_entity_types.zone',
                                   'child_entity_types.zone.instances: count (3) exceeds id_format.max (1)'),
                                  ('child_entity_types.zone',
                                   "child_entity_types.zone.instances: 'count' requires integer ids "
                                   "(id_format.type is 'string'; use 'ids_from')")],
 'count_with_id_format_a_list': [('child_entity_types.zone',
                                  'child_entity_types.zone.id_format: must be a mapping')],
 'count_within_max': [],
 'count_zero': [('child_entity_types.zone',
                 'child_entity_types.zone.instances: count must be an integer >= 1 (got 0)')],
 'id_format_a_list': [('child_entity_types.zone', 'child_entity_types.zone.id_format: must be a mapping')],
 'id_format_a_string': [('child_entity_types.zone', 'child_entity_types.zone.id_format: must be a mapping')],
 'id_format_an_empty_list': [('child_entity_types.zone',
                              'child_entity_types.zone.id_format: must be a mapping')],
 'id_format_both_floats': [('child_entity_types.zone',
                            'child_entity_types.zone.id_format: min must be an integer (got 9.0)'),
                           ('child_entity_types.zone',
                            'child_entity_types.zone.id_format: max must be an integer (got 2.0)')],
 'id_format_empty': [],
 'id_format_everything_wrong': [('child_entity_types.zone',
                                 "child_entity_types.zone.id_format: unknown type 'uuid' (expected 'integer' "
                                 "or 'string')"),
                                ('child_entity_types.zone',
                                 "child_entity_types.zone.id_format: min must be an integer (got 'a')"),
                                ('child_entity_types.zone',
                                 'child_entity_types.zone.id_format: max must be an integer (got 1.5)'),
                                ('child_entity_types.zone',
                                 'child_entity_types.zone.id_format: pad_width must be zero or a positive '
                                 'integer (got -2)')],
 'id_format_max_a_boolean': [('child_entity_types.zone',
                              'child_entity_types.zone.id_format: max must be an integer (got False)'),
                             ('child_entity_types.zone',
                              'child_entity_types.zone.id_format: min (2) is greater than max (False)')],
 'id_format_max_a_string': [('child_entity_types.zone',
                             "child_entity_types.zone.id_format: max must be an integer (got '16')")],
 'id_format_min_a_boolean': [('child_entity_types.zone',
                              'child_entity_types.zone.id_format: min must be an integer (got True)'),
                             ('child_entity_types.zone',
                              'child_entity_types.zone.id_format: min (True) is greater than max (0)')],
 'id_format_min_a_float': [('child_entity_types.zone',
                            'child_entity_types.zone.id_format: min must be an integer (got 1.5)')],
 'id_format_min_above_max': [('child_entity_types.zone',
                              'child_entity_types.zone.id_format: min (9) is greater than max (2)')],
 'id_format_min_equals_max': [],
 'id_format_negative_bounds': [('child_entity_types.zone',
                                'child_entity_types.zone.id_format: min (-5) is greater than max (-9)')],
 'id_format_none': [],
 'id_format_pad_a_boolean': [('child_entity_types.zone',
                              'child_entity_types.zone.id_format: pad_width must be zero or a positive '
                              'integer (got True)')],
 'id_format_pad_a_float': [('child_entity_types.zone',
                            'child_entity_types.zone.id_format: pad_width must be zero or a positive integer '
                            '(got 2.0)')],
 'id_format_pad_a_string': [('child_entity_types.zone',
                             'child_entity_types.zone.id_format: pad_width must be zero or a positive '
                             "integer (got '2')")],
 'id_format_pad_negative': [('child_entity_types.zone',
                             'child_entity_types.zone.id_format: pad_width must be zero or a positive '
                             'integer (got -1)')],
 'id_format_pad_two': [],
 'id_format_pad_zero': [],
 'id_format_string': [],
 'id_format_type_a_list': [('child_entity_types.zone',
                            "child_entity_types.zone.id_format: unknown type '['integer']' (expected "
                            "'integer' or 'string')")],
 'id_format_type_a_number': [('child_entity_types.zone',
                              "child_entity_types.zone.id_format: unknown type '3' (expected 'integer' or "
                              "'string')")],
 'id_format_type_none': [('child_entity_types.zone',
                          "child_entity_types.zone.id_format: unknown type 'None' (expected 'integer' or "
                          "'string')")],
 'id_format_unknown_type': [('child_entity_types.zone',
                             "child_entity_types.zone.id_format: unknown type 'uuid' (expected 'integer' or "
                             "'string')")],
 'id_format_zero': [('child_entity_types.zone', 'child_entity_types.zone.id_format: must be a mapping')],
 'ids_a_boolean_first': [('child_entity_types.zone',
                          'child_entity_types.zone.instances: ids entries must be scalars (got True)')],
 'ids_a_float': [('child_entity_types.zone',
                  'child_entity_types.zone.instances: ids entries must be scalars (got 2.5)')],
 'ids_a_float_text_on_integer_ids': [('child_entity_types.zone',
                                      "child_entity_types.zone.instances: id '1.0' is not an integer (zone "
                                      'declares integer ids)')],
 'ids_a_mapping': [('child_entity_types.zone',
                    "child_entity_types.zone.instances: ids entries must be scalars (got {'id': 1})")],
 'ids_a_string': [('child_entity_types.zone',
                   'child_entity_types.zone.instances: ids must be a non-empty list of literal child ids')],
 'ids_empty': [('child_entity_types.zone',
                'child_entity_types.zone.instances: ids must be a non-empty list of literal child ids')],
 'ids_from_a_list': [('child_entity_types.zone',
                      'child_entity_types.zone.instances: ids_from must name a config field')],
 'ids_from_declared': [],
 'ids_from_on_string_ids': [],
 'ids_from_undeclared': [('child_entity_types.zone',
                          "child_entity_types.zone.instances: ids_from 'mic_list' is not a declared config "
                          'field (config_schema / default_config / config_derived)')],
 'ids_from_with_no_config': [('child_entity_types.zone',
                              "child_entity_types.zone.instances: ids_from 'mic_ids' is not a declared "
                              'config field (config_schema / default_config / config_derived)')],
 'ids_integer_text': [],
 'ids_integers': [],
 'ids_none': [('child_entity_types.zone',
               'child_entity_types.zone.instances: ids must be a non-empty list of literal child ids')],
 'ids_on_an_unknown_id_kind': [('child_entity_types.zone',
                                "child_entity_types.zone.id_format: unknown type 'uuid' (expected 'integer' "
                                "or 'string')")],
 'ids_text_on_integer_ids': [('child_entity_types.zone',
                              "child_entity_types.zone.instances: id 'two' is not an integer (zone declares "
                              'integer ids)')],
 'ids_text_on_string_ids': [],
 'ids_text_on_undeclared_ids': [('child_entity_types.zone',
                                 "child_entity_types.zone.instances: id 'two' is not an integer (zone "
                                 'declares integer ids)')],
 'instances_a_list': [('child_entity_types.zone', 'child_entity_types.zone.instances: must be a mapping')],
 'instances_a_string': [('child_entity_types.zone', 'child_entity_types.zone.instances: must be a mapping')],
 'instances_empty': [('child_entity_types.zone',
                      "child_entity_types.zone.instances: declare exactly one of 'count', 'count_from', "
                      "'ids_from', 'ids'")],
 'instances_every_source': [('child_entity_types.zone',
                             "child_entity_types.zone.instances: declare exactly one of 'count', "
                             "'count_from', 'ids_from', 'ids'")],
 'instances_label_a_number': [('child_entity_types.zone',
                               'child_entity_types.zone.instances: label must be a string')],
 'instances_label_falsy': [('child_entity_types.zone',
                            'child_entity_types.zone.instances: label must be a string')],
 'instances_label_none': [],
 'instances_label_with_no_source': [('child_entity_types.zone',
                                     "child_entity_types.zone.instances: declare exactly one of 'count', "
                                     "'count_from', 'ids_from', 'ids'"),
                                    ('child_entity_types.zone',
                                     'child_entity_types.zone.instances: label must be a string')],
 'instances_none': [],
 'instances_only_a_label': [('child_entity_types.zone',
                             "child_entity_types.zone.instances: declare exactly one of 'count', "
                             "'count_from', 'ids_from', 'ids'")],
 'instances_two_sources': [('child_entity_types.zone',
                            "child_entity_types.zone.instances: declare exactly one of 'count', "
                            "'count_from', 'ids_from', 'ids'")],
 'name_a_boolean': [('child_entity_types.True',
                     'child_entity_types: type name True must be a non-empty string')],
 'name_a_number': [('child_entity_types.5', 'child_entity_types: type name 5 must be a non-empty string')],
 'name_empty': [('child_entity_types.', "child_entity_types: type name '' must be a non-empty string")],
 'name_with_a_dot': [('child_entity_types.a.b',
                      "child_entity_types: type name 'a.b' must not contain dots (used as state key "
                      'separator)')],
 'name_with_a_dot_and_a_bad_declaration': [('child_entity_types.a.b*',
                                            "child_entity_types: type name 'a.b*' must not contain dots "
                                            '(used as state key separator)'),
                                           ('child_entity_types.a.b*',
                                            "child_entity_types: type name 'a.b*' must not contain glob "
                                            'metacharacters (*); they break state-change dispatch'),
                                           ('child_entity_types.a.b*',
                                            'child_entity_types.a.b*: must be a mapping')],
 'name_with_a_dot_and_a_glob': [('child_entity_types.a.b?',
                                 "child_entity_types: type name 'a.b?' must not contain dots (used as state "
                                 'key separator)'),
                                ('child_entity_types.a.b?',
                                 "child_entity_types: type name 'a.b?' must not contain glob metacharacters "
                                 '(?); they break state-change dispatch'),
                                ('child_entity_types.a.b?',
                                 'child_entity_types.a.b?.id_format: must be a mapping')],
 'name_with_a_space': [],
 'name_with_every_glob': [('child_entity_types.[a?*',
                           "child_entity_types: type name '[a?*' must not contain glob metacharacters (*, ?, "
                           '[); they break state-change dispatch')],
 'name_with_one_glob': [('child_entity_types.a*',
                         "child_entity_types: type name 'a*' must not contain glob metacharacters (*); they "
                         'break state-change dispatch')],
 'one_type_wrong_everywhere': [('child_entity_types.zone',
                                "child_entity_types.zone.id_format: unknown type 'uuid' (expected 'integer' "
                                "or 'string')"),
                               ('child_entity_types.zone',
                                'child_entity_types.zone.id_format: pad_width must be zero or a positive '
                                'integer (got -1)'),
                               ('child_entity_types.zone',
                                "child_entity_types.zone.state_variables.a: unknown type 'decimal'"),
                               ('child_entity_types.zone',
                                'child_entity_types.zone.state_variables.b: must be a mapping'),
                               ('child_entity_types.zone',
                                'child_entity_types.zone.instances: count must be an integer >= 1 (got 0)'),
                               ('child_entity_types.zone',
                                'child_entity_types.zone.instances: label must be a string')],
 'two_types_in_order': [('child_entity_types.zone',
                         "child_entity_types.zone.id_format: unknown type 'uuid' (expected 'integer' or "
                         "'string')"),
                        ('child_entity_types.zone',
                         'child_entity_types.zone.instances: count must be an integer >= 1 (got 0)'),
                        ('child_entity_types.a.b',
                         "child_entity_types: type name 'a.b' must not contain dots (used as state key "
                         'separator)'),
                        ('child_entity_types.a.b',
                         'child_entity_types.a.b.state_variables.x: unit must be a string (got 1)'),
                        ('child_entity_types.mic',
                         'child_entity_types.mic.state_variables: must be a mapping'),
                        ('child_entity_types.mic',
                         'child_entity_types.mic.instances: ids must be a non-empty list of literal child '
                         'ids')],
 'type_def_a_list': [('child_entity_types.zone', 'child_entity_types.zone: must be a mapping')],
 'type_def_a_string': [('child_entity_types.zone', 'child_entity_types.zone: must be a mapping')],
 'type_def_empty': [],
 'type_def_none': [('child_entity_types.zone', 'child_entity_types.zone: must be a mapping')],
 'types_a_list': [('child_entity_types', 'child_entity_types: must be a mapping')],
 'types_a_number': [('child_entity_types', 'child_entity_types: must be a mapping')],
 'types_a_string': [('child_entity_types', 'child_entity_types: must be a mapping')],
 'types_an_empty_list': [],
 'types_empty': [],
 'types_none': [],
 'types_zero': [],
 'var_cloud_priority': [('state_variables.a',
                         "State variable 'a': cloud_priority must be 'low' or 'high' (got 'urgent'); omit it "
                         'for the default cadence')],
 'var_cloud_priority_empty': [('state_variables.a',
                               "State variable 'a': cloud_priority must be 'low' or 'high' (got ''); omit it "
                               'for the default cadence')],
 'var_control': [('state_variables.a', "State variable 'a': control must be true or false (got 'yes')")],
 'var_everything_wrong': [('state_variables.a', "State variable 'a': unknown type 'decimal'"),
                          ('state_variables.a', "State variable 'a': missing 'label'"),
                          ('state_variables.a', "State variable 'a': unit must be a string (got 5)"),
                          ('state_variables.a', "State variable 'a': control must be true or false (got 0)"),
                          ('state_variables.a',
                           "State variable 'a': cloud_priority must be 'low' or 'high' (got 'x'); omit it "
                           'for the default cadence')],
 'var_label_a_number': [],
 'var_label_empty': [('state_variables.a', "State variable 'a': missing 'label'")],
 'var_label_zero': [('state_variables.a', "State variable 'a': missing 'label'")],
 'var_names_not_text': [('state_variables.True', "State variable 'True': must be a dict")],
 'var_no_label': [('state_variables.a', "State variable 'a': missing 'label'")],
 'var_not_a_mapping': [('state_variables.a', "State variable 'a': must be a dict"),
                       ('state_variables.b', "State variable 'b': must be a dict"),
                       ('state_variables.c', "State variable 'c': must be a dict")],
 'var_type_a_number': [('state_variables.a', "State variable 'a': unknown type '3'")],
 'var_type_empty': [],
 'var_unit': [('state_variables.a', "State variable 'a': unit must be a string (got 5)")],
 'var_unit_a_date': [('state_variables.a',
                      "State variable 'a': unit must be a string (got datetime.date(2026, 1, 2))")],
 'var_unit_falsy': [('state_variables.a', "State variable 'a': unit must be a string (got False)"),
                    ('state_variables.b', "State variable 'b': unit must be a string (got 0)")],
 'var_unknown_type': [('state_variables.a', "State variable 'a': unknown type 'decimal'")],
 'vars_a_list': [('state_variables', 'state_variables: must be a mapping')],
 'vars_a_string': [('state_variables', 'state_variables: must be a mapping')],
 'vars_absent': [],
 'vars_and_child_vars_side_by_side': [('state_variables.a', "State variable 'a': unknown type 'decimal'"),
                                      ('state_variables.a', "State variable 'a': missing 'label'"),
                                      ('state_variables.a',
                                       "State variable 'a': unit must be a string (got 5)"),
                                      ('state_variables.a',
                                       "State variable 'a': control must be true or false (got 0)"),
                                      ('state_variables.a',
                                       "State variable 'a': cloud_priority must be 'low' or 'high' (got "
                                       "'x'); omit it for the default cadence"),
                                      ('child_entity_types.zone',
                                       "child_entity_types.zone.state_variables.a: unknown type 'decimal'"),
                                      ('child_entity_types.zone',
                                       'child_entity_types.zone.state_variables.a: cloud_priority must be '
                                       "'low' or 'high' (got 'x'); omit it for the default cadence"),
                                      ('child_entity_types.zone',
                                       'child_entity_types.zone.state_variables.a: unit must be a string '
                                       '(got 5)'),
                                      ('child_entity_types.zone',
                                       'child_entity_types.zone.state_variables.a: control must be true or '
                                       'false (got 0)')],
 'vars_empty': [],
 'vars_none': [('state_variables', 'state_variables: must be a mapping')]}

RAISES_EXPECTED: dict[str, str] = {'child_var_type_a_list': 'TypeError',
 'count_from_state_over_a_number_of_variables': 'TypeError',
 'var_type_a_list': 'TypeError'}

REFERENCE_EXPECTED: dict[str, tuple[list[str], list[str]]] = {'a_command_named_like_the_marker': ([],
                                     ['0 of an unknown number of commands were readable; params of the rest '
                                      'were not checked']),
 'bound_computed': (['commands.route.params.z: max 99 is above child_entity_types.zone.id_format.max (16), '
                     'so the gate would accept an id the child type cannot have'],
                    []),
 'child_type_computed': ([], ['commands.route.params.z: child_type is computed']),
 'command_computed': ([], []),
 'commands_a_list': ([], ['commands is computed, so no command params were checked']),
 'commands_an_empty_list': ([], ['commands is computed, so no command params were checked']),
 'commands_computed': ([], ['commands is computed, so no command params were checked']),
 'commands_empty': ([], []),
 'commands_none': ([], []),
 'commands_only_computed': ([],
                            ['0 of an unknown number of commands were readable; params of the rest were not '
                             'checked']),
 'commands_partly_computed': (["commands.route.params.z: a child_id param must declare 'child_type' (without "
                               'it the id cannot be resolved to a child type and is coerced as a plain '
                               'integer)'],
                              ['1 of an unknown number of commands were readable; params of the rest were '
                               'not checked']),
 'everything_at_once': (["commands.a.params.z: a child_id param must declare 'child_type' (without it the id "
                         'cannot be resolved to a child type and is coerced as a plain integer)',
                         'commands.a.params.y: min 0 is below child_entity_types.zone.id_format.min (1), so '
                         'the gate would accept an id the child type cannot have'],
                        ['3 of an unknown number of commands were readable; params of the rest were not '
                         'checked',
                         'commands.b.params.c: child_type is computed',
                         "commands.c.params.d: child_type 'out', because child_entity_types keys are "
                         'computed']),
 'format_bound_computed': (['commands.route.params.z: max 99 is above child_entity_types.zone.id_format.max '
                            '(16), so the gate would accept an id the child type cannot have'],
                           []),
 'id_format_computed': ([], []),
 'no_commands': ([], []),
 'param_computed': ([], []),
 'params_computed': ([], []),
 'type_def_computed': ([], []),
 'types_a_list': ([], ['child_entity_types is computed, so no child_type reference was checked']),
 'types_absent': (["commands.route.params.z: child_type 'zone' is not a declared child_entity_type (the "
                   'driver declares none)'],
                  []),
 'types_an_empty_list': ([], ['child_entity_types is computed, so no child_type reference was checked']),
 'types_computed': ([], ['child_entity_types is computed, so no child_type reference was checked']),
 'types_none': (["commands.route.params.z: child_type 'zone' is not a declared child_entity_type (the driver "
                 'declares none)'],
                []),
 'types_partly_computed_declared': (['commands.route.params.z: min 0 is below '
                                     'child_entity_types.zone.id_format.min (1), so the gate would accept an '
                                     'id the child type cannot have'],
                                    []),
 'types_partly_computed_no_child_type': (['commands.route.params.z: a child_id param must declare '
                                          "'child_type' (without it the id cannot be resolved to a child "
                                          'type and is coerced as a plain integer)'],
                                         []),
 'types_partly_computed_undeclared': ([],
                                      ["commands.route.params.z: child_type 'out', because "
                                       'child_entity_types keys are computed'])}

PYTHON_EXPECTED: dict[str, tuple[list[str], list[str]]] = {'a_command_named_like_the_marker': ([],
                                     ['0 of an unknown number of commands were readable; params of the rest '
                                      'were not checked']),
 'bound_computed': (['commands.route.params.z: max 99 is above child_entity_types.zone.id_format.max (16), '
                     'so the gate would accept an id the child type cannot have'],
                    []),
 'child_type_computed': ([], ['commands.route.params.z: child_type is computed']),
 'command_computed': ([], []),
 'commands_a_list': ([], ['commands is computed, so no command params were checked']),
 'commands_an_empty_list': ([], ['commands is computed, so no command params were checked']),
 'commands_computed': ([], ['commands is computed, so no command params were checked']),
 'commands_empty': ([], []),
 'commands_none': ([], []),
 'commands_only_computed': ([],
                            ['0 of an unknown number of commands were readable; params of the rest were not '
                             'checked']),
 'commands_partly_computed': (["commands.route.params.z: a child_id param must declare 'child_type' (without "
                               'it the id cannot be resolved to a child type and is coerced as a plain '
                               'integer)'],
                              ['1 of an unknown number of commands were readable; params of the rest were '
                               'not checked']),
 'everything_at_once': (["commands.a.params.z: a child_id param must declare 'child_type' (without it the id "
                         'cannot be resolved to a child type and is coerced as a plain integer)',
                         'commands.a.params.y: min 0 is below child_entity_types.zone.id_format.min (1), so '
                         'the gate would accept an id the child type cannot have'],
                        ['3 of an unknown number of commands were readable; params of the rest were not '
                         'checked',
                         'commands.b.params.c: child_type is computed',
                         "commands.c.params.d: child_type 'out', because child_entity_types keys are "
                         'computed']),
 'format_bound_computed': (['commands.route.params.z: max 99 is above child_entity_types.zone.id_format.max '
                            '(16), so the gate would accept an id the child type cannot have'],
                           []),
 'id_format_computed': ([], []),
 'no_commands': ([], []),
 'param_computed': ([], []),
 'params_computed': ([], []),
 'type_def_computed': ([], []),
 'types_a_list': ([], ['child_entity_types is computed, so no child_type reference was checked']),
 'types_absent': (["commands.route.params.z: child_type 'zone' is not a declared child_entity_type (the "
                   'driver declares none)'],
                  []),
 'types_an_empty_list': ([], ['child_entity_types is computed, so no child_type reference was checked']),
 'types_computed': ([], ['child_entity_types is computed, so no child_type reference was checked']),
 'types_none': (["commands.route.params.z: child_type 'zone' is not a declared child_entity_type (the driver "
                 'declares none)'],
                []),
 'types_partly_computed_declared': (['commands.route.params.z: min 0 is below '
                                     'child_entity_types.zone.id_format.min (1), so the gate would accept an '
                                     'id the child type cannot have'],
                                    []),
 'types_partly_computed_no_child_type': (['commands.route.params.z: a child_id param must declare '
                                          "'child_type' (without it the id cannot be resolved to a child "
                                          'type and is coerced as a plain integer)'],
                                         []),
 'types_partly_computed_undeclared': ([],
                                      ["commands.route.params.z: child_type 'out', because "
                                       'child_entity_types keys are computed'])}

REASON_EXPECTED: dict[str, str] = {'computed_and_declared': "child_type 'out' is not a declared child_entity_type (declared: zone)",
 'none_declared': "child_type 'zone' is not a declared child_entity_type (the driver declares none)",
 'only_computed': "child_type 'zone' is not a declared child_entity_type (the driver declares none)",
 'some_declared': "child_type 'zon' is not a declared child_entity_type (declared: bus, mic, zone)"}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    issues = validate_driver_issues(definition)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [m for _, m in got]


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED)


@pytest.mark.parametrize("case", sorted(RAISES_EXPECTED))
def test_what_the_walk_raises_on(case: str) -> None:
    with pytest.raises(Exception) as raised:
        validate_driver_definition({**BASE, **RAISES[case]})
    assert type(raised.value).__name__ == RAISES_EXPECTED[case]


def test_every_raising_case_has_an_expectation() -> None:
    assert set(RAISES) == set(RAISES_EXPECTED)


@pytest.mark.parametrize("case", sorted(REFERENCE_EXPECTED))
def test_the_references_and_what_could_not_be_decided(case: str) -> None:
    errors, skipped = child_param_reference_errors(REFERENCE_CASES[case])
    assert (errors, skipped) == REFERENCE_EXPECTED[case]


@pytest.mark.parametrize("case", sorted(PYTHON_EXPECTED))
def test_the_python_surface_reaches_the_same_references(case: str) -> None:
    info = REFERENCE_CASES[case]
    issues, skips = PYTHON_EXPECTED[case]
    assert python_driver_info_issues(info) == issues
    assert python_driver_reference_skips(info) == skips


def test_every_reference_case_has_an_expectation() -> None:
    assert set(REFERENCE_CASES) == set(REFERENCE_EXPECTED) == set(PYTHON_EXPECTED)


@pytest.mark.parametrize("case", sorted(REASON_EXPECTED))
def test_why_a_child_type_reference_does_not_resolve(case: str) -> None:
    assert undeclared_child_type_reason(*REASON_CASES[case]) == REASON_EXPECTED[case]
