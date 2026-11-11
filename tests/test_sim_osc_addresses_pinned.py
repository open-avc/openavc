"""Pins for how the auto-simulator answers an OSC device's addresses.

The console's own addresses, a script handler from the ``simulator:`` section,
a message to a response rule's address with args (it sets the variables the
mappings name) and without (it reports them), and a change pushed to the peer:
the value map each way, the variable's conversion, the type tag each value
goes out under. Recorded from the code as it is, on an invented device.
"""

from __future__ import annotations

import logging

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.osc_codec import osc_decode_message


def _sim(responses=None, state_variables=None, simulator=None) -> YAMLAutoSimulator:
    d = {
        "id": "acme_mixer",
        "name": "Acme Mixer",
        "transport": "osc",
        "state_variables": state_variables or {},
        "commands": {},
        "responses": responses or [],
    }
    if simulator:
        d["simulator"] = simulator
    return YAMLAutoSimulator("m1", {}, driver_def=d)


def _typed(pairs):
    """Each (tag, value) with the value's type, so 3 and 3.0 differ."""
    return [(tag, value, type(value).__name__) for tag, value in pairs]


_FADER = {
    "address": "/ch/01/fader",
    "mappings": [{"arg": 0, "state": "fader", "type": "float"}],
}
_MUTE = {
    "address": "/ch/01/on",
    "mappings": [{"arg": 0, "state": "mute", "type": "integer",
                  "map": {"0": True, "1": False}}],
}


# ── the console's own addresses ──


def test_the_console_addresses_answer_before_any_rule():
    sim = _sim(responses=[{"address": "/*", "mappings": [{"state": "x"}]}])
    assert sim.handle_message("/xremote", [("i", 1)]) == [("/xremote", [])]
    assert sim.handle_message("/info", []) == [("/info", [
        ("s", "V2.07"), ("s", "osc-server"), ("s", "Simulator"), ("s", "1.0.0"),
    ])]
    sim._state["console_model"] = "X1"
    sim._state["firmware_version"] = "4.2"
    assert sim.handle_message("/info", [])[0][1][2:] == [("s", "X1"), ("s", "4.2")]
    assert sim.handle_message("/status", []) == [("/status", [
        ("s", "active"), ("s", "127.0.0.1"), ("s", "osc-server"),
    ])]


def test_the_action_and_show_namespaces_echo():
    sim = _sim()
    assert sim.handle_message("/-action/go", [("i", 3)]) == [("/-action/go", [("i", 3)])]
    assert sim.handle_message("/-show/cue", []) == [("/-show/cue", [])]
    assert sim.handle_message("/-other/x", []) is None


# ── script handlers ──


def test_a_script_handler_wins_over_a_rule_and_its_args_are_typed():
    sim = _sim(
        responses=[_FADER],
        simulator={"command_handlers": [
            {"address": "/ch/*/fader", "handler": "respond(address, [0.5, 'x'])"},
        ]},
    )
    assert sim.handle_message("/ch/01/fader", [("f", 0.1)]) == [
        ("/ch/01/fader", [("f", 0.5), ("s", "x")]),
    ]
    assert "fader" not in sim._state


def test_a_script_handler_that_raises_or_says_nothing_answers_none(caplog):
    sim = _sim(simulator={"command_handlers": [
        {"address": "/boom", "handler": "raise ValueError('x')"},
        {"address": "/quiet", "handler": "state['q'] = args[0][1]"},
    ]})
    with caplog.at_level(logging.ERROR, logger="openavc.simulator.yaml_auto"):
        assert sim.handle_message("/boom", []) is None
    assert any("OSC script handler error for /boom" in r.getMessage() for r in caplog.records)
    assert sim.handle_message("/quiet", [("i", 7)]) is None
    assert sim._state["q"] == 7


# ── a message with args sets ──


def test_a_set_reads_each_mapping_arg_through_its_map_else_the_conversion():
    sim = _sim(
        responses=[_MUTE, {
            "address": "/ch/01/level",
            "mappings": [
                {"arg": 0, "state": "level", "type": "float"},
                {"arg": 1, "state": "trim", "type": "integer"},
                {"arg": 5, "state": "unused"},
                {"arg": 0},
            ],
        }],
        state_variables={
            "mute": {"type": "boolean"},
            "level": {"type": "integer", "scale": 100},
            "trim": {"type": "integer", "min": -10, "max": 10},
        },
    )
    assert sim.handle_message("/ch/01/on", [("i", 0)]) == [("/ch/01/on", [("i", 0)])]
    assert sim._state["mute"] is True
    assert sim.handle_message("/ch/01/level", [("f", 0.5), ("i", 40)]) == [
        ("/ch/01/level", [("f", 0.5), ("i", 40)]),
    ]
    assert sim._state["level"] == 50
    assert sim._state["trim"] == 10
    assert "unused" not in sim._state


