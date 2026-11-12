"""Pins for the simulator validator's send side, through ``validate_yaml_driver``.

Whether the simulator answers what the driver sends: each command's sample
(``command_coverage``), each poll query, ``then_query`` entry and ``read_back``
query (``poll_coverage``), the same for an OSC driver's addresses, and the
sample value each parameter type is given. Each case is an invented Acme
driver; the expected lines are what the validator says today, messages and
order exact, so a restructure that changes any of them fails here.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from openavc.simulator.validate import (
    ValidationResult,
    _check_reply_framing,
    validate_yaml_driver,
)


_BASE: dict = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {
        "power_on": {"send": "PWR 1\\r\\n"},
        "ask_power": {"send": "PWR?\\r\\n"},
        "set_level": {"send": "LVL {level}\\r\\n", "params": {"level": {"type": "integer"}}},
    },
    "responses": [
        {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
        {"match": "^LVL (\\d+)$", "set": {"level": "$1"}},
    ],
}


def _with(**changes) -> dict:
    """The base driver with top-level keys replaced (None removes one)."""
    definition = copy.deepcopy(_BASE)
    for key, value in changes.items():
        if value is None:
            definition.pop(key, None)
        else:
            definition[key] = value
    return definition


def _polling(*queries, **changes) -> dict:
    return _with(polling={"interval": 5, "queries": list(queries)}, **changes)


def _handled(handlers, **changes) -> dict:
    definition = _with(**changes)
    definition["simulator"] = {"command_handlers": handlers}
    return definition


def _commands(**commands) -> dict:
    return dict(_BASE["commands"], **commands)


#: One command per parameter type, each sent as an OSC address, so the
#: sample each one is given shows in the error (an OSC driver with nothing
#: to answer it).
_OSC_TYPED: dict = {
    "int_default": {"address": "/a/{v}", "params": {"v": {"type": "integer", "default": 7}}},
    "int_min": {"address": "/b/{v}", "params": {"v": {"type": "integer", "min": 3}}},
    "int_bare": {"address": "/c/{v}", "params": {"v": {"type": "integer"}}},
    "int_both": {"address": "/c2/{v}", "params": {"v": {"type": "integer", "default": 5, "min": 2}}},
    "child": {"address": "/d/{v:02d}", "params": {"v": {"type": "child_id"}}},
    "num_default": {"address": "/e/{v}", "params": {"v": {"type": "number", "default": 2.5}}},
    "num_min": {"address": "/f/{v}", "params": {"v": {"type": "number", "min": -1}}},
    "num_bare": {"address": "/g/{v:.2f}", "params": {"v": {"type": "number"}}},
    "num_both": {"address": "/g2/{v}", "params": {"v": {"type": "number", "default": 0.5, "min": 0.1}}},
    "flag": {"address": "/h/{v:02d}", "params": {"v": {"type": "boolean"}}},
    "enum_plain": {"address": "/i/{v}", "params": {"v": {"type": "enum", "values": ["x1", "x2"]}}},
    "enum_pair": {
        "address": "/j/{v}",
        "params": {"v": {"type": "enum", "values": [{"value": "w1", "label": "One"}]}},
    },
    "enum_empty": {"address": "/k/{v}", "params": {"v": {"type": "enum", "values": []}}},
    "text_default": {"address": "/l/{v}", "params": {"v": {"type": "string", "default": "abc"}}},
    "text_bare": {"address": "/m/{v}", "params": {"v": {}}},
    "two": {
        "address": "/n/{a}/{b}",
        "params": {"a": {"type": "integer", "min": 2}, "b": {"type": "string"}},
    },
    "config_filled": {"address": "/o/{zone}", "params": {}},
    "config_unfilled": {"address": "/p/{alias}", "params": {}},
    "protocol_braces": {"address": "/q/{{v}}", "params": {"v": {"type": "integer", "min": 4}}},
}


def _osc(**changes) -> dict:
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "osc",
        "default_config": {"port": 10023, "zone": "z1"},
        "config_schema": {"alias": {"type": "string"}},
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
        "commands": {},
        "responses": [],
    }
    definition.update(changes)
    return definition


CASES: dict[str, dict] = {
    # ── command coverage (text) ──
    "command_all_covered_by_the_built_in_handlers": _with(),
    "command_passthrough_skipped": _with(commands=_commands(
        raw={"send": "{text}\\r", "params": {"text": {"type": "string"}}},
    )),
    "command_passthrough_with_a_numeric_spec": _with(commands=_commands(
        raw={"send": "{text:02d}\\r", "params": {"text": {"type": "string"}}},
    )),
    "command_sample_unfillable": _with(
        commands=_commands(alias={"send": "NAME {alias}\\r\\n"}),
        config_schema={"alias": {"type": "string"}},
    ),
    "command_sample_filled_from_config": _with(
        commands=_commands(zone={"send": "Z{zone} ON\\r\\n"}),
        default_config={"zone": "3"},
    ),
    "command_sample_filled_from_derived_config": _with(
        commands=_commands(zone={"send": "{target}ON\\r\\n"}),
        default_config={"zone": "3"},
        config_derived={"target": "Z{zone} "},
    ),
    "command_without_send_skipped": _with(commands=_commands(
        wake={"udp": {"port": 9, "payload": "AA"}},
    )),
    "command_matched_by_an_explicit_handler": _handled(
        [{"receive": "PWR 1", "respond": "PWR 1\\r\\n"}],
    ),
    "command_matched_with_the_prefix": _handled(
        [{"receive": "@PWR 1", "respond": "PWR 1\\r\\n"}], command_prefix="@",
    ),
    "command_raw_skips_the_prefix": _handled(
        [{"receive": "PWR 1", "respond": "PWR 1\\r\\n"}],
        command_prefix="@",
        commands={"power_on": {"send": "PWR 1\\r\\n", "raw": True}},
    ),
    "command_raw_with_config_skips_the_prefix": _with(
        command_prefix="@",
        default_config={"zone": "3"},
        commands={"zone_on": {"send": "Z{zone} ON\\r\\n", "raw": True}},
    ),
    "command_already_prefixed": _with(
        command_prefix="@", commands={"power_on": {"send": "@PWR 1\\r\\n"}},
    ),
    "command_handler_that_is_not_a_mapping": _handled(["PWR 1"]),
    "command_handler_regex_that_does_not_compile": _handled(
        [{"receive": "PWR (", "respond": "x"}, {"match": "", "respond": "y"}],
    ),
    "command_number_with_a_spec": _with(commands={
        "set_gain": {"send": "G{gain:+.1f}\\r\\n", "params": {"gain": {"type": "number", "min": -10}}},
    }),
    "command_enum_pair": _with(commands={
        "pick": {
            "send": "IN {src}\\r\\n",
            "params": {"src": {"type": "enum", "values": [{"value": "a", "label": "A"}, "b"]}},
        },
    }),
    "command_protocol_braces": _with(commands={
        "brace": {"send": "{{{level}}}\\r\\n", "params": {"level": {"type": "integer", "min": 2}}},
    }),
    # ── poll coverage (text) ──
    "poll_query_unanswered": _polling("STATUS?\\r\\n"),
    "poll_query_equal_to_a_plain_send": _polling("PWR?\\r\\n"),
    "poll_query_matching_a_parameterised_command": _polling("LVL 5"),
    "poll_query_equal_to_a_send_with_config": _polling(
        "Z{zone}?",
        default_config={"zone": "7"},
        commands=_commands(ask_zone={"send": "Z{zone}?"}),
    ),
    "poll_query_naming_a_command": _polling("ask_power", "set_level"),
    "poll_query_declared_by_query_for": _polling({"send": "XYZ?", "query_for": "power"}),
    "poll_query_query_for_blank_still_checked": _polling({"send": "XYZ?", "query_for": ""}),
    "poll_query_empty": _polling("", {"send": ""}),
    "poll_query_dict_unanswered": _polling({"send": "INFO?\\r\\n"}),
    "poll_query_each_child": _polling("CH{child_id:02d}?", "CH{child_id}?"),
    "poll_query_config_filled": _polling("Z{zone}?", default_config={"zone": "7"}),
    "poll_query_matched_by_a_handler": _handled(
        [{"receive": "STATUS\\?", "respond": "PWR 1\\r\\n"}],
        polling={"interval": 5, "queries": ["STATUS?\\r\\n"]},
    ),
    "poll_query_skips_a_command_without_send": _polling(
        "WAKE?",
        commands=_commands(wake={"udp": {"port": 9, "payload": "AA"}}),
    ),
    "poll_query_http_raw_path": _polling(
        "/api/status",
        transport="http",
        delimiter=None,
        commands={"power_on": {"method": "POST", "path": "/api/power", "send": "x"}},
    ),
    "poll_query_whitespace_and_terminators": _polling("  STATUS? \\r\\n"),
    "then_query_unanswered": _with(responses=[
        {"match": "^CHANGED$", "then_query": ["WHAT?\\r\\n", "PWR?\\r\\n"]},
        {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
    ]),
    "answering_passes_over_an_address_entry": _handled(
        [{"receive": "STATUS\\?", "respond": "HUH\r\n"}, {"receive": "INFO\\?", "respond": "EH\r\n"}],
        on_connect=[
            {"address": "/x", "send": "STATUS?", "query_for": "power"},
            {"send": "INFO?", "query_for": "power"},
        ],
    ),
    # ── which queries each check reads ──
    # Poll coverage: the poll, then_query and read_back, passing over a
    # query_for entry and a command's name. The query-answer list: the
    # poll, then_query and an on_connect entry with query_for. The framing
    # probes: the liveness, the poll as written and each command's sample.
    "which_queries_each_check_reads": _handled(
        [
            {"receive": "ask_power", "respond": "A1"},
            {"receive": "Q1\\?", "respond": "A2"},
            {"receive": "OC\\?", "respond": "A3"},
            {"receive": "OCW", "respond": "A4"},
            {"receive": "TQ\\?", "respond": "A5"},
            {"receive": "RB\\?", "respond": "A6"},
            {"receive": "LIVE\\?", "respond": "A7"},
        ],
        polling={"interval": 5, "queries": [
            "ask_power", {"send": "Q1?", "query_for": "power"}, {"send": "QF?", "query_for": "power"},
        ]},
        on_connect=[{"send": "OC?", "query_for": "power"}, {"send": "OCW"}, {"send": "OCX?"}],
        liveness={"send": "LIVE?\\r\\n"},
        responses=[
            {"match": "^CHANGED$", "then_query": ["TQ?"]},
            {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
        ],
        commands=_commands(power_off={"send": "PWR 0\\r\\n", "read_back": "RB?"}),
    ),
    # ── malformed shapes: what each says or raises today ──
    "malformed_command_not_a_mapping": _with(commands=_commands(bad="PWR 9")),
    "malformed_param_not_a_mapping": _with(commands={
        "set_level": {"send": "LVL {level}\\r\\n", "params": {"level": "integer"}},
    }),
    "malformed_params_not_a_mapping": _with(commands={
        "set_level": {"send": "LVL {level}\\r\\n", "params": ["level"]},
    }),
    "malformed_send_not_text": _with(commands={"odd": {"send": 7}}),
    "malformed_poll_entries": _polling(7, None, {"send": 7}, {"send": None}, ["X?"]),
    "malformed_polling_null": _with(polling=None) | {"polling": None},
    "malformed_polling_queries_null": _with(polling={"queries": None}),
    "malformed_commands_null": _with() | {"commands": None},
    "malformed_handlers_not_a_list": _with(simulator={"command_handlers": {"receive": "X"}}),
    "malformed_default_config_list": _with(default_config=["zone"]),
    "malformed_default_config_list_no_commands": _with(default_config=["zone"], commands={}),
    "malformed_config_derived_text": _with(config_derived="Z{zone}"),
    "malformed_config_derived_text_no_commands": _with(config_derived="Z{zone}", commands={}),
    "malformed_then_query_text": _with(responses=[{"match": "^X$", "then_query": "TQ?"}]),
    "malformed_read_back_number": _with(commands=_commands(off={"send": "OFF\\r\\n", "read_back": 5})),
    "malformed_liveness_text": _with(liveness="PING"),
    "malformed_on_connect_text": _with(on_connect="PWR?"),
    "osc_malformed_command_not_a_mapping": _osc(commands={"bad": "/x"}),
    "osc_malformed_param_not_a_mapping": _osc(commands={"m": {"address": "/a/{v}", "params": {"v": "int"}}}),
    "osc_malformed_poll_entries": _osc(polling={"queries": [7, None, {"send": 7}]}),
    "osc_malformed_handlers_not_a_list": _osc(simulator={"command_handlers": {"address": "/x"}}),
    "osc_malformed_default_config_list": _osc(default_config=["port"], commands={"m": {"address": "/a"}}),
    "read_back_unanswered": _with(commands=_commands(
        power_off={"send": "PWR 0\\r\\n", "read_back": "POWER?\\r\\n"},
    )),
    "read_back_naming_a_command": _with(commands=_commands(
        power_off={"send": "PWR 0\\r\\n", "read_back": "ask_power"},
    )),
    # ── OSC: command coverage ──
    "osc_samples_by_parameter_type": _osc(commands=_OSC_TYPED),
    "osc_command_covered_by_a_response_address": _osc(
        commands={"mute": {"address": "/ch/1/mute", "params": {}}},
        responses=[{"address": "/ch/1/mute", "set": {"level": "$1"}}],
    ),
    "osc_command_covered_by_a_handler_address": _osc(
        commands={"mute": {"address": "/ch/{ch}/mute", "params": {"ch": {"type": "integer"}}}},
        simulator={"command_handlers": [{"address": "/ch/*/mute", "handler": "pass"}]},
    ),
    "osc_command_covered_by_a_system_address": _osc(
        commands={"info": {"address": "/info", "params": {}}},
    ),
    "osc_command_covered_by_the_template_shape": _osc(
        commands={"mix": {"address": "/mtx/{mtx}/mix/on", "params": {"mtx": {"type": "string"}}}},
        responses=[{"address": "/mtx/01/mix/on", "set": {"level": "$1"}}],
    ),
    "osc_command_covered_by_a_wildcard_shape": _osc(
        commands={"mix": {"address": "/bus/{b}/on", "params": {"b": {"type": "string"}}}},
        simulator={"command_handlers": [
            {"address": "/bus/[0-9]?/on", "handler": "pass"}, "not a handler", {"handler": "pass"},
        ]},
        responses=["^not osc$", {"address": ""}],
    ),
    "osc_command_covered_by_a_one_character_wildcard": _osc(
        commands={"mix": {"address": "/bus/{b}/on", "params": {"b": {"type": "string"}}}},
        simulator={"command_handlers": [{"address": "/bus/?/on", "handler": "pass"}]},
    ),
    "osc_command_shape_with_a_config_hole": _osc(
        commands={"mix": {"address": "/{alias}/{b}/on", "params": {"b": {"type": "string"}}}},
        responses=[{"address": "/x/01/on", "set": {"level": "$1"}}],
    ),
    "osc_command_shape_that_does_not_compile": _osc(
        commands={"mix": {"address": "/a/{b:x}/on", "params": {"b": {"type": "string"}}}},
        responses=[{"address": "/a/01/on", "set": {"level": "$1"}}],
    ),
    "osc_command_without_address_skipped": _osc(
        commands={"plain": {"send": "x"}, "mute": {"address": "", "params": {}}},
    ),
    # ── OSC: poll coverage ──
    "osc_poll_queries": _osc(
        commands={"mute": {"address": "/ch/1/mute", "params": {}}},
        responses=[{"address": "/ch/1/mute", "set": {"level": "$1"}}],
        polling={"interval": 5, "queries": [
            "mute", "", {"send": ""}, "/ch/1/mute", {"send": "/ch/2/mute"},
            "/ch/{child_id}/mute", "/ch/{child_id:02d}/mute", "/{zone}/level", "/info",
        ]},
    ),
}

#: The checks these cases exercise; the other checks' lines are not pinned here.
_CHECKS = frozenset({"command_coverage", "poll_coverage", "response_parsing", "reply_framing"})


def _issues(tmp_path, name: str) -> list[tuple[str, str, str]] | tuple[str, str, str]:
    """The pinned checks' lines, or what the validator raises."""
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(CASES[name], sort_keys=False), encoding="utf-8")
    try:
        result = validate_yaml_driver(path)
    except Exception as exc:  # noqa: BLE001 - what raises is pinned too
        return ("raises", type(exc).__name__, str(exc))
    return [(i.severity, i.check, i.message) for i in result.issues if i.check in _CHECKS]


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_send_side_says_what_it_said(tmp_path, name):
    assert _issues(tmp_path, name) == EXPECTED[name]


