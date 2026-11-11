"""Pins for the order a line is offered to the auto-simulator's handlers, what
a handler that matched only the front of a line does with the rest, which
handlers the coalesced-line planner counts, and how the driver's simulator:
section is read (its state, delays, error modes, controls, state machines and
hand-written handlers, the ones it skips and the log line each skip writes).
Recorded from the code as it is, on an invented device.
"""

from __future__ import annotations

import logging
import re

from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.yaml_auto import (
    ChildQueryHandler,
    CommandHandler,
    ExplicitHandler,
    QueryHandler,
    ScriptHandler,
    YAMLAutoSimulator,
)


def _sim(sim_section: dict | None = None, **overrides) -> YAMLAutoSimulator:
    d = {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp", "delimiter": "\\r",
        "state_variables": {
            "power": {"type": "boolean"}, "level": {"type": "integer"},
            "mode": {"type": "string"},
        },
        "child_entity_types": {"output": {"id_format": "int", "instances": {"count": 2},
                                          "state_variables": {"input": {"type": "integer"}}}},
        "commands": {},
        "responses": [
            {"match": "LVL(\\d+)", "set": {"level": "$1"}},
            {"match": "MODE (\\w+)", "set": {"mode": "$1"}},
            {"match": "Out(\\d+) In(\\d+)", "child_set": [
                {"type": "output", "id": "$1", "state": {"input": "$2"}}]},
        ],
    }
    d.update(overrides)
    if sim_section is not None:
        d["simulator"] = sim_section
    return YAMLAutoSimulator("w1", {}, driver_def=d)


def _every_family(sim: YAMLAutoSimulator, pattern: str) -> None:
    """One handler in every family, each answering ``pattern`` its own way."""
    p = re.compile(pattern)
    sim._explicit_handlers = [ExplicitHandler(p, "explicit", {})]
    sim._script_handlers = [ScriptHandler(p, compile("respond('script')", "<t>", "exec"))]
    sim._inline_json_polls = [p]
    sim._inline_json_fields = [{"state": "mode", "key": "m"}]
    sim._inline_response_handlers = [(p, {"mode": "inline"})]
    sim._child_query_handlers = [ChildQueryHandler(p, "output", "1", "input")]
    sim._command_handlers = [CommandHandler(name="c", pattern=p, state_changes={"level": ("literal", 7)},
                                            response_var="level")]
    sim._query_handlers = [QueryHandler(p, "mode")]


FAMILIES = [
    "_explicit_handlers", "_script_handlers", "_inline_json_polls",
    "_inline_response_handlers", "_child_query_handlers", "_command_handlers",
    "_query_handlers",
]


def test_a_line_is_offered_to_each_family_in_turn():
    sim = _sim()
    sim._state["mode"] = "m0"
    sim._state["output.1.input"] = 4
    _every_family(sim, "^X$")
    answers = []
    for family in FAMILIES:
        answers.append(sim.handle_command(b"X"))
        setattr(sim, family, [])
    answers.append(sim.handle_command(b"X"))
    assert answers == [
        b"explicit",
        b"script",
        b'{"m": "m0"}',
        b"X\r",
        b"Out1 In4\r",
        b"LVL7\r",
        b"MODE inline\r",
        None,
    ]


def test_what_a_front_match_leaves_is_the_next_command_except_after_a_json_poll():
    rest = QueryHandler(re.compile("^B$"), "level")
    results = {}
    for family in FAMILIES:
        sim = _sim()
        sim._state.update({"mode": "m0", "level": 3})
        _every_family(sim, "^A")
        for other in FAMILIES:
            if other != family:
                setattr(sim, other, [])
        sim._query_handlers = (
            [rest] if family != "_query_handlers" else [QueryHandler(re.compile("^A"), "mode"), rest]
        )
        results[family] = sim.handle_command(b"AB")
    assert results == {
        "_explicit_handlers": b"explicitLVL3\r",
        "_script_handlers": b"scriptLVL3\r",
        "_inline_json_polls": b'{"m": "m0"}',
        "_inline_response_handlers": b"AB\rLVL3\r",
        "_child_query_handlers": b"Out1 In0\rLVL3\r",
        "_command_handlers": b"LVL7\rLVL7\r",
        "_query_handlers": b"MODE m0\rLVL3\r",
    }


