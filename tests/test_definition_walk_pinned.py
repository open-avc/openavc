"""Pins for the walk over a driver definition: which part of the definition
each message belongs to, the order the parts report in, and what each part
accepts as its shape.

``validate_driver_definition`` reads a definition one part at a time (its own
fields, the response rules, the commands, the settings, the discovery block,
the connection's blocks, the actions, the state variables, the framing, the
child types, the query entries, the routing block and the placeholders) and
asks the check module for each. What it decides itself is the order, the tag
each message carries, whether a part that is not the shape it should be is
refused or read as empty, and where the discovery check it is handed runs.

Each case is a set of top-level keys over one driver. The expected value is
every issue ``validate_driver_issues`` reports for the whole definition, in
order, each with its severity and the section tag the Driver Builder anchors
it to, lenient and strict. A few shapes raise, and are pinned by the exception
they raise. The discovery hook, the tagged list itself and the issue records'
fallback are pinned directly.

These record what the walk does today, so a restructuring that keeps the
contract keeps every message, its position and its tag.
"""
from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import (
    _as_issues,
    _TaggedErrors,
    validate_driver_definition,
    validate_driver_issues,
)

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

TEXT_RULE = {"match": "P(\\d)", "set": {"power": "$1"}}

# A rule the then_query checks refuse before its entries are read, and one
# they read: the second's first entry reads again what the rule wrote, its
# second names a config field that is not there, its third is not a query.
THEN_QUERY_REFUSED_JSON = {"json": True, "set": {"power": "power"}, "then_query": ["Q\r"]}
THEN_QUERY_READ = {
    **TEXT_RULE,
    "then_query": [
        {"send": "P?\r", "query_for": "power"},
        {"send": "L?\r", "when": "no_such_field"},
        5,
    ],
}

# One mistake in every part of the definition, in no particular order: the
# walk's order is what the expected value records.
EVERYTHING = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "carrier_pigeon",
    "ir_codes": "yes",
    "responses": [{"match": "("}, THEN_QUERY_READ],
    "commands": {
        "noop": {"send": "NOOP\r"},
        "set": {"send": "SET {levl}\r", "params": {"level": {"type": "integer"}}},
        "bad": 5,
    },
    "command_prefix": 5,
    "command_suffix": 5,
    "config_schema": {"host": {"type": "string", "learned_from": "nope"}},
    "device_settings": {"mode": {"type": "enum", "label": "Mode"}},
    "push": {"type": "nope"},
    "auth": {"type": "nope"},
    "liveness": {"send": 5},
    "actions": [{"id": "", "label": "Go"}],
    "state_variables": {**POWER, "gain": {"type": "string", "label": "G", "scale": 2}, "bad": 5},
    "frame_parser": {"type": "nope"},
    "send_frame": {"type": "nope"},
    "child_entity_types": {"zone": {"id_format": {"type": "nope"}}},
    "polling": {"interval": 5, "queries": [{"send": "Q\r", "when": "nope"}], "when_state": 5},
    "on_connect": [{"send": "C\r", "when": "nope"}],
    "routing": {"planes": 5},
    "min_platform_version": "0.1.0",
    "lable": "typo",
}

CASES: dict[str, dict[str, Any]] = {
    "clean": {},
    # --- the driver's own fields ---------------------------------------------
    "fields_missing_all": {"id": DROP, "name": DROP, "transport": DROP},
    "fields_missing_name": {"name": DROP},
    "transport_unknown": {"transport": "carrier_pigeon"},
    "transport_empty": {"transport": ""},
    "transport_none": {"transport": None},
    "transport_a_list": {"transport": ["tcp"]},
    "transport_a_mapping": {"transport": {"tcp": 1}},
    "transport_bridge": {"transport": "bridge"},
    "ir_codes_text": {"ir_codes": "yes"},
    "ir_codes_number": {"ir_codes": 1},
    "ir_codes_true": {"ir_codes": True},
    "ir_codes_none": {"ir_codes": None},
    # --- each part's shape: left out, null, empty of another shape, wrong ----
    "responses_left_out": {"responses": DROP},
    "responses_none": {"responses": None},
    "responses_empty_mapping": {"responses": {}},
    "responses_empty_text": {"responses": ""},
    "responses_zero": {"responses": 0},
    "responses_text": {"responses": "x"},
    "responses_mapping": {"responses": {"a": 1}},
    "responses_empty": {"responses": []},
    "responses_entry_not_a_mapping": {"responses": [5, TEXT_RULE]},
    "commands_left_out": {"commands": DROP},
    "commands_none": {"commands": None},
    "commands_empty_list": {"commands": []},
    "commands_empty_text": {"commands": ""},
    "commands_text": {"commands": "x"},
    "commands_list": {"commands": [1]},
    "commands_entry_not_a_mapping": {"commands": {"noop": 5}},
    "state_variables_left_out": {"state_variables": DROP},
    "state_variables_none": {"state_variables": None},
    "state_variables_empty_list": {"state_variables": []},
    "state_variables_text": {"state_variables": "x"},
    "state_variables_list": {"state_variables": [1]},
    "state_variables_empty": {"state_variables": {}},
    "state_variable_not_a_mapping": {"state_variables": {**POWER, "gain": "x"}},
    "state_variable_list": {"state_variables": {**POWER, "gain": [1]}},
    "device_settings_none": {"device_settings": None},
    "device_settings_empty_list": {"device_settings": []},
    "device_settings_empty_text": {"device_settings": ""},
    "device_settings_zero": {"device_settings": 0},
    "device_settings_text": {"device_settings": "x"},
    "device_settings_list": {"device_settings": [1]},
    "device_settings_empty": {"device_settings": {}},
    "device_settings_entry": {"device_settings": {"mode": {"type": "enum", "label": "Mode"}}},
    "device_settings_entry_not_a_mapping": {"device_settings": {"mode": 5}},
    "polling_none": {"polling": None},
    "polling_empty_list": {"polling": []},
    "polling_empty_text": {"polling": ""},
    "polling_zero": {"polling": 0},
    "polling_text": {"polling": "x"},
    "polling_text_interval": {"polling": "interval"},
    "polling_list": {"polling": ["interval"]},
    "polling_empty": {"polling": {}},
    "polling_interval": {"polling": {"interval": 5}},
    "polling_queries_text": {"polling": {"queries": "Q\r"}},
    "polling_queries_mapping": {"polling": {"queries": {"send": "Q\r", "when": "nope"}}},
    "polling_queries_entries": {
        "polling": {"queries": ["Q\r", {"send": "Q\r", "when": "nope"}, 5, {"each_child": "nope"}]}
    },
    "polling_when_state_bad": {"polling": {"when_state": 5}},
    "polling_when_state_none": {"polling": {"when_state": None}},
    "polling_everything": {
        "polling": {"interval": 5, "queries": [{"send": "Q\r", "when": "nope"}], "when_state": 5}
    },
    "child_types_none": {"child_entity_types": None},
    "child_types_empty_list": {"child_entity_types": []},
    "child_types_empty_text": {"child_entity_types": ""},
    "child_types_zero": {"child_entity_types": 0},
    "child_types_false": {"child_entity_types": False},
    "child_types_text": {"child_entity_types": "x"},
    "child_types_list": {"child_entity_types": [1]},
    "child_types_true": {"child_entity_types": True},
    "child_types_empty": {"child_entity_types": {}},
    "child_type_bad": {"child_entity_types": {"zone": {"id_format": {"type": "nope"}}}},
    "child_type_two": {
        "child_entity_types": {
            "zone": {"id_format": {"type": "nope"}},
            "output": 5,
        }
    },
    "config_schema_none": {"config_schema": None},
    "config_schema_list": {"config_schema": [{"learned_from": "nope"}]},
    "config_schema_text": {"config_schema": "learned_from"},
    "config_schema_entry_text": {"config_schema": {"host": "learned_from"}},
    "config_schema_learned_from": {
        "config_schema": {
            "host": {"type": "string", "learned_from": "nope"},
            "port": {"type": "integer"},
            "name": {"type": "string", "learned_from": "power"},
        }
    },
    "actions_none": {"actions": None},
    "actions_empty": {"actions": []},
    "actions_empty_mapping": {"actions": {}},
    "actions_empty_text": {"actions": ""},
    "actions_text": {"actions": "go"},
    "actions_mapping": {"actions": {"go": {"id": "go", "label": "Go"}}},
    "actions_entries": {"actions": [5, {"id": "", "label": "Go"}, {"label": "Go", "kind": "nope"}]},
    "on_connect_none": {"on_connect": None},
    "on_connect_text": {"on_connect": "C\r"},
    "on_connect_mapping": {"on_connect": {"send": "C\r", "when": "nope"}},
    "on_connect_entries": {
        "on_connect": [5, {"address": 1}, {"address": "/a", "args": 5}, {"send": "C\r", "when": "nope"}]
    },
    # --- then_query: refused before its entries, or each entry read twice ----
    "then_query_on_json_rule": {"responses": [THEN_QUERY_REFUSED_JSON]},
    "then_query_on_osc_rule": {"responses": [{"address": "/a", "then_query": ["Q\r"]}]},
    "then_query_empty": {"responses": [{**TEXT_RULE, "then_query": []}]},
    "then_query_text": {"responses": [{**TEXT_RULE, "then_query": "Q\r"}]},
    "then_query_none": {"responses": [{**TEXT_RULE, "then_query": None}]},
    "then_query_read": {"responses": [THEN_QUERY_READ]},
    "then_query_child_id": {
        "responses": [{**TEXT_RULE, "then_query": ["Z{child_id}?\r", "Y{child_id}?\r"]}]
    },
    "then_query_three_rules": {
        "responses": [THEN_QUERY_READ, 5, THEN_QUERY_REFUSED_JSON, {**TEXT_RULE}, THEN_QUERY_READ]
    },
    # --- the connection's blocks and the framing -----------------------------
    "push_bad": {"push": {"type": "nope"}},
    "auth_bad": {"auth": {"type": "nope"}},
    "liveness_bad": {"liveness": {"send": 5}},
    "framing_bad": {"frame_parser": {"type": "nope"}, "send_frame": {"type": "nope"}},
    "affixes_bad": {"command_prefix": 5, "command_suffix": 5},
    "routing_bad": {"routing": {"planes": 5}},
    "conversion_on_a_child": {
        "child_entity_types": {
            "zone": {
                "id_format": {"type": "integer", "min": 1, "max": 4},
                "state_variables": {"level": {"type": "string", "label": "L", "scale": 2}},
            }
        }
    },
    # --- placeholders, each tagged from its own message ----------------------
    "placeholders": {
        "commands": {
            "noop": {"send": "NOOP\r"},
            "set": {"send": "SET {levl}\r", "params": {"level": {"type": "integer"}}},
        },
        "responses": [{"match": "P{nope}"}],
        "polling": {"queries": ["Q{nope}\r"]},
        "on_connect": ["C{nope}\r"],
    },
    "placeholder_in_a_command_named_with_a_colon": {
        "commands": {
            "noop": {"send": "NOOP\r"},
            "a:b": {"send": "SET {levl}\r", "params": {"level": {"type": "integer"}}},
        },
    },
    # --- one mistake everywhere: the order the parts report in ----------------
    "everything": EVERYTHING,
    "everything_without_responses_and_child_types": {
        **EVERYTHING, "responses": DROP, "child_entity_types": DROP,
    },
    "everything_with_wrong_shapes": {
        **EVERYTHING,
        "responses": {"a": 1},
        "commands": [1],
        "device_settings": [1],
        "state_variables": [1],
        "child_entity_types": [1],
        "polling": [1],
        "actions": [5],
        "on_connect": "x",
    },
}

