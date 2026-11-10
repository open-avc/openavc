"""Pins for two parts of a driver's checks: where a value conversion may be
declared, and the keys the driver contract does not declare.

A ``scale``, ``offset`` or ``unknown`` converts between the number a device
sends and the real value. The platform converts a YAML driver's values, so on
a YAML driver the keys need a numeric type, a scale of 0 is refused, and an
action's parameters are never converted. A Python driver converts in its own
code, so there every one is refused, and so is a device setting's ``map``.

The unknown-key check walks the definition against the contract's registry:
a block whose keys are fixed reports a key it does not declare (with a
spelling hint), a block whose keys are names the author chose reports a name
that is not text, and anything else is descended without being judged. It
runs only in strict mode, after every other error.

Each YAML case is a set of top-level keys over one driver. The expected value
is every issue ``validate_driver_issues`` reports for the whole definition, in
order, each with its severity and the section tag the Driver Builder anchors
it to. Each rule's own entry point is pinned too, the unknown-key check with
the tag it records for each message, and the conversion rule on both
surfaces. A few shapes raise, and are pinned by the exception they raise.

These record what the checks say today, so a restructuring that keeps the
contract keeps every message, its position and its tag.
"""
from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import (
    unknown_key_errors,
    validate_driver_definition,
    validate_driver_issues,
    value_conversion_errors,
)
from openavc.drivers.python_info import python_driver_info_issues

# A case key set to this is left out of the definition altogether.
DROP = "<left out>"

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"noop": {"send": "NOOP\r"}},
}

POWER = {"power": {"type": "boolean", "label": "Power"}}


def variables(**entries: Any) -> dict[str, Any]:
    return {"state_variables": {**POWER, **entries}}


def params(**entries: Any) -> dict[str, Any]:
    """One command ``set`` with the given parameters."""
    return {"commands": {"noop": {"send": "NOOP\r"}, "set": {"send": "SET\r", "params": entries}}}


def child(**variables_: Any) -> dict[str, Any]:
    return {
        "child_entity_types": {
            "zone": {
                "id_format": {"type": "integer", "min": 1, "max": 4},
                "state_variables": variables_,
            }
        }
    }


def action(params_: Any, **fields: Any) -> dict[str, Any]:
    entry = {"id": "go", "label": "Go", "kind": "command", "command": "noop", "params": params_}
    entry.update(fields)
    return {"actions": [entry]}


GAIN = {"type": "number", "label": "Gain"}

CASES: dict[str, dict[str, Any]] = {
    # --- value conversion, on the YAML surface ------------------------------
    "conv_clean": variables(gain={**GAIN, "scale": 0.5, "offset": -18, "unknown": [255]}),
    "conv_each_numeric_type": variables(
        a={"type": "integer", "label": "A", "scale": 2},
        b={"type": "number", "label": "B", "offset": 1},
        c={"type": "float", "label": "C", "unknown": [0]},
    ),
    "conv_string_type": variables(gain={"type": "string", "label": "G", "scale": 2}),
    "conv_missing_type": variables(gain={"label": "G", "offset": 2}),
    "conv_enum_all_three": variables(
        gain={"type": "enum", "label": "G", "values": ["a"], "unknown": [1], "offset": 1, "scale": 2}
    ),
    "conv_order_is_the_keys_not_the_decl": variables(
        gain={"type": "boolean", "label": "G", "unknown": [1], "scale": 3}
    ),
    "conv_scale_zero": variables(gain={**GAIN, "scale": 0}),
    "conv_scale_zero_float": variables(gain={**GAIN, "scale": 0.0}),
    "conv_scale_negative_zero": variables(gain={**GAIN, "scale": -0.0}),
    "conv_scale_false": variables(gain={**GAIN, "scale": False}),
    "conv_scale_true": variables(gain={**GAIN, "scale": True}),
    "conv_scale_text_zero": variables(gain={**GAIN, "scale": "0"}),
    "conv_scale_none": variables(gain={**GAIN, "scale": None}),
    "conv_scale_zero_wrong_type": variables(gain={"type": "string", "label": "G", "scale": 0}),
    "conv_keys_present_as_none": variables(
        gain={"type": "string", "label": "G", "scale": None, "offset": None, "unknown": None}
    ),
    "conv_no_keys": variables(gain={"type": "string", "label": "G", "min": 0}),
    "conv_variable_not_a_mapping": variables(gain="scale"),
    "conv_variables_not_a_mapping": {"state_variables": ["scale"]},
    "conv_child_variable": child(level={"type": "string", "label": "L", "scale": 0}),
    "conv_child_variable_clean": child(level={**GAIN, "scale": 2}),
    "conv_child_type_not_a_mapping": {"child_entity_types": {"zone": ["scale"]}},
    "conv_child_variables_not_a_mapping": {
        "child_entity_types": {"zone": {"id_format": {"type": "integer"}, "state_variables": [1]}}
    },
    "conv_child_types_a_list": {"child_entity_types": [{"state_variables": {}}]},
    "conv_command_param": params(level={"type": "string", "label": "L", "offset": 3}),
    "conv_command_param_scale_zero": params(level={"type": "integer", "label": "L", "scale": 0}),
    "conv_command_param_clean": params(level={"type": "integer", "label": "L", "scale": 2}),
    "conv_command_not_a_mapping": {"commands": {"noop": {"send": "N"}, "set": "SET"}},
    "conv_command_params_not_a_mapping": {
        "commands": {"noop": {"send": "N"}, "set": {"send": "S", "params": ["level"]}}
    },
    "conv_commands_not_a_mapping": {"commands": ["noop"]},
    "conv_action_param": action({"level": {"type": "integer", "label": "L", "scale": 2}}),
    "conv_action_param_wrong_type_too": action({"level": {"type": "string", "scale": 0}}),
    "conv_action_param_without_keys": action({"level": {"type": "integer", "label": "L"}}),
    "conv_action_without_id": {
        "actions": [{"label": "Go", "kind": "command", "command": "noop",
                     "params": {"level": {"type": "integer", "unknown": [1]}}}]
    },
    "conv_action_not_a_mapping": {"actions": ["go"]},
    "conv_actions_a_mapping": {"actions": {"go": {"params": {"x": {"scale": 1}}}}},
    "conv_action_params_a_list": action([{"scale": 1}]),
    "conv_setting_map_on_yaml": {
        "device_settings": {
            "mode": {"type": "boolean", "label": "Mode", "state_key": "power",
                     "write": {"send": "M {value}\r"}, "map": {"true": "ON", "false": "OFF"}}
        }
    },
    "conv_every_place_in_order": {
        **variables(v={"type": "string", "label": "V", "scale": 1}),
        **params(p={"type": "string", "label": "P", "scale": 1}),
        **child(c={"type": "string", "label": "C", "scale": 1}),
        **action({"a": {"type": "integer", "scale": 1}}),
    },
    # --- names that are not text, which only strict mode reports ------------
    "names_not_text_lenient": {
        "state_variables": {**POWER, True: {"type": "integer", "label": "On"}},
    },
}