def test_every_case_has_an_expectation():
    assert sorted(EXPECTED) == sorted(CASES)


def test_framing_probes_in_the_order_the_driver_sends_them():
    """Liveness, then the poll, then each command whose sample fills; an
    unfillable command sends nothing."""
    definition = {
        "id": "acme_widget", "transport": "tcp", "delimiter": "\\r\\n",
        "liveness": {"send": "PING\\r\\n"},
        "config_schema": {"alias": {"type": "string"}},
        "commands": {
            "alias": {"send": "NAME {alias}\\r\\n"},
            "power_on": {"send": "PWR 1\\r\\n"},
        },
        "polling": {"queries": ["LVL?"]},
        "simulator": {"command_handlers": [
            {"receive": "PING", "respond": "PONG"},
            {"receive": "LVL\\?", "respond": "L"},
            {"receive": "PWR 1", "respond": "P"},
            {"receive": "", "respond": "EMPTY"},
            {"receive": "NAME .*", "respond": "N"},
        ]},
    }
    result = ValidationResult("x.avcdriver", "acme_widget", "yaml")
    _check_reply_framing(result, definition["commands"], ["LVL?"], definition)
    assert [(i.severity, i.check, i.message) for i in result.issues] == FRAMING_ORDER


FRAMING_ORDER: list = [('error',
  'reply_framing',
  "3 of 3 simulator replies come back without the '\\\\r\\\\n' this driver splits incoming bytes "
  "on, so the driver never completes a frame and reads nothing they carry: 'PING' -> b'PONG'; "
  "'LVL?' -> b'L'; 'PWR 1' -> b'P'. A reply written by hand — a respond: template or a respond() "
  'call in a handler — is sent verbatim, so end it with the delimiter; only the handlers the '
  'simulator generates are terminated for you.')]


