"""Pins for two blocks as they load: ``routing:``, and a driver's settings
(``device_settings`` and a config field's ``learned_from``).

``routing:`` is a mapping with a non-empty ``planes`` list. Each plane names
the property a crosspoint lights from, on the destination child type or on
the device itself, and may inherit the destination and source child types,
the command and its two route parameters from the block. Every name has to
be one the driver declares; a parameter or a fixed extra needs a command to
ride on; a fixed extra may not overwrite either end of the route; and two
planes watching one property and routing it the same way are refused.

A device setting is a mapping of a known type whose ``state_key`` (its own
name when left out) is a declared state variable, with ``min`` no greater than
``max``, a ``write`` block, OSC write args the encoder takes, and a ``map``
whose keys are values the setting can take. A config field's ``learned_from``
names a declared state variable, and a secret, a table or a connection field
is never learned.

Each YAML case is a set of top-level keys over one driver. The expected value
is every error ``validate_driver_issues`` reports for the whole definition, in
order, each with the section tag the Driver Builder anchors it to. A Python
case is a ``DRIVER_INFO`` through ``python_driver_info_issues``, which asks
the routing and settings rules the YAML walk asks. A few shapes raise, and are
pinned by the exception they raise.

These record what the checks say today, so a restructuring that keeps the
contract keeps every message, its position and its tag.
"""
from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import (
    UNEVALUATED_KEY,
    config_learned_from_errors,
    device_setting_map_errors,
    device_setting_state_key_errors,
    routing_block_errors,
    validate_driver_definition,
    validate_driver_issues,
)
from openavc.drivers.python_info import python_driver_info_issues

# A case key set to this is left out of the definition altogether.
DROP = "<left out>"

OUTPUT = {
    "id_format": {"type": "integer", "min": 1, "max": 8},
    "state_variables": {
        "source": {"type": "integer", "label": "Source"},
        "audio_source": {"type": "integer", "label": "Audio source"},
    },
}
INPUT = {"id_format": {"type": "integer", "min": 1, "max": 8}}

ROUTE_PARAMS = {
    "output": {"type": "child_id", "child_type": "output"},
    "input": {"type": "integer"},
    "signal": {"type": "string"},
}

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "volume": {"type": "integer", "label": "Volume"},
        "picture": {"type": "string", "label": "Picture"},
        "source_video": {"type": "integer", "label": "Video source"},
    },
    "child_entity_types": {"output": OUTPUT, "input": INPUT},
    "commands": {
        "noop": {"send": "NOOP\r"},
        "route": {"send": "R{output} {input} {signal}\r", "params": ROUTE_PARAMS},
        "route_audio": {
            "send": "A{output} {input}\r",
            "params": {"output": ROUTE_PARAMS["output"], "input": ROUTE_PARAMS["input"]},
        },
        "select": {"send": "S{input}\r", "params": {"input": {"type": "integer"}}},
    },
}


def routing(**block: Any) -> dict[str, Any]:
    return {"routing": block}


def plane(**fields: Any) -> dict[str, Any]:
    return {"route_property": "source", **fields}


def routed(*planes: Any, **block: Any) -> dict[str, Any]:
    """A block whose defaults route an output from an input with ``route``."""
    defaults = {
        "destination_child_type": "output",
        "source_child_type": "input",
        "command": "route",
    }
    return {"routing": {**defaults, **block, "planes": list(planes)}}


def setting(**fields: Any) -> dict[str, Any]:
    return {"device_settings": {"volume": {"type": "integer", "write": {"send": "V{value}\r"}, **fields}}}


def settings(**entries: Any) -> dict[str, Any]:
    return {"device_settings": entries}


def learned(**fields: Any) -> dict[str, Any]:
    return {"config_schema": {"zone": {"type": "string", **fields}}}


WRITE = {"send": "X{value}\r"}