# Cases also recorded in strict mode, where the unknown-key check runs last.
STRICT = {
    "conv_every_place_in_order",
    "conv_actions_a_mapping",
    "names_not_text_lenient",
    "conv_child_types_a_list",
}

STRICT_CASES: dict[str, dict[str, Any]] = {
    "keys_clean": {},
    "keys_top_level_typo": {"state_varibles": {}},
    "keys_top_level_far_off": {"zzzz": 1},
    "keys_top_level_several_sorted": {"zeta": 1, "alpha": 2, "commandz": {}},
    "keys_top_level_name_not_text": {1: "x"},
    "keys_top_level_none": {None: "x"},
    "keys_variable_typo": variables(gain={"type": "number", "label": "G", "lable": "x"}),
    "keys_param_typo": params(level={"type": "integer", "label": "L", "mni": 0}),
    "keys_command_typo": {"commands": {"noop": {"send": "N", "sned": "x"}}},
    "keys_response_typo": {"responses": [{"match": "P(\\d)", "set": {"power": "$1"}, "mapings": []}]},
    "keys_response_mapping_typo": {
        "responses": [{"match": "P(\\d)", "mappings": [{"group": 1, "state": "power", "tpye": "x"}]}]
    },
    "keys_child_variable_typo": child(level={**GAIN, "unti": "dB"}),
    "keys_action_typo": action({}, lable="x"),
    "keys_action_param_typo": action({"level": {"type": "integer", "lable": "x"}}),
    "keys_config_field_typo": {"config_schema": {"host": {"type": "string", "lable": "Host"}}},
    "keys_polling_typo": {"polling": {"querys": []}},
    "keys_open_block_not_judged": {"push": {"type": "sse", "path": "/e", "anything": 1}},
    "keys_open_bridge_not_judged": {"bridge": {"anything": 1}},
    "keys_named_map_values_open": {"config_derived": {"x": {"anything": 1}}},
    "keys_list_where_a_block_goes": {"polling": [{"querys": 1}]},
    "keys_block_where_a_list_goes": {"responses": {"match": "x", "mapings": 1}},
    "keys_list_of_non_mappings": {"responses": ["x", 1, None]},
    "keys_scalar_where_a_block_goes": {"auth": "token"},
    "keys_named_map_a_list": {"state_variables": [{"lable": 1}]},
    "keys_name_not_text_variable": {"state_variables": {**POWER, True: {"type": "integer", "label": "On"}}},
    "keys_name_not_text_param": {
        "commands": {"noop": {"send": "N"}, "set": {"send": "S", "params": {True: {"type": "integer"}}}}
    },
    "keys_name_not_text_command": {"commands": {"noop": {"send": "N"}, 5: {"send": "F"}}},
    "keys_name_not_text_number_and_none": {
        "config_schema": {1.5: {"type": "string"}, None: {"type": "string"}}
    },
    "keys_name_not_text_first": {
        "state_variables": {False: {"type": "integer", "label": "Off"}, **POWER}
    },
    "keys_name_not_text_after_a_judged_sibling": {
        "state_variables": {"volume": {"type": "integer", "label": "V"}, 1: {"type": "integer", "label": "One"}}
    },
    "keys_name_not_text_after_a_sibling_with_a_typo": {
        "state_variables": {"volume": {"type": "integer", "lable": "V"}, 1: {"type": "integer"}}
    },
    "keys_name_not_text_after_a_nested_map": {
        "commands": {
            "set": {"send": "S", "params": {"level": {"type": "integer"}}},
            0: {"send": "Z"},
        }
    },
    "keys_name_not_text_in_a_setting_map": {
        "device_settings": {
            "mode": {"type": "boolean", "label": "Mode", "state_key": "power",
                     "write": {"send": "M {value}\r"}, "map": {True: "ON"}}
        }
    },
    "keys_name_not_text_in_when_state": {"polling": {"when_state": {1: "on"}}},
    "keys_in_both_and_nested": {
        "polling": {"querys": [], "when_state": {False: 1}},
        "state_variables": {**POWER, "x": {"type": "integer", "lable": 1, "unti": 2}},
    },
    "keys_typo_and_unknown_together_ordered": {
        "state_variables": {**POWER, "x": {"type": "integer", "lable": "X", "zz": 1, "aa": 2}},
    },
}

# Shapes that make the unknown-key check raise (a fixed block holding a key
# that is not text beside an unknown key: the sort compares them).
RAISING_CASES: dict[str, dict[str, Any]] = {
    "keys_text_and_number_unknown_together": {"zzz": 1, 7: 2},
    "keys_nested_text_and_bool_unknown": variables(x={"type": "integer", "lable": "X", True: 1}),
}

# The Python surface: a DRIVER_INFO's value conversion and setting map.
PY_BASE: dict[str, Any] = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}

PY_CASES: dict[str, dict[str, Any]] = {
    "py_clean": {"state_variables": {"gain": GAIN}},
    "py_variable": {"state_variables": {"gain": {**GAIN, "unknown": [1], "scale": 2}}},
    "py_variable_wrong_type": {"state_variables": {"gain": {"type": "string", "offset": 0}}},
    "py_variable_not_a_mapping": {"state_variables": {"gain": 5}},
    "py_child_variable": child(level={**GAIN, "offset": 1}),
    "py_command_param": params(level={"type": "integer", "scale": 0}),
    "py_action_param": action({"level": {"type": "integer", "scale": 2}}),
    "py_setting_map": {"device_settings": {"mode": {"type": "boolean", "map": {"true": "ON"}}}},
    "py_setting_map_none": {"device_settings": {"mode": {"map": None}}},
    "py_setting_without_map": {"device_settings": {"mode": {"type": "boolean"}}},
    "py_setting_not_a_mapping": {"device_settings": {"mode": "map"}},
    "py_settings_a_list": {"device_settings": [{"map": {}}]},
    "py_every_place_in_order": {
        "device_settings": {"mode": {"map": {}}, "other": {"map": {}}},
        **action({"a": {"scale": 1}}),
        **params(p={"offset": 1}),
        **child(c={"unknown": [1]}),
        **variables(v={"scale": 1}),
    },
}


def _definition(case: str) -> dict[str, Any]:
    merged = {**BASE, **CASES[case]}
    return {key: value for key, value in merged.items() if value is not DROP}


def _strict_definition(case: str) -> dict[str, Any]:
    return {**BASE, **STRICT_CASES[case]}


def _issues(definition: dict[str, Any], strict: bool) -> list[tuple[str, str, str]]:
    return [
        (i["severity"], i["path"], i["message"])
        for i in validate_driver_issues(definition, strict=strict)
    ]


