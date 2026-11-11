"""Pins for how the auto-simulator reads a driver's response rules into its
reply tables: which rules a flat variable, a child variable and a child list
take a reply from, in which order, and what each form of a rule's value
(a capture, a mapped capture, a static value) and of its child id
(``$N``, ``{group, map}``) becomes. Recorded from the code as it is, on an
invented device; how a table then renders a value is
test_sim_state_replies_pinned.py.
"""

from __future__ import annotations

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator


def _sim(responses, state_variables=None, child_entity_types=None, transport="tcp"):
    d = {
        "id": "acme_switcher",
        "name": "Acme Switcher",
        "transport": transport,
        "delimiter": "\r",
        "state_variables": state_variables or {},
        "commands": {},
        "responses": responses,
    }
    if child_entity_types is not None:
        d["child_entity_types"] = child_entity_types
    return YAMLAutoSimulator("s1", {}, driver_def=d)


_OUTPUT = {"output": {
    "id_format": "int",
    "state_variables": {
        "input": {"type": "integer"},
        "mute": {"type": "boolean"},
        "level": {"type": "integer", "scale": 2},
        "audio": {"type": "integer"},
    },
}}


def _flat(sim) -> dict:
    return {
        key: (sr.template, [a[0] for a in sr.alternates], dict(sr.value_map))
        for key, sr in sim._state_responses.items()
    }


def _child(sim) -> dict:
    return {
        key: (sr.template, [a[0] for a in sr.alternates], dict(sr.value_map), sr.id_wire_map)
        for key, sr in sim._child_state_responses.items()
    }


def _lists(sim) -> dict:
    return {
        key: (a.outer, a.item, a.separator, a.by_place, a.start, a.value_words, a.id_wire_map)
        for key, a in sim._child_list_answers.items()
    }


# ── flat variables ──


def test_a_json_rule_and_an_address_rule_give_no_flat_reply():
    sim = _sim([
        {"json": True, "mappings": [{"state": "a", "key": "a"}]},
        {"address": "/x", "match": "X(\\d+)", "mappings": [{"group": 1, "state": "b"}]},
    ])
    assert _flat(sim) == {}


def test_a_mappings_rule_is_read_as_a_set_and_its_maps_are_reversed():
    sim = _sim([
        {"match": "PWR(\\d)", "mappings": [
            {"group": 1, "state": "power", "map": {"1": "on", "0": "off"}},
        ]},
        {"match": "SRC(\\w+)", "mappings": [{"group": 1, "state": "source"}]},
        {"match": "READY", "mappings": [{"state": "ready", "value": True}]},
        {"match": "BAD(\\d)", "mappings": [
            {"group": "x", "state": "bad", "map": {"1": "one"}},
            {"group": 0, "state": "zero", "map": {"1": "one"}},
        ]},
    ])
    assert _flat(sim) == {
        "power": ("PWR{value}", [], {"on": "PWR1", "off": "PWR0"}),
        "source": ("SRC{value}", [], {}),
        "ready": (None, [], {"true": "READY"}),
        "bad": (None, [], {}),
        "zero": (None, [], {}),
    }


def test_a_capture_reference_picks_its_group_and_a_bad_one_gives_nothing():
    sim = _sim([
        {"match": "V(\\d+) M(\\d)", "set": {"vol": "$1", "mode": "$2"}},
        {"match": "Z(\\d+)", "set": {"zed": "$zz", "zero": "$0"}},
        {"match": "LIT", "set": {"vol": "5"}},
    ])
    assert _flat(sim) == {
        "vol": ("V{value} M0", [], {"5": "LIT"}),
        "mode": ("V0 M{value}", [], {}),
        "zed": (None, [], {}),
        "zero": (None, [], {}),
    }


def test_a_mapping_rule_with_a_set_reads_the_set_alone():
    sim = _sim([
        {"match": "P(\\d)", "set": {"p": "$1"},
         "mappings": [{"group": 1, "state": "q", "map": {"1": "x"}}]},
    ])
    assert set(_flat(sim)) == {"p"}


def test_a_flat_rule_that_is_not_a_dict_raises():
    with pytest.raises(AttributeError):
        _sim(["MATCH"])


# ── child variables ──


def test_a_child_capture_a_mapped_capture_and_a_static_value():
    sim = _sim([{"match": "Out(\\d+) In(\\d+) M(\\d) L(\\d+)", "child_set": [
        {"type": "output", "id": "$1", "state": {
            "input": "$2",
            "mute": {"group": 3, "map": {"1": True, "0": False}},
            "level": {"group": "4"},
            "audio": {"value": 7},
        }},
    ]}], child_entity_types=_OUTPUT)
    assert _child(sim) == {
        ("output", "input"): ("Out{child_id} In{value} M0 L0", [], {}, None),
        ("output", "mute"): (None, [], {
            "true": "Out{child_id} In0 M1 L0",
            "false": "Out{child_id} In0 M0 L0",
        }, None),
        ("output", "level"): ("Out{child_id} In0 M0 L{value}", [], {}, None),
        ("output", "audio"): (None, [], {"7": "Out{child_id} In0 M0 L0"}, None),
    }