CASES: dict[str, dict[str, Any]] = {
    # --- routing: the block ---------------------------------------------
    "routing_absent": {},
    "routing_none": {"routing": None},
    "routing_zero": {"routing": 0},
    "routing_false": {"routing": False},
    "routing_empty_text": {"routing": ""},
    "routing_text": {"routing": "route"},
    "routing_list": {"routing": [plane()]},
    "routing_empty_mapping": {"routing": {}},
    "routing_planes_none": routing(planes=None),
    "routing_planes_empty": routing(planes=[]),
    "routing_planes_mapping": routing(planes={"a": plane()}),
    "routing_planes_text": routing(planes="source"),
    "routing_good": routed(plane()),
    "routing_good_two": routed(plane(), plane(route_property="audio_source", command="route_audio")),
    "routing_plane_not_mapping": routed(5, "x", None, [], plane()),
    # --- routing: the keys a plane inherits -----------------------------
    "routing_inherited_empty": routing(
        destination_child_type="", source_child_type="", command="",
        destination_param="", source_param="", planes=[plane()],
    ),
    "routing_inherited_numbers": routing(
        destination_child_type=5, source_child_type=0, command=True,
        destination_param=[], source_param={}, planes=[plane()],
    ),
    "routing_inherited_false": routing(
        destination_child_type=False, command=False, planes=[plane()],
    ),
    "routing_inherited_good_bad_plane": routing(
        destination_child_type="output", command="route", destination_param="output",
        source_param="input", planes="x",
    ),
    # --- routing: route_property ----------------------------------------
    "routing_no_property": routed({"label": "Video"}),
    "routing_property_empty": routed(plane(route_property="")),
    "routing_property_none": routed(plane(route_property=None)),
    "routing_property_number": routed(plane(route_property=5)),
    "routing_property_list": routed(plane(route_property=["source"])),
    "routing_property_undeclared": routed(plane(route_property="sorce")),
    "routing_property_far": routed(plane(route_property="zzzz")),
    "routing_property_on_device": routing(command="select", planes=[plane(route_property="source_video")]),
    "routing_property_on_device_missing": routing(command="select", planes=[plane(route_property="source")]),
    "routing_property_device_vars_none": {
        "state_variables": None,
        **routing(command="select", planes=[plane(route_property="source_video")]),
    },
    "routing_property_device_vars_list": {
        "state_variables": ["source_video"],
        **routing(command="select", planes=[plane(route_property="source_video")]),
    },
    "routing_property_inherited_from_block_only": routing(
        route_property="source", destination_child_type="output", planes=[{}],
    ),
    # --- routing: the destination child type ----------------------------
    "routing_dest_undeclared": routed(plane(destination_child_type="outptu")),
    "routing_dest_undeclared_no_property": routed({"destination_child_type": "nope"}),
    "routing_dest_number": routed(plane(destination_child_type=5)),
    "routing_dest_plane_empty": routed(plane(destination_child_type="")),
    "routing_dest_plane_none": routed(plane(destination_child_type=None)),
    "routing_dest_plane_none_device_property": routed(
        plane(destination_child_type=None, route_property="source_video"),
    ),
    "routing_dest_child_not_mapping": {
        "child_entity_types": {"output": 5, "input": INPUT},
        **routed(plane()),
    },
    "routing_dest_child_vars_unevaluated": {
        "child_entity_types": {"output": {"state_variables": UNEVALUATED_KEY}, "input": INPUT},
        **routed(plane(route_property="anything")),
    },
    "routing_dest_child_vars_none": {
        "child_entity_types": {"output": {"id_format": {"type": "integer"}}, "input": INPUT},
        **routed(plane()),
    },
    "routing_dest_child_vars_list": {
        "child_entity_types": {"output": {"state_variables": ["source"]}, "input": INPUT},
        **routed(plane()),
    },
    "routing_child_types_none": {"child_entity_types": None, **routed(plane())},
    "routing_child_types_list": {"child_entity_types": ["output"], **routed(plane())},
    # --- routing: the source child type ---------------------------------
    "routing_source_undeclared": routed(plane(source_child_type="inptu")),
    "routing_source_number": routed(plane(source_child_type=7)),
    "routing_source_empty": routed(plane(source_child_type="")),
    "routing_source_none": routed(plane(source_child_type=None)),
    # --- routing: the command -------------------------------------------
    "routing_command_undeclared": routed(plane(command="rout")),
    "routing_command_far": routed(plane(command="zzzzzz")),
    "routing_command_number": routed(plane(command=5)),
    "routing_command_true": routed(plane(command=True)),
    "routing_command_empty": routed(plane(command="")),
    "routing_command_none": routed(plane(command=None)),
    "routing_command_not_mapping": {
        "commands": {**BASE["commands"], "route": "R"},
        **routed(plane(destination_param="nope")),
    },
    "routing_command_params_unevaluated": {
        "commands": {**BASE["commands"], "route": {"send": "R", "params": UNEVALUATED_KEY}},
        **routed(plane(destination_param="nope", params={"x": 1})),
    },
    "routing_command_params_none": {
        "commands": {**BASE["commands"], "route": {"send": "R"}},
        **routed(plane(destination_param="output")),
    },
    "routing_command_params_list": {
        "commands": {**BASE["commands"], "route": {"send": "R", "params": ["output"]}},
        **routed(plane(source_param="input")),
    },
    "routing_commands_none": {"commands": None, **routed(plane())},
    "routing_commands_list": {"commands": ["route"], **routed(plane())},
    # --- routing: what needs a command ----------------------------------
    "routing_params_no_command": routing(
        destination_child_type="output", planes=[plane(params={"signal": "VIDEO"})],
    ),
    "routing_params_none_no_command": routing(
        destination_child_type="output", planes=[plane(params=None)],
    ),
    "routing_params_empty_no_command": routing(
        destination_child_type="output", planes=[plane(params={})],
    ),
    "routing_dest_param_no_command": routing(
        destination_child_type="output", planes=[plane(destination_param="output")],
    ),
    "routing_source_param_no_command": routing(
        destination_child_type="output", planes=[plane(source_param="input")],
    ),
    "routing_block_param_no_command": routing(
        destination_child_type="output", source_param="input", planes=[plane()],
    ),
    "routing_block_dest_param_empty_no_command": routing(
        destination_child_type="output", destination_param="", planes=[plane()],
    ),
    "routing_param_number_no_command": routing(
        destination_child_type="output", planes=[plane(destination_param=5)],
    ),
    "routing_params_and_param_no_command": routing(
        destination_child_type="output",
        planes=[plane(destination_param="output", params={"signal": "A"})],
    ),
    "routing_command_number_with_params": routed(plane(command=5, params={"signal": "A"})),
    "routing_block_params_no_command": routing(
        destination_child_type="output", params={"signal": "VIDEO"}, planes=[plane()],
    ),
    "routing_block_params_unknown": routed(plane(), params={"signl": "VIDEO"}),
    "routing_block_params_not_mapping": routed(plane(), params=5),
    # --- routing: the route's two ends ----------------------------------
    "routing_ends_good": routed(plane(destination_param="output", source_param="input")),
    "routing_dest_param_unknown": routed(plane(destination_param="outptu")),
    "routing_source_param_unknown": routed(plane(source_param="zzzz")),
    "routing_block_ends_unknown": routed(plane(), destination_param="out", source_param="in"),
    "routing_end_params_empty": routed(plane(destination_param="", source_param="")),
    "routing_end_params_number": routed(plane(destination_param=5, source_param=None)),
    "routing_ends_on_undeclared_command": routed(
        plane(command="nope", destination_param="x", source_param="y", params={"z": 1}),
    ),
    # --- routing: fixed extras ------------------------------------------
    "routing_params_good": routed(plane(params={"signal": "VIDEO"})),
    "routing_params_not_mapping": routed(plane(params=5)),
    "routing_params_list": routed(plane(params=["signal"])),
    "routing_params_text": routed(plane(params="signal=VIDEO")),
    "routing_params_unknown": routed(plane(params={"signl": "VIDEO", "zzzz": 1})),
    "routing_params_overwrite_dest": routed(
        plane(destination_param="output", source_param="input", params={"output": 1}),
    ),
    "routing_params_overwrite_source": routed(
        plane(destination_param="output", source_param="input", params={"input": 1, "signal": "A"}),
    ),
    "routing_params_overwrite_inherited_end": routed(
        plane(params={"input": 2}), source_param="input",
    ),
    "routing_params_same_end_names": routed(
        plane(destination_param="input", source_param="input", params={"input": 1}),
    ),
    "routing_dest_list": routed(plane(destination_child_type=["output"], source_child_type={"a": 1})),
    "routing_command_list": routed(plane(command=["route"])),
    "routing_params_on_command_without_params": {
        "commands": {**BASE["commands"], "route": {"send": "R"}},
        **routed(plane(params={"signal": "VIDEO"})),
    },
    "routing_params_on_command_params_list": {
        "commands": {**BASE["commands"], "route": {"send": "R", "params": ["signal"]}},
        **routed(plane(params={"signal": "VIDEO"})),
    },
    "routing_params_on_unevaluated_command": {
        "commands": {**BASE["commands"], "route": {"send": "R", "params": UNEVALUATED_KEY}},
        **routed(plane(params={"anything": 1})),
    },
    # --- routing: two planes that are one ------------------------------
    "routing_duplicate": routed(plane(), plane()),
    "routing_duplicate_third": routed(plane(), plane(route_property="audio_source"), plane()),
    "routing_duplicate_twice": routed(plane(), plane(), plane()),
    "routing_same_property_other_params": routed(
        plane(params={"signal": "VIDEO"}), plane(params={"signal": "ALL"}),
    ),
    "routing_same_property_same_params": routed(
        plane(params={"signal": "VIDEO"}), plane(params={"signal": "VIDEO"}),
    ),
    "routing_same_params_other_order": routed(
        plane(params={"signal": "VIDEO", "input": 1}), plane(params={"input": 1, "signal": "VIDEO"}),
    ),
    "routing_same_params_text_and_number": routed(
        plane(params={"signal": 1}), plane(params={"signal": "1"}),
    ),
    "routing_same_property_other_command": routed(plane(), plane(command="route_audio")),
    "routing_same_property_other_dest": routing(
        command="route",
        planes=[plane(destination_child_type="output"), plane(destination_child_type="input")],
    ),
    "routing_same_property_number_command": routed(plane(command=5), plane(command="5")),
    "routing_same_property_params_not_mapping": routed(plane(params=5), plane(params=[1])),
    "routing_duplicate_undeclared_dest": routed(
        plane(destination_child_type="nope"), plane(destination_child_type="nope"),
    ),
    "routing_duplicate_no_property": routed({"label": "A"}, {"label": "B"}),
    # --- routing: everything at once ------------------------------------
    "routing_everything_wrong": routing(
        command=7, destination_param="",
        planes=[
            {"destination_child_type": "outptu", "params": 5, "source_param": "x"},
            plane(source_child_type="nope", command="rout", params={"q": 1}),
            plane(command="route", destination_param="zz", params={"zz": 1}),
            plane(command="route", destination_param="zz", params={"zz": 1}),
        ],
    ),
    # --- device_settings: the block -------------------------------------
    "settings_none": {"device_settings": None},
    "settings_empty": {"device_settings": {}},
    "settings_list": {"device_settings": [{"volume": {}}]},
    "settings_text": {"device_settings": "volume"},
    "settings_zero": {"device_settings": 0},
    "settings_false": {"device_settings": False},
    "settings_entry_not_mapping": settings(volume=5, power=None, picture=["x"]),
    "settings_good": setting(),
    "settings_good_three": settings(
        volume={"type": "integer", "min": 0, "max": 100, "write": WRITE},
        power={"type": "boolean", "write": WRITE},
        picture={"type": "enum", "values": ["vivid", "cinema"], "write": WRITE},
    ),
    # --- device_settings: the type --------------------------------------
    "settings_type_unknown": setting(type="int"),
    "settings_type_empty": setting(type=""),
    "settings_type_none": setting(type=None),
    "settings_type_missing": settings(volume={"write": WRITE}),
    "settings_type_cased": setting(type="Integer"),
    "settings_type_number": setting(type=5),
    "settings_type_false": setting(type=False),
    # --- device_settings: state_key -------------------------------------
    "settings_state_key_default_undeclared": settings(volumes={"type": "integer", "write": WRITE}),
    "settings_state_key_undeclared": setting(state_key="volum"),
    "settings_state_key_declared": settings(level={"type": "integer", "state_key": "volume", "write": WRITE}),
    "settings_state_key_number": setting(state_key=5),
    "settings_state_key_none": setting(state_key=None),
    "settings_state_key_empty": setting(state_key=""),
    "settings_state_vars_none": {"state_variables": None, **setting()},
    "settings_state_vars_list": {"state_variables": ["volume"], **setting()},
    "settings_state_vars_unevaluated": {
        "state_variables": {UNEVALUATED_KEY: True},
        **settings(anything={"type": "integer", "write": WRITE}),
    },
    # --- device_settings: min and max -----------------------------------
    "settings_min_over_max": setting(min=10, max=1),
    "settings_min_over_max_float": setting(min=1.5, max=1.25),
    "settings_min_over_max_mixed": setting(min=2, max=1.5),
    "settings_min_equals_max": setting(min=5, max=5),
    "settings_min_under_max": setting(min=0, max=100),
    "settings_min_bool": setting(min=True, max=0),
    "settings_max_bool": setting(min=1, max=False),
    "settings_min_text": setting(min="10", max=1),
    "settings_min_only": setting(min=10),
    "settings_min_nan": setting(min=float("nan"), max=1),
    "settings_min_inf": setting(min=float("inf"), max=1),
    # --- device_settings: write -----------------------------------------
    "settings_no_write": settings(volume={"type": "integer"}),
    "settings_write_none": setting(write=None),
    "settings_write_text": setting(write="V{value}"),
    "settings_write_list": setting(write=[WRITE]),
    "settings_write_empty": setting(write={}),
    "settings_write_osc_good": setting(write={"address": "/vol", "args": [{"type": "i", "value": "{value}"}]}),
    "settings_write_osc_bad_tag": setting(write={"address": "/vol", "args": [{"type": "q", "value": "{value}"}]}),
    "settings_write_osc_args_text": setting(write={"address": "/vol", "args": "i"}),
    "settings_write_osc_no_args": setting(write={"address": "/vol"}),
    "settings_write_osc_address_none": setting(write={"address": None, "args": [{"type": "q"}]}),
    "settings_write_osc_address_empty": setting(write={"address": "", "args": [{"type": "q"}]}),
    # --- device_settings: map -------------------------------------------
    "settings_map_none": setting(map=None),
    "settings_map_empty": setting(map={}),
    "settings_map_list": setting(map=["a"]),
    "settings_map_text": setting(map="a"),
    "settings_map_good": setting(map={"1": "LOW", 2: "HIGH", 3.5: 7}),
    "settings_map_bool_key": setting(map={True: "ON", "2": "B"}),
    "settings_map_bool_word": setting(map={"1": False, "2": True}),
    "settings_map_bool_key_and_word": setting(map={True: False}),
    "settings_map_not_scalar": setting(map={"1": ["a"], "2": None, (1, 2): "x"}),
    "settings_map_boolean_good": settings(power={"type": "boolean", "write": WRITE, "map": {"true": "ON", "false": "OFF"}}),
    "settings_map_boolean_spaced": settings(power={"type": "boolean", "write": WRITE, "map": {" TRUE ": "ON", "False": "OFF"}}),
    "settings_map_boolean_bad_keys": settings(power={"type": "boolean", "write": WRITE, "map": {"on": "1", "yes": "2", 1: "3"}}),
    "settings_map_enum_good": settings(
        picture={"type": "enum", "values": ["vivid", "cinema"], "write": WRITE, "map": {"vivid": "V", "cinema": "C"}},
    ),
    "settings_map_enum_bad_key": settings(
        picture={"type": "enum", "values": ["vivid", "cinema"], "write": WRITE, "map": {"vivd": "V", "cinema": "C"}},
    ),
    "settings_map_enum_object_values": settings(
        picture={
            "type": "enum", "write": WRITE,
            "values": [{"value": "vivid", "label": "Vivid"}, {"label": "no value"}, 3],
            "map": {"vivid": "V", "3": "T", "no value": "N"},
        },
    ),
    "settings_map_enum_number_keys": settings(
        picture={"type": "enum", "values": [1, 2], "write": WRITE, "map": {1: "A", "2": "B", 3: "C"}},
    ),
    "settings_map_enum_no_values": settings(picture={"type": "enum", "write": WRITE, "map": {"x": "X"}}),
    "settings_map_enum_values_text": settings(picture={"type": "enum", "values": "vivid", "write": WRITE, "map": {"x": "X"}}),
    "settings_map_type_missing": settings(volume={"write": WRITE, "map": {"x": "X"}}),
    "settings_map_false_key": setting(map={False: "OFF", "1": "ON"}),
    "settings_map_boolean_bad_key_bool_word": settings(
        power={"type": "boolean", "write": WRITE, "map": {"on": True, "maybe": "M"}},
    ),
    "settings_map_enum_empty_values": settings(picture={"type": "enum", "values": [], "write": WRITE, "map": {"x": "X"}}),
    "settings_map_enum_values_without_value": settings(
        picture={"type": "enum", "values": [{"label": "A"}], "write": WRITE, "map": {"x": "X"}},
    ),
    "settings_map_values_on_integer": setting(values=["a"], map={"b": "B"}),
    "settings_map_on_string": settings(picture={"type": "string", "write": WRITE, "map": {"anything": "A"}}),
    # --- device_settings: everything at once ----------------------------
    "settings_everything_wrong": settings(
        volume={"type": "int", "state_key": "volum", "min": 9, "max": 1, "map": {True: "x"}},
        power=None,
        picture={"type": "enum", "values": ["a"], "write": {"address": "/p", "args": [{"type": "q"}]}, "map": {"b": "B"}},
    ),
    # --- config_schema: learned_from ------------------------------------
    "learned_good": learned(learned_from="picture"),
    "learned_none": learned(learned_from=None),
    "learned_empty": learned(learned_from=""),
    "learned_blank": learned(learned_from="  "),
    "learned_number": learned(learned_from=5),
    "learned_false": learned(learned_from=False),
    "learned_list": learned(learned_from=["picture"]),
    "learned_undeclared": learned(learned_from="pictures"),
    "learned_padded": learned(learned_from=" picture "),
    "learned_secret_flag": learned(learned_from="picture", secret=True),
    "learned_password_type": learned(learned_from="picture", type="password"),
    "learned_secret_name": {"config_schema": {"password": {"type": "string", "learned_from": "picture"}}},
    "learned_table": learned(learned_from="picture", type="table"),
    "learned_list_type": learned(learned_from="picture", type="list"),
    "learned_connection_host": {"config_schema": {"host": {"type": "string", "learned_from": "picture"}}},
    "learned_connection_port": {"config_schema": {"port": {"type": "integer", "learned_from": "volume"}}},
    "learned_everything": {
        "config_schema": {"password": {"type": "table", "secret": True, "learned_from": "nope"}},
    },
    "learned_connection_secret_table": {
        "config_schema": {"host": {"type": "table", "secret": True, "learned_from": "nope"}},
    },
    "learned_state_vars_none": {"state_variables": None, **learned(learned_from="picture")},
    "learned_state_vars_unevaluated": {
        "state_variables": {UNEVALUATED_KEY: True}, **learned(learned_from="anything"),
    },
    "learned_two_fields": {
        "config_schema": {
            "zone": {"type": "string", "learned_from": "zones"},
            "other": {"type": "string"},
            "mode": {"type": "string", "learned_from": ""},
        },
    },
    "learned_schema_list": {"config_schema": [{"learned_from": "x"}]},
    "learned_field_not_mapping": {"config_schema": {"zone": "learned_from"}},
    # --- across the sections --------------------------------------------
    "order_across_sections": {
        "commands": {**BASE["commands"], "bad": 5},
        "command_suffix": 5,
        "config_schema": {"zone": {"type": "string", "learned_from": "nope"}},
        "device_settings": {"volume": {"type": "int"}},
        "push": {"type": "nope"},
        "routing": {"planes": []},
    },
    "order_settings_before_learned_field_after": {
        "device_settings": {"volume": {"type": "int", "write": WRITE}},
        "config_schema": {
            "a": {"type": "string", "learned_from": "x"},
            "b": {"type": "table", "learned_from": "volume"},
        },
    },
    "order_strict": {
        "device_settings": {"volume": {"type": "integer", "write": WRITE, "colour": 1}},
        "routing": {"planes": [plane(destination_child_type="output", command="route", hue=1)]},
    },
}

