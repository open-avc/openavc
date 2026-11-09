"""Pins for the ``push:`` block's load-time checks, through the public door.

Each case is one push block (plus any top-level keys it changes) on a driver
that declares commands to register with, two child types and one config field
in each config source. The expected value is every error
``validate_driver_issues`` reports for the whole definition, in order, each
with its section tag. The cases reach the block's type and keys, each type's
own addressing (multicast's group and port, udp_listener's and tcp_listener's
port, sse's paths, idle timeout and session, tcp_listener's frame parser), a
``{config_field}`` template in any of them, and ``register:`` / ``unregister:``.

These record what the checks say today, so a restructuring that keeps the
contract keeps every message, its position and its tag.
"""
from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import (
    validate_driver_definition,
    validate_driver_issues,
)

COMMANDS = {
    "noop": {"send": "NOOP\r"},
    "subscribe": {"send": "SUB {listener_port}\r"},
    "watch_zone": {
        "send": "WATCH {zone}\r",
        "params": {"zone": {"type": "child_id", "child_type": "zone"}},
    },
    "watch_other": {
        "send": "WATCH {mic}\r",
        "params": {"mic": {"type": "child_id", "child_type": "mic"}},
    },
    "plain_params": {"send": "P {x}\r", "params": {"x": {"type": "integer"}, "y": "odd"}},
}

CHILD_TYPES = {
    "zone": {"state_variables": {"level": {"type": "number", "label": "Level"}},
             "instances": {"count": 2}},
    "mic": {"id_format": {"type": "string"}, "instances": {"ids": ["a"]}},
}

CONFIG = {
    "config_schema": {"group_field": {"type": "string"}},
    "default_config": {"port_field": 5000, "poll_interval": 5},
    "config_derived": {"derived_field": {"from": "group_field"}},
}

