"""Pins the exact error messages of every driver-validation rejection rule.

One synthetic invalid definition per rule in ``validate_driver_definition``
(and the actions/params/push/auth/liveness/child blocks it dispatches to).
The expected output lives in ``tests/fixtures/driver_validation_messages.json``
and is compared byte-for-byte, so any change to a rule's wording, ordering,
or trigger condition shows up as a reviewable diff instead of drifting
silently — the validator's messages are part of the authoring contract
(the Driver Builder, the community catalog CI, and the docs all surface
them).

The case inputs themselves are exported to
``tests/fixtures/driver_validation_cases.json`` so consumers of the shared
rules module — the community driver catalog vendors a copy — can replay the
identical corpus against their copy and prove verdict parity byte-for-byte.

To regenerate both fixtures after an intentional rule or message change:

    python tests/test_driver_validation_messages.py --regen
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Import lazily and pin this checkout's root first: run as a script, the
# checkout containing this file must win over any installed `server`
# package, or --regen captures another tree's messages.
_ROOT = Path(__file__).resolve().parents[1]


def _validate(driver_def: Any) -> list[str]:
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from server.drivers.driver_loader import validate_driver_definition

    return validate_driver_definition(driver_def)


FIXTURE = Path(__file__).parent / "fixtures" / "driver_validation_messages.json"
CASES_FIXTURE = Path(__file__).parent / "fixtures" / "driver_validation_cases.json"


def _d(**over: Any) -> dict[str, Any]:
    """A minimal valid definition with per-case overrides."""
    base: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"noop": {"send": "NOOP\r"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
    }
    base.update(over)
    return base


_CHILD_TYPES: dict[str, Any] = {
    "zone": {
        "id_format": {"type": "integer", "min": 1, "max": 8},
        "state_variables": {"level": {"type": "number", "label": "Level"}},
        "instances": {"count": 4},
    }
}


def _resp(**over: Any) -> dict[str, Any]:
    """A definition with one response entry."""
    return _d(responses=[over])


def _child_resp(**entry: Any) -> dict[str, Any]:
    """A definition with a child_set response against declared child types."""
    return _d(
        child_entity_types=dict(_CHILD_TYPES),
        responses=[{"match": r"ZONE(\d) LVL (\d+)", "child_set": [entry]}],
    )


def _osc_child_resp(**entry: Any) -> dict[str, Any]:
    return _d(
        transport="osc",
        child_entity_types=dict(_CHILD_TYPES),
        responses=[{"address": "/zone/*/level", "child_set": [entry]}],
    )


def _param(**pdef: Any) -> dict[str, Any]:
    """A definition whose command declares one 'level' param."""
    return _d(
        commands={"set_level": {"send": "LVL {level}\r", "params": {"level": pdef}}}
    )


def _setting(**sdef: Any) -> dict[str, Any]:
    body = {"type": "integer", "state_key": "power", "write": {"send": "SET {value}\r"}}
    body.update(sdef)
    return _d(device_settings={"knob": body})


def _push(transport: str = "tcp", **push: Any) -> dict[str, Any]:
    return _d(transport=transport, push=push)


def _child_type(**tdef: Any) -> dict[str, Any]:
    return _d(child_entity_types={"zone": tdef})


def _instances(idef: Any, **tdef_extra: Any) -> dict[str, Any]:
    tdef: dict[str, Any] = {
        "id_format": {"type": "integer", "min": 1, "max": 8},
        "state_variables": {"level": {"type": "number", "label": "Level"}},
        "instances": idef,
    }
    tdef.update(tdef_extra)
    return _d(child_entity_types={"zone": tdef})


# One case per rejection rule: name -> definition (or non-mapping payload).
CASES: dict[str, Any] = {
    # --- top level ---
    "not_a_mapping": ["not", "a", "dict"],
    "missing_required_fields": {"description": "nothing declared"},
    "unsupported_transport": _d(transport="carrier_pigeon"),
    "ir_codes_not_bool": _d(ir_codes="yes"),
    # --- responses ---
    "responses_not_list": _d(responses={"a": 1}),
    "response_not_mapping": _d(responses=["PWR=1"]),
    "response_throttle_zero": _resp(match="OK", throttle=0),
    "response_throttle_bool": _resp(match="OK", throttle=True),
    "response_require_without_json": _resp(match="OK", require="power"),
    "response_require_empty_string": _resp(json=True, set={"power": "$.p"}, require=" "),
    "response_require_bad_list_entry": _resp(json=True, set={"power": "$.p"}, require=[""]),
    "response_require_wrong_type": _resp(json=True, set={"power": "$.p"}, require=5),
    "response_osc_address_no_slash": _d(transport="osc", responses=[{"address": "zone"}]),
    "response_json_with_child_set": _resp(json=True, set={"power": "$.p"}, child_set=[{}]),
    "response_json_without_set_or_mappings": _resp(json=True),
    "response_missing_pattern": _resp(set={"power": "$1"}),
    "response_redos_pattern": _resp(match="(a+)+$"),
    # --- byte-stream child_set ---
    "child_set_not_list": _d(
        child_entity_types=dict(_CHILD_TYPES),
        responses=[{"match": r"Z(\d)", "child_set": {}}],
    ),
    "child_set_entry_not_mapping": _d(
        child_entity_types=dict(_CHILD_TYPES),
        responses=[{"match": r"Z(\d)", "child_set": ["zone"]}],
    ),
    "child_set_unknown_type": _child_resp(type="relay", id="$1", state={"level": "$2"}),
    "child_set_missing_id": _child_resp(type="zone", state={"level": "$2"}),
    "child_set_id_group_invalid": _child_resp(type="zone", id={"group": True}, state={"level": "$2"}),
    "child_set_id_group_zero": _child_resp(type="zone", id={"group": 0}, state={"level": "$2"}),
    "child_set_id_group_exceeds": _child_resp(type="zone", id={"group": 9}, state={"level": "$2"}),
    "child_set_id_map_empty": _child_resp(type="zone", id={"group": 1, "map": {}}, state={"level": "$2"}),
    "child_set_id_map_bad_pairs": _child_resp(type="zone", id={"group": 1, "map": {"1": True}}, state={"level": "$2"}),
    "child_set_id_map_non_integer_local": _child_resp(type="zone", id={"group": 1, "map": {"A": "left"}}, state={"level": "$2"}),
    "child_set_id_ref_below_one": _child_resp(type="zone", id="$0", state={"level": "$2"}),
    "child_set_id_ref_exceeds": _child_resp(type="zone", id="$9", state={"level": "$2"}),
    "child_set_id_ref_not_numeric": _child_resp(type="zone", id="$x", state={"level": "$2"}),
    "child_set_missing_state": _child_resp(type="zone", id="$1"),
    "child_set_state_prop_undeclared": _child_resp(type="zone", id="$1", state={"gain": "$2"}),
    "child_set_state_ref_exceeds": _child_resp(type="zone", id="$1", state={"level": "$9"}),
    # --- OSC child_set ---
    "osc_child_set_not_list": _d(
        transport="osc",
        child_entity_types=dict(_CHILD_TYPES),
        responses=[{"address": "/zone/*/level", "child_set": {}}],
    ),
    "osc_child_set_entry_not_mapping": _d(
        transport="osc",
        child_entity_types=dict(_CHILD_TYPES),
        responses=[{"address": "/zone/*/level", "child_set": ["zone"]}],
    ),
    "osc_child_set_unknown_type": _osc_child_resp(type="relay", id={"segment": 1}, state={"level": {"arg": 0}}),
    "osc_child_set_missing_id": _osc_child_resp(type="zone", state={"level": {"arg": 0}}),
    "osc_child_set_id_segment_not_int": _osc_child_resp(type="zone", id={"segment": "one"}, state={"level": {"arg": 0}}),
    "osc_child_set_id_segment_negative": _osc_child_resp(type="zone", id={"segment": -1}, state={"level": {"arg": 0}}),
    "osc_child_set_id_segment_past_end": _osc_child_resp(type="zone", id={"segment": 7}, state={"level": {"arg": 0}}),
    "osc_child_set_id_map_empty": _osc_child_resp(type="zone", id={"segment": 1, "map": {}}, state={"level": {"arg": 0}}),
    "osc_child_set_id_map_bad_pairs": _osc_child_resp(type="zone", id={"segment": 1, "map": {"1": True}}, state={"level": {"arg": 0}}),
    "osc_child_set_id_map_non_integer_local": _osc_child_resp(type="zone", id={"segment": 1, "map": {"A": "left"}}, state={"level": {"arg": 0}}),
    "osc_child_set_id_capture_ref": _osc_child_resp(type="zone", id="$1", state={"level": {"arg": 0}}),
    "osc_child_set_missing_state": _osc_child_resp(type="zone", id={"segment": 1}),
    "osc_child_set_state_prop_undeclared": _osc_child_resp(type="zone", id={"segment": 1}, state={"gain": {"arg": 0}}),
    "osc_child_set_state_capture_ref": _osc_child_resp(type="zone", id={"segment": 1}, state={"level": "$1"}),
    "osc_child_set_state_missing_arg_value": _osc_child_resp(type="zone", id={"segment": 1}, state={"level": {"format": "db"}}),
    "osc_child_set_state_arg_negative": _osc_child_resp(type="zone", id={"segment": 1}, state={"level": {"arg": -2}}),
    # --- commands ---
    "commands_not_mapping": _d(commands=["noop"]),
    "command_not_dict": _d(commands={"noop": "NOOP\r"}),
    "command_without_send_path_address": _d(commands={"noop": {"label": "No-op"}}),
    "command_osc_args_not_list": _d(
        transport="osc", commands={"beep": {"address": "/beep", "args": "f"}}
    ),
    "command_osc_arg_not_mapping": _d(
        transport="osc", commands={"beep": {"address": "/beep", "args": ["f"]}}
    ),
    "command_osc_arg_unknown_type": _d(
        transport="osc", commands={"beep": {"address": "/beep", "args": [{"type": "z"}]}}
    ),
    # --- declared command semantics (sets / query_for) ---
    "command_sets_not_mapping": _d(commands={"go": {"send": "GO", "sets": ["power"]}}),
    "command_sets_undeclared_var": _d(
        commands={"go": {"send": "GO", "sets": {"ghost": True}}}
    ),
    "command_sets_undeclared_param_ref": _d(
        commands={"go": {
            "send": "GO {n}",
            "params": {"n": {"type": "integer"}},
            "sets": {"power": "{ghost}"},
        }}
    ),
    "command_sets_partial_brace_value": _d(
        commands={"go": {
            "send": "GO {n}",
            "params": {"n": {"type": "integer"}},
            "sets": {"power": "on-{n}"},
        }}
    ),
    "command_query_for_empty": _d(commands={"chk": {"send": "S?", "query_for": ""}}),
    "command_query_for_undeclared_var": _d(
        commands={"chk": {"send": "S?", "query_for": "ghost"}}
    ),
    "query_entry_query_for_empty": _d(
        polling={"queries": [{"send": "I", "query_for": ""}]}
    ),
    "query_entry_query_for_undeclared_var": _d(
        polling={"queries": [{"send": "I", "query_for": "ghost"}]}
    ),
    "query_entry_query_for_on_each_child": _d(
        child_entity_types=dict(_CHILD_TYPES),
        polling={"queries": [
            {"each_child": "zone", "send": "Z{child_id}?", "query_for": "power"},
        ]},
    ),
    # --- command params (pickers / free-text aids) ---
    "param_pattern_redos": _param(type="string", pattern="(a+)+$"),
    "param_min_not_number": _param(type="number", min="low"),
    "param_min_greater_than_max": _param(type="number", min=10, max=1),
    "param_decimals_negative": _param(type="number", decimals=-1),
    "param_trim_not_bool": _param(type="string", trim="yes"),
    "param_map_empty": _param(type="string", map={}),
    "param_map_bad_pairs": _param(type="string", map={"a": True}),
    "param_options_state_empty": _param(type="string", options_state=""),
    "param_options_from_not_mapping": _param(type="string", options_from="other"),
    "param_options_from_bad_source": _param(type="string", options_from={"source": "moon", "param": "level"}),
    "param_options_from_param_missing": _param(type="string", options_from={"source": "child_schema"}),
    "param_options_from_param_not_sibling": _param(type="string", options_from={"source": "child_schema", "param": "ghost"}),
    "param_options_from_sibling_not_child_id": _d(
        commands={"set": {"send": "S {a} {b}\r", "params": {
            "a": {"type": "string"},
            "b": {"type": "string", "options_from": {"source": "child_schema", "param": "a"}},
        }}}
    ),
    "param_type_from_not_mapping": _param(type="string", type_from="other"),
    "param_type_from_param_missing": _param(type="string", type_from={}),
    "param_type_from_param_not_sibling": _param(type="string", type_from={"param": "ghost"}),
    "param_type_from_sibling_not_cascade": _d(
        commands={"set": {"send": "S {a} {b}\r", "params": {
            "a": {"type": "string"},
            "b": {"type": "string", "type_from": {"param": "a"}},
        }}}
    ),
    # --- send-side framing strings ---
    "command_prefix_not_string": _d(command_prefix=2),
    "command_suffix_not_string": _d(command_suffix=["\r"]),
    # --- device settings ---
    "device_settings_not_mapping": _d(device_settings=["knob"]),
    "device_setting_not_mapping": _d(device_settings={"knob": "integer"}),
    "device_setting_unknown_type": _setting(type="knob"),
    "device_setting_state_key_undeclared": _setting(state_key="ghost"),
    "device_setting_min_greater_than_max": _setting(min=10, max=1),
    "device_setting_missing_write": _d(device_settings={"knob": {"type": "integer", "state_key": "power"}}),
    "device_setting_osc_write_bad_args": _d(
        transport="osc",
        device_settings={"knob": {
            "type": "integer", "state_key": "power",
            "write": {"address": "/knob", "args": [{"type": "z"}]},
        }},
    ),
    # --- discovery (validated through the hints parser) ---
    "discovery_invalid_block": _d(discovery={"tcp_probe": {"port": "not-a-port"}}),
    # --- push ---
    "push_not_mapping": _d(push=["multicast"]),
    "push_unknown_type": _push(type="carrier_pigeon"),
    "push_unknown_keys": _push(type="multicast", group="239.1.1.1", port=5000, banana=1),
    "push_template_no_token": _push(type="multicast", group="{-}", port=5000),
    "push_template_undeclared_field": _push(type="multicast", group="{mcast_group}", port=5000),
    "push_multicast_missing_group": _push(type="multicast", port=5000),
    "push_multicast_bad_group": _push(type="multicast", group="10.0.0.1", port=5000),
    "push_multicast_missing_port": _push(type="multicast", group="239.1.1.1"),
    "push_multicast_bad_port": _push(type="multicast", group="239.1.1.1", port=0),
    "push_sse_wrong_transport": _push(type="sse", path="/events"),
    "push_sse_missing_path": _push(transport="http", type="sse"),
    "push_sse_path_wrong_type": _push(transport="http", type="sse", path=5),
    "push_sse_path_entry_empty": _push(transport="http", type="sse", path=[" "]),
    "push_sse_path_no_slash": _push(transport="http", type="sse", path="events"),
    "push_sse_idle_timeout_bad": _push(transport="http", type="sse", path="/events", idle_timeout=0),
    "push_tcp_listener_missing_port": _push(type="tcp_listener"),
    "push_tcp_listener_bad_port": _push(type="tcp_listener", port=70000),
    "push_frame_parser_not_mapping": _push(type="tcp_listener", port=0, frame_parser="struct_frame"),
    "push_frame_parser_unknown_type": _push(type="tcp_listener", port=0, frame_parser={"type": "delimiter"}),
    "push_struct_frame_reserve_negative": _push(
        type="tcp_listener", port=0,
        frame_parser={"type": "struct_frame", "header_reserve": -1},
    ),
    "push_struct_frame_bad_length_size": _push(
        type="tcp_listener", port=0,
        frame_parser={"type": "struct_frame", "length_size": 3},
    ),
    "push_struct_frame_bad_length_adjust": _push(
        type="tcp_listener", port=0,
        frame_parser={"type": "struct_frame", "length_adjust": "two"},
    ),
    "push_struct_frame_bad_endian": _push(
        type="tcp_listener", port=0,
        frame_parser={"type": "struct_frame", "length_endian": "mid"},
    ),
    "push_register_not_a_name": _push(type="tcp_listener", port=0, register=5),
    "push_register_unknown_command": _push(type="tcp_listener", port=0, register="arm"),
    # --- auth ---
    "auth_not_mapping": _d(auth="telnet_login"),
    "auth_unsupported_type": _d(auth={"type": "oauth", "username_prompt": "U:", "password_prompt": "P:"}),
    "auth_wrong_transport": _d(transport="udp", auth={"username_prompt": "U:", "password_prompt": "P:"}),
    "auth_missing_prompts": _d(auth={"type": "telnet_login"}),
    "auth_redos_pattern": _d(auth={"username_prompt": "U:", "password_prompt": "P:", "success_pattern": "(a+)+$"}),
    # --- liveness ---
    "liveness_not_mapping": _d(liveness="ping"),
    "liveness_wrong_transport": _d(transport="http", liveness={"send": "PING\r"}),
    "liveness_missing_send": _d(liveness={"interval": 10}),
    "liveness_expect_not_string": _d(liveness={"send": "PING\r", "expect": 5}),
    "liveness_expect_redos": _d(liveness={"send": "PING\r", "expect": "(a+)+$"}),
    "liveness_interval_too_small": _d(liveness={"send": "PING\r", "interval": 0.5}),
    "liveness_timeout_too_small": _d(liveness={"send": "PING\r", "timeout": 0.05}),
    "liveness_max_failures_zero": _d(liveness={"send": "PING\r", "max_failures": 0}),
    "liveness_args_on_tcp": _d(liveness={"send": "PING\r", "args": []}),
    "liveness_args_not_list": _d(transport="osc", liveness={"send": "/ping", "args": "f"}),
    # --- actions / quick_actions ---
    "quick_actions_not_list": _d(quick_actions="noop"),
    "quick_action_entry_empty": _d(quick_actions=[""]),
    "quick_action_unknown_command": _d(quick_actions=["ghost"]),
    "actions_not_list": _d(actions={"id": "noop"}),
    "action_not_mapping": _d(actions=["noop"]),
    "action_missing_id": _d(actions=[{"kind": "command"}]),
    "action_duplicate_id": _d(actions=[{"id": "noop"}, {"id": "noop"}]),
    "action_unknown_kind": _d(actions=[{"id": "noop", "kind": "wizardry"}]),
    "action_label_not_string": _d(actions=[{"id": "noop", "label": 5}]),
    "action_icon_not_string": _d(actions=[{"id": "noop", "icon": 5}]),
    "action_bad_availability": _d(actions=[{"id": "noop", "availability": "sometimes"}]),
    "action_confirm_bad_type": _d(actions=[{"id": "noop", "confirm": 5}]),
    "action_params_not_mapping": _d(actions=[{"id": "noop", "params": ["level"]}]),
    "action_url_on_command_kind": _d(actions=[{"id": "noop", "url": "http://x"}]),
    "action_link_url_empty": _d(actions=[{"id": "web", "kind": "link", "url": ""}]),
    "action_visible_when_not_mapping": _d(actions=[{"id": "noop", "visible_when": "power"}]),
    "action_visible_when_any_empty": _d(actions=[{"id": "noop", "visible_when": {"any": []}}]),
    "action_visible_when_missing_key": _d(actions=[{"id": "noop", "visible_when": {"operator": "eq"}}]),
    "action_visible_when_unknown_operator": _d(actions=[{"id": "noop", "visible_when": {"key": "device.x.power", "operator": "resembles"}}]),
    "action_command_undeclared": _d(actions=[{"id": "ghost"}]),
    "action_setup_kind_in_yaml": _d(actions=[{"id": "provision", "kind": "setup"}]),
    "action_param_pattern_redos": _d(actions=[{"id": "noop", "params": {"level": {"pattern": "(a+)+$"}}}]),
    # --- state variables ---
    "state_variables_not_mapping": _d(state_variables=["power"]),
    "state_variable_not_dict": _d(state_variables={"power": "string"}),
    "state_variable_unknown_type": _d(state_variables={"power": {"type": "voltage", "label": "Power"}}),
    "state_variable_missing_label": _d(state_variables={"power": {"type": "string"}}),
    "state_variable_unit_not_string": _d(state_variables={"power": {"type": "string", "label": "Power", "unit": 5}}),
    "state_variable_control_not_bool": _d(state_variables={"power": {"type": "string", "label": "Power", "control": "yes"}}),
    "state_variable_bad_cloud_priority": _d(state_variables={"power": {"type": "string", "label": "Power", "cloud_priority": "urgent"}}),
    # --- frame_parser ---
    "frame_parser_not_mapping": _d(frame_parser="length_prefix"),
    "frame_parser_bad_header_size": _d(frame_parser={"type": "length_prefix", "header_size": 3}),
    "frame_parser_header_offset_not_int": _d(frame_parser={"type": "length_prefix", "header_offset": "two"}),
    "frame_parser_length_offset_negative": _d(frame_parser={"type": "length_prefix", "length_offset": -1}),
    "frame_parser_bad_endian": _d(frame_parser={"type": "length_prefix", "length_endian": "mid"}),
    "frame_parser_fixed_bad_length": _d(frame_parser={"type": "fixed_length", "length": 0}),
    "frame_parser_unknown_type": _d(frame_parser={"type": "crc16"}),
    "frame_parser_missing_type": _d(frame_parser={"header_size": 2}),
    # --- send_frame ---
    "send_frame_not_mapping": _d(send_frame="length_prefix"),
    "send_frame_unknown_type": _d(send_frame={"type": "delimiter"}),
    "send_frame_bad_length_size": _d(send_frame={"length_size": 0}),
    "send_frame_bad_endian": _d(send_frame={"length_endian": "mid"}),
    "send_frame_header_not_string": _d(send_frame={"header": 5}),
    # --- child_entity_types ---
    "child_entity_types_not_mapping": _d(child_entity_types=["zone"]),
    "child_type_name_empty": _d(child_entity_types={"": {}}),
    "child_type_name_with_dot": _d(child_entity_types={"zone.a": {}}),
    "child_type_name_with_glob": _d(child_entity_types={"zone*": {}}),
    "child_type_def_not_mapping": _d(child_entity_types={"zone": "outputs"}),
    "child_id_format_not_mapping": _child_type(id_format="integer"),
    "child_id_format_unknown_type": _child_type(id_format={"type": "uuid"}),
    "child_id_format_min_not_int": _child_type(id_format={"min": "one"}),
    "child_id_format_min_greater_than_max": _child_type(id_format={"min": 8, "max": 1}),
    "child_id_format_pad_width_zero": _child_type(id_format={"pad_width": 0}),
    "child_state_variables_not_mapping": _child_type(state_variables=["level"]),
    "child_state_variable_not_mapping": _child_type(state_variables={"level": "number"}),
    "child_state_variable_unknown_type": _child_type(state_variables={"level": {"type": "voltage"}}),
    "child_state_variable_bad_cloud_priority": _child_type(state_variables={"level": {"type": "number", "cloud_priority": "urgent"}}),
    "child_state_variable_unit_not_string": _child_type(state_variables={"level": {"type": "number", "unit": 5}}),
    "child_state_variable_control_not_bool": _child_type(state_variables={"level": {"type": "number", "control": "yes"}}),
    # --- child instances roster ---
    "instances_not_mapping": _instances([4]),
    "instances_count_from_state_not_string": _instances({"count": 4, "count_from_state": 5}),
    "instances_count_from_state_undeclared": _instances({"count": 4, "count_from_state": "zones"}),
    "instances_no_source": _instances({}),
    "instances_two_sources": _instances({"count": 4, "ids": [1, 2]}),
    "instances_ids_empty": _instances({"ids": []}),
    "instances_ids_non_scalar": _instances({"ids": [[1]]}),
    "instances_ids_non_integer": _instances({"ids": ["left"]}),
    "instances_count_zero": _instances({"count": 0}),
    "instances_count_exceeds_max": _instances({"count": 99}),
    "instances_count_with_string_ids": _instances(
        {"count": 4}, id_format={"type": "string"}
    ),
    "instances_count_from_not_string": _instances({"count_from": 5}),
    "instances_count_from_undeclared_field": _instances({"count_from": "zone_count"}),
    "instances_count_from_with_string_ids": _d(
        default_config={"zone_count": 4},
        child_entity_types={"zone": {
            "id_format": {"type": "string"},
            "state_variables": {"level": {"type": "number", "label": "Level"}},
            "instances": {"count_from": "zone_count"},
        }},
    ),
    "instances_label_not_string": _instances({"count": 4, "label": 5}),
    # --- polling / each_child queries ---
    "polling_not_mapping": _d(polling=["power?"]),
    "polling_interval_inert": _d(polling={"interval": 5, "queries": ["PWR?\r"]}),
    "polling_query_bad_mapping": _d(polling={"queries": [{"foo": 1}]}),
    "polling_when_not_string": _d(polling={"queries": [{"send": "PWR?\r", "when": 5}]}),
    "polling_when_undeclared_field": _d(polling={"queries": [{"send": "PWR?\r", "when": "has_zones"}]}),
    "each_child_undeclared_type": _d(
        polling={"queries": [{"each_child": "relay", "send": "Z{child_id}?\r"}]}
    ),
    "each_child_without_instances": _d(
        child_entity_types={"zone": {
            "state_variables": {"level": {"type": "number", "label": "Level"}},
        }},
        polling={"queries": [{"each_child": "zone", "send": "Z{child_id}?\r"}]},
    ),
    "each_child_missing_send": _d(
        child_entity_types=dict(_CHILD_TYPES),
        polling={"queries": [{"each_child": "zone"}]},
    ),
    "each_child_send_without_child_id": _d(
        child_entity_types=dict(_CHILD_TYPES),
        polling={"queries": [{"each_child": "zone", "send": "ZALL?\r"}]},
    ),
    "on_connect_bad_mapping_osc_variant": _d(
        transport="osc", on_connect=[{"foo": 1}]
    ),
}


def _current_verdicts() -> dict[str, list[str]]:
    return {name: _validate(case) for name, case in CASES.items()}


def test_every_case_is_rejected():
    """Every synthetic case must trip at least one validation error."""
    clean = [n for n, errs in _current_verdicts().items() if not errs]
    assert clean == [], f"cases that unexpectedly validated clean: {clean}"


def test_rejection_messages_match_fixture():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = _current_verdicts()
    assert sorted(actual) == sorted(expected), (
        "case list changed — regenerate the fixture "
        "(python tests/test_driver_validation_messages.py --regen)"
    )
    for name in sorted(expected):
        assert actual[name] == expected[name], (
            f"validation messages changed for '{name}':\n"
            f"  expected: {expected[name]}\n"
            f"  actual:   {actual[name]}"
        )


def test_cases_fixture_matches_case_table():
    """The exported case inputs must match CASES exactly.

    Consumers of the rules module replay the exported inputs, so an edited
    case that isn't re-exported would leave them proving parity against a
    stale corpus.
    """
    exported = json.loads(CASES_FIXTURE.read_text(encoding="utf-8"))
    assert exported == CASES, (
        "case inputs changed — regenerate the fixtures "
        "(python tests/test_driver_validation_messages.py --regen)"
    )


def _write_fixture(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        # Every case must survive a JSON round-trip unchanged, or the
        # exported corpus would silently diverge from what this file tests.
        round_tripped = json.loads(json.dumps(CASES))
        if round_tripped != CASES:
            print(
                "CASES is not JSON-round-trip-safe (non-string dict key, "
                "tuple, or similar) — fix the case before exporting",
                file=sys.stderr,
            )
            sys.exit(1)
        _write_fixture(FIXTURE, _current_verdicts())
        _write_fixture(CASES_FIXTURE, CASES)
        print(f"wrote {FIXTURE} ({len(CASES)} cases)")
        print(f"wrote {CASES_FIXTURE}")
    else:
        print("run with --regen to rewrite the fixtures", file=sys.stderr)
        sys.exit(2)
