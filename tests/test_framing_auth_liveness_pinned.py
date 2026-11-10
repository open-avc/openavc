"""Pins for a driver's link blocks as they load: ``auth:``, ``liveness:``,
``frame_parser:``, ``send_frame:`` and ``command_prefix`` / ``command_suffix``.

``auth:`` is a mapping whose ``type`` the runtime implements, on a transport
that carries a byte stream, with both prompts given and every pattern it
watches for safe to run; a type the runtime does not know is still held to
the prompts and patterns. ``liveness:`` needs a probe to send, an ``expect``
that is a safe regex, numbers no smaller than the watchdog takes, and ``args``
only on OSC. ``frame_parser:`` names a type and is held to that type's fields;
``send_frame:`` is held to its fields only when its type is known; a command
prefix or suffix is text. A ``push:`` block's own ``frame_parser`` is checked
with the push block, and a ``struct_frame`` there is held to its fields.

Each case is a set of top-level keys over one driver. The expected value is
every error ``validate_driver_issues`` reports for the whole definition, in
order, each with the section tag the Driver Builder anchors it to. None of
these shapes raises.

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

NAN = float("nan")
INF = float("inf")

# A case key set to this is left out of the definition altogether.
DROP = "<left out>"

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"noop": {"send": "NOOP\r"}},
}

PROMPTS = {"username_prompt": "login:", "password_prompt": "Password:"}
REDOS = "(a+)+$"


def auth(**block: Any) -> dict[str, Any]:
    return {"auth": block}


def live(**block: Any) -> dict[str, Any]:
    return {"liveness": {"send": "PING\r", **block}}


def frame(**block: Any) -> dict[str, Any]:
    return {"frame_parser": block}


def prefix(**block: Any) -> dict[str, Any]:
    return {"frame_parser": {"type": "length_prefix", **block}}


def fixed(**block: Any) -> dict[str, Any]:
    return {"frame_parser": {"type": "fixed_length", **block}}


def send(**block: Any) -> dict[str, Any]:
    return {"send_frame": block}


def push_frame(**block: Any) -> dict[str, Any]:
    return {"push": {"type": "tcp_listener", "port": 0, "frame_parser": block}}


def struct(**block: Any) -> dict[str, Any]:
    return push_frame(type="struct_frame", **block)


CASES: dict[str, dict[str, Any]] = {
    # --- auth: the block ------------------------------------------------
    "auth_none": {"auth": None},
    "auth_zero": {"auth": 0},
    "auth_empty_text": {"auth": ""},
    "auth_false": {"auth": False},
    "auth_empty_list": {"auth": []},
    "auth_a_list": {"auth": ["telnet_login"]},
    "auth_a_string": {"auth": "telnet_login"},
    "auth_empty_mapping": {"auth": {}},
    "auth_good": auth(**PROMPTS),
    "auth_good_typed": auth(type="telnet_login", **PROMPTS),
    "auth_good_full": auth(
        type="telnet_login", success_pattern="Welcome", failure_pattern="denied", **PROMPTS
    ),
    # --- auth: the type -------------------------------------------------
    "auth_type_unknown": auth(type="ssh_key", **PROMPTS),
    "auth_type_unknown_bare": auth(type="hashed_challenge"),
    "auth_type_cased": auth(type="TELNET_LOGIN", **PROMPTS),
    "auth_type_none": auth(type=None, **PROMPTS),
    "auth_type_empty": auth(type="", **PROMPTS),
    "auth_type_zero": auth(type=0, **PROMPTS),
    "auth_type_true": auth(type=True, **PROMPTS),
    "auth_type_list": auth(type=["telnet_login"], **PROMPTS),
    "auth_type_mapping": auth(type={"telnet_login": 1}, **PROMPTS),
    # --- auth: the transport --------------------------------------------
    "auth_on_serial": {"transport": "serial", **auth(**PROMPTS)},
    "auth_on_udp": {"transport": "udp", **auth(**PROMPTS)},
    "auth_on_http": {"transport": "http", **auth(**PROMPTS)},
    "auth_on_osc": {"transport": "osc", **auth(**PROMPTS)},
    "auth_on_bridge": {"transport": "bridge", **auth(**PROMPTS)},
    "auth_on_ssh": {"transport": "ssh", **auth(**PROMPTS)},
    "auth_on_empty_transport": {"transport": "", **auth(**PROMPTS)},
    "auth_on_no_transport": {"transport": None, **auth(**PROMPTS)},
    "auth_on_zero_transport": {"transport": 0, **auth(**PROMPTS)},
    "auth_on_listed_transport": {"transport": ["tcp"], **auth(**PROMPTS)},
    "auth_on_udp_unknown_type": {"transport": "udp", **auth(type="x")},
    # --- auth: the prompts ----------------------------------------------
    "auth_no_username_prompt": auth(password_prompt="Password:"),
    "auth_no_password_prompt": auth(username_prompt="login:"),
    "auth_prompts_empty": auth(username_prompt="", password_prompt=""),
    "auth_prompts_none": auth(username_prompt=None, password_prompt=None),
    "auth_prompts_zero": auth(username_prompt=0, password_prompt=False),
    "auth_prompts_empty_lists": auth(username_prompt=[], password_prompt={}),
    "auth_prompt_blank_line": auth(username_prompt=" ", password_prompt="\r\n"),
    "auth_prompts_not_text": auth(username_prompt=5, password_prompt=["Password:"]),
    "auth_prompt_true": auth(username_prompt=True, password_prompt="Password:"),
    "auth_patterns_not_text": auth(success_pattern=5, failure_pattern={"x": 1}, **PROMPTS),
    # --- auth: the patterns ---------------------------------------------
    "auth_username_redos": auth(username_prompt=REDOS, password_prompt="Password:"),
    "auth_password_redos": auth(username_prompt="login:", password_prompt="(x|x)*y"),
    "auth_success_redos": auth(success_pattern=REDOS, **PROMPTS),
    "auth_failure_redos": auth(failure_pattern="(.*)*z", **PROMPTS),
    "auth_success_empty": auth(success_pattern="", failure_pattern=None, **PROMPTS),
    "auth_success_invalid": auth(success_pattern="(", **PROMPTS),
    "auth_failure_invalid": auth(failure_pattern="[a-", **PROMPTS),
    "auth_every_pattern_redos": auth(
        username_prompt=REDOS, password_prompt="(b+)+c", success_pattern="(c+)+d",
        failure_pattern="(d+)+e",
    ),
    "auth_everything_wrong": {
        "transport": "http",
        **auth(type="digest", password_prompt="(a+)+$", failure_pattern="("),
    },
    # --- liveness: the block --------------------------------------------
    "live_none": {"liveness": None},
    "live_zero": {"liveness": 0},
    "live_empty_text": {"liveness": ""},
    "live_false": {"liveness": False},
    "live_empty_list": {"liveness": []},
    "live_a_list": {"liveness": ["PING"]},
    "live_a_string": {"liveness": "PING"},
    "live_empty_mapping": {"liveness": {}},
    "live_good": live(),
    "live_good_full": live(expect="^OK", interval=10, timeout=2.5, max_failures=3),
    # --- liveness: the transport ----------------------------------------
    "live_on_serial": {"transport": "serial", **live()},
    "live_on_udp": {"transport": "udp", **live()},
    "live_on_osc": {"transport": "osc", **live(send="/ping")},
    "live_on_http": {"transport": "http", **live()},
    "live_on_bridge": {"transport": "bridge", **live()},
    "live_on_ssh": {"transport": "ssh", **live()},
    "live_on_empty_transport": {"transport": "", **live()},
    "live_on_no_transport": {"transport": None, **live()},
    "live_on_listed_transport": {"transport": ["tcp"], **live()},
    # --- liveness: send -------------------------------------------------
    "live_send_empty": live(send=""),
    "live_send_none": live(send=None),
    "live_send_number": live(send=5),
    "live_send_list": live(send=["PING"]),
    "live_send_true": live(send=True),
    "live_send_space": live(send=" "),
    # --- liveness: expect -----------------------------------------------
    "live_expect_none": live(expect=None),
    "live_expect_empty": live(expect=""),
    "live_expect_number": live(expect=5),
    "live_expect_zero": live(expect=0),
    "live_expect_false": live(expect=False),
    "live_expect_list": live(expect=["OK"]),
    "live_expect_redos": live(expect=REDOS),
    "live_expect_invalid": live(expect="("),
    # --- liveness: interval and timeout ---------------------------------
    "live_interval_zero": live(interval=0),
    "live_interval_half": live(interval=0.5),
    "live_interval_one": live(interval=1),
    "live_interval_one_float": live(interval=1.0),
    "live_interval_negative": live(interval=-5),
    "live_interval_text": live(interval="5"),
    "live_interval_true": live(interval=True),
    "live_interval_false": live(interval=False),
    "live_interval_none": live(interval=None),
    "live_interval_nan": live(interval=NAN),
    "live_interval_inf": live(interval=INF),
    "live_interval_negative_inf": live(interval=-INF),
    "live_interval_list": live(interval=[30]),
    "live_timeout_tenth": live(timeout=0.1),
    "live_timeout_small": live(timeout=0.05),
    "live_timeout_zero": live(timeout=0),
    "live_timeout_text": live(timeout="1"),
    "live_timeout_true": live(timeout=True),
    "live_timeout_nan": live(timeout=NAN),
    "live_timeout_inf": live(timeout=INF),
    "live_both_numbers_wrong": live(interval=0, timeout=0),
    # --- liveness: max_failures -----------------------------------------
    "live_failures_one": live(max_failures=1),
    "live_failures_zero": live(max_failures=0),
    "live_failures_negative": live(max_failures=-1),
    "live_failures_float": live(max_failures=1.5),
    "live_failures_whole_float": live(max_failures=2.0),
    "live_failures_true": live(max_failures=True),
    "live_failures_false": live(max_failures=False),
    "live_failures_text": live(max_failures="2"),
    "live_failures_none": live(max_failures=None),
    "live_failures_nan": live(max_failures=NAN),
    # --- liveness: args -------------------------------------------------
    "live_args_on_tcp": live(args=["x"]),
    "live_args_empty_on_tcp": live(args=[]),
    "live_args_zero_on_tcp": live(args=0),
    "live_args_none_on_tcp": live(args=None),
    "live_args_on_osc": {"transport": "osc", **live(send="/ping", args=[1, "a"])},
    "live_args_empty_on_osc": {"transport": "osc", **live(send="/ping", args=[])},
    "live_args_text_on_osc": {"transport": "osc", **live(send="/ping", args="x")},
    "live_args_mapping_on_osc": {"transport": "osc", **live(send="/ping", args={})},
    "live_args_zero_on_osc": {"transport": "osc", **live(send="/ping", args=0)},
    "live_args_bad_tag_on_osc": {
        "transport": "osc", **live(send="/ping", args=[{"type": "nope", "value": 1}]),
    },
    "live_args_on_empty_transport": {"transport": "", **live(args=["x"])},
    "live_args_on_none_transport": {"transport": None, **live(args=["x"])},
    "live_args_on_absent_transport": {"transport": DROP, **live(args=["x"])},
    "live_on_absent_transport": {"transport": DROP, **live()},
    "auth_on_absent_transport": {"transport": DROP, **auth(**PROMPTS)},
    "live_everything_wrong": {
        "transport": "http",
        "liveness": {
            "send": "", "expect": REDOS, "interval": 0, "timeout": "x",
            "max_failures": 0, "args": "x",
        },
    },
    # --- frame_parser: the block ----------------------------------------
    "frame_none": {"frame_parser": None},
    "frame_zero": {"frame_parser": 0},
    "frame_empty_text": {"frame_parser": ""},
    "frame_false": {"frame_parser": False},
    "frame_empty_list": {"frame_parser": []},
    "frame_a_list": {"frame_parser": ["length_prefix"]},
    "frame_a_string": {"frame_parser": "length_prefix"},
    "frame_empty_mapping": {"frame_parser": {}},
    # --- frame_parser: the type -----------------------------------------
    "frame_type_empty": frame(type=""),
    "frame_type_none": frame(type=None),
    "frame_type_zero": frame(type=0),
    "frame_type_false": frame(type=False),
    "frame_type_empty_list": frame(type=[]),
    "frame_type_empty_mapping": frame(type={}),
    "frame_type_unknown": frame(type="delimiter"),
    "frame_type_struct": frame(type="struct_frame"),
    "frame_type_cased": frame(type="LENGTH_PREFIX"),
    "frame_type_number": frame(type=5),
    "frame_type_true": frame(type=True),
    "frame_type_list": frame(type=["length_prefix"]),
    "frame_type_mapping": frame(type={"length_prefix": 1}),
    "frame_type_unknown_with_fields": frame(type="slip", header_size=3, length=0),
    # --- frame_parser: length_prefix ------------------------------------
    "prefix_defaults": prefix(),
    "prefix_good_full": prefix(
        header_size=4, header_offset=8, length_offset=8, header_extra=4,
        length_endian="little", include_header=True,
    ),
    "prefix_size_1": prefix(header_size=1),
    "prefix_size_3": prefix(header_size=3),
    "prefix_size_0": prefix(header_size=0),
    "prefix_size_true": prefix(header_size=True),
    "prefix_size_whole_float": prefix(header_size=2.0),
    "prefix_size_float": prefix(header_size=2.5),
    "prefix_size_text": prefix(header_size="2"),
    "prefix_size_none": prefix(header_size=None),
    "prefix_size_list": prefix(header_size=[2]),
    "prefix_size_nan": prefix(header_size=NAN),
    "prefix_offset_negative": prefix(header_offset=-1),
    "prefix_offset_float": prefix(header_offset=1.5),
    "prefix_offset_true": prefix(header_offset=True),
    "prefix_offset_false": prefix(header_offset=False),
    "prefix_offset_text": prefix(header_offset="0"),
    "prefix_offset_none": prefix(header_offset=None),
    "prefix_length_offset_negative": prefix(length_offset=-1),
    "prefix_length_offset_float": prefix(length_offset=1.5),
    "prefix_length_offset_true": prefix(length_offset=True),
    "prefix_length_offset_text": prefix(length_offset="8"),
    "prefix_length_offset_none": prefix(length_offset=None),
    "prefix_header_extra_negative": prefix(header_extra=-2),
    "prefix_header_extra_float": prefix(header_extra=0.5),
    "prefix_header_extra_false": prefix(header_extra=False),
    "prefix_header_extra_none": prefix(header_extra=None),
    "prefix_endian_cased": prefix(length_endian="BIG"),
    "prefix_endian_none": prefix(length_endian=None),
    "prefix_endian_number": prefix(length_endian=1),
    "prefix_endian_list": prefix(length_endian=["big"]),
    "prefix_ignores_length": prefix(length=-1),
    "prefix_everything_wrong": prefix(
        header_size=3, header_offset="x", length_offset=-1, header_extra=True,
        length_endian="middle",
    ),
    # --- frame_parser: fixed_length -------------------------------------
    "fixed_defaults": fixed(),
    "fixed_good": fixed(length=4),
    "fixed_one": fixed(length=1),
    "fixed_zero": fixed(length=0),
    "fixed_negative": fixed(length=-1),
    "fixed_float": fixed(length=1.5),
    "fixed_whole_float": fixed(length=4.0),
    "fixed_true": fixed(length=True),
    "fixed_false": fixed(length=False),
    "fixed_text": fixed(length="4"),
    "fixed_none": fixed(length=None),
    "fixed_ignores_prefix_fields": fixed(header_size=3, length_endian="middle"),
    # --- send_frame: the block ------------------------------------------
    "send_none": {"send_frame": None},
    "send_zero": {"send_frame": 0},
    "send_empty_text": {"send_frame": ""},
    "send_false": {"send_frame": False},
    "send_empty_list": {"send_frame": []},
    "send_a_list": {"send_frame": ["length_prefix"]},
    "send_a_string": {"send_frame": "length_prefix"},
    "send_empty_mapping": {"send_frame": {}},
    "send_good_full": send(
        type="length_prefix", header="ISCP\\x00\\x00\\x00\\x10", after_length="\\x01",
        length_size=4, length_endian="little",
    ),
    # --- send_frame: the type -------------------------------------------
    "send_type_unknown": send(type="cobs"),
    "send_type_unknown_skips_fields": send(
        type="cobs", length_size=0, length_endian="x", header=5,
    ),
    "send_type_none": send(type=None),
    "send_type_empty": send(type=""),
    "send_type_zero": send(type=0),
    "send_type_list": send(type=["length_prefix"]),
    "send_type_mapping": send(type={"length_prefix": 1}),
    "send_type_cased": send(type="LENGTH_PREFIX"),
    "send_type_fixed": send(type="fixed_length"),
    # --- send_frame: the fields -----------------------------------------
    "send_size_one": send(length_size=1),
    "send_size_nine": send(length_size=9),
    "send_size_zero": send(length_size=0),
    "send_size_negative": send(length_size=-1),
    "send_size_float": send(length_size=1.5),
    "send_size_whole_float": send(length_size=4.0),
    "send_size_true": send(length_size=True),
    "send_size_text": send(length_size="4"),
    "send_size_none": send(length_size=None),
    "send_endian_cased": send(length_endian="Little"),
    "send_endian_none": send(length_endian=None),
    "send_endian_number": send(length_endian=0),
    "send_header_number": send(header=5),
    "send_header_empty": send(header=""),
    "send_header_none": send(header=None),
    "send_header_false": send(header=False),
    "send_header_list": send(header=["\\x02"]),
    "send_header_bytes": send(header=b"\x02"),
    "send_after_length_number": send(after_length=0),
    "send_after_length_mapping": send(after_length={}),
    "send_everything_wrong": send(
        length_size=0, length_endian="middle", header=1, after_length=[],
    ),
    # --- command_prefix and command_suffix ------------------------------
    "affix_good": {"command_prefix": "\\x02", "command_suffix": "\\x03"},
    "affix_empty": {"command_prefix": "", "command_suffix": ""},
    "affix_none": {"command_prefix": None, "command_suffix": None},
    "affix_prefix_number": {"command_prefix": 2},
    "affix_prefix_zero": {"command_prefix": 0},
    "affix_prefix_false": {"command_prefix": False},
    "affix_prefix_list": {"command_prefix": ["\\x02"]},
    "affix_prefix_mapping": {"command_prefix": {"serial": "\\x02", "tcp": ""}},
    "affix_suffix_true": {"command_suffix": True},
    "affix_suffix_bytes": {"command_suffix": b"\r"},
    "affix_both_wrong": {"command_prefix": 1, "command_suffix": [1]},
    # --- push: a tcp_listener's frame_parser ----------------------------
    "push_frame_struct_defaults": struct(),
    "push_frame_struct_good": struct(
        header_reserve=2, mid_reserve=1, trailer_reserve=2, length_size=4,
        length_adjust=-3, length_endian="little",
    ),
    "push_frame_header_reserve_negative": struct(header_reserve=-1),
    "push_frame_header_reserve_true": struct(header_reserve=True),
    "push_frame_mid_reserve_float": struct(mid_reserve=1.5),
    "push_frame_trailer_reserve_text": struct(trailer_reserve="2"),
    "push_frame_trailer_reserve_none": struct(trailer_reserve=None),
    "push_frame_length_size_3": struct(length_size=3),
    "push_frame_length_size_true": struct(length_size=True),
    "push_frame_length_size_whole_float": struct(length_size=2.0),
    "push_frame_length_size_none": struct(length_size=None),
    "push_frame_adjust_negative": struct(length_adjust=-5),
    "push_frame_adjust_float": struct(length_adjust=1.5),
    "push_frame_adjust_text": struct(length_adjust="1"),
    "push_frame_endian_cased": struct(length_endian="BIG"),
    "push_frame_endian_none": struct(length_endian=None),
    "push_frame_struct_everything_wrong": struct(
        header_reserve=-1, mid_reserve="x", trailer_reserve=1.5, length_size=3,
        length_adjust=True, length_endian="middle",
    ),
    "push_frame_prefix_unchecked": push_frame(
        type="length_prefix", header_size=3, header_offset="x", length_endian="middle",
    ),
    "push_frame_fixed_unchecked": push_frame(type="fixed_length", length=0),
    "push_frame_type_list": push_frame(type=["struct_frame"]),
    "push_frame_type_mapping": push_frame(type={"struct_frame": 1}),
    "push_frame_type_cased": push_frame(type="STRUCT_FRAME"),
    "push_frame_beside_top_level": {
        **struct(length_size=3), **prefix(header_size=3),
        "send_frame": {"length_size": 0},
    },
    # --- the order across sections --------------------------------------
    "every_block_wrong": {
        "transport": "udp",
        "command_prefix": 1,
        "command_suffix": 2,
        "push": {"type": "tcp_listener", "port": 0, "frame_parser": {
            "type": "struct_frame", "length_size": 3,
        }},
        "auth": {"type": "x"},
        "liveness": {"send": "", "max_failures": 0},
        "actions": [{"id": "", "kind": "command"}],
        "state_variables": {"power": {"type": "nope", "label": "Power"}},
        "frame_parser": {"type": "length_prefix", "header_size": 3},
        "send_frame": {"length_size": 0, "header": 1},
        "child_entity_types": {"zone": {"id_format": {"type": "nope"}}},
    },
    "every_block_wrong_strict": {
        "transport": "osc",
        "auth": {"type": "x", "zz": 1},
        "liveness": {"send": "/p", "args": "x", "zz": 1},
        "frame_parser": {"type": "nope", "zz": 1},
        "send_frame": {"type": "nope", "zz": 1},
    },
}

STRICT = {"every_block_wrong_strict"}


EXPECTED: dict[str, list[tuple[str, str]]] = {'affix_both_wrong': [('command_prefix', 'command_prefix: must be a string'),
                      ('command_suffix', 'command_suffix: must be a string')],
 'affix_empty': [],
 'affix_good': [],
 'affix_none': [],
 'affix_prefix_false': [('command_prefix', 'command_prefix: must be a string')],
 'affix_prefix_list': [('command_prefix', 'command_prefix: must be a string')],
 'affix_prefix_mapping': [('command_prefix', 'command_prefix: must be a string')],
 'affix_prefix_number': [('command_prefix', 'command_prefix: must be a string')],
 'affix_prefix_zero': [('command_prefix', 'command_prefix: must be a string')],
 'affix_suffix_bytes': [('command_suffix', 'command_suffix: must be a string')],
 'affix_suffix_true': [('command_suffix', 'command_suffix: must be a string')],
 'auth_a_list': [('auth', 'auth: must be a mapping')],
 'auth_a_string': [('auth', 'auth: must be a mapping')],
 'auth_empty_list': [('auth', 'auth: must be a mapping')],
 'auth_empty_mapping': [('auth', "auth: missing required 'username_prompt'"),
                        ('auth', "auth: missing required 'password_prompt'")],
 'auth_empty_text': [('auth', 'auth: must be a mapping')],
 'auth_every_pattern_redos': [('auth',
                               "auth.username_prompt: regex '(a+)+$' has a nested quantifier; this "
                               'can cause catastrophic backtracking against hostile input'),
                              ('auth',
                               "auth.password_prompt: regex '(b+)+c' has a nested quantifier; this "
                               'can cause catastrophic backtracking against hostile input'),
                              ('auth',
                               "auth.success_pattern: regex '(c+)+d' has a nested quantifier; this "
                               'can cause catastrophic backtracking against hostile input'),
                              ('auth',
                               "auth.failure_pattern: regex '(d+)+e' has a nested quantifier; this "
                               'can cause catastrophic backtracking against hostile input')],
 'auth_everything_wrong': [('auth', "auth: unsupported type 'digest' (only 'telnet_login')"),
                           ('auth',
                            'auth: login handshake is only supported on tcp/serial transports, not '
                            "'http'"),
                           ('auth', "auth: missing required 'username_prompt'"),
                           ('auth',
                            "auth.password_prompt: regex '(a+)+$' has a nested quantifier; this "
                            'can cause catastrophic backtracking against hostile input'),
                           ('auth',
                            "auth.failure_pattern: invalid regex '(': missing ), unterminated "
                            'subpattern at position 0')],
 'auth_failure_invalid': [('auth',
                           "auth.failure_pattern: invalid regex '[a-': unterminated character set "
                           'at position 0')],
 'auth_failure_redos': [('auth',
                         "auth.failure_pattern: regex '(.*)*z' has a nested quantifier; this can "
                         'cause catastrophic backtracking against hostile input')],
 'auth_false': [('auth', 'auth: must be a mapping')],
 'auth_good': [],
 'auth_good_full': [],
 'auth_good_typed': [],
 'auth_no_password_prompt': [('auth', "auth: missing required 'password_prompt'")],
 'auth_no_username_prompt': [('auth', "auth: missing required 'username_prompt'")],
 'auth_none': [],
 'auth_on_absent_transport': [('', 'Missing required field: transport')],
 'auth_on_bridge': [('auth',
                     'auth: login handshake is only supported on tcp/serial transports, not '
                     "'bridge'")],
 'auth_on_empty_transport': [],
 'auth_on_http': [('auth',
                   "auth: login handshake is only supported on tcp/serial transports, not 'http'")],
 'auth_on_listed_transport': [('', "Unsupported transport: ['tcp']"),
                              ('auth',
                               'auth: login handshake is only supported on tcp/serial transports, '
                               "not '['tcp']'")],
 'auth_on_no_transport': [],
 'auth_on_osc': [('auth',
                  "auth: login handshake is only supported on tcp/serial transports, not 'osc'")],
 'auth_on_serial': [],
 'auth_on_ssh': [('', 'Unsupported transport: ssh'),
                 ('auth',
                  "auth: login handshake is only supported on tcp/serial transports, not 'ssh'")],
 'auth_on_udp': [('auth',
                  "auth: login handshake is only supported on tcp/serial transports, not 'udp'")],
 'auth_on_udp_unknown_type': [('auth', "auth: unsupported type 'x' (only 'telnet_login')"),
                              ('auth',
                               'auth: login handshake is only supported on tcp/serial transports, '
                               "not 'udp'"),
                              ('auth', "auth: missing required 'username_prompt'"),
                              ('auth', "auth: missing required 'password_prompt'")],
 'auth_on_zero_transport': [],
 'auth_password_redos': [('auth',
                          "auth.password_prompt: regex '(x|x)*y' has duplicate alternatives under "
                          'a quantifier; this can cause catastrophic backtracking against hostile '
                          'input')],
 'auth_patterns_not_text': [('auth', 'auth.success_pattern: pattern must be a string, got int'),
                            ('auth', 'auth.failure_pattern: pattern must be a string, got dict')],
 'auth_prompt_blank_line': [],
 'auth_prompt_true': [('auth', 'auth.username_prompt: pattern must be a string, got bool')],
 'auth_prompts_empty': [('auth', "auth: missing required 'username_prompt'"),
                        ('auth', "auth: missing required 'password_prompt'")],
 'auth_prompts_empty_lists': [('auth', "auth: missing required 'username_prompt'"),
                              ('auth', "auth: missing required 'password_prompt'")],
 'auth_prompts_none': [('auth', "auth: missing required 'username_prompt'"),
                       ('auth', "auth: missing required 'password_prompt'")],
 'auth_prompts_not_text': [('auth', 'auth.username_prompt: pattern must be a string, got int'),
                           ('auth', 'auth.password_prompt: pattern must be a string, got list')],
 'auth_prompts_zero': [('auth', "auth: missing required 'username_prompt'"),
                       ('auth', "auth: missing required 'password_prompt'")],
 'auth_success_empty': [],
 'auth_success_invalid': [('auth',
                           "auth.success_pattern: invalid regex '(': missing ), unterminated "
                           'subpattern at position 0')],
 'auth_success_redos': [('auth',
                         "auth.success_pattern: regex '(a+)+$' has a nested quantifier; this can "
                         'cause catastrophic backtracking against hostile input')],
 'auth_type_cased': [('auth', "auth: unsupported type 'TELNET_LOGIN' (only 'telnet_login')")],
 'auth_type_empty': [('auth', "auth: unsupported type '' (only 'telnet_login')")],
 'auth_type_list': [('auth', "auth: unsupported type '['telnet_login']' (only 'telnet_login')")],
 'auth_type_mapping': [('auth',
                        "auth: unsupported type '{'telnet_login': 1}' (only 'telnet_login')")],
 'auth_type_none': [('auth', "auth: unsupported type 'None' (only 'telnet_login')")],
 'auth_type_true': [('auth', "auth: unsupported type 'True' (only 'telnet_login')")],
 'auth_type_unknown': [('auth', "auth: unsupported type 'ssh_key' (only 'telnet_login')")],
 'auth_type_unknown_bare': [('auth',
                             "auth: unsupported type 'hashed_challenge' (only 'telnet_login')"),
                            ('auth', "auth: missing required 'username_prompt'"),
                            ('auth', "auth: missing required 'password_prompt'")],
 'auth_type_zero': [('auth', "auth: unsupported type '0' (only 'telnet_login')")],
 'auth_username_redos': [('auth',
                          "auth.username_prompt: regex '(a+)+$' has a nested quantifier; this can "
                          'cause catastrophic backtracking against hostile input')],
 'auth_zero': [('auth', 'auth: must be a mapping')],
 'every_block_wrong': [('command_prefix', 'command_prefix: must be a string'),
                       ('command_suffix', 'command_suffix: must be a string'),
                       ('push', 'push: frame_parser length_size must be 1, 2, or 4'),
                       ('auth', "auth: unsupported type 'x' (only 'telnet_login')"),
                       ('auth',
                        'auth: login handshake is only supported on tcp/serial transports, not '
                        "'udp'"),
                       ('auth', "auth: missing required 'username_prompt'"),
                       ('auth', "auth: missing required 'password_prompt'"),
                       ('liveness',
                        "liveness: missing required 'send' (the probe payload: a raw protocol "
                        'string, or an OSC address on osc)'),
                       ('liveness', "liveness: 'max_failures' must be an integer >= 1"),
                       ('actions', "actions[0]: missing required 'id' (non-empty string)"),
                       ('state_variables.power', "State variable 'power': unknown type 'nope'"),
                       ('frame_parser', 'frame_parser: header_size must be 1, 2, or 4 (got 3)'),
                       ('send_frame', 'send_frame: length_size must be a positive integer (got 0)'),
                       ('send_frame', 'send_frame: header must be a string (got 1)'),
                       ('child_entity_types.zone',
                        "child_entity_types.zone.id_format: unknown type 'nope' (expected "
                        "'integer' or 'string')")],
 'every_block_wrong_strict': [('auth', "auth: unsupported type 'x' (only 'telnet_login')"),
                              ('auth',
                               'auth: login handshake is only supported on tcp/serial transports, '
                               "not 'osc'"),
                              ('auth', "auth: missing required 'username_prompt'"),
                              ('auth', "auth: missing required 'password_prompt'"),
                              ('liveness', "liveness: 'args' must be a list"),
                              ('frame_parser',
                               "frame_parser: unknown type 'nope' (expected 'length_prefix' or "
                               "'fixed_length')"),
                              ('send_frame',
                               "send_frame: unknown type 'nope' (expected 'length_prefix')"),
                              ('auth', "auth: unknown key 'zz'"),
                              ('liveness', "liveness: unknown key 'zz'"),
                              ('send_frame', "send_frame: unknown key 'zz'")],
 'fixed_defaults': [],
 'fixed_false': [('frame_parser', 'frame_parser: length must be a positive integer (got False)')],
 'fixed_float': [('frame_parser', 'frame_parser: length must be a positive integer (got 1.5)')],
 'fixed_good': [],
 'fixed_ignores_prefix_fields': [],
 'fixed_negative': [('frame_parser', 'frame_parser: length must be a positive integer (got -1)')],
 'fixed_none': [('frame_parser', 'frame_parser: length must be a positive integer (got None)')],
 'fixed_one': [],
 'fixed_text': [('frame_parser', "frame_parser: length must be a positive integer (got '4')")],
 'fixed_true': [('frame_parser', 'frame_parser: length must be a positive integer (got True)')],
 'fixed_whole_float': [('frame_parser',
                        'frame_parser: length must be a positive integer (got 4.0)')],
 'fixed_zero': [('frame_parser', 'frame_parser: length must be a positive integer (got 0)')],
 'frame_a_list': [('frame_parser', 'frame_parser: must be a mapping')],
 'frame_a_string': [('frame_parser', 'frame_parser: must be a mapping')],
 'frame_empty_list': [('frame_parser', 'frame_parser: must be a mapping')],
 'frame_empty_mapping': [('frame_parser', "frame_parser: missing 'type'")],
 'frame_empty_text': [('frame_parser', 'frame_parser: must be a mapping')],
 'frame_false': [('frame_parser', 'frame_parser: must be a mapping')],
 'frame_none': [],
 'frame_type_cased': [('frame_parser',
                       "frame_parser: unknown type 'LENGTH_PREFIX' (expected 'length_prefix' or "
                       "'fixed_length')")],
 'frame_type_empty': [('frame_parser', "frame_parser: missing 'type'")],
 'frame_type_empty_list': [('frame_parser', "frame_parser: missing 'type'")],
 'frame_type_empty_mapping': [('frame_parser', "frame_parser: missing 'type'")],
 'frame_type_false': [('frame_parser', "frame_parser: missing 'type'")],
 'frame_type_list': [('frame_parser',
                      "frame_parser: unknown type '['length_prefix']' (expected 'length_prefix' or "
                      "'fixed_length')")],
 'frame_type_mapping': [('frame_parser',
                         "frame_parser: unknown type '{'length_prefix': 1}' (expected "
                         "'length_prefix' or 'fixed_length')")],
 'frame_type_none': [('frame_parser', "frame_parser: missing 'type'")],
 'frame_type_number': [('frame_parser',
                        "frame_parser: unknown type '5' (expected 'length_prefix' or "
                        "'fixed_length')")],
 'frame_type_struct': [('frame_parser',
                        "frame_parser: unknown type 'struct_frame' (expected 'length_prefix' or "
                        "'fixed_length')")],
 'frame_type_true': [('frame_parser',
                      "frame_parser: unknown type 'True' (expected 'length_prefix' or "
                      "'fixed_length')")],
 'frame_type_unknown': [('frame_parser',
                         "frame_parser: unknown type 'delimiter' (expected 'length_prefix' or "
                         "'fixed_length')")],
 'frame_type_unknown_with_fields': [('frame_parser',
                                     "frame_parser: unknown type 'slip' (expected 'length_prefix' "
                                     "or 'fixed_length')")],
 'frame_type_zero': [('frame_parser', "frame_parser: missing 'type'")],
 'frame_zero': [('frame_parser', 'frame_parser: must be a mapping')],
 'live_a_list': [('liveness', 'liveness: must be a mapping')],
 'live_a_string': [('liveness', 'liveness: must be a mapping')],
 'live_args_bad_tag_on_osc': [],
 'live_args_empty_on_osc': [],
 'live_args_empty_on_tcp': [('liveness', "liveness: 'args' is only valid on osc")],
 'live_args_mapping_on_osc': [('liveness', "liveness: 'args' must be a list")],
 'live_args_none_on_tcp': [],
 'live_args_on_absent_transport': [('', 'Missing required field: transport'),
                                   ('liveness', "liveness: 'args' is only valid on osc")],
 'live_args_on_empty_transport': [('liveness', "liveness: 'args' is only valid on osc")],
 'live_args_on_none_transport': [('liveness', "liveness: 'args' is only valid on osc")],
 'live_args_on_osc': [],
 'live_args_on_tcp': [('liveness', "liveness: 'args' is only valid on osc")],
 'live_args_text_on_osc': [('liveness', "liveness: 'args' must be a list")],
 'live_args_zero_on_osc': [('liveness', "liveness: 'args' must be a list")],
 'live_args_zero_on_tcp': [('liveness', "liveness: 'args' is only valid on osc")],
 'live_both_numbers_wrong': [('liveness', "liveness: 'interval' must be a number >= 1.0"),
                             ('liveness', "liveness: 'timeout' must be a number >= 0.1")],
 'live_empty_list': [('liveness', 'liveness: must be a mapping')],
 'live_empty_mapping': [('liveness',
                         "liveness: missing required 'send' (the probe payload: a raw protocol "
                         'string, or an OSC address on osc)')],
 'live_empty_text': [('liveness', 'liveness: must be a mapping')],
 'live_everything_wrong': [('liveness',
                            "liveness: not supported on transport 'http' (only "
                            'tcp/serial/udp/osc)'),
                           ('liveness',
                            "liveness: missing required 'send' (the probe payload: a raw protocol "
                            'string, or an OSC address on osc)'),
                           ('liveness',
                            "liveness.expect: regex '(a+)+$' has a nested quantifier; this can "
                            'cause catastrophic backtracking against hostile input'),
                           ('liveness', "liveness: 'interval' must be a number >= 1.0"),
                           ('liveness', "liveness: 'timeout' must be a number >= 0.1"),
                           ('liveness', "liveness: 'max_failures' must be an integer >= 1"),
                           ('liveness', "liveness: 'args' is only valid on osc")],
 'live_expect_empty': [('liveness', "liveness: 'expect' must be a regex string")],
 'live_expect_false': [('liveness', "liveness: 'expect' must be a regex string")],
 'live_expect_invalid': [('liveness',
                          "liveness.expect: invalid regex '(': missing ), unterminated subpattern "
                          'at position 0')],
 'live_expect_list': [('liveness', "liveness: 'expect' must be a regex string")],
 'live_expect_none': [],
 'live_expect_number': [('liveness', "liveness: 'expect' must be a regex string")],
 'live_expect_redos': [('liveness',
                        "liveness.expect: regex '(a+)+$' has a nested quantifier; this can cause "
                        'catastrophic backtracking against hostile input')],
 'live_expect_zero': [('liveness', "liveness: 'expect' must be a regex string")],
 'live_failures_false': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_failures_float': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_failures_nan': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_failures_negative': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_failures_none': [],
 'live_failures_one': [],
 'live_failures_text': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_failures_true': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_failures_whole_float': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_failures_zero': [('liveness', "liveness: 'max_failures' must be an integer >= 1")],
 'live_false': [('liveness', 'liveness: must be a mapping')],
 'live_good': [],
 'live_good_full': [],
 'live_interval_false': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_interval_half': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_interval_inf': [],
 'live_interval_list': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_interval_nan': [],
 'live_interval_negative': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_interval_negative_inf': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_interval_none': [],
 'live_interval_one': [],
 'live_interval_one_float': [],
 'live_interval_text': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_interval_true': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_interval_zero': [('liveness', "liveness: 'interval' must be a number >= 1.0")],
 'live_none': [],
 'live_on_absent_transport': [('', 'Missing required field: transport')],
 'live_on_bridge': [('liveness',
                     "liveness: not supported on transport 'bridge' (only tcp/serial/udp/osc)")],
 'live_on_empty_transport': [],
 'live_on_http': [('liveness',
                   "liveness: not supported on transport 'http' (only tcp/serial/udp/osc)")],
 'live_on_listed_transport': [('', "Unsupported transport: ['tcp']"),
                              ('liveness',
                               "liveness: not supported on transport '['tcp']' (only "
                               'tcp/serial/udp/osc)')],
 'live_on_no_transport': [],
 'live_on_osc': [],
 'live_on_serial': [],
 'live_on_ssh': [('', 'Unsupported transport: ssh'),
                 ('liveness',
                  "liveness: not supported on transport 'ssh' (only tcp/serial/udp/osc)")],
 'live_on_udp': [],
 'live_send_empty': [('liveness',
                      "liveness: missing required 'send' (the probe payload: a raw protocol "
                      'string, or an OSC address on osc)')],
 'live_send_list': [('liveness',
                     "liveness: missing required 'send' (the probe payload: a raw protocol string, "
                     'or an OSC address on osc)')],
 'live_send_none': [('liveness',
                     "liveness: missing required 'send' (the probe payload: a raw protocol string, "
                     'or an OSC address on osc)')],
 'live_send_number': [('liveness',
                       "liveness: missing required 'send' (the probe payload: a raw protocol "
                       'string, or an OSC address on osc)')],
 'live_send_space': [],
 'live_send_true': [('liveness',
                     "liveness: missing required 'send' (the probe payload: a raw protocol string, "
                     'or an OSC address on osc)')],
 'live_timeout_inf': [],
 'live_timeout_nan': [],
 'live_timeout_small': [('liveness', "liveness: 'timeout' must be a number >= 0.1")],
 'live_timeout_tenth': [],
 'live_timeout_text': [('liveness', "liveness: 'timeout' must be a number >= 0.1")],
 'live_timeout_true': [('liveness', "liveness: 'timeout' must be a number >= 0.1")],
 'live_timeout_zero': [('liveness', "liveness: 'timeout' must be a number >= 0.1")],
 'live_zero': [('liveness', 'liveness: must be a mapping')],
 'prefix_defaults': [],
 'prefix_endian_cased': [('frame_parser',
                          "frame_parser: length_endian must be 'big' or 'little' (got 'BIG')")],
 'prefix_endian_list': [('frame_parser',
                         "frame_parser: length_endian must be 'big' or 'little' (got ['big'])")],
 'prefix_endian_none': [('frame_parser',
                         "frame_parser: length_endian must be 'big' or 'little' (got None)")],
 'prefix_endian_number': [('frame_parser',
                           "frame_parser: length_endian must be 'big' or 'little' (got 1)")],
 'prefix_everything_wrong': [('frame_parser',
                              'frame_parser: header_size must be 1, 2, or 4 (got 3)'),
                             ('frame_parser',
                              "frame_parser: header_offset must be an integer (got 'x')"),
                             ('frame_parser',
                              'frame_parser: length_offset must be a non-negative integer (got '
                              '-1)'),
                             ('frame_parser',
                              'frame_parser: header_extra must be a non-negative integer (got '
                              'True)'),
                             ('frame_parser',
                              "frame_parser: length_endian must be 'big' or 'little' (got "
                              "'middle')")],
 'prefix_good_full': [],
 'prefix_header_extra_false': [('frame_parser',
                                'frame_parser: header_extra must be a non-negative integer (got '
                                'False)')],
 'prefix_header_extra_float': [('frame_parser',
                                'frame_parser: header_extra must be a non-negative integer (got '
                                '0.5)')],
 'prefix_header_extra_negative': [('frame_parser',
                                   'frame_parser: header_extra must be a non-negative integer (got '
                                   '-2)')],
 'prefix_header_extra_none': [('frame_parser',
                               'frame_parser: header_extra must be a non-negative integer (got '
                               'None)')],
 'prefix_ignores_length': [],
 'prefix_length_offset_float': [('frame_parser',
                                 'frame_parser: length_offset must be a non-negative integer (got '
                                 '1.5)')],
 'prefix_length_offset_negative': [('frame_parser',
                                    'frame_parser: length_offset must be a non-negative integer '
                                    '(got -1)')],
 'prefix_length_offset_none': [('frame_parser',
                                'frame_parser: length_offset must be a non-negative integer (got '
                                'None)')],
 'prefix_length_offset_text': [('frame_parser',
                                'frame_parser: length_offset must be a non-negative integer (got '
                                "'8')")],
 'prefix_length_offset_true': [('frame_parser',
                                'frame_parser: length_offset must be a non-negative integer (got '
                                'True)')],
 'prefix_offset_false': [('frame_parser',
                          'frame_parser: header_offset must be an integer (got False)')],
 'prefix_offset_float': [('frame_parser',
                          'frame_parser: header_offset must be an integer (got 1.5)')],
 'prefix_offset_negative': [],
 'prefix_offset_none': [('frame_parser',
                         'frame_parser: header_offset must be an integer (got None)')],
 'prefix_offset_text': [('frame_parser',
                         "frame_parser: header_offset must be an integer (got '0')")],
 'prefix_offset_true': [('frame_parser',
                         'frame_parser: header_offset must be an integer (got True)')],
 'prefix_size_0': [('frame_parser', 'frame_parser: header_size must be 1, 2, or 4 (got 0)')],
 'prefix_size_1': [],
 'prefix_size_3': [('frame_parser', 'frame_parser: header_size must be 1, 2, or 4 (got 3)')],
 'prefix_size_float': [('frame_parser', 'frame_parser: header_size must be 1, 2, or 4 (got 2.5)')],
 'prefix_size_list': [('frame_parser', 'frame_parser: header_size must be 1, 2, or 4 (got [2])')],
 'prefix_size_nan': [('frame_parser', 'frame_parser: header_size must be 1, 2, or 4 (got nan)')],
 'prefix_size_none': [('frame_parser', 'frame_parser: header_size must be 1, 2, or 4 (got None)')],
 'prefix_size_text': [('frame_parser', "frame_parser: header_size must be 1, 2, or 4 (got '2')")],
 'prefix_size_true': [],
 'prefix_size_whole_float': [],
 'push_frame_adjust_float': [('push', 'push: frame_parser length_adjust must be an integer')],
 'push_frame_adjust_negative': [],
 'push_frame_adjust_text': [('push', 'push: frame_parser length_adjust must be an integer')],
 'push_frame_beside_top_level': [('push', 'push: frame_parser length_size must be 1, 2, or 4'),
                                 ('frame_parser',
                                  'frame_parser: header_size must be 1, 2, or 4 (got 3)'),
                                 ('send_frame',
                                  'send_frame: length_size must be a positive integer (got 0)')],
 'push_frame_endian_cased': [('push',
                              "push: frame_parser length_endian must be 'big' or 'little'")],
 'push_frame_endian_none': [('push', "push: frame_parser length_endian must be 'big' or 'little'")],
 'push_frame_fixed_unchecked': [],
 'push_frame_header_reserve_negative': [('push',
                                         'push: frame_parser header_reserve must be a non-negative '
                                         'integer')],
 'push_frame_header_reserve_true': [('push',
                                     'push: frame_parser header_reserve must be a non-negative '
                                     'integer')],
 'push_frame_length_size_3': [('push', 'push: frame_parser length_size must be 1, 2, or 4')],
 'push_frame_length_size_none': [('push', 'push: frame_parser length_size must be 1, 2, or 4')],
 'push_frame_length_size_true': [],
 'push_frame_length_size_whole_float': [],
 'push_frame_mid_reserve_float': [('push',
                                   'push: frame_parser mid_reserve must be a non-negative '
                                   'integer')],
 'push_frame_prefix_unchecked': [],
 'push_frame_struct_defaults': [],
 'push_frame_struct_everything_wrong': [('push',
                                         'push: frame_parser header_reserve must be a non-negative '
                                         'integer'),
                                        ('push',
                                         'push: frame_parser mid_reserve must be a non-negative '
                                         'integer'),
                                        ('push',
                                         'push: frame_parser trailer_reserve must be a '
                                         'non-negative integer'),
                                        ('push',
                                         'push: frame_parser length_size must be 1, 2, or 4'),
                                        ('push',
                                         'push: frame_parser length_adjust must be an integer'),
                                        ('push',
                                         "push: frame_parser length_endian must be 'big' or "
                                         "'little'")],
 'push_frame_struct_good': [],
 'push_frame_trailer_reserve_none': [('push',
                                      'push: frame_parser trailer_reserve must be a non-negative '
                                      'integer')],
 'push_frame_trailer_reserve_text': [('push',
                                      'push: frame_parser trailer_reserve must be a non-negative '
                                      'integer')],
 'push_frame_type_cased': [('push',
                            "push: frame_parser type 'STRUCT_FRAME' must be struct_frame, "
                            'length_prefix, or fixed_length')],
 'push_frame_type_list': [('push',
                           "push: frame_parser type ['struct_frame'] must be struct_frame, "
                           'length_prefix, or fixed_length')],
 'push_frame_type_mapping': [('push',
                              "push: frame_parser type {'struct_frame': 1} must be struct_frame, "
                              'length_prefix, or fixed_length')],
 'send_a_list': [('send_frame', 'send_frame: must be a mapping')],
 'send_a_string': [('send_frame', 'send_frame: must be a mapping')],
 'send_after_length_mapping': [('send_frame',
                                'send_frame: after_length must be a string (got {})')],
 'send_after_length_number': [('send_frame', 'send_frame: after_length must be a string (got 0)')],
 'send_empty_list': [('send_frame', 'send_frame: must be a mapping')],
 'send_empty_mapping': [],
 'send_empty_text': [('send_frame', 'send_frame: must be a mapping')],
 'send_endian_cased': [('send_frame',
                        "send_frame: length_endian must be 'big' or 'little' (got 'Little')")],
 'send_endian_none': [('send_frame',
                       "send_frame: length_endian must be 'big' or 'little' (got None)")],
 'send_endian_number': [('send_frame',
                         "send_frame: length_endian must be 'big' or 'little' (got 0)")],
 'send_everything_wrong': [('send_frame',
                            'send_frame: length_size must be a positive integer (got 0)'),
                           ('send_frame',
                            "send_frame: length_endian must be 'big' or 'little' (got 'middle')"),
                           ('send_frame', 'send_frame: header must be a string (got 1)'),
                           ('send_frame', 'send_frame: after_length must be a string (got [])')],
 'send_false': [('send_frame', 'send_frame: must be a mapping')],
 'send_good_full': [],
 'send_header_bytes': [('send_frame', "send_frame: header must be a string (got b'\\x02')")],
 'send_header_empty': [],
 'send_header_false': [('send_frame', 'send_frame: header must be a string (got False)')],
 'send_header_list': [('send_frame', "send_frame: header must be a string (got ['\\\\x02'])")],
 'send_header_none': [],
 'send_header_number': [('send_frame', 'send_frame: header must be a string (got 5)')],
 'send_none': [],
 'send_size_float': [('send_frame',
                      'send_frame: length_size must be a positive integer (got 1.5)')],
 'send_size_negative': [('send_frame',
                         'send_frame: length_size must be a positive integer (got -1)')],
 'send_size_nine': [],
 'send_size_none': [('send_frame',
                     'send_frame: length_size must be a positive integer (got None)')],
 'send_size_one': [],
 'send_size_text': [('send_frame', "send_frame: length_size must be a positive integer (got '4')")],
 'send_size_true': [('send_frame',
                     'send_frame: length_size must be a positive integer (got True)')],
 'send_size_whole_float': [('send_frame',
                            'send_frame: length_size must be a positive integer (got 4.0)')],
 'send_size_zero': [('send_frame', 'send_frame: length_size must be a positive integer (got 0)')],
 'send_type_cased': [('send_frame',
                      "send_frame: unknown type 'LENGTH_PREFIX' (expected 'length_prefix')")],
 'send_type_empty': [('send_frame', "send_frame: unknown type '' (expected 'length_prefix')")],
 'send_type_fixed': [('send_frame',
                      "send_frame: unknown type 'fixed_length' (expected 'length_prefix')")],
 'send_type_list': [('send_frame',
                     "send_frame: unknown type '['length_prefix']' (expected 'length_prefix')")],
 'send_type_mapping': [('send_frame',
                        "send_frame: unknown type '{'length_prefix': 1}' (expected "
                        "'length_prefix')")],
 'send_type_none': [('send_frame', "send_frame: unknown type 'None' (expected 'length_prefix')")],
 'send_type_unknown': [('send_frame',
                        "send_frame: unknown type 'cobs' (expected 'length_prefix')")],
 'send_type_unknown_skips_fields': [('send_frame',
                                     "send_frame: unknown type 'cobs' (expected 'length_prefix')")],
 'send_type_zero': [('send_frame', "send_frame: unknown type '0' (expected 'length_prefix')")],
 'send_zero': [('send_frame', 'send_frame: must be a mapping')]}


def _definition(case: str) -> dict[str, Any]:
    merged = {**BASE, **CASES[case]}
    return {key: value for key, value in merged.items() if value is not DROP}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    strict = case in STRICT
    issues = validate_driver_issues(definition, strict=strict)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition, strict=strict)) == [m for _, m in got]


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED)
