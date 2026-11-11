"""Pins for three things a generated simulator builds from its driver: the
handlers that answer a ``device_settings`` write, a Generic device's inline
protocol (the merge, its response-pattern handlers, the HTTP JSON status
poll), and the order construction builds things in. Recorded from the code as
it is, on invented devices.
"""

from __future__ import annotations

import json
import logging

import pytest

from openavc.simulator.yaml_auto import CommandHandler, YAMLAutoSimulator, _merge_inline_protocol


def _records(caplog) -> list[tuple[str, str, str]]:
    return [(r.name, r.levelname, r.getMessage()) for r in caplog.records
            if r.name.startswith("openavc.simulator") and r.levelno >= logging.DEBUG
            and "Auto-gen simulator for" not in r.getMessage()]


def _driver(settings, **overrides) -> dict:
    d = {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp", "delimiter": "\\r",
        "default_config": {"unit": "02"},
        "state_variables": {"level": {"type": "integer"}, "gain": {"type": "number", "scale": 0.1},
                            "mode": {"type": "string"}},
        "commands": {"go": {"send": "GO"}}, "responses": [], "device_settings": settings,
    }
    d.update(overrides)
    return d


def _sim(settings, config=None, **overrides) -> YAMLAutoSimulator:
    return YAMLAutoSimulator("w1", config or {}, driver_def=_driver(settings, **overrides))


def _row(h: CommandHandler) -> tuple:
    return (h.name, h.pattern.pattern, h.state_changes, h.response_var, h.group_bases,
            h.group_conversions, h.group_value_maps)


def _setting_rows(sim: YAMLAutoSimulator) -> list[tuple]:
    return [_row(h) for h in sim._command_handlers if h.name.startswith("setting:")]


def _level(pattern: str) -> list[tuple]:
    return [("setting:level", pattern, {"level": 1}, "level", {}, {}, {})]


# ── Device settings writes ──


@pytest.mark.parametrize("write, rows", [
    ({"send": "LVL {value}"}, _level(r"^LVL (-?\d+)$")),
    ({"send": "LVL {value:02X}"},
     [("setting:level", r"^LVL ([0-9a-fA-F]+)$", {"level": 1}, "level", {1: 16}, {}, {})]),
    ({"path": "/set", "body": "v={value}"}, _level(r"^POST /set\|v=(-?\d+)$")),
    ({"method": "put", "body": "{value}"}, _level(r"^PUT /\|(-?\d+)$")),
    ({"path": "", "method": "patch", "body": "x={value}"}, _level(r"^PATCH /\|x=(-?\d+)$")),
    ({"path": "/v/{value}", "body": 5}, _level(r"^POST /v/(-?\d+)$")),
    ({"path": "/v/{value}", "body": ""}, _level(r"^POST /v/(-?\d+)$")),
    ({"path": "/v/{value}", "send": "LVL {value}"}, _level(r"^POST /v/(-?\d+)$")),
    ({"address": "/x", "send": "LVL {value}"}, []),
    ({"address": "/x", "path": "/v/{value}"}, []),
    # The write form is picked by what is filled in, not by which keys are
    # present: an empty address or path hands on to the next form.
    ({"address": "", "send": "LVL {value}"}, _level(r"^LVL (-?\d+)$")),
    ({"path": "", "send": "LVL {value}"}, _level(r"^LVL (-?\d+)$")),
    ({"path": 5, "send": "LVL {value}"}, _level(r"^LVL (-?\d+)$")),
    ({"method": "", "send": "LVL {value}"}, _level(r"^LVL (-?\d+)$")),
    ({"send": ""}, []),
    ({"send": 5}, []),
    ({}, []),
    ({"send": "SET {value}\\rGET?"}, _level(r"^SET (-?\d+)$")),
    ({"send": "HDR\\r\\nSET {value}"}, _level(r"^SET (-?\d+)$")),
    ({"send": "A {value}\\rB {value}"}, _level(r"^A (-?\d+)$")),
    ({"send": "U{unit} {value}"}, _level(r"^U02 (-?\d+)$")),
    ({"send": "LVL 5"}, []),
    ({"send": "  \\r{value}  "}, _level(r"^(-?\d+)$")),
    ({"send": "LVL {nope} {value}"}, _level(r"^LVL \{nope\} (-?\d+)$")),
])
def test_a_settings_write_builds_one_handler_from_the_line_it_puts_on_the_wire(write, rows):
    assert _setting_rows(_sim({"level": {"type": "integer", "write": write}})) == rows


