"""Pins for what the auto-simulator does when a line matches a command: the
state machines it drives first, the child a child-addressed command names
(its id translated and normalised, an unknown one unanswered), the value each
state change takes (a declared literal, a capture read through the setting's
word, its base and its conversion, a bare literal), and the reply, the
child's own or the flat variable's, also what the query handlers answer.
Recorded from the code as it is, on an invented device.
"""

from __future__ import annotations

import logging
import re

from openavc.simulator.yaml_auto import (
    ChildQueryHandler,
    CommandHandler,
    QueryHandler,
    YAMLAutoSimulator,
)


def _sim(**overrides) -> YAMLAutoSimulator:
    d = {
        "id": "acme_switcher",
        "name": "Acme Switcher",
        "transport": "tcp",
        "delimiter": "\\r",
        "state_variables": {
            "power": {"type": "boolean"},
            "level": {"type": "integer", "min": 0, "max": 100},
            "code": {"type": "string"},
            "gain": {"type": "number", "scale": 0.5},
        },
        "child_entity_types": {"output": {
            "id_format": "int",
            "state_variables": {"input": {"type": "integer"}, "mute": {"type": "boolean"}},
        }},
        "commands": {},
        "responses": [
            {"match": "LVL(\\d+)", "set": {"level": "$1"}},
            {"match": "Out(\\d+) In(\\d+)", "child_set": [
                {"type": "output", "id": "$1", "state": {"input": "$2"}},
            ]},
        ],
    }
    d.update(overrides)
    sim = YAMLAutoSimulator("w1", {}, driver_def=d)
    sim._register_sim_children("output", [1, 2, 7])
    return sim


def _run(sim, handler: CommandHandler, text: str):
    return sim._execute_command_handler(handler, handler.pattern.match(text))


def _h(pattern: str, **kw) -> CommandHandler:
    return CommandHandler(name=kw.pop("name", "cmd"), pattern=re.compile(pattern), **kw)


# ── the value each state change takes ──


def test_a_declared_literal_is_coerced_and_a_bare_literal_is_stored_as_it_is():
    sim = _sim()
    h = _h("GO", state_changes={"level": ("literal", "250"), "code": True}, response_var=None)
    assert _run(sim, h, "GO") is None
    assert sim._state["level"] == 100
    assert sim._state["code"] is True


def test_a_capture_is_read_through_the_word_then_base_then_conversion():
    sim = _sim()
    h = _h(
        "P(\\w+) L(\\w+) C(\\w+) G(\\w+)",
        state_changes={"power": 1, "level": 2, "code": 3, "gain": 4},
        response_var=None,
        group_value_maps={1: {"true": "ON", "false": "OFF"}},
        group_bases={1: 16, 2: 16, 3: 16},
        group_conversions={1: {"scale": 9}, 4: {"scale": 0.5}},
    )
    _run(sim, h, "PON L1F CFF G10")
    assert sim._state["power"] is True
    assert sim._state["level"] == 31
    assert sim._state["code"] == "FF"
    assert sim._state["gain"] == 5


def test_a_capture_the_base_cannot_read_and_a_word_the_map_lacks_go_on_as_text():
    sim = _sim()
    h = _h(
        "P(\\w+) L(\\w+)",
        state_changes={"code": 1, "level": 2},
        response_var=None,
        group_value_maps={1: {"true": "ON"}},
        group_bases={2: 16},
    )
    _run(sim, h, "PYES LZZ")
    assert sim._state["code"] == "YES"
    assert sim._state["level"] == "ZZ"


def test_a_conversion_reads_the_variable_type():
    sim = _sim()
    h = _h("G(\\S+)", state_changes={"level": 1}, response_var=None,
           group_conversions={1: {"scale": 2, "unknown": ["--"]}})
    _run(sim, h, "G21")
    assert sim._state["level"] == 42
    _run(sim, h, "G--")
    assert sim._state["level"] is None


# ── the child a command names ──


def test_a_child_command_writes_the_child_and_its_flat_fallback():
    sim = _sim()
    h = _h(
        "O(\\w+) I(\\d+) L(\\d+)",
        state_changes={"input": 2, "level": 3},
        response_var="input",
        child_type="output", child_id_group=1,
        child_wire_map={"B": "2"}, child_state_keys={"input"},
        response_is_child=True,
    )
    assert _run(sim, h, "OB I4 L9") == b"Out2 In4\r"
    assert sim._state["output.2.input"] == 4
    assert sim._state["level"] == 9
    assert _run(sim, h, "O007 I5 L1") == b"Out7 In5\r"
    assert sim._state["output.7.input"] == 5