# Strict cases also reject keys the contract does not declare.
STRICT = {"order_strict"}

# Definitions that raise, by the exception they raise.
# A fixed extra whose name is not text, and not a parameter, reaches the
# did-you-mean, which cannot compare it.
RAISES: dict[str, type[BaseException]] = {
    "routing_params_numeric_keys": TypeError,
}
RAISING_CASES: dict[str, dict[str, Any]] = {
    "routing_params_numeric_keys": routed(plane(params={1: "a", 2.5: "b"})),
}

# A device setting whose type is not hashable reaches the lookup of the known
# types, which cannot take it; the walk raises.
WALK_RAISES: dict[str, type[BaseException]] = {
    "settings_type_list": TypeError,
    "settings_type_mapping": TypeError,
}
WALK_RAISING_CASES: dict[str, dict[str, Any]] = {
    "settings_type_list": setting(type=["integer"]),
    "settings_type_mapping": setting(type={"integer": 1}),
}

# --- the Python surface --------------------------------------------------

PY_BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": dict(BASE["state_variables"]),
    "child_entity_types": {"output": OUTPUT, "input": INPUT},
    "commands": dict(BASE["commands"]),
}

PY_CASES: dict[str, dict[str, Any]] = {
    "py_good": {},
    "py_routing_good": routed(plane()),
    "py_routing_bad": routed(
        plane(route_property="sorce", destination_param="x", params={"output": 1, "q": 2}),
        plane(route_property="sorce"),
    ),
    "py_routing_not_mapping": {"routing": [1]},
    "py_routing_commands_unevaluated": {
        "commands": {UNEVALUATED_KEY: True},
        **routed(plane(route_property="sorce")),
    },
    "py_routing_commands_empty": {"commands": {}, **routed(plane(route_property="sorce"))},
    "py_routing_dest_vars_unevaluated": {
        "child_entity_types": {"output": {"state_variables": UNEVALUATED_KEY}, "input": INPUT},
        **routed(plane(route_property="anything", command="nope")),
    },
    "py_routing_device_itself": routing(command="select", planes=[plane(route_property="sorce_video")]),
    "py_settings_good": setting(),
    "py_settings_undeclared": settings(volumes={"type": "integer"}, level={"state_key": "lvl"}),
    "py_settings_not_mapping": {"device_settings": [1]},
    "py_settings_entry_not_mapping": settings(volume=5, power={"state_key": 7}),
    "py_settings_vars_unevaluated": {
        "state_variables": {UNEVALUATED_KEY: True}, **settings(anything={"type": "integer"}),
    },
    "py_settings_type_and_bounds_unjudged": setting(type="int", min=9, max=1, write=None),
    "py_settings_map": setting(map={"1": "A"}),
    "py_learned": {
        "config_schema": {
            "zone": {"type": "string", "learned_from": "zones"},
            "host": {"type": "table", "secret": True, "learned_from": "picture"},
            UNEVALUATED_KEY: {"learned_from": "x"},
            "plain": "x",
        },
    },
    "py_learned_schema_list": {"config_schema": [1]},
    "py_order": {
        "config_schema": {"zone": {"learned_from": "zones"}},
        "device_settings": {"volumes": {"type": "integer"}},
        "routing": {"planes": []},
    },
}


