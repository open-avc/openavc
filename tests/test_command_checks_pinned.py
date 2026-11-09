"""Pins for a command's load-time checks, through the public doors.

A YAML command is checked in this order: how it sends (exactly one of
``send``, ``path``/``method``, ``address``, ``udp``, ``ir``), an OSC
command's ``args``, a ``udp:`` block, its parameters' picker and free-text
aids, what ``sets:`` and ``query_for:`` name, then ``confirm``,
``sleeps_device``, ``read_back`` and the file rules. Two of those checks are
asked elsewhere too: an OSC device setting's ``write: args`` and an action's
``params``. A Python driver's ``DRIVER_INFO`` gets the four shared ones
(``confirm``, ``sleeps_device``, ``read_back``, the file rules) through
``python_driver_info_issues``.

Each YAML case is a set of commands (or top-level keys) over one driver. The
expected value is every error ``validate_driver_issues`` reports for the whole
definition, in order, each with the section tag the Driver Builder anchors it
to. The Python cases record ``python_driver_info_issues`` as it answers.

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
from openavc.drivers.python_info import UNEVALUATED, python_driver_info_issues

CHILD_TYPES = {
    "zone": {
        "id_format": {"type": "integer", "min": 1, "max": 16},
        "state_variables": {"level": {"type": "number", "label": "Level"}},
        "instances": {"count": 8},
    },
    "mic": {
        "id_format": {"type": "string"},
        "state_variables": {"gain": {"type": "number", "label": "Gain"}},
        "instances": {"ids": ["a", "b"]},
    },
}

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "volume": {"type": "integer", "label": "Volume"},
    },
    "child_entity_types": CHILD_TYPES,
    "config_schema": {"wake_mac": {"type": "string"}, "udp_port": {"type": "integer"}},
    "default_config": {"poll_interval": 5},
}

ZONE = {"type": "child_id", "child_type": "zone", "label": "Zone"}
MIC = {"type": "child_id", "child_type": "mic", "label": "Mic"}
LEVEL = {"type": "number", "label": "Level"}


def cmds(**commands: Any) -> dict[str, Any]:
    return {"commands": commands}


def send(text: str = "X\r", **rest: Any) -> dict[str, Any]:
    return {"send": text, **rest}


def udp(**block: Any) -> dict[str, Any]:
    return {"udp": block}


def picker(**params: Any) -> dict[str, Any]:
    return {"send": "X {a}\r", "params": params}


CASES: dict[str, dict[str, Any]] = {
    # --- the section ------------------------------------------------------
    "commands_not_a_mapping": {"commands": ["power_on"]},
    "commands_absent": {"commands": None},
    "command_not_a_mapping": cmds(a="X\r", b=None, c=5, d=send()),
    # --- how it sends -----------------------------------------------------
    "send_shapes": cmds(
        none={}, empty_send={"send": ""}, send=send(), path={"path": "/x"},
        method={"method": "POST"}, address={"address": "/x"}, udp=udp(payload="x", port=9),
        ir={"ir": "power"}, null_address={"address": None}, null_udp={"udp": None},
        two={"send": "X", "path": "/x"},
        all={"send": "X", "method": "GET", "address": "/x", "udp": {}, "ir": "x"},
        false_send={"send": False, "ir": None},
    ),
    # --- an OSC command's args --------------------------------------------
    "osc_args": cmds(
        none={"address": "/a"},
        null={"address": "/a", "args": None},
        not_a_list={"address": "/a", "args": {"type": "f"}},
        items={"address": "/a", "args": [{"type": "i"}, "f", {"type": "x"}, {}, None,
                                         {"type": "T"}, {"type": 5}]},
        not_osc={"send": "X", "args": "whatever"},
    ),
    "osc_args_on_a_setting_write": {
        "device_settings": {
            "gain": {"type": "integer", "state_key": "volume",
                     "write": {"address": "/gain", "args": [{"type": "q"}, "x"]}},
            "trim": {"type": "integer", "state_key": "volume",
                     "write": {"address": "/trim", "args": "x"}},
            "fine": {"type": "integer", "state_key": "volume",
                     "write": {"address": None, "args": "x", "send": "F"}},
        },
    },
    # --- a udp: block -----------------------------------------------------
    "udp_block_shapes": cmds(
        not_a_mapping={"udp": "x"},
        a_list={"udp": [1]},
        both=udp(payload="x", magic_packet="wake_mac", port=9),
        neither=udp(port=9),
        empty=udp(),
        host=udp(payload="x", port=9, host=5),
        host_ok=udp(payload="x", port=9, host="10.0.0.9"),
        broadcast=udp(payload="x", port=9, broadcast="yes"),
        broadcast_ok=udp(payload="x", port=9, broadcast=True),
    ),
    "udp_ports": cmds(
        missing=udp(payload="x"),
        blank=udp(payload="x", port=""),
        missing_for_magic=udp(magic_packet="wake_mac"),
        a_bool=udp(payload="x", port=True),
        a_float=udp(payload="x", port=9.0),
        a_list=udp(payload="x", port=[9]),
        zero=udp(payload="x", port=0),
        high=udp(payload="x", port=65536),
        low_edge=udp(payload="x", port=1),
        high_edge=udp(payload="x", port=65535),
        text_digits=udp(payload="x", port=" 9 "),
        text_zero=udp(payload="x", port="0"),
        text_high=udp(payload="x", port="70000"),
        text_word=udp(payload="x", port="nine"),
        text_negative=udp(payload="x", port="-9"),
        field=udp(payload="x", port="{udp_port}"),
        field_spaced=udp(payload="x", port=" {udp_port} "),
        derived_or_default=udp(payload="x", port="{poll_interval}"),
        platform_name=udp(payload="x", port="{listener_port}"),
        undeclared=udp(payload="x", port="{nope}"),
        spec=udp(payload="x", port="{udp_port:d}"),
    ),
    "udp_magic_packets": cmds(
        field=udp(magic_packet="wake_mac"),
        state_only=udp(magic_packet="power"),
        blank=udp(magic_packet=""),
        a_number=udp(magic_packet=5),
        false=udp(magic_packet=False, payload="x", port=9),
        none_with_payload=udp(magic_packet=None, payload="x", port=9),
    ),
    # --- a parameter's aids -----------------------------------------------
    "param_free_text_aids": cmds(
        c=picker(
            a={"type": "string", "pattern": "^[A-Z]+$"},
            b={"type": "string", "pattern": "(a+)+$"},
            c={"type": "string", "pattern": "("},
            d={"type": "integer", "min": "1", "max": [2]},
            e={"type": "integer", "min": 5, "max": 1},
            f={"type": "number", "min": 1.5, "max": 1.5},
            g={"type": "integer", "min": True, "max": 0},
            h={"type": "number", "decimals": -1},
            i={"type": "number", "decimals": True},
            j={"type": "number", "decimals": 1.0},
            k={"type": "number", "decimals": 2},
            l={"type": "string", "trim": "no"},
            m={"type": "string", "trim": False},
            n="not a mapping",
        ),
    ),
    "param_maps": cmds(
        c=picker(
            a={"type": "integer", "map": {}},
            b={"type": "integer", "map": [1, 2]},
            c={"type": "integer", "map": {1: "A", 2: "B"}},
            d={"type": "string", "map": {"on": True}},
            e={"type": "string", "map": {True: "1"}},
            f={"type": "string", "map": {"a": [1], "b": None}},
            g={"type": "string", "map": {"a": 1.5, "b": "x"}},
        ),
    ),
    "param_options_state": cmds(
        c=picker(
            a={"type": "string", "options_state": ""},
            b={"type": "string", "options_state": 5},
            c={"type": "string", "options_state": "device.x.inputs"},
            d={"type": "string", "options_state": None},
        ),
    ),
    "param_options_from": cmds(
        c=picker(
            zone=ZONE,
            level=LEVEL,
            a={"type": "string", "options_from": "zone"},
            b={"type": "string", "options_from": {}},
            c={"type": "string", "options_from": {"source": "nope", "param": "zone"}},
            d={"type": "string", "options_from": {"source": "child_schema", "param": ""}},
            e={"type": "string", "options_from": {"source": "child_schema", "param": 5}},
            f={"type": "string", "options_from": {"source": "child_schema", "param": "nope"}},
            g={"type": "string", "options_from": {"source": "child_schema", "param": "level"}},
            h={"type": "string", "options_from": {"source": "child_schema", "param": "zone"}},
            i={"type": "string", "options_from": {"source": "child_schema", "param": "n"}},
            n="not a mapping",
        ),
    ),
    "param_options_from_lists": cmds(
        c=picker(
            zone=ZONE,
            a={"options_from": {"source": "child_schema", "param": "zone", "types": []}},
            b={"options_from": {"source": "child_schema", "param": "zone", "types": "number"}},
            c={"options_from": {"source": "child_schema", "param": "zone", "types": ["nope"]}},
            d={"options_from": {"source": "child_schema", "param": "zone",
                                "types": ["number", "float"]}},
            e={"options_from": {"source": "child_schema", "param": "zone", "units": []}},
            f={"options_from": {"source": "child_schema", "param": "zone", "units": ["dB", " "]}},
            g={"options_from": {"source": "child_schema", "param": "zone", "units": [5]}},
            h={"options_from": {"source": "child_schema", "param": "zone", "units": ["dB"]}},
            i={"options_from": {"source": "nope", "param": "nope", "types": [], "units": []}},
        ),
    ),
    "param_type_from": cmds(
        c=picker(
            zone=ZONE,
            control={"type": "string",
                     "options_from": {"source": "child_schema", "param": "zone"}},
            plain={"type": "string"},
            odd={"type": "string", "options_from": "zone"},
            a={"type_from": "control"},
            b={"type_from": {}},
            c={"type_from": {"param": 5}},
            d={"type_from": {"param": "nope"}},
            e={"type_from": {"param": "plain"}},
            f={"type_from": {"param": "odd"}},
            g={"type_from": {"param": "control"}},
            h={"type_from": {"param": "n"}},
            n="not a mapping",
        ),
    ),
    "param_aids_not_a_mapping": cmds(a={"send": "X", "params": ["a"]},
                                     b={"send": "X", "params": None}),
    "param_aids_on_an_action": {
        "commands": {"go": {"send": "GO {a}\r", "params": {"a": {"type": "string"}}}},
        "actions": [
            {"id": "go", "kind": "command", "params": {"a": {"type": "string", "pattern": "("}}},
            {"id": "go2", "kind": "command", "command": "go",
             "params": {"a": {"type": "integer", "min": 3, "max": 1}}},
            {"id": "go3", "kind": "command", "command": "go", "params": ["a"]},
        ],
    },
    # --- sets: and query_for: ----------------------------------------------
    "sets_shapes": cmds(
        a=send(sets="power"),
        b=send(sets=["power"]),
        c=send(sets=None),
        d=send(sets={}),
        e=send(sets={"power": True, "volume": 5}),
        f=send(sets={"nope": 1, "level": 1}),
    ),
    "sets_values": cmds(
        a={"send": "V {v}\r", "params": {"v": {"type": "integer"}},
           "sets": {"volume": "{v}"}},
        b={"send": "V {v}\r", "params": {"v": {"type": "integer"}},
           "sets": {"volume": "{w}"}},
        c={"send": "V {v}\r", "params": {"v": {"type": "integer"}},
           "sets": {"volume": "v={v}"}},
        d={"send": "V {v}\r", "params": {"v": {"type": "integer"}},
           "sets": {"volume": "{v"}},
        e={"send": "V {v}\r", "params": {"v": {"type": "integer"}},
           "sets": {"volume": "}"}},
        f={"send": "V {v}\r", "params": None, "sets": {"volume": "{v}"}},
        g={"send": "V {v}\r", "params": {"v": {"type": "integer"}},
           "sets": {"volume": "{{v}}"}},
        h=send(sets={"nope": "{x}"}),
    ),
    "sets_on_a_child": cmds(
        one={"send": "Z{zone} {lvl}\r", "params": {"zone": ZONE, "lvl": LEVEL},
             "sets": {"level": "{lvl}", "online": True, "volume": 1, "gain": 1}},
        two={"send": "Z{zone} {mic}\r", "params": {"zone": ZONE, "mic": MIC},
             "sets": {"level": 1}},
        undeclared_type={"send": "Q{q}\r",
                         "params": {"q": {"type": "child_id", "child_type": "nope"}},
                         "sets": {"level": 1}},
        no_child_type={"send": "Q{q}\r", "params": {"q": {"type": "child_id"}},
                       "sets": {"level": 1}},
        params_not_mappings={"send": "Q\r", "params": {"q": "child_id"},
                             "sets": {"level": 1}},
    ),
    "query_for_on_a_command": cmds(
        a=send(query_for="volume"),
        b=send(query_for="nope"),
        c=send(query_for=""),
        d=send(query_for=5),
        e=send(query_for=None),
        f={"send": "Z{zone}?\r", "params": {"zone": ZONE}, "query_for": "level"},
        g={"send": "Z{zone}?\r", "params": {"zone": ZONE}, "query_for": "nope"},
        h={"send": "Z{zone}?\r", "params": {"zone": ZONE}, "query_for": "label"},
        i={"send": "M{mic}?\r", "params": {"mic": MIC}, "query_for": "level"},
    ),
    # --- confirm, sleeps_device -------------------------------------------
    "confirm_values": cmds(
        a=send(confirm=True), b=send(confirm="Restart?"), c=send(confirm=5),
        d=send(confirm=None), e=send(confirm=["x"]),
    ),
    "sleeps_device": cmds(
        off=send(sleeps_device="wake"),
        off_unsaid=send(sleeps_device="lazy"),
        off_self=send(sleeps_device="off_self"),
        off_blank=send(sleeps_device="  "),
        off_number=send(sleeps_device=5),
        off_missing=send(sleeps_device="wkae"),
        off_far=send(sleeps_device="zzzzzz"),
        off_odd=send(sleeps_device="odd"),
        off_none=send(sleeps_device=None),
        wake={"udp": {"magic_packet": "wake_mac"}, "available_offline": True,
              "restarts_device_for": 30},
        lazy=send(available_offline="yes", restarts_device_for=0),
        odd="X\r",
    ),
    # --- read_back --------------------------------------------------------
    "read_back_shapes": cmds(
        a=send(read_back="VOL?"),
        b=send(read_back=["VOL?", "PWR?"]),
        c=send(read_back=[]),
        d=send(read_back=5),
        e=send(read_back=["VOL?", ""]),
        f=send(read_back=["VOL?", 5]),
        g=send(read_back="  "),
        h=send(read_back=None),
        i=send(read_back=False),
    ),
    "read_back_true": cmds(
        a=send(read_back=True),
        b={"address": "/vol", "read_back": True},
        c={"path": "/vol", "read_back": True},
        d={"method": "GET", "read_back": True},
        e={"udp": {"payload": "x", "port": 9}, "read_back": True},
        f={"ir": "x", "read_back": True},
        g={"udp": None, "ir": None, "send": "X", "read_back": True},
    ),
    "read_back_naming_commands": cmds(
        get_vol=send("VOL?\r"),
        get_zone={"send": "Z{zone}?\r", "params": {"zone": ZONE}},
        chained=send("C?\r", read_back="get_vol"),
        beside={"udp": {"payload": "x", "port": 9}},
        beside_ir={"ir": "x"},
        a=send(read_back="a"),
        b=send(read_back=["get_vol", "b"]),
        c=send(read_back="chained"),
        d=send(read_back=["beside", "beside_ir"]),
        e=send(read_back="get_zone"),
        f={"send": "Z{zone} {x}\r", "params": {"zone": ZONE, "x": LEVEL},
           "read_back": "get_zone"},
        g={"send": "Z\r", "params": None, "read_back": ["get_zone"]},
        h=send(read_back="odd"),
        odd="X\r",
    ),
    "read_back_on_osc": {
        "transport": "osc",
        "commands": {
            "get": {"address": "/get"},
            "a": {"address": "/a", "read_back": "/vol"},
            "b": {"address": "/b", "read_back": ["vol", "get", "/x"]},
            "c": {"address": "/c", "read_back": "c"},
        },
    },
    # --- the file rules ---------------------------------------------------
    "file_rules_on_yaml": cmds(
        a={"send": "X {f}\r", "params": {"f": {"type": "file"}}},
        b={"send": "X {f}\r", "params": {"f": {"type": "string", "accept": [".bin"]}}},
        c={"send": "X {f}\r", "params": {"f": {"type": "string", "accept": [".bin"],
                                                "max_bytes": 5}}},
        d={"send": "X {f}\r", "params": {"f": {"type": "integer", "max_bytes": 5}}},
        e=send(returns_file=True),
        f=send(returns_file="yes"),
        g=send(returns_file=False),
        h={"send": "X\r", "params": {"f": "file"}},
        i={"send": "X\r", "params": ["f"]},
    ),
    # --- the order of one command's checks --------------------------------
    "every_check_on_one_command": cmds(
        a={"address": "/a", "send": "A", "args": [{"type": "x"}],
           "udp": {"payload": "x", "magic_packet": "nope"},
           "params": {"p": {"type": "file", "pattern": "(", "min": 2, "max": 1}},
           "sets": {"nope": "{q}"}, "query_for": "nope", "confirm": 1,
           "sleeps_device": "a", "read_back": True, "returns_file": True},
    ),
    "commands_after_responses_before_settings": {
        "responses": ["not a rule"],
        "commands": {"a": {}},
        "device_settings": {"s": "x"},
    },
}


def _definition(case: str) -> dict[str, Any]:
    over = CASES[case]
    if "commands" not in over:
        over = {"commands": {"noop": {"send": "NOOP\r"}}, **over}
    return {**BASE, **over}


EXPECTED: dict[str, list[tuple[str, str]]] = {'commands_not_a_mapping': [('commands', 'commands: must be a mapping')],
 'commands_absent': [('commands', 'commands: must be a mapping')],
 'command_not_a_mapping': [('commands.a', "Command 'a': must be a dict"),
                           ('commands.b', "Command 'b': must be a dict"),
                           ('commands.c', "Command 'c': must be a dict")],
 'send_shapes': [('commands.none',
                  "Command 'none': must have 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' (OSC), "
                  "or 'udp' (a datagram beside the main transport)"),
                 ('commands.empty_send',
                  "Command 'empty_send': must have 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' "
                  "(OSC), or 'udp' (a datagram beside the main transport)"),
                 ('commands.null_address',
                  "Command 'null_address': must have 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' "
                  "(OSC), or 'udp' (a datagram beside the main transport)"),
                 ('commands.null_udp',
                  "Command 'null_udp': must have 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' "
                  "(OSC), or 'udp' (a datagram beside the main transport)"),
                 ('commands.two',
                  "Command 'two': declares send and path/method, but a command sends one way. Keep one of "
                  "'send' (TCP/serial), 'path'/'method' (HTTP), 'address' (OSC), or 'udp' (a datagram beside "
                  'the main transport)'),
                 ('commands.all',
                  "Command 'all': declares send and path/method and address and udp and ir, but a command "
                  "sends one way. Keep one of 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' (OSC), "
                  "or 'udp' (a datagram beside the main transport)"),
                 ('commands.all',
                  "Command 'all': 'udp' needs a 'payload' (the datagram) or a 'magic_packet' (the field "
                  'holding the MAC to wake)'),
                 ('commands.false_send',
                  "Command 'false_send': must have 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' "
                  "(OSC), or 'udp' (a datagram beside the main transport)")],
 'osc_args': [('commands.not_a_list', "Command 'not_a_list': args must be a list"),
              ('commands.items', "Command 'items': args[1] must be a mapping"),
              ('commands.items',
               "Command 'items': args[2] unknown OSC type 'x' (expected one of f/i/s/h/d/T/F/N)"),
              ('commands.items', "Command 'items': args[4] must be a mapping"),
              ('commands.items',
               "Command 'items': args[6] unknown OSC type '5' (expected one of f/i/s/h/d/T/F/N)")],
 'osc_args_on_a_setting_write': [('device_settings.gain',
                                  "Device setting 'gain' write: args[0] unknown OSC type 'q' (expected one "
                                  'of f/i/s/h/d/T/F/N)'),
                                 ('device_settings.gain',
                                  "Device setting 'gain' write: args[1] must be a mapping"),
                                 ('device_settings.trim',
                                  "Device setting 'trim' write: args must be a list")],
 'udp_block_shapes': [('commands.not_a_mapping',
                       "Command 'not_a_mapping': 'udp' must be a mapping (host, port, payload or "
                       'magic_packet)'),
                      ('commands.a_list',
                       "Command 'a_list': 'udp' must be a mapping (host, port, payload or magic_packet)"),
                      ('commands.both',
                       "Command 'both': 'udp' declares both 'payload' and 'magic_packet'; a datagram is one "
                       'or the other'),
                      ('commands.neither',
                       "Command 'neither': 'udp' needs a 'payload' (the datagram) or a 'magic_packet' (the "
                       'field holding the MAC to wake)'),
                      ('commands.empty',
                       "Command 'empty': 'udp' needs a 'payload' (the datagram) or a 'magic_packet' (the "
                       'field holding the MAC to wake)'),
                      ('commands.host', "Command 'host': 'udp.host' must be a string"),
                      ('commands.broadcast', "Command 'broadcast': 'udp.broadcast' must be true or false")],
 'udp_ports': [('commands.missing',
                "Command 'missing': 'udp.port' is required with a payload (a number, or a {config_field} "
                'placeholder)'),
               ('commands.blank',
                "Command 'blank': 'udp.port' is required with a payload (a number, or a {config_field} "
                'placeholder)'),
               ('commands.a_bool',
                "Command 'a_bool': 'udp.port' must be a number or a {config_field} placeholder"),
               ('commands.a_float',
                "Command 'a_float': 'udp.port' must be a number or a {config_field} placeholder"),
               ('commands.a_list',
                "Command 'a_list': 'udp.port' must be a number or a {config_field} placeholder"),
               ('commands.zero', "Command 'zero': 'udp.port' must be between 1 and 65535, got 0"),
               ('commands.high', "Command 'high': 'udp.port' must be between 1 and 65535, got 65536"),
               ('commands.text_zero', "Command 'text_zero': 'udp.port' must be between 1 and 65535, got 0"),
               ('commands.text_high',
                "Command 'text_high': 'udp.port' must be between 1 and 65535, got 70000"),
               ('commands.text_word',
                "Command 'text_word': 'udp.port' 'nine' is neither a number nor a {config_field} "
                'placeholder'),
               ('commands.text_negative',
                "Command 'text_negative': 'udp.port' '-9' is neither a number nor a {config_field} "
                'placeholder'),
               ('commands.undeclared',
                "Command 'undeclared': 'udp.port' names config field 'nope', which the driver does not "
                'declare'),
               ('commands.spec',
                "Command 'spec': 'udp.port' '{udp_port:d}' is neither a number nor a {config_field} "
                'placeholder')],
 'udp_magic_packets': [('commands.state_only',
                        "Command 'state_only': 'udp.magic_packet' names 'power', which is not a config "
                        'field. Declare it in config_schema so the MAC can be typed in; a state variable of '
                        'the same name is read first once the device reports one'),
                       ('commands.blank',
                        "Command 'blank': 'udp' needs a 'payload' (the datagram) or a 'magic_packet' (the "
                        'field holding the MAC to wake)'),
                       ('commands.blank', "Command 'blank': 'udp.magic_packet' must name a config field"),
                       ('commands.a_number',
                        "Command 'a_number': 'udp.magic_packet' must name a config field"),
                       ('commands.false', "Command 'false': 'udp.magic_packet' must name a config field")],
 'param_free_text_aids': [('commands.c',
                           "Command 'c' param 'b': pattern: regex '(a+)+$' has a nested quantifier; this can "
                           'cause catastrophic backtracking against hostile input'),
                          ('commands.c',
                           "Command 'c' param 'c': pattern: invalid regex '(': missing ), unterminated "
                           'subpattern at position 0'),
                          ('commands.c', "Command 'c' param 'd': min must be a number"),
                          ('commands.c', "Command 'c' param 'd': max must be a number"),
                          ('commands.c', "Command 'c' param 'e': min (5) must be <= max (1)"),
                          ('commands.c', "Command 'c' param 'g': min (True) must be <= max (0)"),
                          ('commands.c', "Command 'c' param 'h': decimals must be a non-negative integer"),
                          ('commands.c', "Command 'c' param 'i': decimals must be a non-negative integer"),
                          ('commands.c', "Command 'c' param 'j': decimals must be a non-negative integer"),
                          ('commands.c', "Command 'c' param 'l': trim must be true or false")],
 'param_maps': [('commands.c',
                 "Command 'c' param 'a': map must be a non-empty mapping of value -> wire value"),
                ('commands.c',
                 "Command 'c' param 'b': map must be a non-empty mapping of value -> wire value"),
                ('commands.c', "Command 'c' param 'd': map entries must be scalar value -> wire value pairs"),
                ('commands.c', "Command 'c' param 'e': map entries must be scalar value -> wire value pairs"),
                ('commands.c',
                 "Command 'c' param 'f': map entries must be scalar value -> wire value pairs")],
 'param_options_state': [('commands.c', "Command 'c' param 'a': options_state must be a non-empty string"),
                         ('commands.c', "Command 'c' param 'b': options_state must be a non-empty string")],
 'param_options_from': [('commands.c',
                         "Command 'c' param 'a': options_from must be a mapping with 'param' and 'source'"),
                        ('commands.c',
                         "Command 'c' param 'b': options_from.source must be one of ['child_schema']"),
                        ('commands.c', "Command 'c' param 'b': options_from.param must name a sibling param"),
                        ('commands.c',
                         "Command 'c' param 'c': options_from.source must be one of ['child_schema']"),
                        ('commands.c', "Command 'c' param 'd': options_from.param must name a sibling param"),
                        ('commands.c', "Command 'c' param 'e': options_from.param must name a sibling param"),
                        ('commands.c',
                         "Command 'c' param 'f': options_from.param 'nope' is not a param of this command"),
                        ('commands.c',
                         "Command 'c' param 'g': options_from.param 'level' must be a child_id param for "
                         "source 'child_schema'")],
 'param_options_from_lists': [('commands.c',
                               "Command 'c' param 'a': options_from.types must be a non-empty list of "
                               "['string', 'integer', 'number', 'boolean', 'enum', 'float']"),
                              ('commands.c',
                               "Command 'c' param 'b': options_from.types must be a non-empty list of "
                               "['string', 'integer', 'number', 'boolean', 'enum', 'float']"),
                              ('commands.c',
                               "Command 'c' param 'c': options_from.types must be a non-empty list of "
                               "['string', 'integer', 'number', 'boolean', 'enum', 'float']"),
                              ('commands.c',
                               "Command 'c' param 'e': options_from.units must be a non-empty list of unit "
                               'names, e.g. ["dB"]'),
                              ('commands.c',
                               "Command 'c' param 'f': options_from.units must be a non-empty list of unit "
                               'names, e.g. ["dB"]'),
                              ('commands.c',
                               "Command 'c' param 'g': options_from.units must be a non-empty list of unit "
                               'names, e.g. ["dB"]'),
                              ('commands.c',
                               "Command 'c' param 'i': options_from.source must be one of ['child_schema']"),
                              ('commands.c',
                               "Command 'c' param 'i': options_from.param 'nope' is not a param of this "
                               'command'),
                              ('commands.c',
                               "Command 'c' param 'i': options_from.types must be a non-empty list of "
                               "['string', 'integer', 'number', 'boolean', 'enum', 'float']"),
                              ('commands.c',
                               "Command 'c' param 'i': options_from.units must be a non-empty list of unit "
                               'names, e.g. ["dB"]')],
 'param_type_from': [('commands.c',
                      "Command 'c' param 'odd': options_from must be a mapping with 'param' and 'source'"),
                     ('commands.c', "Command 'c' param 'a': type_from must be a mapping with 'param'"),
                     ('commands.c', "Command 'c' param 'b': type_from.param must name a sibling param"),
                     ('commands.c', "Command 'c' param 'c': type_from.param must name a sibling param"),
                     ('commands.c',
                      "Command 'c' param 'd': type_from.param 'nope' is not a param of this command"),
                     ('commands.c',
                      "Command 'c' param 'e': type_from.param 'plain' must itself be an options_from "
                      'child_schema cascade'),
                     ('commands.c',
                      "Command 'c' param 'f': type_from.param 'odd' must itself be an options_from "
                      'child_schema cascade'),
                     ('commands.c',
                      "Command 'c' param 'h': type_from.param 'n' must itself be an options_from "
                      'child_schema cascade')],
 'param_aids_not_a_mapping': [],
 'param_aids_on_an_action': [('actions', "actions[2]: 'params' must be a mapping"),
                             ('actions[0]',
                              "actions[0] param 'a': pattern: invalid regex '(': missing ), unterminated "
                              'subpattern at position 0'),
                             ('actions[1]', "actions[1] param 'a': min (3) must be <= max (1)")],
 'sets_shapes': [('commands.a', "Command 'a': 'sets' must be a mapping"),
                 ('commands.b', "Command 'b': 'sets' must be a mapping"),
                 ('commands.f', "Command 'f': sets 'nope' which is not a declared state variable"),
                 ('commands.f', "Command 'f': sets 'level' which is not a declared state variable")],
 'sets_values': [('commands.b',
                  "Command 'b': sets.volume value '{w}' must be a literal or a bare {param} reference to a "
                  'declared parameter'),
                 ('commands.c',
                  "Command 'c': sets.volume value 'v={v}' must be a literal or a bare {param} reference to a "
                  'declared parameter'),
                 ('commands.d',
                  "Command 'd': sets.volume value '{v' must be a literal or a bare {param} reference to a "
                  'declared parameter'),
                 ('commands.e',
                  "Command 'e': sets.volume value '}' must be a literal or a bare {param} reference to a "
                  'declared parameter'),
                 ('commands.f',
                  "Command 'f': sets.volume value '{v}' must be a literal or a bare {param} reference to a "
                  'declared parameter'),
                 ('commands.g',
                  "Command 'g': sets.volume value '{{v}}' must be a literal or a bare {param} reference to a "
                  'declared parameter'),
                 ('commands.h', "Command 'h': sets 'nope' which is not a declared state variable"),
                 ('commands.h',
                  "Command 'h': sets.nope value '{x}' must be a literal or a bare {param} reference to a "
                  'declared parameter')],
 'sets_on_a_child': [('commands.one',
                      "Command 'one': sets 'gain' which is not a declared state variable (device-level or of "
                      'the addressed child type)'),
                     ('commands.two', "Command 'two': sets 'level' which is not a declared state variable"),
                     ('commands.undeclared_type',
                      "Command 'undeclared_type': sets 'level' which is not a declared state variable"),
                     ('commands.no_child_type',
                      "Command 'no_child_type': sets 'level' which is not a declared state variable"),
                     ('commands.params_not_mappings',
                      "Command 'params_not_mappings': sets 'level' which is not a declared state variable"),
                     ('commands',
                      "commands.undeclared_type.params.q: child_type 'nope' is not a declared "
                      'child_entity_type (declared: mic, zone)'),
                     ('commands',
                      "commands.no_child_type.params.q: a child_id param must declare 'child_type' (without "
                      'it the id cannot be resolved to a child type and is coerced as a plain integer)')],
 'query_for_on_a_command': [('commands.b', "Command 'b': query_for 'nope' is not a declared state variable"),
                            ('commands.c', "Command 'c': 'query_for' must name a state variable"),
                            ('commands.d', "Command 'd': 'query_for' must name a state variable"),
                            ('commands.g',
                             "Command 'g': query_for 'nope' is not a declared state variable (device-level "
                             'or of the addressed child type)'),
                            ('commands.i',
                             "Command 'i': query_for 'level' is not a declared state variable (device-level "
                             'or of the addressed child type)')],
 'confirm_values': [('commands.c', "Command 'c': 'confirm' must be a boolean or a message string"),
                    ('commands.e', "Command 'e': 'confirm' must be a boolean or a message string")],
 'sleeps_device': [('commands.off_unsaid',
                    "Command 'off_unsaid': 'sleeps_device' names 'lazy', which does not set "
                    'available_offline: true, so it cannot wake a device that is off the network'),
                   ('commands.off_unsaid',
                    "Command 'off_unsaid': 'sleeps_device' names 'lazy', which declares no "
                    'restarts_device_for: give it the measured seconds the device takes to come back after '
                    "'lazy'"),
                   ('commands.off_self',
                    "Command 'off_self': 'sleeps_device' names this command itself; name the command that "
                    'wakes the device'),
                   ('commands.off_blank',
                    "Command 'off_blank': 'sleeps_device' must name the command that wakes the device"),
                   ('commands.off_number',
                    "Command 'off_number': 'sleeps_device' must name the command that wakes the device"),
                   ('commands.off_missing',
                    "Command 'off_missing': 'sleeps_device' names 'wkae', which is not a declared command "
                    "(did you mean 'wake'?)"),
                   ('commands.off_far',
                    "Command 'off_far': 'sleeps_device' names 'zzzzzz', which is not a declared command"),
                   ('commands.odd', "Command 'odd': must be a dict")],
 'read_back_shapes': [('commands.c', "Command 'c': 'read_back' must be true, a query, or a list of queries"),
                      ('commands.d', "Command 'd': 'read_back' must be true, a query, or a list of queries"),
                      ('commands.e', "Command 'e': 'read_back' must be true, a query, or a list of queries"),
                      ('commands.f', "Command 'f': 'read_back' must be true, a query, or a list of queries"),
                      ('commands.g', "Command 'g': 'read_back' must be true, a query, or a list of queries"),
                      ('commands.i', "Command 'i': 'read_back' must be true, a query, or a list of queries")],
 'read_back_true': [('commands.a',
                     "Command 'a': 'read_back: true' reads back from an OSC command's own address or an HTTP "
                     "command's own path. Name the query that reads this command's value instead"),
                    ('commands.e',
                     "Command 'e': 'read_back' reads a value back over the device's connection, and a 'udp' "
                     'command is sent beside it, so nothing would answer'),
                    ('commands.f',
                     "Command 'f': 'read_back' reads a value back over the device's connection, and a 'ir' "
                     'command is sent beside it, so nothing would answer'),
                    ('commands.g',
                     "Command 'g': 'read_back: true' reads back from an OSC command's own address or an HTTP "
                     "command's own path. Name the query that reads this command's value instead")],
 'read_back_naming_commands': [('commands.a',
                                "Command 'a': read_back names this command itself; name the query that reads "
                                'its value'),
                               ('commands.b',
                                "Command 'b': read_back[1] names this command itself; name the query that "
                                'reads its value'),
                               ('commands.c',
                                "Command 'c': read_back names 'chained', which has a read_back of its own. "
                                'Name a command that only reads'),
                               ('commands.d',
                                "Command 'd': read_back[0] names 'beside', which is sent beside the device's "
                                'connection, so nothing would answer it'),
                               ('commands.d',
                                "Command 'd': read_back[1] names 'beside_ir', which is sent beside the "
                                "device's connection, so nothing would answer it"),
                               ('commands.e',
                                "Command 'e': read_back names 'get_zone', whose parameter 'zone' is not a "
                                'parameter of this command, so it would be sent without it'),
                               ('commands.g',
                                "Command 'g': read_back[0] names 'get_zone', whose parameter 'zone' is not a "
                                'parameter of this command, so it would be sent without it'),
                               ('commands.odd', "Command 'odd': must be a dict")],
 'read_back_on_osc': [('commands.b',
                       "Command 'b': read_back[0] 'vol' is neither a command nor an OSC address (an address "
                       'starts with /)'),
                      ('commands.c',
                       "Command 'c': read_back names this command itself; name the query that reads its "
                       'value')],
 'file_rules_on_yaml': [('commands.a', "Command 'a' param 'f': type 'file' requires a Python driver"),
                        ('commands.b',
                         "Command 'b' param 'f': accept applies only to a file param (type: file)"),
                        ('commands.c',
                         "Command 'c' param 'f': accept and max_bytes apply only to a file param (type: "
                         'file)'),
                        ('commands.d',
                         "Command 'd' param 'f': max_bytes applies only to a file param (type: file)"),
                        ('commands.e', "Command 'e': 'returns_file' requires a Python driver"),
                        ('commands.f', "Command 'f': 'returns_file' must be true or false")],
 'every_check_on_one_command': [('commands.a',
                                 "Command 'a': declares send and address and udp, but a command sends one "
                                 "way. Keep one of 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' "
                                 "(OSC), or 'udp' (a datagram beside the main transport)"),
                                ('commands.a',
                                 "Command 'a': args[0] unknown OSC type 'x' (expected one of "
                                 'f/i/s/h/d/T/F/N)'),
                                ('commands.a',
                                 "Command 'a': 'udp' declares both 'payload' and 'magic_packet'; a datagram "
                                 'is one or the other'),
                                ('commands.a',
                                 "Command 'a': 'udp.magic_packet' names 'nope', which is not a config field. "
                                 'Declare it in config_schema so the MAC can be typed in; a state variable '
                                 'of the same name is read first once the device reports one'),
                                ('commands.a',
                                 "Command 'a' param 'p': pattern: invalid regex '(': missing ), unterminated "
                                 'subpattern at position 0'),
                                ('commands.a', "Command 'a' param 'p': min (2) must be <= max (1)"),
                                ('commands.a',
                                 "Command 'a': sets 'nope' which is not a declared state variable"),
                                ('commands.a',
                                 "Command 'a': sets.nope value '{q}' must be a literal or a bare {param} "
                                 'reference to a declared parameter'),
                                ('commands.a',
                                 "Command 'a': query_for 'nope' is not a declared state variable"),
                                ('commands.a',
                                 "Command 'a': 'confirm' must be a boolean or a message string"),
                                ('commands.a',
                                 "Command 'a': 'sleeps_device' names this command itself; name the command "
                                 'that wakes the device'),
                                ('commands.a',
                                 "Command 'a': 'read_back' reads a value back over the device's connection, "
                                 "and a 'udp' command is sent beside it, so nothing would answer"),
                                ('commands.a', "Command 'a' param 'p': type 'file' requires a Python driver"),
                                ('commands.a', "Command 'a': 'returns_file' requires a Python driver")],
 'commands_after_responses_before_settings': [('responses[0]', 'Response 0: must be a mapping'),
                                              ('commands.a',
                                               "Command 'a': must have 'send' (TCP/serial), 'path'/'method' "
                                               "(HTTP), 'address' (OSC), or 'udp' (a datagram beside the "
                                               'main transport)'),
                                              ('device_settings.s', "Device setting 's': must be a mapping")]}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    issues = validate_driver_issues(definition)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [m for _, m in got]


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED)


# --- the Python surface: the four checks python_info shares ---------------

PYTHON_COMMANDS: dict[str, dict[str, Any]] = {
    "confirm": {
        "a": {"confirm": True}, "b": {"confirm": 5}, "c": {"confirm": UNEVALUATED},
    },
    "sleeps_device": {
        "off": {"sleeps_device": "wake"},
        "off2": {"sleeps_device": UNEVALUATED},
        "off3": {"sleeps_device": "gone"},
        "wake": {"available_offline": UNEVALUATED, "restarts_device_for": UNEVALUATED},
        "wake2": {"available_offline": False, "restarts_device_for": None},
        "off4": {"sleeps_device": "wake2"},
    },
    "sleeps_device_into_a_computed_map": {
        "off": {"sleeps_device": "gone"},
        "<unevaluated>": {},
    },
    "read_back": {
        "a": {"read_back": "VOL?"}, "b": {"read_back": UNEVALUATED}, "c": {"read_back": None},
    },
    "file_rules": {
        "a": {"params": {
            "f": {"type": "file", "accept": [".bin", "bin", UNEVALUATED], "max_bytes": 0},
            "g": {"type": "file", "accept": [], "max_bytes": True, "min": 1, "map": {}},
            "h": {"type": "file", "accept": UNEVALUATED, "max_bytes": UNEVALUATED},
            "i": {"type": "file", "accept": (".bin",), "max_bytes": 1024},
            "j": {"type": "file", "accept": ".bin", "max_bytes": 2 ** 31},
            "k": {"type": UNEVALUATED, "accept": 5},
            "l": {"type": "string", "max_bytes": 5},
        }, "returns_file": True},
        "b": {"returns_file": UNEVALUATED},
        "c": {"returns_file": "yes"},
        "d": {"params": UNEVALUATED},
    },
}

PYTHON_EXPECTED: dict[str, list[str]] = {'confirm': ["Command 'b': 'confirm' must be a boolean or a message string"],
 'sleeps_device': ["Command 'off3': 'sleeps_device' names 'gone', which is not a declared command",
                   "Command 'off4': 'sleeps_device' names 'wake2', which does not set available_offline: "
                   'true, so it cannot wake a device that is off the network',
                   "Command 'off4': 'sleeps_device' names 'wake2', which declares no restarts_device_for: "
                   "give it the measured seconds the device takes to come back after 'wake2'"],
 'sleeps_device_into_a_computed_map': [],
 'read_back': ["Command 'a': 'read_back' is read by YAML drivers; a Python driver reads the value back in "
               'its own send_command'],
 'file_rules': ['Command \'a\' param \'f\': accept must list file-name endings, e.g. [".bin"]',
                "Command 'a' param 'f': max_bytes must be a whole number of bytes from 1 to 1073741824 (1 "
                'GiB)',
                "Command 'a' param 'g': a file param takes no min, map",
                'Command \'a\' param \'g\': accept must list file-name endings, e.g. [".bin"]',
                "Command 'a' param 'g': max_bytes must be a whole number of bytes from 1 to 1073741824 (1 "
                'GiB)',
                'Command \'a\' param \'j\': accept must list file-name endings, e.g. [".bin"]',
                "Command 'a' param 'j': max_bytes must be a whole number of bytes from 1 to 1073741824 (1 "
                'GiB)',
                "Command 'a' param 'l': max_bytes applies only to a file param (type: file)",
                "Command 'c': 'returns_file' must be true or false"]}


def _python_info(case: str) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": PYTHON_COMMANDS[case],
    }


@pytest.mark.parametrize("case", sorted(PYTHON_EXPECTED))
def test_the_python_surface_gets_the_shared_checks(case: str) -> None:
    assert python_driver_info_issues(_python_info(case)) == PYTHON_EXPECTED[case]


def test_every_python_case_has_an_expectation() -> None:
    assert set(PYTHON_COMMANDS) == set(PYTHON_EXPECTED)