def test_the_coalesced_line_planner_counts_every_family_but_the_json_polls():
    planned = {}
    for family in FAMILIES:
        sim = _sim()
        sim._state["mode"] = "m0"
        _every_family(sim, "^J$")
        for other in FAMILIES:
            if other != family:
                setattr(sim, other, [])
        sim._query_handlers = sim._query_handlers + [QueryHandler(re.compile("^K$"), "level")]
        sim._state["level"] = 2
        planned[family] = (sim._any_handler_fullmatch("J"), sim.handle_command(b"JK"))
    assert planned == {
        "_explicit_handlers": (True, b"explicitLVL2\r"),
        "_script_handlers": (True, b"scriptLVL2\r"),
        "_inline_json_polls": (False, None),
        "_inline_response_handlers": (True, b"J\rLVL2\r"),
        "_child_query_handlers": (True, b"Out1 In0\rLVL2\r"),
        "_command_handlers": (True, b"LVL7\rLVL7\r"),
        "_query_handlers": (True, b"MODE m0\rLVL2\r"),
    }


def test_dispatch_matches_a_front_and_the_planner_a_whole_piece():
    sim = _sim()
    for family in FAMILIES:
        setattr(sim, family, [])
    sim._query_handlers = [QueryHandler(re.compile("^A"), "level"),
                           QueryHandler(re.compile("^B$"), "mode")]
    sim._state.update({"level": 1, "mode": "m"})
    assert sim._any_handler_fullmatch("AZ") is False
    assert sim._any_handler_fullmatch("A") is True
    assert sim.handle_command(b"AZ") == b"LVL1\r"
    assert sim.handle_command(b"BA") == b"MODE m\rLVL1\r"


def test_a_line_of_known_pieces_runs_each_and_a_long_one_is_not_planned():
    sim = _sim()
    for family in FAMILIES:
        setattr(sim, family, [])
    sim._command_handlers = [
        CommandHandler(name="p", pattern=re.compile("^P$"), state_changes={"level": ("literal", 5)},
                       response_var="level"),
        CommandHandler(name="q", pattern=re.compile("^Q$"), state_changes={}, response_var=None),
    ]
    assert sim.handle_command(b"PQ") == b"LVL5\r"
    assert sim.handle_command(b"QQ") == b""
    assert sim.handle_command(b"PZ") is None
    assert sim.handle_command(b"P" * 257) is None
    assert sim.handle_command(b"   ") is None


def test_a_registration_nothing_answers_is_acknowledged_empty(monkeypatch):
    sim = _sim()
    monkeypatch.setattr(sim, "_watch_push_registration", lambda text: text == "SUB 1")
    assert sim.handle_command(b"SUB 1") == b""
    assert sim.handle_command(b"SUB 2") is None


def _records(caplog) -> list[tuple[str, str, str]]:
    return [(r.name, r.levelname, r.getMessage()) for r in caplog.records
            if r.name.startswith("openavc.simulator") and r.levelno >= logging.WARNING]