def _tagged(messages: list[str]) -> list[tuple[str, str]]:
    return list(zip(messages.tags, messages))  # type: ignore[attr-defined]


EXPECTED: dict[str, list[tuple[str, str, str]]] = {'conv_action_not_a_mapping': [('error', 'actions', 'actions[0]: must be a mapping')],
 'conv_action_param': [('error',
                        'state_variables',
                        "Action 'go' param 'level': put scale and offset on the command's own "
                        "parameter; an action's parameters are not converted")],
 'conv_action_param_without_keys': [],
 'conv_action_param_wrong_type_too': [('error',
                                       'state_variables',
                                       "Action 'go' param 'level': put scale and offset on the "
                                       "command's own parameter; an action's parameters are not "
                                       'converted')],
 'conv_action_params_a_list': [('error', 'actions', "actions[0]: 'params' must be a mapping")],
 'conv_action_without_id': [('error',
                             'actions',
                             "actions[0]: missing required 'id' (non-empty string)"),
                            ('error',
                             'state_variables',
                             "Action 'None' param 'level': put scale and offset on the command's "
                             "own parameter; an action's parameters are not converted")],
 'conv_actions_a_mapping': [('error', 'actions', 'actions: must be a list')],
 'conv_child_type_not_a_mapping': [('error',
                                    'child_entity_types.zone',
                                    'child_entity_types.zone: must be a mapping')],
 'conv_child_types_a_list': [('error',
                              'child_entity_types',
                              'child_entity_types: must be a mapping')],
 'conv_child_variable': [('error',
                          'state_variables',
                          'child_entity_types.zone.state_variables.level: the type must be '
                          'integer, number or float to use scale'),
                         ('error',
                          'state_variables',
                          'child_entity_types.zone.state_variables.level: scale cannot be 0')],
 'conv_child_variable_clean': [],
 'conv_child_variables_not_a_mapping': [('error',
                                         'child_entity_types.zone',
                                         'child_entity_types.zone.state_variables: must be a '
                                         'mapping')],
 'conv_clean': [],
 'conv_command_not_a_mapping': [('error', 'commands.set', "Command 'set': must be a dict")],
 'conv_command_param': [('error',
                         'state_variables',
                         "Command 'set' param 'level': the type must be integer, number or float "
                         'to use offset')],
 'conv_command_param_clean': [],
 'conv_command_param_scale_zero': [('error',
                                    'state_variables',
                                    "Command 'set' param 'level': scale cannot be 0")],
 'conv_command_params_not_a_mapping': [],
 'conv_commands_not_a_mapping': [('error', 'commands', 'commands: must be a mapping')],
 'conv_each_numeric_type': [],
 'conv_enum_all_three': [('error',
                          'state_variables',
                          "State variable 'gain': the type must be integer, number or float to use "
                          'scale, offset, unknown')],
 'conv_every_place_in_order': [('error',
                                'state_variables',
                                "State variable 'v': the type must be integer, number or float to "
                                'use scale'),
                               ('error',
                                'state_variables',
                                'child_entity_types.zone.state_variables.c: the type must be '
                                'integer, number or float to use scale'),
                               ('error',
                                'state_variables',
                                "Command 'set' param 'p': the type must be integer, number or "
                                'float to use scale'),
                               ('error',
                                'state_variables',
                                "Action 'go' param 'a': put scale and offset on the command's own "
                                "parameter; an action's parameters are not converted")],
 'conv_keys_present_as_none': [('error',
                                'state_variables',
                                "State variable 'gain': the type must be integer, number or float "
                                'to use scale, offset, unknown')],
 'conv_missing_type': [('error',
                        'state_variables',
                        "State variable 'gain': the type must be integer, number or float to use "
                        'offset')],
 'conv_no_keys': [],
 'conv_order_is_the_keys_not_the_decl': [('error',
                                          'state_variables',
                                          "State variable 'gain': the type must be integer, number "
                                          'or float to use scale, unknown')],
 'conv_scale_false': [],
 'conv_scale_negative_zero': [('error',
                               'state_variables',
                               "State variable 'gain': scale cannot be 0")],
 'conv_scale_none': [],
 'conv_scale_text_zero': [],
 'conv_scale_true': [],
 'conv_scale_zero': [('error', 'state_variables', "State variable 'gain': scale cannot be 0")],
 'conv_scale_zero_float': [('error',
                            'state_variables',
                            "State variable 'gain': scale cannot be 0")],
 'conv_scale_zero_wrong_type': [('error',
                                 'state_variables',
                                 "State variable 'gain': the type must be integer, number or float "
                                 'to use scale'),
                                ('error',
                                 'state_variables',
                                 "State variable 'gain': scale cannot be 0")],
 'conv_setting_map_on_yaml': [],
 'conv_string_type': [('error',
                       'state_variables',
                       "State variable 'gain': the type must be integer, number or float to use "
                       'scale')],
 'conv_variable_not_a_mapping': [('error',
                                  'state_variables.gain',
                                  "State variable 'gain': must be a dict")],
 'conv_variables_not_a_mapping': [('error',
                                   'state_variables',
                                   'state_variables: must be a mapping')],
 'names_not_text_lenient': []}