CASES = {
    # --- the block and its type ---------------------------------------------
    "not_a_mapping": ("x", {}),
    "a_list": ([], {}),
    "absent": (None, {}),
    "type_missing": ({"group": "239.1.1.1", "port": 5000}, {}),
    "type_unknown_with_odd_keys": ({"type": "carrier_pigeon", "group": "239.1.1.1", "nest": 1, "port": 1}, {}),
    "type_not_a_string": ({"type": 5}, {}),
    "unknown_keys_per_type": ({"type": "multicast", "group": "239.1.1.1", "port": 5000, "path": "/x", "zz": 1}, {}),
    "http_listener_clean": ({"type": "http_listener"}, {}),
    "http_listener_keys": ({"type": "http_listener", "port": 5}, {}),
    "udp_listener_keys": ({"type": "udp_listener", "port": 5000, "group": "239.1.1.1"}, {}),
    # --- multicast -------------------------------------------------------------
    "multicast_clean": ({"type": "multicast", "group": "239.1.1.1", "port": 5000}, {}),
    "multicast_missing_both": ({"type": "multicast"}, {}),
    "multicast_group_values": ({"type": "multicast", "group": "10.0.0.1", "port": 5000}, {}),
    "multicast_group_int": ({"type": "multicast", "group": 5, "port": 5000}, {}),
    "multicast_group_edges": ({"type": "multicast", "group": "224.0.0.0", "port": 65535}, {}),
    "multicast_group_top": ({"type": "multicast", "group": "240.0.0.1", "port": 1}, {}),
    "multicast_group_template": ({"type": "multicast", "group": "{group_field}", "port": "{port_field}"}, {}),
    "multicast_group_template_derived": ({"type": "multicast", "group": "{derived_field}", "port": 5000}, {}),
    "multicast_template_undeclared": ({"type": "multicast", "group": "{nope}", "port": "{also_nope}"}, {}),
    "multicast_template_no_token": ({"type": "multicast", "group": "{}", "port": "{bad-name}"}, {}),
    "multicast_template_two_tokens": ({"type": "multicast", "group": "{group_field}.{nope}", "port": 5000}, {}),
    "multicast_template_inside_text": ({"type": "multicast", "group": "239.1.1.{octet}", "port": "5{port_field}"}, {}),
    "multicast_port_values_0": ({"type": "multicast", "group": "239.1.1.1", "port": 0}, {}),
    "multicast_port_values_big": ({"type": "multicast", "group": "239.1.1.1", "port": 65536}, {}),
    "multicast_port_values_bool": ({"type": "multicast", "group": "239.1.1.1", "port": True}, {}),
    "multicast_port_values_str": ({"type": "multicast", "group": "239.1.1.1", "port": "5000"}, {}),
    "multicast_port_values_float": ({"type": "multicast", "group": "239.1.1.1", "port": 5000.0}, {}),
    "multicast_ignores_register": ({"type": "multicast", "group": "239.1.1.1", "port": 5000, "register": "nope"}, {}),
    # --- udp_listener ----------------------------------------------------------
    "udp_clean": ({"type": "udp_listener", "port": 162}, {}),
    "udp_missing_port": ({"type": "udp_listener"}, {}),
    "udp_port_0": ({"type": "udp_listener", "port": 0}, {}),
    "udp_port_big": ({"type": "udp_listener", "port": 70000}, {}),
    "udp_port_bool": ({"type": "udp_listener", "port": False}, {}),
    "udp_port_str": ({"type": "udp_listener", "port": "x"}, {}),
    "udp_port_template": ({"type": "udp_listener", "port": "{port_field}"}, {}),
    "udp_port_template_bad": ({"type": "udp_listener", "port": "{nope}"}, {}),
    # --- tcp_listener ------------------------------------------------------------
    "tcp_clean": ({"type": "tcp_listener", "port": 0, "register": "subscribe"}, {}),
    "tcp_missing_port": ({"type": "tcp_listener"}, {}),
    "tcp_port_negative": ({"type": "tcp_listener", "port": -1}, {}),
    "tcp_port_big": ({"type": "tcp_listener", "port": 65536}, {}),
    "tcp_port_top": ({"type": "tcp_listener", "port": 65535}, {}),
    "tcp_port_bool": ({"type": "tcp_listener", "port": True}, {}),
    "tcp_port_template": ({"type": "tcp_listener", "port": "{port_field}"}, {}),
    "tcp_port_template_bad": ({"type": "tcp_listener", "port": "{x-y}"}, {}),
    "tcp_frame_not_mapping": ({"type": "tcp_listener", "port": 0, "frame_parser": "x"}, {}),
    "tcp_frame_type_unknown": ({"type": "tcp_listener", "port": 0, "frame_parser": {"type": "delimiter"}}, {}),
    "tcp_frame_type_missing": ({"type": "tcp_listener", "port": 0, "frame_parser": {}}, {}),
    "tcp_frame_length_prefix": ({"type": "tcp_listener", "port": 0, "frame_parser": {"type": "length_prefix", "header_size": 99}}, {}),
    "tcp_frame_fixed_length": ({"type": "tcp_listener", "port": 0, "frame_parser": {"type": "fixed_length", "length": -1}}, {}),
    "tcp_frame_struct_clean": ({"type": "tcp_listener", "port": 0, "frame_parser": {"type": "struct_frame"}}, {}),
    "tcp_frame_struct_full": ({"type": "tcp_listener", "port": 0, "frame_parser": {
        "type": "struct_frame", "header_reserve": 4, "mid_reserve": 0, "trailer_reserve": 2,
        "length_size": 4, "length_adjust": -2, "length_endian": "little"}}, {}),
    "tcp_frame_struct_all_wrong": ({"type": "tcp_listener", "port": 0, "frame_parser": {
        "type": "struct_frame", "header_reserve": -1, "mid_reserve": True, "trailer_reserve": "2",
        "length_size": 3, "length_adjust": 1.5, "length_endian": "middle"}}, {}),
    "tcp_frame_struct_bool_adjust": ({"type": "tcp_listener", "port": 0, "frame_parser": {
        "type": "struct_frame", "length_adjust": True, "length_size": True}}, {}),
    "tcp_frame_on_multicast": ({"type": "multicast", "group": "239.1.1.1", "port": 1, "frame_parser": "x"}, {}),
    # --- sse ---------------------------------------------------------------------
    "sse_clean": ({"type": "sse", "path": "/events"}, {"transport": "http"}),
    "sse_on_tcp": ({"type": "sse", "path": "/events"}, {}),
    "sse_no_transport": ({"type": "sse", "path": "/events"}, {"transport": ""}),
    "sse_path_missing": ({"type": "sse"}, {"transport": "http"}),
    "sse_path_empty_list": ({"type": "sse", "path": []}, {"transport": "http"}),
    "sse_path_wrong_type": ({"type": "sse", "path": 5}, {"transport": "http"}),
    "sse_path_dict": ({"type": "sse", "path": {"a": 1}}, {"transport": "http"}),
    "sse_path_entries": ({"type": "sse", "path": ["", "  ", 5, None, "{group_field}", "{nope}", "x", "/ok", "{}"]}, {"transport": "http"}),
    "sse_path_empty_string": ({"type": "sse", "path": ""}, {"transport": "http"}),
    "sse_path_relative": ({"type": "sse", "path": "events"}, {"transport": "http"}),
    "sse_idle_values_0": ({"type": "sse", "path": "/e", "idle_timeout": 0}, {"transport": "http"}),
    "sse_idle_values_bool": ({"type": "sse", "path": "/e", "idle_timeout": True}, {"transport": "http"}),
    "sse_idle_values_str": ({"type": "sse", "path": "/e", "idle_timeout": "5"}, {"transport": "http"}),
    "sse_idle_values_ok": ({"type": "sse", "path": "/e", "idle_timeout": 0.5}, {"transport": "http"}),
    "sse_session_not_mapping": ({"type": "sse", "path": "/e", "session": "x"}, {"transport": "http"}),
    "sse_session_empty": ({"type": "sse", "path": "/e", "session": {}}, {"transport": "http"}),
    "sse_session_header": ({"type": "sse", "path": "/e", "session": {"header": "X-Session"}}, {"transport": "http"}),
    "sse_session_event_key": ({"type": "sse", "path": "/e", "session": {"event": "hello", "key": "sid"}}, {"transport": "http"}),
    "sse_session_key_alone": ({"type": "sse", "path": "/e", "session": {"key": "sid"}}, {"transport": "http"}),
    "sse_session_event_alone": ({"type": "sse", "path": "/e", "session": {"event": "hello"}}, {"transport": "http"}),
    "sse_session_bad_strings": ({"type": "sse", "path": "/e", "session": {
        "header": 5, "event": " ", "key": "", "close_event": [], "zz": 1, "aa": 2}}, {"transport": "http"}),
    "sse_session_pattern_values": ({"type": "sse", "path": "/e", "session": {"header": "X", "pattern": ""}}, {"transport": "http"}),
    "sse_session_pattern_int": ({"type": "sse", "path": "/e", "session": {"header": "X", "pattern": 5}}, {"transport": "http"}),
    "sse_session_pattern_redos": ({"type": "sse", "path": "/e", "session": {"header": "X", "pattern": "(a+)+$"}}, {"transport": "http"}),
    "sse_session_pattern_broken": ({"type": "sse", "path": "/e", "session": {"header": "X", "pattern": "("}}, {"transport": "http"}),
    "sse_session_pattern_ok": ({"type": "sse", "path": "/e", "session": {"header": "X", "pattern": "id=(\\w+)"}}, {"transport": "http"}),
    "sse_session_two_paths": ({"type": "sse", "path": ["/a", "/b"], "session": {"header": "X"}}, {"transport": "http"}),
    "sse_session_one_path_list": ({"type": "sse", "path": ["/a"], "session": {"header": "X"}}, {"transport": "http"}),
    "sse_session_bad_path_two": ({"type": "sse", "path": 5, "session": {"header": "X"}}, {"transport": "http"}),
    "sse_everything_wrong": ({"type": "sse", "path": ["x", "{nope}"], "idle_timeout": -1, "session": {
        "key": "k", "pattern": "", "zz": 1}, "register": "nope", "unregister": "", "frame_parser": "x"}, {}),
    # --- register / unregister ------------------------------------------------------
    "register_str_ok": ({"type": "tcp_listener", "port": 0, "register": "subscribe", "unregister": "noop"}, {}),
    "register_str_undeclared": ({"type": "tcp_listener", "port": 0, "register": "nope"}, {}),
    "register_str_empty": ({"type": "tcp_listener", "port": 0, "register": " "}, {}),
    "register_int": ({"type": "tcp_listener", "port": 0, "register": 5}, {}),
    "register_list_empty": ({"type": "tcp_listener", "port": 0, "register": []}, {}),
    "register_list_mixed": ({"type": "tcp_listener", "port": 0, "register": [
        "subscribe", "nope", 5, {"command": "noop"}, {"command": "nope"}, {}, {"command": 7}]}, {}),
    "register_dict_alone": ({"type": "tcp_listener", "port": 0, "register": {"command": "noop", "when": "group_field"}}, {}),
    "register_entry_unknown_keys": ({"type": "tcp_listener", "port": 0, "register": [
        {"command": "noop", "zz": 1, "aa": 2}]}, {}),
    "register_entry_when": ({"type": "sse", "path": "/e", "register": [
        {"command": "noop", "when": "group_field"},
        {"command": "noop", "when": "derived_field"},
        {"command": "noop", "when": "port_field"},
        {"command": "noop", "when": "nope"},
        {"command": "noop", "when": 5},
        {"command": "noop", "when": None}]}, {"transport": "http"}),
    "register_entry_each_child": ({"type": "tcp_listener", "port": 0, "register": [
        {"command": "watch_zone", "each_child": "zone"},
        {"command": "watch_other", "each_child": "zone"},
        {"command": "noop", "each_child": "zone"},
        {"command": "nope", "each_child": "zone"},
        {"command": "plain_params", "each_child": "zone"},
        {"command": "watch_zone", "each_child": "nope"},
        {"command": "watch_zone", "each_child": 5},
        {"command": "watch_zone", "each_child": None},
        {"command": 5, "each_child": "mic"}]}, {}),
    "register_each_child_no_child_types": ({"type": "tcp_listener", "port": 0, "register": [
        {"command": "watch_zone", "each_child": "zone"}]}, {"child_entity_types": None}),
    "register_each_child_types_list": ({"type": "tcp_listener", "port": 0, "register": [
        {"command": "watch_zone", "each_child": "zone"}]}, {"child_entity_types": ["zone"]}),
    "register_no_commands": ({"type": "tcp_listener", "port": 0, "register": "noop", "unregister": "noop"}, {"commands": None}),
    "register_commands_list": ({"type": "tcp_listener", "port": 0, "register": "noop"}, {"commands": ["noop"]}),
    "unregister_values": ({"type": "sse", "path": "/e", "unregister": "nope"}, {"transport": "http"}),
    "unregister_int": ({"type": "sse", "path": "/e", "unregister": 3}, {"transport": "http"}),
    "unregister_list": ({"type": "sse", "path": "/e", "unregister": ["noop"]}, {"transport": "http"}),
    "register_on_udp_listener": ({"type": "udp_listener", "port": 5, "register": "nope", "unregister": "nope"}, {}),
    "register_on_http_listener": ({"type": "http_listener", "register": "nope"}, {}),
    "no_config_fields": ({"type": "multicast", "group": "{group_field}", "port": 5000}, {
        "config_schema": None, "default_config": "x", "config_derived": None}),
}