# Shapes the walk raises on, with what it raises (the expected value).
RAISING_CASES: dict[str, dict[str, Any]] = {
    "id_a_number": {"id": 5},
    "id_a_list": {"id": ["acme"]},
    "actions_a_number": {"actions": 5},
    "actions_true": {"actions": True},
}


def _definition(case: str, cases: dict[str, dict[str, Any]] = CASES) -> dict[str, Any]:
    definition = dict(BASE)
    for key, value in cases[case].items():
        if value == DROP:
            definition.pop(key, None)
        else:
            definition[key] = value
    return definition


def _issues(definition: Any, *, strict: bool) -> list[tuple[str, str, str]]:
    return [
        (issue["severity"], issue["path"], issue["message"])
        for issue in validate_driver_issues(definition, strict=strict)
    ]


EXPECTED: dict[str, list[tuple[str, str, str]]] = {'actions_empty': [],
 'actions_empty_mapping': [('error', 'actions', 'actions: must be a list')],
 'actions_empty_text': [('error', 'actions', 'actions: must be a list')],
 'actions_entries': [('error', 'actions', 'actions[0]: must be a mapping'),
                     ('error', 'actions', "actions[1]: missing required 'id' (non-empty string)"),
                     ('error', 'actions', "actions[2]: missing required 'id' (non-empty string)"),
                     ('error',
                      'actions',
                      "actions[2]: unknown kind 'nope' (expected one of ['command', 'setup', "
                      "'link'])")],
 'actions_mapping': [('error', 'actions', 'actions: must be a list')],
 'actions_none': [],
 'actions_text': [('error', 'actions', 'actions: must be a list')],
 'affixes_bad': [('error', 'command_prefix', 'command_prefix: must be a string'),
                 ('error', 'command_suffix', 'command_suffix: must be a string')],
 'auth_bad': [('error', 'auth', "auth: unsupported type 'nope' (only 'telnet_login')"),
              ('error', 'auth', "auth: missing required 'username_prompt'"),
              ('error', 'auth', "auth: missing required 'password_prompt'")],
 'child_type_bad': [('error',
                     'child_entity_types.zone',
                     "child_entity_types.zone.id_format: unknown type 'nope' (expected 'integer' "
                     "or 'string')")],
 'child_type_two': [('error',
                     'child_entity_types.zone',
                     "child_entity_types.zone.id_format: unknown type 'nope' (expected 'integer' "
                     "or 'string')"),
                    ('error',
                     'child_entity_types.output',
                     'child_entity_types.output: must be a mapping')],
 'child_types_empty': [],
 'child_types_empty_list': [],
 'child_types_empty_text': [],
 'child_types_false': [],
 'child_types_list': [('error', 'child_entity_types', 'child_entity_types: must be a mapping')],
 'child_types_none': [],
 'child_types_text': [('error', 'child_entity_types', 'child_entity_types: must be a mapping')],
 'child_types_true': [('error', 'child_entity_types', 'child_entity_types: must be a mapping')],
 'child_types_zero': [],
 'clean': [],
 'commands_empty_list': [('error', 'commands', 'commands: must be a mapping')],
 'commands_empty_text': [('error', 'commands', 'commands: must be a mapping')],
 'commands_entry_not_a_mapping': [('error', 'commands.noop', "Command 'noop': must be a dict")],
 'commands_left_out': [],
 'commands_list': [('error', 'commands', 'commands: must be a mapping')],
 'commands_none': [('error', 'commands', 'commands: must be a mapping')],
 'commands_text': [('error', 'commands', 'commands: must be a mapping')],
 'config_schema_entry_text': [],
 'config_schema_learned_from': [('error',
                                 'config_schema.host',
                                 "Config field 'host': a connection field cannot be learned_from a "
                                 'state variable, because a reported value would change how the '
                                 'device is reached'),
                                ('error',
                                 'config_schema.host',
                                 "Config field 'host': learned_from 'nope' is not a declared state "
                                 'variable, so the field would never be filled in')],
 'config_schema_list': [],
 'config_schema_none': [],
 'config_schema_text': [],
 'conversion_on_a_child': [('error',
                            'state_variables',
                            'child_entity_types.zone.state_variables.level: the type must be '
                            'integer, number or float to use scale')],
 'device_settings_empty': [],
 'device_settings_empty_list': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_empty_text': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_entry': [('error',
                            'device_settings.mode',
                            "Device setting 'mode': state_key 'mode' is not a declared state "
                            'variable, so the setting would never read back'),
                           ('error',
                            'device_settings.mode',
                            "Device setting 'mode': missing 'write' block (send / path / address); "
                            'a device setting must be writable')],
 'device_settings_entry_not_a_mapping': [('error',
                                          'device_settings.mode',
                                          "Device setting 'mode': must be a mapping")],
 'device_settings_list': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_none': [],
 'device_settings_text': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_zero': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'everything': [('error', '', 'Unsupported transport: carrier_pigeon'),
                ('error',
                 '',
                 'ir_codes: must be a boolean (the code-set lives in default_config.ir_codes / the '
                 'device config)'),
                ('error',
                 'responses[0]',
                 "Response 0: invalid regex '(': missing ), unterminated subpattern at position 0"),
                ('error', 'commands.bad', "Command 'bad': must be a dict"),
                ('error', 'command_prefix', 'command_prefix: must be a string'),
                ('error', 'command_suffix', 'command_suffix: must be a string'),
                ('error',
                 'config_schema.host',
                 "Config field 'host': a connection field cannot be learned_from a state variable, "
                 'because a reported value would change how the device is reached'),
                ('error',
                 'config_schema.host',
                 "Config field 'host': learned_from 'nope' is not a declared state variable, so "
                 'the field would never be filled in'),
                ('error',
                 'device_settings.mode',
                 "Device setting 'mode': state_key 'mode' is not a declared state variable, so the "
                 'setting would never read back'),
                ('error',
                 'device_settings.mode',
                 "Device setting 'mode': missing 'write' block (send / path / address); a device "
                 'setting must be writable'),
                ('error',
                 'push',
                 "push: missing or unknown 'type' (supported: multicast, sse, tcp_listener, "
                 'http_listener, udp_listener)'),
                ('error', 'auth', "auth: unsupported type 'nope' (only 'telnet_login')"),
                ('error',
                 'auth',
                 'auth: login handshake is only supported on tcp/serial transports, not '
                 "'carrier_pigeon'"),
                ('error', 'auth', "auth: missing required 'username_prompt'"),
                ('error', 'auth', "auth: missing required 'password_prompt'"),
                ('error',
                 'liveness',
                 "liveness: not supported on transport 'carrier_pigeon' (only tcp/serial/udp/osc)"),
                ('error',
                 'liveness',
                 "liveness: missing required 'send' (the probe payload: a raw protocol string, or "
                 'an OSC address on osc)'),
                ('error', 'actions', "actions[0]: missing required 'id' (non-empty string)"),
                ('error', 'state_variables.bad', "State variable 'bad': must be a dict"),
                ('error',
                 'state_variables',
                 "State variable 'gain': the type must be integer, number or float to use scale"),
                ('error',
                 'frame_parser',
                 "frame_parser: unknown type 'nope' (expected 'length_prefix' or 'fixed_length')"),
                ('error',
                 'send_frame',
                 "send_frame: unknown type 'nope' (expected 'length_prefix')"),
                ('error',
                 'child_entity_types.zone',
                 "child_entity_types.zone.id_format: unknown type 'nope' (expected 'integer' or "
                 "'string')"),
                ('error',
                 'polling',
                 'polling.interval is not read by the runtime. Remove the interval: key and set '
                 'default_config.poll_interval instead'),
                ('error',
                 'polling.queries[0]',
                 "polling.queries[0]: 'when' field 'nope' is not a declared config field "
                 '(config_schema / default_config / config_derived)'),
                ('error',
                 'polling',
                 'polling: when_state must map a state variable to the value it must hold, or to a '
                 'list of values'),
                ('error',
                 'on_connect[0]',
                 "on_connect[0]: 'when' field 'nope' is not a declared config field (config_schema "
                 '/ default_config / config_derived)'),
                ('error',
                 'responses[1].then_query[0]',
                 "responses[1].then_query[0]: reads 'power', which this rule has just written. "
                 'then_query reads what the report did not say'),
                ('error',
                 'responses[1].then_query[1]',
                 "responses[1].then_query[1]: 'when' field 'no_such_field' is not a declared "
                 'config field (config_schema / default_config / config_derived)'),
                ('error',
                 'routing',
                 "routing: needs a non-empty 'planes' list, the independent things this device "
                 'routes (usually one; a decoder that routes video, audio and USB separately '
                 'declares three)'),
                ('error',
                 'commands.set.send',
                 "commands.set.send: '{levl}' resolves to nothing (did you mean 'level'?). It "
                 'names no parameter of this command and no config field, so it would be sent to '
                 'the device as written'),
                ('warning',
                 'commands.set.params.level',
                 "Command 'set' parameter 'level' has no label, so forms show its key made "
                 'readable. Give it a label that says what to enter.')],
 'everything_with_wrong_shapes': [('error', '', 'Unsupported transport: carrier_pigeon'),
                                  ('error',
                                   '',
                                   'ir_codes: must be a boolean (the code-set lives in '
                                   'default_config.ir_codes / the device config)'),
                                  ('error', 'responses', 'responses: must be a list'),
                                  ('error', 'commands', 'commands: must be a mapping'),
                                  ('error', 'command_prefix', 'command_prefix: must be a string'),
                                  ('error', 'command_suffix', 'command_suffix: must be a string'),
                                  ('error',
                                   'config_schema.host',
                                   "Config field 'host': a connection field cannot be learned_from "
                                   'a state variable, because a reported value would change how '
                                   'the device is reached'),
                                  ('error',
                                   'config_schema.host',
                                   "Config field 'host': learned_from 'nope' is not a declared "
                                   'state variable, so the field would never be filled in'),
                                  ('error',
                                   'device_settings',
                                   'device_settings: must be a mapping'),
                                  ('error',
                                   'push',
                                   "push: missing or unknown 'type' (supported: multicast, sse, "
                                   'tcp_listener, http_listener, udp_listener)'),
                                  ('error',
                                   'auth',
                                   "auth: unsupported type 'nope' (only 'telnet_login')"),
                                  ('error',
                                   'auth',
                                   'auth: login handshake is only supported on tcp/serial '
                                   "transports, not 'carrier_pigeon'"),
                                  ('error', 'auth', "auth: missing required 'username_prompt'"),
                                  ('error', 'auth', "auth: missing required 'password_prompt'"),
                                  ('error',
                                   'liveness',
                                   "liveness: not supported on transport 'carrier_pigeon' (only "
                                   'tcp/serial/udp/osc)'),
                                  ('error',
                                   'liveness',
                                   "liveness: missing required 'send' (the probe payload: a raw "
                                   'protocol string, or an OSC address on osc)'),
                                  ('error', 'actions', 'actions[0]: must be a mapping'),
                                  ('error',
                                   'state_variables',
                                   'state_variables: must be a mapping'),
                                  ('error',
                                   'frame_parser',
                                   "frame_parser: unknown type 'nope' (expected 'length_prefix' or "
                                   "'fixed_length')"),
                                  ('error',
                                   'send_frame',
                                   "send_frame: unknown type 'nope' (expected 'length_prefix')"),
                                  ('error',
                                   'child_entity_types',
                                   'child_entity_types: must be a mapping'),
                                  ('error', 'polling', 'polling: must be a mapping'),
                                  ('error',
                                   'routing',
                                   "routing: needs a non-empty 'planes' list, the independent "
                                   'things this device routes (usually one; a decoder that routes '
                                   'video, audio and USB separately declares three)')],
 'everything_without_responses_and_child_types': [('error',
                                                   '',
                                                   'Unsupported transport: carrier_pigeon'),
                                                  ('error',
                                                   '',
                                                   'ir_codes: must be a boolean (the code-set '
                                                   'lives in default_config.ir_codes / the device '
                                                   'config)'),
                                                  ('error',
                                                   'commands.bad',
                                                   "Command 'bad': must be a dict"),
                                                  ('error',
                                                   'command_prefix',
                                                   'command_prefix: must be a string'),
                                                  ('error',
                                                   'command_suffix',
                                                   'command_suffix: must be a string'),
                                                  ('error',
                                                   'config_schema.host',
                                                   "Config field 'host': a connection field cannot "
                                                   'be learned_from a state variable, because a '
                                                   'reported value would change how the device is '
                                                   'reached'),
                                                  ('error',
                                                   'config_schema.host',
                                                   "Config field 'host': learned_from 'nope' is "
                                                   'not a declared state variable, so the field '
                                                   'would never be filled in'),
                                                  ('error',
                                                   'device_settings.mode',
                                                   "Device setting 'mode': state_key 'mode' is not "
                                                   'a declared state variable, so the setting '
                                                   'would never read back'),
                                                  ('error',
                                                   'device_settings.mode',
                                                   "Device setting 'mode': missing 'write' block "
                                                   '(send / path / address); a device setting must '
                                                   'be writable'),
                                                  ('error',
                                                   'push',
                                                   "push: missing or unknown 'type' (supported: "
                                                   'multicast, sse, tcp_listener, http_listener, '
                                                   'udp_listener)'),
                                                  ('error',
                                                   'auth',
                                                   "auth: unsupported type 'nope' (only "
                                                   "'telnet_login')"),
                                                  ('error',
                                                   'auth',
                                                   'auth: login handshake is only supported on '
                                                   "tcp/serial transports, not 'carrier_pigeon'"),
                                                  ('error',
                                                   'auth',
                                                   "auth: missing required 'username_prompt'"),
                                                  ('error',
                                                   'auth',
                                                   "auth: missing required 'password_prompt'"),
                                                  ('error',
                                                   'liveness',
                                                   'liveness: not supported on transport '
                                                   "'carrier_pigeon' (only tcp/serial/udp/osc)"),
                                                  ('error',
                                                   'liveness',
                                                   "liveness: missing required 'send' (the probe "
                                                   'payload: a raw protocol string, or an OSC '
                                                   'address on osc)'),
                                                  ('error',
                                                   'actions',
                                                   "actions[0]: missing required 'id' (non-empty "
                                                   'string)'),
                                                  ('error',
                                                   'state_variables.bad',
                                                   "State variable 'bad': must be a dict"),
                                                  ('error',
                                                   'state_variables',
                                                   "State variable 'gain': the type must be "
                                                   'integer, number or float to use scale'),
                                                  ('error',
                                                   'frame_parser',
                                                   "frame_parser: unknown type 'nope' (expected "
                                                   "'length_prefix' or 'fixed_length')"),
                                                  ('error',
                                                   'send_frame',
                                                   "send_frame: unknown type 'nope' (expected "
                                                   "'length_prefix')"),
                                                  ('error',
                                                   'polling',
                                                   'polling.interval is not read by the runtime. '
                                                   'Remove the interval: key and set '
                                                   'default_config.poll_interval instead'),
                                                  ('error',
                                                   'polling.queries[0]',
                                                   "polling.queries[0]: 'when' field 'nope' is not "
                                                   'a declared config field (config_schema / '
                                                   'default_config / config_derived)'),
                                                  ('error',
                                                   'polling',
                                                   'polling: when_state must map a state variable '
                                                   'to the value it must hold, or to a list of '
                                                   'values'),
                                                  ('error',
                                                   'on_connect[0]',
                                                   "on_connect[0]: 'when' field 'nope' is not a "
                                                   'declared config field (config_schema / '
                                                   'default_config / config_derived)'),
                                                  ('error',
                                                   'routing',
                                                   "routing: needs a non-empty 'planes' list, the "
                                                   'independent things this device routes (usually '
                                                   'one; a decoder that routes video, audio and '
                                                   'USB separately declares three)'),
                                                  ('error',
                                                   'commands.set.send',
                                                   "commands.set.send: '{levl}' resolves to "
                                                   "nothing (did you mean 'level'?). It names no "
                                                   'parameter of this command and no config field, '
                                                   'so it would be sent to the device as written'),
                                                  ('warning',
                                                   'commands.set.params.level',
                                                   "Command 'set' parameter 'level' has no label, "
                                                   'so forms show its key made readable. Give it a '
                                                   'label that says what to enter.')],
 'fields_missing_all': [('error', '', 'Missing required field: id'),
                        ('error', '', 'Missing required field: name'),
                        ('error', '', 'Missing required field: transport')],
 'fields_missing_name': [('error', '', 'Missing required field: name')],
 'framing_bad': [('error',
                  'frame_parser',
                  "frame_parser: unknown type 'nope' (expected 'length_prefix' or 'fixed_length')"),
                 ('error',
                  'send_frame',
                  "send_frame: unknown type 'nope' (expected 'length_prefix')")],
 'ir_codes_none': [],
 'ir_codes_number': [('error',
                      '',
                      'ir_codes: must be a boolean (the code-set lives in default_config.ir_codes '
                      '/ the device config)')],
 'ir_codes_text': [('error',
                    '',
                    'ir_codes: must be a boolean (the code-set lives in default_config.ir_codes / '
                    'the device config)')],
 'ir_codes_true': [],
 'liveness_bad': [('error',
                   'liveness',
                   "liveness: missing required 'send' (the probe payload: a raw protocol string, "
                   'or an OSC address on osc)')],
 'on_connect_entries': [('error',
                         'on_connect[3]',
                         "on_connect[3]: 'when' field 'nope' is not a declared config field "
                         '(config_schema / default_config / config_derived)')],
 'on_connect_mapping': [],
 'on_connect_none': [],
 'on_connect_text': [],
 'placeholder_in_a_command_named_with_a_colon': [('error',
                                                  'commands.a',
                                                  "commands.a:b.send: '{levl}' resolves to nothing "
                                                  "(did you mean 'level'?). It names no parameter "
                                                  'of this command and no config field, so it '
                                                  'would be sent to the device as written'),
                                                 ('warning',
                                                  'commands.a:b.params.level',
                                                  "Command 'a:b' parameter 'level' has no label, "
                                                  'so forms show its key made readable. Give it a '
                                                  'label that says what to enter.')],
 'placeholders': [('error',
                   'commands.set.send',
                   "commands.set.send: '{levl}' resolves to nothing (did you mean 'level'?). It "
                   'names no parameter of this command and no config field, so it would be sent to '
                   'the device as written'),
                  ('error',
                   'responses[0].match',
                   "responses[0].match: '{nope}' resolves to nothing. It names no config field "
                   '(only config values substitute here), so the pattern would look for it as '
                   'written'),
                  ('error',
                   'polling.queries[0]',
                   "polling.queries[0]: '{nope}' resolves to nothing. It names no config field "
                   '(only config values substitute here), so it would be sent to the device as '
                   'written'),
                  ('error',
                   'on_connect[0]',
                   "on_connect[0]: '{nope}' resolves to nothing. It names no config field (only "
                   'config values substitute here), so it would be sent to the device as written'),
                  ('warning',
                   'commands.set.params.level',
                   "Command 'set' parameter 'level' has no label, so forms show its key made "
                   'readable. Give it a label that says what to enter.'),
                  ('warning',
                   'liveness',
                   'Nothing notices if the device stops answering over tcp: it sends no poll at '
                   'its default config, and there is no liveness block, so the device reads '
                   'connected for as long as the connection stays open. Add a liveness block that '
                   'asks something the device answers in every state.')],
 'polling_empty': [],
 'polling_empty_list': [('error', 'polling', 'polling: must be a mapping')],
 'polling_empty_text': [('error', 'polling', 'polling: must be a mapping')],
 'polling_everything': [('error',
                         'polling',
                         'polling.interval is not read by the runtime. Remove the interval: key '
                         'and set default_config.poll_interval instead'),
                        ('error',
                         'polling.queries[0]',
                         "polling.queries[0]: 'when' field 'nope' is not a declared config field "
                         '(config_schema / default_config / config_derived)'),
                        ('error',
                         'polling',
                         'polling: when_state must map a state variable to the value it must hold, '
                         'or to a list of values')],
 'polling_interval': [('error',
                       'polling',
                       'polling.interval is not read by the runtime. Remove the interval: key and '
                       'set default_config.poll_interval instead')],
 'polling_list': [('error', 'polling', 'polling: must be a mapping')],
 'polling_none': [],
 'polling_queries_entries': [('error',
                              'polling.queries[1]',
                              "polling.queries[1]: 'when' field 'nope' is not a declared config "
                              'field (config_schema / default_config / config_derived)'),
                             ('error',
                              'polling.queries[3]',
                              "polling.queries[3]: each_child type 'nope' is not a declared "
                              'child_entity_type'),
                             ('error',
                              'polling.queries[3]',
                              "polling.queries[3]: missing 'send' template")],
 'polling_queries_mapping': [],
 'polling_queries_text': [],
 'polling_text': [('error', 'polling', 'polling: must be a mapping')],
 'polling_text_interval': [('error', 'polling', 'polling: must be a mapping')],
 'polling_when_state_bad': [('error',
                             'polling',
                             'polling: when_state must map a state variable to the value it must '
                             'hold, or to a list of values')],
 'polling_when_state_none': [('error',
                              'polling',
                              'polling: when_state must map a state variable to the value it must '
                              'hold, or to a list of values')],
 'polling_zero': [('error', 'polling', 'polling: must be a mapping')],
 'push_bad': [('error',
               'push',
               "push: missing or unknown 'type' (supported: multicast, sse, tcp_listener, "
               'http_listener, udp_listener)')],
 'responses_empty': [],
 'responses_empty_mapping': [('error', 'responses', 'responses: must be a list')],
 'responses_empty_text': [('error', 'responses', 'responses: must be a list')],
 'responses_entry_not_a_mapping': [('error', 'responses[0]', 'Response 0: must be a mapping'),
                                   ('warning',
                                    'liveness',
                                    'Nothing notices if the device stops answering over tcp: it '
                                    'sends no poll at its default config, and there is no liveness '
                                    'block, so the device reads connected for as long as the '
                                    'connection stays open. Add a liveness block that asks '
                                    'something the device answers in every state.')],
 'responses_left_out': [],
 'responses_mapping': [('error', 'responses', 'responses: must be a list')],
 'responses_none': [('error', 'responses', 'responses: must be a list')],
 'responses_text': [('error', 'responses', 'responses: must be a list')],
 'responses_zero': [('error', 'responses', 'responses: must be a list')],
 'routing_bad': [('error',
                  'routing',
                  "routing: needs a non-empty 'planes' list, the independent things this device "
                  'routes (usually one; a decoder that routes video, audio and USB separately '
                  'declares three)')],
 'state_variable_list': [('error',
                          'state_variables.gain',
                          "State variable 'gain': must be a dict")],
 'state_variable_not_a_mapping': [('error',
                                   'state_variables.gain',
                                   "State variable 'gain': must be a dict")],
 'state_variables_empty': [],
 'state_variables_empty_list': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'state_variables_left_out': [],
 'state_variables_list': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'state_variables_none': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'state_variables_text': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'then_query_child_id': [('error',
                          'responses[0].then_query[0]',
                          'responses[0].then_query[0]: {child_id} names the child this rule '
                          'writes, and the rule has no child_set. Route the report to its child '
                          'with child_set, or send the query to every child with each_child'),
                         ('error',
                          'responses[0].then_query[1]',
                          'responses[0].then_query[1]: {child_id} names the child this rule '
                          'writes, and the rule has no child_set. Route the report to its child '
                          'with child_set, or send the query to every child with each_child'),
                         ('warning',
                          'liveness',
                          'Nothing notices if the device stops answering over tcp: it sends no '
                          'poll at its default config, and there is no liveness block, so the '
                          'device reads connected for as long as the connection stays open. Add a '
                          'liveness block that asks something the device answers in every state.')],
 'then_query_empty': [('error',
                       'responses[0]',
                       'Response 0: then_query must list at least one query'),
                      ('warning',
                       'liveness',
                       'Nothing notices if the device stops answering over tcp: it sends no poll '
                       'at its default config, and there is no liveness block, so the device reads '
                       'connected for as long as the connection stays open. Add a liveness block '
                       'that asks something the device answers in every state.')],
 'then_query_none': [('error',
                      'responses[0]',
                      'Response 0: then_query must list at least one query'),
                     ('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'then_query_on_json_rule': [('error',
                              'responses[0]',
                              'Response 0: then_query works on text rules (match:) only'),
                             ('warning',
                              'liveness',
                              'Nothing notices if the device stops answering over tcp: it sends no '
                              'poll at its default config, and there is no liveness block, so the '
                              'device reads connected for as long as the connection stays open. '
                              'Add a liveness block that asks something the device answers in '
                              'every state.')],
 'then_query_on_osc_rule': [('error',
                             'responses[0]',
                             'Response 0: then_query works on text rules (match:) only'),
                            ('warning',
                             'liveness',
                             'Nothing notices if the device stops answering over tcp: it sends no '
                             'poll at its default config, and there is no liveness block, so the '
                             'device reads connected for as long as the connection stays open. Add '
                             'a liveness block that asks something the device answers in every '
                             'state.')],
 'then_query_read': [('error',
                      'responses[0].then_query[0]',
                      "responses[0].then_query[0]: reads 'power', which this rule has just "
                      'written. then_query reads what the report did not say'),
                     ('error',
                      'responses[0].then_query[1]',
                      "responses[0].then_query[1]: 'when' field 'no_such_field' is not a declared "
                      'config field (config_schema / default_config / config_derived)'),
                     ('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'then_query_text': [('error',
                      'responses[0]',
                      'Response 0: then_query must list at least one query'),
                     ('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'then_query_three_rules': [('error', 'responses[1]', 'Response 1: must be a mapping'),
                            ('error',
                             'responses[0].then_query[0]',
                             "responses[0].then_query[0]: reads 'power', which this rule has just "
                             'written. then_query reads what the report did not say'),
                            ('error',
                             'responses[0].then_query[1]',
                             "responses[0].then_query[1]: 'when' field 'no_such_field' is not a "
                             'declared config field (config_schema / default_config / '
                             'config_derived)'),
                            ('error',
                             'responses[2]',
                             'Response 2: then_query works on text rules (match:) only'),
                            ('error',
                             'responses[4].then_query[0]',
                             "responses[4].then_query[0]: reads 'power', which this rule has just "
                             'written. then_query reads what the report did not say'),
                            ('error',
                             'responses[4].then_query[1]',
                             "responses[4].then_query[1]: 'when' field 'no_such_field' is not a "
                             'declared config field (config_schema / default_config / '
                             'config_derived)'),
                            ('warning',
                             'liveness',
                             'Nothing notices if the device stops answering over tcp: it sends no '
                             'poll at its default config, and there is no liveness block, so the '
                             'device reads connected for as long as the connection stays open. Add '
                             'a liveness block that asks something the device answers in every '
                             'state.')],
 'transport_a_list': [('error', '', "Unsupported transport: ['tcp']")],
 'transport_a_mapping': [('error', '', "Unsupported transport: {'tcp': 1}")],
 'transport_bridge': [],
 'transport_empty': [],
 'transport_none': [],
 'transport_unknown': [('error', '', 'Unsupported transport: carrier_pigeon')]}