STRICT_EXPECTED: dict[str, list[tuple[str, str, str]]] = {'conv_actions_a_mapping': [('error', 'actions', 'actions: must be a list')],
 'conv_child_types_a_list': [('error',
                              'child_entity_types',
                              'child_entity_types: must be a mapping')],
 'conv_every_place_in_order': [('error',
                                'state_variables',
                                "State variable 'v': the type must be integer, number or float to "
                                'use scale'),
                               ('error',
                                'state_variables',
                                'child_entity_types.zone.state_variables.c: the type must be '
                                'integer, number or float to use scale'),
                               ('error',
                                'state_variables',
                                "Command 'set' param 'p': the type must be integer, number or "
                                'float to use scale'),
                               ('error',
                                'state_variables',
                                "Action 'go' param 'a': put scale and offset on the command's own "
                                "parameter; an action's parameters are not converted")],
 'keys_action_param_typo': [('error',
                             'actions[0].params.level',
                             "actions[0].params.level: unknown key 'lable' (did you mean "
                             "'label'?)")],
 'keys_action_typo': [('error',
                       'actions[0]',
                       "actions[0]: unknown key 'lable' (did you mean 'label'?)")],
 'keys_block_where_a_list_goes': [('error', 'responses', 'responses: must be a list')],
 'keys_child_variable_typo': [('error',
                               'child_entity_types.zone.state_variables.level',
                               "child_entity_types.zone.state_variables.level: unknown key 'unti' "
                               "(did you mean 'unit'?)")],
 'keys_clean': [],
 'keys_command_typo': [('error',
                        'commands.noop',
                        "commands.noop: unknown key 'sned' (did you mean 'send'?)")],
 'keys_config_field_typo': [('error',
                             'config_schema.host',
                             "config_schema.host: unknown key 'lable' (did you mean 'label'?)")],
 'keys_in_both_and_nested': [('error', 'state_variables.x', "State variable 'x': missing 'label'"),
                             ('error',
                              'polling',
                              "polling: when_state 'False' is not a declared state variable"),
                             ('error',
                              'state_variables.x',
                              "state_variables.x: unknown key 'lable' (did you mean 'label'?)"),
                             ('error',
                              'state_variables.x',
                              "state_variables.x: unknown key 'unti' (did you mean 'unit'?)"),
                             ('error',
                              'polling',
                              "polling: unknown key 'querys' (did you mean 'queries'?)"),
                             ('error',
                              'polling.when_state',
                              "polling.when_state: name 'False' is not text. YAML reads bare "
                              'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                              'Put it in quotes.')],
 'keys_list_of_non_mappings': [('error', 'responses[0]', 'Response 0: must be a mapping'),
                               ('error', 'responses[1]', 'Response 1: must be a mapping'),
                               ('error', 'responses[2]', 'Response 2: must be a mapping'),
                               ('warning',
                                'liveness',
                                'Nothing notices if the device stops answering over tcp: it sends '
                                'no poll at its default config, and there is no liveness block, so '
                                'the device reads connected for as long as the connection stays '
                                'open. Add a liveness block that asks something the device answers '
                                'in every state.')],
 'keys_list_where_a_block_goes': [('error', 'polling', 'polling: must be a mapping')],
 'keys_name_not_text_after_a_judged_sibling': [('error',
                                                'state_variables.volume',
                                                "state_variables: name '1' is not text. YAML reads "
                                                'bare on/off/yes/no/true/false as booleans and '
                                                'bare digits as numbers. Put it in quotes.')],
 'keys_name_not_text_after_a_nested_map': [('error',
                                            'commands.set.params.level',
                                            "commands: name '0' is not text. YAML reads bare "
                                            'on/off/yes/no/true/false as booleans and bare digits '
                                            'as numbers. Put it in quotes.'),
                                           ('warning',
                                            'commands.set.params.level',
                                            "Command 'set' parameter 'level' has no label, so "
                                            'forms show its key made readable. Give it a label '
                                            'that says what to enter.')],
 'keys_name_not_text_after_a_sibling_with_a_typo': [('error',
                                                     'state_variables.volume',
                                                     "State variable 'volume': missing 'label'"),
                                                    ('error',
                                                     'state_variables.1',
                                                     "State variable '1': missing 'label'"),
                                                    ('error',
                                                     'state_variables.volume',
                                                     "state_variables.volume: unknown key 'lable' "
                                                     "(did you mean 'label'?)"),
                                                    ('error',
                                                     'state_variables.volume',
                                                     "state_variables: name '1' is not text. YAML "
                                                     'reads bare on/off/yes/no/true/false as '
                                                     'booleans and bare digits as numbers. Put it '
                                                     'in quotes.')],
 'keys_name_not_text_command': [('error',
                                 'commands.noop',
                                 "commands: name '5' is not text. YAML reads bare "
                                 'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                                 'Put it in quotes.')],
 'keys_name_not_text_first': [('error',
                               'state_variables',
                               "state_variables: name 'False' is not text. YAML reads bare "
                               'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                               'Put it in quotes.')],
 'keys_name_not_text_in_a_setting_map': [('error',
                                          'device_settings.mode',
                                          "Device setting 'mode': map key 'True' is not text; YAML "
                                          'reads a bare true, false, on, off, yes or no as a '
                                          'boolean. Put it in quotes.'),
                                         ('error',
                                          'device_settings.mode.map',
                                          "device_settings.mode.map: name 'True' is not text. YAML "
                                          'reads bare on/off/yes/no/true/false as booleans and '
                                          'bare digits as numbers. Put it in quotes.')],
 'keys_name_not_text_in_when_state': [('error',
                                       'polling',
                                       "polling: when_state '1' is not a declared state variable"),
                                      ('error',
                                       'polling.when_state',
                                       "polling.when_state: name '1' is not text. YAML reads bare "
                                       'on/off/yes/no/true/false as booleans and bare digits as '
                                       'numbers. Put it in quotes.')],
 'keys_name_not_text_number_and_none': [('error',
                                         'config_schema',
                                         "config_schema: name '1.5' is not text. YAML reads bare "
                                         'on/off/yes/no/true/false as booleans and bare digits as '
                                         'numbers. Put it in quotes.'),
                                        ('error',
                                         'config_schema.1.5',
                                         "config_schema: name 'None' is not text. YAML reads bare "
                                         'on/off/yes/no/true/false as booleans and bare digits as '
                                         'numbers. Put it in quotes.')],
 'keys_name_not_text_param': [('error',
                               'commands.set.params',
                               "commands.set.params: name 'True' is not text. YAML reads bare "
                               'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                               'Put it in quotes.'),
                              ('warning',
                               'commands.set.params.True',
                               "Command 'set' parameter 'True' has no label, so forms show its key "
                               'made readable. Give it a label that says what to enter.')],
 'keys_name_not_text_variable': [('error',
                                  'state_variables.power',
                                  "state_variables: name 'True' is not text. YAML reads bare "
                                  'on/off/yes/no/true/false as booleans and bare digits as '
                                  'numbers. Put it in quotes.')],
 'keys_named_map_a_list': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'keys_named_map_values_open': [],
 'keys_open_block_not_judged': [('error',
                                 'push',
                                 "push: unknown key(s): anything (known keys for type 'sse': "
                                 'idle_timeout, path, register, session, type, unregister)'),
                                ('error',
                                 'push',
                                 "push: type 'sse' requires the http transport, not 'tcp'")],
 'keys_open_bridge_not_judged': [],
 'keys_param_typo': [('error',
                      'commands.set.params.level',
                      "commands.set.params.level: unknown key 'mni'")],
 'keys_polling_typo': [('error',
                        'polling',
                        "polling: unknown key 'querys' (did you mean 'queries'?)")],
 'keys_response_mapping_typo': [('error',
                                 'responses[0].mappings[0]',
                                 "responses[0].mappings[0]: unknown key 'tpye' (did you mean "
                                 "'type'?)"),
                                ('warning',
                                 'liveness',
                                 'Nothing notices if the device stops answering over tcp: it sends '
                                 'no poll at its default config, and there is no liveness block, '
                                 'so the device reads connected for as long as the connection '
                                 'stays open. Add a liveness block that asks something the device '
                                 'answers in every state.')],
 'keys_response_typo': [('error',
                         'responses[0]',
                         "responses[0]: unknown key 'mapings' (did you mean 'mappings'?)"),
                        ('warning',
                         'liveness',
                         'Nothing notices if the device stops answering over tcp: it sends no poll '
                         'at its default config, and there is no liveness block, so the device '
                         'reads connected for as long as the connection stays open. Add a liveness '
                         'block that asks something the device answers in every state.')],
 'keys_scalar_where_a_block_goes': [('error', 'auth', 'auth: must be a mapping')],
 'keys_top_level_far_off': [('error', '', "unknown key 'zzzz'")],
 'keys_top_level_name_not_text': [('error', '', "unknown key '1'")],
 'keys_top_level_none': [('error', '', "unknown key 'None'")],
 'keys_top_level_several_sorted': [('error', '', "unknown key 'alpha'"),
                                   ('error',
                                    '',
                                    "unknown key 'commandz' (did you mean 'commands'?)"),
                                   ('error', '', "unknown key 'zeta'")],
 'keys_top_level_typo': [('error',
                          '',
                          "unknown key 'state_varibles' (did you mean 'state_variables'?)")],
 'keys_typo_and_unknown_together_ordered': [('error',
                                             'state_variables.x',
                                             "State variable 'x': missing 'label'"),
                                            ('error',
                                             'state_variables.x',
                                             "state_variables.x: unknown key 'aa'"),
                                            ('error',
                                             'state_variables.x',
                                             "state_variables.x: unknown key 'lable' (did you mean "
                                             "'label'?)"),
                                            ('error',
                                             'state_variables.x',
                                             "state_variables.x: unknown key 'zz'")],
 'keys_variable_typo': [('error',
                         'state_variables.gain',
                         "state_variables.gain: unknown key 'lable' (did you mean 'label'?)")],
 'names_not_text_lenient': [('error',
                             'state_variables.power',
                             "state_variables: name 'True' is not text. YAML reads bare "
                             'on/off/yes/no/true/false as booleans and bare digits as numbers. Put '
                             'it in quotes.')]}

UNKNOWN_EXPECTED: dict[str, list[tuple[str, str]]] = {'keys_action_param_typo': [('actions[0].params.level',
                             "actions[0].params.level: unknown key 'lable' (did you mean "
                             "'label'?)")],
 'keys_action_typo': [('actions[0]', "actions[0]: unknown key 'lable' (did you mean 'label'?)")],
 'keys_block_where_a_list_goes': [],
 'keys_child_variable_typo': [('child_entity_types.zone.state_variables.level',
                               "child_entity_types.zone.state_variables.level: unknown key 'unti' "
                               "(did you mean 'unit'?)")],
 'keys_clean': [],
 'keys_command_typo': [('commands.noop',
                        "commands.noop: unknown key 'sned' (did you mean 'send'?)")],
 'keys_config_field_typo': [('config_schema.host',
                             "config_schema.host: unknown key 'lable' (did you mean 'label'?)")],
 'keys_in_both_and_nested': [('state_variables.x',
                              "state_variables.x: unknown key 'lable' (did you mean 'label'?)"),
                             ('state_variables.x',
                              "state_variables.x: unknown key 'unti' (did you mean 'unit'?)"),
                             ('polling', "polling: unknown key 'querys' (did you mean 'queries'?)"),
                             ('polling.when_state',
                              "polling.when_state: name 'False' is not text. YAML reads bare "
                              'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                              'Put it in quotes.')],
 'keys_list_of_non_mappings': [],
 'keys_list_where_a_block_goes': [],
 'keys_name_not_text_after_a_judged_sibling': [('state_variables.volume',
                                                "state_variables: name '1' is not text. YAML reads "
                                                'bare on/off/yes/no/true/false as booleans and '
                                                'bare digits as numbers. Put it in quotes.')],
 'keys_name_not_text_after_a_nested_map': [('commands.set.params.level',
                                            "commands: name '0' is not text. YAML reads bare "
                                            'on/off/yes/no/true/false as booleans and bare digits '
                                            'as numbers. Put it in quotes.')],
 'keys_name_not_text_after_a_sibling_with_a_typo': [('state_variables.volume',
                                                     "state_variables.volume: unknown key 'lable' "
                                                     "(did you mean 'label'?)"),
                                                    ('state_variables.volume',
                                                     "state_variables: name '1' is not text. YAML "
                                                     'reads bare on/off/yes/no/true/false as '
                                                     'booleans and bare digits as numbers. Put it '
                                                     'in quotes.')],
 'keys_name_not_text_command': [('commands.noop',
                                 "commands: name '5' is not text. YAML reads bare "
                                 'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                                 'Put it in quotes.')],
 'keys_name_not_text_first': [('state_variables',
                               "state_variables: name 'False' is not text. YAML reads bare "
                               'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                               'Put it in quotes.')],
 'keys_name_not_text_in_a_setting_map': [('device_settings.mode.map',
                                          "device_settings.mode.map: name 'True' is not text. YAML "
                                          'reads bare on/off/yes/no/true/false as booleans and '
                                          'bare digits as numbers. Put it in quotes.')],
 'keys_name_not_text_in_when_state': [('polling.when_state',
                                       "polling.when_state: name '1' is not text. YAML reads bare "
                                       'on/off/yes/no/true/false as booleans and bare digits as '
                                       'numbers. Put it in quotes.')],
 'keys_name_not_text_number_and_none': [('config_schema',
                                         "config_schema: name '1.5' is not text. YAML reads bare "
                                         'on/off/yes/no/true/false as booleans and bare digits as '
                                         'numbers. Put it in quotes.'),
                                        ('config_schema.1.5',
                                         "config_schema: name 'None' is not text. YAML reads bare "
                                         'on/off/yes/no/true/false as booleans and bare digits as '
                                         'numbers. Put it in quotes.')],
 'keys_name_not_text_param': [('commands.set.params',
                               "commands.set.params: name 'True' is not text. YAML reads bare "
                               'on/off/yes/no/true/false as booleans and bare digits as numbers. '
                               'Put it in quotes.')],
 'keys_name_not_text_variable': [('state_variables.power',
                                  "state_variables: name 'True' is not text. YAML reads bare "
                                  'on/off/yes/no/true/false as booleans and bare digits as '
                                  'numbers. Put it in quotes.')],
 'keys_named_map_a_list': [],
 'keys_named_map_values_open': [],
 'keys_open_block_not_judged': [],
 'keys_open_bridge_not_judged': [],
 'keys_param_typo': [('commands.set.params.level', "commands.set.params.level: unknown key 'mni'")],
 'keys_polling_typo': [('polling', "polling: unknown key 'querys' (did you mean 'queries'?)")],
 'keys_response_mapping_typo': [('responses[0].mappings[0]',
                                 "responses[0].mappings[0]: unknown key 'tpye' (did you mean "
                                 "'type'?)")],
 'keys_response_typo': [('responses[0]',
                         "responses[0]: unknown key 'mapings' (did you mean 'mappings'?)")],
 'keys_scalar_where_a_block_goes': [],
 'keys_top_level_far_off': [('', "unknown key 'zzzz'")],
 'keys_top_level_name_not_text': [('', "unknown key '1'")],
 'keys_top_level_none': [('', "unknown key 'None'")],
 'keys_top_level_several_sorted': [('', "unknown key 'alpha'"),
                                   ('', "unknown key 'commandz' (did you mean 'commands'?)"),
                                   ('', "unknown key 'zeta'")],
 'keys_top_level_typo': [('', "unknown key 'state_varibles' (did you mean 'state_variables'?)")],
 'keys_typo_and_unknown_together_ordered': [('state_variables.x',
                                             "state_variables.x: unknown key 'aa'"),
                                            ('state_variables.x',
                                             "state_variables.x: unknown key 'lable' (did you mean "
                                             "'label'?)"),
                                            ('state_variables.x',
                                             "state_variables.x: unknown key 'zz'")],
 'keys_variable_typo': [('state_variables.gain',
                         "state_variables.gain: unknown key 'lable' (did you mean 'label'?)")]}

CONVERSION_EXPECTED: dict[str, dict[str, list[str]]] = {'conv_action_not_a_mapping': {'python': [], 'yaml': []},
 'conv_action_param': {'python': ["Action 'go' param 'level': remove scale; the platform converts "
                                  'values only for YAML drivers, and a Python driver converts in '
                                  'its own code'],
                       'yaml': ["Action 'go' param 'level': put scale and offset on the command's "
                                "own parameter; an action's parameters are not converted"]},
 'conv_action_param_without_keys': {'python': [], 'yaml': []},
 'conv_action_param_wrong_type_too': {'python': ["Action 'go' param 'level': remove scale; the "
                                                 'platform converts values only for YAML drivers, '
                                                 'and a Python driver converts in its own code'],
                                      'yaml': ["Action 'go' param 'level': put scale and offset on "
                                               "the command's own parameter; an action's "
                                               'parameters are not converted']},
 'conv_action_params_a_list': {'python': [], 'yaml': []},
 'conv_action_without_id': {'python': ["Action 'None' param 'level': remove unknown; the platform "
                                       'converts values only for YAML drivers, and a Python driver '
                                       'converts in its own code'],
                            'yaml': ["Action 'None' param 'level': put scale and offset on the "
                                     "command's own parameter; an action's parameters are not "
                                     'converted']},
 'conv_actions_a_mapping': {'python': [], 'yaml': []},
 'conv_child_type_not_a_mapping': {'python': [], 'yaml': []},
 'conv_child_types_a_list': {'python': [], 'yaml': []},
 'conv_child_variable': {'python': ['child_entity_types.zone.state_variables.level: remove scale; '
                                    'the platform converts values only for YAML drivers, and a '
                                    'Python driver converts in its own code'],
                         'yaml': ['child_entity_types.zone.state_variables.level: the type must be '
                                  'integer, number or float to use scale',
                                  'child_entity_types.zone.state_variables.level: scale cannot be '
                                  '0']},
 'conv_child_variable_clean': {'python': ['child_entity_types.zone.state_variables.level: remove '
                                          'scale; the platform converts values only for YAML '
                                          'drivers, and a Python driver converts in its own code'],
                               'yaml': []},
 'conv_child_variables_not_a_mapping': {'python': [], 'yaml': []},
 'conv_clean': {'python': ["State variable 'gain': remove scale, offset, unknown; the platform "
                           'converts values only for YAML drivers, and a Python driver converts in '
                           'its own code'],
                'yaml': []},
 'conv_command_not_a_mapping': {'python': [], 'yaml': []},
 'conv_command_param': {'python': ["Command 'set' param 'level': remove offset; the platform "
                                   'converts values only for YAML drivers, and a Python driver '
                                   'converts in its own code'],
                        'yaml': ["Command 'set' param 'level': the type must be integer, number or "
                                 'float to use offset']},
 'conv_command_param_clean': {'python': ["Command 'set' param 'level': remove scale; the platform "
                                         'converts values only for YAML drivers, and a Python '
                                         'driver converts in its own code'],
                              'yaml': []},
 'conv_command_param_scale_zero': {'python': ["Command 'set' param 'level': remove scale; the "
                                              'platform converts values only for YAML drivers, and '
                                              'a Python driver converts in its own code'],
                                   'yaml': ["Command 'set' param 'level': scale cannot be 0"]},
 'conv_command_params_not_a_mapping': {'python': [], 'yaml': []},
 'conv_commands_not_a_mapping': {'python': [], 'yaml': []},
 'conv_each_numeric_type': {'python': ["State variable 'a': remove scale; the platform converts "
                                       'values only for YAML drivers, and a Python driver converts '
                                       'in its own code',
                                       "State variable 'b': remove offset; the platform converts "
                                       'values only for YAML drivers, and a Python driver converts '
                                       'in its own code',
                                       "State variable 'c': remove unknown; the platform converts "
                                       'values only for YAML drivers, and a Python driver converts '
                                       'in its own code'],
                            'yaml': []},
 'conv_enum_all_three': {'python': ["State variable 'gain': remove scale, offset, unknown; the "
                                    'platform converts values only for YAML drivers, and a Python '
                                    'driver converts in its own code'],
                         'yaml': ["State variable 'gain': the type must be integer, number or "
                                  'float to use scale, offset, unknown']},
 'conv_every_place_in_order': {'python': ["State variable 'v': remove scale; the platform converts "
                                          'values only for YAML drivers, and a Python driver '
                                          'converts in its own code',
                                          'child_entity_types.zone.state_variables.c: remove '
                                          'scale; the platform converts values only for YAML '
                                          'drivers, and a Python driver converts in its own code',
                                          "Command 'set' param 'p': remove scale; the platform "
                                          'converts values only for YAML drivers, and a Python '
                                          'driver converts in its own code',
                                          "Action 'go' param 'a': remove scale; the platform "
                                          'converts values only for YAML drivers, and a Python '
                                          'driver converts in its own code'],
                               'yaml': ["State variable 'v': the type must be integer, number or "
                                        'float to use scale',
                                        'child_entity_types.zone.state_variables.c: the type must '
                                        'be integer, number or float to use scale',
                                        "Command 'set' param 'p': the type must be integer, number "
                                        'or float to use scale',
                                        "Action 'go' param 'a': put scale and offset on the "
                                        "command's own parameter; an action's parameters are not "
                                        'converted']},
 'conv_keys_present_as_none': {'python': ["State variable 'gain': remove scale, offset, unknown; "
                                          'the platform converts values only for YAML drivers, and '
                                          'a Python driver converts in its own code'],
                               'yaml': ["State variable 'gain': the type must be integer, number "
                                        'or float to use scale, offset, unknown']},
 'conv_missing_type': {'python': ["State variable 'gain': remove offset; the platform converts "
                                  'values only for YAML drivers, and a Python driver converts in '
                                  'its own code'],
                       'yaml': ["State variable 'gain': the type must be integer, number or float "
                                'to use offset']},
 'conv_no_keys': {'python': [], 'yaml': []},
 'conv_order_is_the_keys_not_the_decl': {'python': ["State variable 'gain': remove scale, unknown; "
                                                    'the platform converts values only for YAML '
                                                    'drivers, and a Python driver converts in its '
                                                    'own code'],
                                         'yaml': ["State variable 'gain': the type must be "
                                                  'integer, number or float to use scale, '
                                                  'unknown']},
 'conv_scale_false': {'python': ["State variable 'gain': remove scale; the platform converts "
                                 'values only for YAML drivers, and a Python driver converts in '
                                 'its own code'],
                      'yaml': []},
 'conv_scale_negative_zero': {'python': ["State variable 'gain': remove scale; the platform "
                                         'converts values only for YAML drivers, and a Python '
                                         'driver converts in its own code'],
                              'yaml': ["State variable 'gain': scale cannot be 0"]},
 'conv_scale_none': {'python': ["State variable 'gain': remove scale; the platform converts values "
                                'only for YAML drivers, and a Python driver converts in its own '
                                'code'],
                     'yaml': []},
 'conv_scale_text_zero': {'python': ["State variable 'gain': remove scale; the platform converts "
                                     'values only for YAML drivers, and a Python driver converts '
                                     'in its own code'],
                          'yaml': []},
 'conv_scale_true': {'python': ["State variable 'gain': remove scale; the platform converts values "
                                'only for YAML drivers, and a Python driver converts in its own '
                                'code'],
                     'yaml': []},
 'conv_scale_zero': {'python': ["State variable 'gain': remove scale; the platform converts values "
                                'only for YAML drivers, and a Python driver converts in its own '
                                'code'],
                     'yaml': ["State variable 'gain': scale cannot be 0"]},
 'conv_scale_zero_float': {'python': ["State variable 'gain': remove scale; the platform converts "
                                      'values only for YAML drivers, and a Python driver converts '
                                      'in its own code'],
                           'yaml': ["State variable 'gain': scale cannot be 0"]},
 'conv_scale_zero_wrong_type': {'python': ["State variable 'gain': remove scale; the platform "
                                           'converts values only for YAML drivers, and a Python '
                                           'driver converts in its own code'],
                                'yaml': ["State variable 'gain': the type must be integer, number "
                                         'or float to use scale',
                                         "State variable 'gain': scale cannot be 0"]},
 'conv_setting_map_on_yaml': {'python': ["Device setting 'mode': remove map; the platform maps a "
                                         "setting's value only for YAML drivers, and a Python "
                                         'driver writes its settings in its own code'],
                              'yaml': []},
 'conv_string_type': {'python': ["State variable 'gain': remove scale; the platform converts "
                                 'values only for YAML drivers, and a Python driver converts in '
                                 'its own code'],
                      'yaml': ["State variable 'gain': the type must be integer, number or float "
                               'to use scale']},
 'conv_variable_not_a_mapping': {'python': [], 'yaml': []},
 'conv_variables_not_a_mapping': {'python': [], 'yaml': []},
 'names_not_text_lenient': {'python': [], 'yaml': []}}

PY_EXPECTED: dict[str, dict[str, list[str]]] = {'py_action_param': {'conversion': ["Action 'go' param 'level': remove scale; the platform "
                                    'converts values only for YAML drivers, and a Python driver '
                                    'converts in its own code'],
                     'issues': ["Action 'go' param 'level': remove scale; the platform converts "
                                'values only for YAML drivers, and a Python driver converts in its '
                                'own code'],
                     'yaml': ["Action 'go' param 'level': put scale and offset on the command's "
                              "own parameter; an action's parameters are not converted"]},
 'py_child_variable': {'conversion': ['child_entity_types.zone.state_variables.level: remove '
                                      'offset; the platform converts values only for YAML drivers, '
                                      'and a Python driver converts in its own code'],
                       'issues': ['child_entity_types.zone.state_variables.level: remove offset; '
                                  'the platform converts values only for YAML drivers, and a '
                                  'Python driver converts in its own code'],
                       'yaml': []},
 'py_clean': {'conversion': [], 'issues': [], 'yaml': []},
 'py_command_param': {'conversion': ["Command 'set' param 'level': remove scale; the platform "
                                     'converts values only for YAML drivers, and a Python driver '
                                     'converts in its own code'],
                      'issues': ["Command 'set' param 'level': remove scale; the platform converts "
                                 'values only for YAML drivers, and a Python driver converts in '
                                 'its own code'],
                      'yaml': ["Command 'set' param 'level': scale cannot be 0"]},
 'py_every_place_in_order': {'conversion': ["State variable 'v': remove scale; the platform "
                                            'converts values only for YAML drivers, and a Python '
                                            'driver converts in its own code',
                                            'child_entity_types.zone.state_variables.c: remove '
                                            'unknown; the platform converts values only for YAML '
                                            'drivers, and a Python driver converts in its own code',
                                            "Command 'set' param 'p': remove offset; the platform "
                                            'converts values only for YAML drivers, and a Python '
                                            'driver converts in its own code',
                                            "Action 'go' param 'a': remove scale; the platform "
                                            'converts values only for YAML drivers, and a Python '
                                            'driver converts in its own code',
                                            "Device setting 'mode': remove map; the platform maps "
                                            "a setting's value only for YAML drivers, and a Python "
                                            'driver writes its settings in its own code',
                                            "Device setting 'other': remove map; the platform maps "
                                            "a setting's value only for YAML drivers, and a Python "
                                            'driver writes its settings in its own code'],
                             'issues': ["Device setting 'mode': state_key 'mode' is not a declared "
                                        'state variable, so the setting would never read back',
                                        "Device setting 'other': state_key 'other' is not a "
                                        'declared state variable, so the setting would never read '
                                        'back',
                                        "State variable 'v': remove scale; the platform converts "
                                        'values only for YAML drivers, and a Python driver '
                                        'converts in its own code',
                                        'child_entity_types.zone.state_variables.c: remove '
                                        'unknown; the platform converts values only for YAML '
                                        'drivers, and a Python driver converts in its own code',
                                        "Command 'set' param 'p': remove offset; the platform "
                                        'converts values only for YAML drivers, and a Python '
                                        'driver converts in its own code',
                                        "Action 'go' param 'a': remove scale; the platform "
                                        'converts values only for YAML drivers, and a Python '
                                        'driver converts in its own code',
                                        "Device setting 'mode': remove map; the platform maps a "
                                        "setting's value only for YAML drivers, and a Python "
                                        'driver writes its settings in its own code',
                                        "Device setting 'other': remove map; the platform maps a "
                                        "setting's value only for YAML drivers, and a Python "
                                        'driver writes its settings in its own code'],
                             'yaml': ["State variable 'v': the type must be integer, number or "
                                      'float to use scale',
                                      'child_entity_types.zone.state_variables.c: the type must be '
                                      'integer, number or float to use unknown',
                                      "Command 'set' param 'p': the type must be integer, number "
                                      'or float to use offset',
                                      "Action 'go' param 'a': put scale and offset on the "
                                      "command's own parameter; an action's parameters are not "
                                      'converted']},
 'py_setting_map': {'conversion': ["Device setting 'mode': remove map; the platform maps a "
                                   "setting's value only for YAML drivers, and a Python driver "
                                   'writes its settings in its own code'],
                    'issues': ["Device setting 'mode': remove map; the platform maps a setting's "
                               'value only for YAML drivers, and a Python driver writes its '
                               'settings in its own code'],
                    'yaml': []},
 'py_setting_map_none': {'conversion': ["Device setting 'mode': remove map; the platform maps a "
                                        "setting's value only for YAML drivers, and a Python "
                                        'driver writes its settings in its own code'],
                         'issues': ["Device setting 'mode': remove map; the platform maps a "
                                    "setting's value only for YAML drivers, and a Python driver "
                                    'writes its settings in its own code'],
                         'yaml': []},
 'py_setting_not_a_mapping': {'conversion': [],
                              'issues': ["device_settings['mode'] must be a mapping"],
                              'yaml': []},
 'py_setting_without_map': {'conversion': [], 'issues': [], 'yaml': []},
 'py_settings_a_list': {'conversion': [],
                        'issues': ['device_settings must be a mapping'],
                        'yaml': []},
 'py_variable': {'conversion': ["State variable 'gain': remove scale, unknown; the platform "
                                'converts values only for YAML drivers, and a Python driver '
                                'converts in its own code'],
                 'issues': ["State variable 'gain': remove scale, unknown; the platform converts "
                            'values only for YAML drivers, and a Python driver converts in its own '
                            'code'],
                 'yaml': []},
 'py_variable_not_a_mapping': {'conversion': [], 'issues': [], 'yaml': []},
 'py_variable_wrong_type': {'conversion': ["State variable 'gain': remove offset; the platform "
                                           'converts values only for YAML drivers, and a Python '
                                           'driver converts in its own code'],
                            'issues': ["State variable 'gain': remove offset; the platform "
                                       'converts values only for YAML drivers, and a Python driver '
                                       'converts in its own code'],
                            'yaml': ["State variable 'gain': the type must be integer, number or "
                                     'float to use offset']}}

RAISES: dict[str, type[BaseException]] = {
    'keys_nested_text_and_bool_unknown': TypeError,
    'keys_text_and_number_unknown_together': TypeError,
}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_issues_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    got = _issues(definition, strict=False)
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [
        m for s, _, m in got if s == "error"
    ]


@pytest.mark.parametrize("case", sorted(STRICT))
def test_strict_reports_the_same_and_then_the_keys(case: str) -> None:
    assert _issues(_definition(case), strict=True) == STRICT_EXPECTED[case]


@pytest.mark.parametrize("case", sorted(STRICT_CASES))
def test_unknown_keys_and_their_tags(case: str) -> None:
    definition = _strict_definition(case)
    assert _tagged(unknown_key_errors(definition)) == UNKNOWN_EXPECTED[case]
    assert _issues(definition, strict=True) == STRICT_EXPECTED[case]
    lenient = [m for _, _, m in _issues(definition, strict=False)]
    strict_messages = [m for _, _, m in STRICT_EXPECTED[case]]
    assert all(m in strict_messages for m in lenient)


@pytest.mark.parametrize("case", sorted(CONVERSION_EXPECTED))
def test_value_conversion_on_both_surfaces(case: str) -> None:
    definition = _definition(case)
    assert {
        "yaml": value_conversion_errors(definition),
        "python": value_conversion_errors(definition, python=True),
    } == CONVERSION_EXPECTED[case]


@pytest.mark.parametrize("case", sorted(PY_CASES))
def test_the_python_surface(case: str) -> None:
    info = {**PY_BASE, **PY_CASES[case]}
    issues = python_driver_info_issues(info)
    conversion = value_conversion_errors(info, python=True)
    assert {"conversion": conversion, "yaml": value_conversion_errors(info)} == {
        k: PY_EXPECTED[case][k] for k in ("conversion", "yaml")
    }
    assert issues[len(issues) - len(conversion):] == conversion
    assert issues == PY_EXPECTED[case]["issues"]


@pytest.mark.parametrize("case", sorted(RAISES))
def test_shapes_that_raise(case: str) -> None:
    definition = {**BASE, **RAISING_CASES[case]}
    with pytest.raises(RAISES[case]):
        unknown_key_errors(definition)
    with pytest.raises(RAISES[case]):
        validate_driver_definition(definition, strict=True)
    validate_driver_definition(definition)


@pytest.mark.parametrize("value", [None, [], "driver", 5])
def test_a_definition_that_is_not_a_mapping(value: Any) -> None:
    assert unknown_key_errors(value) == []
    assert unknown_key_errors(value).tags == []


@pytest.mark.parametrize("actions", [5, "go", {"go": {"params": {"x": {"scale": 1}}}}])
def test_actions_that_are_not_a_list_declare_nothing(actions: Any) -> None:
    assert value_conversion_errors({"actions": actions}) == []
    assert value_conversion_errors({"actions": actions}, python=True) == []


def test_a_python_drivers_settings_are_refused_their_map_in_declared_order() -> None:
    info = {**PY_BASE, "device_settings": {"zeta": {"map": {}}, "alpha": {"map": {"x": "y"}}}}
    tail = "the platform maps a setting's value only for YAML drivers, and a Python driver writes its settings in its own code"
    assert value_conversion_errors(info, python=True) == [
        f"Device setting 'zeta': remove map; {tail}",
        f"Device setting 'alpha': remove map; {tail}",
    ]


def test_value_conversion_takes_a_definition_with_nothing_in_it() -> None:
    assert value_conversion_errors({}) == []
    assert value_conversion_errors({}, python=True) == []


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED) == set(CONVERSION_EXPECTED)
    assert set(STRICT_CASES) == set(UNKNOWN_EXPECTED)
    assert STRICT | set(STRICT_CASES) == set(STRICT_EXPECTED)
    assert set(PY_CASES) == set(PY_EXPECTED)
    assert set(RAISING_CASES) == set(RAISES)
    assert not set(STRICT_CASES) & set(CASES)