def test_a_set_value_the_map_lacks_goes_through_the_conversion():
    sim = _sim(
        responses=[{"address": "/a", "mappings": [
            {"arg": 0, "state": "lvl", "type": "integer", "map": {"9": 1}},
        ]}],
        state_variables={"lvl": {"type": "integer", "scale": 2}},
    )
    sim.handle_message("/a", [("i", 9)])
    assert sim._state["lvl"] == 1
    sim.handle_message("/a", [("i", 6)])
    assert sim._state["lvl"] == 12


# ── a message without args reports ──


@pytest.mark.parametrize("value_type, value, expected", [
    ("float", 0.25, [("f", 0.25, "float")]),
    ("number", 2, [("f", 2, "int")]),
    ("integer", 3, [("i", 3, "int")]),
    ("boolean", True, [("i", True, "bool")]),
    ("string", "abc", [("s", "abc", "str")]),
    ("other", 1.5, [("f", 1.5, "float")]),
])
def test_a_query_answers_in_the_mapping_type_with_the_value_uncast(value_type, value, expected):
    sim = _sim(responses=[{"address": "/v", "mappings": [
        {"arg": 0, "state": "v", "type": value_type},
    ]}])
    sim._state["v"] = value
    ((address, args),) = sim.handle_message("/v", [])
    assert address == "/v"
    assert _typed(args) == expected


def test_a_query_of_an_unset_variable_answers_zero_in_the_device_numbers():
    sim = _sim(
        responses=[{"address": "/v", "mappings": [{"state": "v", "type": "float"}]}],
        state_variables={"v": {"type": "number", "scale": 0.5, "offset": 1}},
    )
    sim._state.pop("v", None)
    ((_, args),) = sim.handle_message("/v", [])
    assert _typed(args) == [("f", -2, "int")]


def test_a_query_reverse_maps_and_casts_to_the_tag():
    sim = _sim(responses=[_MUTE], state_variables={"mute": {"type": "boolean"}})
    sim._state["mute"] = True
    assert _typed(sim.handle_message("/ch/01/on", [])[0][1]) == [("i", 0, "int")]
    sim._state["mute"] = "maybe"
    assert _typed(sim.handle_message("/ch/01/on", [])[0][1]) == [("i", "maybe", "str")]


@pytest.mark.parametrize("value_type, expected", [
    ("float", [("f", 1.0, "float")]),
    ("string", [("s", "1", "str")]),
])
def test_a_reverse_mapped_query_casts_float_and_keeps_text(value_type, expected):
    sim = _sim(responses=[{"address": "/m", "mappings": [
        {"state": "m", "type": value_type, "map": {"1": "on", "0": "off"}},
    ]}])
    sim._state["m"] = "ON"
    assert _typed(sim.handle_message("/m", [])[0][1]) == expected


def test_a_query_reads_the_reverse_map_of_the_last_address_naming_the_variable():
    sim = _sim(responses=[
        {"address": "/a", "mappings": [{"state": "k", "type": "string",
                                         "map": {"A1": "on"}}]},
        {"address": "/b", "mappings": [{"state": "k", "type": "integer",
                                         "map": {"7": "on"}}]},
    ])
    sim._state["k"] = "on"
    assert _typed(sim.handle_message("/a", [])[0][1]) == [("s", "7", "str")]
    assert _typed(sim.handle_message("/b", [])[0][1]) == [("i", 7, "int")]


def test_a_query_with_no_named_variable_answers_the_bare_address():
    sim = _sim(responses=[{"address": "/n", "mappings": [{"arg": 0, "type": "float"}]}])
    assert sim.handle_message("/n", []) == [("/n", [])]


def test_the_first_matching_address_answers_and_an_unknown_one_is_none(caplog):
    sim = _sim(responses=[
        {"address": "/ch/*/x", "mappings": [{"state": "first", "type": "integer"}]},
        {"address": "/ch/01/x", "mappings": [{"state": "second", "type": "integer"}]},
        {"address": "/empty", "mappings": []},
        {"mappings": [{"state": "no_address"}]},
    ])
    sim._state.update(first=1, second=2)
    assert sim.handle_message("/ch/01/x", []) == [("/ch/01/x", [("i", 1)])]
    with caplog.at_level(logging.DEBUG, logger="openavc.simulator.yaml_auto"):
        assert sim.handle_message("/empty", []) is None
    assert any(
        r.getMessage() == "m1: unmatched OSC address: /empty" for r in caplog.records
    )


