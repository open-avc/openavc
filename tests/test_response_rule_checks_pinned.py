"""Pins for a response rule's load-time checks, through the public door.

Each case is a list of response rules on a driver that declares two child
types (``zone``, integer ids; ``mic``, string ids) and one malformed one. The
expected value is every error ``validate_driver_issues`` reports for the whole
definition, in order, each with the section tag the Driver Builder anchors it
to. The cases reach every check a rule gets whatever its form (throttle,
``only_when``, ``require``, ``after_json``, where ``child_list`` may sit, a
value map written into ``set:``), then each form's own: an OSC address rule, a
``json: true`` rule and a text (``match:``) rule, each with ``child_set:``
entries of every shape, and a text rule's ``child_list:``.

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

CHILD_TYPES = {
    "zone": {
        "id_format": {"type": "integer", "min": 1, "max": 8},
        "state_variables": {"level": {"type": "number", "label": "Level"}},
        "instances": {"count": 4},
    },
    "mic": {
        "id_format": {"type": "string"},
        "state_variables": {"gain": {"type": "number", "label": "Gain"}},
        "instances": {"ids": ["a", "b"]},
    },
    "bare": "not a mapping",
}

OSC = "/zone/*/level"
TEXT = r"ZONE(\d) LVL (\d+)"
LIST_MATCH = r"LEVELS (\d) (.+)"


def osc(*entries, address=OSC):
    return {"address": address, "child_set": list(entries)}


def js(*entries, **rule):
    return {"json": True, "child_set": list(entries), **rule}


def tx(*entries, match=TEXT):
    return {"match": match, "child_set": list(entries)}


def lst(match=LIST_MATCH, **spec):
    full = {"type": "zone", "element": r"(\d+)", "separator": ",", "state": {"level": "$1"}}
    full.update(spec)
    return {"match": match, "child_list": {k: v for k, v in full.items() if v is not DROP}}


DROP = object()

CASES = {
    # --- every rule --------------------------------------------------------
    "not_a_mapping": ["x", 5, None],
    "throttle_values": [
        {"match": "A", "throttle": True},
        {"match": "A", "throttle": "1"},
        {"match": "A", "throttle": 0},
        {"match": "A", "throttle": -2},
        {"match": "A", "throttle": 0.5},
        {"match": "A", "throttle": 3},
        {"match": "A", "throttle": None},
    ],
    "only_when_shapes": [
        {"match": "A", "only_when": "x"},
        {"match": "A", "only_when": []},
        {"match": "A", "only_when": {"any": []}},
        {"match": "A", "only_when": {"all": "x"}},
        {"match": "A", "only_when": {"any": ["x", {}], "all": [{"key": "k", "operator": "zz"}]}},
        {"match": "A", "only_when": {"key": "", "operator": "nope"}},
        {"match": "A", "only_when": {"key": "device.x.power", "operator": "eq", "value": "on"}},
        {"match": "A", "only_when": {"all": [{"key": "a"}], "extra": 1}},
    ],
    "require_shapes": [
        {"match": "A", "require": "k"},
        {"json": True, "set": {"a": "a"}, "require": ""},
        {"json": True, "set": {"a": "a"}, "require": "  "},
        {"json": True, "set": {"a": "a"}, "require": []},
        {"json": True, "set": {"a": "a"}, "require": ["a", ""]},
        {"json": True, "set": {"a": "a"}, "require": [1]},
        {"json": True, "set": {"a": "a"}, "require": 5},
        {"json": True, "set": {"a": "a"}, "require": ["a", "b"]},
        {"json": True, "set": {"a": "a"}, "require": "a"},
        {"match": "A", "require": None},
    ],
    "after_json_no_json_rule": [
        {"match": "A", "after_json": True},
        {"match": "A", "after_json": False},
    ],
    "after_json_with_json_rule": [
        {"json": True, "set": {"a": "a"}, "after_json": True},
        {"address": "/a", "after_json": True},
        {"match": "A", "after_json": True},
        {"match": "A", "after_json": 1},
    ],
    "child_list_placement": [
        {"json": True, "set": {"a": "a"}, "child_list": {}},
        {"address": "/a", "child_list": {}},
        {"match": "A", "child_list": None, "child_set": []},
        {"match": "A", "child_list": {"type": "zone"}, "child_set": None},
    ],
    "capture_set_shape": [
        {"match": "A(\\d)", "set": {"a": {"group": 1, "map": {}}, "b": [1], "c": "$1", "d": 5}},
        {"address": "/a", "set": {"a": {"arg": 0}}},
        {"json": True, "set": {"a": {"key": "x"}}},
        {"match": "A", "set": "not a map"},
    ],
    "common_checks_in_order": [
        {
            "match": "A",
            "throttle": 0,
            "only_when": "x",
            "require": 5,
            "after_json": True,
            "child_list": {},
            "child_set": [],
            "set": {"a": {}},
        },
    ],
    # --- OSC rules -----------------------------------------------------------
    "osc_address": [
        {"address": "a/b"},
        {"address": 5},
        {"address": None},
        {"address": "/ok"},
    ],
    "osc_child_set_container": [
        {"address": OSC, "child_set": "x"},
        {"address": OSC, "child_set": []},
        {"address": "bad", "child_set": []},
        {"address": OSC, "child_set": None},
    ],
    "osc_child_set_entry_type": [
        osc("x", {"type": "nope"}, {"type": 5}, {"type": "bare", "id": 1, "state": {"online": True}}),
    ],
    "osc_child_set_id": [
        osc(
            {"type": "zone", "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": True}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": "1"}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": -1}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 3}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 1}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": "$1", "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": 2, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": "x", "state": {"level": {"arg": 0}}},
        ),
    ],
    "osc_child_set_id_no_segments": [
        osc({"type": "zone", "id": {"segment": 9}, "state": {"level": {"arg": 0}}}, address="/"),
        osc({"type": "zone", "id": {"segment": 9}, "state": {"level": {"arg": 0}}}, address=5),
    ],
    "osc_child_set_id_map": [
        osc(
            {"type": "zone", "id": {"segment": 1, "map": "x"}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 1, "map": {}}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 1, "map": {"a": True}}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 1, "map": {"a": [1]}}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 1, "map": {(1, 2): 1}}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 1, "map": {"a": "x", "b": "y"}}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 1, "map": {"a": 1, "b": "2", "c": 3.5}}, "state": {"level": {"arg": 0}}},
            {"type": "mic", "id": {"segment": 1, "map": {"a": "x", "b": 2}}, "state": {"gain": {"arg": 0}}},
            {"type": "zone", "id": {"segment": "x", "map": {"a": "q"}}, "state": {"level": {"arg": 0}}},
        ),
    ],
    "osc_child_set_state": [
        osc(
            {"type": "zone", "id": 1},
            {"type": "zone", "id": 1, "state": {}},
            {"type": "zone", "id": 1, "state": "x"},
            {"type": "zone", "id": "$2"},
            {"type": "zone", "id": 1, "state": {"nope": {"arg": 0}, "online": {"value": True}}},
            {"type": "zone", "id": 1, "state": {"level": "$1"}},
            {"type": "zone", "id": 1, "state": {"level": {}}},
            {"type": "zone", "id": 1, "state": {"level": {"arg": True}}},
            {"type": "zone", "id": 1, "state": {"level": {"arg": -1}}},
            {"type": "zone", "id": 1, "state": {"level": {"arg": "0"}}},
            {"type": "zone", "id": 1, "state": {"level": {"value": 3}}},
            {"type": "zone", "id": 1, "state": {"level": 7, "label": "x"}},
            {"type": "zone", "id": 1, "state": {"level": {"arg": None, "value": 1}}},
            {"type": "zone", "id": 1, "state": {"nope": "$1"}},
        ),
    ],
    "osc_with_common_checks": [
        {"address": "x", "throttle": 0, "child_set": [{"type": "zone"}]},
    ],
    # --- json rules ----------------------------------------------------------
    "json_needs_something": [
        {"json": True},
        {"json": True, "set": "x", "mappings": "y"},
        {"json": True, "mappings": []},
        {"json": True, "child_set": None},
        {"json": 1, "set": {}},
    ],
    "json_contains": [
        {"json": True, "set": {
            "a": {"key": "x", "contains": None},
            "b": {"key": "x", "contains": [1]},
            "c": {"key": "x", "contains": {}},
            "d": {"key": "x", "contains": "y"},
            "e": "x",
        }},
        {"json": True, "mappings": [
            "x",
            {"key": "k", "state": "a", "contains": {}},
            {"key": "k", "state": "a", "contains": 1},
            {"key": "k", "state": "a"},
        ]},
    ],
    "json_child_set_container": [
        {"json": True, "child_set": "x"},
        {"json": True, "child_set": []},
        {"json": True, "set": {"a": {"contains": []}}, "child_set": []},
    ],
    "json_child_set_entry": [
        js("x", {"type": "nope"}, {"type": None}),
    ],
    "json_child_set_id": [
        js(
            {"type": "zone", "state": {"level": "a"}},
            {"type": "zone", "id": {"group": 1}, "state": {"level": "a"}},
            {"type": "zone", "id": "$1", "state": {"level": "a"}},
            {"type": "zone", "id": True, "state": {"level": "a"}},
            {"type": "zone", "id": 1.5, "state": {"level": "a"}},
            {"type": "zone", "id": [1], "state": {"level": "a"}},
            {"type": "zone", "id": "x", "state": {"level": "a"}},
            {"type": "zone", "id": " 3 ", "state": {"level": "a"}},
            {"type": "zone", "id": 2, "state": {"level": "a"}},
            {"type": "mic", "id": "x", "state": {"gain": "a"}},
            {"type": "mic", "id": 7, "state": {"gain": "a"}},
        ),
    ],
    "json_child_set_state": [
        js(
            {"type": "zone", "id": 1},
            {"type": "zone", "id": 1, "state": []},
            {"type": "zone", "id": "x"},
            {"type": "zone", "id": 1, "state": {"nope": "a", "online": "b.c"}},
            {"type": "zone", "id": 1, "state": {"level": {}}},
            {"type": "zone", "id": 1, "state": {"level": {"key": " "}}},
            {"type": "zone", "id": 1, "state": {"level": {"path": "a.b"}}},
            {"type": "zone", "id": 1, "state": {"level": {"key": 5, "path": "a"}}},
            {"type": "zone", "id": 1, "state": {"level": {"key": "a", "group": 1, "arg": 0}}},
            {"type": "zone", "id": 1, "state": {"level": {"key": "a", "contains": None}}},
            {"type": "zone", "id": 1, "state": {"level": {"key": "a", "type": "integer", "map": {}, "contains": "x"}}},
            {"type": "zone", "id": 1, "state": {"level": "$1"}},
            {"type": "zone", "id": 1, "state": {"level": ""}},
            {"type": "zone", "id": 1, "state": {"level": "  "}},
            {"type": "zone", "id": 1, "state": {"level": 5}},
            {"type": "zone", "id": 1, "state": {"level": None}},
            {"type": "zone", "id": 1, "state": {"nope": {"wrong": 1}}},
        ),
    ],
    "json_with_common_checks": [
        {"json": True, "throttle": -1, "only_when": "x", "require": [], "after_json": True,
         "child_list": {}, "set": {"a": {"contains": {}}}, "child_set": "x"},
    ],
    # --- text rules ----------------------------------------------------------
    "text_match": [
        {"set": {"a": "b"}},
        {"match": ""},
        {"match": None},
        {"match": "(a+)+$"},
        {"match": "("},
        {"match": "A"},
    ],
    "text_child_set_container": [
        {"match": TEXT, "child_set": "x"},
        {"match": TEXT, "child_set": []},
        {"match": TEXT, "child_set": None},
        {"match": TEXT},
        {"child_set": "x"},
    ],
    "text_child_set_entry": [
        tx("x", {"type": "nope"}, {"type": ["zone"]}, {"type": "bare", "id": "$1", "state": {"online": "$2"}}),
    ],
    "text_child_set_id": [
        tx(
            {"type": "zone", "state": {"level": "$2"}},
            {"type": "zone", "id": "$1", "state": {"level": "$2"}},
            {"type": "zone", "id": "$x", "state": {"level": "$2"}},
            {"type": "zone", "id": "$", "state": {"level": "$2"}},
            {"type": "zone", "id": "$0", "state": {"level": "$2"}},
            {"type": "zone", "id": "$3", "state": {"level": "$2"}},
            {"type": "zone", "id": 4, "state": {"level": "$2"}},
            {"type": "zone", "id": "lit", "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": "$1"}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": "$z"}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 2}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 9}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": -1}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": True}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": "1"}, "state": {"level": "$2"}},
            {"type": "zone", "id": {}, "state": {"level": "$2"}},
        ),
    ],
    "text_child_set_id_map": [
        tx(
            {"type": "zone", "id": {"group": 1, "map": []}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 1, "map": {}}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 1, "map": {"a": False}}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 1, "map": {"a": None}}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 1, "map": {1.5: 1}}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 1, "map": {"a": "one", "b": "two"}}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 1, "map": {"a": "1", 2: 2}}, "state": {"level": "$2"}},
            {"type": "mic", "id": {"group": 1, "map": {"a": "x"}}, "state": {"gain": "$2"}},
            {"type": "zone", "id": {"group": 0, "map": {"a": "x"}}, "state": {"level": "$2"}},
        ),
    ],
    "text_child_set_state": [
        tx(
            {"type": "zone", "id": "$1"},
            {"type": "zone", "id": "$1", "state": {}},
            {"type": "zone", "id": "$9"},
            {"type": "zone", "id": "$1", "state": {"nope": "$2", "online": "$1"}},
            {"type": "zone", "id": "$1", "state": {"level": "$5"}},
            {"type": "zone", "id": "$1", "state": {"level": "$q"}},
            {"type": "zone", "id": "$1", "state": {"level": {"group": 9}}},
            {"type": "zone", "id": "$1", "state": {"level": 3}},
            {"type": "zone", "id": "$1", "state": {"nope": "lit"}},
        ),
    ],
    "text_child_set_unbounded": [
        tx({"type": "zone", "id": "$9", "state": {"level": "$7"}}, match="{prefix}(\\d"),
        tx({"type": "zone", "id": "$9", "state": {"level": "$7"}}, match=""),
        {"child_set": [{"type": "zone", "id": "$9", "state": {"level": "$7"}}]},
        tx({"type": "zone", "id": {"group": 9}, "state": {"level": "$7"}}, match="(a+)+$"),
    ],
    "text_with_common_checks": [
        {"match": "(", "throttle": "x", "require": "k", "child_set": [{"type": "zone", "id": "$0"}],
         "set": {"a": [1]}},
    ],
    # --- child_list ----------------------------------------------------------
    "child_list_container": [
        {"match": LIST_MATCH, "child_list": "x"},
        {"match": LIST_MATCH, "child_list": []},
        {"match": LIST_MATCH, "child_list": {"type": "nope"}},
        {"match": LIST_MATCH, "child_list": {"type": 5}},
        {"match": LIST_MATCH, "child_list": {"type": "bare"}},
        {"match": LIST_MATCH, "child_list": None},
    ],
    "child_list_group": [
        lst(),
        lst(group=2),
        lst(group="$2"),
        lst(group="$3"),
        lst(group=0),
        lst(group=True),
        lst(group="2"),
        lst(group="$x"),
        lst(group=None),
        lst(match="{p}(", group=9),
        lst(match=5, group=9),
        lst(match="", group=9),
    ],
    "child_list_element": [
        lst(element=DROP),
        lst(element=""),
        lst(element=5),
        lst(element="("),
        lst(element="(a+)+$"),
        lst(element="(\\d*)"),
        lst(element="x?"),
    ],
    "child_list_separator": [
        lst(separator=""),
        lst(separator=5),
        lst(separator=None),
        lst(separator=DROP),
    ],
    "child_list_id": [
        lst(id="$1"),
        lst(id="$2"),
        lst(id=1),
        lst(id="x"),
        lst(id={"group": 1}),
        lst(id={"group": 4}),
        lst(id={"group": "$1", "map": {"a": "1", "b": 2}}),
        lst(id={"group": 1, "map": {}}),
        lst(id={"group": 1, "map": "x"}),
        lst(id={"group": 1, "map": {"a": True}}),
        lst(id={"group": 1, "map": {"a": [1]}}),
        lst(id={"group": 1, "map": {"a": "one", "b": "two"}}),
        lst(type="mic", id={"group": 1, "map": {"a": "one"}}, state={"gain": "$1"}),
        lst(id={}),
        lst(id="$1", start=3),
        lst(id={"group": 1}, start="$1"),
    ],
    "child_list_start": [
        lst(start=1),
        lst(start=5),
        lst(start="$1"),
        lst(start="$4"),
        lst(start="$0"),
        lst(start="1"),
        lst(start=True),
        lst(start=1.5),
        lst(start=None),
        lst(type="mic", state={"gain": "$1"}),
        lst(type="mic", state={"gain": "$1"}, start="$1"),
        lst(type="mic", id="$1", state={"gain": "$1"}),
    ],
    "child_list_state": [
        lst(state=DROP),
        lst(state={}),
        lst(state="x"),
        lst(state={"nope": "$1", "online": "$1"}),
        lst(state={"level": "$2"}),
        lst(state={"level": "$0"}),
        lst(state={"level": "$x"}),
        lst(state={"level": {"group": 1}}),
        lst(state={"level": {"group": 3}}),
        lst(state={"level": {"group": "x"}}),
        lst(state={"level": {"value": 1}}),
        lst(state={"level": {}}),
        lst(state={"level": {"other": 1}}),
        lst(state={"level": 4, "label": "x"}),
        lst(element="(", state={"level": "$9"}),
        lst(element="(a+)+$", state={"level": "$9"}),
    ],
    "child_list_everything_wrong": [
        lst(match="(", group="$9", element="(", separator="", id={"group": 9, "map": {"a": "x"}},
            start=2, state={"nope": "$9"}),
    ],
    "form_precedence": [
        {"address": "x", "json": True, "match": "("},
        {"json": True, "match": "(", "set": {}},
    ],
    "json_contains_set_before_mappings": [
        {"json": True, "set": {"a": {"key": "x", "contains": []}},
         "mappings": [{"key": "k", "state": "a", "contains": {}}]},
    ],
    "throttle_nan": [{"match": "A", "throttle": float("nan")}],
    # --- several rules -------------------------------------------------------
    "several_rules_each_tagged": [
        {"match": "A", "throttle": 0},
        "x",
        {"address": "x"},
        {"json": True},
        {"match": TEXT, "child_set": [{"type": "nope"}]},
    ],
}

# Extra top-level keys for a case, beside the shared child types.
EXTRA = {
    "responses_not_a_list": {"responses": {"a": 1}},
    "responses_absent": {"responses": None},
    "no_child_types": {"child_entity_types": None, "responses": [
        osc({"type": "zone", "id": 1, "state": {"level": {"arg": 0}}}),
        js({"type": "zone", "id": 1, "state": {"level": "a"}}),
        tx({"type": "zone", "id": "$1", "state": {"level": "$2"}}),
        lst(),
    ]},
    "child_list_other_id_kind": {
        "child_entity_types": {
            "odd": {"id_format": {"type": "uuid"},
                    "state_variables": {"level": {"type": "number", "label": "Level"}},
                    "instances": {"count": 2}},
        },
        "responses": [
            lst(type="odd"),
            lst(type="odd", id={"group": 1, "map": {"a": "one"}}),
        ],
    },
    "child_types_not_a_mapping": {"child_entity_types": ["zone"], "responses": [
        tx({"type": "zone", "id": "$1", "state": {"level": "$2"}}),
    ]},
}


def _definition(case: str) -> dict[str, Any]:
    over: dict[str, Any] = {"child_entity_types": CHILD_TYPES}
    if case in EXTRA:
        over.update(EXTRA[case])
    else:
        over["responses"] = CASES[case]
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"noop": {"send": "NOOP\r"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        **over,
    }


EXPECTED: dict[str, list[tuple[str, str]]] = {'not_a_mapping': [('responses[0]', 'Response 0: must be a mapping'),
                   ('responses[1]', 'Response 1: must be a mapping'),
                   ('responses[2]', 'Response 2: must be a mapping'),
                   ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'throttle_values': [('responses[0]', 'Response 0: throttle must be a positive number of seconds'),
                     ('responses[1]', 'Response 1: throttle must be a positive number of seconds'),
                     ('responses[2]', 'Response 2: throttle must be a positive number of seconds'),
                     ('responses[3]', 'Response 3: throttle must be a positive number of seconds'),
                     ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'only_when_shapes': [('responses[0]', "Response 0: 'only_when' must be a mapping"),
                      ('responses[1]', "Response 1: 'only_when' must be a mapping"),
                      ('responses[2]', 'Response 2: only_when.any must be a non-empty list'),
                      ('responses[3]', 'Response 3: only_when.all must be a non-empty list'),
                      ('responses[4]', 'Response 4: only_when.any[0]: condition must be a mapping'),
                      ('responses[4]',
                       "Response 4: only_when.any[1]: condition missing 'key' (state key string)"),
                      ('responses[4]', "Response 4: only_when.all[0]: unknown operator 'zz'"),
                      ('responses[5]',
                       "Response 5: only_when: condition missing 'key' (state key string)"),
                      ('responses[5]', "Response 5: only_when: unknown operator 'nope'"),
                      ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'require_shapes': [('responses[0]', 'Response 0: require only applies to json: true responses'),
                    ('responses[1]', 'Response 1: require must name a JSON key'),
                    ('responses[2]', 'Response 2: require must name a JSON key'),
                    ('responses[3]',
                     'Response 3: require list entries must be non-empty JSON key names'),
                    ('responses[4]',
                     'Response 4: require list entries must be non-empty JSON key names'),
                    ('responses[5]',
                     'Response 5: require list entries must be non-empty JSON key names'),
                    ('responses[6]',
                     'Response 6: require must be a JSON key name or a list of them'),
                    ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'after_json_no_json_rule': [('responses[0]',
                              'Response 0: after_json has no effect: no response rule in this '
                              'driver is json: true'),
                             ('child_entity_types.bare',
                              'child_entity_types.bare: must be a mapping')],
 'after_json_with_json_rule': [('responses[0]',
                                'Response 0: after_json applies to regex rules; a json: true rule '
                                'already reads the body'),
                               ('responses[1]',
                                'Response 1: after_json applies to regex rules; an OSC address '
                                'rule never runs after a json rule'),
                               ('child_entity_types.bare',
                                'child_entity_types.bare: must be a mapping')],
 'child_list_placement': [('responses[0]',
                           'Response 0: child_list works on text rules (match:) only; a json: true '
                           'rule routes to children with child_set'),
                          ('responses[1]',
                           'Response 1: child_list works on text rules (match:) only; an OSC rule '
                           'routes to children with child_set'),
                          ('responses[2]',
                           'Response 2: has both child_set and child_list; keep child_list, which '
                           'also reads a list of one item'),
                          ('responses[2]', 'Response 2: child_set must be a non-empty list'),
                          ('responses[3]',
                           "Response 3: child_list: group $1 exceeds the 0 group(s) of the rule's "
                           'match'),
                          ('responses[3]',
                           "Response 3: child_list: missing 'element' (the regex for one item of "
                           'the list)'),
                          ('responses[3]',
                           "Response 3: child_list: missing 'state' mapping (prop -> $N or "
                           'literal)'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'capture_set_shape': [('responses[0]',
                        'Response 0: set.a must be a capture reference like "$1" or a static '
                        'value; move a value map into mappings:'),
                       ('responses[0]',
                        'Response 0: set.b must be a capture reference like "$1" or a static '
                        'value; move a value map into mappings:'),
                       ('responses[1]',
                        'Response 1: set.a must be a capture reference like "$1" or a static '
                        'value; move a value map into mappings:'),
                       ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'common_checks_in_order': [('responses[0]',
                             'Response 0: throttle must be a positive number of seconds'),
                            ('responses[0]', "Response 0: 'only_when' must be a mapping"),
                            ('responses[0]',
                             'Response 0: require only applies to json: true responses'),
                            ('responses[0]',
                             'Response 0: require must be a JSON key name or a list of them'),
                            ('responses[0]',
                             'Response 0: after_json has no effect: no response rule in this '
                             'driver is json: true'),
                            ('responses[0]',
                             'Response 0: has both child_set and child_list; keep child_list, '
                             'which also reads a list of one item'),
                            ('responses[0]',
                             'Response 0: set.a must be a capture reference like "$1" or a static '
                             'value; move a value map into mappings:'),
                            ('responses[0]',
                             'Response 0: child_list: type None is not a declared '
                             'child_entity_type'),
                            ('responses[0]', 'Response 0: child_set must be a non-empty list'),
                            ('child_entity_types.bare',
                             'child_entity_types.bare: must be a mapping')],
 'osc_address': [('responses[0]', "Response 0: OSC address must start with '/'"),
                 ('responses[1]', "Response 1: OSC address must start with '/'"),
                 ('responses[2]', "Response 2: OSC address must start with '/'"),
                 ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'osc_child_set_container': [('responses[0]', 'Response 0: child_set must be a non-empty list'),
                             ('responses[1]', 'Response 1: child_set must be a non-empty list'),
                             ('responses[2]', "Response 2: OSC address must start with '/'"),
                             ('responses[2]', 'Response 2: child_set must be a non-empty list'),
                             ('child_entity_types.bare',
                              'child_entity_types.bare: must be a mapping')],
 'osc_child_set_entry_type': [('responses[0]', 'Response 0: child_set[0]: must be a mapping'),
                              ('responses[0]',
                               "Response 0: child_set[1]: type 'nope' is not a declared "
                               'child_entity_type'),
                              ('responses[0]',
                               'Response 0: child_set[2]: type 5 is not a declared '
                               'child_entity_type'),
                              ('child_entity_types.bare',
                               'child_entity_types.bare: must be a mapping')],
 'osc_child_set_id': [('responses[0]',
                       "Response 0: child_set[0]: missing 'id' ({segment: N} for an address "
                       'segment, or a literal)'),
                      ('responses[0]',
                       "Response 0: child_set[1]: id needs an integer 'segment' (0-based index "
                       'into the /-split address; OSC rules have no capture groups)'),
                      ('responses[0]',
                       "Response 0: child_set[2]: id needs an integer 'segment' (0-based index "
                       'into the /-split address; OSC rules have no capture groups)'),
                      ('responses[0]', 'Response 0: child_set[3]: segment must be 0 or higher'),
                      ('responses[0]',
                       'Response 0: child_set[4]: segment 3 is past the end of the address pattern '
                       '(3 segment(s))'),
                      ('responses[0]',
                       "Response 0: child_set[6]: id needs an integer 'segment' (0-based index "
                       'into the /-split address; OSC rules have no capture groups)'),
                      ('responses[0]',
                       'Response 0: child_set[7]: OSC rules have no capture groups; use {segment: '
                       'N} for the id, {arg: N} for values'),
                      ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'osc_child_set_id_no_segments': [('responses[1]', "Response 1: OSC address must start with '/'"),
                                  ('child_entity_types.bare',
                                   'child_entity_types.bare: must be a mapping')],
 'osc_child_set_id_map': [('responses[0]',
                           'Response 0: child_set[0]: id map must be a non-empty mapping of wire '
                           'id -> local child id'),
                          ('responses[0]',
                           'Response 0: child_set[1]: id map must be a non-empty mapping of wire '
                           'id -> local child id'),
                          ('responses[0]',
                           'Response 0: child_set[2]: id map entries must be scalar wire id -> '
                           'local id pairs'),
                          ('responses[0]',
                           'Response 0: child_set[3]: id map entries must be scalar wire id -> '
                           'local id pairs'),
                          ('responses[0]',
                           'Response 0: child_set[4]: id map entries must be scalar wire id -> '
                           'local id pairs'),
                          ('responses[0]',
                           "Response 0: child_set[5]: id map value 'x' is not an integer (zone "
                           'declares integer ids)'),
                          ('responses[0]',
                           'Response 0: child_set[6]: id map entries must be scalar wire id -> '
                           'local id pairs'),
                          ('responses[0]',
                           "Response 0: child_set[8]: id needs an integer 'segment' (0-based index "
                           'into the /-split address; OSC rules have no capture groups)'),
                          ('responses[0]',
                           "Response 0: child_set[8]: id map value 'q' is not an integer (zone "
                           'declares integer ids)'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'osc_child_set_state': [('responses[0]',
                          "Response 0: child_set[0]: missing 'state' mapping (prop -> {arg: N} or "
                          'literal)'),
                         ('responses[0]',
                          "Response 0: child_set[1]: missing 'state' mapping (prop -> {arg: N} or "
                          'literal)'),
                         ('responses[0]',
                          "Response 0: child_set[2]: missing 'state' mapping (prop -> {arg: N} or "
                          'literal)'),
                         ('responses[0]',
                          'Response 0: child_set[3]: OSC rules have no capture groups; use '
                          '{segment: N} for the id, {arg: N} for values'),
                         ('responses[0]',
                          "Response 0: child_set[3]: missing 'state' mapping (prop -> {arg: N} or "
                          'literal)'),
                         ('responses[0]',
                          "Response 0: child_set[4]: state prop 'nope' is not declared in "
                          'child_entity_types.zone.state_variables'),
                         ('responses[0]',
                          "Response 0: child_set[5]: state 'level': OSC rules have no capture "
                          'groups; use {arg: N}'),
                         ('responses[0]',
                          "Response 0: child_set[6]: state 'level' needs {arg: N} or {value: ...}"),
                         ('responses[0]',
                          "Response 0: child_set[7]: state 'level' arg must be an integer >= 0 "
                          '(got True)'),
                         ('responses[0]',
                          "Response 0: child_set[8]: state 'level' arg must be an integer >= 0 "
                          '(got -1)'),
                         ('responses[0]',
                          "Response 0: child_set[9]: state 'level' arg must be an integer >= 0 "
                          "(got '0')"),
                         ('responses[0]',
                          "Response 0: child_set[13]: state prop 'nope' is not declared in "
                          'child_entity_types.zone.state_variables'),
                         ('responses[0]',
                          "Response 0: child_set[13]: state 'nope': OSC rules have no capture "
                          'groups; use {arg: N}'),
                         ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'osc_with_common_checks': [('responses[0]',
                             'Response 0: throttle must be a positive number of seconds'),
                            ('responses[0]', "Response 0: OSC address must start with '/'"),
                            ('responses[0]',
                             "Response 0: child_set[0]: missing 'id' ({segment: N} for an address "
                             'segment, or a literal)'),
                            ('responses[0]',
                             "Response 0: child_set[0]: missing 'state' mapping (prop -> {arg: N} "
                             'or literal)'),
                            ('child_entity_types.bare',
                             'child_entity_types.bare: must be a mapping')],
 'json_needs_something': [('responses[0]',
                           "Response 0: json response needs a 'set' map, a 'mappings' list or a "
                           "'child_set'"),
                          ('responses[1]',
                           "Response 1: json response needs a 'set' map, a 'mappings' list or a "
                           "'child_set'"),
                          ('responses[3]',
                           "Response 3: json response needs a 'set' map, a 'mappings' list or a "
                           "'child_set'"),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'json_contains': [('responses[0]',
                    'Response 0: set.a: contains must be a scalar to look for (a flag name, a '
                    'number, true/false), not NoneType'),
                   ('responses[0]',
                    'Response 0: set.b: contains must be a scalar to look for (a flag name, a '
                    'number, true/false), not list'),
                   ('responses[0]',
                    'Response 0: set.c: contains must be a scalar to look for (a flag name, a '
                    'number, true/false), not dict'),
                   ('responses[1]',
                    'Response 1: mappings[1]: contains must be a scalar to look for (a flag name, '
                    'a number, true/false), not dict'),
                   ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'json_child_set_container': [('responses[0]', 'Response 0: child_set must be a non-empty list'),
                              ('responses[1]', 'Response 1: child_set must be a non-empty list'),
                              ('responses[2]',
                               'Response 2: set.a: contains must be a scalar to look for (a flag '
                               'name, a number, true/false), not list'),
                              ('responses[2]', 'Response 2: child_set must be a non-empty list'),
                              ('child_entity_types.bare',
                               'child_entity_types.bare: must be a mapping')],
 'json_child_set_entry': [('responses[0]', 'Response 0: child_set[0]: must be a mapping'),
                          ('responses[0]',
                           "Response 0: child_set[1]: type 'nope' is not a declared "
                           'child_entity_type'),
                          ('responses[0]',
                           'Response 0: child_set[2]: type None is not a declared '
                           'child_entity_type'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'json_child_set_id': [('responses[0]',
                        "Response 0: child_set[0]: missing 'id' (a literal child id: a json body "
                        'has no captures to route on, so write one entry per child)'),
                       ('responses[0]',
                        'Response 0: child_set[1]: a json rule has no captures or address '
                        "segments, so id must be a literal child id (got {'group': 1})"),
                       ('responses[0]',
                        'Response 0: child_set[2]: a json rule has no captures or address '
                        "segments, so id must be a literal child id (got '$1')"),
                       ('responses[0]',
                        'Response 0: child_set[3]: id must be a literal child id (got True)'),
                       ('responses[0]',
                        'Response 0: child_set[4]: id must be a literal child id (got 1.5)'),
                       ('responses[0]',
                        'Response 0: child_set[5]: id must be a literal child id (got [1])'),
                       ('responses[0]',
                        "Response 0: child_set[6]: id 'x' is not an integer (zone declares integer "
                        'ids)'),
                       ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'json_child_set_state': [('responses[0]',
                           "Response 0: child_set[0]: missing 'state' mapping (prop -> JSON path)"),
                          ('responses[0]',
                           "Response 0: child_set[1]: missing 'state' mapping (prop -> JSON path)"),
                          ('responses[0]',
                           "Response 0: child_set[2]: id 'x' is not an integer (zone declares "
                           'integer ids)'),
                          ('responses[0]',
                           "Response 0: child_set[2]: missing 'state' mapping (prop -> JSON path)"),
                          ('responses[0]',
                           "Response 0: child_set[3]: state prop 'nope' is not declared in "
                           'child_entity_types.zone.state_variables'),
                          ('responses[0]',
                           'Response 0: child_set[4]: state \'level\' needs a JSON path: "a.b" or '
                           '{key: a.b}'),
                          ('responses[0]',
                           'Response 0: child_set[5]: state \'level\' needs a JSON path: "a.b" or '
                           '{key: a.b}'),
                          ('responses[0]',
                           'Response 0: child_set[7]: state \'level\' needs a JSON path: "a.b" or '
                           '{key: a.b}'),
                          ('responses[0]',
                           "Response 0: child_set[8]: state 'level' has ['arg', 'group'], which a "
                           'json rule does not read; the spec is {key, type, map, contains}'),
                          ('responses[0]',
                           "Response 0: child_set[9]: state 'level': contains must be a scalar to "
                           'look for (a flag name, a number, true/false), not NoneType'),
                          ('responses[0]',
                           "Response 0: child_set[11]: state 'level': a json rule has no capture "
                           'groups; use a JSON path'),
                          ('responses[0]',
                           "Response 0: child_set[12]: state 'level' must be a JSON path (a "
                           'literal value has nothing to read)'),
                          ('responses[0]',
                           "Response 0: child_set[13]: state 'level' must be a JSON path (a "
                           'literal value has nothing to read)'),
                          ('responses[0]',
                           "Response 0: child_set[14]: state 'level' must be a JSON path (a "
                           'literal value has nothing to read)'),
                          ('responses[0]',
                           "Response 0: child_set[15]: state 'level' must be a JSON path (a "
                           'literal value has nothing to read)'),
                          ('responses[0]',
                           "Response 0: child_set[16]: state prop 'nope' is not declared in "
                           'child_entity_types.zone.state_variables'),
                          ('responses[0]',
                           'Response 0: child_set[16]: state \'nope\' needs a JSON path: "a.b" or '
                           '{key: a.b}'),
                          ('responses[0]',
                           "Response 0: child_set[16]: state 'nope' has ['wrong'], which a json "
                           'rule does not read; the spec is {key, type, map, contains}'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'json_with_common_checks': [('responses[0]',
                              'Response 0: throttle must be a positive number of seconds'),
                             ('responses[0]', "Response 0: 'only_when' must be a mapping"),
                             ('responses[0]',
                              'Response 0: require list entries must be non-empty JSON key names'),
                             ('responses[0]',
                              'Response 0: after_json applies to regex rules; a json: true rule '
                              'already reads the body'),
                             ('responses[0]',
                              'Response 0: child_list works on text rules (match:) only; a json: '
                              'true rule routes to children with child_set'),
                             ('responses[0]',
                              'Response 0: set.a: contains must be a scalar to look for (a flag '
                              'name, a number, true/false), not dict'),
                             ('responses[0]', 'Response 0: child_set must be a non-empty list'),
                             ('child_entity_types.bare',
                              'child_entity_types.bare: must be a mapping')],
 'text_match': [('responses[0]', 'Response 0: missing match or address'),
                ('responses[1]', 'Response 1: missing match or address'),
                ('responses[2]', 'Response 2: missing match or address'),
                ('responses[3]',
                 "Response 3: regex '(a+)+$' has a nested quantifier; this can cause catastrophic "
                 'backtracking against hostile input'),
                ('responses[4]',
                 "Response 4: invalid regex '(': missing ), unterminated subpattern at position 0"),
                ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'text_child_set_container': [('responses[0]', 'Response 0: child_set must be a non-empty list'),
                              ('responses[1]', 'Response 1: child_set must be a non-empty list'),
                              ('responses[4]', 'Response 4: missing match or address'),
                              ('responses[4]', 'Response 4: child_set must be a non-empty list'),
                              ('child_entity_types.bare',
                               'child_entity_types.bare: must be a mapping')],
 'text_child_set_entry': [('responses[0]', 'Response 0: child_set[0]: must be a mapping'),
                          ('responses[0]',
                           "Response 0: child_set[1]: type 'nope' is not a declared "
                           'child_entity_type'),
                          ('responses[0]',
                           "Response 0: child_set[2]: type ['zone'] is not a declared "
                           'child_entity_type'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'text_child_set_id': [('responses[0]',
                        "Response 0: child_set[0]: missing 'id' (a capture ref like $1, a literal, "
                        'or {group, map})'),
                       ('responses[0]',
                        "Response 0: child_set[2]: id: '$x' is not a numeric capture ref ($1, $2, "
                        '...)'),
                       ('responses[0]',
                        "Response 0: child_set[3]: id: '$' is not a numeric capture ref ($1, $2, "
                        '...)'),
                       ('responses[0]',
                        'Response 0: child_set[4]: id: capture ref must be $1 or higher'),
                       ('responses[0]',
                        "Response 0: child_set[5]: id: capture ref $3 exceeds the pattern's 2 "
                        'group(s)'),
                       ('responses[0]',
                        "Response 0: child_set[9]: id group: '$z' is not a numeric capture ref "
                        '($1, $2, ...)'),
                       ('responses[0]',
                        "Response 0: child_set[11]: id group: capture ref $9 exceeds the pattern's "
                        '2 group(s)'),
                       ('responses[0]',
                        'Response 0: child_set[12]: id group: capture ref must be $1 or higher'),
                       ('responses[0]',
                        'Response 0: child_set[13]: id group must be a capture ref (1, 2, ... or '
                        "'$1')"),
                       ('responses[0]',
                        'Response 0: child_set[14]: id group must be a capture ref (1, 2, ... or '
                        "'$1')"),
                       ('responses[0]',
                        'Response 0: child_set[15]: id group must be a capture ref (1, 2, ... or '
                        "'$1')"),
                       ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'text_child_set_id_map': [('responses[0]',
                            'Response 0: child_set[0]: id map must be a non-empty mapping of wire '
                            'id -> local child id'),
                           ('responses[0]',
                            'Response 0: child_set[1]: id map must be a non-empty mapping of wire '
                            'id -> local child id'),
                           ('responses[0]',
                            'Response 0: child_set[2]: id map entries must be scalar wire id -> '
                            'local id pairs'),
                           ('responses[0]',
                            'Response 0: child_set[3]: id map entries must be scalar wire id -> '
                            'local id pairs'),
                           ('responses[0]',
                            'Response 0: child_set[4]: id map entries must be scalar wire id -> '
                            'local id pairs'),
                           ('responses[0]',
                            "Response 0: child_set[5]: id map value 'one' is not an integer (zone "
                            'declares integer ids)'),
                           ('responses[0]',
                            'Response 0: child_set[8]: id group: capture ref must be $1 or higher'),
                           ('responses[0]',
                            "Response 0: child_set[8]: id map value 'x' is not an integer (zone "
                            'declares integer ids)'),
                           ('child_entity_types.bare',
                            'child_entity_types.bare: must be a mapping')],
 'text_child_set_state': [('responses[0]',
                           "Response 0: child_set[0]: missing 'state' mapping (prop -> $N or "
                           'literal)'),
                          ('responses[0]',
                           "Response 0: child_set[1]: missing 'state' mapping (prop -> $N or "
                           'literal)'),
                          ('responses[0]',
                           "Response 0: child_set[2]: id: capture ref $9 exceeds the pattern's 2 "
                           'group(s)'),
                          ('responses[0]',
                           "Response 0: child_set[2]: missing 'state' mapping (prop -> $N or "
                           'literal)'),
                          ('responses[0]',
                           "Response 0: child_set[3]: state prop 'nope' is not declared in "
                           'child_entity_types.zone.state_variables'),
                          ('responses[0]',
                           "Response 0: child_set[4]: state 'level': capture ref $5 exceeds the "
                           "pattern's 2 group(s)"),
                          ('responses[0]',
                           "Response 0: child_set[5]: state 'level': '$q' is not a numeric capture "
                           'ref ($1, $2, ...)'),
                          ('responses[0]',
                           "Response 0: child_set[8]: state prop 'nope' is not declared in "
                           'child_entity_types.zone.state_variables'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'text_child_set_unbounded': [('responses[0]',
                               "Response 0: invalid regex '{prefix}(\\d': missing ), unterminated "
                               'subpattern at position 8'),
                              ('responses[1]', 'Response 1: missing match or address'),
                              ('responses[2]', 'Response 2: missing match or address'),
                              ('responses[3]',
                               "Response 3: regex '(a+)+$' has a nested quantifier; this can cause "
                               'catastrophic backtracking against hostile input'),
                              ('responses[3]',
                               'Response 3: child_set[0]: id group: capture ref $9 exceeds the '
                               "pattern's 1 group(s)"),
                              ('responses[3]',
                               "Response 3: child_set[0]: state 'level': capture ref $7 exceeds "
                               "the pattern's 1 group(s)"),
                              ('child_entity_types.bare',
                               'child_entity_types.bare: must be a mapping'),
                              ('responses[0].match',
                               "responses[0].match: '{prefix}' resolves to nothing. It names no "
                               'config field (only config values substitute here), so the pattern '
                               'would look for it as written')],
 'text_with_common_checks': [('responses[0]',
                              'Response 0: throttle must be a positive number of seconds'),
                             ('responses[0]',
                              'Response 0: require only applies to json: true responses'),
                             ('responses[0]',
                              'Response 0: set.a must be a capture reference like "$1" or a static '
                              'value; move a value map into mappings:'),
                             ('responses[0]',
                              "Response 0: invalid regex '(': missing ), unterminated subpattern "
                              'at position 0'),
                             ('responses[0]',
                              'Response 0: child_set[0]: id: capture ref must be $1 or higher'),
                             ('responses[0]',
                              "Response 0: child_set[0]: missing 'state' mapping (prop -> $N or "
                              'literal)'),
                             ('child_entity_types.bare',
                              'child_entity_types.bare: must be a mapping')],
 'child_list_container': [('responses[0]', 'Response 0: child_list: must be a mapping'),
                          ('responses[1]', 'Response 1: child_list: must be a mapping'),
                          ('responses[2]',
                           "Response 2: child_list: type 'nope' is not a declared "
                           'child_entity_type'),
                          ('responses[3]',
                           'Response 3: child_list: type 5 is not a declared child_entity_type'),
                          ('responses[4]',
                           "Response 4: child_list: type 'bare' is not a declared "
                           'child_entity_type'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'child_list_group': [('responses[3]',
                       "Response 3: child_list: group $3 exceeds the 2 group(s) of the rule's "
                       'match'),
                      ('responses[4]',
                       'Response 4: child_list: group must be a capture ref ($1, $2, ...)'),
                      ('responses[5]',
                       'Response 5: child_list: group must be a capture ref ($1, $2, ...)'),
                      ('responses[6]',
                       'Response 6: child_list: group must be a capture ref ($1, $2, ...)'),
                      ('responses[7]',
                       'Response 7: child_list: group must be a capture ref ($1, $2, ...)'),
                      ('responses[8]',
                       'Response 8: child_list: group must be a capture ref ($1, $2, ...)'),
                      ('responses[9]',
                       "Response 9: invalid regex '{p}(': missing ), unterminated subpattern at "
                       'position 3'),
                      ('responses[10]', 'Response 10: pattern must be a string, got int'),
                      ('responses[11]', 'Response 11: missing match or address'),
                      ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                      ('responses[9].match',
                       "responses[9].match: '{p}' resolves to nothing. It names no config field "
                       '(only config values substitute here), so the pattern would look for it as '
                       'written')],
 'child_list_element': [('responses[0]',
                         "Response 0: child_list: missing 'element' (the regex for one item of the "
                         'list)'),
                        ('responses[1]',
                         "Response 1: child_list: missing 'element' (the regex for one item of the "
                         'list)'),
                        ('responses[2]',
                         "Response 2: child_list: missing 'element' (the regex for one item of the "
                         'list)'),
                        ('responses[3]',
                         'Response 3: child_list: element is not a valid regular expression '
                         '(missing ), unterminated subpattern at position 0)'),
                        ('responses[4]',
                         "Response 4: child_list: element: regex '(a+)+$' has a nested quantifier; "
                         'this can cause catastrophic backtracking against hostile input'),
                        ('responses[5]',
                         'Response 5: child_list: element matches empty text, so it cannot tell '
                         'one item from the next'),
                        ('responses[6]',
                         'Response 6: child_list: element matches empty text, so it cannot tell '
                         'one item from the next'),
                        ('responses[6]',
                         "Response 6: child_list: state 'level' $1 exceeds the 0 group(s) of the "
                         'element'),
                        ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'child_list_separator': [('responses[0]',
                           'Response 0: child_list: separator must be the text between items'),
                          ('responses[1]',
                           'Response 1: child_list: separator must be the text between items'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'child_list_id': [('responses[1]',
                    'Response 1: child_list: id $2 exceeds the 1 group(s) of the element'),
                   ('responses[3]',
                    'Response 3: child_list: id must be a capture ref ($1, $2, ...)'),
                   ('responses[5]',
                    'Response 5: child_list: id group $4 exceeds the 1 group(s) of the element'),
                   ('responses[7]',
                    'Response 7: child_list: id map must be a non-empty mapping of wire id -> '
                    'local child id'),
                   ('responses[8]',
                    'Response 8: child_list: id map must be a non-empty mapping of wire id -> '
                    'local child id'),
                   ('responses[9]',
                    'Response 9: child_list: id map must be a non-empty mapping of wire id -> '
                    'local child id'),
                   ('responses[10]',
                    'Response 10: child_list: id map must be a non-empty mapping of wire id -> '
                    'local child id'),
                   ('responses[11]',
                    "Response 11: child_list: id map value 'one' is not an integer (zone declares "
                    'integer ids)'),
                   ('responses[13]',
                    'Response 13: child_list: id group must be a capture ref ($1, $2, ...)'),
                   ('responses[14]',
                    'Response 14: child_list: start counts places in the list, and with id each '
                    'item names its own child; remove one of them'),
                   ('responses[15]',
                    'Response 15: child_list: start counts places in the list, and with id each '
                    'item names its own child; remove one of them'),
                   ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'child_list_start': [('responses[3]',
                       "Response 3: child_list: start $4 exceeds the 2 group(s) of the rule's "
                       'match'),
                      ('responses[4]',
                       'Response 4: child_list: start must be a capture ref ($1, $2, ...)'),
                      ('responses[5]',
                       'Response 5: child_list: start must be a capture ref ($1, $2, ...)'),
                      ('responses[6]',
                       "Response 6: child_list: start must be the first item's child id or a "
                       "capture ref of the rule's match"),
                      ('responses[7]',
                       "Response 7: child_list: start must be the first item's child id or a "
                       "capture ref of the rule's match"),
                      ('responses[8]',
                       "Response 8: child_list: start must be the first item's child id or a "
                       "capture ref of the rule's match"),
                      ('responses[9]',
                       'Response 9: child_list: mic has string ids, so an item cannot be named by '
                       'its place in the list; give id: $N'),
                      ('responses[10]',
                       'Response 10: child_list: mic has string ids, so an item cannot be named by '
                       'its place in the list; give id: $N'),
                      ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'child_list_state': [('responses[0]',
                       "Response 0: child_list: missing 'state' mapping (prop -> $N or literal)"),
                      ('responses[1]',
                       "Response 1: child_list: missing 'state' mapping (prop -> $N or literal)"),
                      ('responses[2]',
                       "Response 2: child_list: missing 'state' mapping (prop -> $N or literal)"),
                      ('responses[3]',
                       "Response 3: child_list: state prop 'nope' is not declared in "
                       'child_entity_types.zone.state_variables'),
                      ('responses[4]',
                       "Response 4: child_list: state 'level' $2 exceeds the 1 group(s) of the "
                       'element'),
                      ('responses[5]',
                       "Response 5: child_list: state 'level' must be a capture ref ($1, $2, ...)"),
                      ('responses[6]',
                       "Response 6: child_list: state 'level' must be a capture ref ($1, $2, ...)"),
                      ('responses[8]',
                       "Response 8: child_list: state 'level' group $3 exceeds the 1 group(s) of "
                       'the element'),
                      ('responses[9]',
                       "Response 9: child_list: state 'level' group must be a capture ref ($1, $2, "
                       '...)'),
                      ('responses[11]',
                       "Response 11: child_list: state 'level' needs a group (the item's capture) "
                       'or a value'),
                      ('responses[12]',
                       "Response 12: child_list: state 'level' needs a group (the item's capture) "
                       'or a value'),
                      ('responses[14]',
                       'Response 14: child_list: element is not a valid regular expression '
                       '(missing ), unterminated subpattern at position 0)'),
                      ('responses[15]',
                       "Response 15: child_list: element: regex '(a+)+$' has a nested quantifier; "
                       'this can cause catastrophic backtracking against hostile input'),
                      ('responses[15]',
                       "Response 15: child_list: state 'level' $9 exceeds the 1 group(s) of the "
                       'element'),
                      ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'child_list_everything_wrong': [('responses[0]',
                                  "Response 0: invalid regex '(': missing ), unterminated "
                                  'subpattern at position 0'),
                                 ('responses[0]',
                                  'Response 0: child_list: element is not a valid regular '
                                  'expression (missing ), unterminated subpattern at position 0)'),
                                 ('responses[0]',
                                  'Response 0: child_list: separator must be the text between '
                                  'items'),
                                 ('responses[0]',
                                  "Response 0: child_list: id map value 'x' is not an integer "
                                  '(zone declares integer ids)'),
                                 ('responses[0]',
                                  'Response 0: child_list: start counts places in the list, and '
                                  'with id each item names its own child; remove one of them'),
                                 ('responses[0]',
                                  "Response 0: child_list: state prop 'nope' is not declared in "
                                  'child_entity_types.zone.state_variables'),
                                 ('child_entity_types.bare',
                                  'child_entity_types.bare: must be a mapping')],
 'form_precedence': [('responses[0]', "Response 0: OSC address must start with '/'"),
                     ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'json_contains_set_before_mappings': [('responses[0]',
                                        'Response 0: set.a: contains must be a scalar to look for '
                                        '(a flag name, a number, true/false), not list'),
                                       ('responses[0]',
                                        'Response 0: mappings[0]: contains must be a scalar to '
                                        'look for (a flag name, a number, true/false), not dict'),
                                       ('child_entity_types.bare',
                                        'child_entity_types.bare: must be a mapping')],
 'throttle_nan': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'several_rules_each_tagged': [('responses[0]',
                                'Response 0: throttle must be a positive number of seconds'),
                               ('responses[1]', 'Response 1: must be a mapping'),
                               ('responses[2]', "Response 2: OSC address must start with '/'"),
                               ('responses[3]',
                                "Response 3: json response needs a 'set' map, a 'mappings' list or "
                                "a 'child_set'"),
                               ('responses[4]',
                                "Response 4: child_set[0]: type 'nope' is not a declared "
                                'child_entity_type'),
                               ('child_entity_types.bare',
                                'child_entity_types.bare: must be a mapping')],
 'responses_not_a_list': [('responses', 'responses: must be a list'),
                          ('child_entity_types.bare',
                           'child_entity_types.bare: must be a mapping')],
 'responses_absent': [('responses', 'responses: must be a list'),
                      ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'no_child_types': [('responses[0]',
                     "Response 0: child_set[0]: type 'zone' is not a declared child_entity_type"),
                    ('responses[1]',
                     "Response 1: child_set[0]: type 'zone' is not a declared child_entity_type"),
                    ('responses[2]',
                     "Response 2: child_set[0]: type 'zone' is not a declared child_entity_type"),
                    ('responses[3]',
                     "Response 3: child_list: type 'zone' is not a declared child_entity_type")],
 'child_list_other_id_kind': [('child_entity_types.odd',
                               "child_entity_types.odd.id_format: unknown type 'uuid' (expected "
                               "'integer' or 'string')")],
 'child_types_not_a_mapping': [('responses[0]',
                                "Response 0: child_set[0]: type 'zone' is not a declared "
                                'child_entity_type'),
                               ('child_entity_types', 'child_entity_types: must be a mapping')]}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    issues = validate_driver_issues(definition)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [m for _, m in got]


def test_every_case_has_an_expectation() -> None:
    cases = set(CASES) | set(globals().get("EXTRA", {}))
    assert cases == set(EXPECTED)