def test_the_simulator_section_is_read_in_its_order(caplog):
    section = {
        "initial_state": {"level": 9, "mode": "boot", "extra": 1, "output.2.input": 3},
        "delays": {"command_response": 0.2, "other": 1},
        "error_modes": {"jam": {"behavior": "no_response", "description": "Jammed"}},
        "controls": [{"type": "toggle", "key": "power"}],
        "state_machines": {
            "mode": {"states": ["boot", "on"], "initial": "on",
                     "transitions": [{"from": "boot", "to": "on", "trigger": "go"}]},
            "bad": {"states": ["a"], "initial": "a"},
            "worse": "not a mapping",
        },
        "command_handlers": [
            {"receive": "R(\\d)", "respond": "r{1}", "set_state": {"level": "{1}"}},
            {"match": "S(\\d)", "handler": "respond('s')"},
            {"match": "T(", "respond": "never"},
            {"match": "U", "handler": "def broken(:"},
            {"address": "/a/*", "handler": "respond('/a', [1])"},
            {"address": "/b/*", "handler": "def broken(:"},
            {"address": "/c", "receive": "C", "respond": "c"},
            {"respond": "no pattern"},
            {"receive": "", "match": "M", "respond": "m"},
        ],
    }
    with caplog.at_level(logging.DEBUG):
        sim = _sim(section)
    assert [(h.pattern.pattern, h.respond, h.set_state) for h in sim._explicit_handlers] == [
        ("^R(\\d)$", "r{1}", {"level": "{1}"}),
        ("^C$", "c", {}),
        ("^M$", "m", {}),
    ]
    assert [h.pattern.pattern for h in sim._script_handlers] == ["^S(\\d)$"]
    assert [h.address_pattern for h in sim._osc_script_handlers] == ["/a/*"]
    assert list(sim._state_machines) == ["mode"]
    assert sim._state_machines["mode"].current == "on"
    assert sim._state["mode"] == "on"
    assert sim._state["level"] == 9
    assert sim._state["extra"] == 1
    assert sim._child_roster["output"] == ["1", "2"]
    assert sim._delays["command_response"] == 0.2
    assert sim._delays["other"] == 1
    assert sim._error_modes["jam"] == {"behavior": "no_response", "description": "Jammed"}
    assert sim.SIMULATOR_INFO["controls"] == [{"type": "toggle", "key": "power"}]
    assert list(sim._state)[:8] == [
        "power", "level", "mode", "extra", "output.2.input", "output.1.input",
    ][:8]
    assert _records(caplog) == [
        ("openavc.simulator.yaml_auto", "WARNING",
         "acme_widget: skipping malformed state_machine 'bad' (needs states, initial, transitions)"),
        ("openavc.simulator.yaml_auto", "WARNING",
         "acme_widget: skipping malformed state_machine 'worse' (needs states, initial, transitions)"),
        ("openavc.simulator.yaml_auto", "WARNING",
         "Invalid regex in simulator command_handler: T("),
        ("openavc.simulator.yaml_auto", "WARNING",
         "acme_widget: skipping command handler 'U' — handler code has a syntax error: "
         "invalid syntax (<sim:acme_widget>, line 1)"),
        ("openavc.simulator.yaml_auto", "WARNING",
         "acme_widget: skipping OSC handler for /b/* — handler code has a syntax error: "
         "invalid syntax (<sim:acme_widget>, line 1)"),
    ]


def test_a_section_without_handlers_leaves_the_class_lists_and_no_section_skips_the_merge():
    sim = _sim({"push_state": True})
    assert sim._explicit_handlers == [] and sim._script_handlers == []
    assert "_explicit_handlers" in vars(sim)
    bare = _sim()
    assert "_explicit_handlers" not in vars(bare)
    assert bare._push_state is False and sim._push_state is True


def test_a_python_simulator_skips_a_malformed_machine_in_its_own_words(caplog):
    class Acme(TCPSimulator):
        SIMULATOR_INFO = {
            "driver_id": "acme_py", "name": "Acme Py", "transport": "tcp",
            "initial_state": {"phase": "x"},
            "state_machines": {
                "phase": {"states": ["a", "b"], "initial": "a",
                          "transitions": [{"from": "a", "to": "b", "trigger": "go"}]},
                "bad": {"states": ["a"]},
            },
        }

        def handle_command(self, data):  # pragma: no cover - not dispatched
            return None

    with caplog.at_level(logging.DEBUG):
        sim = Acme("p1")
    assert list(sim._state_machines) == ["phase"]
    assert sim._state["phase"] == "a"
    assert _records(caplog) == [
        ("openavc.simulator.base", "WARNING",
         "Skipping malformed state_machine 'bad' on p1 (needs states, initial, transitions)"),
    ]