# --- each rule asked directly ---------------------------------------------

DIRECT: dict[str, Any] = {
    "state_key_vars_not_mapping": lambda: device_setting_state_key_errors("W", "volume", {}, 5),
    "state_key_vars_unevaluated": lambda: device_setting_state_key_errors(
        "W", "x", {}, {UNEVALUATED_KEY: 1},
    ),
    "state_key_declared": lambda: device_setting_state_key_errors("W", "volume", {}, {"volume": {}}),
    "state_key_undeclared": lambda: device_setting_state_key_errors("W", "volume", {}, {"power": {}}),
    "state_key_given": lambda: device_setting_state_key_errors(
        "W", "volume", {"state_key": "power"}, {"power": {}},
    ),
    "state_key_not_text": lambda: device_setting_state_key_errors(
        "W", "volume", {"state_key": ["power"]}, {"power": {}},
    ),
    "learned_absent": lambda: config_learned_from_errors("zone", {}, {"a": {}}),
    "learned_none": lambda: config_learned_from_errors("zone", {"learned_from": None}, {}),
    "learned_vars_not_mapping": lambda: config_learned_from_errors(
        "zone", {"learned_from": "a"}, ["a"],
    ),
    "learned_vars_unevaluated": lambda: config_learned_from_errors(
        "zone", {"learned_from": "a"}, {UNEVALUATED_KEY: 1},
    ),
    "learned_all": lambda: config_learned_from_errors(
        "port", {"learned_from": "a", "type": "table", "secret": True}, {"b": {}},
    ),
    "map_absent": lambda: device_setting_map_errors("W", {}),
    "map_on_boolean": lambda: device_setting_map_errors(
        "W", {"type": "boolean", "map": {"true": "1", "no": "0"}},
    ),
    "map_on_enum": lambda: device_setting_map_errors(
        "W", {"type": "enum", "values": ["a", {"value": 2}], "map": {"a": "A", "2": "B", "c": "C"}},
    ),
    "routing_absent": lambda: routing_block_errors({}),
    "routing_none": lambda: routing_block_errors({"routing": None}),
    "routing_false": lambda: routing_block_errors({"routing": False}),
    "routing_alone": lambda: routing_block_errors(
        {"routing": {"command": "x", "planes": [plane()]}},
    ),
    "routing_against_nothing": lambda: routing_block_errors(
        {"routing": {"destination_child_type": "out", "planes": [plane(), plane()]}},
    ),
}


