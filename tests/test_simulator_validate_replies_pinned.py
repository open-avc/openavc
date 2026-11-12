"""Pins for the simulator validator's reply side, through ``validate_yaml_driver``.

Whether the driver would read what the simulator sends: a query handler's
answer (``response_parsing``), a ``notifications:`` template's message
(``notifications``) and a reply's framing (``reply_framing``), plus the JSON
keys and the read-back lines those checks lean on. Each case is an invented
Acme driver; the expected lines are what the validator says today, messages
and order exact, so a restructure that changes any of them fails here.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from openavc.simulator.validate import (
    ValidationResult,
    _check_osc_read_back_coverage,
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
        "input": {"type": "enum", "values": ["hdmi1", "hdmi2"], "label": "Input"},
        "level": {"type": "integer", "min": 3, "label": "Level"},
        "name": {"type": "string", "label": "Name"},
    },
    "commands": {
        "power_on": {"send": "PWR 1\\r\\n"},
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


def _notifying(notifications, **changes) -> dict:
    definition = _with(**changes)
    sim = dict(definition.get("simulator") or {})
    sim["notifications"] = notifications
    definition["simulator"] = sim
    return definition


_JSON_RULES = [
    {"json": True, "set": {"power": {"key": "status.power"}, "name": "label[0]"}},
    {"json": True, "mappings": [{"key": "zone.level"}, "skipped", {"path": "ignored"}]},
    {"json": True, "child_set": [{"state": {"level": {"path": "outputs.1"}}}, "bad"]},
    {"match": "^OK$"},
]


CASES: dict[str, dict] = {
    # ── notifications: shape ──
    "notifications_not_a_mapping": _notifying(["PWR 1"]),
    "notifications_empty": _notifying({}),
    "notification_value_map_neither": _notifying({"power": 5}),
    "notification_unknown_key": _notifying({"volume": "VOL {value}"}),
    # ── notifications: value keys ──
    "notification_boolean_key_capitalised": _notifying({"power": {"True": "PWR 1", "false": "PWR 0"}}),
    "notification_boolean_value_template": _notifying({"power": "PWR {value}"}),
    "notification_enum_key_unknown": _notifying({"input": {"hdmi3": "INP 3", "hdmi1": "INP 1"}}),
    "notification_enum_star": _notifying({"input": {"*": "INP {value}"}}),
    # ── notifications: round trip ──
    "notification_round_trip_reads": _notifying({"power": {"true": "PWR 1", "false": "PWR 0"}}),
    "notification_round_trip_unread": _notifying({"power": {"true": "POWER ON"}}),
    "notification_star_default_from_type": _notifying({"level": "LVL {value}"}),
    "notification_star_default_unread": _notifying({"level": "LEVEL={value}"}),
    "notification_star_from_initial": _notifying(
        {"level": "LEVEL={value}"}, simulator={"initial_state": {"level": 7}},
    ),
    "notification_unresolved_placeholder": _notifying({"level": "LVL {mystery}"}),
    "notification_state_ref": _notifying(
        {"level": "LVL {state.level}"}, simulator={"initial_state": {"level": 9}},
    ),
    "notification_wrapped_in_crlf": _notifying({"level": "\r\nLVL {value}\r\n"}),
    "notification_own_terminator": _notifying(
        {"level": "LVL {value}>"}, delimiter=">",
    ),
    "notification_no_rules": _notifying({"level": "anything {value}"}, responses=[]),
    "notification_json_reads": _notifying(
        {"power": {"true": '{"status": {"power": true}}'}}, responses=_JSON_RULES,
    ),
    "notification_json_mapping_key": _notifying(
        {"level": '{"zone": {"level": 4}}'}, responses=_JSON_RULES,
    ),
    "notification_json_child_key": _notifying(
        {"level": '{"outputs": [1, 2]}'}, responses=_JSON_RULES,
    ),
    "notification_json_list_unwrap": _notifying(
        {"level": '[{"zone": 1}]'}, responses=_JSON_RULES,
    ),
    "notification_json_list_of_two": _notifying(
        {"level": '[{"zone": 1}, {"zone": 2}]'}, responses=_JSON_RULES,
    ),
    "notification_json_path_only_mapping": _notifying(
        {"level": '{"ignored": 1}'}, responses=_JSON_RULES,
    ),
    "notification_json_not_json": _notifying(
        {"level": "zone {value"}, responses=_JSON_RULES,
    ),
    "notification_json_set_shorthand": _notifying(
        {"name": '{"label": ["a"]}'}, responses=_JSON_RULES,
    ),
    # ── notifications: which transports carry them ──
    "push_gate_tcp": _notifying({"level": "LVL {value}"}),
    "push_gate_serial": _notifying({"level": "LVL {value}"}, transport="serial"),
    "push_gate_udp": _notifying({"level": "LVL {value}"}, transport="udp"),
    "push_gate_http_none": _notifying({"level": "LVL {value}"}, transport="http"),
    "push_gate_http_sse": _notifying(
        {"level": "LVL {value}"}, transport="http", push={"type": "sse", "path": "/events"},
    ),
    "push_gate_osc_sse": _notifying(
        {"level": "LVL {value}"}, transport="osc", push={"type": "sse", "path": "/events"},
    ),
    "push_gate_http_multicast": _notifying(
        {"level": "LVL {value}"}, transport="http",
        push={"type": "multicast", "group": "239.1.1.1", "port": 5000},
    ),
    "push_gate_http_tcp_listener": _notifying(
        {"level": "LVL {value}"}, transport="http", push={"type": "tcp_listener", "port": 0},
    ),
    "push_gate_http_http_listener": _notifying(
        {"level": "LVL {value}"}, transport="http", push={"type": "http_listener"},
    ),
    "push_gate_http_udp_listener": _notifying(
        {"level": "LVL {value}"}, transport="http", push={"type": "udp_listener", "port": 5001},
    ),
    "push_gate_http_unknown_type": _notifying(
        {"level": "LVL {value}"}, transport="http", push={"type": "carrier_pigeon"},
    ),
    "push_gate_http_list_type": _notifying(
        {"level": "LVL {value}"}, transport="http", push={"type": ["sse"]},
    ),
    "push_gate_http_push_not_mapping": _notifying(
        {"level": "LVL {value}"}, transport="http", push="sse",
    ),
    # ── response parsing ──
    "parsing_query_answer_unread": _with(
        polling={"queries": ["PWR?"]},
        simulator={"command_handlers": [{"receive": "PWR\\?", "respond": "POWER IS ON\r\n"}]},
    ),
    "parsing_query_answer_reads": _with(
        polling={"queries": ["PWR?"]},
        simulator={"command_handlers": [{"receive": "PWR\\?", "respond": "PWR 1\r\n"}]},
    ),
    "parsing_acknowledgements": _with(
        simulator={"command_handlers": [
            {"receive": "A1", "respond": "ACK\r\n"},
            {"receive": "A2", "respond": "ACK\\r\\n"},
            {"receive": "A3", "respond": "OK1\\r\\nOK2\\r\\n"},
            {"receive": "A4", "respond": "OK3"},
            {"receive": "A5", "respond": "OK4"},
            {"address": "/osc", "respond": "IGNORED"},
            {"receive": "A6"},
            "not a mapping",
        ]},
    ),
    "parsing_capture_unresolvable": _with(
        polling={"queries": ["LVL?"]},
        simulator={"command_handlers": [{"receive": "LVL\\?", "respond": "LEVEL {1}"}]},
    ),
    "parsing_state_ref_resolved": _with(
        polling={"queries": ["LVL?"]},
        simulator={
            "initial_state": {"level": 5},
            "command_handlers": [{"receive": "LVL\\?", "respond": "LEVEL={state.level}"}],
        },
    ),
    "parsing_script_handler": _with(
        polling={"queries": ["PWR?", "NAME?"]},
        simulator={"command_handlers": [
            {"receive": "PWR\\?", "handler": "respond('POWER?')\nrespond('PWR 1')\n"},
            {"receive": "NAME\\?", "handler": "respond('NAME ' + str(1))\nrespond('NAME X')\n"},
            {"receive": "ODD", "handler": "respond('{state.name}')\nrespond()\n"},
        ]},
    ),
    "parsing_json_reply": _with(
        polling={"queries": ["STATUS?"]},
        responses=_JSON_RULES,
        simulator={"command_handlers": [
            {"receive": "STATUS\\?", "respond": '{"status": {"power": true}}'},
        ]},
    ),
    "parsing_named_query_and_on_connect": _with(
        polling={"queries": [{"send": "power_on"}, {"address": "/x"}, {"send": ""}]},
        on_connect=[{"send": "NAME?", "query_for": "name"}, {"send": "ARM"}],
        simulator={"command_handlers": [
            {"receive": "PWR 1", "respond": "DONE\r\n"},
            {"receive": "NAME\\?", "respond": "NAME IS X\\r\\n"},
            {"receive": "ARM", "respond": "ARMED\\r\\n"},
        ]},
    ),
    "parsing_own_terminator": _with(
        delimiter=">",
        polling={"queries": ["PWR?"]},
        simulator={"command_handlers": [{"receive": "PWR\\?", "respond": "PWR 1>PWR 0>"}]},
    ),
    "parsing_config_in_pattern": _with(
        default_config={"unit": "7"},
        responses=[{"match": "^U{unit} PWR (\\d)$", "set": {"power": "$1"}}],
        polling={"queries": ["PWR?"]},
        simulator={"command_handlers": [{"receive": "PWR\\?", "respond": "U7 PWR 1"}]},
    ),
    "parsing_bad_regex_rule": _with(
        responses=[{"match": "^(unclosed"}, {"match": "^PWR (\\d)$", "set": {"power": "$1"}}],
        polling={"queries": ["PWR?"]},
        simulator={"command_handlers": [{"receive": "PWR\\?", "respond": "PWR 1"}]},
    ),
    "parsing_then_query_answer": _with(
        responses=[
            {"match": "^PWR (\\d)$", "set": {"power": "$1"}, "then_query": ["LVL?"]},
            {"match": "^LVL (\\d+)$", "set": {"level": "$1"}},
        ],
        simulator={"command_handlers": [{"receive": "LVL\\?", "respond": "LEVEL 4"}]},
    ),
    # ── reply framing ──
    "framing_unframed_many": _with(
        commands={
            f"c{i}": {"send": f"C{i}\\r\\n"} for i in range(5)
        },
        simulator={"command_handlers": [
            {"receive": f"C{i}", "respond": f"R{i}"} for i in range(5)
        ]},
    ),
    "framing_all_terminated": _with(
        simulator={"command_handlers": [{"receive": "PWR 1", "respond": "PWR 1\r\n"}]},
    ),
    "framing_raising_handler": _with(
        simulator={"command_handlers": [
            {"receive": "PWR 1", "handler": "raise RuntimeError('boom')\n"},
        ]},
    ),
    "framing_udp_skipped": _with(
        transport="udp",
        simulator={"command_handlers": [{"receive": "PWR 1", "respond": "R"}]},
    ),
    "framing_empty_delimiter_skipped": _with(
        delimiter="",
        simulator={"command_handlers": [{"receive": "PWR 1", "respond": "R"}]},
    ),
    # ── the breaks nothing caught at first ──
    "notification_unknown_state_ref": _notifying({"level": "LVL {state.missing}"}),
    "notification_json_rules_only": _notifying(
        {"level": "LEVEL={value}"}, responses=_JSON_RULES[:3],
    ),
    "notification_boolean_formatted": _notifying(
        {"power": {"true": "POWER {value:d}", "false": "PWR {value:d}"}},
    ),
    "notifications_a_string": _notifying("PWR 1"),
    "notification_json_set_by_path": _notifying(
        {"level": '{"meter": {"level": 1}}'},
        responses=[{"json": True, "set": {"level": {"path": "meter.level"}}}, {"match": "^X$"}],
    ),
    "notification_not_json_rule_keys": _notifying(
        {"level": '{"quality": 1}'},
        responses=[{"match": "^X$", "set": {"level": {"key": "quality"}}}],
    ),
    "parsing_serial_own_terminator": _with(
        transport="serial",
        delimiter=">",
        polling={"queries": ["PWR?"]},
        simulator={"command_handlers": [{"receive": "PWR\\?", "respond": "PWR 1>PWR 0>"}]},
    ),
    # ── what raises today ──
    "rule_not_a_mapping": _notifying({"level": "LVL {value}"}, responses=["^LVL$"]),
    "json_set_not_a_mapping": _notifying(
        {"level": "LVL {value}"}, responses=[{"json": True, "set": ["level"]}],
    ),
}

#: The checks these cases exercise; the other checks' lines are not pinned here.
_CHECKS = frozenset({
    "notifications", "response_parsing", "reply_framing", "poll_coverage",
})


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
def test_the_reply_side_says_what_it_said(tmp_path, name):
    assert _issues(tmp_path, name) == EXPECTED[name]


def test_every_case_has_an_expectation():
    assert sorted(EXPECTED) == sorted(CASES)



# ── Direct calls: the cases another check would stop first ──


def _lines(result: ValidationResult) -> list[tuple[str, str, str]]:
    return [(i.severity, i.check, i.message) for i in result.issues]


_FRAMED = {"transport": "tcp", "delimiter": "\\r\\n", "id": "acme_widget"}


def _framing(commands, queries, **definition) -> list[tuple[str, str, str]]:
    result = ValidationResult("x.avcdriver", "acme_widget", "yaml")
    _check_reply_framing(result, commands, queries, dict(_FRAMED, commands=commands, **definition))
    return _lines(result)


def test_framing_skips_a_send_that_is_not_text():
    commands = {
        "odd": {"send": 5}, "empty": {"send": ""}, "ok": {"send": "OK\\r\\n"},
    }
    assert _framing(commands, [], simulator={
        "command_handlers": [{"receive": "OK", "respond": "OK"}],
    }) == FRAMING_NON_TEXT


def test_framing_probes_liveness_then_queries_then_commands():
    commands = {"power_on": {"send": "PWR 1\\r\\n"}}
    queries = ["LVL?", {"send": "PWR?"}, {"send": 7}, {"nothing": 1}]
    assert _framing(
        commands, queries, liveness={"send": "PING\\r\\n"},
        polling={"queries": queries},
        simulator={"command_handlers": [
            {"receive": "PING", "respond": "PONG"},
            {"receive": "LVL\\?", "respond": "LVL 3\r\n"},
            {"receive": "PWR\\?", "respond": "PWR 1\r\nPWR"},
            {"receive": "PWR 1", "respond": "\r\nDONE\r\n>"},
        ]},
        responses=[{"match": "^>$"}],
    ) == FRAMING_PROBES


def test_framing_quotes_three_unframed_replies_and_counts_the_rest():
    commands = {f"c{i}": {"send": f"C{i}\\r\\n"} for i in range(5)}
    assert _framing(commands, [], simulator={"command_handlers": [
        {"receive": f"C{i}", "respond": f"R{i}"} for i in range(5)
    ]}) == FRAMING_MANY


def test_framing_quotes_three_trailing_replies_and_counts_the_rest():
    commands = {f"c{i}": {"send": f"C{i}\\r\\n"} for i in range(5)}
    assert _framing(commands, [], simulator={"command_handlers": [
        {"receive": f"C{i}", "respond": f"R{i}\r\nP{i}"} for i in range(5)
    ]}) == FRAMING_MANY_TRAILING


def test_framing_says_nothing_when_the_simulator_cannot_be_built(monkeypatch):
    from openavc.simulator import yaml_auto

    def refuse(self, *args, **kwargs):
        raise RuntimeError("cannot build")

    monkeypatch.setattr(yaml_auto.YAMLAutoSimulator, "__init__", refuse)
    assert _framing({"ok": {"send": "OK\\r\\n"}}, []) == []


def test_framing_says_nothing_when_the_delimiter_cannot_be_sent():
    # The simulator cannot be built on a delimiter that does not encode, and
    # the check says nothing rather than raising.
    assert _framing({"ok": {"send": "OK\\r\\n"}}, [], delimiter="\ud800") == []


def test_framing_passes_over_a_probe_the_simulator_raises_on(monkeypatch):
    from openavc.simulator import yaml_auto

    real = yaml_auto.YAMLAutoSimulator.handle_command

    def flaky(self, data):
        if data.startswith(b"BAD"):
            raise RuntimeError("boom")
        return real(self, data)

    monkeypatch.setattr(yaml_auto.YAMLAutoSimulator, "handle_command", flaky)
    commands = {"bad": {"send": "BAD\\r\\n"}, "ok": {"send": "OK\\r\\n"}}
    assert _framing(commands, [], simulator={"command_handlers": [
        {"receive": "BAD", "respond": "B"}, {"receive": "OK", "respond": "O"},
    ]}) == FRAMING_RAISING_PROBE


def test_framing_mutes_the_simulator_log_and_puts_its_level_back():
    import logging

    log = logging.getLogger("openavc.simulator.yaml_auto")
    log.setLevel(logging.INFO)
    seen: list[int] = []

    class Spy(logging.Handler):
        def emit(self, record):
            seen.append(record.levelno)

    spy = Spy()
    log.addHandler(spy)
    try:
        lines = _framing({"ok": {"send": "OK\\r\\n"}}, [], simulator={"command_handlers": [
            {"receive": "OK", "handler": "raise RuntimeError('boom')\n"},
        ]})
        assert log.level == logging.INFO
    finally:
        log.removeHandler(spy)
        log.setLevel(logging.NOTSET)
    assert lines == FRAMING_MUTED
    assert [lvl for lvl in seen if lvl < logging.CRITICAL] == []


def test_osc_read_back_reads_each_query_line():
    commands = {
        "gain": {"address": "/gain", "read_back": ["/gain/get", "mute", "", 5, "/unheard/{ch}"]},
        "mute": {"address": "/mute", "read_back": "/mute/get"},
        "odd": "not a mapping",
        "plain": {"address": "/plain", "read_back": True},
        "cfg": {"address": "/cfg", "read_back": "/{unit}/state"},
    }
    definition = {"transport": "osc", "default_config": {"unit": "u2"}}
    responses = [{"address": "/gain/get"}, {"address": "/mute*"}]
    result = ValidationResult("x.avcdriver", "acme_widget", "yaml")
    _check_osc_read_back_coverage(result, commands, [], responses, definition)
    assert _lines(result) == OSC_READ_BACK


def test_osc_read_back_with_no_commands_says_nothing():
    result = ValidationResult("x.avcdriver", "acme_widget", "yaml")
    _check_osc_read_back_coverage(result, None, [], [], {"transport": "osc"})
    assert _lines(result) == []


FRAMING_NON_TEXT: list = []
FRAMING_PROBES: list = [('warning',
  'reply_framing',
  "2 simulator replies end with text after the last '\\\\r\\\\n', which reaches the driver "
  "only when the next reply arrives, joined to its first line: 'PWR?' -> b'PWR 1\\r\\nPWR'; "
  "'PWR 1' -> b'\\r\\nDONE\\r\\n>'. End the reply with the delimiter, or, for a prompt the "
  'device prints after each reply, open every reply with the delimiter and give the prompt a '
  'response rule of its own.'),
 ('error',
  'reply_framing',
  "1 of 4 simulator replies come back without the '\\\\r\\\\n' this driver splits incoming "
  "bytes on, so the driver never completes a frame and reads nothing they carry: 'PING' -> "
  "b'PONG'. A reply written by hand — a respond: template or a respond() call in a handler — "
  'is sent verbatim, so end it with the delimiter; only the handlers the simulator generates '
  'are terminated for you.')]
FRAMING_MANY: list = [('error',
  'reply_framing',
  "5 of 5 simulator replies come back without the '\\\\r\\\\n' this driver splits incoming "
  "bytes on, so the driver never completes a frame and reads nothing they carry: 'C0' -> "
  "b'R0'; 'C1' -> b'R1'; 'C2' -> b'R2'; +2 more. A reply written by hand — a respond: template "
  'or a respond() call in a handler — is sent verbatim, so end it with the delimiter; only the '
  'handlers the simulator generates are terminated for you.')]
FRAMING_MANY_TRAILING: list = [('warning',
  'reply_framing',
  "5 simulator replies end with text after the last '\\\\r\\\\n', which reaches the driver "
  "only when the next reply arrives, joined to its first line: 'C0' -> b'R0\\r\\nP0'; 'C1' -> "
  "b'R1\\r\\nP1'; 'C2' -> b'R2\\r\\nP2'; +2 more. End the reply with the delimiter, or, for a "
  'prompt the device prints after each reply, open every reply with the delimiter and give the '
  'prompt a response rule of its own.')]
FRAMING_RAISING_PROBE: list = [('error',
  'reply_framing',
  "1 of 1 simulator replies come back without the '\\\\r\\\\n' this driver splits incoming "
  "bytes on, so the driver never completes a frame and reads nothing they carry: 'OK' -> b'O'. "
  'A reply written by hand — a respond: template or a respond() call in a handler — is sent '
  'verbatim, so end it with the delimiter; only the handlers the simulator generates are '
  'terminated for you.')]
FRAMING_MUTED: list = []
OSC_READ_BACK: list = [('error', 'poll_coverage', "No simulator handler matches OSC read_back query: '/unheard/1'"),
 ('error', 'poll_coverage', "No simulator handler matches OSC read_back query: '/u2/state'")]


EXPECTED: dict = {'framing_all_terminated': [('info',
                             'reply_framing',
                             "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'framing_empty_delimiter_skipped': [('info',
                                      'response_parsing',
                                      '1 simulator reply/replies match no driver response '
                                      'rule, none of them the only answer to a polled query — '
                                      'the shape of a write acknowledgement, which carries no '
                                      "state: 'R'. A polled query whose answer nothing parses "
                                      'warns instead.')],
 'framing_raising_handler': [('info',
                              'reply_framing',
                              "1 simulator reply checked, all terminated with '\\\\r\\\\n'.")],
 'framing_udp_skipped': [('info',
                          'response_parsing',
                          '1 simulator reply/replies match no driver response rule, none of '
                          'them the only answer to a polled query — the shape of a write '
                          "acknowledgement, which carries no state: 'R'. A polled query whose "
                          'answer nothing parses warns instead.')],
 'framing_unframed_many': [('info',
                            'response_parsing',
                            '5 simulator reply/replies match no driver response rule, none of '
                            'them the only answer to a polled query — the shape of a write '
                            "acknowledgement, which carries no state: 'R0', 'R1', 'R2', 'R3' "
                            '(+1 more). A polled query whose answer nothing parses warns '
                            'instead.'),
                           ('error',
                            'reply_framing',
                            "5 of 5 simulator replies come back without the '\\\\r\\\\n' this "
                            'driver splits incoming bytes on, so the driver never completes a '
                            "frame and reads nothing they carry: 'C0' -> b'R0'; 'C1' -> b'R1'; "
                            "'C2' -> b'R2'; +2 more. A reply written by hand — a respond: "
                            'template or a respond() call in a handler — is sent verbatim, so '
                            'end it with the delimiter; only the handlers the simulator '
                            'generates are terminated for you.')],
 'json_set_not_a_mapping': ('raises',
                            'AttributeError',
                            "'list' object has no attribute 'values'"),
 'notification_boolean_formatted': [('info',
                                     'reply_framing',
                                     '2 simulator replies checked, all terminated with '
                                     "'\\\\r\\\\n'."),
                                    ('warning',
                                     'notifications',
                                     "notification 'power' emits 'POWER 1' which matches no "
                                     'driver response pattern — the driver will ignore it')],
 'notification_boolean_key_capitalised': [('info',
                                           'reply_framing',
                                           '2 simulator replies checked, all terminated with '
                                           "'\\\\r\\\\n'."),
                                          ('error',
                                           'notifications',
                                           "notification 'power' value key 'True' never "
                                           'matches — boolean lookups use lowercase '
                                           "'true'/'false' (quote the keys in YAML so they "
                                           'stay strings)')],
 'notification_boolean_value_template': [('info',
                                          'reply_framing',
                                          '2 simulator replies checked, all terminated with '
                                          "'\\\\r\\\\n'."),
                                         ('error',
                                          'notifications',
                                          "notification 'power' template uses {value} but "
                                          "'power' is boolean — Python's str(True) produces "
                                          "'True' not 'true'. Use per-value templates ('true': "
                                          "..., 'false': ...) instead.")],
 'notification_enum_key_unknown': [('info',
                                    'reply_framing',
                                    '2 simulator replies checked, all terminated with '
                                    "'\\\\r\\\\n'."),
                                   ('warning',
                                    'notifications',
                                    "notification 'input' value key 'hdmi3' is not one of the "
                                    "enum values ['hdmi1', 'hdmi2']"),
                                   ('warning',
                                    'notifications',
                                    "notification 'input' emits 'INP 3' which matches no "
                                    'driver response pattern — the driver will ignore it'),
                                   ('warning',
                                    'notifications',
                                    "notification 'input' emits 'INP 1' which matches no "
                                    'driver response pattern — the driver will ignore it')],
 'notification_enum_star': [('info',
                             'reply_framing',
                             "2 simulator replies checked, all terminated with '\\\\r\\\\n'."),
                            ('warning',
                             'notifications',
                             "notification 'input' emits 'INP hdmi1' which matches no driver "
                             'response pattern — the driver will ignore it')],
 'notification_json_child_key': [],
 'notification_json_list_of_two': [('warning',
                                    'notifications',
                                    'notification \'level\' emits \'[{"zone": 1}, {"zone": '
                                    "2}]' which matches no driver response pattern — the "
                                    'driver will ignore it')],
 'notification_json_list_unwrap': [],
 'notification_json_mapping_key': [],
 'notification_json_not_json': [('warning',
                                 'notifications',
                                 "notification 'level' emits 'zone {value' which matches no "
                                 'driver response pattern — the driver will ignore it')],
 'notification_json_path_only_mapping': [('warning',
                                          'notifications',
                                          'notification \'level\' emits \'{"ignored": 1}\' '
                                          'which matches no driver response pattern — the '
                                          'driver will ignore it')],
 'notification_json_reads': [],
 'notification_json_rules_only': [('warning',
                                   'notifications',
                                   "notification 'level' emits 'LEVEL=3' which matches no "
                                   'driver response pattern — the driver will ignore it')],
 'notification_json_set_by_path': [],
 'notification_json_set_shorthand': [],
 'notification_no_rules': [],
 'notification_not_json_rule_keys': [('info',
                                      'reply_framing',
                                      '1 simulator reply checked, all terminated with '
                                      "'\\\\r\\\\n'."),
                                     ('warning',
                                      'notifications',
                                      'notification \'level\' emits \'{"quality": 1}\' which '
                                      'matches no driver response pattern — the driver will '
                                      'ignore it')],
 'notification_own_terminator': [('info',
                                  'reply_framing',
                                  "2 simulator replies checked, all terminated with '>'.")],
 'notification_round_trip_reads': [('info',
                                    'reply_framing',
                                    '2 simulator replies checked, all terminated with '
                                    "'\\\\r\\\\n'.")],
 'notification_round_trip_unread': [('info',
                                     'reply_framing',
                                     '2 simulator replies checked, all terminated with '
                                     "'\\\\r\\\\n'."),
                                    ('warning',
                                     'notifications',
                                     "notification 'power' emits 'POWER ON' which matches no "
                                     'driver response pattern — the driver will ignore it')],
 'notification_star_default_from_type': [('info',
                                          'reply_framing',
                                          '2 simulator replies checked, all terminated with '
                                          "'\\\\r\\\\n'.")],
 'notification_star_default_unread': [('info',
                                       'reply_framing',
                                       '2 simulator replies checked, all terminated with '
                                       "'\\\\r\\\\n'."),
                                      ('warning',
                                       'notifications',
                                       "notification 'level' emits 'LEVEL=3' which matches no "
                                       'driver response pattern — the driver will ignore it')],
 'notification_star_from_initial': [('info',
                                     'reply_framing',
                                     '2 simulator replies checked, all terminated with '
                                     "'\\\\r\\\\n'."),
                                    ('warning',
                                     'notifications',
                                     "notification 'level' emits 'LEVEL=7' which matches no "
                                     'driver response pattern — the driver will ignore it')],
 'notification_state_ref': [('info',
                             'reply_framing',
                             "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'notification_unknown_key': [('info',
                               'reply_framing',
                               '2 simulator replies checked, all terminated with '
                               "'\\\\r\\\\n'."),
                              ('warning',
                               'notifications',
                               "notification key 'volume' is not a state variable or "
                               'initial_state key — it only fires if a handler writes that '
                               'key'),
                              ('warning',
                               'notifications',
                               "notification 'volume' emits 'VOL ' which matches no driver "
                               'response pattern — the driver will ignore it')],
 'notification_unknown_state_ref': [('info',
                                     'reply_framing',
                                     '2 simulator replies checked, all terminated with '
                                     "'\\\\r\\\\n'.")],
 'notification_unresolved_placeholder': [('info',
                                          'reply_framing',
                                          '2 simulator replies checked, all terminated with '
                                          "'\\\\r\\\\n'.")],
 'notification_value_map_neither': [('info',
                                     'reply_framing',
                                     '2 simulator replies checked, all terminated with '
                                     "'\\\\r\\\\n'."),
                                    ('error',
                                     'notifications',
                                     "notification 'power' must be a template string or a "
                                     'value → template mapping, got: 5')],
 'notification_wrapped_in_crlf': [('info',
                                   'reply_framing',
                                   '2 simulator replies checked, all terminated with '
                                   "'\\\\r\\\\n'.")],
 'notifications_a_string': [('error',
                             'notifications',
                             'simulator.notifications must be a mapping')],
 'notifications_empty': [('info',
                          'reply_framing',
                          "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'notifications_not_a_mapping': [('error',
                                  'notifications',
                                  'simulator.notifications must be a mapping')],
 'parsing_acknowledgements': [('info',
                               'response_parsing',
                               '5 simulator reply/replies match no driver response rule, none '
                               'of them the only answer to a polled query — the shape of a '
                               "write acknowledgement, which carries no state: 'ACK', 'OK1', "
                               "'OK2', 'OK3' (+1 more). A polled query whose answer nothing "
                               'parses warns instead.')],
 'parsing_bad_regex_rule': [('error',
                             'reply_framing',
                             "1 of 2 simulator replies come back without the '\\\\r\\\\n' this "
                             'driver splits incoming bytes on, so the driver never completes a '
                             "frame and reads nothing they carry: 'PWR?' -> b'PWR 1'. A reply "
                             'written by hand — a respond: template or a respond() call in a '
                             'handler — is sent verbatim, so end it with the delimiter; only '
                             'the handlers the simulator generates are terminated for you.')],
 'parsing_capture_unresolvable': [('error',
                                   'reply_framing',
                                   '1 of 3 simulator replies come back without the '
                                   "'\\\\r\\\\n' this driver splits incoming bytes on, so the "
                                   'driver never completes a frame and reads nothing they '
                                   "carry: 'LVL?' -> b'LEVEL {1}'. A reply written by hand — a "
                                   'respond: template or a respond() call in a handler — is '
                                   'sent verbatim, so end it with the delimiter; only the '
                                   'handlers the simulator generates are terminated for you.')],
 'parsing_config_in_pattern': [('error',
                                'reply_framing',
                                "1 of 2 simulator replies come back without the '\\\\r\\\\n' "
                                'this driver splits incoming bytes on, so the driver never '
                                "completes a frame and reads nothing they carry: 'PWR?' -> "
                                "b'U7 PWR 1'. A reply written by hand — a respond: template or "
                                'a respond() call in a handler — is sent verbatim, so end it '
                                'with the delimiter; only the handlers the simulator generates '
                                'are terminated for you.')],
 'parsing_json_reply': [('error',
                         'reply_framing',
                         "1 of 1 simulator replies come back without the '\\\\r\\\\n' this "
                         'driver splits incoming bytes on, so the driver never completes a '
                         'frame and reads nothing they carry: \'STATUS?\' -> b\'{"status": '
                         '{"power": true}}\'. A reply written by hand — a respond: template or '
                         'a respond() call in a handler — is sent verbatim, so end it with the '
                         'delimiter; only the handlers the simulator generates are terminated '
                         'for you.')],
 'parsing_named_query_and_on_connect': [('warning',
                                         'response_parsing',
                                         'Simulator answers a query this driver polls with a '
                                         "reply no response rule matches: handler='PWR 1', "
                                         "response='DONE'. Nothing reads that answer, so the "
                                         'state behind the query never updates.'),
                                        ('warning',
                                         'response_parsing',
                                         'Simulator answers a query this driver polls with a '
                                         "reply no response rule matches: handler='NAME\\\\?', "
                                         "response='NAME IS X'. Nothing reads that answer, so "
                                         'the state behind the query never updates.'),
                                        ('info',
                                         'response_parsing',
                                         '1 simulator reply/replies match no driver response '
                                         'rule, none of them the only answer to a polled query '
                                         '— the shape of a write acknowledgement, which '
                                         "carries no state: 'ARMED'. A polled query whose "
                                         'answer nothing parses warns instead.'),
                                        ('info',
                                         'reply_framing',
                                         '2 simulator replies checked, all terminated with '
                                         "'\\\\r\\\\n'.")],
 'parsing_own_terminator': [('info',
                             'reply_framing',
                             "3 simulator replies checked, all terminated with '>'.")],
 'parsing_query_answer_reads': [('info',
                                 'reply_framing',
                                 '3 simulator replies checked, all terminated with '
                                 "'\\\\r\\\\n'.")],
 'parsing_query_answer_unread': [('warning',
                                  'response_parsing',
                                  'Simulator answers a query this driver polls with a reply no '
                                  "response rule matches: handler='PWR\\\\?', response='POWER "
                                  "IS ON'. Nothing reads that answer, so the state behind the "
                                  'query never updates.'),
                                 ('info',
                                  'reply_framing',
                                  '3 simulator replies checked, all terminated with '
                                  "'\\\\r\\\\n'.")],
 'parsing_script_handler': [('info',
                             'response_parsing',
                             '2 simulator reply/replies match no driver response rule, none of '
                             'them the only answer to a polled query — the shape of a write '
                             "acknowledgement, which carries no state: 'POWER?', 'NAME X'. A "
                             'polled query whose answer nothing parses warns instead.'),
                            ('error',
                             'reply_framing',
                             "2 of 4 simulator replies come back without the '\\\\r\\\\n' this "
                             'driver splits incoming bytes on, so the driver never completes a '
                             "frame and reads nothing they carry: 'PWR?' -> b'POWER?'; 'NAME?' "
                             "-> b'NAME 1'. A reply written by hand — a respond: template or a "
                             'respond() call in a handler — is sent verbatim, so end it with '
                             'the delimiter; only the handlers the simulator generates are '
                             'terminated for you.')],
 'parsing_serial_own_terminator': [('info',
                                    'reply_framing',
                                    "3 simulator replies checked, all terminated with '>'.")],
 'parsing_state_ref_resolved': [('warning',
                                 'response_parsing',
                                 'Simulator answers a query this driver polls with a reply no '
                                 "response rule matches: handler='LVL\\\\?', "
                                 "response='LEVEL=5'. Nothing reads that answer, so the state "
                                 'behind the query never updates.'),
                                ('error',
                                 'reply_framing',
                                 "1 of 3 simulator replies come back without the '\\\\r\\\\n' "
                                 'this driver splits incoming bytes on, so the driver never '
                                 "completes a frame and reads nothing they carry: 'LVL?' -> "
                                 "b'LEVEL=5'. A reply written by hand — a respond: template or "
                                 'a respond() call in a handler — is sent verbatim, so end it '
                                 'with the delimiter; only the handlers the simulator '
                                 'generates are terminated for you.')],
 'parsing_then_query_answer': [('warning',
                                'response_parsing',
                                'Simulator answers a query this driver polls with a reply no '
                                "response rule matches: handler='LVL\\\\?', response='LEVEL "
                                "4'. Nothing reads that answer, so the state behind the query "
                                'never updates.'),
                               ('info',
                                'reply_framing',
                                '2 simulator replies checked, all terminated with '
                                "'\\\\r\\\\n'.")],
 'push_gate_http_http_listener': [],
 'push_gate_http_list_type': [('warning',
                               'notifications',
                               "notifications: has no effect for transport 'http' — only TCP, "
                               'serial and UDP simulators push notification messages on the '
                               'control link (unless the driver declares a multicast, SSE, TCP '
                               'dial-back, or HTTP-listener push: block)')],
 'push_gate_http_multicast': [],
 'push_gate_http_none': [('warning',
                          'notifications',
                          "notifications: has no effect for transport 'http' — only TCP, "
                          'serial and UDP simulators push notification messages on the control '
                          'link (unless the driver declares a multicast, SSE, TCP dial-back, '
                          'or HTTP-listener push: block)')],
 'push_gate_http_push_not_mapping': [('warning',
                                      'notifications',
                                      "notifications: has no effect for transport 'http' — "
                                      'only TCP, serial and UDP simulators push notification '
                                      'messages on the control link (unless the driver '
                                      'declares a multicast, SSE, TCP dial-back, or '
                                      'HTTP-listener push: block)')],
 'push_gate_http_sse': [],
 'push_gate_http_tcp_listener': [],
 'push_gate_http_udp_listener': [],
 'push_gate_http_unknown_type': [('warning',
                                  'notifications',
                                  "notifications: has no effect for transport 'http' — only "
                                  'TCP, serial and UDP simulators push notification messages '
                                  'on the control link (unless the driver declares a '
                                  'multicast, SSE, TCP dial-back, or HTTP-listener push: '
                                  'block)')],
 'push_gate_osc_sse': [('warning',
                        'notifications',
                        "notifications: has no effect for transport 'osc' — only TCP, serial "
                        'and UDP simulators push notification messages on the control link '
                        '(unless the driver declares a multicast, SSE, TCP dial-back, or '
                        'HTTP-listener push: block)')],
 'push_gate_serial': [('info',
                       'reply_framing',
                       "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'push_gate_tcp': [('info',
                    'reply_framing',
                    "2 simulator replies checked, all terminated with '\\\\r\\\\n'.")],
 'push_gate_udp': [],
 'rule_not_a_mapping': ('raises', 'AttributeError', "'str' object has no attribute 'get'")}