EXPECTED: dict = {'answering_passes_over_an_address_entry': [('warning',
                                             'response_parsing',
                                             'Simulator answers a query this driver polls with a '
                                             "reply no response rule matches: handler='INFO\\\\?', "
                                             "response='EH'. Nothing reads that answer, so the "
                                             'state behind the query never updates.'),
                                            ('info',
                                             'response_parsing',
                                             '1 simulator reply/replies match no driver response '
                                             'rule, none of them the only answer to a polled query '
                                             '— the shape of a write acknowledgement, which '
                                             "carries no state: 'HUH'. A polled query whose answer "
                                             'nothing parses warns instead.'),
                                            ('info',
                                             'reply_framing',
                                             '2 simulator replies checked, all terminated with '
                                             "'\\\\r\\\\n'.")],
 'command_all_covered_by_the_built_in_handlers': [('info',
                                                   'reply_framing',
                                                   '2 simulator replies checked, all terminated '
                                                   "with '\\\\r\\\\n'.")],
 'command_already_prefixed': [('info',
                               'reply_framing',
                               "1 simulator reply checked, all terminated with '\\\\r\\\\n'.")],
 'command_enum_pair': [],
 'command_handler_regex_that_does_not_compile': [('info',
                                                  'response_parsing',
                                                  '2 simulator reply/replies match no driver '
                                                  'response rule, none of them the only answer to '
                                                  'a polled query — the shape of a write '
                                                  "acknowledgement, which carries no state: 'x', "
                                                  "'y'. A polled query whose answer nothing parses "
                                                  'warns instead.'),
                                                 ('info',
                                                  'reply_framing',
                                                  '2 simulator replies checked, all terminated '
                                                  "with '\\\\r\\\\n'.")],
 'command_handler_that_is_not_a_mapping': [],
 'command_matched_by_an_explicit_handler': [('error',
                                             'reply_framing',
                                             '1 of 2 simulator replies come back without the '
                                             "'\\\\r\\\\n' this driver splits incoming bytes on, "
                                             'so the driver never completes a frame and reads '
                                             "nothing they carry: 'PWR 1' -> b'PWR 1\\\\r\\\\n'. A "
                                             'reply written by hand — a respond: template or a '
                                             'respond() call in a handler — is sent verbatim, so '
                                             'end it with the delimiter; only the handlers the '
                                             'simulator generates are terminated for you.')],
 'command_matched_with_the_prefix': [],
 'command_number_with_a_spec': [],
 'command_passthrough_skipped': [('info',
                                  'reply_framing',
                                  '2 simulator replies checked, all terminated with '
                                  "'\\\\r\\\\n'.")],
 'command_passthrough_with_a_numeric_spec': [('info',
                                              'reply_framing',
                                              '2 simulator replies checked, all terminated with '
                                              "'\\\\r\\\\n'.")],
 'command_protocol_braces': [('info',
                              'reply_framing',
                              "1 simulator reply checked, all terminated with '\\\\r\\\\n'.")],
 'command_raw_skips_the_prefix': [('error',
                                   'reply_framing',
                                   "1 of 1 simulator replies come back without the '\\\\r\\\\n' "
                                   'this driver splits incoming bytes on, so the driver never '
                                   "completes a frame and reads nothing they carry: 'PWR 1' -> "
                                   "b'PWR 1\\\\r\\\\n'. A reply written by hand — a respond: "
                                   'template or a respond() call in a handler — is sent verbatim, '
                                   'so end it with the delimiter; only the handlers the simulator '
                                   'generates are terminated for you.')],
 'command_raw_with_config_skips_the_prefix': [('error',
                                               'command_coverage',
                                               "No simulator handler matches command 'zone_on' "
                                               "(sample: 'Z3 ON')")],
 'command_sample_filled_from_config': [('error',
                                        'command_coverage',
                                        "No simulator handler matches command 'zone' (sample: 'Z3 "
                                        "ON')"),
                                       ('info',
                                        'reply_framing',
                                        '2 simulator replies checked, all terminated with '
                                        "'\\\\r\\\\n'.")],
 'command_sample_filled_from_derived_config': [('error',
                                                'command_coverage',
                                                "No simulator handler matches command 'zone' "
                                                "(sample: 'Z3 ON')"),
                                               ('info',
                                                'reply_framing',
                                                '2 simulator replies checked, all terminated with '
                                                "'\\\\r\\\\n'.")],
 'command_sample_unfillable': [('warning',
                                'command_coverage',
                                "Could not generate sample for command 'alias' (send: 'NAME "
                                "{alias}\\\\r\\\\n')"),
                               ('info',
                                'reply_framing',
                                "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'command_without_send_skipped': [('info',
                                   'reply_framing',
                                   '2 simulator replies checked, all terminated with '
                                   "'\\\\r\\\\n'.")],
 'malformed_command_not_a_mapping': ('raises',
                                     'AttributeError',
                                     "'str' object has no attribute 'get'"),
 'malformed_commands_null': ('raises',
                             'AttributeError',
                             "'NoneType' object has no attribute 'items'"),
 'malformed_config_derived_text': [('info',
                                    'reply_framing',
                                    '2 simulator replies checked, all terminated with '
                                    "'\\\\r\\\\n'.")],
 'malformed_config_derived_text_no_commands': [],
 'malformed_default_config_list': ('raises',
                                   'ValueError',
                                   'dictionary update sequence element #0 has length 4; 2 is '
                                   'required'),
 'malformed_default_config_list_no_commands': ('raises',
                                               'ValueError',
                                               'dictionary update sequence element #0 has length '
                                               '4; 2 is required'),
 'malformed_handlers_not_a_list': [],
 'malformed_liveness_text': [('info',
                              'reply_framing',
                              "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'malformed_on_connect_text': [('info',
                                'reply_framing',
                                "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'malformed_param_not_a_mapping': ('raises',
                                   'AttributeError',
                                   "'str' object has no attribute 'get'"),
 'malformed_params_not_a_mapping': ('raises',
                                    'AttributeError',
                                    "'list' object has no attribute 'items'"),
 'malformed_poll_entries': ('raises',
                            'TypeError',
                            "expected string or bytes-like object, got 'int'"),
 'malformed_polling_null': ('raises', 'AttributeError', "'NoneType' object has no attribute 'get'"),
 'malformed_polling_queries_null': ('raises', 'TypeError', "'NoneType' object is not iterable"),
 'malformed_read_back_number': [('info',
                                 'reply_framing',
                                 "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'malformed_send_not_text': ('raises', 'AttributeError', "'int' object has no attribute 'strip'"),
 'malformed_then_query_text': [],
 'osc_command_covered_by_a_handler_address': [],
 'osc_command_covered_by_a_one_character_wildcard': [],
 'osc_command_covered_by_a_response_address': [],
 'osc_command_covered_by_a_system_address': [],
 'osc_command_covered_by_a_wildcard_shape': [],
 'osc_command_covered_by_the_template_shape': [],
 'osc_command_shape_that_does_not_compile': [],
 'osc_command_shape_with_a_config_hole': [('warning',
                                           'command_coverage',
                                           "Could not generate sample address for command 'mix' "
                                           "(address: '/{alias}/{b}/on')")],
 'osc_command_without_address_skipped': [],
 'osc_malformed_command_not_a_mapping': ('raises',
                                         'AttributeError',
                                         "'str' object has no attribute 'get'"),
 'osc_malformed_default_config_list': ('raises',
                                       'ValueError',
                                       'dictionary update sequence element #0 has length 4; 2 is '
                                       'required'),
 'osc_malformed_handlers_not_a_list': [],
 'osc_malformed_param_not_a_mapping': ('raises',
                                       'AttributeError',
                                       "'str' object has no attribute 'get'"),
 'osc_malformed_poll_entries': [('error',
                                 'poll_coverage',
                                 "No simulator handler matches OSC polling query: '7'"),
                                ('error',
                                 'poll_coverage',
                                 "No simulator handler matches OSC polling query: 'None'"),
                                ('error',
                                 'poll_coverage',
                                 "No simulator handler matches OSC polling query: '7'")],
 'osc_poll_queries': [('error',
                       'poll_coverage',
                       "No simulator handler matches OSC polling query: '/ch/2/mute'"),
                      ('error',
                       'poll_coverage',
                       "No simulator handler matches OSC polling query: '/ch/{child_id:02d}/mute'"),
                      ('error',
                       'poll_coverage',
                       "No simulator handler matches OSC polling query: '/z1/level'")],
 'osc_samples_by_parameter_type': [('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'int_default' "
                                    "(sample address: '/a/7') — add a command_handlers entry with "
                                    'a matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'int_min' (sample "
                                    "address: '/b/3') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'int_bare' (sample "
                                    "address: '/c/1') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'int_both' (sample "
                                    "address: '/c2/5') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'child' (sample "
                                    "address: '/d/01') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'num_default' "
                                    "(sample address: '/e/2.5') — add a command_handlers entry "
                                    'with a matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'num_min' (sample "
                                    "address: '/f/-1') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'num_bare' (sample "
                                    "address: '/g/1.00') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'num_both' (sample "
                                    "address: '/g2/0.5') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'flag' (sample "
                                    "address: '/h/01') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'enum_plain' (sample "
                                    "address: '/i/x1') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'enum_pair' (sample "
                                    "address: '/j/w1') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'enum_empty' (sample "
                                    "address: '/k/test') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'text_default' "
                                    "(sample address: '/l/abc') — add a command_handlers entry "
                                    'with a matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'text_bare' (sample "
                                    "address: '/m/test') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'two' (sample "
                                    "address: '/n/2/test') — add a command_handlers entry with a "
                                    'matching address: pattern or a responses entry for it'),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'config_filled' "
                                    "(sample address: '/o/z1') — add a command_handlers entry with "
                                    'a matching address: pattern or a responses entry for it'),
                                   ('warning',
                                    'command_coverage',
                                    'Could not generate sample address for command '
                                    "'config_unfilled' (address: '/p/{alias}')"),
                                   ('error',
                                    'command_coverage',
                                    "No simulator handler matches OSC command 'protocol_braces' "
                                    "(sample address: '/q/{4}') — add a command_handlers entry "
                                    'with a matching address: pattern or a responses entry for '
                                    'it')],
 'poll_query_config_filled': [('error',
                               'poll_coverage',
                               "No simulator handler matches polling query: 'Z7?'"),
                              ('info',
                               'reply_framing',
                               "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'poll_query_declared_by_query_for': [('info',
                                       'reply_framing',
                                       '3 simulator replies checked, all terminated with '
                                       "'\\\\r\\\\n'.")],
 'poll_query_dict_unanswered': [('error',
                                 'poll_coverage',
                                 "No simulator handler matches polling query: 'INFO?'"),
                                ('info',
                                 'reply_framing',
                                 "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'poll_query_each_child': [('error',
                            'poll_coverage',
                            "No simulator handler matches polling query: 'CH01?'"),
                           ('error',
                            'poll_coverage',
                            "No simulator handler matches polling query: 'CH1?'"),
                           ('info',
                            'reply_framing',
                            "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'poll_query_empty': [('info',
                       'reply_framing',
                       "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'poll_query_equal_to_a_plain_send': [('info',
                                       'reply_framing',
                                       '2 simulator replies checked, all terminated with '
                                       "'\\\\r\\\\n'.")],
 'poll_query_equal_to_a_send_with_config': [('error',
                                             'command_coverage',
                                             "No simulator handler matches command 'ask_zone' "
                                             "(sample: 'Z7?')"),
                                            ('info',
                                             'reply_framing',
                                             '2 simulator replies checked, all terminated with '
                                             "'\\\\r\\\\n'.")],
 'poll_query_http_raw_path': [('error',
                               'poll_coverage',
                               "No simulator handler matches polling query: 'GET /api/status'")],
 'poll_query_matched_by_a_handler': [('error',
                                      'reply_framing',
                                      "1 of 3 simulator replies come back without the '\\\\r\\\\n' "
                                      'this driver splits incoming bytes on, so the driver never '
                                      "completes a frame and reads nothing they carry: 'STATUS?' "
                                      "-> b'PWR 1\\\\r\\\\n'. A reply written by hand — a respond: "
                                      'template or a respond() call in a handler — is sent '
                                      'verbatim, so end it with the delimiter; only the handlers '
                                      'the simulator generates are terminated for you.')],
 'poll_query_matching_a_parameterised_command': [('info',
                                                  'reply_framing',
                                                  '3 simulator replies checked, all terminated '
                                                  "with '\\\\r\\\\n'.")],
 'poll_query_naming_a_command': [('info',
                                  'reply_framing',
                                  '2 simulator replies checked, all terminated with '
                                  "'\\\\r\\\\n'.")],
 'poll_query_query_for_blank_still_checked': [('error',
                                               'poll_coverage',
                                               'No simulator handler matches polling query: '
                                               "'XYZ?'"),
                                              ('info',
                                               'reply_framing',
                                               '2 simulator replies checked, all terminated with '
                                               "'\\\\r\\\\n'.")],
 'poll_query_skips_a_command_without_send': [('error',
                                              'poll_coverage',
                                              'No simulator handler matches polling query: '
                                              "'WAKE?'"),
                                             ('info',
                                              'reply_framing',
                                              '2 simulator replies checked, all terminated with '
                                              "'\\\\r\\\\n'.")],
 'poll_query_unanswered': [('error',
                            'poll_coverage',
                            "No simulator handler matches polling query: 'STATUS?'"),
                           ('info',
                            'reply_framing',
                            "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'poll_query_whitespace_and_terminators': [('error',
                                            'poll_coverage',
                                            'No simulator handler matches polling query: '
                                            "'STATUS?'"),
                                           ('info',
                                            'reply_framing',
                                            '2 simulator replies checked, all terminated with '
                                            "'\\\\r\\\\n'.")],
 'read_back_naming_a_command': [('info',
                                 'reply_framing',
                                 "3 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'read_back_unanswered': [('error',
                           'poll_coverage',
                           "No simulator handler matches read_back entry: 'POWER?'"),
                          ('info',
                           'reply_framing',
                           "3 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'then_query_unanswered': [('error',
                            'poll_coverage',
                            "No simulator handler matches then_query entry: 'WHAT?'"),
                           ('info',
                            'reply_framing',
                            "1 simulator reply checked, all terminated with '\\\\r\\\\n'.")],
 'which_queries_each_check_reads': [('warning',
                                     'response_parsing',
                                     'Simulator answers a query this driver polls with a reply no '
                                     "response rule matches: handler='Q1\\\\?', response='A2'. "
                                     'Nothing reads that answer, so the state behind the query '
                                     'never updates.'),
                                    ('warning',
                                     'response_parsing',
                                     'Simulator answers a query this driver polls with a reply no '
                                     "response rule matches: handler='OC\\\\?', response='A3'. "
                                     'Nothing reads that answer, so the state behind the query '
                                     'never updates.'),
                                    ('warning',
                                     'response_parsing',
                                     'Simulator answers a query this driver polls with a reply no '
                                     "response rule matches: handler='TQ\\\\?', response='A5'. "
                                     'Nothing reads that answer, so the state behind the query '
                                     'never updates.'),
                                    ('info',
                                     'response_parsing',
                                     '4 simulator reply/replies match no driver response rule, '
                                     'none of them the only answer to a polled query — the shape '
                                     "of a write acknowledgement, which carries no state: 'A1', "
                                     "'A4', 'A6', 'A7'. A polled query whose answer nothing parses "
                                     'warns instead.'),
                                    ('error',
                                     'reply_framing',
                                     "3 of 6 simulator replies come back without the '\\\\r\\\\n' "
                                     'this driver splits incoming bytes on, so the driver never '
                                     "completes a frame and reads nothing they carry: 'LIVE?' -> "
                                     "b'A7'; 'ask_power' -> b'A1'; 'Q1?' -> b'A2'. A reply written "
                                     'by hand — a respond: template or a respond() call in a '
                                     'handler — is sent verbatim, so end it with the delimiter; '
                                     'only the handlers the simulator generates are terminated for '
                                     'you.')]}