EXPECTED: dict[str, list[tuple[str, str]]] = {'learned_blank': [('config_schema.zone',
                    "Config field 'zone': learned_from must name a state variable")],
 'learned_connection_host': [('config_schema.host',
                              "Config field 'host': a connection field cannot be learned_from a "
                              'state variable, because a reported value would change how the '
                              'device is reached')],
 'learned_connection_port': [('config_schema.port',
                              "Config field 'port': a connection field cannot be learned_from a "
                              'state variable, because a reported value would change how the '
                              'device is reached')],
 'learned_connection_secret_table': [('config_schema.host',
                                      "Config field 'host': a secret field cannot be learned_from "
                                      'a state variable, because the value would have to be shown '
                                      'as state first'),
                                     ('config_schema.host',
                                      "Config field 'host': a table field cannot be learned_from a "
                                      'state variable, because it has no single value to fill in'),
                                     ('config_schema.host',
                                      "Config field 'host': a connection field cannot be "
                                      'learned_from a state variable, because a reported value '
                                      'would change how the device is reached'),
                                     ('config_schema.host',
                                      "Config field 'host': learned_from 'nope' is not a declared "
                                      'state variable, so the field would never be filled in')],
 'learned_empty': [('config_schema.zone',
                    "Config field 'zone': learned_from must name a state variable")],
 'learned_everything': [('config_schema.password',
                         "Config field 'password': a secret field cannot be learned_from a state "
                         'variable, because the value would have to be shown as state first'),
                        ('config_schema.password',
                         "Config field 'password': a table field cannot be learned_from a state "
                         'variable, because it has no single value to fill in'),
                        ('config_schema.password',
                         "Config field 'password': a connection field cannot be learned_from a "
                         'state variable, because a reported value would change how the device is '
                         'reached'),
                        ('config_schema.password',
                         "Config field 'password': learned_from 'nope' is not a declared state "
                         'variable, so the field would never be filled in')],
 'learned_false': [('config_schema.zone',
                    "Config field 'zone': learned_from must name a state variable")],
 'learned_field_not_mapping': [],
 'learned_good': [],
 'learned_list': [('config_schema.zone',
                   "Config field 'zone': learned_from must name a state variable")],
 'learned_list_type': [],
 'learned_none': [],
 'learned_number': [('config_schema.zone',
                     "Config field 'zone': learned_from must name a state variable")],
 'learned_padded': [('config_schema.zone',
                     "Config field 'zone': learned_from ' picture ' is not a declared state "
                     'variable, so the field would never be filled in')],
 'learned_password_type': [('config_schema.zone',
                            "Config field 'zone': a secret field cannot be learned_from a state "
                            'variable, because the value would have to be shown as state first')],
 'learned_schema_list': [],
 'learned_secret_flag': [('config_schema.zone',
                          "Config field 'zone': a secret field cannot be learned_from a state "
                          'variable, because the value would have to be shown as state first')],
 'learned_secret_name': [('config_schema.password',
                          "Config field 'password': a secret field cannot be learned_from a state "
                          'variable, because the value would have to be shown as state first'),
                         ('config_schema.password',
                          "Config field 'password': a connection field cannot be learned_from a "
                          'state variable, because a reported value would change how the device is '
                          'reached')],
 'learned_state_vars_none': [('config_schema.zone',
                              "Config field 'zone': learned_from 'picture' is not a declared state "
                              'variable, so the field would never be filled in'),
                             ('state_variables', 'state_variables: must be a mapping')],
 'learned_state_vars_unevaluated': [('state_variables.<unevaluated>',
                                     "State variable '<unevaluated>': must be a dict")],
 'learned_table': [('config_schema.zone',
                    "Config field 'zone': a table field cannot be learned_from a state variable, "
                    'because it has no single value to fill in')],
 'learned_two_fields': [('config_schema.zone',
                         "Config field 'zone': learned_from 'zones' is not a declared state "
                         'variable, so the field would never be filled in'),
                        ('config_schema.mode',
                         "Config field 'mode': learned_from must name a state variable")],
 'learned_undeclared': [('config_schema.zone',
                         "Config field 'zone': learned_from 'pictures' is not a declared state "
                         'variable, so the field would never be filled in')],
 'order_across_sections': [('commands.bad', "Command 'bad': must be a dict"),
                           ('command_suffix', 'command_suffix: must be a string'),
                           ('config_schema.zone',
                            "Config field 'zone': learned_from 'nope' is not a declared state "
                            'variable, so the field would never be filled in'),
                           ('device_settings.volume',
                            "Device setting 'volume': unknown type 'int'"),
                           ('device_settings.volume',
                            "Device setting 'volume': missing 'write' block (send / path / "
                            'address); a device setting must be writable'),
                           ('push',
                            "push: missing or unknown 'type' (supported: multicast, sse, "
                            'tcp_listener, http_listener, udp_listener)'),
                           ('routing',
                            "routing: needs a non-empty 'planes' list, the independent things this "
                            'device routes (usually one; a decoder that routes video, audio and '
                            'USB separately declares three)')],
 'order_settings_before_learned_field_after': [('config_schema.a',
                                                "Config field 'a': learned_from 'x' is not a "
                                                'declared state variable, so the field would never '
                                                'be filled in'),
                                               ('config_schema.b',
                                                "Config field 'b': a table field cannot be "
                                                'learned_from a state variable, because it has no '
                                                'single value to fill in'),
                                               ('device_settings.volume',
                                                "Device setting 'volume': unknown type 'int'")],
 'order_strict': [('device_settings.volume', "device_settings.volume: unknown key 'colour'"),
                  ('routing.planes[0]', "routing.planes[0]: unknown key 'hue'")],
 'routing_absent': [],
 'routing_block_dest_param_empty_no_command': [('routing',
                                                'routing.destination_param: must be a non-empty '
                                                'string'),
                                               ('routing',
                                                'routing.planes[0]: names a routing parameter but '
                                                "no 'command'. Declare the routing command here or "
                                                'on the routing block')],
 'routing_block_ends_unknown': [('routing',
                                 "routing.planes[0]: destination_param 'out' is not a parameter of "
                                 "command 'route'"),
                                ('routing',
                                 "routing.planes[0]: source_param 'in' is not a parameter of "
                                 "command 'route'")],
 'routing_block_param_no_command': [('routing',
                                     'routing.planes[0]: names a routing parameter but no '
                                     "'command'. Declare the routing command here or on the "
                                     'routing block')],
 'routing_block_params_no_command': [],
 'routing_block_params_not_mapping': [],
 'routing_block_params_unknown': [],
 'routing_child_types_list': [('child_entity_types', 'child_entity_types: must be a mapping'),
                              ('routing',
                               "routing.planes[0]: destination_child_type 'output' is not a "
                               'declared child_entity_type'),
                              ('routing',
                               "routing.planes[0]: source_child_type 'input' is not a declared "
                               'child_entity_type')],
 'routing_child_types_none': [('commands',
                               "commands.route.params.output: child_type 'output' is not a "
                               'declared child_entity_type (the driver declares none)'),
                              ('commands',
                               "commands.route_audio.params.output: child_type 'output' is not a "
                               'declared child_entity_type (the driver declares none)'),
                              ('routing',
                               "routing.planes[0]: destination_child_type 'output' is not a "
                               'declared child_entity_type'),
                              ('routing',
                               "routing.planes[0]: source_child_type 'input' is not a declared "
                               'child_entity_type')],
 'routing_command_empty': [],
 'routing_command_far': [('routing',
                          "routing.planes[0]: command 'zzzzzz' is not a declared command")],
 'routing_command_list': [],
 'routing_command_none': [],
 'routing_command_not_mapping': [('commands.route', "Command 'route': must be a dict")],
 'routing_command_number': [],
 'routing_command_number_with_params': [('routing',
                                         "routing.planes[0]: 'params' needs a 'command' to send "
                                         'them on. Declare the routing command here or on the '
                                         'routing block')],
 'routing_command_params_list': [('routing',
                                  "routing.planes[0]: source_param 'input' is not a parameter of "
                                  "command 'route'")],
 'routing_command_params_none': [('routing',
                                  "routing.planes[0]: destination_param 'output' is not a "
                                  "parameter of command 'route'")],
 'routing_command_params_unevaluated': [],
 'routing_command_true': [],
 'routing_command_undeclared': [('routing',
                                 "routing.planes[0]: command 'rout' is not a declared command (did "
                                 "you mean 'route'?)")],
 'routing_commands_list': [('commands', 'commands: must be a mapping'),
                           ('routing',
                            "routing.planes[0]: command 'route' is not a declared command")],
 'routing_commands_none': [('commands', 'commands: must be a mapping'),
                           ('routing',
                            "routing.planes[0]: command 'route' is not a declared command")],
 'routing_dest_child_not_mapping': [('child_entity_types.output',
                                     'child_entity_types.output: must be a mapping')],
 'routing_dest_child_vars_list': [('child_entity_types.output',
                                   'child_entity_types.output.state_variables: must be a mapping'),
                                  ('routing',
                                   "routing.planes[0]: route_property 'source' is not a state "
                                   "variable of child type 'output'")],
 'routing_dest_child_vars_none': [('routing',
                                   "routing.planes[0]: route_property 'source' is not a state "
                                   "variable of child type 'output'")],
 'routing_dest_child_vars_unevaluated': [('child_entity_types.output',
                                          'child_entity_types.output.state_variables: must be a '
                                          'mapping')],
 'routing_dest_list': [('routing',
                        "routing.planes[0]: route_property 'source' is not a state variable of "
                        'this driver')],
 'routing_dest_number': [('routing',
                          "routing.planes[0]: route_property 'source' is not a state variable of "
                          'this driver')],
 'routing_dest_param_no_command': [('routing',
                                    'routing.planes[0]: names a routing parameter but no '
                                    "'command'. Declare the routing command here or on the routing "
                                    'block')],
 'routing_dest_param_unknown': [('routing',
                                 "routing.planes[0]: destination_param 'outptu' is not a parameter "
                                 "of command 'route' (did you mean 'output'?)")],
 'routing_dest_plane_empty': [('routing',
                               "routing.planes[0]: route_property 'source' is not a state variable "
                               'of this driver')],
 'routing_dest_plane_none': [('routing',
                              "routing.planes[0]: route_property 'source' is not a state variable "
                              'of this driver')],
 'routing_dest_plane_none_device_property': [],
 'routing_dest_undeclared': [('routing',
                              "routing.planes[0]: destination_child_type 'outptu' is not a "
                              "declared child_entity_type (did you mean 'output'?)")],
 'routing_dest_undeclared_no_property': [('routing',
                                          "routing.planes[0]: missing required 'route_property' "
                                          '(the property reporting what is routed here; without it '
                                          'a Matrix can switch and can never show what is on)'),
                                         ('routing',
                                          "routing.planes[0]: destination_child_type 'nope' is not "
                                          'a declared child_entity_type')],
 'routing_duplicate': [('routing',
                        'routing.planes[1]: already declared by routing.planes[0] ; two planes '
                        'watching one property and routing it the same way are the same matrix '
                        'twice')],
 'routing_duplicate_no_property': [('routing',
                                    "routing.planes[0]: missing required 'route_property' (the "
                                    'property reporting what is routed here; without it a Matrix '
                                    'can switch and can never show what is on)'),
                                   ('routing',
                                    "routing.planes[1]: missing required 'route_property' (the "
                                    'property reporting what is routed here; without it a Matrix '
                                    'can switch and can never show what is on)')],
 'routing_duplicate_third': [('routing',
                              'routing.planes[2]: already declared by routing.planes[0] ; two '
                              'planes watching one property and routing it the same way are the '
                              'same matrix twice')],
 'routing_duplicate_twice': [('routing',
                              'routing.planes[1]: already declared by routing.planes[0] ; two '
                              'planes watching one property and routing it the same way are the '
                              'same matrix twice'),
                             ('routing',
                              'routing.planes[2]: already declared by routing.planes[0] ; two '
                              'planes watching one property and routing it the same way are the '
                              'same matrix twice')],
 'routing_duplicate_undeclared_dest': [('routing',
                                        "routing.planes[0]: destination_child_type 'nope' is not a "
                                        'declared child_entity_type'),
                                       ('routing',
                                        "routing.planes[1]: destination_child_type 'nope' is not a "
                                        'declared child_entity_type'),
                                       ('routing',
                                        'routing.planes[1]: already declared by routing.planes[0] '
                                        '; two planes watching one property and routing it the '
                                        'same way are the same matrix twice')],
 'routing_empty_mapping': [('routing',
                            "routing: needs a non-empty 'planes' list, the independent things this "
                            'device routes (usually one; a decoder that routes video, audio and '
                            'USB separately declares three)')],
 'routing_empty_text': [('routing', "routing: must be a mapping with a 'planes' list")],
 'routing_end_params_empty': [],
 'routing_end_params_number': [],
 'routing_ends_good': [],
 'routing_ends_on_undeclared_command': [('routing',
                                         "routing.planes[0]: command 'nope' is not a declared "
                                         "command (did you mean 'noop'?)")],
 'routing_everything_wrong': [('routing', 'routing.command: must be a non-empty string'),
                              ('routing', 'routing.destination_param: must be a non-empty string'),
                              ('routing',
                               "routing.planes[0]: missing required 'route_property' (the property "
                               'reporting what is routed here; without it a Matrix can switch and '
                               'can never show what is on)'),
                              ('routing',
                               "routing.planes[0]: destination_child_type 'outptu' is not a "
                               "declared child_entity_type (did you mean 'output'?)"),
                              ('routing',
                               "routing.planes[0]: 'params' needs a 'command' to send them on. "
                               'Declare the routing command here or on the routing block'),
                              ('routing',
                               "routing.planes[0]: 'params' must be a mapping of parameter name to "
                               'value'),
                              ('routing',
                               "routing.planes[1]: route_property 'source' is not a state variable "
                               'of this driver'),
                              ('routing',
                               "routing.planes[1]: source_child_type 'nope' is not a declared "
                               'child_entity_type'),
                              ('routing',
                               "routing.planes[1]: command 'rout' is not a declared command (did "
                               "you mean 'route'?)"),
                              ('routing',
                               "routing.planes[2]: route_property 'source' is not a state variable "
                               'of this driver'),
                              ('routing',
                               "routing.planes[2]: destination_param 'zz' is not a parameter of "
                               "command 'route'"),
                              ('routing',
                               "routing.planes[2]: params.zz is already the route's destination; a "
                               'fixed value here would overwrite what the panel routes'),
                              ('routing',
                               "routing.planes[3]: route_property 'source' is not a state variable "
                               'of this driver'),
                              ('routing',
                               "routing.planes[3]: destination_param 'zz' is not a parameter of "
                               "command 'route'"),
                              ('routing',
                               "routing.planes[3]: params.zz is already the route's destination; a "
                               'fixed value here would overwrite what the panel routes'),
                              ('routing',
                               'routing.planes[3]: already declared by routing.planes[2] ; two '
                               'planes watching one property and routing it the same way are the '
                               'same matrix twice')],
 'routing_false': [('routing', "routing: must be a mapping with a 'planes' list")],
 'routing_good': [],
 'routing_good_two': [],
 'routing_inherited_empty': [('routing',
                              'routing.destination_child_type: must be a non-empty string'),
                             ('routing', 'routing.source_child_type: must be a non-empty string'),
                             ('routing', 'routing.command: must be a non-empty string'),
                             ('routing', 'routing.destination_param: must be a non-empty string'),
                             ('routing', 'routing.source_param: must be a non-empty string'),
                             ('routing',
                              "routing.planes[0]: route_property 'source' is not a state variable "
                              'of this driver'),
                             ('routing',
                              "routing.planes[0]: names a routing parameter but no 'command'. "
                              'Declare the routing command here or on the routing block')],
 'routing_inherited_false': [('routing',
                              'routing.destination_child_type: must be a non-empty string'),
                             ('routing', 'routing.command: must be a non-empty string'),
                             ('routing',
                              "routing.planes[0]: route_property 'source' is not a state variable "
                              'of this driver')],
 'routing_inherited_good_bad_plane': [('routing',
                                       "routing: needs a non-empty 'planes' list, the independent "
                                       'things this device routes (usually one; a decoder that '
                                       'routes video, audio and USB separately declares three)')],
 'routing_inherited_numbers': [('routing',
                                'routing.destination_child_type: must be a non-empty string'),
                               ('routing', 'routing.source_child_type: must be a non-empty string'),
                               ('routing', 'routing.command: must be a non-empty string'),
                               ('routing', 'routing.destination_param: must be a non-empty string'),
                               ('routing', 'routing.source_param: must be a non-empty string'),
                               ('routing',
                                "routing.planes[0]: route_property 'source' is not a state "
                                'variable of this driver'),
                               ('routing',
                                "routing.planes[0]: names a routing parameter but no 'command'. "
                                'Declare the routing command here or on the routing block')],
 'routing_list': [('routing', "routing: must be a mapping with a 'planes' list")],
 'routing_no_property': [('routing',
                          "routing.planes[0]: missing required 'route_property' (the property "
                          'reporting what is routed here; without it a Matrix can switch and can '
                          'never show what is on)')],
 'routing_none': [],
 'routing_param_number_no_command': [('routing',
                                      'routing.planes[0]: names a routing parameter but no '
                                      "'command'. Declare the routing command here or on the "
                                      'routing block')],
 'routing_params_and_param_no_command': [('routing',
                                          "routing.planes[0]: 'params' needs a 'command' to send "
                                          'them on. Declare the routing command here or on the '
                                          'routing block')],
 'routing_params_empty_no_command': [('routing',
                                      "routing.planes[0]: 'params' needs a 'command' to send them "
                                      'on. Declare the routing command here or on the routing '
                                      'block')],
 'routing_params_good': [],
 'routing_params_list': [('routing',
                          "routing.planes[0]: 'params' must be a mapping of parameter name to "
                          'value')],
 'routing_params_no_command': [('routing',
                                "routing.planes[0]: 'params' needs a 'command' to send them on. "
                                'Declare the routing command here or on the routing block')],
 'routing_params_none_no_command': [],
 'routing_params_not_mapping': [('routing',
                                 "routing.planes[0]: 'params' must be a mapping of parameter name "
                                 'to value')],
 'routing_params_on_command_params_list': [('routing',
                                            'routing.planes[0]: params.signal is not a parameter '
                                            "of command 'route'")],
 'routing_params_on_command_without_params': [('routing',
                                               'routing.planes[0]: params.signal is not a '
                                               "parameter of command 'route'")],
 'routing_params_on_unevaluated_command': [],
 'routing_params_overwrite_dest': [('routing',
                                    "routing.planes[0]: params.output is already the route's "
                                    'destination; a fixed value here would overwrite what the '
                                    'panel routes')],
 'routing_params_overwrite_inherited_end': [('routing',
                                             'routing.planes[0]: params.input is already the '
                                             "route's source; a fixed value here would overwrite "
                                             'what the panel routes')],
 'routing_params_overwrite_source': [('routing',
                                      "routing.planes[0]: params.input is already the route's "
                                      'source; a fixed value here would overwrite what the panel '
                                      'routes')],
 'routing_params_same_end_names': [('routing',
                                    "routing.planes[0]: params.input is already the route's "
                                    'destination; a fixed value here would overwrite what the '
                                    'panel routes')],
 'routing_params_text': [('routing',
                          "routing.planes[0]: 'params' must be a mapping of parameter name to "
                          'value')],
 'routing_params_unknown': [('routing',
                             'routing.planes[0]: params.signl is not a parameter of command '
                             "'route' (did you mean 'signal'?)"),
                            ('routing',
                             'routing.planes[0]: params.zzzz is not a parameter of command '
                             "'route'")],
 'routing_plane_not_mapping': [('routing', 'routing.planes[0]: must be a mapping'),
                               ('routing', 'routing.planes[1]: must be a mapping'),
                               ('routing', 'routing.planes[2]: must be a mapping'),
                               ('routing', 'routing.planes[3]: must be a mapping')],
 'routing_planes_empty': [('routing',
                           "routing: needs a non-empty 'planes' list, the independent things this "
                           'device routes (usually one; a decoder that routes video, audio and USB '
                           'separately declares three)')],
 'routing_planes_mapping': [('routing',
                             "routing: needs a non-empty 'planes' list, the independent things "
                             'this device routes (usually one; a decoder that routes video, audio '
                             'and USB separately declares three)')],
 'routing_planes_none': [('routing',
                          "routing: needs a non-empty 'planes' list, the independent things this "
                          'device routes (usually one; a decoder that routes video, audio and USB '
                          'separately declares three)')],
 'routing_planes_text': [('routing',
                          "routing: needs a non-empty 'planes' list, the independent things this "
                          'device routes (usually one; a decoder that routes video, audio and USB '
                          'separately declares three)')],
 'routing_property_device_vars_list': [('state_variables', 'state_variables: must be a mapping'),
                                       ('routing',
                                        "routing.planes[0]: route_property 'source_video' is not a "
                                        'state variable of this driver')],
 'routing_property_device_vars_none': [('state_variables', 'state_variables: must be a mapping'),
                                       ('routing',
                                        "routing.planes[0]: route_property 'source_video' is not a "
                                        'state variable of this driver')],
 'routing_property_empty': [('routing',
                             "routing.planes[0]: missing required 'route_property' (the property "
                             'reporting what is routed here; without it a Matrix can switch and '
                             'can never show what is on)')],
 'routing_property_far': [('routing',
                           "routing.planes[0]: route_property 'zzzz' is not a state variable of "
                           "child type 'output'")],
 'routing_property_inherited_from_block_only': [('routing',
                                                 'routing.planes[0]: missing required '
                                                 "'route_property' (the property reporting what is "
                                                 'routed here; without it a Matrix can switch and '
                                                 'can never show what is on)')],
 'routing_property_list': [('routing',
                            "routing.planes[0]: missing required 'route_property' (the property "
                            'reporting what is routed here; without it a Matrix can switch and can '
                            'never show what is on)')],
 'routing_property_none': [('routing',
                            "routing.planes[0]: missing required 'route_property' (the property "
                            'reporting what is routed here; without it a Matrix can switch and can '
                            'never show what is on)')],
 'routing_property_number': [('routing',
                              "routing.planes[0]: missing required 'route_property' (the property "
                              'reporting what is routed here; without it a Matrix can switch and '
                              'can never show what is on)')],
 'routing_property_on_device': [],
 'routing_property_on_device_missing': [('routing',
                                         "routing.planes[0]: route_property 'source' is not a "
                                         'state variable of this driver')],
 'routing_property_undeclared': [('routing',
                                  "routing.planes[0]: route_property 'sorce' is not a state "
                                  "variable of child type 'output' (did you mean 'source'?)")],
 'routing_same_params_other_order': [('routing',
                                      'routing.planes[1]: already declared by routing.planes[0] ; '
                                      'two planes watching one property and routing it the same '
                                      'way are the same matrix twice')],
 'routing_same_params_text_and_number': [('routing',
                                          'routing.planes[1]: already declared by '
                                          'routing.planes[0] ; two planes watching one property '
                                          'and routing it the same way are the same matrix twice')],
 'routing_same_property_number_command': [('routing',
                                           "routing.planes[1]: command '5' is not a declared "
                                           'command'),
                                          ('routing',
                                           'routing.planes[1]: already declared by '
                                           'routing.planes[0] ; two planes watching one property '
                                           'and routing it the same way are the same matrix '
                                           'twice')],
 'routing_same_property_other_command': [],
 'routing_same_property_other_dest': [('routing',
                                       "routing.planes[1]: route_property 'source' is not a state "
                                       "variable of child type 'input'")],
 'routing_same_property_other_params': [],
 'routing_same_property_params_not_mapping': [('routing',
                                               "routing.planes[0]: 'params' must be a mapping of "
                                               'parameter name to value'),
                                              ('routing',
                                               "routing.planes[1]: 'params' must be a mapping of "
                                               'parameter name to value'),
                                              ('routing',
                                               'routing.planes[1]: already declared by '
                                               'routing.planes[0] ; two planes watching one '
                                               'property and routing it the same way are the same '
                                               'matrix twice')],
 'routing_same_property_same_params': [('routing',
                                        'routing.planes[1]: already declared by routing.planes[0] '
                                        '; two planes watching one property and routing it the '
                                        'same way are the same matrix twice')],
 'routing_source_empty': [],
 'routing_source_none': [],
 'routing_source_number': [],
 'routing_source_param_no_command': [('routing',
                                      'routing.planes[0]: names a routing parameter but no '
                                      "'command'. Declare the routing command here or on the "
                                      'routing block')],
 'routing_source_param_unknown': [('routing',
                                   "routing.planes[0]: source_param 'zzzz' is not a parameter of "
                                   "command 'route'")],
 'routing_source_undeclared': [('routing',
                                "routing.planes[0]: source_child_type 'inptu' is not a declared "
                                "child_entity_type (did you mean 'input'?)")],
 'routing_text': [('routing', "routing: must be a mapping with a 'planes' list")],
 'routing_zero': [('routing', "routing: must be a mapping with a 'planes' list")],
 'settings_empty': [],
 'settings_entry_not_mapping': [('device_settings.volume',
                                 "Device setting 'volume': must be a mapping"),
                                ('device_settings.power',
                                 "Device setting 'power': must be a mapping"),
                                ('device_settings.picture',
                                 "Device setting 'picture': must be a mapping")],
 'settings_everything_wrong': [('device_settings.volume',
                                "Device setting 'volume': unknown type 'int'"),
                               ('device_settings.volume',
                                "Device setting 'volume': state_key 'volum' is not a declared "
                                'state variable, so the setting would never read back'),
                               ('device_settings.volume',
                                "Device setting 'volume': min (9) is greater than max (1)"),
                               ('device_settings.volume',
                                "Device setting 'volume': missing 'write' block (send / path / "
                                'address); a device setting must be writable'),
                               ('device_settings.volume',
                                "Device setting 'volume': map key 'True' is not text; YAML reads a "
                                'bare true, false, on, off, yes or no as a boolean. Put it in '
                                'quotes.'),
                               ('device_settings.power',
                                "Device setting 'power': must be a mapping"),
                               ('device_settings.picture',
                                "Device setting 'picture' write: args[0] unknown OSC type 'q' "
                                '(expected one of f/i/s/h/d/T/F/N)'),
                               ('device_settings.picture',
                                "Device setting 'picture': map key 'b' is not one of the setting's "
                                'values (a)')],
 'settings_false': [('device_settings', 'device_settings: must be a mapping')],
 'settings_good': [],
 'settings_good_three': [],
 'settings_list': [('device_settings', 'device_settings: must be a mapping')],
 'settings_map_bool_key': [('device_settings.volume',
                            "Device setting 'volume': map key 'True' is not text; YAML reads a "
                            'bare true, false, on, off, yes or no as a boolean. Put it in '
                            'quotes.')],
 'settings_map_bool_key_and_word': [('device_settings.volume',
                                     "Device setting 'volume': map key 'True' is not text; YAML "
                                     'reads a bare true, false, on, off, yes or no as a boolean. '
                                     'Put it in quotes.')],
 'settings_map_bool_word': [('device_settings.volume',
                             "Device setting 'volume': map word for '1' is not text; YAML reads a "
                             'bare ON, OFF, YES or NO as a boolean. Put it in quotes.'),
                            ('device_settings.volume',
                             "Device setting 'volume': map word for '2' is not text; YAML reads a "
                             'bare ON, OFF, YES or NO as a boolean. Put it in quotes.')],
 'settings_map_boolean_bad_key_bool_word': [('device_settings.power',
                                             "Device setting 'power': map word for 'on' is not "
                                             'text; YAML reads a bare ON, OFF, YES or NO as a '
                                             'boolean. Put it in quotes.'),
                                            ('device_settings.power',
                                             "Device setting 'power': map key 'maybe' is not a "
                                             'value a boolean setting takes; use "true" and '
                                             '"false"')],
 'settings_map_boolean_bad_keys': [('device_settings.power',
                                    "Device setting 'power': map key 'on' is not a value a boolean "
                                    'setting takes; use "true" and "false"'),
                                   ('device_settings.power',
                                    "Device setting 'power': map key 'yes' is not a value a "
                                    'boolean setting takes; use "true" and "false"'),
                                   ('device_settings.power',
                                    "Device setting 'power': map key '1' is not a value a boolean "
                                    'setting takes; use "true" and "false"')],
 'settings_map_boolean_good': [],
 'settings_map_boolean_spaced': [],
 'settings_map_empty': [('device_settings.volume',
                         "Device setting 'volume': map must be a non-empty mapping of setting "
                         'value -> device word')],
 'settings_map_enum_bad_key': [('device_settings.picture',
                                "Device setting 'picture': map key 'vivd' is not one of the "
                                "setting's values (vivid, cinema)")],
 'settings_map_enum_empty_values': [('device_settings.picture',
                                     "Device setting 'picture': map key 'x' is not one of the "
                                     "setting's values ()")],
 'settings_map_enum_good': [],
 'settings_map_enum_no_values': [],
 'settings_map_enum_number_keys': [('device_settings.picture',
                                    "Device setting 'picture': map key '3' is not one of the "
                                    "setting's values (1, 2)")],
 'settings_map_enum_object_values': [('device_settings.picture',
                                      "Device setting 'picture': map key 'no value' is not one of "
                                      "the setting's values (vivid, 3)")],
 'settings_map_enum_values_text': [],
 'settings_map_enum_values_without_value': [('device_settings.picture',
                                             "Device setting 'picture': map key 'x' is not one of "
                                             "the setting's values ()")],
 'settings_map_false_key': [('device_settings.volume',
                             "Device setting 'volume': map key 'False' is not text; YAML reads a "
                             'bare true, false, on, off, yes or no as a boolean. Put it in '
                             'quotes.')],
 'settings_map_good': [],
 'settings_map_list': [('device_settings.volume',
                        "Device setting 'volume': map must be a non-empty mapping of setting value "
                        '-> device word')],
 'settings_map_none': [],
 'settings_map_not_scalar': [('device_settings.volume',
                              "Device setting 'volume': map entries must be setting value -> "
                              'device word pairs of text or numbers'),
                             ('device_settings.volume',
                              "Device setting 'volume': map entries must be setting value -> "
                              'device word pairs of text or numbers'),
                             ('device_settings.volume',
                              "Device setting 'volume': map entries must be setting value -> "
                              'device word pairs of text or numbers')],
 'settings_map_on_string': [],
 'settings_map_text': [('device_settings.volume',
                        "Device setting 'volume': map must be a non-empty mapping of setting value "
                        '-> device word')],
 'settings_map_type_missing': [],
 'settings_map_values_on_integer': [],
 'settings_max_bool': [],
 'settings_min_bool': [],
 'settings_min_equals_max': [],
 'settings_min_inf': [('device_settings.volume',
                       "Device setting 'volume': min (inf) is greater than max (1)")],
 'settings_min_nan': [],
 'settings_min_only': [],
 'settings_min_over_max': [('device_settings.volume',
                            "Device setting 'volume': min (10) is greater than max (1)")],
 'settings_min_over_max_float': [('device_settings.volume',
                                  "Device setting 'volume': min (1.5) is greater than max (1.25)")],
 'settings_min_over_max_mixed': [('device_settings.volume',
                                  "Device setting 'volume': min (2) is greater than max (1.5)")],
 'settings_min_text': [],
 'settings_min_under_max': [],
 'settings_no_write': [('device_settings.volume',
                        "Device setting 'volume': missing 'write' block (send / path / address); a "
                        'device setting must be writable')],
 'settings_none': [],
 'settings_state_key_declared': [],
 'settings_state_key_default_undeclared': [('device_settings.volumes',
                                            "Device setting 'volumes': state_key 'volumes' is not "
                                            'a declared state variable, so the setting would never '
                                            'read back')],
 'settings_state_key_empty': [('device_settings.volume',
                               "Device setting 'volume': state_key '' is not a declared state "
                               'variable, so the setting would never read back')],
 'settings_state_key_none': [],
 'settings_state_key_number': [],
 'settings_state_key_undeclared': [('device_settings.volume',
                                    "Device setting 'volume': state_key 'volum' is not a declared "
                                    'state variable, so the setting would never read back')],
 'settings_state_vars_list': [('device_settings.volume',
                               "Device setting 'volume': state_key 'volume' is not a declared "
                               'state variable, so the setting would never read back'),
                              ('state_variables', 'state_variables: must be a mapping')],
 'settings_state_vars_none': [('device_settings.volume',
                               "Device setting 'volume': state_key 'volume' is not a declared "
                               'state variable, so the setting would never read back'),
                              ('state_variables', 'state_variables: must be a mapping')],
 'settings_state_vars_unevaluated': [('state_variables.<unevaluated>',
                                      "State variable '<unevaluated>': must be a dict")],
 'settings_text': [('device_settings', 'device_settings: must be a mapping')],
 'settings_type_cased': [('device_settings.volume',
                          "Device setting 'volume': unknown type 'Integer'")],
 'settings_type_empty': [],
 'settings_type_false': [],
 'settings_type_missing': [],
 'settings_type_none': [],
 'settings_type_number': [('device_settings.volume', "Device setting 'volume': unknown type '5'")],
 'settings_type_unknown': [('device_settings.volume',
                            "Device setting 'volume': unknown type 'int'")],
 'settings_write_empty': [],
 'settings_write_list': [('device_settings.volume',
                          "Device setting 'volume': missing 'write' block (send / path / address); "
                          'a device setting must be writable')],
 'settings_write_none': [('device_settings.volume',
                          "Device setting 'volume': missing 'write' block (send / path / address); "
                          'a device setting must be writable')],
 'settings_write_osc_address_empty': [('device_settings.volume',
                                       "Device setting 'volume' write: args[0] unknown OSC type "
                                       "'q' (expected one of f/i/s/h/d/T/F/N)")],
 'settings_write_osc_address_none': [],
 'settings_write_osc_args_text': [('device_settings.volume',
                                   "Device setting 'volume' write: args must be a list")],
 'settings_write_osc_bad_tag': [('device_settings.volume',
                                 "Device setting 'volume' write: args[0] unknown OSC type 'q' "
                                 '(expected one of f/i/s/h/d/T/F/N)')],
 'settings_write_osc_good': [],
 'settings_write_osc_no_args': [],
 'settings_write_text': [('device_settings.volume',
                          "Device setting 'volume': missing 'write' block (send / path / address); "
                          'a device setting must be writable')],
 'settings_zero': [('device_settings', 'device_settings: must be a mapping')]}