def test_an_unknown_or_missing_child_goes_unanswered(caplog):
    sim = _sim()
    h = _h("(?:O(\\w+))?X(\\d)", state_changes={"input": 2}, response_var="input",
           child_type="output", child_id_group=1, child_state_keys={"input"},
           response_is_child=True)
    with caplog.at_level(logging.DEBUG, logger="openavc.simulator.yaml_auto"):
        assert _run(sim, h, "O9X3") is None
        assert _run(sim, h, "X3") is None
        bad = _h("O(\\d)", state_changes={"input": 1}, response_var=None,
                 child_type="output", child_id_group=4)
        assert _run(sim, bad, "O1") is None
    assert "output.9.input" not in sim._state
    assert [r.getMessage() for r in caplog.records
            if "addressed unknown" in r.getMessage()] == [
        "w1: cmd addressed unknown output '9'",
    ]


def test_a_state_machine_rejects_before_the_child_is_read():
    sim = _sim(simulator={"state_machines": {"pwr": {
        "states": ["on", "cooling", "off"], "initial": "on",
        "transitions": [
            {"from": "on", "trigger": "cool", "to": "cooling"},
            {"from": "cooling", "trigger": "*", "reject": True},
        ],
    }}})
    h = _h("C(\\d)", name="cool", state_changes={"level": 1}, response_var="level",
           child_type="output", child_id_group=1)
    assert _run(sim, h, "C9") is None
    assert sim._state["pwr"] == "cooling"
    assert _run(sim, h, "C1") is None
    assert "level" not in sim._state or sim._state["level"] != 1


# ── the reply ──


def test_the_reply_is_the_child_one_with_no_fall_back_to_the_flat_one():
    sim = _sim()
    child = _h("O(\\d) L(\\d+)", state_changes={"level": 2}, response_var="level",
               child_type="output", child_id_group=1, response_is_child=True)
    assert _run(sim, child, "O1 L5") is None
    assert sim._state["level"] == 5
    mute = _h("O(\\d) M", state_changes={}, response_var="mute",
              child_type="output", child_id_group=1, response_is_child=True)
    sim._child_state_responses[("output", "mute")] = sim._child_state_responses[
        ("output", "input")].__class__("output", "mute")
    assert _run(sim, mute, "O1 M") is None


def test_the_reply_is_the_flat_variable_or_none():
    sim = _sim()
    assert _run(sim, _h("L(\\d+)", state_changes={"level": 1}, response_var="level"),
                "L12") == b"LVL12\r"
    assert _run(sim, _h("Q", state_changes={}, response_var="code"), "Q") is None
    assert _run(sim, _h("R", state_changes={}, response_var=None), "R") is None
    flat_on_child = _h("O(\\d) L(\\d+)", state_changes={"level": 2}, response_var="level",
                       child_type="output", child_id_group=1)
    assert _run(sim, flat_on_child, "O2 L3") == b"LVL3\r"


def test_an_http_device_replies_with_no_delimiter():
    sim = _sim(transport="http")
    assert _run(sim, _h("L(\\d+)", state_changes={"level": 1}, response_var="level"),
                "L4") == b"LVL4"


# ── the query handlers ──


def test_a_query_answers_its_variable_and_a_child_query_its_child():
    sim = _sim()
    sim._state["level"] = 8
    sim._state["output.2.input"] = 3
    assert sim._execute_query_handler(QueryHandler(re.compile("Q"), "level")) == b"LVL8\r"
    assert sim._execute_query_handler(QueryHandler(re.compile("Q"), "code")) is None
    child_q = ChildQueryHandler(pattern=re.compile("Q"), child_type="output",
                                child_id="2", response_var="input")
    assert sim._execute_child_query_handler(child_q) == b"Out2 In3\r"
    none_q = ChildQueryHandler(pattern=re.compile("Q"), child_type="output",
                               child_id="2", response_var="mute")
    assert sim._execute_child_query_handler(none_q) is None


def test_a_line_runs_the_command_end_to_end():
    sim = _sim(commands={
        "set_level": {"send": "SET {level}\\r", "params": {"level": {"type": "integer"}}},
        "route": {"send": "R{output} {input}\\r", "params": {
            "output": {"type": "child_id", "child_type": "output"},
            "input": {"type": "integer"}}},
    })
    assert sim.handle_command(b"SET 40\r") == b"LVL40\r"
    assert sim._state["level"] == 40
    assert sim.handle_command(b"R7 2\r") == b"Out7 In2\r"
    assert sim._state["output.7.input"] == 2
    assert sim.handle_command(b"R8 2\r") is None