STRICT_EXPECTED: dict[str, list[tuple[str, str, str]]] = {'actions_empty': [],
 'actions_empty_mapping': [('error', 'actions', 'actions: must be a list')],
 'actions_empty_text': [('error', 'actions', 'actions: must be a list')],
 'actions_entries': [('error', 'actions', 'actions[0]: must be a mapping'),
                     ('error', 'actions', "actions[1]: missing required 'id' (non-empty string)"),
                     ('error', 'actions', "actions[2]: missing required 'id' (non-empty string)"),
                     ('error',
                      'actions',
                      "actions[2]: unknown kind 'nope' (expected one of ['command', 'setup', "
                      "'link'])")],
 'actions_mapping': [('error', 'actions', 'actions: must be a list')],
 'actions_none': [],
 'actions_text': [('error', 'actions', 'actions: must be a list')],
 'affixes_bad': [('error', 'command_prefix', 'command_prefix: must be a string'),
                 ('error', 'command_suffix', 'command_suffix: must be a string')],
 'auth_bad': [('error', 'auth', "auth: unsupported type 'nope' (only 'telnet_login')"),
              ('error', 'auth', "auth: missing required 'username_prompt'"),
              ('error', 'auth', "auth: missing required 'password_prompt'")],
 'child_type_bad': [('error',
                     'child_entity_types.zone',
                     "child_entity_types.zone.id_format: unknown type 'nope' (expected 'integer' "
                     "or 'string')")],
 'child_type_two': [('error',
                     'child_entity_types.zone',
                     "child_entity_types.zone.id_format: unknown type 'nope' (expected 'integer' "
                     "or 'string')"),
                    ('error',
                     'child_entity_types.output',
                     'child_entity_types.output: must be a mapping')],
 'child_types_empty': [],
 'child_types_empty_list': [],
 'child_types_empty_text': [],
 'child_types_false': [],
 'child_types_list': [('error', 'child_entity_types', 'child_entity_types: must be a mapping')],
 'child_types_none': [],
 'child_types_text': [('error', 'child_entity_types', 'child_entity_types: must be a mapping')],
 'child_types_true': [('error', 'child_entity_types', 'child_entity_types: must be a mapping')],
 'child_types_zero': [],
 'clean': [],
 'commands_empty_list': [('error', 'commands', 'commands: must be a mapping')],
 'commands_empty_text': [('error', 'commands', 'commands: must be a mapping')],
 'commands_entry_not_a_mapping': [('error', 'commands.noop', "Command 'noop': must be a dict")],
 'commands_left_out': [],
 'commands_list': [('error', 'commands', 'commands: must be a mapping')],
 'commands_none': [('error', 'commands', 'commands: must be a mapping')],
 'commands_text': [('error', 'commands', 'commands: must be a mapping')],
 'config_schema_entry_text': [],
 'config_schema_learned_from': [('error',
                                 'config_schema.host',
                                 "Config field 'host': a connection field cannot be learned_from a "
                                 'state variable, because a reported value would change how the '
                                 'device is reached'),
                                ('error',
                                 'config_schema.host',
                                 "Config field 'host': learned_from 'nope' is not a declared state "
                                 'variable, so the field would never be filled in')],
 'config_schema_list': [],
 'config_schema_none': [],
 'config_schema_text': [],
 'conversion_on_a_child': [('error',
                            'state_variables',
                            'child_entity_types.zone.state_variables.level: the type must be '
                            'integer, number or float to use scale')],
 'device_settings_empty': [],
 'device_settings_empty_list': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_empty_text': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_entry': [('error',
                            'device_settings.mode',
                            "Device setting 'mode': state_key 'mode' is not a declared state "
                            'variable, so the setting would never read back'),
                           ('error',
                            'device_settings.mode',
                            "Device setting 'mode': missing 'write' block (send / path / address); "
                            'a device setting must be writable')],
 'device_settings_entry_not_a_mapping': [('error',
                                          'device_settings.mode',
                                          "Device setting 'mode': must be a mapping")],
 'device_settings_list': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_none': [],
 'device_settings_text': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'device_settings_zero': [('error', 'device_settings', 'device_settings: must be a mapping')],
 'everything': [('error', '', 'Unsupported transport: carrier_pigeon'),
                ('error',
                 '',
                 'ir_codes: must be a boolean (the code-set lives in default_config.ir_codes / the '
                 'device config)'),
                ('error',
                 'responses[0]',
                 "Response 0: invalid regex '(': missing ), unterminated subpattern at position 0"),
                ('error', 'commands.bad', "Command 'bad': must be a dict"),
                ('error', 'command_prefix', 'command_prefix: must be a string'),
                ('error', 'command_suffix', 'command_suffix: must be a string'),
                ('error',
                 'config_schema.host',
                 "Config field 'host': a connection field cannot be learned_from a state variable, "
                 'because a reported value would change how the device is reached'),
                ('error',
                 'config_schema.host',
                 "Config field 'host': learned_from 'nope' is not a declared state variable, so "
                 'the field would never be filled in'),
                ('error',
                 'device_settings.mode',
                 "Device setting 'mode': state_key 'mode' is not a declared state variable, so the "
                 'setting would never read back'),
                ('error',
                 'device_settings.mode',
                 "Device setting 'mode': missing 'write' block (send / path / address); a device "
                 'setting must be writable'),
                ('error',
                 'push',
                 "push: missing or unknown 'type' (supported: multicast, sse, tcp_listener, "
                 'http_listener, udp_listener)'),
                ('error', 'auth', "auth: unsupported type 'nope' (only 'telnet_login')"),
                ('error',
                 'auth',
                 'auth: login handshake is only supported on tcp/serial transports, not '
                 "'carrier_pigeon'"),
                ('error', 'auth', "auth: missing required 'username_prompt'"),
                ('error', 'auth', "auth: missing required 'password_prompt'"),
                ('error',
                 'liveness',
                 "liveness: not supported on transport 'carrier_pigeon' (only tcp/serial/udp/osc)"),
                ('error',
                 'liveness',
                 "liveness: missing required 'send' (the probe payload: a raw protocol string, or "
                 'an OSC address on osc)'),
                ('error', 'actions', "actions[0]: missing required 'id' (non-empty string)"),
                ('error', 'state_variables.bad', "State variable 'bad': must be a dict"),
                ('error',
                 'state_variables',
                 "State variable 'gain': the type must be integer, number or float to use scale"),
                ('error',
                 'frame_parser',
                 "frame_parser: unknown type 'nope' (expected 'length_prefix' or 'fixed_length')"),
                ('error',
                 'send_frame',
                 "send_frame: unknown type 'nope' (expected 'length_prefix')"),
                ('error',
                 'child_entity_types.zone',
                 "child_entity_types.zone.id_format: unknown type 'nope' (expected 'integer' or "
                 "'string')"),
                ('error',
                 'polling',
                 'polling.interval is not read by the runtime. Remove the interval: key and set '
                 'default_config.poll_interval instead'),
                ('error',
                 'polling.queries[0]',
                 "polling.queries[0]: 'when' field 'nope' is not a declared config field "
                 '(config_schema / default_config / config_derived)'),
                ('error',
                 'polling',
                 'polling: when_state must map a state variable to the value it must hold, or to a '
                 'list of values'),
                ('error',
                 'on_connect[0]',
                 "on_connect[0]: 'when' field 'nope' is not a declared config field (config_schema "
                 '/ default_config / config_derived)'),
                ('error',
                 'responses[1].then_query[0]',
                 "responses[1].then_query[0]: reads 'power', which this rule has just written. "
                 'then_query reads what the report did not say'),
                ('error',
                 'responses[1].then_query[1]',
                 "responses[1].then_query[1]: 'when' field 'no_such_field' is not a declared "
                 'config field (config_schema / default_config / config_derived)'),
                ('error',
                 'routing',
                 "routing: needs a non-empty 'planes' list, the independent things this device "
                 'routes (usually one; a decoder that routes video, audio and USB separately '
                 'declares three)'),
                ('error',
                 'commands.set.send',
                 "commands.set.send: '{levl}' resolves to nothing (did you mean 'level'?). It "
                 'names no parameter of this command and no config field, so it would be sent to '
                 'the device as written'),
                ('error', '', "unknown key 'lable'"),
                ('error', 'polling', "polling: unknown key 'interval'"),
                ('error',
                 'min_platform_version',
                 'min_platform_version is "0.1.0", but this driver uses polling.when_state, '
                 'state_variables.gain.scale, which needs platform 0.37.0. Raise it to "0.37.0", '
                 'or drop those fields if the driver has to keep installing on 0.1.0.'),
                ('warning',
                 'commands.set.params.level',
                 "Command 'set' parameter 'level' has no label, so forms show its key made "
                 'readable. Give it a label that says what to enter.')],
 'everything_with_wrong_shapes': [('error', '', 'Unsupported transport: carrier_pigeon'),
                                  ('error',
                                   '',
                                   'ir_codes: must be a boolean (the code-set lives in '
                                   'default_config.ir_codes / the device config)'),
                                  ('error', 'responses', 'responses: must be a list'),
                                  ('error', 'commands', 'commands: must be a mapping'),
                                  ('error', 'command_prefix', 'command_prefix: must be a string'),
                                  ('error', 'command_suffix', 'command_suffix: must be a string'),
                                  ('error',
                                   'config_schema.host',
                                   "Config field 'host': a connection field cannot be learned_from "
                                   'a state variable, because a reported value would change how '
                                   'the device is reached'),
                                  ('error',
                                   'config_schema.host',
                                   "Config field 'host': learned_from 'nope' is not a declared "
                                   'state variable, so the field would never be filled in'),
                                  ('error',
                                   'device_settings',
                                   'device_settings: must be a mapping'),
                                  ('error',
                                   'push',
                                   "push: missing or unknown 'type' (supported: multicast, sse, "
                                   'tcp_listener, http_listener, udp_listener)'),
                                  ('error',
                                   'auth',
                                   "auth: unsupported type 'nope' (only 'telnet_login')"),
                                  ('error',
                                   'auth',
                                   'auth: login handshake is only supported on tcp/serial '
                                   "transports, not 'carrier_pigeon'"),
                                  ('error', 'auth', "auth: missing required 'username_prompt'"),
                                  ('error', 'auth', "auth: missing required 'password_prompt'"),
                                  ('error',
                                   'liveness',
                                   "liveness: not supported on transport 'carrier_pigeon' (only "
                                   'tcp/serial/udp/osc)'),
                                  ('error',
                                   'liveness',
                                   "liveness: missing required 'send' (the probe payload: a raw "
                                   'protocol string, or an OSC address on osc)'),
                                  ('error', 'actions', 'actions[0]: must be a mapping'),
                                  ('error',
                                   'state_variables',
                                   'state_variables: must be a mapping'),
                                  ('error',
                                   'frame_parser',
                                   "frame_parser: unknown type 'nope' (expected 'length_prefix' or "
                                   "'fixed_length')"),
                                  ('error',
                                   'send_frame',
                                   "send_frame: unknown type 'nope' (expected 'length_prefix')"),
                                  ('error',
                                   'child_entity_types',
                                   'child_entity_types: must be a mapping'),
                                  ('error', 'polling', 'polling: must be a mapping'),
                                  ('error',
                                   'routing',
                                   "routing: needs a non-empty 'planes' list, the independent "
                                   'things this device routes (usually one; a decoder that routes '
                                   'video, audio and USB separately declares three)'),
                                  ('error', '', "unknown key 'lable'"),
                                  ('error',
                                   'min_platform_version',
                                   'min_platform_version is "0.1.0", but this driver uses routing, '
                                   'which needs platform 0.27.0. Raise it to "0.27.0", or drop '
                                   'that field if the driver has to keep installing on 0.1.0.')],
 'everything_without_responses_and_child_types': [('error',
                                                   '',
                                                   'Unsupported transport: carrier_pigeon'),
                                                  ('error',
                                                   '',
                                                   'ir_codes: must be a boolean (the code-set '
                                                   'lives in default_config.ir_codes / the device '
                                                   'config)'),
                                                  ('error',
                                                   'commands.bad',
                                                   "Command 'bad': must be a dict"),
                                                  ('error',
                                                   'command_prefix',
                                                   'command_prefix: must be a string'),
                                                  ('error',
                                                   'command_suffix',
                                                   'command_suffix: must be a string'),
                                                  ('error',
                                                   'config_schema.host',
                                                   "Config field 'host': a connection field cannot "
                                                   'be learned_from a state variable, because a '
                                                   'reported value would change how the device is '
                                                   'reached'),
                                                  ('error',
                                                   'config_schema.host',
                                                   "Config field 'host': learned_from 'nope' is "
                                                   'not a declared state variable, so the field '
                                                   'would never be filled in'),
                                                  ('error',
                                                   'device_settings.mode',
                                                   "Device setting 'mode': state_key 'mode' is not "
                                                   'a declared state variable, so the setting '
                                                   'would never read back'),
                                                  ('error',
                                                   'device_settings.mode',
                                                   "Device setting 'mode': missing 'write' block "
                                                   '(send / path / address); a device setting must '
                                                   'be writable'),
                                                  ('error',
                                                   'push',
                                                   "push: missing or unknown 'type' (supported: "
                                                   'multicast, sse, tcp_listener, http_listener, '
                                                   'udp_listener)'),
                                                  ('error',
                                                   'auth',
                                                   "auth: unsupported type 'nope' (only "
                                                   "'telnet_login')"),
                                                  ('error',
                                                   'auth',
                                                   'auth: login handshake is only supported on '
                                                   "tcp/serial transports, not 'carrier_pigeon'"),
                                                  ('error',
                                                   'auth',
                                                   "auth: missing required 'username_prompt'"),
                                                  ('error',
                                                   'auth',
                                                   "auth: missing required 'password_prompt'"),
                                                  ('error',
                                                   'liveness',
                                                   'liveness: not supported on transport '
                                                   "'carrier_pigeon' (only tcp/serial/udp/osc)"),
                                                  ('error',
                                                   'liveness',
                                                   "liveness: missing required 'send' (the probe "
                                                   'payload: a raw protocol string, or an OSC '
                                                   'address on osc)'),
                                                  ('error',
                                                   'actions',
                                                   "actions[0]: missing required 'id' (non-empty "
                                                   'string)'),
                                                  ('error',
                                                   'state_variables.bad',
                                                   "State variable 'bad': must be a dict"),
                                                  ('error',
                                                   'state_variables',
                                                   "State variable 'gain': the type must be "
                                                   'integer, number or float to use scale'),
                                                  ('error',
                                                   'frame_parser',
                                                   "frame_parser: unknown type 'nope' (expected "
                                                   "'length_prefix' or 'fixed_length')"),
                                                  ('error',
                                                   'send_frame',
                                                   "send_frame: unknown type 'nope' (expected "
                                                   "'length_prefix')"),
                                                  ('error',
                                                   'polling',
                                                   'polling.interval is not read by the runtime. '
                                                   'Remove the interval: key and set '
                                                   'default_config.poll_interval instead'),
                                                  ('error',
                                                   'polling.queries[0]',
                                                   "polling.queries[0]: 'when' field 'nope' is not "
                                                   'a declared config field (config_schema / '
                                                   'default_config / config_derived)'),
                                                  ('error',
                                                   'polling',
                                                   'polling: when_state must map a state variable '
                                                   'to the value it must hold, or to a list of '
                                                   'values'),
                                                  ('error',
                                                   'on_connect[0]',
                                                   "on_connect[0]: 'when' field 'nope' is not a "
                                                   'declared config field (config_schema / '
                                                   'default_config / config_derived)'),
                                                  ('error',
                                                   'routing',
                                                   "routing: needs a non-empty 'planes' list, the "
                                                   'independent things this device routes (usually '
                                                   'one; a decoder that routes video, audio and '
                                                   'USB separately declares three)'),
                                                  ('error',
                                                   'commands.set.send',
                                                   "commands.set.send: '{levl}' resolves to "
                                                   "nothing (did you mean 'level'?). It names no "
                                                   'parameter of this command and no config field, '
                                                   'so it would be sent to the device as written'),
                                                  ('error', '', "unknown key 'lable'"),
                                                  ('error',
                                                   'polling',
                                                   "polling: unknown key 'interval'"),
                                                  ('error',
                                                   'min_platform_version',
                                                   'min_platform_version is "0.1.0", but this '
                                                   'driver uses polling.when_state, '
                                                   'state_variables.gain.scale, which needs '
                                                   'platform 0.37.0. Raise it to "0.37.0", or drop '
                                                   'those fields if the driver has to keep '
                                                   'installing on 0.1.0.'),
                                                  ('warning',
                                                   'commands.set.params.level',
                                                   "Command 'set' parameter 'level' has no label, "
                                                   'so forms show its key made readable. Give it a '
                                                   'label that says what to enter.')],
 'fields_missing_all': [('error', '', 'Missing required field: id'),
                        ('error', '', 'Missing required field: name'),
                        ('error', '', 'Missing required field: transport')],
 'fields_missing_name': [('error', '', 'Missing required field: name')],
 'framing_bad': [('error',
                  'frame_parser',
                  "frame_parser: unknown type 'nope' (expected 'length_prefix' or 'fixed_length')"),
                 ('error',
                  'send_frame',
                  "send_frame: unknown type 'nope' (expected 'length_prefix')")],
 'ir_codes_none': [],
 'ir_codes_number': [('error',
                      '',
                      'ir_codes: must be a boolean (the code-set lives in default_config.ir_codes '
                      '/ the device config)')],
 'ir_codes_text': [('error',
                    '',
                    'ir_codes: must be a boolean (the code-set lives in default_config.ir_codes / '
                    'the device config)')],
 'ir_codes_true': [],
 'liveness_bad': [('error',
                   'liveness',
                   "liveness: missing required 'send' (the probe payload: a raw protocol string, "
                   'or an OSC address on osc)')],
 'on_connect_entries': [('error',
                         'on_connect[3]',
                         "on_connect[3]: 'when' field 'nope' is not a declared config field "
                         '(config_schema / default_config / config_derived)')],
 'on_connect_mapping': [],
 'on_connect_none': [],
 'on_connect_text': [],
 'placeholder_in_a_command_named_with_a_colon': [('error',
                                                  'commands.a',
                                                  "commands.a:b.send: '{levl}' resolves to nothing "
                                                  "(did you mean 'level'?). It names no parameter "
                                                  'of this command and no config field, so it '
                                                  'would be sent to the device as written'),
                                                 ('warning',
                                                  'commands.a:b.params.level',
                                                  "Command 'a:b' parameter 'level' has no label, "
                                                  'so forms show its key made readable. Give it a '
                                                  'label that says what to enter.')],
 'placeholders': [('error',
                   'commands.set.send',
                   "commands.set.send: '{levl}' resolves to nothing (did you mean 'level'?). It "
                   'names no parameter of this command and no config field, so it would be sent to '
                   'the device as written'),
                  ('error',
                   'responses[0].match',
                   "responses[0].match: '{nope}' resolves to nothing. It names no config field "
                   '(only config values substitute here), so the pattern would look for it as '
                   'written'),
                  ('error',
                   'polling.queries[0]',
                   "polling.queries[0]: '{nope}' resolves to nothing. It names no config field "
                   '(only config values substitute here), so it would be sent to the device as '
                   'written'),
                  ('error',
                   'on_connect[0]',
                   "on_connect[0]: '{nope}' resolves to nothing. It names no config field (only "
                   'config values substitute here), so it would be sent to the device as written'),
                  ('warning',
                   'commands.set.params.level',
                   "Command 'set' parameter 'level' has no label, so forms show its key made "
                   'readable. Give it a label that says what to enter.'),
                  ('warning',
                   'liveness',
                   'Nothing notices if the device stops answering over tcp: it sends no poll at '
                   'its default config, and there is no liveness block, so the device reads '
                   'connected for as long as the connection stays open. Add a liveness block that '
                   'asks something the device answers in every state.')],
 'polling_empty': [],
 'polling_empty_list': [('error', 'polling', 'polling: must be a mapping')],
 'polling_empty_text': [('error', 'polling', 'polling: must be a mapping')],
 'polling_everything': [('error',
                         'polling',
                         'polling.interval is not read by the runtime. Remove the interval: key '
                         'and set default_config.poll_interval instead'),
                        ('error',
                         'polling.queries[0]',
                         "polling.queries[0]: 'when' field 'nope' is not a declared config field "
                         '(config_schema / default_config / config_derived)'),
                        ('error',
                         'polling',
                         'polling: when_state must map a state variable to the value it must hold, '
                         'or to a list of values'),
                        ('error', 'polling', "polling: unknown key 'interval'")],
 'polling_interval': [('error',
                       'polling',
                       'polling.interval is not read by the runtime. Remove the interval: key and '
                       'set default_config.poll_interval instead'),
                      ('error', 'polling', "polling: unknown key 'interval'")],
 'polling_list': [('error', 'polling', 'polling: must be a mapping')],
 'polling_none': [],
 'polling_queries_entries': [('error',
                              'polling.queries[1]',
                              "polling.queries[1]: 'when' field 'nope' is not a declared config "
                              'field (config_schema / default_config / config_derived)'),
                             ('error',
                              'polling.queries[3]',
                              "polling.queries[3]: each_child type 'nope' is not a declared "
                              'child_entity_type'),
                             ('error',
                              'polling.queries[3]',
                              "polling.queries[3]: missing 'send' template")],
 'polling_queries_mapping': [],
 'polling_queries_text': [],
 'polling_text': [('error', 'polling', 'polling: must be a mapping')],
 'polling_text_interval': [('error', 'polling', 'polling: must be a mapping')],
 'polling_when_state_bad': [('error',
                             'polling',
                             'polling: when_state must map a state variable to the value it must '
                             'hold, or to a list of values')],
 'polling_when_state_none': [('error',
                              'polling',
                              'polling: when_state must map a state variable to the value it must '
                              'hold, or to a list of values')],
 'polling_zero': [('error', 'polling', 'polling: must be a mapping')],
 'push_bad': [('error',
               'push',
               "push: missing or unknown 'type' (supported: multicast, sse, tcp_listener, "
               'http_listener, udp_listener)')],
 'responses_empty': [],
 'responses_empty_mapping': [('error', 'responses', 'responses: must be a list')],
 'responses_empty_text': [('error', 'responses', 'responses: must be a list')],
 'responses_entry_not_a_mapping': [('error', 'responses[0]', 'Response 0: must be a mapping'),
                                   ('warning',
                                    'liveness',
                                    'Nothing notices if the device stops answering over tcp: it '
                                    'sends no poll at its default config, and there is no liveness '
                                    'block, so the device reads connected for as long as the '
                                    'connection stays open. Add a liveness block that asks '
                                    'something the device answers in every state.')],
 'responses_left_out': [],
 'responses_mapping': [('error', 'responses', 'responses: must be a list')],
 'responses_none': [('error', 'responses', 'responses: must be a list')],
 'responses_text': [('error', 'responses', 'responses: must be a list')],
 'responses_zero': [('error', 'responses', 'responses: must be a list')],
 'routing_bad': [('error',
                  'routing',
                  "routing: needs a non-empty 'planes' list, the independent things this device "
                  'routes (usually one; a decoder that routes video, audio and USB separately '
                  'declares three)')],
 'state_variable_list': [('error',
                          'state_variables.gain',
                          "State variable 'gain': must be a dict")],
 'state_variable_not_a_mapping': [('error',
                                   'state_variables.gain',
                                   "State variable 'gain': must be a dict")],
 'state_variables_empty': [],
 'state_variables_empty_list': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'state_variables_left_out': [],
 'state_variables_list': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'state_variables_none': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'state_variables_text': [('error', 'state_variables', 'state_variables: must be a mapping')],
 'then_query_child_id': [('error',
                          'responses[0].then_query[0]',
                          'responses[0].then_query[0]: {child_id} names the child this rule '
                          'writes, and the rule has no child_set. Route the report to its child '
                          'with child_set, or send the query to every child with each_child'),
                         ('error',
                          'responses[0].then_query[1]',
                          'responses[0].then_query[1]: {child_id} names the child this rule '
                          'writes, and the rule has no child_set. Route the report to its child '
                          'with child_set, or send the query to every child with each_child'),
                         ('warning',
                          'liveness',
                          'Nothing notices if the device stops answering over tcp: it sends no '
                          'poll at its default config, and there is no liveness block, so the '
                          'device reads connected for as long as the connection stays open. Add a '
                          'liveness block that asks something the device answers in every state.')],
 'then_query_empty': [('error',
                       'responses[0]',
                       'Response 0: then_query must list at least one query'),
                      ('warning',
                       'liveness',
                       'Nothing notices if the device stops answering over tcp: it sends no poll '
                       'at its default config, and there is no liveness block, so the device reads '
                       'connected for as long as the connection stays open. Add a liveness block '
                       'that asks something the device answers in every state.')],
 'then_query_none': [('error',
                      'responses[0]',
                      'Response 0: then_query must list at least one query'),
                     ('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'then_query_on_json_rule': [('error',
                              'responses[0]',
                              'Response 0: then_query works on text rules (match:) only'),
                             ('warning',
                              'liveness',
                              'Nothing notices if the device stops answering over tcp: it sends no '
                              'poll at its default config, and there is no liveness block, so the '
                              'device reads connected for as long as the connection stays open. '
                              'Add a liveness block that asks something the device answers in '
                              'every state.')],
 'then_query_on_osc_rule': [('error',
                             'responses[0]',
                             'Response 0: then_query works on text rules (match:) only'),
                            ('warning',
                             'liveness',
                             'Nothing notices if the device stops answering over tcp: it sends no '
                             'poll at its default config, and there is no liveness block, so the '
                             'device reads connected for as long as the connection stays open. Add '
                             'a liveness block that asks something the device answers in every '
                             'state.')],
 'then_query_read': [('error',
                      'responses[0].then_query[0]',
                      "responses[0].then_query[0]: reads 'power', which this rule has just "
                      'written. then_query reads what the report did not say'),
                     ('error',
                      'responses[0].then_query[1]',
                      "responses[0].then_query[1]: 'when' field 'no_such_field' is not a declared "
                      'config field (config_schema / default_config / config_derived)'),
                     ('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'then_query_text': [('error',
                      'responses[0]',
                      'Response 0: then_query must list at least one query'),
                     ('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'then_query_three_rules': [('error', 'responses[1]', 'Response 1: must be a mapping'),
                            ('error',
                             'responses[0].then_query[0]',
                             "responses[0].then_query[0]: reads 'power', which this rule has just "
                             'written. then_query reads what the report did not say'),
                            ('error',
                             'responses[0].then_query[1]',
                             "responses[0].then_query[1]: 'when' field 'no_such_field' is not a "
                             'declared config field (config_schema / default_config / '
                             'config_derived)'),
                            ('error',
                             'responses[2]',
                             'Response 2: then_query works on text rules (match:) only'),
                            ('error',
                             'responses[4].then_query[0]',
                             "responses[4].then_query[0]: reads 'power', which this rule has just "
                             'written. then_query reads what the report did not say'),
                            ('error',
                             'responses[4].then_query[1]',
                             "responses[4].then_query[1]: 'when' field 'no_such_field' is not a "
                             'declared config field (config_schema / default_config / '
                             'config_derived)'),
                            ('warning',
                             'liveness',
                             'Nothing notices if the device stops answering over tcp: it sends no '
                             'poll at its default config, and there is no liveness block, so the '
                             'device reads connected for as long as the connection stays open. Add '
                             'a liveness block that asks something the device answers in every '
                             'state.')],
 'transport_a_list': [('error', '', "Unsupported transport: ['tcp']")],
 'transport_a_mapping': [('error', '', "Unsupported transport: {'tcp': 1}")],
 'transport_bridge': [],
 'transport_empty': [],
 'transport_none': [],
 'transport_unknown': [('error', '', 'Unsupported transport: carrier_pigeon')]}

RAISES: dict[str, type[BaseException]] = {
    'actions_a_number': TypeError,
    'actions_true': TypeError,
    'id_a_list': AttributeError,
    'id_a_number': AttributeError,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_issues_their_order_and_their_tags(case: str) -> None:
    assert _issues(_definition(case), strict=False) == EXPECTED[case]


@pytest.mark.parametrize("case", sorted(CASES))
def test_strict_reports_the_same_and_then_the_keys(case: str) -> None:
    assert _issues(_definition(case), strict=True) == STRICT_EXPECTED[case]


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_error_list_is_a_list_with_a_tag_for_each_message(case: str) -> None:
    for strict in (False, True):
        errors = validate_driver_definition(_definition(case), strict=strict)
        assert type(errors).__name__ == "_TaggedErrors"
        assert isinstance(errors, list)
        expected = [m for s, _, m in (STRICT_EXPECTED if strict else EXPECTED)[case] if s == "error"]
        assert list(errors) == expected
        assert errors.tags == [  # type: ignore[attr-defined]
            p for s, p, _ in (STRICT_EXPECTED if strict else EXPECTED)[case] if s == "error"
        ]


@pytest.mark.parametrize("case", sorted(RAISING_CASES))
def test_shapes_that_raise(case: str) -> None:
    definition = _definition(case, RAISING_CASES)
    for strict in (False, True):
        with pytest.raises(RAISES[case]):
            validate_driver_definition(definition, strict=strict)
        with pytest.raises(RAISES[case]):
            validate_driver_issues(definition, strict=strict)


@pytest.mark.parametrize("value", [None, [], "driver", 5, ("id",)])
def test_a_definition_that_is_not_a_mapping(value: Any) -> None:
    for strict in (False, True):
        errors = validate_driver_definition(value, strict=strict)
        assert list(errors) == ["Driver definition must be a mapping"]
        assert errors.tags == [""]  # type: ignore[attr-defined]
        assert validate_driver_issues(value, strict=strict) == [
            {"severity": "error", "message": "Driver definition must be a mapping", "path": ""}
        ]


# --- the discovery check the walk is handed ----------------------------------


class _Recorder:
    def __init__(self, answer: list[str]) -> None:
        self.answer = answer
        self.seen: list[Any] = []

    def __call__(self, definition: dict[str, Any]) -> list[str]:
        self.seen.append(definition)
        return list(self.answer)


def test_the_discovery_check_runs_after_the_settings_and_before_the_push_block() -> None:
    definition = _definition("everything")
    hook = _Recorder(["discovery: from the hook", "discovery: and again"])
    errors = validate_driver_definition(definition, discovery_validator=hook)
    assert hook.seen == [definition] and hook.seen[0] is definition
    tagged = list(zip(errors.tags, errors))  # type: ignore[attr-defined]
    at = tagged.index(("discovery", "discovery: from the hook"))
    assert tagged[at + 1] == ("discovery", "discovery: and again")
    before = [tag for tag, _ in tagged[:at]]
    after = [tag for tag, _ in tagged[at + 2:]]
    assert before[-1] == "device_settings.mode"
    assert after[0] == "push"
    without = list(zip(
        validate_driver_definition(definition).tags,  # type: ignore[attr-defined]
        validate_driver_definition(definition),
    ))
    assert tagged[:at] + tagged[at + 2:] == without


def test_the_discovery_check_runs_on_the_lenient_and_strict_walk_alike() -> None:
    hook = _Recorder(["discovery: x"])
    strict = validate_driver_definition(_definition("clean"), discovery_validator=hook, strict=True)
    lenient = validate_driver_definition(_definition("clean"), discovery_validator=hook)
    assert list(strict) == list(lenient) == ["discovery: x"]
    assert strict.tags == lenient.tags == ["discovery"]  # type: ignore[attr-defined]
    assert len(hook.seen) == 2


@pytest.mark.parametrize("driver_id", ["generic_tcp", "generic_", "generic_anything"])
def test_a_template_is_not_handed_to_the_discovery_check(driver_id: str) -> None:
    hook = _Recorder(["discovery: x"])
    errors = validate_driver_definition({**BASE, "id": driver_id}, discovery_validator=hook)
    assert hook.seen == []
    assert list(errors) == []


@pytest.mark.parametrize(
    "over", [{"id": ""}, {"id": None}, {"id": DROP}, {"id": "acme_generic_tcp"}, {"id": "Generic_tcp"}]
)
def test_anything_else_is_handed_to_the_discovery_check(over: dict[str, Any]) -> None:
    hook = _Recorder(["discovery: x"])
    definition = dict(BASE)
    for key, value in over.items():
        if value == DROP:
            definition.pop(key)
        else:
            definition[key] = value
    errors = validate_driver_definition(definition, discovery_validator=hook)
    assert hook.seen == [definition]
    assert "discovery: x" in list(errors)
    assert errors.tags[list(errors).index("discovery: x")] == "discovery"  # type: ignore[attr-defined]


def test_a_discovery_check_that_raises_is_not_caught() -> None:
    def hook(definition: dict[str, Any]) -> list[str]:
        raise RuntimeError("discovery parser broke")

    with pytest.raises(RuntimeError, match="discovery parser broke"):
        validate_driver_definition(_definition("everything"), discovery_validator=hook)
    with pytest.raises(RuntimeError, match="discovery parser broke"):
        validate_driver_issues(_definition("clean"), discovery_validator=hook)


def test_a_number_for_an_id_raises_with_or_without_a_discovery_check() -> None:
    hook = _Recorder([])
    with pytest.raises(AttributeError):
        validate_driver_definition({**BASE, "id": 5})
    with pytest.raises(AttributeError):
        validate_driver_definition({**BASE, "id": 5}, discovery_validator=hook)
    assert hook.seen == []


def test_a_discovery_check_message_is_carried_by_the_issue_records() -> None:
    hook = _Recorder(["discovery: x"])
    issues = validate_driver_issues(_definition("clean"), discovery_validator=hook, strict=True)
    assert issues == [{"severity": "error", "message": "discovery: x", "path": "discovery"}]


# --- the tagged list ---------------------------------------------------------


def test_a_tagged_list_built_with_entries_tags_them_with_nothing() -> None:
    errors = _TaggedErrors(["a", "b"])
    assert list(errors) == ["a", "b"]
    assert errors.tags == ["", ""]
    assert errors.ctx == ""
    assert _TaggedErrors().tags == []


def test_an_entry_takes_the_place_set_when_it_was_added() -> None:
    errors = _TaggedErrors()
    errors.append("top")
    errors.ctx = "commands"
    errors.append("one")
    errors.extend(["two", "three"])
    errors.ctx = "push"
    errors.extend(iter(["four"]))
    errors.extend(m for m in ["five"])
    errors.extend(())
    assert list(errors) == ["top", "one", "two", "three", "four", "five"]
    assert errors.tags == ["", "commands", "commands", "commands", "push", "push"]


def test_a_tagged_sub_list_keeps_its_own_tags_when_they_match_its_length() -> None:
    inner = _TaggedErrors()
    inner.ctx = "a.b"
    inner.append("x")
    inner.ctx = "a.c"
    inner.append("y")
    outer = _TaggedErrors()
    outer.ctx = "outer"
    outer.extend(inner)
    assert list(outer) == ["x", "y"]
    assert outer.tags == ["a.b", "a.c"]


def test_a_sub_list_whose_tags_do_not_match_its_length_takes_the_place_set() -> None:
    class Odd(list):
        tags = ["only one"]

    outer = _TaggedErrors()
    outer.ctx = "outer"
    outer.extend(Odd(["x", "y"]))
    assert outer.tags == ["outer", "outer"]
    short = _TaggedErrors(["x", "y"])
    short.tags = ["t"]
    outer.extend(short)
    assert outer.tags == ["outer", "outer", "outer", "outer"]


def test_an_issue_record_with_no_tags_or_the_wrong_number_has_an_empty_path() -> None:
    assert _as_issues(["a", "b"], "warning") == [
        {"severity": "warning", "message": "a", "path": ""},
        {"severity": "warning", "message": "b", "path": ""},
    ]
    short = _TaggedErrors(["a", "b"])
    short.tags = ["only"]
    assert [i["path"] for i in _as_issues(short, "error")] == ["", ""]
    tagged = _TaggedErrors()
    tagged.ctx = "commands"
    tagged.append("a")
    assert _as_issues(tagged, "error") == [{"severity": "error", "message": "a", "path": "commands"}]
    assert _as_issues([], "error") == []