def _definition(case: str) -> dict[str, Any]:
    block, extra = CASES[case]
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": dict(COMMANDS),
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "child_entity_types": CHILD_TYPES,
        **CONFIG,
        "push": block,
    }
    definition.update(extra)
    return definition


EXPECTED: dict[str, list[tuple[str, str]]] = {'not_a_mapping': [('push', 'push: must be a mapping')],
 'a_list': [('push', 'push: must be a mapping')],
 'absent': [],
 'type_missing': [('push',
                   "push: missing or unknown 'type' (supported: multicast, sse, tcp_listener, "
                   'http_listener, udp_listener)')],
 'type_unknown_with_odd_keys': [('push',
                                 "push: missing or unknown 'type' (supported: multicast, sse, "
                                 'tcp_listener, http_listener, udp_listener)'),
                                ('push',
                                 'push: unknown key(s): nest (known keys for type '
                                 "'carrier_pigeon': frame_parser, group, idle_timeout, path, port, "
                                 'register, type, unregister)')],
 'type_not_a_string': [('push',
                        "push: missing or unknown 'type' (supported: multicast, sse, tcp_listener, "
                        'http_listener, udp_listener)')],
 'unknown_keys_per_type': [('push',
                            "push: unknown key(s): path, zz (known keys for type 'multicast': "
                            'group, port, type)')],
 'http_listener_clean': [],
 'http_listener_keys': [('push',
                         "push: unknown key(s): port (known keys for type 'http_listener': type)")],
 'udp_listener_keys': [('push',
                        "push: unknown key(s): group (known keys for type 'udp_listener': port, "
                        'type)')],
 'multicast_clean': [],
 'multicast_missing_both': [('push', "push: missing 'group'"), ('push', "push: missing 'port'")],
 'multicast_group_values': [('push',
                             "push: group '10.0.0.1' must be an IPv4 multicast address (224.0.0.0 "
                             '- 239.255.255.255) or a {config_field} template')],
 'multicast_group_int': [('push',
                          'push: group 5 must be an IPv4 multicast address (224.0.0.0 - '
                          '239.255.255.255) or a {config_field} template')],
 'multicast_group_edges': [],
 'multicast_group_top': [('push',
                          "push: group '240.0.0.1' must be an IPv4 multicast address (224.0.0.0 - "
                          '239.255.255.255) or a {config_field} template')],
 'multicast_group_template': [],
 'multicast_group_template_derived': [],
 'multicast_template_undeclared': [('push',
                                    "push: group references config field 'nope' that is not "
                                    'declared in config_schema, default_config, or config_derived'),
                                   ('push',
                                    "push: port references config field 'also_nope' that is not "
                                    'declared in config_schema, default_config, or '
                                    'config_derived')],
 'multicast_template_no_token': [('push',
                                  "push: group '{}' has braces but no {config_field} token"),
                                 ('push',
                                  "push: port '{bad-name}' has braces but no {config_field} "
                                  'token')],
 'multicast_template_two_tokens': [('push',
                                    "push: group references config field 'nope' that is not "
                                    'declared in config_schema, default_config, or '
                                    'config_derived')],
 'multicast_template_inside_text': [('push',
                                     "push: group references config field 'octet' that is not "
                                     'declared in config_schema, default_config, or '
                                     'config_derived')],
 'multicast_port_values_0': [('push',
                              'push: port must be an integer 1-65535 or a {config_field} '
                              'template')],
 'multicast_port_values_big': [('push',
                                'push: port must be an integer 1-65535 or a {config_field} '
                                'template')],
 'multicast_port_values_bool': [('push',
                                 'push: port must be an integer 1-65535 or a {config_field} '
                                 'template')],
 'multicast_port_values_str': [('push',
                                'push: port must be an integer 1-65535 or a {config_field} '
                                'template')],
 'multicast_port_values_float': [('push',
                                  'push: port must be an integer 1-65535 or a {config_field} '
                                  'template')],
 'multicast_ignores_register': [('push',
                                 "push: unknown key(s): register (known keys for type 'multicast': "
                                 'group, port, type)')],
 'udp_clean': [],
 'udp_missing_port': [('push', "push: missing 'port'")],
 'udp_port_0': [('push', 'push: port must be an integer 1-65535 or a {config_field} template')],
 'udp_port_big': [('push', 'push: port must be an integer 1-65535 or a {config_field} template')],
 'udp_port_bool': [('push', 'push: port must be an integer 1-65535 or a {config_field} template')],
 'udp_port_str': [('push', 'push: port must be an integer 1-65535 or a {config_field} template')],
 'udp_port_template': [],
 'udp_port_template_bad': [('push',
                            "push: port references config field 'nope' that is not declared in "
                            'config_schema, default_config, or config_derived')],
 'tcp_clean': [],
 'tcp_missing_port': [('push', "push: missing 'port'")],
 'tcp_port_negative': [('push',
                        'push: port must be an integer 0-65535 (0 = OS-assigned) or a '
                        '{config_field} template')],
 'tcp_port_big': [('push',
                   'push: port must be an integer 0-65535 (0 = OS-assigned) or a {config_field} '
                   'template')],
 'tcp_port_top': [],
 'tcp_port_bool': [('push',
                    'push: port must be an integer 0-65535 (0 = OS-assigned) or a {config_field} '
                    'template')],
 'tcp_port_template': [],
 'tcp_port_template_bad': [('push', "push: port '{x-y}' has braces but no {config_field} token")],
 'tcp_frame_not_mapping': [('push', 'push: frame_parser must be a mapping')],
 'tcp_frame_type_unknown': [('push',
                             "push: frame_parser type 'delimiter' must be struct_frame, "
                             'length_prefix, or fixed_length')],
 'tcp_frame_type_missing': [('push',
                             'push: frame_parser type None must be struct_frame, length_prefix, or '
                             'fixed_length')],
 'tcp_frame_length_prefix': [],
 'tcp_frame_fixed_length': [],
 'tcp_frame_struct_clean': [],
 'tcp_frame_struct_full': [],
 'tcp_frame_struct_all_wrong': [('push',
                                 'push: frame_parser header_reserve must be a non-negative '
                                 'integer'),
                                ('push',
                                 'push: frame_parser mid_reserve must be a non-negative integer'),
                                ('push',
                                 'push: frame_parser trailer_reserve must be a non-negative '
                                 'integer'),
                                ('push', 'push: frame_parser length_size must be 1, 2, or 4'),
                                ('push', 'push: frame_parser length_adjust must be an integer'),
                                ('push',
                                 "push: frame_parser length_endian must be 'big' or 'little'")],
 'tcp_frame_struct_bool_adjust': [('push', 'push: frame_parser length_adjust must be an integer')],
 'tcp_frame_on_multicast': [('push',
                             "push: unknown key(s): frame_parser (known keys for type 'multicast': "
                             'group, port, type)')],
 'sse_clean': [],
 'sse_on_tcp': [('push', "push: type 'sse' requires the http transport, not 'tcp'")],
 'sse_no_transport': [],
 'sse_path_missing': [('push',
                       "push: missing 'path' (an event-stream URL path, or a list of them)")],
 'sse_path_empty_list': [('push',
                          "push: missing 'path' (an event-stream URL path, or a list of them)")],
 'sse_path_wrong_type': [('push', 'push: path must be a string or a list of strings')],
 'sse_path_dict': [('push', 'push: path must be a string or a list of strings')],
 'sse_path_entries': [('push', "push: path entry '' must be a non-empty string"),
                      ('push', "push: path entry '  ' must be a non-empty string"),
                      ('push', 'push: path entry 5 must be a non-empty string'),
                      ('push', 'push: path entry None must be a non-empty string'),
                      ('push',
                       "push: path references config field 'nope' that is not declared in "
                       'config_schema, default_config, or config_derived'),
                      ('push',
                       "push: path 'x' must start with '/' (a URL path on the device) or be a "
                       '{config_field} template'),
                      ('push', "push: path '{}' has braces but no {config_field} token")],
 'sse_path_empty_string': [('push', "push: path entry '' must be a non-empty string")],
 'sse_path_relative': [('push',
                        "push: path 'events' must start with '/' (a URL path on the device) or be "
                        'a {config_field} template')],
 'sse_idle_values_0': [('push', 'push: idle_timeout must be a positive number of seconds')],
 'sse_idle_values_bool': [('push', 'push: idle_timeout must be a positive number of seconds')],
 'sse_idle_values_str': [('push', 'push: idle_timeout must be a positive number of seconds')],
 'sse_idle_values_ok': [],
 'sse_session_not_mapping': [('push', 'push: session must be a mapping')],
 'sse_session_empty': [('push',
                        "push: session needs a source for the session id: 'header' (a response "
                        "header), or 'event' + 'key' (a JSON key in the opening event's data), or "
                        'both')],
 'sse_session_header': [],
 'sse_session_event_key': [],
 'sse_session_key_alone': [('push',
                            "push: session key names a JSON key in the opening event's data, so it "
                            "needs 'event' (the event type that carries it)"),
                           ('push',
                            "push: session needs a source for the session id: 'header' (a response "
                            "header), or 'event' + 'key' (a JSON key in the opening event's data), "
                            'or both')],
 'sse_session_event_alone': [('push',
                              "push: session needs a source for the session id: 'header' (a "
                              "response header), or 'event' + 'key' (a JSON key in the opening "
                              "event's data), or both")],
 'sse_session_bad_strings': [('push',
                              'push: session has unknown key(s): aa, zz (known: header, event, '
                              'key, pattern, close_event)'),
                             ('push', 'push: session header must be a non-empty string'),
                             ('push', 'push: session event must be a non-empty string'),
                             ('push', 'push: session key must be a non-empty string'),
                             ('push', 'push: session close_event must be a non-empty string')],
 'sse_session_pattern_values': [('push', 'push: session pattern must be a non-empty regex')],
 'sse_session_pattern_int': [('push', 'push: session pattern must be a non-empty regex')],
 'sse_session_pattern_redos': [('push',
                                "push: session pattern: regex '(a+)+$' has a nested quantifier; "
                                'this can cause catastrophic backtracking against hostile input')],
 'sse_session_pattern_broken': [('push',
                                 "push: session pattern: invalid regex '(': missing ), "
                                 'unterminated subpattern at position 0')],
 'sse_session_pattern_ok': [],
 'sse_session_two_paths': [('push',
                            'push: a session block needs a single event-stream path '
                            '({push_session} would be ambiguous across several sessions)')],
 'sse_session_one_path_list': [],
 'sse_session_bad_path_two': [('push', 'push: path must be a string or a list of strings')],
 'sse_everything_wrong': [('push',
                           "push: unknown key(s): frame_parser (known keys for type 'sse': "
                           'idle_timeout, path, register, session, type, unregister)'),
                          ('push', "push: type 'sse' requires the http transport, not 'tcp'"),
                          ('push',
                           "push: path 'x' must start with '/' (a URL path on the device) or be a "
                           '{config_field} template'),
                          ('push',
                           "push: path references config field 'nope' that is not declared in "
                           'config_schema, default_config, or config_derived'),
                          ('push', 'push: idle_timeout must be a positive number of seconds'),
                          ('push',
                           'push: session has unknown key(s): zz (known: header, event, key, '
                           'pattern, close_event)'),
                          ('push',
                           "push: session key names a JSON key in the opening event's data, so it "
                           "needs 'event' (the event type that carries it)"),
                          ('push',
                           "push: session needs a source for the session id: 'header' (a response "
                           "header), or 'event' + 'key' (a JSON key in the opening event's data), "
                           'or both'),
                          ('push', 'push: session pattern must be a non-empty regex'),
                          ('push',
                           'push: a session block needs a single event-stream path ({push_session} '
                           'would be ambiguous across several sessions)'),
                          ('push', "push: register command 'nope' is not declared in commands"),
                          ('push', 'push: unregister must be a command name')],
 'register_str_ok': [],
 'register_str_undeclared': [('push', "push: register command 'nope' is not declared in commands")],
 'register_str_empty': [('push', 'push: register must be a command name')],
 'register_int': [('push', 'push: register must be a command name')],
 'register_list_empty': [('push', 'push: register list must not be empty')],
 'register_list_mixed': [('push', "push: register[1] command 'nope' is not declared in commands"),
                         ('push', 'push: register[2] must be a command name'),
                         ('push', "push: register[4] command 'nope' is not declared in commands"),
                         ('push', 'push: register[5] must be a command name'),
                         ('push', 'push: register[6] must be a command name')],
 'register_dict_alone': [],
 'register_entry_unknown_keys': [('push',
                                  'push: register[0] has unknown key(s): aa, zz (known: command, '
                                  'when, each_child)')],
 'register_entry_when': [('push',
                          "push: register[3] when 'nope' must name a config field declared in "
                          'config_schema, default_config, or config_derived'),
                         ('push',
                          'push: register[4] when 5 must name a config field declared in '
                          'config_schema, default_config, or config_derived')],
 'register_entry_each_child': [('push',
                                "push: register[1] runs once per 'zone' child, so command "
                                "'watch_other' needs a child_id parameter of that type"),
                               ('push',
                                "push: register[2] runs once per 'zone' child, so command 'noop' "
                                'needs a child_id parameter of that type'),
                               ('push',
                                "push: register[3] command 'nope' is not declared in commands"),
                               ('push',
                                "push: register[3] runs once per 'zone' child, so command 'nope' "
                                'needs a child_id parameter of that type'),
                               ('push',
                                "push: register[4] runs once per 'zone' child, so command "
                                "'plain_params' needs a child_id parameter of that type"),
                               ('push',
                                "push: register[5] each_child 'nope' is not a declared child "
                                'entity type'),
                               ('push',
                                'push: register[6] each_child 5 is not a declared child entity '
                                'type'),
                               ('push', 'push: register[8] must be a command name'),
                               ('push',
                                "push: register[8] runs once per 'mic' child, so command '5' needs "
                                'a child_id parameter of that type')],
 'register_each_child_no_child_types': [('commands',
                                         "commands.watch_zone.params.zone: child_type 'zone' is "
                                         'not a declared child_entity_type (the driver declares '
                                         'none)'),
                                        ('commands',
                                         "commands.watch_other.params.mic: child_type 'mic' is not "
                                         'a declared child_entity_type (the driver declares none)'),
                                        ('push',
                                         "push: register[0] each_child 'zone' is not a declared "
                                         'child entity type')],
 'register_each_child_types_list': [('push',
                                     "push: register[0] each_child 'zone' is not a declared child "
                                     'entity type'),
                                    ('child_entity_types',
                                     'child_entity_types: must be a mapping')],
 'register_no_commands': [('commands', 'commands: must be a mapping'),
                          ('push', "push: register command 'noop' is not declared in commands"),
                          ('push', "push: unregister command 'noop' is not declared in commands")],
 'register_commands_list': [('commands', 'commands: must be a mapping'),
                            ('push', "push: register command 'noop' is not declared in commands")],
 'unregister_values': [('push', "push: unregister command 'nope' is not declared in commands")],
 'unregister_int': [('push', 'push: unregister must be a command name')],
 'unregister_list': [('push', 'push: unregister must be a command name')],
 'register_on_udp_listener': [('push',
                               'push: unknown key(s): register, unregister (known keys for type '
                               "'udp_listener': port, type)")],
 'register_on_http_listener': [('push',
                                'push: unknown key(s): register (known keys for type '
                                "'http_listener': type)")],
 'no_config_fields': [('push',
                       "push: group references config field 'group_field' that is not declared in "
                       'config_schema, default_config, or config_derived')]}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    issues = validate_driver_issues(definition)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [m for _, m in got]


def test_every_case_has_an_expectation() -> None:
    cases = set(CASES) | set(globals().get("EXTRA", {}))
    assert cases == set(EXPECTED)
