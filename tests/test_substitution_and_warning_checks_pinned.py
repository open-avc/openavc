"""Pins for two parts of a driver's checks: its ``{name}`` placeholders, and
the warnings an author sees beside the errors.

A ``{name}`` (or ``{name:spec}``) in a wire template has to name something
that fills it: a command's send, path, body, address, headers, its read-back
queries and its ``udp:`` payload, host and port take the command's own
parameters and the config, once the command declares parameters; a response
pattern and the query lists (the poll, the start-up and each rule's
``then_query``) take the config alone. Each message opens with where the
template sits, and the walk tags it from that opening.

The warnings never block a driver: a masked config field with a default, a
``port_open`` hint on a driver with no TCP transport, a command parameter with
no label, a poll that takes longer than its interval (at the default config
and at the largest roster the driver accepts), and a device whose silence
nothing would notice.

The token itself (an identifier in braces, with an optional format spec) is
pinned through every reader of it: the placeholder check, the runtime's
warning for a token that survives substitution, and the simulator's send
template reader and read-back coverage check.

Each YAML case is a set of top-level keys over one driver. The expected value
is every issue ``validate_driver_issues`` reports for the whole definition, in
order, each with its severity and the section tag the Driver Builder anchors
it to. A few shapes raise, and are pinned by the exception they raise.

These record what the checks say today, so a restructuring that keeps the
contract keeps every message, its position and its tag.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from openavc.drivers import compiled_protocol
from openavc.drivers.avcdriver_semantic import (
    UNEVALUATED_KEY,
    validate_driver_definition,
    validate_driver_issues,
    validate_driver_warnings,
    validate_substitutions,
)
from openavc.drivers.configurable import ConfigurableDriver
from openavc.simulator import validate as sim_validate
from openavc.simulator.send_templates import (
    declared_placeholders,
    is_passthrough_send,
    send_regex,
)

# A case key set to this is left out of the definition altogether.
DROP = "<left out>"

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "config_schema": {"zone": {"type": "integer", "label": "Zone"}},
    "default_config": {"zone": 1},
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {"noop": {"send": "NOOP\r"}},
}

LEVEL = {"level": {"type": "integer", "label": "Level"}}


def command(**fields: Any) -> dict[str, Any]:
    """One command ``set`` that declares a ``level`` parameter."""
    return {"commands": {"noop": {"send": "NOOP\r"}, "set": {"params": LEVEL, **fields}}}


def commands(**entries: Any) -> dict[str, Any]:
    return {"commands": {"noop": {"send": "NOOP\r"}, **entries}}


def poll(*queries: Any, **config: Any) -> dict[str, Any]:
    """A poll of ``queries`` at ``config`` over the base default config."""
    return {
        "polling": {"queries": list(queries)},
        "default_config": {"zone": 1, **config},
    }


RULE = {"match": r"L(\d+)", "set": {"level": "$1"}}

OUTPUTS = {
    "id_format": {"type": "integer", "min": 1, "max": 64},
    "instances": {"count_from": "outputs"},
}

CASES: dict[str, dict[str, Any]] = {
    # --- a command's templates ----------------------------------------------
    "cmd_clean": command(send="L{level} Z{zone} {child_id} {push_callback_url}\r"),
    "cmd_typo": command(send="L{levl}\r"),
    "cmd_no_hint": command(send="L{qqq}\r"),
    "cmd_spec_typo": command(send="L{levl:03d}\r"),
    "cmd_config_typo": command(send="Z{zon}\r"),
    "cmd_repeats_in_order": command(send="{b}{a}{level}{a}\r"),
    "cmd_each_field": command(
        send="S{s1}", path="/p/{p1}", body='{"v": {b1}}', address="/a/{a1}",
    ),
    "cmd_fields_not_text": command(send=["{s1}"], path=5, body=None, address={"x": "{a1}"}),
    "cmd_json_body": command(body='{"vol": {level}, "z": {"q": 1}}'),
    "cmd_headers": command(
        send="X", headers={"X-A": "{tokn}", "X-B": 5, 7: "{num}", "X:C": "{colon}"},
    ),
    "cmd_headers_not_mapping": command(send="X", headers=["{tokn}"]),
    "cmd_read_back_text": command(send="X", read_back="Q{lvl}"),
    "cmd_read_back_list": command(
        send="X", read_back=["Q{lvl}", "noop", 5, "Q{level}", "", "R{x}", "set"],
    ),
    "cmd_read_back_true": command(send="X", read_back=True),
    # A read-back naming a command is that command's, even when the name
    # itself reads as a placeholder.
    "cmd_read_back_names_a_templated_command": commands(
        **{"Q{lvl}": {"send": "Q\r"}},
        set={"send": "X", "params": LEVEL, "read_back": ["Q{lvl}", "R{lvl}"]},
        get={"send": "Y", "params": LEVEL, "read_back": "Q{lvl}"},
    ),
    "cmd_read_back_mapping": command(send="X", read_back={"q": "{x}"}),
    "cmd_udp": command(
        send="X",
        udp={"payload": "P{lvl}", "host": "{hst}", "port": "{prt}", "extra": "{x}"},
    ),
    "cmd_udp_port_number": command(send="X", udp={"payload": "P", "port": 9}),
    "cmd_udp_not_mapping": command(send="X", udp="{x}"),
    "cmd_every_place_in_order": command(
        send="{s}", path="{p}", body="{b}", address="{a}",
        headers={"H": "{h}"}, read_back=["{r}"], udp={"payload": "{u}", "host": "{uh}"},
    ),
    "cmd_no_params": commands(set={"send": "IN{input}\r"}),
    "cmd_params_empty": commands(set={"send": "IN{input}\r", "params": {}}),
    "cmd_params_list": commands(set={"send": "IN{input}\r", "params": ["input"]}),
    "cmd_params_unevaluated": commands(
        set={"send": "IN{input}\r", "params": {UNEVALUATED_KEY: True, "level": {}}},
    ),
    "cmd_params_key_not_text": commands(
        set={"send": "{True}{level}\r", "params": {True: {"type": "boolean"}, **LEVEL}},
    ),
    "cmd_not_mapping": commands(set="L{x}"),
    "commands_unevaluated": {
        "commands": {UNEVALUATED_KEY: True, "set": {"send": "{x}", "params": LEVEL}},
    },
    "commands_not_mapping": {"commands": [{"send": "{x}", "params": LEVEL}]},
    "commands_in_order": commands(
        b={"send": "{bb}", "params": LEVEL}, a={"send": "{aa}", "params": LEVEL},
    ),
    "cmd_name_with_colon": commands(**{"a:b": {"send": "{q}", "params": LEVEL}}),
    # --- response patterns --------------------------------------------------
    "resp_match_typo": {"responses": [{**RULE, "match": r"L(\d+) {zon}"}]},
    "resp_match_config": {"responses": [{**RULE, "match": r"Z{zone} L(\d+)"}]},
    "resp_match_param_name": {
        **command(send="L{level}"),
        "responses": [{**RULE, "match": r"L(\d+) {level}"}],
    },
    "resp_match_not_text": {"responses": [{**RULE, "match": 5}]},
    "resp_entry_not_mapping": {"responses": ["{x}", {**RULE, "match": r"L(\d+){q}"}]},
    "resp_not_list": {"responses": {"match": "{x}"}},
    # --- the query lists ----------------------------------------------------
    "poll_text": poll("Q{zon}", "Q{zone}", poll_interval=5),
    "poll_entries": poll(
        {"send": "S{a}"}, {"send": "", "address": "/a/{b}"}, {"address": "/a/{c}"},
        {"send": 5}, {"send": None, "address": None}, 5, ["{d}"], poll_interval=5,
    ),
    "poll_param_name": {**command(send="L{level}"), **poll("Q{level}", poll_interval=5)},
    "polling_not_mapping": {"polling": ["Q{x}"]},
    "poll_queries_not_list": {"polling": {"queries": "Q{x}"}},
    "on_connect": {"on_connect": ["X{q}", {"send": "Y{r}"}]},
    "on_connect_not_list": {"on_connect": "X{q}"},
    "then_query": {
        "responses": [
            "x",
            {**RULE, "then_query": ["Q{zz}", {"send": "R{yy}"}]},
            {**RULE, "then_query": "Q{ww}"},
        ],
    },
    "every_list_in_order": {
        **command(send="{c}"),
        "responses": [{**RULE, "match": r"L(\d+){m}", "then_query": ["{t}"]}],
        **poll("{p}", poll_interval=5),
        "on_connect": ["{o}"],
    },
    # --- warnings: a masked config field with a default ---------------------
    "masked_default_in_schema": {
        "config_schema": {"zone": BASE["config_schema"]["zone"],
                          "secret_key": {"type": "string", "secret": True, "default": "admin"}},
    },
    "masked_default_in_defaults": {
        "config_schema": {"password": {"type": "string"}},
        "default_config": {"zone": 1, "password": "admin"},
    },
    "masked_type_password": {
        "config_schema": {"pin": {"type": "password", "default": 1234}},
    },
    "masked_empty_defaults": {
        "config_schema": {
            "password": {"type": "string", "default": ""},
            "key": {"type": "string", "secret": True, "default": None},
            "code": {"type": "string", "secret": True, "default": 0},
            "flag": {"type": "string", "secret": True, "default": False},
        },
    },
    "masked_secret_not_true": {
        "config_schema": {"key": {"type": "string", "secret": "yes", "default": "x"}},
    },
    "masked_entry_not_mapping": {
        "config_schema": {"password": "x", "key": {"secret": True}},
        "default_config": {"key": "k"},
    },
    "masked_schema_not_mapping": {"config_schema": ["password"]},
    "masked_defaults_not_mapping": {
        "config_schema": {"password": {"type": "string"}},
        "default_config": ["password"],
    },
    # --- warnings: port_open on a driver with no TCP transport --------------
    "port_open_osc": {"transport": "osc", "discovery": {"port_open": [10023]}},
    "port_open_udp_with_tcp": {
        "transport": "udp", "transports": ["tcp"], "discovery": {"port_open": [6454]},
    },
    "port_open_extras_odd": {
        "transport": "udp", "transports": ["udp", 5, "", "osc", None],
        "discovery": {"port_open": [6454]},
    },
    "port_open_extras_not_list": {
        "transport": "udp", "transports": "tcp", "discovery": {"port_open": [6454]},
    },
    "port_open_empty": {"transport": "osc", "discovery": {"port_open": []}},
    "port_open_no_transport": {"transport": "", "discovery": {"port_open": [1]}},
    "port_open_transport_not_text": {
        "transport": 5, "transports": [None], "discovery": {"port_open": [1]},
    },
    "port_open_discovery_not_mapping": {"transport": "osc", "discovery": ["port_open"]},
    "port_open_tcp": {"discovery": {"port_open": [4998]}},
    # --- warnings: a command parameter with no label -----------------------
    "param_labels": commands(
        a={"send": "A{x}{y}{z}{w}{v}", "params": {
            "x": {"type": "integer"},
            "y": {"type": "integer", "label": "  "},
            "z": {"type": "integer", "label": 5},
            "w": "integer",
            "v": {"type": "integer", "label": " V "},
        }},
        b={"send": "B", "params": ["x"]},
        c="text",
    ),
    # --- warnings: a poll longer than its interval --------------------------
    "poll_long_default_gap": poll(*["Q"] * 30, poll_interval=1),
    "poll_fits": poll(*["Q"] * 20, poll_interval=1),
    "poll_exactly_fits": poll(*["Q"] * 4, poll_interval=0.2),
    "poll_with_delay": poll(*["Q"] * 10, poll_interval=1, inter_command_delay=0.2),
    "poll_delay_text": poll(*["Q"] * 10, poll_interval=1, inter_command_delay="0.2"),
    "poll_delay_bool": poll(*["Q"] * 30, poll_interval=1, inter_command_delay=True),
    "poll_delay_negative": poll(*["Q"] * 30, poll_interval=1, inter_command_delay=-1),
    "poll_delay_not_a_number": poll(*["Q"] * 30, poll_interval=1, inter_command_delay="x"),
    "poll_serial": {"transport": "serial", **poll(*["Q"] * 30, poll_interval=1)},
    "poll_udp_no_delay": {"transport": "udp", **poll(*["Q"] * 300, poll_interval=1)},
    "poll_udp_delay": {
        "transport": "udp", **poll("Q", "Q", "Q", poll_interval=1, inter_command_delay=0.5),
    },
    "poll_http_delay": {
        "transport": "http",
        **poll("/a", "/b", "/c", poll_interval=1.25, inter_command_delay=0.5),
    },
    "poll_osc": {"transport": "osc", **poll(*["/q"] * 300, poll_interval=1)},
    "poll_config_transport": poll(*["Q"] * 300, poll_interval=1, transport="udp"),
    "poll_config_transport_serial": {
        "transport": "udp", **poll(*["Q"] * 30, poll_interval=1, transport="serial"),
    },
    "poll_interval_missing": poll(*["Q"] * 300),
    "poll_interval_zero": poll(*["Q"] * 300, poll_interval=0),
    "poll_interval_negative": poll(*["Q"] * 300, poll_interval=-2),
    "poll_interval_text": poll(*["Q"] * 30, poll_interval="1"),
    "poll_interval_bool": poll(*["Q"] * 300, poll_interval=True),
    "poll_interval_nan": poll("Q", poll_interval="nan"),
    "poll_queries_mapping": {
        "polling": {"queries": {"a": "Q"}}, "default_config": {"zone": 1, "poll_interval": 1},
    },
    "poll_defaults_not_mapping": {"polling": {"queries": ["Q"] * 300}, "default_config": [1]},
    "poll_counts_each_form": poll(
        "Q", 5, None, {"send": "S"}, {"send": 5}, {"address": "/a"},
        {"send": "W", "when": "on"}, {"send": "W", "when": "off"},
        {"send": "W", "when": "missing"}, {"send": "W", "when": ""},
        {"send": "W", "when": 7}, {"send": "W", "when_state": {"power": True}},
        poll_interval=0.01, on=True, off=False,
    ),
    "poll_each_child": {
        "child_entity_types": {
            "output": OUTPUTS,
            "zone": {"id_format": {"type": "integer"}, "instances": {"count": 3}},
            "named": {"id_format": {"type": "string"}, "instances": {"ids": ["a", "b"]}},
            "unsized": {"id_format": {"type": "integer"}},
        },
        "config_schema": {**BASE["config_schema"], "outputs": {"type": "integer", "max": 64}},
        **poll(
            {"each_child": "output", "send": "O{child_id}"},
            {"each_child": "zone", "send": "Z{child_id}"},
            {"each_child": "named", "send": "N{child_id}"},
            {"each_child": "unsized", "send": "U{child_id}"},
            {"each_child": "nothing", "send": "X{child_id}"},
            poll_interval=0.01, outputs=4,
        ),
    },
    "poll_each_child_paged": {
        "child_entity_types": {"output": OUTPUTS},
        **poll(
            {"each_child": "output", "send": "O{child_id}", "page_size": 16},
            poll_interval=0.01, outputs=40,
        ),
    },
    "poll_each_child_when": {
        "child_entity_types": {"output": OUTPUTS},
        **poll(
            {"each_child": "output", "send": "O{child_id}", "when": "meters"},
            "Q", poll_interval=0.01, outputs=8, meters=False,
        ),
    },
    "poll_count_from_state": {
        "child_entity_types": {"output": {
            **OUTPUTS, "instances": {"count_from": "outputs", "count_from_state": "count"},
        }},
        **poll({"each_child": "output", "send": "O{child_id}"},
               poll_interval=0.01, outputs=6),
    },
    "poll_largest_roster": {
        "child_entity_types": {"output": OUTPUTS},
        "config_schema": {**BASE["config_schema"],
                          "outputs": {"type": "integer", "label": "Outputs", "max": 64}},
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=1, outputs=2),
    },
    "poll_largest_roster_id_format_max": {
        "child_entity_types": {"output": OUTPUTS},
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=1, outputs=2),
    },
    "poll_largest_two_rosters": {
        "child_entity_types": {
            "output": OUTPUTS,
            "input": {"id_format": {"type": "integer", "max": 32},
                      "instances": {"count_from": "inputs"}},
        },
        "config_schema": {**BASE["config_schema"],
                          "outputs": {"type": "integer", "label": "Outputs", "max": 16},
                          "inputs": {"type": "integer", "label": 5}},
        **poll({"each_child": "output", "send": "O{child_id}"},
               {"each_child": "input", "send": "I{child_id}"},
               poll_interval=1, outputs=2, inputs=2),
    },
    "poll_largest_roster_fits": {
        "child_entity_types": {"output": {**OUTPUTS, "id_format": {"type": "integer", "max": 8}}},
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=1, outputs=2),
    },
    "poll_largest_roster_already_there": {
        "child_entity_types": {"output": OUTPUTS},
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=1, outputs=64),
    },
    "poll_largest_one_roster_already_there": {
        "child_entity_types": {
            "output": OUTPUTS,
            "input": {"id_format": {"type": "integer", "max": 32},
                      "instances": {"count_from": "inputs"}},
        },
        **poll({"each_child": "output", "send": "O{child_id}"},
               {"each_child": "input", "send": "I{child_id}"},
               poll_interval=4, outputs=64, inputs=2),
    },
    "poll_largest_roster_above_max": {
        "child_entity_types": {"output": OUTPUTS},
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=1, outputs="99"),
    },
    "poll_largest_roster_current_not_a_number": {
        "child_entity_types": {"output": OUTPUTS},
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=1, outputs="x"),
    },
    "poll_largest_roster_tops_unusable": {
        "child_entity_types": {
            "a": {"id_format": {"type": "integer", "max": True}, "instances": {"count_from": "a"}},
            "b": {"id_format": {"type": "integer", "max": 2.5}, "instances": {"count_from": "b"}},
            "c": {"id_format": {"type": "integer", "max": 0}, "instances": {"count_from": "c"}},
            "d": {"id_format": {"type": "integer"}, "instances": {"count_from": "d"}},
            "e": {"id_format": ["max"], "instances": {"count_from": "e"}},
            "f": {"id_format": {"type": "integer", "max": 50}, "instances": {"count_from": ""}},
            "g": {"id_format": {"type": "integer", "max": 50}, "instances": {"count_from": 5}},
            "h": {"id_format": {"type": "integer", "max": 50}, "instances": ["count_from"]},
            "i": "type",
            "j": {"id_format": {"type": "integer", "max": 50}},
        },
        "config_schema": {**BASE["config_schema"], "a": {"type": "integer", "max": 50},
                          "b": "integer"},
        **poll(*[{"each_child": t, "send": "{child_id}"} for t in "abcdefghij"],
               poll_interval=1),
    },
    # A field max that is a boolean is no maximum.
    "poll_largest_roster_top_bool": {
        "child_entity_types": {
            "output": OUTPUTS,
            "k": {"id_format": {"type": "integer"}, "instances": {"count_from": "k"}},
        },
        "config_schema": {**BASE["config_schema"], "k": {"type": "integer", "max": True}},
        **poll({"each_child": "output", "send": "O{child_id}"},
               {"each_child": "k", "send": "K{child_id}"},
               poll_interval=1, outputs=2, k=0),
    },
    # The spacing is rounded to the nearest millisecond.
    "poll_delay_fractional_ms": {
        "transport": "udp", **poll(*["Q"] * 100, poll_interval=1, inter_command_delay=0.0299),
    },
    "poll_largest_roster_schema_not_mapping": {
        "child_entity_types": {"output": OUTPUTS},
        "config_schema": ["outputs"],
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=1, outputs=2),
    },
    "poll_child_types_not_mapping": {
        "child_entity_types": ["output"],
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=0.01),
    },
    "poll_long_at_default_says_only_that": {
        "child_entity_types": {"output": OUTPUTS},
        **poll({"each_child": "output", "send": "O{child_id}"}, poll_interval=0.1, outputs=8),
    },
    # --- warnings: nobody notices a device that stops answering -------------
    "silence_no_poll": {"responses": [RULE]},
    "silence_tcp_polls": {"responses": [RULE], **poll("Q", poll_interval=5)},
    "silence_serial_polls": {
        "transport": "serial", "responses": [RULE], **poll("Q", poll_interval=5),
    },
    "silence_liveness": {
        "responses": [RULE], "liveness": {"send": "PING\r", "interval": 10, "timeout": 5},
    },
    "silence_liveness_empty": {"responses": [RULE], "liveness": {}},
    "silence_no_responses": {"responses": []},
    "silence_responses_not_list": {"responses": {"a": RULE}},
    "silence_extras": {
        "responses": [RULE], "transports": ["serial", "tcp", "http", "serial", "osc"],
        **poll("Q", poll_interval=5),
    },
    "silence_extras_no_poll": {
        "responses": [RULE], "transports": ["serial", "udp", "tcp"],
    },
    "silence_extras_not_list": {"responses": [RULE], "transports": "serial"},
    "silence_config_transport": {
        "responses": [RULE], **poll("Q", poll_interval=5, transport="serial"),
    },
    "silence_interval_zero": {"responses": [RULE], **poll("Q", poll_interval=0)},
    "silence_interval_nan": {"responses": [RULE], **poll("Q", poll_interval="nan")},
    "silence_queries_empty": {"responses": [RULE], **poll(poll_interval=5)},
    "silence_queries_mapping": {
        "responses": [RULE], "polling": {"queries": {"a": "Q"}},
        "default_config": {"zone": 1, "poll_interval": 5},
    },
    "silence_http": {"transport": "http", "responses": [RULE]},
    # A number too large for a float is read only where each rule reads it.
    "poll_interval_too_large_unread": {"transport": "osc", **poll("Q", poll_interval=10**400)},
    "silence_defaults_not_mapping": {
        "responses": [RULE], "polling": {"queries": ["Q"]}, "default_config": "x",
    },
    # --- every warning, in the order they report ----------------------------
    "warnings_in_order": {
        "transport": "osc",
        "discovery": {"port_open": [10023]},
        "config_schema": {"password": {"type": "string", "default": "x"}},
        **commands(a={"address": "/a/{x}", "params": {"x": {"type": "integer"}}}),
        "responses": [{"address": "/level", "set": {"level": "$1"}}],
    },
    "warnings_and_errors": {
        **command(send="L{levl}"),
        "config_schema": {"password": {"type": "string", "default": "x"}},
        "responses": [RULE],
        "bogus_key": 1,
    },
}

# Cases also run with strict=True (the unknown-key and floor checks after
# every other error).
STRICT = {"warnings_and_errors", "every_list_in_order", "cmd_every_place_in_order"}

RAISING_CASES: dict[str, dict[str, Any]] = {
    "silence_transport_unhashable": {"transport": ["tcp"], "responses": [RULE]},
    "silence_extra_unhashable": {"responses": [RULE], "transports": [["tcp"]]},
    "poll_each_child_unhashable": poll(
        {"each_child": ["output"], "send": "O"}, poll_interval=1,
    ),
    "poll_udp_delay_nan": {
        "transport": "udp", **poll("Q", poll_interval=1, inter_command_delay="nan"),
    },
    "poll_udp_delay_infinite": {
        "transport": "udp", **poll("Q", poll_interval=1, inter_command_delay="inf"),
    },
    "poll_tcp_delay_infinite": poll("Q", poll_interval=1, inter_command_delay="inf"),
    "poll_interval_too_large": poll("Q", poll_interval=10**400),
    "poll_interval_too_large_at_no_gap": {"transport": "udp", **poll("Q", poll_interval=10**400)},
    "poll_delay_too_large": {"transport": "osc", **poll("Q", inter_command_delay=10**400)},
    "silence_interval_too_large": {
        "transport": "osc", "responses": [RULE], **poll("Q", poll_interval=10**400),
    },
    "silence_interval_too_large_no_queries": {
        "transport": "osc", "responses": [RULE],
        "default_config": {"zone": 1, "poll_interval": 10**400},
    },
}

# Raised by validate_driver_warnings (and so by validate_driver_issues);
# validate_driver_definition alone accepts or refuses each shape without
# raising.
RAISES: dict[str, type[BaseException]] = {
    'poll_delay_too_large': OverflowError,
    'poll_each_child_unhashable': TypeError,
    'poll_interval_too_large': OverflowError,
    'poll_interval_too_large_at_no_gap': OverflowError,
    'poll_tcp_delay_infinite': OverflowError,
    'poll_udp_delay_infinite': OverflowError,
    'poll_udp_delay_nan': ValueError,
    'silence_extra_unhashable': TypeError,
    'silence_interval_too_large': OverflowError,
    'silence_interval_too_large_no_queries': OverflowError,
    'silence_transport_unhashable': TypeError,
}

# One template per entry, run through every reader of the token.
TOKENS: list[str] = [
    "{a}", "{a:02d}", "{a:}", "{a:x:y}", "{1}", "{_a}", "{a1}", "{A}", "{\u00e9}",
    "{a b}", "{ a}", "{a-b}", "{{a}}", "{a{b}}", "{a:{b}}", '{"x": {a}}',
    "{a:0{2}d}", "{a}{b}", "{b}x{b}", "{q:}", "{a", "a}", "{}", "{:02d}", "{a:\n}", "x{a}y{c:>4}z",
]
TOKEN_PARAMS = {"a": {"type": "integer"}, "b": {"type": "string"}, "c": {"type": "string"}}


def _definition(case: str) -> dict[str, Any]:
    merged = {**BASE, **CASES[case]}
    return {key: value for key, value in merged.items() if value is not DROP}


def _issues(definition: dict[str, Any], strict: bool) -> list[tuple[str, str, str]]:
    return [
        (i["severity"], i["path"], i["message"])
        for i in validate_driver_issues(definition, strict=strict)
    ]


def _token_readings(template: str) -> dict[str, Any]:
    """What each reader of the token makes of one template."""
    definition = {**BASE, **commands(t={"send": template, "params": TOKEN_PARAMS})}
    driver = SimpleNamespace(
        _safe_substitute=compiled_protocol.safe_substitute,
        _warned_unresolved=set(),
        device_id="acme",
    )
    log = logging.getLogger("openavc.drivers.configurable")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    log.addHandler(handler)
    try:
        wire = ConfigurableDriver._substitute_for_wire(
            driver, "commands.t", template, {"a": 1}
        )
    finally:
        log.removeHandler(handler)
    result = sim_validate.ValidationResult("x.avcdriver", "acme_widget", "yaml")
    sim_validate._check_osc_read_back_coverage(
        result, {"t": {"address": "/t", "read_back": template}}, [], [], {"id": "acme"},
    )
    return {
        "validator": validate_substitutions(definition),
        "runtime_wire": wire,
        "runtime_warned": [r.getMessage() for r in records],
        "declared_placeholders": declared_placeholders(template, TOKEN_PARAMS),
        "is_passthrough_send": is_passthrough_send(template, TOKEN_PARAMS),
        "send_regex": send_regex(template, TOKEN_PARAMS),
        "osc_read_back": [i.message for i in result.issues],
    }


# --- recorded from the code as it is ----------------------------------------

EXPECTED: dict[str, list[tuple[str, str, str]]] = {'cmd_clean': [],
 'cmd_config_typo': [('error',
                      'commands.set.send',
                      "commands.set.send: '{zon}' resolves to nothing (did you mean 'zone'?). It "
                      'names no parameter of this command and no config field, so it would be sent '
                      'to the device as written')],
 'cmd_each_field': [('error',
                     'commands.set',
                     "Command 'set': declares send and path/method and address, but a command "
                     "sends one way. Keep one of 'send' (TCP/serial), 'path'/'method' (HTTP), "
                     "'address' (OSC), or 'udp' (a datagram beside the main transport)"),
                    ('error',
                     'commands.set.send',
                     "commands.set.send: '{s1}' resolves to nothing. It names no parameter of this "
                     'command and no config field, so it would be sent to the device as written'),
                    ('error',
                     'commands.set.path',
                     "commands.set.path: '{p1}' resolves to nothing. It names no parameter of this "
                     'command and no config field, so it would be sent to the device as written'),
                    ('error',
                     'commands.set.body',
                     "commands.set.body: '{b1}' resolves to nothing. It names no parameter of this "
                     'command and no config field, so it would be sent to the device as written'),
                    ('error',
                     'commands.set.address',
                     "commands.set.address: '{a1}' resolves to nothing. It names no parameter of "
                     'this command and no config field, so it would be sent to the device as '
                     'written')],
 'cmd_every_place_in_order': [('error',
                               'commands.set',
                               "Command 'set': declares send and path/method and address and udp, "
                               "but a command sends one way. Keep one of 'send' (TCP/serial), "
                               "'path'/'method' (HTTP), 'address' (OSC), or 'udp' (a datagram "
                               'beside the main transport)'),
                              ('error',
                               'commands.set',
                               "Command 'set': 'udp.port' is required with a payload (a number, or "
                               'a {config_field} placeholder)'),
                              ('error',
                               'commands.set',
                               "Command 'set': 'read_back' reads a value back over the device's "
                               "connection, and a 'udp' command is sent beside it, so nothing "
                               'would answer'),
                              ('error',
                               'commands.set.send',
                               "commands.set.send: '{s}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.path',
                               "commands.set.path: '{p}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.body',
                               "commands.set.body: '{b}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.address',
                               "commands.set.address: '{a}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.headers.H',
                               "commands.set.headers.H: '{h}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.read_back[0]',
                               "commands.set.read_back[0]: '{r}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.udp.payload',
                               "commands.set.udp.payload: '{u}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.udp.host',
                               "commands.set.udp.host: '{uh}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written')],
 'cmd_fields_not_text': [('error',
                          'commands.set',
                          "Command 'set': declares send and path/method and address, but a command "
                          "sends one way. Keep one of 'send' (TCP/serial), 'path'/'method' (HTTP), "
                          "'address' (OSC), or 'udp' (a datagram beside the main transport)")],
 'cmd_headers': [('error',
                  'commands.set.headers.X-A',
                  "commands.set.headers.X-A: '{tokn}' resolves to nothing. It names no parameter "
                  'of this command and no config field, so it would be sent to the device as '
                  'written'),
                 ('error',
                  'commands.set.headers.7',
                  "commands.set.headers.7: '{num}' resolves to nothing. It names no parameter of "
                  'this command and no config field, so it would be sent to the device as written'),
                 ('error',
                  'commands.set.headers.X',
                  "commands.set.headers.X:C: '{colon}' resolves to nothing. It names no parameter "
                  'of this command and no config field, so it would be sent to the device as '
                  'written')],
 'cmd_headers_not_mapping': [],
 'cmd_json_body': [('error',
                    'commands.set',
                    "Command 'set': must have 'send' (TCP/serial), 'path'/'method' (HTTP), "
                    "'address' (OSC), or 'udp' (a datagram beside the main transport)")],
 'cmd_name_with_colon': [('error',
                          'commands.a',
                          "commands.a:b.send: '{q}' resolves to nothing. It names no parameter of "
                          'this command and no config field, so it would be sent to the device as '
                          'written')],
 'cmd_no_hint': [('error',
                  'commands.set.send',
                  "commands.set.send: '{qqq}' resolves to nothing. It names no parameter of this "
                  'command and no config field, so it would be sent to the device as written')],
 'cmd_no_params': [],
 'cmd_not_mapping': [('error', 'commands.set', "Command 'set': must be a dict")],
 'cmd_params_empty': [],
 'cmd_params_key_not_text': [('error',
                              'commands.set.send',
                              "commands.set.send: '{True}' resolves to nothing. It names no "
                              'parameter of this command and no config field, so it would be sent '
                              'to the device as written'),
                             ('warning',
                              'commands.set.params.True',
                              "Command 'set' parameter 'True' has no label, so forms show its key "
                              'made readable. Give it a label that says what to enter.')],
 'cmd_params_list': [],
 'cmd_params_unevaluated': [('warning',
                             'commands.set.params.level',
                             "Command 'set' parameter 'level' has no label, so forms show its key "
                             'made readable. Give it a label that says what to enter.')],
 'cmd_read_back_list': [('error',
                         'commands.set',
                         "Command 'set': 'read_back' must be true, a query, or a list of queries"),
                        ('error',
                         'commands.set.read_back[0]',
                         "commands.set.read_back[0]: '{lvl}' resolves to nothing (did you mean "
                         "'level'?). It names no parameter of this command and no config field, so "
                         'it would be sent to the device as written'),
                        ('error',
                         'commands.set.read_back[5]',
                         "commands.set.read_back[5]: '{x}' resolves to nothing. It names no "
                         'parameter of this command and no config field, so it would be sent to '
                         'the device as written')],
 'cmd_read_back_mapping': [('error',
                            'commands.set',
                            "Command 'set': 'read_back' must be true, a query, or a list of "
                            'queries')],
 'cmd_read_back_names_a_templated_command': [('error',
                                              'commands.set.read_back[1]',
                                              "commands.set.read_back[1]: '{lvl}' resolves to "
                                              "nothing (did you mean 'level'?). It names no "
                                              'parameter of this command and no config field, so '
                                              'it would be sent to the device as written')],
 'cmd_read_back_text': [('error',
                         'commands.set.read_back',
                         "commands.set.read_back: '{lvl}' resolves to nothing (did you mean "
                         "'level'?). It names no parameter of this command and no config field, so "
                         'it would be sent to the device as written')],
 'cmd_read_back_true': [('error',
                         'commands.set',
                         "Command 'set': 'read_back: true' reads back from an OSC command's own "
                         "address or an HTTP command's own path. Name the query that reads this "
                         "command's value instead")],
 'cmd_repeats_in_order': [('error',
                           'commands.set.send',
                           "commands.set.send: '{b}' resolves to nothing. It names no parameter of "
                           'this command and no config field, so it would be sent to the device as '
                           'written'),
                          ('error',
                           'commands.set.send',
                           "commands.set.send: '{a}' resolves to nothing. It names no parameter of "
                           'this command and no config field, so it would be sent to the device as '
                           'written'),
                          ('error',
                           'commands.set.send',
                           "commands.set.send: '{a}' resolves to nothing. It names no parameter of "
                           'this command and no config field, so it would be sent to the device as '
                           'written')],
 'cmd_spec_typo': [('error',
                    'commands.set.send',
                    "commands.set.send: '{levl}' resolves to nothing (did you mean 'level'?). It "
                    'names no parameter of this command and no config field, so it would be sent '
                    'to the device as written')],
 'cmd_typo': [('error',
               'commands.set.send',
               "commands.set.send: '{levl}' resolves to nothing (did you mean 'level'?). It names "
               'no parameter of this command and no config field, so it would be sent to the '
               'device as written')],
 'cmd_udp': [('error',
              'commands.set',
              "Command 'set': declares send and udp, but a command sends one way. Keep one of "
              "'send' (TCP/serial), 'path'/'method' (HTTP), 'address' (OSC), or 'udp' (a datagram "
              'beside the main transport)'),
             ('error',
              'commands.set',
              "Command 'set': 'udp.port' names config field 'prt', which the driver does not "
              'declare'),
             ('error',
              'commands.set.udp.payload',
              "commands.set.udp.payload: '{lvl}' resolves to nothing (did you mean 'level'?). It "
              'names no parameter of this command and no config field, so it would be sent to the '
              'device as written'),
             ('error',
              'commands.set.udp.host',
              "commands.set.udp.host: '{hst}' resolves to nothing. It names no parameter of this "
              'command and no config field, so it would be sent to the device as written'),
             ('error',
              'commands.set.udp.port',
              "commands.set.udp.port: '{prt}' resolves to nothing. It names no parameter of this "
              'command and no config field, so it would be sent to the device as written')],
 'cmd_udp_not_mapping': [('error',
                          'commands.set',
                          "Command 'set': declares send and udp, but a command sends one way. Keep "
                          "one of 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' (OSC), or "
                          "'udp' (a datagram beside the main transport)"),
                         ('error',
                          'commands.set',
                          "Command 'set': 'udp' must be a mapping (host, port, payload or "
                          'magic_packet)')],
 'cmd_udp_port_number': [('error',
                          'commands.set',
                          "Command 'set': declares send and udp, but a command sends one way. Keep "
                          "one of 'send' (TCP/serial), 'path'/'method' (HTTP), 'address' (OSC), or "
                          "'udp' (a datagram beside the main transport)")],
 'commands_in_order': [('error',
                        'commands.b.send',
                        "commands.b.send: '{bb}' resolves to nothing. It names no parameter of "
                        'this command and no config field, so it would be sent to the device as '
                        'written'),
                       ('error',
                        'commands.a.send',
                        "commands.a.send: '{aa}' resolves to nothing. It names no parameter of "
                        'this command and no config field, so it would be sent to the device as '
                        'written')],
 'commands_not_mapping': [('error', 'commands', 'commands: must be a mapping')],
 'commands_unevaluated': [('error',
                           'commands.<unevaluated>',
                           "Command '<unevaluated>': must be a dict")],
 'every_list_in_order': [('error',
                          'commands.set.send',
                          "commands.set.send: '{c}' resolves to nothing. It names no parameter of "
                          'this command and no config field, so it would be sent to the device as '
                          'written'),
                         ('error',
                          'responses[0].match',
                          "responses[0].match: '{m}' resolves to nothing. It names no config field "
                          '(only config values substitute here), so the pattern would look for it '
                          'as written'),
                         ('error',
                          'polling.queries[0]',
                          "polling.queries[0]: '{p}' resolves to nothing. It names no config field "
                          '(only config values substitute here), so it would be sent to the device '
                          'as written'),
                         ('error',
                          'on_connect[0]',
                          "on_connect[0]: '{o}' resolves to nothing. It names no config field "
                          '(only config values substitute here), so it would be sent to the device '
                          'as written'),
                         ('error',
                          'responses[0].then_query[0]',
                          "responses[0].then_query[0]: '{t}' resolves to nothing. It names no "
                          'config field (only config values substitute here), so it would be sent '
                          'to the device as written')],
 'masked_default_in_defaults': [('warning',
                                 'config_schema.password',
                                 "Config field 'password' is masked but has a default value, which "
                                 'is stored in plain text inside the driver file. That is fine for '
                                 'a published factory default, but never put a real site password '
                                 'here.')],
 'masked_default_in_schema': [('warning',
                               'config_schema.secret_key',
                               "Config field 'secret_key' is masked but has a default value, which "
                               'is stored in plain text inside the driver file. That is fine for a '
                               'published factory default, but never put a real site password '
                               'here.')],
 'masked_defaults_not_mapping': [],
 'masked_empty_defaults': [('warning',
                            'config_schema.code',
                            "Config field 'code' is masked but has a default value, which is "
                            'stored in plain text inside the driver file. That is fine for a '
                            'published factory default, but never put a real site password here.'),
                           ('warning',
                            'config_schema.flag',
                            "Config field 'flag' is masked but has a default value, which is "
                            'stored in plain text inside the driver file. That is fine for a '
                            'published factory default, but never put a real site password here.')],
 'masked_entry_not_mapping': [('warning',
                               'config_schema.key',
                               "Config field 'key' is masked but has a default value, which is "
                               'stored in plain text inside the driver file. That is fine for a '
                               'published factory default, but never put a real site password '
                               'here.')],
 'masked_schema_not_mapping': [],
 'masked_secret_not_true': [],
 'masked_type_password': [('warning',
                           'config_schema.pin',
                           "Config field 'pin' is masked but has a default value, which is stored "
                           'in plain text inside the driver file. That is fine for a published '
                           'factory default, but never put a real site password here.')],
 'on_connect': [('error',
                 'on_connect[0]',
                 "on_connect[0]: '{q}' resolves to nothing. It names no config field (only config "
                 'values substitute here), so it would be sent to the device as written'),
                ('error',
                 'on_connect[1]',
                 "on_connect[1]: '{r}' resolves to nothing. It names no config field (only config "
                 'values substitute here), so it would be sent to the device as written')],
 'on_connect_not_list': [],
 'param_labels': [('error', 'commands.c', "Command 'c': must be a dict"),
                  ('warning',
                   'commands.a.params.x',
                   "Command 'a' parameter 'x' has no label, so forms show its key made readable. "
                   'Give it a label that says what to enter.'),
                  ('warning',
                   'commands.a.params.y',
                   "Command 'a' parameter 'y' has no label, so forms show its key made readable. "
                   'Give it a label that says what to enter.'),
                  ('warning',
                   'commands.a.params.z',
                   "Command 'a' parameter 'z' has no label, so forms show its key made readable. "
                   'Give it a label that says what to enter.')],
 'poll_child_types_not_mapping': [('error',
                                   'child_entity_types',
                                   'child_entity_types: must be a mapping'),
                                  ('error',
                                   'polling.queries[0]',
                                   "polling.queries[0]: each_child type 'output' is not a declared "
                                   'child_entity_type')],
 'poll_config_transport': [],
 'poll_config_transport_serial': [('warning',
                                   'polling.queries',
                                   'Each poll sends 30 lines, 1.5 s at 50 ms apart (the spacing '
                                   'used when no inter_command_delay is set), longer than the 1 s '
                                   'poll interval: the device answers polls more than half the '
                                   'time, and each reading refreshes every 2.5 s. Poll less per '
                                   'cycle, or set a longer poll_interval in default_config.')],
 'poll_count_from_state': [('error',
                            'child_entity_types.output',
                            "child_entity_types.output.instances: count_from_state 'count' is not "
                            'a declared state variable'),
                           ('warning',
                            'polling.queries',
                            'Each poll sends 6 lines, 0.3 s at 50 ms apart (the spacing used when '
                            'no inter_command_delay is set), longer than the 0 s poll interval: '
                            'the device answers polls more than half the time, and each reading '
                            'refreshes every 0.3 s. Poll less per cycle, or set a longer '
                            'poll_interval in default_config.')],
 'poll_counts_each_form': [('error',
                            'polling.queries[4]',
                            'polling.queries[4]: mapping entries must be {each_child, send} or '
                            '{send, when} or {send, when_state} or {send, query_for}'),
                           ('error',
                            'polling.queries[5]',
                            'polling.queries[5]: mapping entries must be {each_child, send} or '
                            '{send, when} or {send, when_state} or {send, query_for}'),
                           ('error',
                            'polling.queries[8]',
                            "polling.queries[8]: 'when' field 'missing' is not a declared config "
                            'field (config_schema / default_config / config_derived)'),
                           ('error',
                            'polling.queries[9]',
                            "polling.queries[9]: 'when' must name a config field"),
                           ('error',
                            'polling.queries[10]',
                            "polling.queries[10]: 'when' must name a config field"),
                           ('warning',
                            'polling.queries',
                            'Each poll sends 6 lines, 0.3 s at 50 ms apart (the spacing used when '
                            'no inter_command_delay is set), longer than the 0 s poll interval: '
                            'the device answers polls more than half the time, and each reading '
                            'refreshes every 0.3 s. Poll less per cycle, or set a longer '
                            'poll_interval in default_config.')],
 'poll_defaults_not_mapping': [],
 'poll_delay_bool': [('warning',
                      'polling.queries',
                      'Each poll sends 30 lines, 1.5 s at 50 ms apart (the spacing used when no '
                      'inter_command_delay is set), longer than the 1 s poll interval: the device '
                      'answers polls more than half the time, and each reading refreshes every 2.5 '
                      's. Poll less per cycle, or set a longer poll_interval in default_config.')],
 'poll_delay_fractional_ms': [('warning',
                               'polling.queries',
                               'Each poll sends 100 lines, 3 s at 30 ms apart, longer than the 1 s '
                               'poll interval: the device answers polls more than half the time, '
                               'and each reading refreshes every 4 s. Poll less per cycle, or set '
                               'a longer poll_interval in default_config.')],
 'poll_delay_negative': [('warning',
                          'polling.queries',
                          'Each poll sends 30 lines, 1.5 s at 50 ms apart (the spacing used when '
                          'no inter_command_delay is set), longer than the 1 s poll interval: the '
                          'device answers polls more than half the time, and each reading '
                          'refreshes every 2.5 s. Poll less per cycle, or set a longer '
                          'poll_interval in default_config.')],
 'poll_delay_not_a_number': [('warning',
                              'polling.queries',
                              'Each poll sends 30 lines, 1.5 s at 50 ms apart (the spacing used '
                              'when no inter_command_delay is set), longer than the 1 s poll '
                              'interval: the device answers polls more than half the time, and '
                              'each reading refreshes every 2.5 s. Poll less per cycle, or set a '
                              'longer poll_interval in default_config.')],
 'poll_delay_text': [('warning',
                      'polling.queries',
                      'Each poll sends 10 lines, 2 s at 200 ms apart, longer than the 1 s poll '
                      'interval: the device answers polls more than half the time, and each '
                      'reading refreshes every 3 s. Poll less per cycle, or set a longer '
                      'poll_interval in default_config.')],
 'poll_each_child': [('error',
                      'polling.queries[3]',
                      "polling.queries[3]: each_child type 'unsized' declares no instances: block, "
                      'so nothing would ever be polled'),
                     ('error',
                      'polling.queries[4]',
                      "polling.queries[4]: each_child type 'nothing' is not a declared "
                      'child_entity_type'),
                     ('warning',
                      'polling.queries',
                      'Each poll sends 9 lines, 0.5 s at 50 ms apart (the spacing used when no '
                      'inter_command_delay is set), longer than the 0 s poll interval: the device '
                      'answers polls more than half the time, and each reading refreshes every 0.5 '
                      's. Poll less per cycle, or set a longer poll_interval in default_config.')],
 'poll_each_child_paged': [('warning',
                            'polling.queries',
                            'Each poll sends 3 lines, 0.2 s at 50 ms apart (the spacing used when '
                            'no inter_command_delay is set), longer than the 0 s poll interval: '
                            'the device answers polls more than half the time, and each reading '
                            'refreshes every 0.2 s. Poll less per cycle, or set a longer '
                            'poll_interval in default_config.')],
 'poll_each_child_when': [('warning',
                           'polling.queries',
                           'Each poll sends 1 lines, 0.1 s at 50 ms apart (the spacing used when '
                           'no inter_command_delay is set), longer than the 0 s poll interval: the '
                           'device answers polls more than half the time, and each reading '
                           'refreshes every 0.1 s. Poll less per cycle, or set a longer '
                           'poll_interval in default_config.')],
 'poll_entries': [('error',
                   'polling.queries[1]',
                   'polling.queries[1]: mapping entries must be {each_child, send} or {send, when} '
                   'or {send, when_state} or {send, query_for}'),
                  ('error',
                   'polling.queries[2]',
                   'polling.queries[2]: mapping entries must be {each_child, send} or {send, when} '
                   'or {send, when_state} or {send, query_for}'),
                  ('error',
                   'polling.queries[3]',
                   'polling.queries[3]: mapping entries must be {each_child, send} or {send, when} '
                   'or {send, when_state} or {send, query_for}'),
                  ('error',
                   'polling.queries[4]',
                   'polling.queries[4]: mapping entries must be {each_child, send} or {send, when} '
                   'or {send, when_state} or {send, query_for}'),
                  ('error',
                   'polling.queries[0]',
                   "polling.queries[0]: '{a}' resolves to nothing. It names no config field (only "
                   'config values substitute here), so it would be sent to the device as written'),
                  ('error',
                   'polling.queries[1]',
                   "polling.queries[1]: '{b}' resolves to nothing. It names no config field (only "
                   'config values substitute here), so it would be sent to the device as written'),
                  ('error',
                   'polling.queries[2]',
                   "polling.queries[2]: '{c}' resolves to nothing. It names no config field (only "
                   'config values substitute here), so it would be sent to the device as written')],
 'poll_exactly_fits': [],
 'poll_fits': [],
 'poll_http_delay': [('warning',
                      'polling.queries',
                      'Each poll sends 3 lines, 1.5 s at 500 ms apart, longer than the 1.2 s poll '
                      'interval: the device answers polls more than half the time, and each '
                      'reading refreshes every 2.8 s. Poll less per cycle, or set a longer '
                      'poll_interval in default_config.')],
 'poll_interval_bool': [],
 'poll_interval_missing': [],
 'poll_interval_nan': [('warning',
                        'polling.queries',
                        'Each poll sends 1 lines, 0.1 s at 50 ms apart (the spacing used when no '
                        'inter_command_delay is set), longer than the nan s poll interval: the '
                        'device answers polls more than half the time, and each reading refreshes '
                        'every nan s. Poll less per cycle, or set a longer poll_interval in '
                        'default_config.')],
 'poll_interval_negative': [],
 'poll_interval_text': [('warning',
                         'polling.queries',
                         'Each poll sends 30 lines, 1.5 s at 50 ms apart (the spacing used when no '
                         'inter_command_delay is set), longer than the 1 s poll interval: the '
                         'device answers polls more than half the time, and each reading refreshes '
                         'every 2.5 s. Poll less per cycle, or set a longer poll_interval in '
                         'default_config.')],
 'poll_interval_too_large_unread': [],
 'poll_interval_zero': [],
 'poll_largest_one_roster_already_there': [('warning',
                                            'polling.queries',
                                            'At the largest roster this driver accepts (inputs '
                                            '32), each poll sends 96 lines, 4.8 s at 50 ms apart '
                                            '(the spacing used when no inter_command_delay is '
                                            'set), longer than the 4 s poll interval: the device '
                                            'answers polls more than half the time, and each '
                                            'reading refreshes every 8.8 s. Poll less per child, '
                                            "set a longer poll_interval, or lower the roster's max "
                                            'to the largest unit the driver supports.')],
 'poll_largest_roster': [('warning',
                          'polling.queries',
                          'At the largest roster this driver accepts (Outputs 64), each poll sends '
                          '64 lines, 3.2 s at 50 ms apart (the spacing used when no '
                          'inter_command_delay is set), longer than the 1 s poll interval: the '
                          'device answers polls more than half the time, and each reading '
                          'refreshes every 4.2 s. Poll less per child, set a longer poll_interval, '
                          "or lower the roster's max to the largest unit the driver supports.")],
 'poll_largest_roster_above_max': [('warning',
                                    'polling.queries',
                                    'Each poll sends 99 lines, 5 s at 50 ms apart (the spacing '
                                    'used when no inter_command_delay is set), longer than the 1 s '
                                    'poll interval: the device answers polls more than half the '
                                    'time, and each reading refreshes every 6 s. Poll less per '
                                    'cycle, or set a longer poll_interval in default_config.')],
 'poll_largest_roster_already_there': [('warning',
                                        'polling.queries',
                                        'Each poll sends 64 lines, 3.2 s at 50 ms apart (the '
                                        'spacing used when no inter_command_delay is set), longer '
                                        'than the 1 s poll interval: the device answers polls more '
                                        'than half the time, and each reading refreshes every 4.2 '
                                        's. Poll less per cycle, or set a longer poll_interval in '
                                        'default_config.')],
 'poll_largest_roster_current_not_a_number': [('warning',
                                               'polling.queries',
                                               'At the largest roster this driver accepts (outputs '
                                               '64), each poll sends 64 lines, 3.2 s at 50 ms '
                                               'apart (the spacing used when no '
                                               'inter_command_delay is set), longer than the 1 s '
                                               'poll interval: the device answers polls more than '
                                               'half the time, and each reading refreshes every '
                                               '4.2 s. Poll less per child, set a longer '
                                               "poll_interval, or lower the roster's max to the "
                                               'largest unit the driver supports.')],
 'poll_largest_roster_fits': [],
 'poll_largest_roster_id_format_max': [('warning',
                                        'polling.queries',
                                        'At the largest roster this driver accepts (outputs 64), '
                                        'each poll sends 64 lines, 3.2 s at 50 ms apart (the '
                                        'spacing used when no inter_command_delay is set), longer '
                                        'than the 1 s poll interval: the device answers polls more '
                                        'than half the time, and each reading refreshes every 4.2 '
                                        's. Poll less per child, set a longer poll_interval, or '
                                        "lower the roster's max to the largest unit the driver "
                                        'supports.')],
 'poll_largest_roster_schema_not_mapping': [('warning',
                                             'polling.queries',
                                             'At the largest roster this driver accepts (outputs '
                                             '64), each poll sends 64 lines, 3.2 s at 50 ms apart '
                                             '(the spacing used when no inter_command_delay is '
                                             'set), longer than the 1 s poll interval: the device '
                                             'answers polls more than half the time, and each '
                                             'reading refreshes every 4.2 s. Poll less per child, '
                                             "set a longer poll_interval, or lower the roster's "
                                             'max to the largest unit the driver supports.')],
 'poll_largest_roster_top_bool': [('warning',
                                   'polling.queries',
                                   'At the largest roster this driver accepts (outputs 64), each '
                                   'poll sends 64 lines, 3.2 s at 50 ms apart (the spacing used '
                                   'when no inter_command_delay is set), longer than the 1 s poll '
                                   'interval: the device answers polls more than half the time, '
                                   'and each reading refreshes every 4.2 s. Poll less per child, '
                                   "set a longer poll_interval, or lower the roster's max to the "
                                   'largest unit the driver supports.')],
 'poll_largest_roster_tops_unusable': [('error',
                                        'child_entity_types.a',
                                        'child_entity_types.a.id_format: max must be an integer '
                                        '(got True)'),
                                       ('error',
                                        'child_entity_types.b',
                                        'child_entity_types.b.id_format: max must be an integer '
                                        '(got 2.5)'),
                                       ('error',
                                        'child_entity_types.c',
                                        "child_entity_types.c.instances: count_from 'c' is not a "
                                        'declared config field (config_schema / default_config / '
                                        'config_derived)'),
                                       ('error',
                                        'child_entity_types.d',
                                        "child_entity_types.d.instances: count_from 'd' is not a "
                                        'declared config field (config_schema / default_config / '
                                        'config_derived)'),
                                       ('error',
                                        'child_entity_types.e',
                                        'child_entity_types.e.id_format: must be a mapping'),
                                       ('error',
                                        'child_entity_types.e',
                                        "child_entity_types.e.instances: count_from 'e' is not a "
                                        'declared config field (config_schema / default_config / '
                                        'config_derived)'),
                                       ('error',
                                        'child_entity_types.f',
                                        'child_entity_types.f.instances: count_from must name a '
                                        'config field'),
                                       ('error',
                                        'child_entity_types.g',
                                        'child_entity_types.g.instances: count_from must name a '
                                        'config field'),
                                       ('error',
                                        'child_entity_types.h',
                                        'child_entity_types.h.instances: must be a mapping'),
                                       ('error',
                                        'child_entity_types.i',
                                        'child_entity_types.i: must be a mapping'),
                                       ('error',
                                        'polling.queries[7]',
                                        "polling.queries[7]: each_child type 'h' declares no "
                                        'instances: block, so nothing would ever be polled'),
                                       ('error',
                                        'polling.queries[8]',
                                        "polling.queries[8]: each_child type 'i' declares no "
                                        'instances: block, so nothing would ever be polled'),
                                       ('error',
                                        'polling.queries[9]',
                                        "polling.queries[9]: each_child type 'j' declares no "
                                        'instances: block, so nothing would ever be polled'),
                                       ('warning',
                                        'polling.queries',
                                        'At the largest roster this driver accepts (a 50), each '
                                        'poll sends 50 lines, 2.5 s at 50 ms apart (the spacing '
                                        'used when no inter_command_delay is set), longer than the '
                                        '1 s poll interval: the device answers polls more than '
                                        'half the time, and each reading refreshes every 3.5 s. '
                                        'Poll less per child, set a longer poll_interval, or lower '
                                        "the roster's max to the largest unit the driver "
                                        'supports.')],
 'poll_largest_two_rosters': [('warning',
                               'polling.queries',
                               'At the largest roster this driver accepts (Outputs 16, inputs 32), '
                               'each poll sends 48 lines, 2.4 s at 50 ms apart (the spacing used '
                               'when no inter_command_delay is set), longer than the 1 s poll '
                               'interval: the device answers polls more than half the time, and '
                               'each reading refreshes every 3.4 s. Poll less per child, set a '
                               "longer poll_interval, or lower the roster's max to the largest "
                               'unit the driver supports.')],
 'poll_long_at_default_says_only_that': [('warning',
                                          'polling.queries',
                                          'Each poll sends 8 lines, 0.4 s at 50 ms apart (the '
                                          'spacing used when no inter_command_delay is set), '
                                          'longer than the 0.1 s poll interval: the device answers '
                                          'polls more than half the time, and each reading '
                                          'refreshes every 0.5 s. Poll less per cycle, or set a '
                                          'longer poll_interval in default_config.')],
 'poll_long_default_gap': [('warning',
                            'polling.queries',
                            'Each poll sends 30 lines, 1.5 s at 50 ms apart (the spacing used when '
                            'no inter_command_delay is set), longer than the 1 s poll interval: '
                            'the device answers polls more than half the time, and each reading '
                            'refreshes every 2.5 s. Poll less per cycle, or set a longer '
                            'poll_interval in default_config.')],
 'poll_osc': [],
 'poll_param_name': [('error',
                      'polling.queries[0]',
                      "polling.queries[0]: '{level}' resolves to nothing. It names no config field "
                      '(only config values substitute here), so it would be sent to the device as '
                      'written')],
 'poll_queries_mapping': [],
 'poll_queries_not_list': [],
 'poll_serial': [('warning',
                  'polling.queries',
                  'Each poll sends 30 lines, 1.5 s at 50 ms apart (the spacing used when no '
                  'inter_command_delay is set), longer than the 1 s poll interval: the device '
                  'answers polls more than half the time, and each reading refreshes every 2.5 s. '
                  'Poll less per cycle, or set a longer poll_interval in default_config.')],
 'poll_text': [('error',
                'polling.queries[0]',
                "polling.queries[0]: '{zon}' resolves to nothing (did you mean 'zone'?). It names "
                'no config field (only config values substitute here), so it would be sent to the '
                'device as written')],
 'poll_udp_delay': [('warning',
                     'polling.queries',
                     'Each poll sends 3 lines, 1.5 s at 500 ms apart, longer than the 1 s poll '
                     'interval: the device answers polls more than half the time, and each reading '
                     'refreshes every 2.5 s. Poll less per cycle, or set a longer poll_interval in '
                     'default_config.')],
 'poll_udp_no_delay': [],
 'poll_with_delay': [('warning',
                      'polling.queries',
                      'Each poll sends 10 lines, 2 s at 200 ms apart, longer than the 1 s poll '
                      'interval: the device answers polls more than half the time, and each '
                      'reading refreshes every 3 s. Poll less per cycle, or set a longer '
                      'poll_interval in default_config.')],
 'polling_not_mapping': [('error', 'polling', 'polling: must be a mapping')],
 'port_open_discovery_not_mapping': [],
 'port_open_empty': [],
 'port_open_extras_not_list': [('warning',
                                'discovery',
                                'discovery.port_open is matched against a TCP port scan, but this '
                                "driver's transport is udp, so the hint can never fire. Drop it, "
                                'or add a transport that listens on TCP.')],
 'port_open_extras_odd': [('warning',
                           'discovery',
                           'discovery.port_open is matched against a TCP port scan, but this '
                           "driver's transport is osc, udp, so the hint can never fire. Drop it, "
                           'or add a transport that listens on TCP.')],
 'port_open_no_transport': [],
 'port_open_osc': [('warning',
                    'discovery',
                    "discovery.port_open is matched against a TCP port scan, but this driver's "
                    'transport is osc, so the hint can never fire. Drop it, or add a transport '
                    'that listens on TCP.')],
 'port_open_tcp': [],
 'port_open_transport_not_text': [('error', '', 'Unsupported transport: 5')],
 'port_open_udp_with_tcp': [],
 'resp_entry_not_mapping': [('error', 'responses[0]', 'Response 0: must be a mapping'),
                            ('error',
                             'responses[1].match',
                             "responses[1].match: '{q}' resolves to nothing. It names no config "
                             'field (only config values substitute here), so the pattern would '
                             'look for it as written'),
                            ('warning',
                             'liveness',
                             'Nothing notices if the device stops answering over tcp: it sends no '
                             'poll at its default config, and there is no liveness block, so the '
                             'device reads connected for as long as the connection stays open. Add '
                             'a liveness block that asks something the device answers in every '
                             'state.')],
 'resp_match_config': [('warning',
                        'liveness',
                        'Nothing notices if the device stops answering over tcp: it sends no poll '
                        'at its default config, and there is no liveness block, so the device '
                        'reads connected for as long as the connection stays open. Add a liveness '
                        'block that asks something the device answers in every state.')],
 'resp_match_not_text': [('error', 'responses[0]', 'Response 0: pattern must be a string, got int'),
                         ('warning',
                          'liveness',
                          'Nothing notices if the device stops answering over tcp: it sends no '
                          'poll at its default config, and there is no liveness block, so the '
                          'device reads connected for as long as the connection stays open. Add a '
                          'liveness block that asks something the device answers in every state.')],
 'resp_match_param_name': [('error',
                            'responses[0].match',
                            "responses[0].match: '{level}' resolves to nothing. It names no config "
                            'field (only config values substitute here), so the pattern would look '
                            'for it as written'),
                           ('warning',
                            'liveness',
                            'Nothing notices if the device stops answering over tcp: it sends no '
                            'poll at its default config, and there is no liveness block, so the '
                            'device reads connected for as long as the connection stays open. Add '
                            'a liveness block that asks something the device answers in every '
                            'state.')],
 'resp_match_typo': [('error',
                      'responses[0].match',
                      "responses[0].match: '{zon}' resolves to nothing (did you mean 'zone'?). It "
                      'names no config field (only config values substitute here), so the pattern '
                      'would look for it as written'),
                     ('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'resp_not_list': [('error', 'responses', 'responses: must be a list')],
 'silence_config_transport': [('warning',
                               'liveness',
                               'Nothing notices if the device stops answering over serial: polls '
                               'over serial are not checked for a reply, and there is no liveness '
                               'block, so the device reads connected for as long as the connection '
                               'stays open. Add a liveness block that asks something the device '
                               'answers in every state.')],
 'silence_defaults_not_mapping': [('warning',
                                   'liveness',
                                   'Nothing notices if the device stops answering over tcp: it '
                                   'sends no poll at its default config, and there is no liveness '
                                   'block, so the device reads connected for as long as the '
                                   'connection stays open. Add a liveness block that asks '
                                   'something the device answers in every state.')],
 'silence_extras': [('warning',
                     'liveness',
                     'Nothing notices if the device stops answering over serial: polls over serial '
                     'are not checked for a reply, and there is no liveness block, so the device '
                     'reads connected for as long as the connection stays open. Add a liveness '
                     'block that asks something the device answers in every state.')],
 'silence_extras_no_poll': [('warning',
                             'liveness',
                             'Nothing notices if the device stops answering over tcp or serial or '
                             'udp: it sends no poll at its default config, and there is no '
                             'liveness block, so the device reads connected for as long as the '
                             'connection stays open. Add a liveness block that asks something the '
                             'device answers in every state.')],
 'silence_extras_not_list': [('warning',
                              'liveness',
                              'Nothing notices if the device stops answering over tcp: it sends no '
                              'poll at its default config, and there is no liveness block, so the '
                              'device reads connected for as long as the connection stays open. '
                              'Add a liveness block that asks something the device answers in '
                              'every state.')],
 'silence_http': [],
 'silence_interval_nan': [('warning',
                           'polling.queries',
                           'Each poll sends 1 lines, 0.1 s at 50 ms apart (the spacing used when '
                           'no inter_command_delay is set), longer than the nan s poll interval: '
                           'the device answers polls more than half the time, and each reading '
                           'refreshes every nan s. Poll less per cycle, or set a longer '
                           'poll_interval in default_config.'),
                          ('warning',
                           'liveness',
                           'Nothing notices if the device stops answering over tcp: it sends no '
                           'poll at its default config, and there is no liveness block, so the '
                           'device reads connected for as long as the connection stays open. Add a '
                           'liveness block that asks something the device answers in every '
                           'state.')],
 'silence_interval_zero': [('warning',
                            'liveness',
                            'Nothing notices if the device stops answering over tcp: it sends no '
                            'poll at its default config, and there is no liveness block, so the '
                            'device reads connected for as long as the connection stays open. Add '
                            'a liveness block that asks something the device answers in every '
                            'state.')],
 'silence_liveness': [],
 'silence_liveness_empty': [('error',
                             'liveness',
                             "liveness: missing required 'send' (the probe payload: a raw protocol "
                             'string, or an OSC address on osc)'),
                            ('warning',
                             'liveness',
                             'Nothing notices if the device stops answering over tcp: it sends no '
                             'poll at its default config, and there is no liveness block, so the '
                             'device reads connected for as long as the connection stays open. Add '
                             'a liveness block that asks something the device answers in every '
                             'state.')],
 'silence_no_poll': [('warning',
                      'liveness',
                      'Nothing notices if the device stops answering over tcp: it sends no poll at '
                      'its default config, and there is no liveness block, so the device reads '
                      'connected for as long as the connection stays open. Add a liveness block '
                      'that asks something the device answers in every state.')],
 'silence_no_responses': [],
 'silence_queries_empty': [('warning',
                            'liveness',
                            'Nothing notices if the device stops answering over tcp: it sends no '
                            'poll at its default config, and there is no liveness block, so the '
                            'device reads connected for as long as the connection stays open. Add '
                            'a liveness block that asks something the device answers in every '
                            'state.')],
 'silence_queries_mapping': [],
 'silence_responses_not_list': [('error', 'responses', 'responses: must be a list')],
 'silence_serial_polls': [('warning',
                           'liveness',
                           'Nothing notices if the device stops answering over serial: polls over '
                           'serial are not checked for a reply, and there is no liveness block, so '
                           'the device reads connected for as long as the connection stays open. '
                           'Add a liveness block that asks something the device answers in every '
                           'state.')],
 'silence_tcp_polls': [],
 'then_query': [('error', 'responses[0]', 'Response 0: must be a mapping'),
                ('error', 'responses[2]', 'Response 2: then_query must list at least one query'),
                ('error',
                 'responses[1].then_query[0]',
                 "responses[1].then_query[0]: '{zz}' resolves to nothing. It names no config field "
                 '(only config values substitute here), so it would be sent to the device as '
                 'written'),
                ('error',
                 'responses[1].then_query[1]',
                 "responses[1].then_query[1]: '{yy}' resolves to nothing. It names no config field "
                 '(only config values substitute here), so it would be sent to the device as '
                 'written'),
                ('warning',
                 'liveness',
                 'Nothing notices if the device stops answering over tcp: it sends no poll at its '
                 'default config, and there is no liveness block, so the device reads connected '
                 'for as long as the connection stays open. Add a liveness block that asks '
                 'something the device answers in every state.')],
 'warnings_and_errors': [('error',
                          'commands.set.send',
                          "commands.set.send: '{levl}' resolves to nothing (did you mean "
                          "'level'?). It names no parameter of this command and no config field, "
                          'so it would be sent to the device as written'),
                         ('warning',
                          'config_schema.password',
                          "Config field 'password' is masked but has a default value, which is "
                          'stored in plain text inside the driver file. That is fine for a '
                          'published factory default, but never put a real site password here.'),
                         ('warning',
                          'liveness',
                          'Nothing notices if the device stops answering over tcp: it sends no '
                          'poll at its default config, and there is no liveness block, so the '
                          'device reads connected for as long as the connection stays open. Add a '
                          'liveness block that asks something the device answers in every state.')],
 'warnings_in_order': [('warning',
                        'config_schema.password',
                        "Config field 'password' is masked but has a default value, which is "
                        'stored in plain text inside the driver file. That is fine for a published '
                        'factory default, but never put a real site password here.'),
                       ('warning',
                        'discovery',
                        "discovery.port_open is matched against a TCP port scan, but this driver's "
                        'transport is osc, so the hint can never fire. Drop it, or add a transport '
                        'that listens on TCP.'),
                       ('warning',
                        'commands.a.params.x',
                        "Command 'a' parameter 'x' has no label, so forms show its key made "
                        'readable. Give it a label that says what to enter.'),
                       ('warning',
                        'liveness',
                        'Nothing notices if the device stops answering over osc: it sends no poll '
                        'at its default config, and there is no liveness block, so the device '
                        'reads connected for as long as the connection stays open. Add a liveness '
                        'block that asks something the device answers in every state.')]}
STRICT_EXPECTED: dict[str, list[tuple[str, str, str]]] = {'cmd_every_place_in_order': [('error',
                               'commands.set',
                               "Command 'set': declares send and path/method and address and udp, "
                               "but a command sends one way. Keep one of 'send' (TCP/serial), "
                               "'path'/'method' (HTTP), 'address' (OSC), or 'udp' (a datagram "
                               'beside the main transport)'),
                              ('error',
                               'commands.set',
                               "Command 'set': 'udp.port' is required with a payload (a number, or "
                               'a {config_field} placeholder)'),
                              ('error',
                               'commands.set',
                               "Command 'set': 'read_back' reads a value back over the device's "
                               "connection, and a 'udp' command is sent beside it, so nothing "
                               'would answer'),
                              ('error',
                               'commands.set.send',
                               "commands.set.send: '{s}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.path',
                               "commands.set.path: '{p}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.body',
                               "commands.set.body: '{b}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.address',
                               "commands.set.address: '{a}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.headers.H',
                               "commands.set.headers.H: '{h}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.read_back[0]',
                               "commands.set.read_back[0]: '{r}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.udp.payload',
                               "commands.set.udp.payload: '{u}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written'),
                              ('error',
                               'commands.set.udp.host',
                               "commands.set.udp.host: '{uh}' resolves to nothing. It names no "
                               'parameter of this command and no config field, so it would be sent '
                               'to the device as written')],
 'every_list_in_order': [('error',
                          'commands.set.send',
                          "commands.set.send: '{c}' resolves to nothing. It names no parameter of "
                          'this command and no config field, so it would be sent to the device as '
                          'written'),
                         ('error',
                          'responses[0].match',
                          "responses[0].match: '{m}' resolves to nothing. It names no config field "
                          '(only config values substitute here), so the pattern would look for it '
                          'as written'),
                         ('error',
                          'polling.queries[0]',
                          "polling.queries[0]: '{p}' resolves to nothing. It names no config field "
                          '(only config values substitute here), so it would be sent to the device '
                          'as written'),
                         ('error',
                          'on_connect[0]',
                          "on_connect[0]: '{o}' resolves to nothing. It names no config field "
                          '(only config values substitute here), so it would be sent to the device '
                          'as written'),
                         ('error',
                          'responses[0].then_query[0]',
                          "responses[0].then_query[0]: '{t}' resolves to nothing. It names no "
                          'config field (only config values substitute here), so it would be sent '
                          'to the device as written')],
 'warnings_and_errors': [('error',
                          'commands.set.send',
                          "commands.set.send: '{levl}' resolves to nothing (did you mean "
                          "'level'?). It names no parameter of this command and no config field, "
                          'so it would be sent to the device as written'),
                         ('error', '', "unknown key 'bogus_key'"),
                         ('warning',
                          'config_schema.password',
                          "Config field 'password' is masked but has a default value, which is "
                          'stored in plain text inside the driver file. That is fine for a '
                          'published factory default, but never put a real site password here.'),
                         ('warning',
                          'liveness',
                          'Nothing notices if the device stops answering over tcp: it sends no '
                          'poll at its default config, and there is no liveness block, so the '
                          'device reads connected for as long as the connection stays open. Add a '
                          'liveness block that asks something the device answers in every state.')]}
TOKEN_EXPECTED: dict[str, dict[str, Any]] = {'a}': {'declared_placeholders': [],
        'is_passthrough_send': False,
        'osc_read_back': ["No simulator handler matches OSC read_back query: 'a}'"],
        'runtime_warned': [],
        'runtime_wire': 'a}',
        'send_regex': 'a\\}',
        'validator': []},
 'x{a}y{c:>4}z': {'declared_placeholders': ['a', 'c'],
                  'is_passthrough_send': False,
                  'osc_read_back': ["No simulator handler matches OSC read_back query: 'x1y1z'"],
                  'runtime_warned': ["[acme] commands.t: '{c}' was not substituted and goes to the "
                                     'device literally — it names no parameter passed to this call '
                                     'and no config field. Declare it, or pass it when calling the '
                                     'command.'],
                  'runtime_wire': 'x1y{c:>4}z',
                  'send_regex': 'x(-?\\d+)y(.+)z',
                  'validator': []},
 '{ a}': {'declared_placeholders': [],
          'is_passthrough_send': False,
          'osc_read_back': ["No simulator handler matches OSC read_back query: '{ a}'"],
          'runtime_warned': [],
          'runtime_wire': '{ a}',
          'send_regex': '\\{ a\\}',
          'validator': []},
 '{"x": {a}}': {'declared_placeholders': ['a'],
                'is_passthrough_send': False,
                'osc_read_back': ['No simulator handler matches OSC read_back query: \'{"x": 1}\''],
                'runtime_warned': [],
                'runtime_wire': '{"x": 1}',
                'send_regex': '\\{"x": (-?\\d+)\\}',
                'validator': []},
 '{1}': {'declared_placeholders': [],
         'is_passthrough_send': False,
         'osc_read_back': ["No simulator handler matches OSC read_back query: '{1}'"],
         'runtime_warned': [],
         'runtime_wire': '{1}',
         'send_regex': '\\{1\\}',
         'validator': []},
 '{:02d}': {'declared_placeholders': [],
            'is_passthrough_send': False,
            'osc_read_back': ["No simulator handler matches OSC read_back query: '{:02d}'"],
            'runtime_warned': [],
            'runtime_wire': '{:02d}',
            'send_regex': '\\{:02d\\}',
            'validator': []},
 '{A}': {'declared_placeholders': [],
         'is_passthrough_send': False,
         'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
         'runtime_warned': ["[acme] commands.t: '{A}' was not substituted and goes to the device "
                            'literally — it names no parameter passed to this call and no config '
                            'field. Declare it, or pass it when calling the command.'],
         'runtime_wire': '{A}',
         'send_regex': '\\{A\\}',
         'validator': ["commands.t.send: '{A}' resolves to nothing. It names no parameter of this "
                       'command and no config field, so it would be sent to the device as '
                       'written']},
 '{_a}': {'declared_placeholders': [],
          'is_passthrough_send': False,
          'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
          'runtime_warned': ["[acme] commands.t: '{_a}' was not substituted and goes to the device "
                             'literally — it names no parameter passed to this call and no config '
                             'field. Declare it, or pass it when calling the command.'],
          'runtime_wire': '{_a}',
          'send_regex': '\\{_a\\}',
          'validator': ["commands.t.send: '{_a}' resolves to nothing. It names no parameter of "
                        'this command and no config field, so it would be sent to the device as '
                        'written']},
 '{a': {'declared_placeholders': [],
        'is_passthrough_send': False,
        'osc_read_back': ["No simulator handler matches OSC read_back query: '{a'"],
        'runtime_warned': [],
        'runtime_wire': '{a',
        'send_regex': '\\{a',
        'validator': []},
 '{a b}': {'declared_placeholders': [],
           'is_passthrough_send': False,
           'osc_read_back': ["No simulator handler matches OSC read_back query: '{a b}'"],
           'runtime_warned': [],
           'runtime_wire': '{a b}',
           'send_regex': '\\{a b\\}',
           'validator': []},
 '{a-b}': {'declared_placeholders': [],
           'is_passthrough_send': False,
           'osc_read_back': ["No simulator handler matches OSC read_back query: '{a-b}'"],
           'runtime_warned': [],
           'runtime_wire': '{a-b}',
           'send_regex': '\\{a-b\\}',
           'validator': []},
 '{a1}': {'declared_placeholders': [],
          'is_passthrough_send': False,
          'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
          'runtime_warned': ["[acme] commands.t: '{a1}' was not substituted and goes to the device "
                             'literally — it names no parameter passed to this call and no config '
                             'field. Declare it, or pass it when calling the command.'],
          'runtime_wire': '{a1}',
          'send_regex': '\\{a1\\}',
          'validator': ["commands.t.send: '{a1}' resolves to nothing. It names no parameter of "
                        'this command and no config field, so it would be sent to the device as '
                        'written']},
 '{a:\n}': {'declared_placeholders': ['a'],
            'is_passthrough_send': True,
            'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
            'runtime_warned': ["[acme] commands.t: '{a}' was not substituted and goes to the "
                               'device literally — it names no parameter passed to this call and '
                               'no config field. Declare it, or pass it when calling the command.'],
            'runtime_wire': '{a:\n}',
            'send_regex': '(-?\\d+)',
            'validator': []},
 '{a:02d}': {'declared_placeholders': ['a'],
             'is_passthrough_send': True,
             'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
             'runtime_warned': [],
             'runtime_wire': '01',
             'send_regex': '(-?\\d+)',
             'validator': []},
 '{a:0{2}d}': {'declared_placeholders': [],
               'is_passthrough_send': False,
               'osc_read_back': ["No simulator handler matches OSC read_back query: '{a:0{2}d}'"],
               'runtime_warned': [],
               'runtime_wire': '{a:0{2}d}',
               'send_regex': '\\{a:0\\{2\\}d\\}',
               'validator': []},
 '{a:x:y}': {'declared_placeholders': ['a'],
             'is_passthrough_send': True,
             'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
             'runtime_warned': ["[acme] commands.t: '{a}' was not substituted and goes to the "
                                'device literally — it names no parameter passed to this call and '
                                'no config field. Declare it, or pass it when calling the '
                                'command.'],
             'runtime_wire': '{a:x:y}',
             'send_regex': '(-?\\d+)',
             'validator': []},
 '{a:{b}}': {'declared_placeholders': ['b'],
             'is_passthrough_send': False,
             'osc_read_back': ["No simulator handler matches OSC read_back query: '{a:1}'"],
             'runtime_warned': ["[acme] commands.t: '{b}' was not substituted and goes to the "
                                'device literally — it names no parameter passed to this call and '
                                'no config field. Declare it, or pass it when calling the '
                                'command.'],
             'runtime_wire': '{a:{b}}',
             'send_regex': '\\{a:(.+)\\}',
             'validator': []},
 '{a:}': {'declared_placeholders': ['a'],
          'is_passthrough_send': True,
          'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
          'runtime_warned': [],
          'runtime_wire': '1',
          'send_regex': '(-?\\d+)',
          'validator': []},
 '{a{b}}': {'declared_placeholders': ['b'],
            'is_passthrough_send': False,
            'osc_read_back': ["No simulator handler matches OSC read_back query: '{a1}'"],
            'runtime_warned': ["[acme] commands.t: '{b}' was not substituted and goes to the "
                               'device literally — it names no parameter passed to this call and '
                               'no config field. Declare it, or pass it when calling the command.'],
            'runtime_wire': '{a{b}}',
            'send_regex': '\\{a(.+)\\}',
            'validator': []},
 '{a}': {'declared_placeholders': ['a'],
         'is_passthrough_send': True,
         'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
         'runtime_warned': [],
         'runtime_wire': '1',
         'send_regex': '(-?\\d+)',
         'validator': []},
 '{a}{b}': {'declared_placeholders': ['a', 'b'],
            'is_passthrough_send': False,
            'osc_read_back': ["No simulator handler matches OSC read_back query: '11'"],
            'runtime_warned': ["[acme] commands.t: '{b}' was not substituted and goes to the "
                               'device literally — it names no parameter passed to this call and '
                               'no config field. Declare it, or pass it when calling the command.'],
            'runtime_wire': '1{b}',
            'send_regex': '(-?\\d+)(.+)',
            'validator': []},
 '{b}x{b}': {'declared_placeholders': ['b', 'b'],
             'is_passthrough_send': False,
             'osc_read_back': ["No simulator handler matches OSC read_back query: '1x1'"],
             'runtime_warned': ["[acme] commands.t: '{b}' was not substituted and goes to the "
                                'device literally — it names no parameter passed to this call and '
                                'no config field. Declare it, or pass it when calling the '
                                'command.'],
             'runtime_wire': '{b}x{b}',
             'send_regex': '(.+)x(.+)',
             'validator': []},
 '{q:}': {'declared_placeholders': [],
          'is_passthrough_send': False,
          'osc_read_back': ["No simulator handler matches OSC read_back query: '1'"],
          'runtime_warned': ["[acme] commands.t: '{q}' was not substituted and goes to the device "
                             'literally — it names no parameter passed to this call and no config '
                             'field. Declare it, or pass it when calling the command.'],
          'runtime_wire': '{q:}',
          'send_regex': '\\{q:\\}',
          'validator': ["commands.t.send: '{q}' resolves to nothing. It names no parameter of this "
                        'command and no config field, so it would be sent to the device as '
                        'written']},
 '{{a}}': {'declared_placeholders': ['a'],
           'is_passthrough_send': False,
           'osc_read_back': ["No simulator handler matches OSC read_back query: '{1}'"],
           'runtime_warned': [],
           'runtime_wire': '{1}',
           'send_regex': '\\{(-?\\d+)\\}',
           'validator': []},
 '{}': {'declared_placeholders': [],
        'is_passthrough_send': False,
        'osc_read_back': ["No simulator handler matches OSC read_back query: '{}'"],
        'runtime_warned': [],
        'runtime_wire': '{}',
        'send_regex': '\\{\\}',
        'validator': []},
 '{é}': {'declared_placeholders': [],
         'is_passthrough_send': False,
         'osc_read_back': ["No simulator handler matches OSC read_back query: '{é}'"],
         'runtime_warned': [],
         'runtime_wire': '{é}',
         'send_regex': '\\{é\\}',
         'validator': []}}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_issues_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    got = _issues(definition, strict=False)
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [
        m for s, _, m in got if s == "error"
    ]
    assert list(validate_driver_warnings(definition)) == [
        m for s, _, m in got if s == "warning"
    ]
    substitutions = validate_substitutions(definition)
    assert [m for m in substitutions] == [
        m for s, _, m in got if s == "error" and m in substitutions
    ]


@pytest.mark.parametrize("case", sorted(STRICT_EXPECTED))
def test_strict_keeps_them_first(case: str) -> None:
    assert _issues(_definition(case), strict=True) == STRICT_EXPECTED[case]


@pytest.mark.parametrize("case", sorted(RAISES))
def test_shapes_that_raise(case: str) -> None:
    definition = {**BASE, **RAISING_CASES[case]}
    with pytest.raises(RAISES[case]):
        validate_driver_warnings(definition)
    with pytest.raises(RAISES[case]):
        validate_driver_issues(definition)
    validate_driver_definition(definition)


@pytest.mark.parametrize("template", sorted(TOKEN_EXPECTED))
def test_every_reader_of_the_token(template: str) -> None:
    assert _token_readings(template) == TOKEN_EXPECTED[template]


def test_validate_substitutions_takes_a_definition_with_nothing_in_it() -> None:
    assert validate_substitutions({}) == []
    assert validate_driver_warnings({}) == []


@pytest.mark.parametrize("value", [None, [], "driver", 5])
def test_a_definition_that_is_not_a_mapping_warns_nothing(value: Any) -> None:
    assert validate_driver_warnings(value) == []
    assert validate_driver_issues(value) == [
        {"severity": "error", "message": "Driver definition must be a mapping", "path": ""}
    ]


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED)
    assert STRICT == set(STRICT_EXPECTED)
    assert set(RAISES) == set(RAISING_CASES)
    assert set(TOKENS) == set(TOKEN_EXPECTED)