PY_EXPECTED: dict[str, list[str]] = {'py_good': [],
 'py_learned': ["Config field 'zone': learned_from 'zones' is not a declared state variable, so "
                'the field would never be filled in',
                "Config field 'host': a secret field cannot be learned_from a state variable, "
                'because the value would have to be shown as state first',
                "Config field 'host': a table field cannot be learned_from a state variable, "
                'because it has no single value to fill in',
                "Config field 'host': a connection field cannot be learned_from a state variable, "
                'because a reported value would change how the device is reached'],
 'py_learned_schema_list': [],
 'py_order': ["routing: needs a non-empty 'planes' list, the independent things this device routes "
              '(usually one; a decoder that routes video, audio and USB separately declares three)',
              "Config field 'zone': learned_from 'zones' is not a declared state variable, so the "
              'field would never be filled in',
              "Device setting 'volumes': state_key 'volumes' is not a declared state variable, so "
              'the setting would never read back'],
 'py_routing_bad': ["routing.planes[0]: route_property 'sorce' is not a state variable of child "
                    "type 'output' (did you mean 'source'?)",
                    "routing.planes[0]: destination_param 'x' is not a parameter of command "
                    "'route'",
                    "routing.planes[0]: params.q is not a parameter of command 'route'",
                    "routing.planes[1]: route_property 'sorce' is not a state variable of child "
                    "type 'output' (did you mean 'source'?)"],
 'py_routing_commands_empty': [],
 'py_routing_commands_unevaluated': [],
 'py_routing_dest_vars_unevaluated': [],
 'py_routing_device_itself': ["routing.planes[0]: route_property 'sorce_video' is not a state "
                              "variable of this driver (did you mean 'source_video'?)"],
 'py_routing_good': [],
 'py_routing_not_mapping': ["routing: must be a mapping with a 'planes' list"],
 'py_settings_entry_not_mapping': ["device_settings['volume'] must be a mapping"],
 'py_settings_good': [],
 'py_settings_map': ["Device setting 'volume': remove map; the platform maps a setting's value "
                     'only for YAML drivers, and a Python driver writes its settings in its own '
                     'code'],
 'py_settings_not_mapping': ['device_settings must be a mapping'],
 'py_settings_type_and_bounds_unjudged': [],
 'py_settings_undeclared': ["Device setting 'volumes': state_key 'volumes' is not a declared state "
                            'variable, so the setting would never read back',
                            "Device setting 'level': state_key 'lvl' is not a declared state "
                            'variable, so the setting would never read back'],
 'py_settings_vars_unevaluated': []}

