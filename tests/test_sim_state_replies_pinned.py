"""Pins for how the auto-simulator writes a state value back on the wire.

The reply (or push) text for a flat state variable, for one child's variable,
and for a variable a ``child_list`` rule spreads across children: the value
map, the templates every rule that writes the variable gives, the first one
whose own rule reads the value back, the device's own numbers, and the forms a
boolean can take. Recorded from the code as it is, on an invented device.
"""

from __future__ import annotations

import re

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.simulator.yaml_auto import _first_read_back, _ref_number


def _sim(**overrides) -> YAMLAutoSimulator:
    d = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "delimiter": "\r",
        "state_variables": {},
        "commands": {},
        "responses": [],
    }
    d.update(overrides)
    return YAMLAutoSimulator("w1", {}, driver_def=d)


# ── flat variables ──


def test_a_value_map_entry_wins_and_a_boolean_reads_lowercase():
    sim = _sim(
        state_variables={"mute": {"type": "boolean"}},
        responses=[
            {"match": "MUTE1", "set": {"mute": True}},
            {"match": "MUTE0", "set": {"mute": False}},
        ],
    )
    sr = sim._state_responses["mute"]
    assert sr.value_map == {"true": "MUTE1", "false": "MUTE0"}
    assert sr.template is None
    assert sim._format_state_reply("mute", True) == "MUTE1"
    assert sim._format_state_reply("mute", False) == "MUTE0"


def test_with_no_template_a_value_the_map_lacks_goes_out_as_its_wire_form():
    sim = _sim(
        state_variables={"level": {"type": "integer", "scale": 2}},
        responses=[{"match": "LVLMAX", "set": {"level": 100}}],
    )
    assert sim._state_responses["level"].value_map == {"100": "LVLMAX"}
    assert sim._format_state_reply("level", 100) == "LVLMAX"
    # 7 is no map key: the fallback writes the device's own number (7 / 2,
    # rounded for an integer variable)
    assert sim._format_state_reply("level", 7) == "4"
    assert sim._format_state_reply("level", "x") == "x"


def test_the_first_rule_that_reads_the_value_back_is_the_one_sent():
    sim = _sim(
        state_variables={"runtime": {"type": "integer"}},
        responses=[
            {"match": r"RT (6553[2-5])", "set": {"runtime": "$1"}},
            {"match": r"RT (\d{5})", "set": {"runtime": "$1"}},
        ],
    )
    sr = sim._state_responses["runtime"]
    assert sr.template == "RT {value}"
    assert [t for t, _rule in sr.alternates] == ["RT {value:05d}"]
    assert sim._format_state_reply("runtime", 65533) == "RT 65533"
    assert sim._format_state_reply("runtime", 400) == "RT 00400"
    # read back by neither rule: the first rendering, as written
    assert sim._format_state_reply("runtime", 1234567) == "RT 1234567"