@pytest.mark.parametrize("stype, value_map, pattern, maps", [
    ("integer", None, r"^V (-?\d+)$", {}),
    ("number", None, r"^V (-?[\d.]+)$", {}),
    ("float", None, r"^V (-?[\d.]+)$", {}),
    ("boolean", None, r"^V (true|false|0|1)$", {}),
    ("enum", None, r"^V (.+)$", {}),
    ("string", None, r"^V (.+)$", {}),
    ("weird", None, r"^V (.+)$", {}),
    (None, None, r"^V (.+)$", {}),
    ("boolean", {"true": "ON", "false": "OFF"}, r"^V (.+)$", {1: {"true": "ON", "false": "OFF"}}),
    ("integer", {"1": "ONE"}, r"^V (.+)$", {1: {"1": "ONE"}}),
    ("integer", {}, r"^V (-?\d+)$", {}),
    ("integer", ["x"], r"^V (-?\d+)$", {}),
])
def test_a_setting_captures_by_its_type_unless_it_maps_words(stype, value_map, pattern, maps):
    entry: dict = {"write": {"send": "V {value}"}, "state_key": "level"}
    if stype is not None:
        entry["type"] = stype
    if value_map is not None:
        entry["map"] = value_map
    assert _setting_rows(_sim({"v": entry})) == [
        ("setting:v", pattern, {"level": 1}, "level", {}, {}, maps)
    ]


def test_a_setting_reads_its_variables_conversion():
    sim = _sim({"gain": {"type": "number", "write": {"send": "G {value}"}}})
    assert _setting_rows(sim) == [
        ("setting:gain", r"^G (-?[\d.]+)$", {"gain": 1}, "gain", {}, {1: {"scale": 0.1}}, {})
    ]