DIRECT_EXPECTED: dict[str, list[str]] = {'learned_absent': [],
 'learned_all': ["Config field 'port': a secret field cannot be learned_from a state variable, "
                 'because the value would have to be shown as state first',
                 "Config field 'port': a table field cannot be learned_from a state variable, "
                 'because it has no single value to fill in',
                 "Config field 'port': a connection field cannot be learned_from a state variable, "
                 'because a reported value would change how the device is reached',
                 "Config field 'port': learned_from 'a' is not a declared state variable, so the "
                 'field would never be filled in'],
 'learned_none': [],
 'learned_vars_not_mapping': [],
 'learned_vars_unevaluated': [],
 'map_absent': [],
 'map_on_boolean': ['W: map key \'no\' is not a value a boolean setting takes; use "true" and '
                    '"false"'],
 'map_on_enum': ["W: map key 'c' is not one of the setting's values (a, 2)"],
 'routing_absent': [],
 'routing_against_nothing': ["routing.planes[0]: destination_child_type 'out' is not a declared "
                             'child_entity_type',
                             "routing.planes[1]: destination_child_type 'out' is not a declared "
                             'child_entity_type',
                             'routing.planes[1]: already declared by routing.planes[0] ; two '
                             'planes watching one property and routing it the same way are the '
                             'same matrix twice'],
 'routing_alone': ["routing.planes[0]: route_property 'source' is not a state variable of this "
                   'driver',
                   "routing.planes[0]: command 'x' is not a declared command"],
 'routing_false': ["routing: must be a mapping with a 'planes' list"],
 'routing_none': [],
 'state_key_declared': [],
 'state_key_given': [],
 'state_key_not_text': [],
 'state_key_undeclared': ["W: state_key 'volume' is not a declared state variable, so the setting "
                          'would never read back'],
 'state_key_vars_not_mapping': [],
 'state_key_vars_unevaluated': []}


