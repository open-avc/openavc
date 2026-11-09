"""What the receive-side compile does with the entries nobody else tests.

``compile_driver`` turns a driver's response rules into the tables the runtime
matches against, and the simulator and validator read the queries a driver sends
besides its poll (``follow_up_queries``, ``read_back_queries``). The loader
refuses a malformed driver up front, so the driver suites only ever hand these a
good one; a hand-installed file can still reach them. These pin what each one
returns, and the warning it logs, for every shape it skips: a child_set entry in
each wire form (regex, OSC, JSON), a child_list block, a throttle, a set:
shorthand, and the read-back queries the simulator answers. On an invented Acme
device.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.drivers.compiled_protocol import (
    compile_driver,
    follow_up_queries,
    read_back_queries,
)

_ZONE = {
    "label": "Zone",
    "state_variables": {
        "level": {"type": "integer"},
        "name": {"type": "string"},
        "odd": "not a definition",
    },
}


def _compile(responses: list[dict[str, Any]], **over: Any):
    definition: dict[str, Any] = {
        "state_variables": {
            "power": {"type": "boolean"},
            "volume": {"type": "integer", "scale": 0.5},
            "label": {"type": "string"},
        },
        "child_entity_types": {"zone": _ZONE},
        "responses": responses,
    }
    definition.update(over)
    return compile_driver(definition, {"unit": "7"}, device_id="acme1")


def _regex_children(child_set: Any, caplog) -> list[dict[str, Any]]:
    with caplog.at_level(logging.WARNING):
        compiled = _compile([{"match": r"Z(\d+) L(\d+)", "child_set": child_set}])
    return compiled.responses[0][2]


def _osc_children(child_set: Any, caplog) -> list[dict[str, Any]]:
    with caplog.at_level(logging.WARNING):
        compiled = _compile([{"address": "/zone/*/level", "child_set": child_set}])
    return compiled.osc_responses[0][2]


def _json_children(child_set: Any, caplog) -> list[dict[str, Any]]:
    with caplog.at_level(logging.WARNING):
        compiled = _compile([{"json": True, "child_set": child_set}])
    return compiled.json_responses[0][4]


# ── child_set, regex form ──


def test_regex_child_set_that_is_not_a_list_compiles_to_nothing(caplog):
    assert _regex_children({"type": "zone"}, caplog) == []
    assert caplog.messages == []


def test_regex_child_set_skips_an_entry_that_is_not_an_object_silently(caplog):
    good = {"type": "zone", "id": "$1", "state": {"level": "$2"}}
    out = _regex_children(["zone", good], caplog)
    assert [e["id"] for e in out] == [("group", 1)]
    assert caplog.messages == []


def test_regex_child_set_unknown_type_warns_and_skips(caplog):
    out = _regex_children([{"type": "speaker", "id": "$1", "state": {"level": "$2"}}], caplog)
    assert out == []
    assert caplog.messages == ["[acme1] child_set: unknown child type 'speaker'; skipping entry"]


def test_regex_child_set_compiles_every_id_form_in_order(caplog):
    out = _regex_children(
        [
            {"type": "zone", "id": "$1", "state": {"level": "$2"}},
            {"type": "zone", "id": "$0", "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": "$1", "map": {0: "a", "1": "b"}}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": 2, "map": {}}, "state": {"level": "$2"}},
            {"type": "zone", "id": {"group": "3", "map": ["x"]}, "state": {"level": "$2"}},
            {"type": "zone", "id": 4, "state": {"level": "$2"}},
            {"type": "zone", "id": "main", "state": {"level": "$2"}},
        ],
        caplog,
    )
    assert [(e["id"], e["id_map"]) for e in out] == [
        (("group", 1), None),
        (("group", 0), None),
        (("group", 1), {"0": "a", "1": "b"}),
        (("group", 2), None),
        (("group", 3), None),
        (("literal", 4), None),
        (("literal", "main"), None),
    ]
    assert [list(e) for e in out] == [["type", "id", "id_map", "props"]] * 7
    assert caplog.messages == []


@pytest.mark.parametrize(
    "cid, message",
    [
        ({"group": "x"}, "[acme1] child_set: id group 'x' is not a capture ref; skipping entry"),
        ({"group": "$x"}, "[acme1] child_set: id group '$x' is not a capture ref; skipping entry"),
        ({"map": {"0": "a"}}, "[acme1] child_set: id group None is not a capture ref; skipping entry"),
        ("$x", "[acme1] child_set: id '$x' is not a numeric capture ref ($1, $2, ...); skipping entry"),
        (None, "[acme1] child_set: missing 'id'; skipping entry"),
    ],
)
def test_regex_child_set_bad_id_warns_and_skips(caplog, cid, message):
    entry: dict[str, Any] = {"type": "zone", "state": {"level": "$2"}}
    if cid is not None:
        entry["id"] = cid
    assert _regex_children([entry], caplog) == []
    assert caplog.messages == [message]


def test_regex_child_set_state_that_is_not_an_object_skips_without_a_word(caplog):
    assert _regex_children([{"type": "zone", "id": "$1", "state": ["level"]}], caplog) == []
    assert _regex_children([{"type": "zone", "id": "$1"}], caplog) == []
    assert caplog.messages == []


def test_regex_child_set_props_every_form(caplog):
    out = _regex_children(
        [
            {
                "type": "zone",
                "id": "$1",
                "state": {
                    "level": "$2",
                    "name": {"group": "2", "map": {"1": "One"}},
                    "online": "$3",
                    "odd": "$2",
                    "undeclared": {"value": 5, "type": "integer", "map": "nope"},
                    "fault": {"value": "x"},
                    "label": "Kitchen",
                },
            }
        ],
        caplog,
    )
    assert out[0]["props"] == [
        {"prop": "level", "group": 2, "type": "integer"},
        {"prop": "name", "type": "string", "group": 2, "map": {"1": "One"}},
        {"prop": "online", "group": 3, "type": "boolean"},
        {"prop": "odd", "group": 2, "type": "string"},
        {"prop": "undeclared", "type": "integer", "value": 5},
        {"prop": "fault", "type": "string", "value": "x"},
        {"prop": "label", "value": "Kitchen", "type": "string"},
    ]
    assert [list(p) for p in out[0]["props"][:2]] == [
        ["prop", "group", "type"],
        ["prop", "type", "group", "map"],
    ]
    assert caplog.messages == []


def test_regex_child_set_bad_props_are_skipped_one_with_a_warning(caplog):
    out = _regex_children(
        [
            {
                "type": "zone",
                "id": "$1",
                "state": {
                    "level": {"group": "x"},
                    "name": {"map": {"1": "One"}},
                    "online": "$x",
                    "label": "$2",
                },
            }
        ],
        caplog,
    )
    assert out[0]["props"] == [{"prop": "label", "group": 2, "type": "string"}]
    assert caplog.messages == [
        "[acme1] child_set: state 'online' ref '$x' is not a numeric capture ref; skipping"
    ]


def test_regex_child_set_entry_with_no_usable_prop_is_dropped(caplog):
    out = _regex_children(
        [{"type": "zone", "id": "$1", "state": {"level": {"group": None}}}], caplog
    )
    assert out == []
    assert caplog.messages == []


def test_a_declared_child_variable_wins_over_the_reserved_one(caplog):
    types = {"zone": {"state_variables": {"online": {"type": "string"}}}}
    with caplog.at_level(logging.WARNING):
        compiled = _compile(
            [
                {"match": r"Z(\d+) (\w+)", "child_set": [{"type": "zone", "id": "$1", "state": {"online": "$2"}}]},
                {"address": "/z", "child_set": [{"type": "zone", "id": 1, "state": {"online": {"arg": 0}}}]},
                {"json": True, "child_set": [{"type": "zone", "id": 1, "state": {"online": "z.on"}}]},
            ],
            child_entity_types=types,
        )
    assert compiled.responses[0][2][0]["props"] == [{"prop": "online", "group": 2, "type": "string"}]
    assert compiled.osc_responses[0][2][0]["props"] == [{"prop": "online", "type": "string", "arg": 0}]
    assert compiled.json_responses[0][4][0]["props"] == [{"prop": "online", "key": "z.on", "type": "string"}]


def test_a_child_type_with_no_state_variables_still_types_the_reserved_props(caplog):
    types = {"zone": {"state_variables": None}}
    with caplog.at_level(logging.WARNING):
        compiled = _compile(
            [{"match": r"Z(\d+) (\w+)", "child_set": [{"type": "zone", "id": "$1", "state": {"online": "$2", "x": "$2"}}]}],
            child_entity_types=types,
        )
    assert compiled.responses[0][2][0]["props"] == [
        {"prop": "online", "group": 2, "type": "boolean"},
        {"prop": "x", "group": 2, "type": "string"},
    ]


# ── child_set, OSC form ──


def test_osc_child_set_that_is_not_a_list_compiles_to_nothing(caplog):
    assert _osc_children("zone", caplog) == []
    assert caplog.messages == []


def test_osc_child_set_compiles_every_id_form(caplog):
    out = _osc_children(
        [
            7,
            {"type": "zone", "id": {"segment": "1", "map": {"07": 7}}, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": {"segment": 2, "map": {}}, "state": {"level": {"arg": "1"}}},
            {"type": "zone", "id": "main", "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": 3, "state": {"level": {"arg": 0}}},
        ],
        caplog,
    )
    assert [(e["id"], e["id_map"]) for e in out] == [
        (("segment", 1), {"07": 7}),
        (("segment", 2), None),
        (("literal", "main"), None),
        (("literal", 3), None),
    ]
    assert [list(e) for e in out] == [["type", "id", "id_map", "props"]] * 4
    assert out[1]["props"] == [{"prop": "level", "type": "integer", "arg": 1}]
    assert caplog.messages == []


@pytest.mark.parametrize(
    "cid, message",
    [
        ({"segment": "x"}, "[acme1] child_set: OSC id needs a 'segment' index (got {'segment': 'x'}); skipping entry"),
        ({"map": {}}, "[acme1] child_set: OSC id needs a 'segment' index (got {'map': {}}); skipping entry"),
        ("$1", "[acme1] child_set: OSC rules have no capture groups — id must be {segment: N} or a literal (got '$1'); skipping entry"),
        (None, "[acme1] child_set: OSC rules have no capture groups — id must be {segment: N} or a literal (got None); skipping entry"),
    ],
)
def test_osc_child_set_bad_id_warns_and_skips(caplog, cid, message):
    entry: dict[str, Any] = {"type": "zone", "state": {"level": {"arg": 0}}}
    if cid is not None:
        entry["id"] = cid
    assert _osc_children([entry], caplog) == []
    assert caplog.messages == [message]


def test_osc_child_set_unknown_type_and_bad_state(caplog):
    out = _osc_children(
        [
            {"type": "speaker", "id": 1, "state": {"level": {"arg": 0}}},
            {"type": "zone", "id": 1, "state": "level"},
        ],
        caplog,
    )
    assert out == []
    assert caplog.messages == ["[acme1] child_set: unknown child type 'speaker'; skipping entry"]


def test_osc_child_set_props_every_form(caplog):
    out = _osc_children(
        [
            {
                "type": "zone",
                "id": 1,
                "state": {
                    "level": {"arg": 1, "map": {"0": 0}, "type": "number"},
                    "name": {"value": "Lobby", "map": None},
                    "online": True,
                    "odd": {"arg": 0},
                    "fault": {"arg": "x"},
                    "label": {"map": {}},
                    "other": "$2",
                },
            }
        ],
        caplog,
    )
    assert out[0]["props"] == [
        {"prop": "level", "type": "number", "arg": 1, "map": {"0": 0}},
        {"prop": "name", "type": "string", "value": "Lobby"},
        {"prop": "online", "value": True, "type": "boolean"},
        {"prop": "odd", "type": "string", "arg": 0},
    ]
    assert list(out[0]["props"][0]) == ["prop", "type", "arg", "map"]
    assert caplog.messages == [
        "[acme1] child_set: state 'other' — OSC rules have no capture groups; use {arg: N}. Skipping"
    ]


def test_osc_child_set_entry_with_no_usable_prop_is_dropped(caplog):
    assert _osc_children([{"type": "zone", "id": 1, "state": {"other": "$2"}}], caplog) == []


# ── child_set, JSON form ──


def test_json_child_set_that_is_not_a_list_compiles_to_nothing(caplog):
    assert _json_children(None, caplog) == []
    assert caplog.messages == []


def test_json_child_set_compiles_a_literal_id_and_every_prop_form(caplog):
    out = _json_children(
        [
            "zone",
            {
                "type": "zone",
                "id": 2,
                "state": {
                    "level": "zones.2.level",
                    "name": {"path": "zones.2.name", "map": {"a": "A"}},
                    "online": {"key": "zones.2.on", "contains": "yes", "type": "string", "map": 3},
                    "odd": {"type": "integer"},
                    "label": 12,
                },
            },
        ],
        caplog,
    )
    assert out == [
        {
            "type": "zone",
            "id": ("literal", 2),
            "props": [
                {"prop": "level", "key": "zones.2.level", "type": "integer"},
                {"prop": "name", "key": "zones.2.name", "type": "string", "map": {"a": "A"}},
                {"prop": "online", "key": "zones.2.on", "type": "string", "contains": "yes"},
                {"prop": "label", "key": "12", "type": "string"},
            ],
        }
    ]
    assert list(out[0]) == ["type", "id", "props"]
    assert list(out[0]["props"][1]) == ["prop", "key", "type", "map"]
    assert caplog.messages == []


def test_json_child_set_key_wins_over_path(caplog):
    out = _json_children(
        [{"type": "zone", "id": 1, "state": {"level": {"key": "a", "path": "b"}}}], caplog
    )
    assert out[0]["props"] == [{"prop": "level", "key": "a", "type": "integer"}]


@pytest.mark.parametrize(
    "cid, message",
    [
        (None, "[acme1] child_set: a json rule has no captures — id must be a literal child id (got None); skipping entry"),
        ("$1", "[acme1] child_set: a json rule has no captures — id must be a literal child id (got '$1'); skipping entry"),
    ],
)
def test_json_child_set_bad_id_warns_and_skips(caplog, cid, message):
    entry: dict[str, Any] = {"type": "zone", "state": {"level": "a"}}
    if cid is not None:
        entry["id"] = cid
    assert _json_children([entry], caplog) == []
    assert caplog.messages == [message]


def test_json_child_set_unknown_type_bad_state_and_no_props(caplog):
    out = _json_children(
        [
            {"type": "speaker", "id": 1, "state": {"level": "a"}},
            {"type": "zone", "id": 1, "state": "a"},
            {"type": "zone", "id": 1, "state": {"level": {"map": {}}}},
        ],
        caplog,
    )
    assert out == []
    assert caplog.messages == ["[acme1] child_set: unknown child type 'speaker'; skipping entry"]


# ── child_list ──


def _child_list(block: Any, caplog) -> list[dict[str, Any]]:
    with caplog.at_level(logging.WARNING):
        compiled = _compile([{"match": r"L:(.*)", "child_list": block}])
    return compiled.responses[0][2]


def test_child_list_that_is_not_an_object_adds_nothing(caplog):
    assert _child_list(["zone"], caplog) == []
    assert caplog.messages == []


def test_child_list_compiles_its_defaults(caplog):
    out = _child_list({"type": "zone", "element": r"(\d+)", "state": {"level": "$1"}}, caplog)
    assert len(out) == 1
    item = out[0]
    assert list(item) == ["type", "list", "id_map", "props"]
    assert item["type"] == "zone"
    assert item["id_map"] is None
    assert item["props"] == [{"prop": "level", "group": 1, "type": "integer"}]
    spec = item["list"]
    assert list(spec) == ["group", "element", "separator", "id_group", "start"]
    assert spec["element"].pattern == r"(\d+)"
    assert (spec["group"], spec["separator"], spec["id_group"], spec["start"]) == (
        1, None, None, ("literal", 1),
    )
    assert caplog.messages == []


@pytest.mark.parametrize(
    "over, expected",
    [
        ({"group": "$2", "separator": ",", "id": "$1", "start": 0}, (2, ",", 1, ("literal", 0))),
        ({"group": 3, "separator": "", "id": 2, "start": "$2"}, (3, None, 2, ("group", 2))),
        ({"separator": 5, "id": {"group": "$2", "map": {1: "a"}}, "start": True}, (1, None, 2, ("literal", 1))),
        ({"id": {"group": 1, "map": {}}, "start": 2.5}, (1, None, 1, ("literal", 1))),
    ],
)
def test_child_list_compiles_each_option(caplog, over, expected):
    block = {"type": "zone", "element": r"(\d+):(\d+)", "state": {"level": "$2"}, **over}
    out = _child_list(block, caplog)
    spec = out[0]["list"]
    assert (spec["group"], spec["separator"], spec["id_group"], spec["start"]) == expected
    assert caplog.messages == []


def test_child_list_carries_the_id_map(caplog):
    block = {"type": "zone", "element": r"(\d+)", "id": {"group": "$1", "map": {1: "a"}}, "state": {"level": "$1"}}
    assert _child_list(block, caplog)[0]["id_map"] == {"1": "a"}


@pytest.mark.parametrize(
    "over, message",
    [
        ({"type": "speaker"}, "[acme1] child_list: unknown child type 'speaker'; skipping"),
        ({"group": "$x"}, "[acme1] child_list: group '$x' is not a capture ref; skipping"),
        ({"group": 0}, "[acme1] child_list: group 0 is not a capture ref; skipping"),
        ({"group": "$0"}, "[acme1] child_list: group '$0' is not a capture ref; skipping"),
        ({"group": True}, "[acme1] child_list: group True is not a capture ref; skipping"),
        ({"group": "2"}, "[acme1] child_list: group '2' is not a capture ref; skipping"),
        ({"element": "("}, "[acme1] child_list: invalid element pattern: missing ), unterminated subpattern at position 0"),
        ({"element": ""}, "[acme1] child_list: missing element; skipping"),
        ({"element": None}, "[acme1] child_list: missing element; skipping"),
        ({"id": "x"}, "[acme1] child_list: id 'x' is not a capture ref of the item; skipping"),
        ({"id": {"map": {1: 2}}}, "[acme1] child_list: id {'map': {1: 2}} is not a capture ref of the item; skipping"),
        ({"id": 0}, "[acme1] child_list: id 0 is not a capture ref of the item; skipping"),
        ({"start": "x"}, "[acme1] child_list: start 'x' is not a number or a capture ref; skipping"),
        ({"state": {"level": {"group": "x"}}}, "[acme1] child_list: no usable state; skipping"),
        ({"state": None}, "[acme1] child_list: no usable state; skipping"),
    ],
)
def test_child_list_bad_block_warns_and_adds_nothing(caplog, over, message):
    block = {"type": "zone", "element": r"(\d+)", "state": {"level": "$1"}, **over}
    assert _child_list(block, caplog) == []
    assert caplog.messages == [message]


def test_child_list_rides_after_the_rule_child_set_entries(caplog):
    with caplog.at_level(logging.WARNING):
        compiled = _compile(
            [
                {
                    "match": r"L:(.*)",
                    "child_set": [{"type": "zone", "id": 1, "state": {"level": "$1"}}],
                    "child_list": {"type": "zone", "element": r"(\d+)", "state": {"level": "$1"}},
                }
            ]
        )
    kinds = ["list" in cm for cm in compiled.responses[0][2]]
    assert kinds == [False, True]


# ── the rest of a rule ──


@pytest.mark.parametrize(
    "raw, expected, warned",
    [
        (None, None, False),
        (0, None, False),
        (-1, None, False),
        ("2", {"window": 2.0, "last": {}}, False),
        (0.25, {"window": 0.25, "last": {}}, False),
        ("soon", None, True),
        ([1], None, True),
    ],
)
def test_throttle_compiles_or_is_dropped(caplog, raw, expected, warned):
    rule: dict[str, Any] = {"match": "P(\\d)"}
    if raw is not None:
        rule["throttle"] = raw
    with caplog.at_level(logging.WARNING):
        compiled = _compile([rule])
    assert compiled.responses[0][3] == expected
    assert caplog.messages == ([f"Invalid response throttle {raw!r} ignored"] if warned else [])


def test_each_rule_gets_its_own_throttle_state():
    compiled = _compile([{"match": "A", "throttle": 1}, {"match": "B", "throttle": 1}])
    assert compiled.responses[0][3] is not compiled.responses[1][3]


@pytest.mark.parametrize(
    "require, expected",
    [
        (None, ()),
        ("status", ("status",)),
        (["a", "", None, 3], ("a", "3")),
        ({"a": 1}, ()),
    ],
)
def test_json_rule_require_scope(require, expected):
    rule: dict[str, Any] = {"json": True, "set": {"power": "on"}}
    if require is not None:
        rule["require"] = require
    assert _compile([rule]).json_responses[0][2] == expected


def test_json_rule_tuple_carries_every_slot_in_order():
    rule = {
        "json": True,
        "set": {"volume": "vol"},
        "throttle": 1,
        "only_when": {"key": "power", "equals": True},
        "child_set": [{"type": "zone", "id": 1, "state": {"level": "z.l"}}],
    }
    mappings, throttle, require, condition, children = _compile([rule]).json_responses[0]
    assert mappings == [{"state": "volume", "key": "vol", "type": "integer", "convert": {"scale": 0.5}}]
    assert throttle == {"window": 1.0, "last": {}}
    assert require == ()
    assert condition == {"key": "power", "equals": True}
    assert children == [{"type": "zone", "id": ("literal", 1), "props": [{"prop": "level", "key": "z.l", "type": "integer"}]}]


def test_osc_rule_tuple_carries_every_slot_in_order():
    rule = {
        "address": "/unit/{unit}/power",
        "mappings": [{"state": "volume", "arg": 0}],
        "throttle": 2,
        "only_when": {"key": "power", "equals": True},
        "then_query": ["ignored on osc"],
    }
    compiled = _compile([rule])
    assert compiled.osc_responses == [
        (
            "/unit/7/power",
            [{"state": "volume", "arg": 0, "convert": {"scale": 0.5}}],
            [],
            {"window": 2.0, "last": {}},
            {"key": "power", "equals": True},
        )
    ]
    assert compiled.responses == [] and compiled.json_responses == []


def test_regex_rule_mappings_and_set_merge_in_order(caplog):
    shared_mapping = {"group": 1, "state": "volume"}
    rule = {
        "match": "V(\\d+) P(\\w+) U{unit}",
        "mappings": [shared_mapping, {"group": 2, "state": "power", "type": "string"}, "odd", {"group": 3}],
        "set": {"power": "$2", "label": "fixed", "volume": "$x", "missing": "$0"},
        "then_query": ["Q1", "Q2"],
        "only_when": {},
        "after_json": True,
    }
    with caplog.at_level(logging.WARNING):
        compiled = _compile([rule])
    pattern, mappings, children, throttle, condition, follow = compiled.responses[0]
    assert pattern.pattern == "V(\\d+) P(\\w+) U7"
    assert mappings == [
        {"group": 1, "state": "volume", "type": "integer", "convert": {"scale": 0.5}},
        {"group": 2, "state": "power", "type": "string"},
        "odd",
        {"group": 3},
        {"group": 2, "state": "power", "type": "boolean"},
        {"group": 0, "state": "label", "value": "fixed", "type": "string"},
        {"group": 0, "state": "missing", "type": "string"},
    ]
    assert shared_mapping == {"group": 1, "state": "volume"}
    assert children == [] and throttle is None and condition is None
    assert follow == ["Q1", "Q2"]
    assert compiled.after_json_responses == [compiled.responses[0]]
    assert compiled.after_json_responses[0] is compiled.responses[0]
    assert caplog.messages == [
        "[acme1] set-shorthand reference '$x' for state 'volume' is not a numeric "
        "group ($1, $2, ...); skipping this mapping"
    ]


def test_regex_rule_mapping_without_a_declared_type_keeps_what_it_has():
    rule = {"match": "X(\\d)", "mappings": [{"group": 1, "state": "nope"}, {"group": 1, "state": ""}]}
    assert _compile([rule]).responses[0][1] == [
        {"group": 1, "state": "nope"},
        {"group": 1, "state": ""},
    ]


def test_regex_rules_skip_an_empty_match_and_warn_on_a_bad_pattern(caplog):
    with caplog.at_level(logging.WARNING):
        compiled = _compile(
            [{"match": ""}, {"set": {"power": "$1"}}, {"match": "(unclosed"}, {"match": "OK"}]
        )
    assert [entry[0].pattern for entry in compiled.responses] == ["OK"]
    assert compiled.after_json_responses == []
    assert caplog.messages == [
        "[acme1] Invalid response pattern '(unclosed': missing ), unterminated subpattern at position 0"
    ]


def test_conversions_attach_to_every_table_and_skip_final_values():
    definition_types = {
        "zone": {"state_variables": {"level": {"type": "integer", "offset": -10, "unknown": [255]}}},
    }
    compiled = _compile(
        [
            {
                "match": "Z(\\d) L(\\d+)",
                "set": {"volume": "$2", "label": "x"},
                "child_set": [{"type": "zone", "id": "$1", "state": {"level": "$2", "online": "$1"}}],
                "child_list": {"type": "zone", "element": "(\\d)", "state": {"level": "$1"}},
            },
            {
                "address": "/z",
                "mappings": [{"state": "volume", "arg": 0}, {"state": "volume", "value": 3}],
                "child_set": [{"type": "zone", "id": 1, "state": {"level": {"arg": 0}}}],
            },
            {
                "json": True,
                "set": {"volume": "v", "power": {"key": "list", "contains": "on"}},
                "child_set": [{"type": "zone", "id": 1, "state": {"level": {"key": "l", "contains": 1}}}],
            },
        ],
        child_entity_types=definition_types,
    )
    regex = compiled.responses[0]
    assert regex[1] == [
        {"group": 2, "state": "volume", "type": "integer", "convert": {"scale": 0.5}},
        {"group": 0, "state": "label", "value": "x", "type": "string"},
    ]
    assert regex[2][0]["props"] == [
        {"prop": "level", "group": 2, "type": "integer", "convert": {"offset": -10, "unknown": [255]}},
        {"prop": "online", "group": 1, "type": "boolean"},
    ]
    assert regex[2][1]["props"] == [
        {"prop": "level", "group": 1, "type": "integer", "convert": {"offset": -10, "unknown": [255]}},
    ]
    osc = compiled.osc_responses[0]
    assert osc[1] == [
        {"state": "volume", "arg": 0, "convert": {"scale": 0.5}},
        {"state": "volume", "value": 3},
    ]
    assert osc[2][0]["props"] == [
        {"prop": "level", "type": "integer", "arg": 0, "convert": {"offset": -10, "unknown": [255]}},
    ]
    json_rule = compiled.json_responses[0]
    assert json_rule[0] == [
        {"state": "volume", "key": "v", "type": "integer", "convert": {"scale": 0.5}},
        {"state": "power", "key": "list", "type": "boolean", "map": None, "contains": "on"},
    ]
    assert json_rule[4][0]["props"] == [
        {"prop": "level", "key": "l", "type": "integer", "contains": 1},
    ]


def test_compile_leaves_the_definition_alone():
    mapping = {"group": 1, "state": "volume"}
    child_props = {"level": "$2"}
    definition: dict[str, Any] = {
        "state_variables": {"volume": {"type": "integer", "scale": 2}},
        "child_entity_types": {"zone": {"state_variables": {"level": {"type": "integer", "scale": 3}}}},
        "responses": [
            {"match": "V(\\d) (\\d)", "mappings": [mapping], "child_set": [{"type": "zone", "id": "$1", "state": child_props}]},
            {"address": "/v", "mappings": [{"state": "volume", "arg": 0}]},
        ],
    }
    first = compile_driver(definition, {})
    second = compile_driver(definition, {})
    assert mapping == {"group": 1, "state": "volume"}
    assert definition["responses"][1]["mappings"] == [{"state": "volume", "arg": 0}]
    assert first.responses[0][1] is not second.responses[0][1]
    assert first.osc_responses[0][1] is not definition["responses"][1]["mappings"]


def test_a_definition_with_no_responses_or_child_types_compiles_empty():
    compiled = compile_driver({}, {})
    assert (compiled.responses, compiled.osc_responses, compiled.json_responses, compiled.after_json_responses) == (
        [], [], [], [],
    )


# ── the queries a driver sends besides its poll ──


def _read_back_definition(**over: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "state_variables": {"volume": {"type": "integer"}, "power": {"type": "boolean"}},
        "child_entity_types": {
            "zone": {"state_variables": {"level": {"type": "integer"}}},
        },
        "commands": {
            "set_volume": {
                "send": "VOL {level}\r",
                "params": {"level": {"type": "integer"}},
                "sets": {"volume": "{level}"},
                "read_back": "VOL?",
            },
            "set_zone": {
                "send": "Z{zone:02d} {level}\r",
                "params": {"zone": {"type": "child_id", "child_type": "zone"}, "level": {"type": "integer"}},
                "sets": {"level": "{level}"},
                "read_back": ["Z{zone:02d}?", "set_volume", "", 5, "Z{level}?"],
            },
            "power_on": {"send": "PWR 1\r", "read_back": ["PWR?", "PWR?"]},
        },
    }
    definition.update(over)
    return definition


def test_read_back_queries_every_shape():
    assert read_back_queries(_read_back_definition()) == [
        {"send": "VOL?", "query_for": "volume"},
        {"each_child": "zone", "send": "Z{child_id:02d}?", "query_for": "level"},
        "PWR?",
    ]


def test_read_back_queries_skip_what_the_poll_start_up_and_follow_ups_send():
    definition = _read_back_definition(
        polling={"queries": [{"send": "VOL?"}, {"query_for": "x"}, 7]},
        on_connect=["PWR?"],
        responses=[
            {"match": "Z(\\d+)", "child_set": [{"type": "zone", "id": "$1", "state": {"level": "$1"}}],
             "then_query": ["Z{child_id:02d}?"]},
        ],
    )
    assert read_back_queries(definition) == []


def test_read_back_queries_skip_a_command_that_is_not_an_object():
    definition = _read_back_definition()
    definition["commands"]["broken"] = "PWR?"
    definition["commands"]["none"] = None
    assert len(read_back_queries(definition)) == 3


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["commands"]["set_zone"]["params"]["zone"].update(type="integer"),
        lambda d: d["commands"]["set_zone"]["params"]["zone"].update(child_type="speaker"),
        lambda d: d["commands"]["set_zone"]["params"].update(zone="child_id"),
        lambda d: d["commands"]["set_zone"].update(sets={"level": 1, "other": 2}),
        lambda d: d["commands"]["set_zone"].update(sets={"volume": 1}),
        lambda d: d["commands"]["set_zone"].update(sets=["level"]),
        lambda d: d["child_entity_types"]["zone"].update(state_variables=None),
        lambda d: d["commands"]["set_zone"].update(read_back="Z{zone}{level}?"),
        lambda d: d.update(child_entity_types=["zone"]),
    ],
)
def test_read_back_queries_leave_out_a_child_query_they_cannot_answer(change):
    definition = _read_back_definition()
    change(definition)
    assert {"each_child": "zone", "send": "Z{child_id:02d}?", "query_for": "level"} not in read_back_queries(definition)


def test_read_back_query_without_one_declared_variable_is_the_bare_line():
    definition = _read_back_definition()
    definition["commands"]["set_volume"]["sets"] = {"volume": 1, "power": 2}
    definition["commands"]["power_on"]["sets"] = {"nope": 1}
    definition["commands"]["power_on"]["params"] = "none"
    definition["state_variables"] = "none"
    assert read_back_queries(definition)[0] == "VOL?"
    assert read_back_queries(definition)[-1] == "PWR?"


def test_read_back_queries_with_no_commands_object():
    assert read_back_queries({"commands": ["x"]}) == []
    assert read_back_queries({}) == []


def test_follow_up_queries_every_shape():
    definition = {
        "polling": {"queries": ["P?"]},
        "on_connect": ["INIT"],
        "responses": [
            "not a rule",
            {"json": True, "then_query": ["J?"]},
            {"address": "/a", "then_query": ["O?"]},
            {"match": "A", "then_query": "not a list"},
            {
                "match": "Z(\\d)",
                "child_set": [{"type": "zone", "id": "$1"}, {"type": "zone", "id": 2}, "odd"],
                "then_query": [
                    "Z{child_id}?",
                    {"send": "Z{child_id:02d} L?", "delay_ms": 5},
                    {"each_child": "other", "send": "X{child_id}"},
                    "P?",
                    "INIT",
                    "PLAIN?",
                    {"query_for": "x"},
                    7,
                ],
            },
            {
                "match": "M(\\d)",
                "child_set": [{"type": "zone", "id": "$1"}, {"type": "out", "id": "$1"}],
                "then_query": ["Z{child_id}?", "M{child_id}?", "PLAIN?"],
            },
            {"match": "N", "child_set": "zone", "then_query": ["N{child_id}?"]},
        ],
    }
    assert follow_up_queries(definition) == [
        {"send": "Z{child_id}?", "each_child": "zone"},
        {"send": "Z{child_id:02d} L?", "delay_ms": 5, "each_child": "zone"},
        {"each_child": "other", "send": "X{child_id}"},
        "PLAIN?",
        {"query_for": "x"},
        7,
        "Z{child_id}?",
        "M{child_id}?",
        "N{child_id}?",
    ]


def test_follow_up_queries_compare_whole_entries_with_the_poll():
    definition = {
        "polling": {"queries": [{"send": "A?", "delay_ms": 1}], "when_state": "x"},
        "on_connect": "not a list",
        "responses": [{"match": "A", "then_query": ["A?", {"send": "A?", "delay_ms": 1}]}],
    }
    assert follow_up_queries(definition) == ["A?"]
    assert follow_up_queries({"polling": "x", "responses": None}) == []