def test_an_expression_that_names_nothing_leaves_an_empty_table():
    sim = _sim([{"match": "Out(\\d+) (\\w+)", "child_set": [
        {"type": "output", "id": "$1", "state": {
            "input": "$x",
            "mute": {"group": "y"},
            "level": {"other": 1},
            "audio": "fixed",
        }},
    ]}], child_entity_types=_OUTPUT)
    assert _child(sim) == {
        ("output", "input"): (None, [], {}, None),
        ("output", "mute"): (None, [], {}, None),
        ("output", "level"): (None, [], {}, None),
        ("output", "audio"): (None, [], {"fixed": "Out{child_id} 0"}, None),
    }


@pytest.mark.parametrize("cid, expected_template, expected_map", [
    ("$1", "Out{child_id} In{value}", None),
    ({"group": "$1"}, "Out{child_id} In{value}", None),
    ({"group": 1, "map": {"A": 1, "B": 2}}, "Out{child_id} In{value}", {"1": "A", "2": "B"}),
    ({"group": True}, "Out{child_id} In{value}", None),
])
def test_a_child_id_is_a_capture_reference_with_its_map_reversed(cid, expected_template, expected_map):
    sim = _sim([{"match": "Out(\\w+) In(\\d+)", "child_set": [
        {"type": "output", "id": cid, "state": {"input": "$2"}},
    ]}], child_entity_types=_OUTPUT)
    assert _child(sim) == {
        ("output", "input"): (expected_template, [], {}, expected_map),
    }


@pytest.mark.parametrize("cid", ["$x", {"group": "z"}, {"map": {"A": 1}}, "2", 2, None])
def test_a_child_id_that_names_no_capture_gives_nothing(cid):
    sim = _sim([{"match": "Out(\\w+) In(\\d+)", "child_set": [
        {"type": "output", "id": cid, "state": {"input": "$2"}},
    ]}], child_entity_types=_OUTPUT)
    assert _child(sim) == {}


def test_rules_naming_one_variable_bind_before_broader_ones():
    sim = _sim([
        {"match": "Out(\\d+) In(\\d+) All", "child_set": [
            {"type": "output", "id": "$1", "state": {"input": "$2", "audio": "$2"}},
        ]},
        {"match": "Out(\\d+) In(\\d+) Vid", "child_set": [
            {"type": "output", "id": "$1", "state": {"input": "$2"}},
        ]},
    ], child_entity_types=_OUTPUT)
    view = _child(sim)
    assert view[("output", "input")][:2] == (
        "Out{child_id} In{value} Vid", ["Out{child_id} In{value} All"],
    )
    assert view[("output", "audio")][:2] == ("Out{child_id} In{value} All", [])


def test_a_child_mapped_value_keeps_the_first_reply_and_the_first_id_map():
    sim = _sim([
        {"match": "M(\\w) (\\d)", "child_set": [
            {"type": "output", "id": {"group": 1, "map": {"A": 1}},
             "state": {"mute": {"group": 2, "map": {"1": True}}}},
        ]},
        {"match": "N(\\w) (\\d)", "child_set": [
            {"type": "output", "id": {"group": 1, "map": {"B": 1}},
             "state": {"mute": {"group": 2, "map": {"1": True, "0": False}}}},
        ]},
    ], child_entity_types=_OUTPUT)
    assert _child(sim) == {("output", "mute"): (
        None, [], {"true": "M{child_id} 1", "false": "N{child_id} 0"}, {"1": "A"},
    )}


@pytest.mark.parametrize("rule", [
    {"match": "X(\\d)", "child_set": {"type": "output"}},
    {"child_set": [{"type": "output", "id": "$1", "state": {"input": "$1"}}]},
    {"match": "X(\\d)", "child_set": ["entry", {"type": "nope", "id": "$1", "state": {"a": "$1"}},
                                      {"type": "output", "id": "$1", "state": ["input"]}]},
    "not a rule",
])
def test_a_malformed_child_rule_is_passed_over(rule):
    sim = _sim([rule], child_entity_types=_OUTPUT) if isinstance(rule, dict) else None
    if sim is None:
        with pytest.raises(AttributeError):
            _sim([rule], child_entity_types=_OUTPUT)
        return
    assert _child(sim) == {}