def _definition(case: str) -> dict[str, Any]:
    merged = {**BASE, **CASES[case]}
    return {key: value for key, value in merged.items() if value is not DROP}


def _python_info(case: str) -> dict[str, Any]:
    return {**PY_BASE, **PY_CASES[case]}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    strict = case in STRICT
    issues = validate_driver_issues(definition, strict=strict)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition, strict=strict)) == [m for _, m in got]


@pytest.mark.parametrize("case", sorted(RAISES))
def test_shapes_that_raise(case: str) -> None:
    definition = {**BASE, **RAISING_CASES[case]}
    with pytest.raises(RAISES[case]):
        validate_driver_definition(definition)
    with pytest.raises(RAISES[case]):
        routing_block_errors(definition)


@pytest.mark.parametrize("case", sorted(WALK_RAISES))
def test_settings_that_raise(case: str) -> None:
    with pytest.raises(WALK_RAISES[case]):
        validate_driver_definition({**BASE, **WALK_RAISING_CASES[case]})


@pytest.mark.parametrize("case", sorted(PY_EXPECTED))
def test_the_python_surface(case: str) -> None:
    assert python_driver_info_issues(_python_info(case)) == PY_EXPECTED[case]


@pytest.mark.parametrize("case", sorted(DIRECT_EXPECTED))
def test_each_rule_asked_directly(case: str) -> None:
    assert DIRECT[case]() == DIRECT_EXPECTED[case]


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED)
    assert set(PY_CASES) == set(PY_EXPECTED)
    assert set(DIRECT) == set(DIRECT_EXPECTED)
    assert set(RAISES) == set(RAISING_CASES)
    assert set(WALK_RAISES) == set(WALK_RAISING_CASES)