def test_a_boolean_goes_out_in_the_first_form_its_rule_reads():
    sim = _sim(
        state_variables={"power": {"type": "boolean"}},
        responses=[{"match": r"PWR=(true|false)", "set": {"power": "$1"}}],
    )
    assert sim._format_state_reply("power", True) == "PWR=true"
    assert sim._format_state_reply("power", False) == "PWR=false"
    sim2 = _sim(
        state_variables={"power": {"type": "boolean"}},
        responses=[{"match": r"PWR=([01])", "set": {"power": "$1"}}],
    )
    assert sim2._format_state_reply("power", True) == "PWR=1"
    assert sim2._format_state_reply("power", False) == "PWR=0"
    sim3 = _sim(
        state_variables={"power": {"type": "boolean"}},
        responses=[{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
    )
    assert sim3._format_state_reply("power", True) == "PWR=True"


def test_a_scaled_variable_replies_in_the_device_numbers():
    sim = _sim(
        state_variables={"gain": {"type": "number", "scale": 0.5, "offset": -10}},
        responses=[{"match": r"G(\d+)", "set": {"gain": "$1"}}],
    )
    assert sim._state_responses["gain"].conversion == {"scale": 0.5, "offset": -10}
    assert sim._format_state_reply("gain", 0) == "G20"
    assert sim._format_state_reply("gain", 5) == "G30"


def test_a_mapped_group_is_reversed_into_one_reply_per_value():
    sim = _sim(
        state_variables={"input": {"type": "enum", "values": ["hdmi", "vga"]}},
        responses=[{
            "match": r"IN (\d+)",
            "mappings": [{"state": "input", "group": 1, "map": {"1": "hdmi", "2": "vga"}}],
        }],
    )
    sr = sim._state_responses["input"]
    assert sr.value_map == {"hdmi": "IN 1", "vga": "IN 2"}
    assert sr.template == "IN {value}"
    assert sim._format_state_reply("input", "vga") == "IN 2"
    assert sim._format_state_reply("input", "other") == "IN other"


def test_an_unknown_key_has_no_reply_and_a_script_reply_reads_state():
    sim = _sim(
        state_variables={"level": {"type": "integer"}},
        responses=[{"match": r"L(\d{3})", "set": {"level": "$1"}}],
    )
    assert sim._format_state_reply("nope", 1) is None
    sim._state["level"] = 7
    assert sim._script_reply("level") == "L007"
    assert sim._script_reply("nope") is None


# ── the shared read-back choice ──


def test_first_read_back_takes_the_first_line_its_rule_reads_exactly():
    rule = (re.compile(r"V(\d+)"), 1)
    lines = [("V1x", "2", rule), ("V2", "2", rule), ("V3", "3", rule)]
    assert _first_read_back(lines) == "V2"


def test_first_read_back_with_no_reader_sends_the_first_line():
    rule = (re.compile(r"V(\d+)"), 1)
    assert _first_read_back([("A", "1", rule), ("B", "2", rule)]) == "A"


def test_first_read_back_a_first_line_with_no_rule_is_sent_at_once():
    rule = (re.compile(r"V(\d+)"), 1)
    assert _first_read_back([("X", "1", None), ("V1", "1", rule)]) == "X"


def test_first_read_back_a_later_line_with_no_rule_is_skipped():
    rule = (re.compile(r"V(\d+)"), 1)
    assert _first_read_back([("Q", "9", rule), ("X", "1", None), ("V1", "1", rule)]) == "V1"
    assert _first_read_back([("Q", "9", rule), ("X", "1", None)]) == "Q"


@pytest.mark.parametrize(
    "ref, expected",
    [("$2", 2), (2, 2), ("$0", None), (0, None), (-1, None), (True, None),
     ("2", None), ("$x", None), (None, None), (2.0, None)],
)
def test_ref_number(ref, expected):
    assert _ref_number(ref) == expected


# ── child variables ──


def _matrix(**overrides) -> YAMLAutoSimulator:
    d = {
        "child_entity_types": {
            "output": {
                "label": "Output",
                "state_variables": {
                    "input": {"type": "integer"},
                    "mute": {"type": "boolean"},
                    "level": {"type": "number", "scale": 0.5},
                },
                "instances": {"count": 3},
            }
        },
        "responses": [],
    }
    d.update(overrides)
    return _sim(**d)


def test_a_child_reply_carries_the_child_id_and_value():
    sim = _matrix(responses=[{
        "match": r"Out(\d+) In(\d+) Vid",
        "child_set": [{"type": "output", "id": "$1", "state": {"input": "$2"}}],
    }])
    sr = sim._child_state_responses[("output", "input")]
    assert sr.template == "Out{child_id} In{value} Vid"
    assert sr.id_wire_map is None
    assert sim._format_state_reply("output.2.input", 5) == "Out2 In5 Vid"
    # a child the roster lacks has no reply
    assert sim._format_state_reply("output.9.input", 5) is None


def test_a_child_id_map_puts_the_device_id_back_on_the_wire():
    sim = _matrix(responses=[{
        "match": r"O(\d) M(\d)",
        "child_set": [{"type": "output", "id": {"group": 1, "map": {"0": "1", "1": "2"}},
                       "state": {"mute": {"group": 2, "map": {"1": True, "0": False}}}}],
    }])
    sr = sim._child_state_responses[("output", "mute")]
    assert sr.value_map == {"true": "O{child_id} M1", "false": "O{child_id} M0"}
    assert sr.id_wire_map == {"1": "0", "2": "1"}
    assert sr.format("2", True) == "O1 M1"
    assert sr.format("3", False) == "O3 M0"


def test_a_child_value_nothing_represents_is_none():
    sim = _matrix(responses=[{
        "match": r"O(\d) MUTED",
        "child_set": [{"type": "output", "id": "$1", "state": {"mute": True}}],
    }])
    sr = sim._child_state_responses[("output", "mute")]
    assert sr.value_map == {"true": "O{child_id} MUTED"}
    assert sr.template is None
    assert sr.format("1", True) == "O1 MUTED"
    assert sr.format("1", False) is None


def test_a_child_variable_with_two_rules_answers_in_the_one_that_reads_it():
    sim = _matrix(responses=[
        {"match": r"Out(\d+) Lvl(6553[2-5])",
         "child_set": [{"type": "output", "id": "$1", "state": {"level": "$2"}}]},
        {"match": r"Out(\d+) Lvl(\d{5})",
         "child_set": [{"type": "output", "id": {"group": 1, "map": {"0": "1"}},
                        "state": {"level": "$2"}}]},
    ])
    sr = sim._child_state_responses[("output", "level")]
    assert sr.template == "Out{child_id} Lvl{value}"
    assert [(t, m) for t, _r, m in sr.alternates] == [("Out{child_id} Lvl{value:05d}", {"1": "0"})]
    # 200 is the device's 400 (scale 0.5); only the second rule reads it,
    # with its own id map
    assert sr.format("1", 200) == "Out0 Lvl00400"
    assert sr.format("2", 32766.5) == "Out2 Lvl65533"


def test_a_child_template_and_a_static_value_share_one_table():
    sim = _matrix(responses=[
        {"match": r"Out(\d+) In(\d+) All",
         "child_set": [{"type": "output", "id": "$1",
                        "state": {"input": "$2", "mute": {"value": False}}}]},
    ])
    assert sim._child_state_responses[("output", "input")].template == "Out{child_id} In{value} All"
    # the static value's reply fills the other group with the shortest text
    # its pattern takes
    assert sim._child_state_responses[("output", "mute")].value_map == {"false": "Out{child_id} In0 All"}


# ── list replies ──


def test_a_list_item_names_its_child_and_uses_the_device_word():
    sim = _matrix(
        responses=[{
            "match": r"Vgp0\*Out(\d+) (.*)Vid",
            "child_list": {
                "type": "output", "element": r"(on|off)>(\d+)", "group": 2,
                "state": {"mute": {"group": 1, "map": {"on": True, "off": False}}},
                "id": {"group": 2, "map": {"0": "1", "1": "2", "2": "3"}},
                "separator": ",",
            },
        }],
        polling={"queries": [{"send": "LIST", "query_for": "output.mute"}]},
    )
    answer = sim._child_list_answers[("output", "mute")]
    # items name their child, so the reply has no start slot: group 1 is
    # the shortest text its pattern takes
    assert answer.outer == "Vgp0*Out0 {list}Vid"
    assert answer.value_words == {"true": "on", "false": "off"}
    assert answer.id_wire_map == {"1": "0", "2": "1", "3": "2"}
    assert answer.render_item("2", True) == "on>1"
    assert answer.render_item("9", "maybe") == "maybe>9"
    sim._state["output.2.mute"] = True
    assert sim.handle_command(b"LIST") == sim.handle_command(b"LIST")
    reply = sim.handle_command(b"LIST")
    assert reply == b"Vgp0*Out0 off>0,on>1,off>2Vid\r"


def test_a_list_by_place_renders_each_value_in_the_device_numbers():
    sim = _matrix(
        responses=[{
            "match": r"Vgp0\*Out(\d+) (.*)Vid",
            "child_list": {"type": "output", "element": r"(\d+)",
                           "state": {"level": "$1"}, "separator": " ", "group": "$2",
                           "start": "$1"},
        }],
        polling={"queries": [{"send": "LVLS", "query_for": "output.level"}]},
    )
    answer = sim._child_list_answers[("output", "level")]
    assert answer.by_place is True
    assert answer.value_words is None and answer.id_wire_map is None
    sim._state["output.1.level"] = 2
    sim._state["output.3.level"] = 7.5
    assert sim.handle_command(b"LVLS") == b"Vgp0*Out1 4 0 15Vid\r"
    assert answer.render(5, ["a", "b"]) == "Vgp0*Out5 a bVid"


# ── what the round's deliberate breaks found unwatched ──


def test_a_child_template_from_a_rule_with_an_id_map_answers_with_the_device_id():
    sim = _matrix(responses=[{
        "match": r"Out(\d) In(\d+)",
        "child_set": [{"type": "output", "id": {"group": 1, "map": {"0": "1", "1": "2"}},
                       "state": {"input": "$2"}}],
    }])
    sr = sim._child_state_responses[("output", "input")]
    assert sr.template == "Out{child_id} In{value}"
    assert sr.id_wire_map == {"1": "0", "2": "1"}
    assert sr.format("2", 7) == "Out1 In7"
    assert sr.format("3", 7) == "Out3 In7"


def test_a_value_two_rules_map_keeps_the_first_reply_and_a_literal_rule_the_last():
    sim = _sim(
        state_variables={"input": {"type": "enum", "values": ["hdmi", "vga"]}},
        responses=[
            {"match": r"IN (\d+)", "mappings": [
                {"state": "input", "group": 1, "map": {"1": "hdmi", "01": "hdmi"}}]},
            {"match": r"SRC (\w+)", "mappings": [{"state": "input", "group": 1, "map": {"H": "hdmi"}}]},
            {"match": "VGA", "set": {"input": "vga"}},
            {"match": "ANALOG", "set": {"input": "vga"}},
        ],
    )
    assert sim._state_responses["input"].value_map == {"hdmi": "IN 1", "vga": "ANALOG"}
    sim = _sim(
        state_variables={"input": {"type": "enum", "values": ["hdmi"]}},
        responses=[
            {"match": "HDMI", "set": {"input": "hdmi"}},
            {"match": r"IN (\d+)", "mappings": [{"state": "input", "group": 1, "map": {"1": "hdmi"}}]},
        ],
    )
    assert sim._state_responses["input"].value_map == {"hdmi": "HDMI"}


def test_a_capture_reference_on_a_pattern_with_no_group_is_a_literal_reply():
    sim = _sim(
        state_variables={"level": {"type": "integer"}},
        responses=[{"match": "PING", "set": {"level": "$1"}}],
    )
    sr = sim._state_responses["level"]
    assert sr.value_map == {"$1": "PING"}
    assert sr.template is None