def test_the_address_tables_follow_the_rules(caplog):
    with caplog.at_level(logging.INFO, logger="openavc.simulator.yaml_auto"):
        sim = _sim(responses=[
            _FADER, _MUTE,
            {"address": "/empty", "mappings": []},
            {"address": "/c", "mappings": [{"arg": 2, "state": "c"}, {"arg": 1}]},
        ])
    assert [a for a, _ in sim._osc_address_handlers] == ["/ch/01/fader", "/ch/01/on", "/c"]
    assert sim._osc_state_to_address == {
        "fader": ("/ch/01/fader", 0, "f", None),
        "mute": ("/ch/01/on", 0, "i", {"true": "0", "false": "1"}),
        "c": ("/c", 2, "f", None),
    }
    assert any(
        r.getMessage() == "Auto-gen OSC simulator for acme_mixer: 3 address handlers"
        for r in caplog.records
    )


# ── a change pushed to the peer ──


def _pushes(sim, monkeypatch) -> list:
    sent: list = []
    monkeypatch.setattr(sim, "push_datagram", lambda data: sent.append(osc_decode_message(data)))
    return sent


@pytest.mark.parametrize("tag_type, value, expected", [
    ("float", 3, ("f", 3.0)),
    ("integer", True, ("i", 1)),
    ("integer", 2.9, ("i", 2)),
    ("string", 12, ("s", "12")),
])
def test_a_push_casts_the_device_number_to_its_tag(tag_type, value, expected, monkeypatch):
    sim = _sim(responses=[{"address": "/p", "mappings": [{"state": "p", "type": tag_type}]}])
    sent = _pushes(sim, monkeypatch)
    sim._push_osc_state("p", value)
    assert sent == [("/p", [expected])]


def test_a_push_reverse_maps_and_a_value_the_map_lacks_sends_nothing(monkeypatch):
    sim = _sim(responses=[
        _MUTE,
        {"address": "/s", "mappings": [{"state": "s", "type": "string", "map": {"A": "x"}}]},
        {"address": "/f", "mappings": [{"state": "f", "type": "float", "map": {"2": "y"}}]},
    ])
    sent = _pushes(sim, monkeypatch)
    sim._push_osc_state("mute", False)
    sim._push_osc_state("mute", "nope")
    sim._push_osc_state("s", "X")
    sim._push_osc_state("f", "y")
    assert sent == [("/ch/01/on", [("i", 1)]), ("/s", [("s", "A")]), ("/f", [("f", 2.0)])]


def test_a_push_writes_the_device_number(monkeypatch):
    sim = _sim(
        responses=[_FADER],
        state_variables={"fader": {"type": "number", "scale": 100}},
    )
    sent = _pushes(sim, monkeypatch)
    sim._push_osc_state("fader", 300)
    assert sent == [("/ch/01/fader", [("f", 3.0)])]


def test_a_changed_value_is_pushed_only_to_a_heard_peer_outside_a_message(monkeypatch):
    sim = _sim(
        responses=[_FADER, {"address": "/x", "mappings": [{"state": "other_x"}]}],
        simulator={"push_state": True},
    )
    sent = _pushes(sim, monkeypatch)
    heard = {"value": False}
    monkeypatch.setattr(sim, "_peer_heard", lambda: heard["value"])
    sim.set_state("fader", 0.1)
    heard["value"] = True
    sim.set_state("fader", 0.2)
    sim.set_state("unmapped", 1)
    sim._handling_osc = True
    sim.set_state("fader", 0.3)
    sim._handling_osc = False
    assert sent == [("/ch/01/fader", [("f", pytest.approx(0.2))])]
    assert sim._state["fader"] == 0.3


def test_a_query_mapping_with_no_type_answers_as_a_float():
    sim = _sim(responses=[{"address": "/u", "mappings": [{"state": "u"}]}])
    sim._state["u"] = 2
    assert _typed(sim.handle_message("/u", [])[0][1]) == [("f", 2, "int")]


def test_a_script_handler_typed_argument_of_the_wrong_length_is_refused(caplog):
    sim = _sim(simulator={"command_handlers": [
        {"address": "/three", "handler": "respond(address, [('i', 1, 2)])"},
        {"address": "/one", "handler": "respond(address, [('i',)])"},
    ]})
    with caplog.at_level(logging.ERROR, logger="openavc.simulator.yaml_auto"):
        assert sim.handle_message("/three", []) is None
        assert sim.handle_message("/one", []) is None
    errors = [r.exc_info[0].__name__ for r in caplog.records if r.exc_info]
    assert errors == ["ValueError", "ValueError"]