def test_settings_follow_the_commands_in_declared_order_and_skip_what_is_not_a_write(caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _sim({
            "b": {"type": "integer", "write": {"send": "B {value}"}},
            "x": "not a mapping", "y": {"write": "not a mapping"}, "z": {"type": "integer"},
            "a": {"type": "integer", "state_key": "", "write": {"send": "A {value}"}},
        })
    assert [_row(h) for h in sim._command_handlers] == [
        ("go", "^GO$", {}, None, {}, {}, {}),
        ("setting:b", r"^B (-?\d+)$", {"b": 1}, "b", {}, {}, {}),
        ("setting:a", r"^A (-?\d+)$", {"a": 1}, "a", {}, {}, {}),
    ]
    assert ("openavc.simulator.yaml_auto", "DEBUG",
            "Auto-sim acme_widget: 2 device_settings write handlers") in _records(caplog)


@pytest.mark.parametrize("settings", [["x"], "x", None, {}])
def test_settings_that_are_not_a_mapping_build_nothing_and_say_nothing(settings, caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _sim(settings)
    assert [h.name for h in sim._command_handlers] == ["go"]
    assert not [r for r in _records(caplog) if "device_settings" in r[2]]


def test_a_setting_whose_pattern_will_not_compile_is_skipped_with_a_warning(monkeypatch, caplog):
    # send_regex escapes every literal, so only an unescaped one can reach the
    # compile: the warning is pinned through that.
    from openavc.simulator import send_templates
    monkeypatch.setattr(send_templates, "_escape_literal", lambda text: text)
    with caplog.at_level(logging.DEBUG):
        sim = _sim({"bad": {"type": "integer", "write": {"send": "LVL [ {value}"}},
                    "good": {"type": "integer", "write": {"send": "G {value}"}}})
    assert [h.name for h in sim._command_handlers] == ["go", "setting:good"]
    assert ("openavc.simulator.yaml_auto", "WARNING",
            r"Invalid regex from setting 'bad': LVL [ (-?\d+)") in _records(caplog)


def test_one_setting_still_logs_its_count(caplog):
    with caplog.at_level(logging.DEBUG):
        _sim({"level": {"type": "integer", "write": {"send": "L {value}"}}})
    assert ("openavc.simulator.yaml_auto", "DEBUG",
            "Auto-sim acme_widget: 1 device_settings write handlers") in _records(caplog)


def test_a_config_field_named_value_is_never_substituted_into_the_write():
    sim = _sim({"level": {"type": "integer", "write": {"send": "U{unit} {value} {value_}"}}},
               {"unit": "07", "value": "9", "value_": "3"})
    assert _setting_rows(sim) == _level(r"^U07 (-?\d+) 3$")


def test_a_setting_that_collides_with_a_command_loses_to_it():
    sim = _sim({"level": {"type": "integer", "write": {"send": "GO"}}})
    assert [h.name for h in sim._command_handlers] == ["go"]
    sim = _sim({"level": {"type": "integer", "write": {"send": "L {value}"}}},
               commands={"lvl": {"send": "L {level}", "params": {"level": {"type": "integer"}}}})
    assert [h.name for h in sim._command_handlers] == ["lvl", "setting:level"]
    assert sim.handle_command(b"L 7") is None
    assert sim._state["level"] == 7


@pytest.mark.parametrize("line, state, reply", [
    (b"POST /set|v=12", 12, None),
    (b"PUT /set|v=12", 0, None),
])
def test_an_http_settings_write_is_answered_through_the_command_chain(line, state, reply):
    sim = _sim({"level": {"type": "integer", "write": {"path": "/set", "body": "v={value}"}}},
               transport="http")
    assert sim.handle_command(line) == reply
    assert sim._state.get("level") == state


# The setting's word read back to the setting value, stored in a text
# variable as its text, and reported by the response rule as stored.
MAPPED_WRITE = ("true", b"P true\r")


def test_a_mapped_setting_write_lands_as_its_value_and_reports_back_in_the_word():
    sim = _sim({"pwr": {"type": "boolean", "state_key": "mode",
                        "map": {"true": "ON", "false": "OFF"}, "write": {"send": "P {value}"}}},
               responses=[{"match": "P (ON|OFF)", "set": {"mode": "$1"}}])
    reply = sim.handle_command(b"P ON")
    assert (sim._state["mode"], reply) == MAPPED_WRITE


def test_a_hex_setting_write_decodes_to_its_number():
    sim = _sim({"level": {"type": "integer", "write": {"send": "L {value:02X}"}}})
    sim.handle_command(b"L 1F")
    assert sim._state["level"] == 31


# ── The Generic device's inline protocol: the merge ──


MERGED_VARS = ["power", "level", "busy", "nothing", "spare"]

_GENERIC = {"id": "acme_generic", "name": "Acme Generic", "transport": "tcp",
            "state_variables": {"power": {"type": "string", "label": "From file"}},
            "commands": {"keep": {"send": "KEEP"}, "pwr": {"send": "OLD"}},
            "responses": [{"match": "FILE", "set": {"power": "file"}}]}


@pytest.mark.parametrize("config", [None, {}, {"commands": {}, "responses": [], "state_variables": []},
                                    {"delimiter": "\n"}])
def test_a_config_with_no_protocol_leaves_the_definition_as_it_is(config):
    merged, had = _merge_inline_protocol(_GENERIC, config)
    assert merged is _GENERIC and had is False


def test_the_inline_protocol_merges_over_the_file_in_its_order():
    config = {
        "delimiter": "\n", "zone": "1",
        "commands": {"pwr": {"label": "Power", "send": "PWR {value} Z{zone}"}},
        "responses": [
            {"match": r"PWR (\w+)", "state": "power", "group": 1},
            {"mode": "json", "key": "data.level", "state": "level", "type": "integer"},
            {"mode": "contains", "text": "BUSY", "state": "busy", "value": "yes"},
            {"mappings": [{"state": "nothing", "value": 1}]},
            {"mode": "regex", "state": "x"},
        ],
        "state_variables": {"power": {"type": "enum", "values": ["ON", "OFF"]}, "spare": "integer"},
    }
    merged, had = _merge_inline_protocol(_GENERIC, config)
    assert had is True and merged is not _GENERIC
    assert merged["delimiter"] == "\n"
    assert list(merged["commands"]) == ["keep", "pwr"]
    assert merged["commands"]["keep"] == {"send": "KEEP"}
    assert merged["commands"]["pwr"]["send"] == "PWR {value} Z{zone}"
    assert merged["commands"]["pwr"]["params"] == {"value": {"type": "string"}}
    assert merged["responses"][0] == {"match": "FILE", "set": {"power": "file"}}
    assert [sorted(r) for r in merged["responses"][1:]] == [
        ["match", "set"], ["json", "set"], ["match", "set"]]
    assert merged["responses"][1]["set"] == {"power": "$1"}
    assert merged["responses"][2] == {"json": True, "set": {"level": {"key": "data.level", "type": "integer"}}}
    assert merged["responses"][3]["match"] == "BUSY"
    assert list(merged["state_variables"]) == MERGED_VARS
    assert merged["state_variables"]["power"]["type"] == "enum"
    assert _GENERIC["commands"]["pwr"] == {"send": "OLD"}


def test_a_delimiter_that_is_not_text_is_not_merged():
    merged, had = _merge_inline_protocol(_GENERIC, {"delimiter": 13, "commands": {"a": {"send": "A"}}})
    assert had is True and "delimiter" not in merged


def test_only_state_variables_still_count_as_an_inline_protocol():
    merged, had = _merge_inline_protocol(
        _GENERIC, {"state_variables": {"level": "integer"}})
    assert had is True
    assert list(merged["state_variables"]) == ["power", "level"]
    assert merged["responses"] == _GENERIC["responses"]


# ── The inline protocol: response patterns answered as commands ──


def _generic_sim(config, transport="tcp", **overrides) -> YAMLAutoSimulator:
    d = {"id": "acme_generic", "name": "Acme Generic", "transport": transport,
         "state_variables": {}, "commands": {}, "responses": []}
    d.update(overrides)
    return YAMLAutoSimulator("g1", config, driver_def=d)


def test_an_inline_response_rule_is_matched_whole_and_echoed(caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _generic_sim({
            "delimiter": "\r",
            "responses": [{"match": r"VOL (\d+)", "state": "volume", "group": 1, "type": "integer"},
                          {"match": r"BAD (", "state": "x", "group": 1}],
        })
    assert [(p.pattern, s) for p, s in sim._inline_response_handlers] == [
        (r"^VOL (\d+)$", {"volume": "$1"})]
    assert ("openavc.simulator.yaml_auto", "WARNING",
            "acme_generic: skipping bad inline response pattern 'BAD ('") in _records(caplog)
    assert sim.handle_command(b"VOL 12") == b"VOL 12\r"
    assert sim._state["volume"] == 12
    assert sim.handle_command(b"XVOL 12") is None


def test_an_inline_response_rule_with_nothing_to_set_is_not_a_handler():
    sim = _generic_sim({"commands": {"a": {"send": "A"}}},
                       responses=[{"match": "X", "set": {}}, {"match": "", "set": {"a": 1}},
                                  {"set": {"a": 1}}, {"match": "Y", "set": {"y": "lit"}}])
    assert [(p.pattern, s) for p, s in sim._inline_response_handlers] == [("^Y$", {"y": "lit"})]


@pytest.mark.parametrize("src, expected", [("$1", "5"), ("$x", "untouched"), ("$9", "untouched"),
                                           ("literal", "literal"), (7, "7")])
def test_an_inline_response_set_reads_a_group_a_literal_or_skips(src, expected):
    sim = _generic_sim({"commands": {"a": {"send": "A"}}},
                       state_variables={"n": {"type": "string"}},
                       responses=[{"match": r"N (\d+)", "set": {"n": src}}])
    sim._state["n"] = "untouched"
    sim.handle_command(b"N 5")
    assert sim._state["n"] == expected


def test_an_inline_response_capture_is_read_through_its_variables_conversion():
    sim = _generic_sim({"commands": {"a": {"send": "A"}}},
                       state_variables={"gain": {"type": "number", "scale": 0.5}},
                       responses=[{"match": r"G (\d+)", "set": {"gain": "$1"}}])
    sim.handle_command(b"G 10")
    assert sim._state["gain"] == 5.0


# ── The inline protocol: an HTTP device's JSON status poll ──


_JSON_CONFIG = {
    "commands": {
        "status": {"label": "Status", "method": "get", "path": "/api/{zone}/status", "poll": True},
        "root": {"label": "Root", "poll": True},
        "posted": {"label": "Posted", "method": "POST", "path": "/p", "poll": True},
        "lined": {"label": "Lined", "send": "S", "poll": True},
        "nopoll": {"label": "Plain", "path": "/plain"},
    },
    "responses": [
        {"mode": "json", "key": "data.power", "state": "power"},
        {"mode": "json", "key": "data.level", "state": "level", "type": "integer"},
        {"mode": "json", "key": "top", "state": "top"},
        {"json": True, "set": {"flag": {"key": "flag", "contains": "yes"}}},
        {"mode": "json", "key": "..", "state": "dots"},
        {"json": True, "set": {"nokey": {"type": "string"}}},
    ],
}


def test_an_inline_http_device_answers_its_poll_commands_with_one_json_body():
    sim = _generic_sim(_JSON_CONFIG, "http")
    assert [p.pattern for p in sim._inline_json_polls] == [
        r"^GET\ /api/[^\s|]+/status(?:\|.*)?$",
        r"^GET\ /(?:\|.*)?$",
        r"^POST\ /p(?:\|.*)?$",
    ]
    assert [(m["state"], m["key"]) for m in sim._inline_json_fields] == [
        ("power", "data.power"), ("level", "data.level"), ("top", "top"), ("dots", ".."),
        ("nokey", "nokey")]
    sim._state.update({"power": "on", "level": 3, "top": None, "dots": 1})
    body = sim.handle_command(b"GET /api/7/status|ignored")
    assert body == b'{"data": {"power": "on", "level": 3}, "nokey": ""}'
    assert sim.handle_command(b"GET /api/7 8/status") is None
    assert sim.handle_command(b"GET /|") == body


def test_a_json_body_replaces_a_value_in_the_way_of_a_nested_key():
    sim = _generic_sim({"commands": {"s": {"label": "S", "path": "/s", "poll": True}},
                        "responses": [{"mode": "json", "key": "a", "state": "a"},
                                      {"mode": "json", "key": "a.b", "state": "b"},
                                      {"mode": "json", "key": "a.c", "state": "c"}]}, "http")
    sim._state.update({"a": 1, "b": 2, "c": 3})
    assert json.loads(sim._inline_json_reply()) == {"a": {"b": 2, "c": 3}}


def test_no_json_field_means_no_poll_is_answered():
    sim = _generic_sim({"commands": {"s": {"label": "S", "path": "/s", "poll": True}},
                        "responses": [{"json": True, "set": {"flag": {"key": "flag", "contains": "x"}}}]},
                       "http")
    assert sim._inline_json_polls == [] and sim._inline_json_fields == []


def test_a_file_json_rule_on_an_inline_http_device_is_a_field_too():
    sim = _generic_sim({"commands": {"s": {"label": "S", "path": "/s", "poll": True}}}, "http",
                       state_variables={"lvl": {"type": "integer"}},
                       responses=[{"json": True, "mappings": [{"state": "lvl", "key": "v"}]},
                                  {"json": False, "match": "Z", "set": {"z": 1}}])
    assert [(m["state"], m["key"]) for m in sim._inline_json_fields] == [("lvl", "v")]


@pytest.mark.parametrize("transport", ["tcp", "udp"])
def test_the_json_poll_is_an_http_devices_only(transport):
    sim = _generic_sim(_JSON_CONFIG, transport)
    assert sim._inline_json_polls == [] and sim._inline_json_fields == []


def test_a_file_driver_on_http_never_gets_a_json_poll():
    sim = _generic_sim({}, "http", commands={"s": {"path": "/s", "poll": True}},
                       state_variables={"lvl": {"type": "integer"}},
                       responses=[{"json": True, "mappings": [{"state": "lvl", "key": "v"}]}])
    assert sim._inline_protocol is False
    assert sim._inline_json_polls == [] and sim._inline_json_fields == []


@pytest.mark.parametrize("transport, section, pushes", [
    ("tcp", None, True), ("http", None, False), ("http", {"push_state": True}, True),
    ("tcp", {"push_state": False}, True), ("udp", None, True),
])
def test_an_inline_device_pushes_unless_it_is_http(transport, section, pushes):
    overrides = {"simulator": section} if section is not None else {}
    sim = _generic_sim({"commands": {"a": {"send": "A"}}}, transport, **overrides)
    assert bool(sim._push_state) is pushes


def test_a_file_driver_pushes_only_when_its_section_says_so():
    d = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp", "state_variables": {},
         "commands": {"a": {"send": "A"}}, "responses": []}
    assert YAMLAutoSimulator("w1", {}, driver_def=d)._push_state is False


# ── The command handler ──


def test_a_command_handler_fills_its_tables_and_compares_by_identity():
    import re
    a = CommandHandler("n", re.compile("x"), {}, None)
    assert (a.group_bases, a.group_conversions, a.group_value_maps, a.child_state_keys) == ({}, {}, {}, set())
    assert (a.child_type, a.child_id_group, a.child_wire_map, a.response_is_child) == (None, 0, None, False)
    b = CommandHandler("n", re.compile("x"), {}, None, None, None, 0, None, None, None, False, None)
    assert (b.group_bases, b.group_conversions, b.group_value_maps, b.child_state_keys) == ({}, {}, {}, set())
    c = CommandHandler("n", re.compile("x"), {"v": 1}, "v", {1: 16}, "out", 2, {1: {"scale": 2}},
                       {"A": "1"}, {"v"}, True, {1: {"true": "ON"}})
    assert (c.group_bases, c.child_type, c.child_id_group, c.group_conversions, c.child_wire_map,
            c.child_state_keys, c.response_is_child, c.group_value_maps) == (
        {1: 16}, "out", 2, {1: {"scale": 2}}, {"A": "1"}, {"v"}, True, {1: {"true": "ON"}})
    assert a != CommandHandler("n", re.compile("x"), {}, None)
    assert c.target_key("v", "3") == "out.3.v"
    assert c.target_key("w", "3") == "w"
    assert c.target_key("v", None) == "v"


@pytest.mark.parametrize("raw, var_def, expected", [
    ("ON", {"type": "boolean"}, "true"),
    ("1F", {"type": "integer"}, 31),
    ("1F", {"type": "string"}, "1F"),
    ("zz", {"type": "integer"}, "zz"),
    ("1F", {}, "1F"),
    ("4", {"type": "number"}, 2.0),
])
def test_a_capture_reads_a_word_then_a_base_then_a_conversion(raw, var_def, expected):
    import re
    h = CommandHandler("n", re.compile("x"), {}, None, group_bases={1: 16, 2: 10},
                       group_conversions={2: {"scale": 0.5}},
                       group_value_maps={1: {"true": "ON"}})
    group = 2 if var_def.get("type") == "number" else 1
    assert h.read_capture(group, raw, var_def) == expected


# ── The order construction builds things in ──


_BUILD_ORDER = [
    "_inline_protocol", "SIMULATOR_INFO", "device_id", "config", "_state", "_error_modes",
    "_active_errors", "_delays", "_state_machines", "_change_listeners", "_protocol_log", "_port",
    "_running", "_network_layer", "_capacity", "_single_client", "_child_entities", "_server",
    "_clients", "_links", "_delimiter", "_line_mode", "_udp_transport", "_last_client_addr",
    "_dgram_queue", "_dgram_queued_bytes", "_dgram_worker", "_dgram_next_start", "_app", "_runner",
    "_site", "_sse_clients", "_tls_files", "_last_peer_ip", "_send_frame", "_driver_def", "_is_udp",
    "_is_osc", "_is_http", "_udp_side_commands", "_side_receiver", "_command_handlers",
    "_query_handlers", "_state_responses", "_inline_response_handlers", "_inline_json_polls",
    "_inline_json_fields", "_osc_address_handlers", "_osc_script_handlers", "_handling_command",
    "_handling_osc", "_osc_state_to_address", "_child_roster", "_child_state_responses",
    "_child_list_answers", "_child_query_handlers", "_push_state", "_push_multicast", "_mcast_sock",
    "sse_paths", "sse_session", "_push_tcp", "_push_tcp_subscribers", "_push_tcp_tasks",
    "_push_http", "_http_push_callbacks", "_push_udp_listener",
]


def test_construction_creates_its_attributes_in_one_order():
    assert list(vars(_sim({"level": {"type": "integer", "write": {"send": "L {value}"}}}))) == _BUILD_ORDER


def test_a_section_adds_its_own_attributes_where_it_is_read():
    sim = _sim({}, simulator={"command_handlers": [{"receive": "PING", "respond": "PONG"}],
                              "notifications": {"level": "N {value}"}})
    names = list(vars(sim))
    assert names[names.index("_osc_state_to_address") + 1:names.index("_child_roster")] == [
        "_explicit_handlers", "_script_handlers", "_notification_map"]


def test_a_section_seeds_children_before_the_roster_is_read_and_its_own_sign_in_mode_wins():
    sim = _sim({}, child_entity_types={"output": {
        "label": "Output", "state_variables": {"input": {"type": "integer"}},
        "instances": {"count": 2}}},
        polling={"interval": 5, "queries": [{"send": "Q {child_id}", "each_child": "output",
                                             "query_for": "input"}]},
        auth={"type": "telnet_login", "password_prompt": "Password:"},
        simulator={"initial_state": {"output.5.input": 3},
                   "error_modes": {"sign_in_required": {"behavior": "x", "description": "Mine"}}})
    assert sim._child_roster == {"output": ["1", "2", "5"]}
    assert list(sim._state) == ["level", "gain", "mode", "output.5.input",
                                "output.1.input", "output.2.input"]
    assert [h.child_id for h in sim._child_query_handlers] == ["1", "2", "5"]
    assert sim._error_modes["sign_in_required"] == {"behavior": "x", "description": "Mine"}


def test_a_send_frame_turns_line_mode_off_and_a_side_send_listens_beside_a_stream_only():
    framed = _sim({}, send_frame={"type": "length_prefix", "header": "!1", "length_bytes": 1})
    assert framed._line_mode is (framed._send_frame is None)
    side = {"wake": {"udp": {"magic_packet": True}}}
    assert _sim({}, commands=side)._side_receiver is True
    assert _sim({}, commands=side, transport="udp")._side_receiver is False
    assert _sim({}, commands=side, transport="osc")._side_receiver is False


@pytest.mark.parametrize("transport, ssl, tls", [("http", True, True), ("tcp", True, False),
                                                 ("http", False, False)])
def test_tls_is_offered_only_to_an_https_device(transport, ssl, tls):
    sim = _sim({}, {"ssl": ssl}, transport=transport)
    assert sim.SIMULATOR_INFO.get("tls") is (True if tls else None)