def test_no_child_types_reads_no_child_rules():
    sim = _sim([{"match": "Out(\\d+) In(\\d+)", "child_set": [
        {"type": "output", "id": "$1", "state": {"input": "$2"}},
    ]}])
    assert _child(sim) == {} and _lists(sim) == {}


# ── child lists ──


def test_a_list_by_id_and_by_place():
    sim = _sim([
        {"match": "Map (.*)", "child_list": {
            "type": "output", "element": "(\\d+)>(\\d+)", "group": "$1",
            "id": {"group": "$2", "map": {"9": 1}}, "separator": ",",
            "state": {"input": "$1", "mute": {"group": 1, "map": {"Y": True, "N": False}}},
        }},
        {"match": "Lvl(\\d+) (.*)", "child_list": {
            "type": "output", "element": "(\\d+)", "group": 2, "start": "$1",
            "state": {"level": "$1", "audio": 5, "input": "$1"},
        }},
    ], child_entity_types=_OUTPUT)
    assert _lists(sim) == {
        ("output", "input"): ("Map {list}", "{value}>{child_id}", ",", False, 1, None, {"1": "9"}),
        ("output", "mute"): ("Map {list}", "{value}>{child_id}", ",", False, 1,
                             {"true": "Y", "false": "N"}, {"1": "9"}),
        ("output", "level"): ("Lvl{start} {list}", "{value}", "", True, 1, None, None),
    }


@pytest.mark.parametrize("start, outer, first", [
    (3, "L {list}", 3),
    (True, "L {list}", 1),
    ("$9", None, None),
    ("x", "L {list}", 1),
])
def test_a_list_start_is_a_number_or_a_capture_of_the_reply(start, outer, first):
    sim = _sim([{"match": "L (.*)", "child_list": {
        "type": "output", "element": "(\\d+)", "start": start, "state": {"input": "$1"},
    }}], child_entity_types=_OUTPUT)
    got = _lists(sim).get(("output", "input"))
    assert (got[0], got[4]) == (outer, first) if outer else got is None


def test_a_start_capture_is_not_a_slot_when_items_name_their_child():
    sim = _sim([{"match": "L(\\d) (.*)", "child_list": {
        "type": "output", "element": "(\\d+):(\\d+)", "group": "$2", "start": "$1",
        "id": "$2", "state": {"input": "$1"},
    }}], child_entity_types=_OUTPUT)
    assert _lists(sim)[("output", "input")][:2] == ("L0 {list}", "{value}:{child_id}")


@pytest.mark.parametrize("spec", [
    {"type": "nope", "element": "(\\d)", "state": {"input": "$1"}},
    {"type": "output", "element": 5, "state": {"input": "$1"}},
    {"type": "output", "element": "(\\d)", "state": ["input"]},
    {"type": "output", "element": "(\\d)", "group": "$0", "state": {"input": "$1"}},
    {"type": "output", "element": "(\\d)", "id": "x", "state": {"input": "$1"}},
    {"type": "output", "element": "(\\d)", "id": {"group": 0}, "state": {"input": "$1"}},
    {"type": "output", "element": "(\\d)", "id": "$1", "state": {"input": "$1"}},
    {"type": "output", "element": "(\\d)", "state": {"input": "$5"}},
    {"type": "output", "element": "(\\d)", "state": {"input": {"map": {"1": 1}}}},
    {"type": "output", "element": "(?<=a)(\\d)", "state": {"input": "$1"}},
])
def test_a_list_rule_or_variable_the_reader_cannot_use_gives_nothing(spec):
    sim = _sim([{"match": "L (.*)", "child_list": spec}], child_entity_types=_OUTPUT)
    assert _lists(sim) == {}


def test_a_list_whose_reply_cannot_be_modelled_and_a_non_dict_rule_give_nothing():
    sim = _sim([
        {"match": "(?<=x)L (.*)", "child_list": {
            "type": "output", "element": "(\\d)", "state": {"input": "$1"}}},
        {"match": 5, "child_list": {"type": "output", "element": "(\\d)", "state": {"input": "$1"}}},
        {"match": "L (.*)", "child_list": "output"},
    ], child_entity_types=_OUTPUT)
    assert _lists(sim) == {}


def test_the_first_list_rule_for_a_variable_wins():
    sim = _sim([
        {"match": "A (.*)", "child_list": {
            "type": "output", "element": "(\\d)", "state": {"input": "$1"}}},
        {"match": "B (.*)", "child_list": {
            "type": "output", "element": "(\\d)", "state": {"input": "$1", "audio": "$1"}}},
    ], child_entity_types=_OUTPUT)
    assert {k: v[0] for k, v in _lists(sim).items()} == {
        ("output", "input"): "A {list}", ("output", "audio"): "B {list}",
    }
