"""Pins for the simulator validator's checks of a driver's own ``simulator:``
section, through ``validate_yaml_driver``.

The declared Simulator UI controls (``controls``), the state machines
(``state_machine``), and the hand-written command handlers: whether their code
compiles (``handler_syntax``), whether a template handler can fire and what its
placeholders name (``command_handlers``, ``set_state``), and a ``{state.X}``
reference to a boolean in a ``respond:`` template (``type_consistency``). Each
case is an invented Acme driver; the expected lines are what the validator
says today, severity, check, message and order exact, or what it raises, so a
restructure that changes any of them fails here. Some pin what looks wrong
(a dotted ``{state.a.b}`` reference goes unchecked, where the simulator fills
one); they record today's behaviour, they do not endorse it.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from openavc.simulator.validate import validate_yaml_driver


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
        "input": {"type": "enum", "values": ["hdmi1", "hdmi2"], "label": "Input"},
    },
    "commands": {
        "power_on": {"send": "PWR 1\\r\\n"},
        "power_off": {"send": "PWR 0\\r\\n"},
    },
    "responses": [
        {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
    ],
}


def _sim(section: dict, **changes) -> dict:
    """The base driver with this ``simulator:`` section and top-level keys
    replaced (None removes one)."""
    definition = copy.deepcopy(_BASE)
    for key, value in changes.items():
        if value is None:
            definition.pop(key, None)
        else:
            definition[key] = value
    definition["simulator"] = section
    return definition


def _controls(*controls, **section) -> dict:
    return _sim({"controls": list(controls), **section})


def _machines(machines, **changes) -> dict:
    return _sim({"state_machines": machines}, **changes)


def _machine(*transitions, states=("off", "on"), initial="off") -> dict:
    return _machines({"pwr": {
        "states": list(states), "initial": initial, "transitions": list(transitions),
    }})


def _handlers(*handlers, **section) -> dict:
    return _sim({"command_handlers": list(handlers), **section})


CASES: dict[str, dict] = {
    # ── controls: the section ──
    "controls_absent": _sim({}),
    "controls_empty_list": _controls(),
    "controls_not_a_list": _sim({"controls": {"type": "power", "key": "power"}}),
    "controls_null": _sim({"controls": None}),
    "controls_empty_mapping": _sim({"controls": {}}),
    "controls_empty_text": _sim({"controls": ""}),
    "controls_false": _sim({"controls": False}),
    "control_not_a_mapping": _controls("power", 5, None),
    "control_unknown_type": _controls({"type": "meter", "key": "power"}),
    "control_without_type": _controls({"key": "power"}),
    "control_type_a_list": _controls({"type": ["power"], "key": "power"}),
    "control_type_a_number": _controls({"type": 3}),
    # ── controls: every type, complete ──
    "controls_every_type_complete": _controls(
        {"type": "power", "key": "power"},
        {"type": "select", "key": "input", "options": ["hdmi1", "hdmi2"]},
        {"type": "slider", "key": "level", "min": 0, "max": 100},
        {"type": "toggle", "key": "power", "label": "Power"},
        {"type": "matrix", "inputs": 4, "outputs": 2, "state_pattern": "route.{output}"},
        {"type": "meters", "channels": 2, "key_pattern": "meter.{ch}", "mute_pattern": "mute.{ch}"},
        {"type": "presets", "key": "level", "count": 4},
        {"type": "group", "label": "Main", "controls": [{"type": "power", "key": "power"}]},
        {"type": "indicator", "key": "power", "label": "On"},
        initial_state={"route.1": 1, "meter.1": 0, "mute.1": False},
    ),
    # ── controls: every type, bare ──
    "controls_every_type_bare": _controls(
        {"type": "power"}, {"type": "select"}, {"type": "slider"}, {"type": "toggle"},
        {"type": "matrix"}, {"type": "meters"}, {"type": "presets"}, {"type": "group"},
        {"type": "indicator"},
    ),
    # ── controls: state keys ──
    "control_keys_unknown": _controls(
        {"type": "power", "key": "pwr"},
        {"type": "select", "key": "src", "options": ["a"]},
        {"type": "slider", "key": "vol", "min": 0, "max": 1},
        {"type": "toggle", "key": "mute", "label": "Mute"},
        {"type": "presets", "key": "preset", "count": 2},
        {"type": "indicator", "key": "alarm", "label": "Alarm"},
        {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": "xpt.{output}", "key": "nope"},
        {"type": "meters", "channels": 2, "key_pattern": "lvl.{ch}", "key": "nope"},
        {"type": "group", "label": "G", "controls": [], "key": "nope"},
    ),
    "control_key_not_a_string": _controls(
        {"type": "power", "key": 5}, {"type": "slider", "key": None, "min": 0, "max": 1},
        {"type": "toggle", "key": ["power"], "label": "P"},
    ),
    "control_keys_from_initial_state_and_machines": _sim({
        "initial_state": {"extra": 1},
        "state_machines": {"lamp": {"states": ["a"], "initial": "a", "transitions": []}},
        "controls": [
            {"type": "power", "key": "extra"},
            {"type": "indicator", "key": "lamp", "label": "Lamp"},
        ],
    }),
    "control_keys_with_nothing_known": _sim(
        {"controls": [
            {"type": "power", "key": "pwr"},
            {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": "xpt.{output}"},
            {"type": "meters", "channels": 1, "key_pattern": "lvl.{ch}"},
        ]},
        state_variables={},
    ),
    # ── controls: select ──
    "select_options_not_a_list": _controls(
        {"type": "select", "key": "input", "options": "hdmi1,hdmi2"},
        {"type": "select", "key": "input", "options": {"a": 1}},
    ),
    "select_options_empty": _controls({"type": "select", "key": "input", "options": []}),
    "select_options_null": _controls({"type": "select", "key": "input", "options": None}),
    # ── controls: slider ──
    "slider_bounds_not_numbers": _controls(
        {"type": "slider", "key": "level", "min": "0", "max": [100]},
        {"type": "slider", "key": "level", "min": None, "max": "high"},
    ),
    "slider_bounds_odd_numbers": _controls(
        {"type": "slider", "key": "level", "min": True, "max": 1.5},
        {"type": "slider", "key": "level", "min": -10, "max": False},
    ),
    # ── controls: matrix ──
    "matrix_sizes_bad": _controls(
        {"type": "matrix", "inputs": 0, "outputs": -1, "state_pattern": "route.{output}"},
        {"type": "matrix", "inputs": "4", "outputs": 2.0, "state_pattern": "route.{output}"},
        {"type": "matrix", "inputs": None, "outputs": [2], "state_pattern": "route.{output}"},
        initial_state={"route.1": 1},
    ),
    "matrix_sizes_booleans": _controls(
        {"type": "matrix", "inputs": True, "outputs": False, "state_pattern": "route.{output}"},
        initial_state={"route.1": 1},
    ),
    "matrix_pattern_without_output": _controls(
        {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": "route"},
        {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": "route.{out}"},
    ),
    "matrix_pattern_unknown_key": _controls(
        {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": "xpt.{output}.src"},
    ),
    "matrix_pattern_not_text": _controls(
        {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": 5},
        {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": None},
    ),
    "matrix_pattern_repeats_output": _controls(
        {"type": "matrix", "inputs": 2, "outputs": 2, "state_pattern": "r.{output}.{output}"},
        initial_state={"r.1.1": 0},
    ),
    # ── controls: meters ──
    "meters_channels_bad": _controls(
        {"type": "meters", "channels": 0, "key_pattern": "m.{ch}"},
        {"type": "meters", "channels": "8", "key_pattern": "m.{ch}"},
        {"type": "meters", "channels": False, "key_pattern": "m.{ch}"},
        {"type": "meters", "channels": True, "key_pattern": "m.{ch}"},
        {"type": "meters", "channels": None, "key_pattern": "m.{ch}"},
        initial_state={"m.1": 0},
    ),
    "meters_patterns_without_ch": _controls(
        {"type": "meters", "channels": 2, "key_pattern": "meter", "mute_pattern": "mute"},
        {"type": "meters", "channels": 2, "key_pattern": "meter.{channel}"},
    ),
    "meters_patterns_unknown": _controls(
        {"type": "meters", "channels": 2, "key_pattern": "lvl.{ch}", "mute_pattern": "mt.{ch}"},
        {"type": "meters", "channels": 2, "key_pattern": "meter.{ch}", "mute_pattern": "mt.{ch}"},
        initial_state={"meter.1": 0},
    ),
    "meters_patterns_not_text": _controls(
        {"type": "meters", "channels": 2, "key_pattern": 7, "mute_pattern": ["x"]},
    ),
    "meters_mute_pattern_only_bad": _controls(
        {"type": "meters", "channels": 2, "key_pattern": "meter.{ch}", "mute_pattern": "mute"},
        initial_state={"meter.1": 0},
    ),
    # ── controls: presets ──
    "presets_neither_count_nor_names": _controls({"type": "presets", "key": "level"}),
    "presets_names_only": _controls({"type": "presets", "key": "level", "names": ["A", "B"]}),
    "presets_count_null": _controls({"type": "presets", "key": "level", "count": None}),
    "presets_count_and_names_bad": _controls(
        {"type": "presets", "key": "level", "count": "4", "names": "A,B"},
        {"type": "presets", "key": "level", "count": 2.5, "names": {"a": 1}},
        {"type": "presets", "key": "level", "count": True, "names": None},
    ),
    # ── controls: group ──
    "group_children_checked": _controls(
        {"type": "group", "label": "Main", "controls": [
            {"type": "power", "key": "nope"},
            "bad",
            {"type": "group", "controls": [{"type": "toggle", "key": "power"}]},
            {"type": "dial"},
        ]},
        {"type": "power", "key": "after"},
    ),
    "group_children_not_a_list": _controls(
        {"type": "group", "label": "G", "controls": "power"},
        {"type": "group", "label": "G", "controls": {"type": "power"}},
    ),
    "group_children_null": _controls({"type": "group", "label": "G", "controls": None}),
    "group_child_type_a_list": _controls(
        {"type": "group", "label": "G", "controls": [{"type": ["x"]}]},
    ),
    "labels_missing_and_present": _controls(
        {"type": "toggle", "key": "power"},
        {"type": "indicator", "key": "power"},
        {"type": "group", "controls": []},
        {"type": "toggle", "key": "power", "label": None},
        {"type": "power", "key": "power"},
        {"type": "slider", "key": "level", "min": 0, "max": 1},
    ),
    # ── state machines: the section ──
    "machines_absent": _sim({}),
    "machines_empty": _machines({}),
    "machines_null": _machines(None),
    "machines_empty_list": _machines([]),
    "machines_empty_text": _machines(""),
    "machines_a_list": _machines([{"states": ["a"]}]),
    "machines_text": _machines("pwr"),
    "machine_not_a_mapping": _machines({"pwr": ["off", "on"], "lamp": 5, "ok": {
        "states": ["a"], "initial": "a", "transitions": [],
    }}),
    # ── state machines: one machine's shape ──
    "machine_bare": _machines({"pwr": {}}),
    "machine_states_empty": _machines({"pwr": {"states": [], "initial": "off", "transitions": [
        {"from": "x", "to": "y", "trigger": "power_on"},
    ]}}),
    "machine_states_text": _machines({"pwr": {"states": "off,on", "initial": "off", "transitions": [
        {"from": "x", "to": "y", "trigger": "power_on"},
    ]}}),
    "machine_initial_off_list": _machines({"pwr": {
        "states": ["off", "on"], "initial": "warm", "transitions": [],
    }}),
    "machine_initial_null": _machines({"pwr": {
        "states": ["off", "on"], "initial": None, "transitions": [],
    }}),
    "machine_initial_off_list_states_empty": _machines({"pwr": {
        "states": [], "initial": "warm", "transitions": [],
    }}),
    "machine_transitions_missing": _machines({"pwr": {"states": ["off"], "initial": "off"}}),
    "machine_transitions_a_mapping": _machines({"pwr": {
        "states": ["off"], "initial": "off", "transitions": {"from": "off"},
    }}),
    "machine_transitions_null_and_more_machines": _machines({
        "pwr": {"states": ["off"], "initial": "off", "transitions": None},
        "lamp": {"states": ["a", "b"], "initial": "a", "transitions": [
            {"from": "a", "to": "c", "trigger": "power_on"},
        ]},
    }),
    # ── state machines: one transition ──
    "transition_not_a_mapping": _machine("off->on", None, ["off", "on"]),
    "transition_complete": _machine(
        {"from": "off", "to": "on", "trigger": "power_on"},
        {"from": "on", "to": "off", "after_seconds": 5},
        {"from": "on", "trigger": "*", "reject": True},
    ),
    "transition_from_missing_and_off_list": _machine(
        {"to": "on", "trigger": "power_on"},
        {"from": "warm", "to": "on", "trigger": "power_off"},
        {"from": None, "to": "on", "trigger": "power_off"},
    ),
    "transition_to_missing_and_off_list": _machine(
        {"from": "off", "trigger": "power_on"},
        {"from": "on", "to": "warm", "trigger": "power_off"},
        {"from": "on", "to": None, "after_seconds": 1},
    ),
    "transition_reject_without_to": _machine(
        {"from": "off", "trigger": "power_off", "reject": True},
        {"from": "off", "to": "nowhere", "trigger": "power_on", "reject": True},
        {"from": "on", "reject": True},
        {"from": "on", "trigger": "power_on", "reject": False},
        {"from": "on", "trigger": "power_off", "reject": "yes"},
    ),
    "transition_after_seconds": _machine(
        {"from": "off", "to": "on", "after_seconds": "5"},
        {"from": "off", "to": "on", "after_seconds": None},
        {"from": "on", "to": "off", "after_seconds": True},
        {"from": "on", "to": "off", "after_seconds": 0.5},
    ),
    "transition_can_never_fire": _machine(
        {"from": "off", "to": "on"},
        {"from": "on", "to": "off", "trigger": None},
        {"from": "on", "to": "off", "trigger": ""},
    ),
    "transition_trigger_names_no_command": _machine(
        {"from": "off", "to": "on", "trigger": "power_toggle"},
        {"from": "on", "to": "off", "trigger": "*"},
        {"from": "on", "to": "off", "trigger": "power_off"},
        {"from": "on", "to": "off", "trigger": 7},
    ),
    "transition_trigger_with_no_commands": _machines(
        {"pwr": {"states": ["off", "on"], "initial": "off", "transitions": [
            {"from": "off", "to": "on", "trigger": "power_toggle"},
        ]}},
        commands={},
    ),
    "transition_duplicates": _machine(
        {"from": "off", "to": "on", "trigger": "power_on"},
        {"from": "off", "to": "off", "trigger": "power_on"},
        {"from": "off", "to": "on", "trigger": "power_on"},
        {"from": "on", "to": "off", "trigger": "*"},
        {"from": "on", "trigger": "*", "reject": True},
        {"to": "on", "trigger": "power_off"},
        {"to": "off", "trigger": "power_off"},
        {"from": "off", "to": "on", "trigger": None},
        {"from": "off", "to": "on", "trigger": None},
        {"from": "off", "to": "on", "after_seconds": 1},
        {"from": "off", "to": "on", "after_seconds": 2},
    ),
    "transition_duplicates_per_machine": _machines({
        "pwr": {"states": ["off", "on"], "initial": "off", "transitions": [
            {"from": "off", "to": "on", "trigger": "power_on"},
        ]},
        "lamp": {"states": ["off", "on"], "initial": "off", "transitions": [
            {"from": "off", "to": "on", "trigger": "power_on"},
            {"from": "off", "to": "off", "trigger": "power_on"},
        ]},
    }),
    "transition_everything_wrong": _machine(
        {"from": "warm", "to": "cold", "after_seconds": "x", "trigger": "nope"},
        {"from": "warm", "to": "cold", "after_seconds": "x", "trigger": "nope"},
        {},
    ),
    "transition_from_a_list_beside_a_trigger": _machine(
        {"from": ["off"], "to": "on", "trigger": "power_on"},
    ),
    "transition_from_a_list_without_a_trigger": _machine(
        {"from": ["off"], "to": ["on"], "after_seconds": 1},
    ),
    "transitions_states_text": _machines({"pwr": {"states": "off", "initial": "x", "transitions": [
        {"from": "q", "to": "r", "trigger": "power_on"},
    ]}}),
    # ── handlers: syntax ──
    "handler_entries_not_mappings": _handlers("PWR?", 5, None, ["x"]),
    "handler_syntax_errors_labelled": _handlers(
        {"address": "/ch/1", "handler": "x = ("},
        {"receive": "PWR\\?", "handler": "def f(:\n  pass"},
        {"match": "LVL", "handler": "if True\n  pass"},
        {"handler": "return ("},
        {"receive": "", "match": "", "handler": "x ="},
        {"address": "", "receive": "SRC", "handler": "x ="},
    ),
    "handler_code_compiles": _handlers(
        {"receive": "PWR\\?", "handler": "respond('PWR 1')"},
        {"address": "/lvl", "handler": 5},
        {"receive": "SRC", "handler": None},
        {"receive": "MUT", "handler": ""},
    ),
    "handler_code_a_bare_return": _handlers({"receive": "PWR\\?", "handler": "return 1"}),
    # ── handlers: a template's surface ──
    "handler_beside_respond_and_set_state": _handlers(
        {"receive": "PWR (\\d)", "handler": "pass", "respond": "OK"},
        {"receive": "PWR (\\d)", "handler": "pass", "set_state": {"power": "{1}"}},
        {"receive": "PWR (\\d)", "handler": "pass", "set_state": {}, "respond": ""},
        {"handler": "pass", "respond": "OK"},
        {"receive": "LVL", "handler": None, "respond": "OK"},
    ),
    "template_without_a_pattern": _handlers(
        {"respond": "OK"},
        {"set_state": {"power": "1"}},
        {"receive": "", "respond": "OK"},
        {"receive": "", "match": "", "set_state": {"level": "2"}},
        {"set_state": {}, "respond": ""},
        {},
    ),
    "template_without_a_pattern_carrying_refs": _handlers(
        {"respond": "P{1}", "set_state": {"nope": "{state.gone}{2}"}},
        {"match": "", "respond": "{state.nope}"},
    ),
    "template_match_used_when_receive_blank": _handlers(
        {"receive": "", "match": "PWR (\\d)", "respond": "P{2}"},
        {"match": "LVL (\\d+) (\\d+)", "respond": "L{2}{3}"},
    ),
    "template_invalid_regex": _handlers(
        {"receive": "PWR (", "respond": "OK"},
        {"match": "[", "set_state": {"nope": "1"}},
        {"receive": "*", "respond": "{9}"},
    ),
    "template_receive_a_number": _handlers({"receive": 5, "respond": "OK"}),
    "template_receive_a_list": _handlers({"receive": ["PWR"], "respond": "OK"}),
    "template_set_state_targets": _handlers(
        {"receive": "PWR (\\d)", "set_state": {"power": "{1}", "nope": "{1}", "lamp": "on"}},
        initial_state={"lamp": "off"},
    ),
    "template_set_state_not_a_mapping": _handlers(
        {"receive": "PWR (\\d)", "set_state": ["power", "{5}"], "respond": "P{1}"},
        {"receive": "LVL", "set_state": "level={2}"},
    ),
    "template_set_state_values_not_text": _handlers(
        {"receive": "PWR (\\d)", "set_state": {"power": 1, "level": None, "input": ["{3}"]}},
    ),
    "template_respond_not_text": _handlers(
        {"receive": "PWR", "respond": 5},
        {"receive": "LVL", "respond": ["{4}"]},
    ),
    "template_respond_empty_not_text": _handlers(
        {"receive": "PWR (\\d)", "respond": 0, "set_state": {"power": "{2}"}},
        {"receive": "LVL", "respond": [], "set_state": {"nope": "1"}},
        {"respond": {}, "set_state": {}},
        {"respond": False},
    ),
    "template_capture_refs": _handlers(
        {"receive": "PWR (\\d) (\\d)", "respond": "P{0}{1}{2}{3}{10}"},
        {"receive": "LVL", "set_state": {"level": "{1}"}, "respond": "L{1}"},
        {"receive": "(?:SRC) (\\w+)", "set_state": {"input": "{1}{2}"}},
    ),
    "template_state_refs": _handlers(
        {"receive": "PWR\\?", "respond": "P{state.power} L{state.level} X{state.nope}"},
        {"receive": "SRC\\?", "respond": "S{state.a.b} {state.zone-1} {state.} {state.nope}{state.nope}"},
        {"receive": "LVL (\\d+)", "set_state": {"level": "{state.gone}{1}", "power": "{state.lamp}"}},
        initial_state={"lamp": "off"},
    ),
    "template_refs_with_nothing_known": _sim(
        {"command_handlers": [
            {"receive": "PWR (\\d)", "set_state": {"pwr": "{1}"}, "respond": "{state.x}{4}"},
        ]},
        state_variables={},
    ),
    "template_refs_name_a_machine": _sim({
        "state_machines": {"lamp": {"states": ["a"], "initial": "a", "transitions": []}},
        "command_handlers": [
            {"receive": "LAMP", "set_state": {"lamp": "a"}, "respond": "L{state.lamp}"},
        ],
    }),
    "template_osc_entries_skipped": _handlers(
        {"address": "/ch/1", "respond": "{9}", "set_state": {"nope": "1"}},
        {"address": "/ch/2", "receive": "(", "respond": "{state.nope}"},
        {"address": None, "receive": "PWR", "respond": "{2}"},
    ),
    "respond_names_a_boolean_dotted": _handlers(
        {"receive": "PWR\\?", "respond": "P{state.power.1} {state.power_x} {state.level}"},
    ),
    "respond_names_a_dotted_boolean": _sim(
        {"command_handlers": [{"receive": "AB\\?", "respond": "A{state.a.b} {state.power}"}]},
        state_variables={
            "power": {"type": "boolean", "label": "Power"},
            "a.b": {"type": "boolean", "label": "A B"},
        },
    ),
    # ── handlers: the order the two passes report ──
    "handlers_both_passes_in_order": _handlers(
        {"receive": "A", "handler": "x = (", "respond": "R"},
        "bad",
        {"receive": "(", "respond": "x"},
        {"receive": "B (\\d)", "handler": "y = (", "set_state": {"nope": "{1}"}},
        {"receive": "C", "respond": "{state.nope}{1}"},
    ),
    # ── handlers: a boolean state value in a respond: template ──
    "respond_names_a_boolean": _handlers(
        {"receive": "PWR\\?", "respond": "P{state.power} {state.power}"},
        {"receive": "LVL\\?", "respond": "L{state.level}"},
        {"receive": "INP\\?", "respond": "I{state.input}"},
        {"receive": "SRC\\?", "handler": "pass", "respond": "{state.power}"},
        {"receive": "X", "respond": "{state.nope}"},
        "bad",
    ),
}

#: The checks these cases exercise; the other checks' lines are not pinned here.
_CHECKS = frozenset({
    "controls", "state_machine", "handler_syntax", "command_handlers", "set_state",
    "type_consistency",
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
def test_the_simulator_section_says_what_it_said(tmp_path, name):
    assert _issues(tmp_path, name) == EXPECTED[name]


def test_every_case_has_an_expectation():
    assert sorted(EXPECTED) == sorted(CASES)


def _nested_groups(depth: int, leaf: str) -> str:
    """A driver whose one control is a group nested ``depth`` deep around
    ``leaf``, written with YAML anchors (plain nesting that deep is more than
    the YAML loader takes)."""
    lines = [
        "id: acme_widget",
        "transport: tcp",
        "state_variables:",
        "  power: {type: boolean, label: Power}",
        "groups:",
        f"  g0: &g0 {leaf}",
    ]
    lines += [
        f"  g{i}: &g{i} {{type: group, label: G, controls: [*g{i - 1}]}}"
        for i in range(1, depth + 1)
    ]
    lines += ["simulator:", "  controls:", f"  - *g{depth}"]
    return "\n".join(lines) + "\n"


def test_a_group_nested_four_hundred_deep_is_checked_to_its_leaf(tmp_path):
    """Each level of a group costs the check one call, as it always has, so a
    deep group is read to its leaf rather than ending the run."""
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(_nested_groups(400, "{type: power, key: nope}"), encoding="utf-8")
    found = [(i.severity, i.message) for i in validate_yaml_driver(path).issues if i.check == "controls"]
    where = "controls[0]" + ".controls[0]" * 400
    assert found == [(
        "warning",
        f"{where} (power): key 'nope' is not a state variable or initial_state key — the "
        f"control shows nothing and writes to a key nothing reads",
    )]


EXPECTED: dict = {'control_key_not_a_string': [],
 'control_keys_from_initial_state_and_machines': [],
 'control_keys_unknown': [('warning',
                           'controls',
                           "controls[0] (power): key 'pwr' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads'),
                          ('warning',
                           'controls',
                           "controls[1] (select): key 'src' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads'),
                          ('warning',
                           'controls',
                           "controls[2] (slider): key 'vol' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads'),
                          ('warning',
                           'controls',
                           "controls[3] (toggle): key 'mute' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads'),
                          ('warning',
                           'controls',
                           "controls[4] (presets): key 'preset' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads'),
                          ('warning',
                           'controls',
                           "controls[5] (indicator): key 'alarm' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads'),
                          ('warning',
                           'controls',
                           "controls[6] (matrix): state_pattern 'xpt.1' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads'),
                          ('warning',
                           'controls',
                           "controls[7] (meters): key_pattern 'lvl.1' is not a state variable or "
                           'initial_state key — the control shows nothing and writes to a key '
                           'nothing reads')],
 'control_keys_with_nothing_known': [],
 'control_not_a_mapping': [('error', 'controls', "controls[0]: must be a mapping, got: 'power'"),
                           ('error', 'controls', 'controls[1]: must be a mapping, got: 5'),
                           ('error', 'controls', 'controls[2]: must be a mapping, got: None')],
 'control_type_a_list': ('raises', 'TypeError', "unhashable type: 'list'"),
 'control_type_a_number': [('error',
                            'controls',
                            'controls[0]: unknown control type 3 — the panel renders nothing for '
                            'it (valid types: group, indicator, matrix, meters, power, presets, '
                            'select, slider, toggle)')],
 'control_unknown_type': [('error',
                           'controls',
                           "controls[0]: unknown control type 'meter' — the panel renders nothing "
                           'for it (valid types: group, indicator, matrix, meters, power, presets, '
                           'select, slider, toggle)')],
 'control_without_type': [('error',
                           'controls',
                           'controls[0]: unknown control type None — the panel renders nothing for '
                           'it (valid types: group, indicator, matrix, meters, power, presets, '
                           'select, slider, toggle)')],
 'controls_absent': [],
 'controls_empty_list': [],
 'controls_empty_mapping': [('error', 'controls', 'simulator.controls must be a list')],
 'controls_empty_text': [('error', 'controls', 'simulator.controls must be a list')],
 'controls_every_type_bare': [('error',
                               'controls',
                               "controls[0] (power): missing required field 'key'"),
                              ('error',
                               'controls',
                               "controls[1] (select): missing required field 'key'"),
                              ('error',
                               'controls',
                               "controls[1] (select): missing required field 'options'"),
                              ('error',
                               'controls',
                               "controls[2] (slider): missing required field 'key'"),
                              ('error',
                               'controls',
                               "controls[2] (slider): missing required field 'min'"),
                              ('error',
                               'controls',
                               "controls[2] (slider): missing required field 'max'"),
                              ('error',
                               'controls',
                               "controls[3] (toggle): missing required field 'key'"),
                              ('warning',
                               'controls',
                               "controls[3] (toggle): missing 'label' — the panel shows "
                               "'undefined'"),
                              ('error',
                               'controls',
                               "controls[4] (matrix): missing required field 'inputs'"),
                              ('error',
                               'controls',
                               "controls[4] (matrix): missing required field 'outputs'"),
                              ('error',
                               'controls',
                               "controls[4] (matrix): missing required field 'state_pattern'"),
                              ('error',
                               'controls',
                               "controls[5] (meters): missing required field 'channels'"),
                              ('error',
                               'controls',
                               "controls[5] (meters): missing required field 'key_pattern'"),
                              ('error',
                               'controls',
                               "controls[6] (presets): missing required field 'key'"),
                              ('warning',
                               'controls',
                               "controls[6] (presets): neither 'count' nor 'names' given — the "
                               'panel renders no preset buttons'),
                              ('error',
                               'controls',
                               "controls[7] (group): missing required field 'controls'"),
                              ('warning',
                               'controls',
                               "controls[7] (group): missing 'label' — the panel shows "
                               "'undefined'"),
                              ('error',
                               'controls',
                               "controls[8] (indicator): missing required field 'key'"),
                              ('warning',
                               'controls',
                               "controls[8] (indicator): missing 'label' — the panel shows "
                               "'undefined'")],
 'controls_every_type_complete': [],
 'controls_false': [('error', 'controls', 'simulator.controls must be a list')],
 'controls_not_a_list': [('error', 'controls', 'simulator.controls must be a list')],
 'controls_null': [],
 'group_child_type_a_list': ('raises', 'TypeError', "unhashable type: 'list'"),
 'group_children_checked': [('warning',
                             'controls',
                             "controls[0].controls[0] (power): key 'nope' is not a state variable "
                             'or initial_state key — the control shows nothing and writes to a key '
                             'nothing reads'),
                            ('error',
                             'controls',
                             "controls[0].controls[1]: must be a mapping, got: 'bad'"),
                            ('warning',
                             'controls',
                             "controls[0].controls[2] (group): missing 'label' — the panel shows "
                             "'undefined'"),
                            ('warning',
                             'controls',
                             "controls[0].controls[2].controls[0] (toggle): missing 'label' — the "
                             "panel shows 'undefined'"),
                            ('error',
                             'controls',
                             "controls[0].controls[3]: unknown control type 'dial' — the panel "
                             'renders nothing for it (valid types: group, indicator, matrix, '
                             'meters, power, presets, select, slider, toggle)'),
                            ('warning',
                             'controls',
                             "controls[1] (power): key 'after' is not a state variable or "
                             'initial_state key — the control shows nothing and writes to a key '
                             'nothing reads')],
 'group_children_not_a_list': [('error',
                                'controls',
                                "controls[0] (group): 'controls' must be a list"),
                               ('error',
                                'controls',
                                "controls[1] (group): 'controls' must be a list")],
 'group_children_null': [],
 'handler_beside_respond_and_set_state': [('warning',
                                           'command_handlers',
                                           "handler 'PWR (\\\\d)': set_state:/respond: are ignored "
                                           'when handler: is present — move that logic into the '
                                           'handler code'),
                                          ('warning',
                                           'command_handlers',
                                           "handler 'PWR (\\\\d)': set_state:/respond: are ignored "
                                           'when handler: is present — move that logic into the '
                                           'handler code'),
                                          ('warning',
                                           'command_handlers',
                                           "handler '': set_state:/respond: are ignored when "
                                           'handler: is present — move that logic into the handler '
                                           'code'),
                                          ('warning',
                                           'command_handlers',
                                           "handler 'LVL': set_state:/respond: are ignored when "
                                           'handler: is present — move that logic into the handler '
                                           'code')],
 'handler_code_a_bare_return': [('error',
                                 'handler_syntax',
                                 "handler for 'PWR\\\\?' has a Python syntax error (line 1): "
                                 "'return' outside function — the simulator will skip this "
                                 'handler')],
 'handler_code_compiles': [],
 'handler_entries_not_mappings': [('error',
                                   'command_handlers',
                                   "command_handlers entries must be mappings, got: 'PWR?'"),
                                  ('error',
                                   'command_handlers',
                                   'command_handlers entries must be mappings, got: 5'),
                                  ('error',
                                   'command_handlers',
                                   'command_handlers entries must be mappings, got: None'),
                                  ('error',
                                   'command_handlers',
                                   "command_handlers entries must be mappings, got: ['x']")],
 'handler_syntax_errors_labelled': [('error',
                                     'handler_syntax',
                                     "handler for '/ch/1' has a Python syntax error (line 1): '(' "
                                     'was never closed — the simulator will skip this handler'),
                                    ('error',
                                     'handler_syntax',
                                     "handler for 'PWR\\\\?' has a Python syntax error (line 1): "
                                     'invalid syntax — the simulator will skip this handler'),
                                    ('error',
                                     'handler_syntax',
                                     "handler for 'LVL' has a Python syntax error (line 1): "
                                     "expected ':' — the simulator will skip this handler"),
                                    ('error',
                                     'handler_syntax',
                                     "handler for '?' has a Python syntax error (line 1): '(' was "
                                     'never closed — the simulator will skip this handler'),
                                    ('error',
                                     'handler_syntax',
                                     "handler for '?' has a Python syntax error (line 1): invalid "
                                     'syntax — the simulator will skip this handler'),
                                    ('error',
                                     'handler_syntax',
                                     "handler for 'SRC' has a Python syntax error (line 1): "
                                     'invalid syntax — the simulator will skip this handler')],
 'handlers_both_passes_in_order': [('error',
                                    'handler_syntax',
                                    "handler for 'A' has a Python syntax error (line 1): '(' was "
                                    'never closed — the simulator will skip this handler'),
                                   ('error',
                                    'command_handlers',
                                    "command_handlers entries must be mappings, got: 'bad'"),
                                   ('error',
                                    'handler_syntax',
                                    "handler for 'B (\\\\d)' has a Python syntax error (line 1): "
                                    "'(' was never closed — the simulator will skip this handler"),
                                   ('warning',
                                    'command_handlers',
                                    "handler 'A': set_state:/respond: are ignored when handler: is "
                                    'present — move that logic into the handler code'),
                                   ('error',
                                    'command_handlers',
                                    "invalid regex in handler pattern '(': missing ), unterminated "
                                    'subpattern at position 0 — the simulator will skip this '
                                    'handler'),
                                   ('warning',
                                    'command_handlers',
                                    "handler 'B (\\\\d)': set_state:/respond: are ignored when "
                                    'handler: is present — move that logic into the handler code'),
                                   ('error',
                                    'set_state',
                                    "handler 'C': template '{state.nope}{1}' references capture "
                                    'group {1} but the pattern has only 0 group(s) — the '
                                    'placeholder stays literal at runtime'),
                                   ('warning',
                                    'set_state',
                                    "handler 'C': template '{state.nope}{1}' references "
                                    '{state.nope} which is not a state key — the placeholder stays '
                                    'literal at runtime')],
 'labels_missing_and_present': [('warning',
                                 'controls',
                                 "controls[0] (toggle): missing 'label' — the panel shows "
                                 "'undefined'"),
                                ('warning',
                                 'controls',
                                 "controls[1] (indicator): missing 'label' — the panel shows "
                                 "'undefined'"),
                                ('warning',
                                 'controls',
                                 "controls[2] (group): missing 'label' — the panel shows "
                                 "'undefined'")],
 'machine_bare': [('error', 'state_machine', "state_machine 'pwr' needs a non-empty 'states' list"),
                  ('error', 'state_machine', "state_machine 'pwr' needs an 'initial' state"),
                  ('error', 'state_machine', "state_machine 'pwr' needs a 'transitions' list")],
 'machine_initial_null': [('error',
                           'state_machine',
                           "state_machine 'pwr' needs an 'initial' state")],
 'machine_initial_off_list': [('error',
                               'state_machine',
                               "state_machine 'pwr' initial 'warm' is not one of ['off', 'on']")],
 'machine_initial_off_list_states_empty': [('error',
                                            'state_machine',
                                            "state_machine 'pwr' needs a non-empty 'states' list")],
 'machine_not_a_mapping': [('error', 'state_machine', "state_machine 'pwr' must be a mapping"),
                           ('error', 'state_machine', "state_machine 'lamp' must be a mapping")],
 'machine_states_empty': [('error',
                           'state_machine',
                           "state_machine 'pwr' needs a non-empty 'states' list")],
 'machine_states_text': [('error',
                          'state_machine',
                          "state_machine 'pwr' needs a non-empty 'states' list")],
 'machine_transitions_a_mapping': [('error',
                                    'state_machine',
                                    "state_machine 'pwr' needs a 'transitions' list")],
 'machine_transitions_missing': [('error',
                                  'state_machine',
                                  "state_machine 'pwr' needs a 'transitions' list")],
 'machine_transitions_null_and_more_machines': [('error',
                                                 'state_machine',
                                                 "state_machine 'pwr' needs a 'transitions' list"),
                                                ('error',
                                                 'state_machine',
                                                 "state_machine 'lamp' transition 0 'to' state 'c' "
                                                 "is not one of ['a', 'b']")],
 'machines_a_list': [('error', 'state_machine', 'simulator.state_machines must be a mapping')],
 'machines_absent': [],
 'machines_empty': [],
 'machines_empty_list': [],
 'machines_empty_text': [],
 'machines_null': [],
 'machines_text': [('error', 'state_machine', 'simulator.state_machines must be a mapping')],
 'matrix_pattern_not_text': [],
 'matrix_pattern_repeats_output': [],
 'matrix_pattern_unknown_key': [('warning',
                                 'controls',
                                 "controls[0] (matrix): state_pattern 'xpt.1.src' is not a state "
                                 'variable or initial_state key — the control shows nothing and '
                                 'writes to a key nothing reads')],
 'matrix_pattern_without_output': [('warning',
                                    'controls',
                                    "controls[0] (matrix): state_pattern 'route' has no {output} "
                                    'placeholder — every output binds the same key'),
                                   ('warning',
                                    'controls',
                                    "controls[1] (matrix): state_pattern 'route.{out}' has no "
                                    '{output} placeholder — every output binds the same key')],
 'matrix_sizes_bad': [('error',
                       'controls',
                       "controls[0] (matrix): 'inputs' must be a positive integer, got: 0"),
                      ('error',
                       'controls',
                       "controls[0] (matrix): 'outputs' must be a positive integer, got: -1"),
                      ('error',
                       'controls',
                       "controls[1] (matrix): 'inputs' must be a positive integer, got: '4'"),
                      ('error',
                       'controls',
                       "controls[1] (matrix): 'outputs' must be a positive integer, got: 2.0"),
                      ('error',
                       'controls',
                       "controls[2] (matrix): 'outputs' must be a positive integer, got: [2]")],
 'matrix_sizes_booleans': [('error',
                            'controls',
                            "controls[0] (matrix): 'outputs' must be a positive integer, got: "
                            'False')],
 'meters_channels_bad': [('error',
                          'controls',
                          "controls[0] (meters): 'channels' must be a positive integer, got: 0"),
                         ('error',
                          'controls',
                          "controls[1] (meters): 'channels' must be a positive integer, got: '8'"),
                         ('error',
                          'controls',
                          "controls[2] (meters): 'channels' must be a positive integer, got: "
                          'False')],
 'meters_mute_pattern_only_bad': [('warning',
                                   'controls',
                                   "controls[0] (meters): mute_pattern 'mute' has no {ch} "
                                   'placeholder — every channel binds the same key')],
 'meters_patterns_not_text': [],
 'meters_patterns_unknown': [('warning',
                              'controls',
                              "controls[0] (meters): key_pattern 'lvl.1' is not a state variable "
                              'or initial_state key — the control shows nothing and writes to a '
                              'key nothing reads'),
                             ('warning',
                              'controls',
                              "controls[0] (meters): mute_pattern 'mt.1' is not a state variable "
                              'or initial_state key — the control shows nothing and writes to a '
                              'key nothing reads'),
                             ('warning',
                              'controls',
                              "controls[1] (meters): mute_pattern 'mt.1' is not a state variable "
                              'or initial_state key — the control shows nothing and writes to a '
                              'key nothing reads')],
 'meters_patterns_without_ch': [('warning',
                                 'controls',
                                 "controls[0] (meters): key_pattern 'meter' has no {ch} "
                                 'placeholder — every channel binds the same key'),
                                ('warning',
                                 'controls',
                                 "controls[0] (meters): mute_pattern 'mute' has no {ch} "
                                 'placeholder — every channel binds the same key'),
                                ('warning',
                                 'controls',
                                 "controls[1] (meters): key_pattern 'meter.{channel}' has no {ch} "
                                 'placeholder — every channel binds the same key')],
 'presets_count_and_names_bad': [('error',
                                  'controls',
                                  "controls[0] (presets): 'count' must be an integer"),
                                 ('error',
                                  'controls',
                                  "controls[0] (presets): 'names' must be a list"),
                                 ('error',
                                  'controls',
                                  "controls[1] (presets): 'count' must be an integer"),
                                 ('error',
                                  'controls',
                                  "controls[1] (presets): 'names' must be a list")],
 'presets_count_null': [],
 'presets_names_only': [],
 'presets_neither_count_nor_names': [('warning',
                                      'controls',
                                      "controls[0] (presets): neither 'count' nor 'names' given — "
                                      'the panel renders no preset buttons')],
 'respond_names_a_boolean': [('error',
                              'type_consistency',
                              "Handler uses {state.power} in respond: template, but 'power' is "
                              "boolean. Python's str(True) produces 'True' not 'true'. Use a "
                              'script handler with str(state["power"]).lower() instead.'),
                             ('error',
                              'type_consistency',
                              "Handler uses {state.power} in respond: template, but 'power' is "
                              "boolean. Python's str(True) produces 'True' not 'true'. Use a "
                              'script handler with str(state["power"]).lower() instead.'),
                             ('error',
                              'type_consistency',
                              "Handler uses {state.power} in respond: template, but 'power' is "
                              "boolean. Python's str(True) produces 'True' not 'true'. Use a "
                              'script handler with str(state["power"]).lower() instead.'),
                             ('error',
                              'command_handlers',
                              "command_handlers entries must be mappings, got: 'bad'"),
                             ('warning',
                              'command_handlers',
                              "handler 'SRC\\\\?': set_state:/respond: are ignored when handler: "
                              'is present — move that logic into the handler code'),
                             ('warning',
                              'set_state',
                              "handler 'X': template '{state.nope}' references {state.nope} which "
                              'is not a state key — the placeholder stays literal at runtime')],
 'respond_names_a_boolean_dotted': [('warning',
                                     'set_state',
                                     "handler 'PWR\\\\?': template 'P{state.power.1} "
                                     "{state.power_x} {state.level}' references {state.power_x} "
                                     'which is not a state key — the placeholder stays literal at '
                                     'runtime')],
 'respond_names_a_dotted_boolean': [('error',
                                     'type_consistency',
                                     "Handler uses {state.power} in respond: template, but 'power' "
                                     "is boolean. Python's str(True) produces 'True' not 'true'. "
                                     'Use a script handler with str(state["power"]).lower() '
                                     'instead.')],
 'select_options_empty': [('warning', 'controls', "controls[0] (select): 'options' is empty")],
 'select_options_not_a_list': [('error',
                                'controls',
                                "controls[0] (select): 'options' must be a list"),
                               ('error',
                                'controls',
                                "controls[1] (select): 'options' must be a list")],
 'select_options_null': [],
 'slider_bounds_not_numbers': [('error',
                                'controls',
                                "controls[0] (slider): 'min' must be a number, got: '0'"),
                               ('error',
                                'controls',
                                "controls[0] (slider): 'max' must be a number, got: [100]"),
                               ('error',
                                'controls',
                                "controls[1] (slider): 'max' must be a number, got: 'high'")],
 'slider_bounds_odd_numbers': [],
 'template_capture_refs': [('error',
                            'set_state',
                            "handler 'PWR (\\\\d) (\\\\d)': template 'P{0}{1}{2}{3}{10}' "
                            'references capture group {3} but the pattern has only 2 group(s) — '
                            'the placeholder stays literal at runtime'),
                           ('error',
                            'set_state',
                            "handler 'PWR (\\\\d) (\\\\d)': template 'P{0}{1}{2}{3}{10}' "
                            'references capture group {10} but the pattern has only 2 group(s) — '
                            'the placeholder stays literal at runtime'),
                           ('error',
                            'set_state',
                            "handler 'LVL': template '{1}' references capture group {1} but the "
                            'pattern has only 0 group(s) — the placeholder stays literal at '
                            'runtime'),
                           ('error',
                            'set_state',
                            "handler 'LVL': template 'L{1}' references capture group {1} but the "
                            'pattern has only 0 group(s) — the placeholder stays literal at '
                            'runtime'),
                           ('error',
                            'set_state',
                            "handler '(?:SRC) (\\\\w+)': template '{1}{2}' references capture "
                            'group {2} but the pattern has only 1 group(s) — the placeholder stays '
                            'literal at runtime')],
 'template_invalid_regex': [('error',
                             'command_handlers',
                             "invalid regex in handler pattern 'PWR (': missing ), unterminated "
                             'subpattern at position 4 — the simulator will skip this handler'),
                            ('error',
                             'command_handlers',
                             "invalid regex in handler pattern '[': unterminated character set at "
                             'position 0 — the simulator will skip this handler'),
                            ('error',
                             'command_handlers',
                             "invalid regex in handler pattern '*': nothing to repeat at position "
                             '0 — the simulator will skip this handler')],
 'template_match_used_when_receive_blank': [('error',
                                             'set_state',
                                             "handler 'PWR (\\\\d)': template 'P{2}' references "
                                             'capture group {2} but the pattern has only 1 '
                                             'group(s) — the placeholder stays literal at runtime'),
                                            ('error',
                                             'set_state',
                                             "handler 'LVL (\\\\d+) (\\\\d+)': template 'L{2}{3}' "
                                             'references capture group {3} but the pattern has '
                                             'only 2 group(s) — the placeholder stays literal at '
                                             'runtime')],
 'template_osc_entries_skipped': [],
 'template_receive_a_list': ('raises', 'TypeError', "unhashable type: 'list'"),
 'template_receive_a_number': ('raises',
                               'TypeError',
                               'first argument must be string or compiled pattern'),
 'template_refs_name_a_machine': [],
 'template_refs_with_nothing_known': [('error',
                                       'set_state',
                                       "handler 'PWR (\\\\d)': template '{state.x}{4}' references "
                                       'capture group {4} but the pattern has only 1 group(s) — '
                                       'the placeholder stays literal at runtime')],
 'template_respond_empty_not_text': [('error',
                                      'set_state',
                                      "handler 'PWR (\\\\d)': template '{2}' references capture "
                                      'group {2} but the pattern has only 1 group(s) — the '
                                      'placeholder stays literal at runtime'),
                                     ('warning',
                                      'set_state',
                                      "handler 'LVL': set_state target 'nope' is not a state "
                                      'variable or initial_state key — the value will be written '
                                      'to a key nothing reads')],
 'template_respond_not_text': ('raises',
                               'TypeError',
                               "expected string or bytes-like object, got 'int'"),
 'template_set_state_not_a_mapping': [],
 'template_set_state_targets': [('warning',
                                 'set_state',
                                 "handler 'PWR (\\\\d)': set_state target 'nope' is not a state "
                                 'variable or initial_state key — the value will be written to a '
                                 'key nothing reads')],
 'template_set_state_values_not_text': [('error',
                                         'set_state',
                                         'handler \'PWR (\\\\d)\': template "[\'{3}\']" references '
                                         'capture group {3} but the pattern has only 1 group(s) — '
                                         'the placeholder stays literal at runtime')],
 'template_state_refs': [('error',
                          'type_consistency',
                          "Handler uses {state.power} in respond: template, but 'power' is "
                          "boolean. Python's str(True) produces 'True' not 'true'. Use a script "
                          'handler with str(state["power"]).lower() instead.'),
                         ('warning',
                          'set_state',
                          "handler 'PWR\\\\?': template 'P{state.power} L{state.level} "
                          "X{state.nope}' references {state.nope} which is not a state key — the "
                          'placeholder stays literal at runtime'),
                         ('warning',
                          'set_state',
                          "handler 'SRC\\\\?': template 'S{state.a.b} {state.zone-1} {state.} "
                          "{state.nope}{state.nope}' references {state.nope} which is not a state "
                          'key — the placeholder stays literal at runtime'),
                         ('warning',
                          'set_state',
                          "handler 'SRC\\\\?': template 'S{state.a.b} {state.zone-1} {state.} "
                          "{state.nope}{state.nope}' references {state.nope} which is not a state "
                          'key — the placeholder stays literal at runtime'),
                         ('warning',
                          'set_state',
                          "handler 'LVL (\\\\d+)': template '{state.gone}{1}' references "
                          '{state.gone} which is not a state key — the placeholder stays literal '
                          'at runtime')],
 'template_without_a_pattern': [('warning',
                                 'command_handlers',
                                 'handler with respond:/set_state: has no receive: or match: '
                                 'pattern — it can never fire'),
                                ('warning',
                                 'command_handlers',
                                 'handler with respond:/set_state: has no receive: or match: '
                                 'pattern — it can never fire'),
                                ('warning',
                                 'command_handlers',
                                 'handler with respond:/set_state: has no receive: or match: '
                                 'pattern — it can never fire'),
                                ('warning',
                                 'command_handlers',
                                 'handler with respond:/set_state: has no receive: or match: '
                                 'pattern — it can never fire')],
 'template_without_a_pattern_carrying_refs': [('warning',
                                               'command_handlers',
                                               'handler with respond:/set_state: has no receive: '
                                               'or match: pattern — it can never fire'),
                                              ('warning',
                                               'command_handlers',
                                               'handler with respond:/set_state: has no receive: '
                                               'or match: pattern — it can never fire')],
 'transition_after_seconds': [('error',
                               'state_machine',
                               "state_machine 'pwr' transition 0 'after_seconds' must be a number"),
                              ('error',
                               'state_machine',
                               "state_machine 'pwr' transition 1 'after_seconds' must be a "
                               'number')],
 'transition_can_never_fire': [('warning',
                                'state_machine',
                                "state_machine 'pwr' transition 0 has neither 'trigger' nor "
                                "'after_seconds' — it can never fire")],
 'transition_complete': [],
 'transition_duplicates': [('warning',
                            'state_machine',
                            "state_machine 'pwr' transition 1 duplicates an earlier transition for "
                            "from 'off' trigger 'power_on' — the first listed wins, this one is "
                            'unreachable'),
                           ('warning',
                            'state_machine',
                            "state_machine 'pwr' transition 2 duplicates an earlier transition for "
                            "from 'off' trigger 'power_on' — the first listed wins, this one is "
                            'unreachable'),
                           ('warning',
                            'state_machine',
                            "state_machine 'pwr' transition 4 duplicates an earlier transition for "
                            "from 'on' trigger '*' — the first listed wins, this one is "
                            'unreachable'),
                           ('error',
                            'state_machine',
                            "state_machine 'pwr' transition 5 is missing 'from'"),
                           ('warning',
                            'state_machine',
                            "state_machine 'pwr' transition 6 duplicates an earlier transition for "
                            "from 'None' trigger 'power_off' — the first listed wins, this one is "
                            'unreachable'),
                           ('error',
                            'state_machine',
                            "state_machine 'pwr' transition 6 is missing 'from'"),
                           ('warning',
                            'state_machine',
                            "state_machine 'pwr' transition 8 duplicates an earlier transition for "
                            "from 'off' trigger 'None' — the first listed wins, this one is "
                            'unreachable')],
 'transition_duplicates_per_machine': [('warning',
                                        'state_machine',
                                        "state_machine 'lamp' transition 1 duplicates an earlier "
                                        "transition for from 'off' trigger 'power_on' — the first "
                                        'listed wins, this one is unreachable')],
 'transition_everything_wrong': [('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 0 'from' state 'warm' is not one "
                                  "of ['off', 'on']"),
                                 ('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 0 'to' state 'cold' is not one "
                                  "of ['off', 'on']"),
                                 ('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 0 'after_seconds' must be a "
                                  'number'),
                                 ('warning',
                                  'state_machine',
                                  "state_machine 'pwr' transition 0 trigger 'nope' matches no "
                                  'command name — it will never fire'),
                                 ('warning',
                                  'state_machine',
                                  "state_machine 'pwr' transition 1 duplicates an earlier "
                                  "transition for from 'warm' trigger 'nope' — the first listed "
                                  'wins, this one is unreachable'),
                                 ('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 1 'from' state 'warm' is not one "
                                  "of ['off', 'on']"),
                                 ('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 1 'to' state 'cold' is not one "
                                  "of ['off', 'on']"),
                                 ('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 1 'after_seconds' must be a "
                                  'number'),
                                 ('warning',
                                  'state_machine',
                                  "state_machine 'pwr' transition 1 trigger 'nope' matches no "
                                  'command name — it will never fire'),
                                 ('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 2 is missing 'from'"),
                                 ('error',
                                  'state_machine',
                                  "state_machine 'pwr' transition 2 is missing 'to'"),
                                 ('warning',
                                  'state_machine',
                                  "state_machine 'pwr' transition 2 has neither 'trigger' nor "
                                  "'after_seconds' — it can never fire")],
 'transition_from_a_list_beside_a_trigger': ('raises', 'TypeError', "unhashable type: 'list'"),
 'transition_from_a_list_without_a_trigger': [('error',
                                               'state_machine',
                                               "state_machine 'pwr' transition 0 'from' state "
                                               "'['off']' is not one of ['off', 'on']"),
                                              ('error',
                                               'state_machine',
                                               "state_machine 'pwr' transition 0 'to' state "
                                               "'['on']' is not one of ['off', 'on']")],
 'transition_from_missing_and_off_list': [('error',
                                           'state_machine',
                                           "state_machine 'pwr' transition 0 is missing 'from'"),
                                          ('error',
                                           'state_machine',
                                           "state_machine 'pwr' transition 1 'from' state 'warm' "
                                           "is not one of ['off', 'on']"),
                                          ('error',
                                           'state_machine',
                                           "state_machine 'pwr' transition 2 is missing 'from'")],
 'transition_not_a_mapping': [('error',
                               'state_machine',
                               "state_machine 'pwr' transition 0 must be a mapping"),
                              ('error',
                               'state_machine',
                               "state_machine 'pwr' transition 1 must be a mapping"),
                              ('error',
                               'state_machine',
                               "state_machine 'pwr' transition 2 must be a mapping")],
 'transition_reject_without_to': [('error',
                                   'state_machine',
                                   "state_machine 'pwr' transition 3 is missing 'to'")],
 'transition_to_missing_and_off_list': [('error',
                                         'state_machine',
                                         "state_machine 'pwr' transition 0 is missing 'to'"),
                                        ('error',
                                         'state_machine',
                                         "state_machine 'pwr' transition 1 'to' state 'warm' is "
                                         "not one of ['off', 'on']"),
                                        ('error',
                                         'state_machine',
                                         "state_machine 'pwr' transition 2 is missing 'to'")],
 'transition_trigger_names_no_command': [('warning',
                                          'state_machine',
                                          "state_machine 'pwr' transition 0 trigger 'power_toggle' "
                                          'matches no command name — it will never fire'),
                                         ('warning',
                                          'state_machine',
                                          "state_machine 'pwr' transition 3 trigger '7' matches no "
                                          'command name — it will never fire')],
 'transition_trigger_with_no_commands': [],
 'transitions_states_text': [('error',
                              'state_machine',
                              "state_machine 'pwr' needs a non-empty 'states' list")]}
