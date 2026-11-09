"""Pins for the query entries' load-time checks, through the public door.

A query entry is one line of ``polling.queries``, of ``on_connect`` or of a
response rule's ``then_query``: a bare string, ``{send}`` with an optional
``when:`` / ``when_state:`` gate and ``query_for:``, a per-child
``{each_child, send}`` with an optional ``page_size:``, or (on ``on_connect``
only) an OSC ``{address, args}``. ``polling.when_state`` gates the whole poll.

Each case is a set of top-level keys over one driver that declares device
variables of every type, config fields in all three sources, and child types
with and without a roster. Two ``child_list`` rules fill a list: one that
places each page (``zone``, which names each item) and one that counts from a
fixed start (``out``). The expected value is every error
``validate_driver_issues`` reports for the whole definition, in order, each
with the section tag the Driver Builder anchors it to.

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
        "id_format": {"type": "integer", "min": 1, "max": 16},
        "state_variables": {
            "level": {"type": "number", "label": "Level"},
            "name": {"type": "string", "label": "Name"},
        },
        "instances": {"count": 8},
    },
    "out": {
        "id_format": {"type": "integer", "min": 1, "max": 4},
        "state_variables": {
            "level": {"type": "number", "label": "Level"},
            "mute": {"type": "boolean", "label": "Mute"},
        },
        "instances": {"count": 4},
    },
    "relay": {"state_variables": {"closed": {"type": "boolean", "label": "Closed"}}},
    "bare": "not a mapping",
}

STATE_VARIABLES = {
    "power": {"type": "boolean", "label": "Power"},
    "input": {
        "type": "enum",
        "label": "Input",
        "values": ["hdmi1", {"value": "hdmi2"}, {"label": "no value"}],
    },
    "volume": {"type": "integer", "label": "Volume"},
    "temp": {"type": "number", "label": "Temp"},
    "ratio": {"type": "float", "label": "Ratio"},
    "mode": {"type": "string", "label": "Mode"},
    "model": {"label": "Model"},
}

# Two child_list rules: zone's names each item (a paged read can place its
# pages), out's counts from a fixed start (every page lands on the same
# children). A third fills nothing (its state is not a mapping).
LIST_RULES = [
    {
        "match": r"ZLIST (.+)",
        "child_list": {
            "type": "zone",
            "element": r"(\d+)=(\d+)",
            "separator": ",",
            "id": "$1",
            "state": {"level": "$2"},
        },
    },
    {
        "match": r"OLIST (.+)",
        "child_list": {
            "type": "out",
            "element": r"(\d+)",
            "separator": ",",
            "start": 1,
            "state": {"level": "$1"},
        },
    },
]

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {"noop": {"send": "NOOP\r"}},
    "state_variables": STATE_VARIABLES,
    "child_entity_types": CHILD_TYPES,
    "config_schema": {"host": {"type": "string"}, "zones_on": {"type": "boolean"}},
    "default_config": {"poll_interval": 5, "verbose": False},
    "config_derived": {"zone_total": {"from": "zones_on"}},
    "responses": LIST_RULES,
}


def poll(*entries: Any, **polling: Any) -> dict[str, Any]:
    return {"polling": {"queries": list(entries), **polling}}


def then(*rules: Any) -> dict[str, Any]:
    return {"responses": [*LIST_RULES, *rules]}


CASES: dict[str, dict[str, Any]] = {
    # --- the lists themselves ---------------------------------------------
    "polling_absent": {},
    "polling_not_a_mapping": {"polling": ["Q?"]},
    "polling_queries_not_a_list": {"polling": {"queries": "Q?"}},
    "polling_interval_is_read_nowhere": poll("Q?", interval=5),
    "on_connect_not_a_list": {"on_connect": {"send": "Q?"}},
    "entries_that_are_not_mappings": poll("Q?", 5, None, ["Q?"]),
    # --- an entry's shape -------------------------------------------------
    "plain_send_forms": poll(
        {"send": "Q1?"},
        {"send": ""},
        {"send": 5},
        {},
        {"query_for": "volume"},
    ),
    "osc_form_on_connect_only": {
        "polling": {"queries": [{"address": "/q", "args": [1]}]},
        "on_connect": [{"address": "/q", "args": [1]}, {"send": ""}, {"address": None}],
    },
    "each_child_forms": poll(
        {"each_child": "zone", "send": "ZL{child_id}?"},
        {"each_child": "zone", "send": "ZL{child_id:02d}?"},
        {"each_child": "zone", "send": "ZL?"},
        {"each_child": "zone"},
        {"each_child": "zone", "send": ""},
        {"each_child": "nope", "send": "X{child_id}"},
        {"each_child": 5, "send": "X{child_id}"},
        {"each_child": "relay", "send": "R{child_id}?"},
        {"each_child": "bare", "send": "B{child_id}?"},
        {"each_child": None},
    ),
    # --- when: ------------------------------------------------------------
    "when_gates": poll(
        {"send": "A?", "when": "zones_on"},
        {"send": "B?", "when": "verbose"},
        {"send": "C?", "when": "zone_total"},
        {"send": "D?", "when": "nope"},
        {"send": "E?", "when": ""},
        {"send": "F?", "when": 5},
        {"send": "G?", "when": None},
        {"each_child": "zone", "send": "Z{child_id}?", "when": "missing"},
    ),
    # --- when_state: ------------------------------------------------------
    "when_state_shapes": poll(
        {"send": "A?", "when_state": "power"},
        {"send": "B?", "when_state": {}},
        {"send": "C?", "when_state": []},
        {"send": "D?", "when_state": None},
        {"send": "E?", "when_state": {"power": True}},
    ),
    "when_state_names": poll(
        {"send": "A?", "when_state": {"nope": "on"}},
        {"send": "B?", "when_state": {"level": 1}},
        {"send": "C?", "when_state": {"zone.level": 1}},
        {"send": "D?", "when_state": {"closed": True}},
        {"send": "E?", "when_state": {5: "x"}},
        {"send": "F?", "when_state": {"nope": 1, "power": "on", "mode": "x"}},
    ),
    "when_state_values_by_type": poll(
        {"send": "A?", "when_state": {"power": "on"}},
        {"send": "B?", "when_state": {"power": [True, 1, "false"]}},
        {"send": "C?", "when_state": {"volume": [3, True, 2.5, "3"]}},
        {"send": "D?", "when_state": {"temp": [1, 2.5, True, "1"]}},
        {"send": "E?", "when_state": {"ratio": ["x", 0.5]}},
        {"send": "F?", "when_state": {"mode": [True, 5, "auto", ""]}},
        {"send": "G?", "when_state": {"model": [None, "x"]}},
        {"send": "H?", "when_state": {"input": ["hdmi1", "hdmi2", "hdmi3", "no value"]}},
        {"send": "I?", "when_state": {"input": []}},
        {"send": "J?", "when_state": {"power": "  "}},
        {"send": "K?", "when_state": {"volume": " "}},
    ),
    "when_state_on_the_poll": {
        "polling": {"queries": ["Q?"], "when_state": {"power": "yes", "nope": 1}},
    },
    "when_state_on_the_poll_not_a_mapping": {
        "polling": {"queries": ["Q?"], "when_state": "power"},
    },
    "when_state_with_state_variables_a_list": {
        "state_variables": ["power"],
        "polling": {"queries": [{"send": "A?", "when_state": {"power": True}}]},
    },
    "when_state_with_child_types_a_list": {
        "child_entity_types": ["zone"],
        "polling": {"queries": [{"send": "A?", "when_state": {"level": 1}}]},
    },
    # --- query_for: -------------------------------------------------------
    "query_for_plain": poll(
        {"send": "A?", "query_for": "volume"},
        {"send": "B?", "query_for": "nope"},
        {"send": "C?", "query_for": "mute"},
        {"send": "D?", "query_for": "level"},
        {"send": "E?", "query_for": ""},
        {"send": "F?", "query_for": 5},
        {"send": "G?", "query_for": None},
        {"address": "/x", "query_for": "volume"},
    ),
    "query_for_plain_with_state_variables_a_list": {
        "state_variables": ["volume"],
        "polling": {"queries": [{"send": "A?", "query_for": "volume"}]},
    },
    "query_for_a_list_read": poll(
        {"send": "ZL?", "query_for": "zone.level"},
        {"send": "ZN?", "query_for": "zone.name"},
        {"send": "OL?", "query_for": "out.level"},
        {"send": "X?", "query_for": "nope.level"},
        {"send": "Y?", "query_for": "zone.nope"},
        {"send": "R?", "query_for": "relay.closed"},
        {"send": "B?", "query_for": "bare.x"},
        {"send": "D?", "query_for": "zone."},
        {"send": "E?", "query_for": ".level"},
    ),
    "query_for_each_child": poll(
        {"each_child": "zone", "send": "Z{child_id}?", "query_for": "level"},
        {"each_child": "zone", "send": "Z{child_id}?", "query_for": "nope"},
        {"each_child": "zone", "send": "Z{child_id}?", "query_for": "volume"},
        {"each_child": "nope", "send": "N{child_id}?", "query_for": "level"},
        {"each_child": 5, "send": "N{child_id}?", "query_for": "level"},
        {"each_child": "bare", "send": "B{child_id}?", "query_for": "level"},
        {"each_child": "relay", "send": "R{child_id}?", "query_for": "closed"},
    ),
    # --- page_size: -------------------------------------------------------
    "page_size_values": poll(
        {"send": "A?", "page_size": 4},
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": 4},
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": 1},
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": True},
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": 2.0},
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": "4"},
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": None},
    ),
    "page_size_and_the_list_rules": poll(
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": 4, "query_for": "level"},
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": 4, "query_for": "name"},
        {"each_child": "out", "send": "O{child_id}?", "page_size": 2, "query_for": "level"},
        {"each_child": "out", "send": "O{child_id}?", "page_size": 2, "query_for": "mute"},
        {"each_child": "out", "send": "O{child_id}?", "page_size": 2},
        {"each_child": "relay", "send": "R{child_id}?", "page_size": 2},
        {"each_child": "out", "send": "O{child_id}?", "page_size": 1},
    ),
    "page_size_with_no_list_rules": {
        "responses": [],
        "polling": {"queries": [
            {"each_child": "zone", "send": "Z{child_id}?", "page_size": 4},
            {"each_child": "zone", "send": "Z{child_id}?", "page_size": 4, "query_for": "level"},
        ]},
    },
    "list_rules_of_every_shape": {
        "responses": [
            "not a rule",
            {"match": r"A (.+)", "child_list": "not a spec"},
            {"match": r"B (.+)", "child_list": {"type": "zone", "element": r"(\d+)",
                                                "separator": ",", "state": "x"}},
            {"match": r"C (\d) (.+)", "child_list": {"type": "out", "element": r"(\d+)",
                                                     "separator": ",", "start": "$1",
                                                     "state": {"mute": "$1"}}},
            {"match": r"D (.+)", "child_list": {"type": "out", "element": r"(\d+)",
                                                "separator": ",", "start": "2",
                                                "state": {"level": "$1"}}},
        ],
        "polling": {"queries": [
            {"each_child": "out", "send": "O{child_id}?", "page_size": 2, "query_for": "mute"},
            {"each_child": "out", "send": "O{child_id}?", "page_size": 2, "query_for": "level"},
            {"each_child": "out", "send": "O{child_id}?", "page_size": 2},
            {"each_child": "zone", "send": "Z{child_id}?", "page_size": 2},
            {"send": "Q?", "query_for": "out.mute"},
            {"send": "Q?", "query_for": "zone.level"},
        ]},
    },
    "every_check_on_one_entry": poll(
        {"each_child": "nope", "send": "X?", "when": "nope", "when_state": {"nope": 1},
         "query_for": "nope", "page_size": 1},
        {"send": "", "when": "", "when_state": {}, "query_for": "", "page_size": 2},
    ),
    "on_connect_runs_after_polling": {
        "polling": {"queries": [{"send": "A?", "when": "nope"}], "when_state": {"nope": 1}},
        "on_connect": [{"send": "B?", "when": "nope"}, {"each_child": "zone", "send": "Z?"}],
    },
    "when_state_on_an_enum_with_no_values": {
        "state_variables": {
            **STATE_VARIABLES,
            "source": {"type": "enum", "label": "Source", "values": [{"label": "unnamed"}]},
            "scene": {"type": "enum", "label": "Scene", "values": []},
            "layout": {"type": "enum", "label": "Layout", "values": "a,b"},
        },
        "polling": {"queries": [
            {"send": "A?", "when_state": {"source": "x", "scene": "y", "layout": "z"}},
        ]},
    },
    "when_names_a_field_no_source_declares": {
        "default_config": {},
        "polling": {"queries": [{"send": "A?", "when": "verbose"}]},
    },
    "query_for_with_two_dots": poll(
        {"send": "Q?", "query_for": "zone.level.x"},
        {"send": "R?", "query_for": "a.b.c"},
    ),
    "polling_interval_falsy": {"polling": {"queries": ["Q?"], "interval": 0}},
    "polling_interval_null": {"polling": {"queries": ["Q?"], "interval": None}},
    "when_with_no_config_declared": {
        "config_schema": {}, "default_config": {}, "config_derived": {},
        "polling": {"queries": [{"send": "A?", "when": "zones_on"}, {"send": "B?", "when": "verbose"}]},
    },
    "each_child_roster_and_id_format_apart": {
        "child_entity_types": {
            **CHILD_TYPES,
            "deck": {"state_variables": {"tc": {"type": "string", "label": "TC"}},
                     "instances": {"count": 2}},
            "panel": {"id_format": {"type": "integer"},
                      "state_variables": {"lit": {"type": "boolean", "label": "Lit"}}},
        },
        "polling": {"queries": [
            {"each_child": "deck", "send": "D{child_id}?"},
            {"each_child": "panel", "send": "P{child_id}?"},
        ]},
    },
    "each_child_tokens_that_are_not_child_id": poll(
        {"each_child": "zone", "send": "Z{child_idx}?"},
        {"each_child": "zone", "send": "Z{child_id_2}?"},
        {"each_child": "zone", "send": "Z{child_id:{w}}?"},
        {"each_child": "zone", "send": "Z{ child_id }?"},
    ),
    "when_state_on_a_string_that_lists_values": {
        "state_variables": {**STATE_VARIABLES,
                            "preset": {"type": "string", "label": "Preset", "values": ["a", "b"]}},
        "polling": {"queries": [{"send": "A?", "when_state": {"preset": "c"}}]},
    },
    # --- then_query: ------------------------------------------------------
    "then_query_on_other_forms": then(
        {"json": True, "set": {"mode": "mode"}, "then_query": ["Q?"]},
        {"address": "/mode", "set": {"mode": {"arg": 0}}, "then_query": ["Q?"]},
        {"json": True, "set": {"mode": "mode"}, "then_query": "Q?"},
    ),
    "then_query_shapes": then(
        {"match": "A", "then_query": []},
        {"match": "B", "then_query": "Q?"},
        {"match": "C", "then_query": None},
        {"match": "D", "then_query": {"send": "Q?"}},
        {"match": "E", "then_query": ["Q?", {"send": "R?"}]},
        {"match": "F"},
    ),
    "then_query_child_id_without_a_route": then(
        {"match": r"G(\d)", "then_query": ["Z{child_id}?", {"send": "Z{child_id:02d}?"}]},
        {"match": r"H(\d)", "child_set": "x", "then_query": ["Z{child_id}?"]},
        {"match": r"I(\d)", "child_set": [], "then_query": ["Z{child_id}?"]},
        {"match": r"J(\d)", "child_set": ["x", 5], "then_query": ["Z{child_id}?"]},
    ),
    "then_query_child_id_more_than_one_type": then(
        {"match": r"ZONE(\d) LVL (\d+)", "child_set": [
            {"type": "zone", "id": "$1", "state": {"level": "$2"}},
            {"type": "out", "id": "$1", "state": {"level": "$2"}},
        ], "then_query": ["Z{child_id}?", {"send": "Z{child_id}?", "query_for": "level"}]},
        {"match": r"Q(\d) (\d+)", "child_set": [
            {"type": "zone", "id": "$1", "state": {"level": "$2"}},
            {"id": "$1", "state": {"level": "$2"}},
        ], "then_query": ["Z{child_id}?"]},
    ),
    "then_query_child_id_one_type": then(
        {"match": r"ZONE(\d) LVL (\d+)", "child_set": [
            {"type": "zone", "id": "$1", "state": {"level": "$2"}},
            {"type": "zone", "id": "$1", "state": "x"},
        ], "then_query": [
            "Z{child_id}?",
            {"send": "ZL{child_id}?", "query_for": "level"},
            {"send": "ZN{child_id}?", "query_for": "name"},
            {"send": "ZX{child_id}?", "query_for": "nope"},
            {"send": "ZV{child_id}?", "query_for": "volume"},
            {"send": "ZE{child_id}?", "query_for": ""},
            {"send": "ZE{child_id}?", "query_for": 5},
            {"each_child": "zone", "send": "ZA{child_id}?", "query_for": "level"},
            {"each_child": "out", "send": "OA{child_id}?", "query_for": "nope"},
        ]},
    ),
    "then_query_child_type_without_variables": then(
        {"match": r"RLY(\d) (\d)", "child_set": [
            {"type": "relay", "id": "$1", "state": {"closed": "$2"}},
        ], "then_query": [{"send": "R{child_id}?", "query_for": "closed"}]},
        {"match": r"BARE(\d) (\d)", "child_set": [
            {"type": "bare", "id": "$1", "state": {"x": "$2"}},
        ], "then_query": [{"send": "B{child_id}?", "query_for": "x"}]},
    ),
    "then_query_reads_back_what_it_wrote": then(
        {"match": r"VOL (\d+)", "set": {"volume": "$1"}, "then_query": [
            {"send": "VOL?", "query_for": "volume"},
            {"send": "PWR?", "query_for": "power"},
            "VOL?",
        ]},
        {"match": r"MODE (\w+)", "mappings": [{"group": 1, "state": "mode"}, "x"],
         "then_query": [{"send": "MODE?", "query_for": "mode"}]},
        {"match": r"T (\d)", "set": "x", "mappings": "y",
         "then_query": [{"send": "T?", "query_for": "temp"}]},
    ),
    "then_query_entries_get_the_entry_checks": then(
        {"match": r"P (\w+)", "set": {"power": "$1"}, "then_query": [
            {"send": "A?", "when": "nope"},
            {"send": "B?", "when_state": {"nope": 1}},
            {"send": "C?", "query_for": "nope"},
            {"address": "/d"},
            {"send": "E?", "page_size": 2},
            {"each_child": "zone", "send": "Z?"},
            5,
            "",
        ]},
    ),
    "then_query_with_responses_not_a_list": {
        "responses": {"r": {"match": "A", "then_query": ["Q?"]}},
    },
    "every_list_together": {
        "responses": [
            *LIST_RULES,
            {"match": r"P (\w+)", "set": {"power": "$1"},
             "then_query": [{"send": "A?", "when": "nope"}]},
            {"match": r"Q (\w+)", "then_query": "x"},
            {"match": r"R (\w+)", "then_query": [{"send": "B?", "when": "nope"}]},
        ],
        "polling": {"queries": [{"send": "C?", "when": "nope"}], "when_state": {"nope": 1},
                    "interval": 3},
        "on_connect": [{"send": "D?", "when": "nope"}],
    },
}


def _definition(case: str) -> dict[str, Any]:
    return {**BASE, **CASES[case]}


EXPECTED: dict[str, list[tuple[str, str]]] = {'polling_absent': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'polling_not_a_mapping': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                           ('polling', 'polling: must be a mapping')],
 'polling_queries_not_a_list': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'polling_interval_is_read_nowhere': [('child_entity_types.bare',
                                       'child_entity_types.bare: must be a mapping'),
                                      ('polling',
                                       'polling.interval is not read by the runtime. Remove the interval: '
                                       'key and set default_config.poll_interval instead')],
 'on_connect_not_a_list': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'entries_that_are_not_mappings': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping')],
 'plain_send_forms': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                      ('polling.queries[1]',
                       'polling.queries[1]: mapping entries must be {each_child, send} or {send, when} or '
                       '{send, when_state} or {send, query_for}'),
                      ('polling.queries[2]',
                       'polling.queries[2]: mapping entries must be {each_child, send} or {send, when} or '
                       '{send, when_state} or {send, query_for}'),
                      ('polling.queries[3]',
                       'polling.queries[3]: mapping entries must be {each_child, send} or {send, when} or '
                       '{send, when_state} or {send, query_for}'),
                      ('polling.queries[4]',
                       'polling.queries[4]: mapping entries must be {each_child, send} or {send, when} or '
                       '{send, when_state} or {send, query_for}')],
 'osc_form_on_connect_only': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                              ('polling.queries[0]',
                               'polling.queries[0]: mapping entries must be {each_child, send} or {send, '
                               'when} or {send, when_state} or {send, query_for}'),
                              ('on_connect[1]',
                               'on_connect[1]: mapping entries must be {each_child, send} or {send, when} or '
                               '{send, when_state} or {send, query_for} or {address, args}')],
 'each_child_forms': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                      ('polling.queries[2]',
                       "polling.queries[2]: 'send' must contain {child_id} (a format spec like "
                       '{child_id:02d} works too)'),
                      ('polling.queries[3]', "polling.queries[3]: missing 'send' template"),
                      ('polling.queries[4]', "polling.queries[4]: missing 'send' template"),
                      ('polling.queries[5]',
                       "polling.queries[5]: each_child type 'nope' is not a declared child_entity_type"),
                      ('polling.queries[6]',
                       'polling.queries[6]: each_child type 5 is not a declared child_entity_type'),
                      ('polling.queries[7]',
                       "polling.queries[7]: each_child type 'relay' declares no instances: block, so nothing "
                       'would ever be polled'),
                      ('polling.queries[8]',
                       "polling.queries[8]: each_child type 'bare' declares no instances: block, so nothing "
                       'would ever be polled'),
                      ('polling.queries[9]',
                       'polling.queries[9]: each_child type None is not a declared child_entity_type'),
                      ('polling.queries[9]', "polling.queries[9]: missing 'send' template")],
 'when_gates': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                ('polling.queries[3]',
                 "polling.queries[3]: 'when' field 'nope' is not a declared config field (config_schema / "
                 'default_config / config_derived)'),
                ('polling.queries[4]', "polling.queries[4]: 'when' must name a config field"),
                ('polling.queries[5]', "polling.queries[5]: 'when' must name a config field"),
                ('polling.queries[6]', "polling.queries[6]: 'when' must name a config field"),
                ('polling.queries[7]',
                 "polling.queries[7]: 'when' field 'missing' is not a declared config field (config_schema / "
                 'default_config / config_derived)')],
 'when_state_shapes': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                       ('polling.queries[0]',
                        'polling.queries[0]: when_state must map a state variable to the value it must hold, '
                        'or to a list of values'),
                       ('polling.queries[1]',
                        'polling.queries[1]: when_state must map a state variable to the value it must hold, '
                        'or to a list of values'),
                       ('polling.queries[2]',
                        'polling.queries[2]: when_state must map a state variable to the value it must hold, '
                        'or to a list of values'),
                       ('polling.queries[3]',
                        'polling.queries[3]: when_state must map a state variable to the value it must hold, '
                        'or to a list of values')],
 'when_state_names': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                      ('polling.queries[0]',
                       "polling.queries[0]: when_state 'nope' is not a declared state variable"),
                      ('polling.queries[1]',
                       "polling.queries[1]: when_state 'level' is a child variable; the gate reads the "
                       "device's own state variables only"),
                      ('polling.queries[2]',
                       "polling.queries[2]: when_state 'zone.level' is a child variable; the gate reads the "
                       "device's own state variables only"),
                      ('polling.queries[3]',
                       "polling.queries[3]: when_state 'closed' is a child variable; the gate reads the "
                       "device's own state variables only"),
                      ('polling.queries[4]',
                       "polling.queries[4]: when_state '5' is not a declared state variable"),
                      ('polling.queries[5]',
                       "polling.queries[5]: when_state 'nope' is not a declared state variable"),
                      ('polling.queries[5]',
                       'polling.queries[5]: when_state.power is a boolean; write true or false without '
                       "quotes, not 'on'")],
 'when_state_values_by_type': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                               ('polling.queries[0]',
                                'polling.queries[0]: when_state.power is a boolean; write true or false '
                                "without quotes, not 'on'"),
                               ('polling.queries[1]',
                                'polling.queries[1]: when_state.power is a boolean; write true or false '
                                'without quotes, not 1'),
                               ('polling.queries[1]',
                                'polling.queries[1]: when_state.power is a boolean; write true or false '
                                "without quotes, not 'false'"),
                               ('polling.queries[2]',
                                'polling.queries[2]: when_state.volume is a whole number; True is not one'),
                               ('polling.queries[2]',
                                'polling.queries[2]: when_state.volume is a whole number; 2.5 is not one'),
                               ('polling.queries[2]',
                                "polling.queries[2]: when_state.volume is a whole number; '3' is not one"),
                               ('polling.queries[3]',
                                'polling.queries[3]: when_state.temp is a number; True is not one'),
                               ('polling.queries[3]',
                                "polling.queries[3]: when_state.temp is a number; '1' is not one"),
                               ('polling.queries[4]',
                                "polling.queries[4]: when_state.ratio is a number; 'x' is not one"),
                               ('polling.queries[5]',
                                'polling.queries[5]: when_state.mode value True is not text; YAML reads a '
                                'bare true, false, on, off, yes or no as a boolean. Put it in quotes.'),
                               ('polling.queries[5]',
                                'polling.queries[5]: when_state.mode holds text; put 5 in quotes'),
                               ('polling.queries[5]',
                                'polling.queries[5]: when_state.mode has no value. Type the value the device '
                                'reports, or remove the condition'),
                               ('polling.queries[6]',
                                'polling.queries[6]: when_state.model holds text; put None in quotes'),
                               ('polling.queries[7]',
                                "polling.queries[7]: when_state.input value 'hdmi3' is not one of its values "
                                '(hdmi1, hdmi2)'),
                               ('polling.queries[7]',
                                "polling.queries[7]: when_state.input value 'no value' is not one of its "
                                'values (hdmi1, hdmi2)'),
                               ('polling.queries[8]', 'polling.queries[8]: when_state.input lists no values'),
                               ('polling.queries[9]',
                                'polling.queries[9]: when_state.power has no value. Type the value the '
                                'device reports, or remove the condition'),
                               ('polling.queries[10]',
                                'polling.queries[10]: when_state.volume has no value. Type the value the '
                                'device reports, or remove the condition')],
 'when_state_on_the_poll': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                            ('polling',
                             'polling: when_state.power is a boolean; write true or false without quotes, '
                             "not 'yes'"),
                            ('polling', "polling: when_state 'nope' is not a declared state variable")],
 'when_state_on_the_poll_not_a_mapping': [('child_entity_types.bare',
                                           'child_entity_types.bare: must be a mapping'),
                                          ('polling',
                                           'polling: when_state must map a state variable to the value it '
                                           'must hold, or to a list of values')],
 'when_state_with_state_variables_a_list': [('state_variables', 'state_variables: must be a mapping'),
                                            ('child_entity_types.bare',
                                             'child_entity_types.bare: must be a mapping'),
                                            ('polling.queries[0]',
                                             "polling.queries[0]: when_state 'power' is not a declared state "
                                             'variable')],
 'when_state_with_child_types_a_list': [('responses[0]',
                                         "Response 0: child_list: type 'zone' is not a declared "
                                         'child_entity_type'),
                                        ('responses[1]',
                                         "Response 1: child_list: type 'out' is not a declared "
                                         'child_entity_type'),
                                        ('child_entity_types', 'child_entity_types: must be a mapping'),
                                        ('polling.queries[0]',
                                         "polling.queries[0]: when_state 'level' is not a declared state "
                                         'variable')],
 'query_for_plain': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                     ('polling.queries[1]',
                      "polling.queries[1]: query_for 'nope' is not a declared state variable"),
                     ('polling.queries[2]',
                      "polling.queries[2]: query_for 'mute' is a child variable; on a query sent once, name "
                      "its child type too: 'out.mute'"),
                     ('polling.queries[3]',
                      "polling.queries[3]: query_for 'level' is a child variable; on a query sent once, name "
                      "its child type too: 'out.level'"),
                     ('polling.queries[4]', "polling.queries[4]: 'query_for' must name a state variable"),
                     ('polling.queries[5]', "polling.queries[5]: 'query_for' must name a state variable"),
                     ('polling.queries[6]', "polling.queries[6]: 'query_for' must name a state variable"),
                     ('polling.queries[7]',
                      "polling.queries[7]: 'query_for' is only valid on a plain {send} or each_child query "
                      'entry'),
                     ('polling.queries[7]',
                      'polling.queries[7]: mapping entries must be {each_child, send} or {send, when} or '
                      '{send, when_state} or {send, query_for}')],
 'query_for_plain_with_state_variables_a_list': [('state_variables', 'state_variables: must be a mapping'),
                                                 ('child_entity_types.bare',
                                                  'child_entity_types.bare: must be a mapping'),
                                                 ('polling.queries[0]',
                                                  "polling.queries[0]: query_for 'volume' is not a declared "
                                                  'state variable')],
 'query_for_a_list_read': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                           ('polling.queries[1]',
                            "polling.queries[1]: query_for 'zone.name' names a child variable, and no "
                            'child_list rule fills it; a query sent once reports a child only through a '
                            'list'),
                           ('polling.queries[3]',
                            "polling.queries[3]: query_for 'nope.level': 'nope' is not a declared "
                            'child_entity_type'),
                           ('polling.queries[4]',
                            "polling.queries[4]: query_for 'zone.nope': 'nope' is not a state variable of "
                            "child type 'zone'"),
                           ('polling.queries[5]',
                            "polling.queries[5]: query_for 'relay.closed' names a child variable, and no "
                            'child_list rule fills it; a query sent once reports a child only through a '
                            'list'),
                           ('polling.queries[6]',
                            "polling.queries[6]: query_for 'bare.x': 'bare' is not a declared "
                            'child_entity_type'),
                           ('polling.queries[7]',
                            "polling.queries[7]: query_for 'zone.': '' is not a state variable of child type "
                            "'zone'"),
                           ('polling.queries[8]',
                            "polling.queries[8]: query_for '.level': '' is not a declared "
                            'child_entity_type')],
 'query_for_each_child': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                          ('polling.queries[1]',
                           "polling.queries[1]: query_for 'nope' is not a state variable of child type "
                           "'zone'"),
                          ('polling.queries[2]',
                           "polling.queries[2]: query_for 'volume' is not a state variable of child type "
                           "'zone'"),
                          ('polling.queries[3]',
                           "polling.queries[3]: query_for 'level' is not a state variable of child type "
                           "'nope'"),
                          ('polling.queries[3]',
                           "polling.queries[3]: each_child type 'nope' is not a declared child_entity_type"),
                          ('polling.queries[4]',
                           "polling.queries[4]: query_for 'level' is not a state variable of child type 5"),
                          ('polling.queries[4]',
                           'polling.queries[4]: each_child type 5 is not a declared child_entity_type'),
                          ('polling.queries[5]',
                           "polling.queries[5]: query_for 'level' is not a state variable of child type "
                           "'bare'"),
                          ('polling.queries[5]',
                           "polling.queries[5]: each_child type 'bare' declares no instances: block, so "
                           'nothing would ever be polled'),
                          ('polling.queries[6]',
                           "polling.queries[6]: each_child type 'relay' declares no instances: block, so "
                           'nothing would ever be polled')],
 'page_size_values': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                      ('polling.queries[0]',
                       'polling.queries[0]: page_size applies to a per-child query (each_child)'),
                      ('polling.queries[2]',
                       'polling.queries[2]: page_size must be a whole number, 2 or more (how many children '
                       'one reply covers)'),
                      ('polling.queries[3]',
                       'polling.queries[3]: page_size must be a whole number, 2 or more (how many children '
                       'one reply covers)'),
                      ('polling.queries[4]',
                       'polling.queries[4]: page_size must be a whole number, 2 or more (how many children '
                       'one reply covers)'),
                      ('polling.queries[5]',
                       'polling.queries[5]: page_size must be a whole number, 2 or more (how many children '
                       'one reply covers)'),
                      ('polling.queries[6]',
                       'polling.queries[6]: page_size must be a whole number, 2 or more (how many children '
                       'one reply covers)')],
 'page_size_and_the_list_rules': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                                  ('polling.queries[1]',
                                   'polling.queries[1]: a paged query reads a list, and no child_list rule '
                                   "fills 'name' on 'zone'"),
                                  ('polling.queries[2]',
                                   'polling.queries[2]: every page of this paged query would land on the '
                                   "same children of 'out': the child_list rule that fills 'level' counts "
                                   'from a fixed start. Take the start from the reply (start: $2) or name '
                                   'each item (id: $1)'),
                                  ('polling.queries[3]',
                                   'polling.queries[3]: a paged query reads a list, and no child_list rule '
                                   "fills 'mute' on 'out'"),
                                  ('polling.queries[4]',
                                   'polling.queries[4]: every page of this paged query would land on the '
                                   "same children of 'out': its child_list rules count from a fixed start. "
                                   'Take the start from the reply (start: $2) or name each item (id: $1)'),
                                  ('polling.queries[5]',
                                   "polling.queries[5]: each_child type 'relay' declares no instances: "
                                   'block, so nothing would ever be polled'),
                                  ('polling.queries[6]',
                                   'polling.queries[6]: page_size must be a whole number, 2 or more (how '
                                   'many children one reply covers)')],
 'page_size_with_no_list_rules': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                                  ('polling.queries[1]',
                                   'polling.queries[1]: a paged query reads a list, and no child_list rule '
                                   "fills 'level' on 'zone'")],
 'list_rules_of_every_shape': [('responses[0]', 'Response 0: must be a mapping'),
                               ('responses[1]', 'Response 1: child_list: must be a mapping'),
                               ('responses[2]',
                                "Response 2: child_list: missing 'state' mapping (prop -> $N or literal)"),
                               ('responses[4]',
                                'Response 4: child_list: start must be a capture ref ($1, $2, ...)'),
                               ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                               ('polling.queries[1]',
                                'polling.queries[1]: every page of this paged query would land on the same '
                                "children of 'out': the child_list rule that fills 'level' counts from a "
                                'fixed start. Take the start from the reply (start: $2) or name each item '
                                '(id: $1)'),
                               ('polling.queries[5]',
                                "polling.queries[5]: query_for 'zone.level' names a child variable, and no "
                                'child_list rule fills it; a query sent once reports a child only through a '
                                'list')],
 'every_check_on_one_entry': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                              ('polling.queries[0]',
                               "polling.queries[0]: 'when' field 'nope' is not a declared config field "
                               '(config_schema / default_config / config_derived)'),
                              ('polling.queries[0]',
                               "polling.queries[0]: when_state 'nope' is not a declared state variable"),
                              ('polling.queries[0]',
                               "polling.queries[0]: query_for 'nope' is not a state variable of child type "
                               "'nope'"),
                              ('polling.queries[0]',
                               'polling.queries[0]: page_size must be a whole number, 2 or more (how many '
                               'children one reply covers)'),
                              ('polling.queries[0]',
                               "polling.queries[0]: each_child type 'nope' is not a declared "
                               'child_entity_type'),
                              ('polling.queries[0]',
                               "polling.queries[0]: 'send' must contain {child_id} (a format spec like "
                               '{child_id:02d} works too)'),
                              ('polling.queries[1]', "polling.queries[1]: 'when' must name a config field"),
                              ('polling.queries[1]',
                               'polling.queries[1]: when_state must map a state variable to the value it '
                               'must hold, or to a list of values'),
                              ('polling.queries[1]',
                               "polling.queries[1]: 'query_for' must name a state variable"),
                              ('polling.queries[1]',
                               'polling.queries[1]: page_size applies to a per-child query (each_child)'),
                              ('polling.queries[1]',
                               'polling.queries[1]: mapping entries must be {each_child, send} or {send, '
                               'when} or {send, when_state} or {send, query_for}')],
 'on_connect_runs_after_polling': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                                   ('polling.queries[0]',
                                    "polling.queries[0]: 'when' field 'nope' is not a declared config field "
                                    '(config_schema / default_config / config_derived)'),
                                   ('polling', "polling: when_state 'nope' is not a declared state variable"),
                                   ('on_connect[0]',
                                    "on_connect[0]: 'when' field 'nope' is not a declared config field "
                                    '(config_schema / default_config / config_derived)'),
                                   ('on_connect[1]',
                                    "on_connect[1]: 'send' must contain {child_id} (a format spec like "
                                    '{child_id:02d} works too)')],
 'when_state_on_an_enum_with_no_values': [('child_entity_types.bare',
                                           'child_entity_types.bare: must be a mapping'),
                                          ('polling.queries[0]',
                                           "polling.queries[0]: when_state.source value 'x' is not one of "
                                           'its values ()'),
                                          ('polling.queries[0]',
                                           "polling.queries[0]: when_state.scene value 'y' is not one of its "
                                           'values ()')],
 'when_names_a_field_no_source_declares': [('child_entity_types.bare',
                                            'child_entity_types.bare: must be a mapping'),
                                           ('polling.queries[0]',
                                            "polling.queries[0]: 'when' field 'verbose' is not a declared "
                                            'config field (config_schema / default_config / '
                                            'config_derived)')],
 'query_for_with_two_dots': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                             ('polling.queries[0]',
                              "polling.queries[0]: query_for 'zone.level.x': 'level.x' is not a state "
                              "variable of child type 'zone'"),
                             ('polling.queries[1]',
                              "polling.queries[1]: query_for 'a.b.c': 'a' is not a declared "
                              'child_entity_type')],
 'polling_interval_falsy': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                            ('polling',
                             'polling.interval is not read by the runtime. Remove the interval: key and set '
                             'default_config.poll_interval instead')],
 'polling_interval_null': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                           ('polling',
                            'polling.interval is not read by the runtime. Remove the interval: key and set '
                            'default_config.poll_interval instead')],
 'when_with_no_config_declared': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                                  ('polling.queries[0]',
                                   "polling.queries[0]: 'when' field 'zones_on' is not a declared config "
                                   'field (config_schema / default_config / config_derived)'),
                                  ('polling.queries[1]',
                                   "polling.queries[1]: 'when' field 'verbose' is not a declared config "
                                   'field (config_schema / default_config / config_derived)')],
 'each_child_roster_and_id_format_apart': [('child_entity_types.bare',
                                            'child_entity_types.bare: must be a mapping'),
                                           ('polling.queries[1]',
                                            "polling.queries[1]: each_child type 'panel' declares no "
                                            'instances: block, so nothing would ever be polled')],
 'each_child_tokens_that_are_not_child_id': [('child_entity_types.bare',
                                              'child_entity_types.bare: must be a mapping'),
                                             ('polling.queries[0]',
                                              "polling.queries[0]: 'send' must contain {child_id} (a format "
                                              'spec like {child_id:02d} works too)'),
                                             ('polling.queries[1]',
                                              "polling.queries[1]: 'send' must contain {child_id} (a format "
                                              'spec like {child_id:02d} works too)'),
                                             ('polling.queries[2]',
                                              "polling.queries[2]: 'send' must contain {child_id} (a format "
                                              'spec like {child_id:02d} works too)'),
                                             ('polling.queries[3]',
                                              "polling.queries[3]: 'send' must contain {child_id} (a format "
                                              'spec like {child_id:02d} works too)'),
                                             ('polling.queries[0]',
                                              "polling.queries[0]: '{child_idx}' resolves to nothing (did "
                                              "you mean 'child_id'?). It names no config field (only config "
                                              'values substitute here), so it would be sent to the device as '
                                              'written'),
                                             ('polling.queries[1]',
                                              "polling.queries[1]: '{child_id_2}' resolves to nothing (did "
                                              "you mean 'child_id'?). It names no config field (only config "
                                              'values substitute here), so it would be sent to the device as '
                                              'written'),
                                             ('polling.queries[2]',
                                              "polling.queries[2]: '{w}' resolves to nothing. It names no "
                                              'config field (only config values substitute here), so it '
                                              'would be sent to the device as written')],
 'when_state_on_a_string_that_lists_values': [('child_entity_types.bare',
                                               'child_entity_types.bare: must be a mapping')],
 'then_query_on_other_forms': [('responses[3]',
                                'Response 3: set.mode must be a capture reference like "$1" or a static '
                                'value; move a value map into mappings:'),
                               ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                               ('responses[2]', 'Response 2: then_query works on text rules (match:) only'),
                               ('responses[3]', 'Response 3: then_query works on text rules (match:) only'),
                               ('responses[4]', 'Response 4: then_query works on text rules (match:) only')],
 'then_query_shapes': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                       ('responses[2]', 'Response 2: then_query must list at least one query'),
                       ('responses[3]', 'Response 3: then_query must list at least one query'),
                       ('responses[4]', 'Response 4: then_query must list at least one query'),
                       ('responses[5]', 'Response 5: then_query must list at least one query')],
 'then_query_child_id_without_a_route': [('responses[3]', 'Response 3: child_set must be a non-empty list'),
                                         ('responses[4]', 'Response 4: child_set must be a non-empty list'),
                                         ('responses[5]', 'Response 5: child_set[0]: must be a mapping'),
                                         ('responses[5]', 'Response 5: child_set[1]: must be a mapping'),
                                         ('child_entity_types.bare',
                                          'child_entity_types.bare: must be a mapping'),
                                         ('responses[2].then_query[0]',
                                          'responses[2].then_query[0]: {child_id} names the child this rule '
                                          'writes, and the rule has no child_set. Route the report to its '
                                          'child with child_set, or send the query to every child with '
                                          'each_child'),
                                         ('responses[2].then_query[1]',
                                          'responses[2].then_query[1]: {child_id} names the child this rule '
                                          'writes, and the rule has no child_set. Route the report to its '
                                          'child with child_set, or send the query to every child with '
                                          'each_child'),
                                         ('responses[3].then_query[0]',
                                          'responses[3].then_query[0]: {child_id} names the child this rule '
                                          'writes, and the rule has no child_set. Route the report to its '
                                          'child with child_set, or send the query to every child with '
                                          'each_child'),
                                         ('responses[4].then_query[0]',
                                          'responses[4].then_query[0]: {child_id} names the child this rule '
                                          'writes, and the rule has no child_set. Route the report to its '
                                          'child with child_set, or send the query to every child with '
                                          'each_child'),
                                         ('responses[5].then_query[0]',
                                          'responses[5].then_query[0]: {child_id} names the child this rule '
                                          'writes, and the rule has no child_set. Route the report to its '
                                          'child with child_set, or send the query to every child with '
                                          'each_child')],
 'then_query_child_id_more_than_one_type': [('responses[3]',
                                             'Response 3: child_set[1]: type None is not a declared '
                                             'child_entity_type'),
                                            ('child_entity_types.bare',
                                             'child_entity_types.bare: must be a mapping'),
                                            ('responses[2].then_query[0]',
                                             'responses[2].then_query[0]: {child_id} names the child this '
                                             'rule writes, and the rule writes children of more than one '
                                             'type (out, zone). Send the query to every child of one type '
                                             'with each_child'),
                                            ('responses[2].then_query[1]',
                                             'responses[2].then_query[1]: {child_id} names the child this '
                                             'rule writes, and the rule writes children of more than one '
                                             'type (out, zone). Send the query to every child of one type '
                                             'with each_child'),
                                            ('responses[3].then_query[0]',
                                             'responses[3].then_query[0]: {child_id} names the child this '
                                             'rule writes, and the rule writes children of more than one '
                                             'type (None, zone). Send the query to every child of one type '
                                             'with each_child')],
 'then_query_child_id_one_type': [('responses[2]',
                                   "Response 2: child_set[1]: missing 'state' mapping (prop -> $N or "
                                   'literal)'),
                                  ('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                                  ('responses[2].then_query[1]',
                                   "responses[2].then_query[1]: reads 'level', which this rule has just "
                                   'written for that child. then_query reads what the report did not say'),
                                  ('responses[2].then_query[3]',
                                   "responses[2].then_query[3]: query_for 'nope' is not a state variable of "
                                   "child type 'zone', the child {child_id} names here"),
                                  ('responses[2].then_query[4]',
                                   "responses[2].then_query[4]: query_for 'volume' is not a state variable "
                                   "of child type 'zone', the child {child_id} names here"),
                                  ('responses[2].then_query[5]',
                                   "responses[2].then_query[5]: 'query_for' must name a state variable"),
                                  ('responses[2].then_query[6]',
                                   "responses[2].then_query[6]: 'query_for' must name a state variable"),
                                  ('responses[2].then_query[8]',
                                   "responses[2].then_query[8]: query_for 'nope' is not a state variable of "
                                   "child type 'out'")],
 'then_query_child_type_without_variables': [('responses[3]',
                                              "Response 3: child_set[0]: state prop 'x' is not declared in "
                                              'child_entity_types.bare.state_variables'),
                                             ('child_entity_types.bare',
                                              'child_entity_types.bare: must be a mapping'),
                                             ('responses[2].then_query[0]',
                                              "responses[2].then_query[0]: reads 'closed', which this rule "
                                              'has just written for that child. then_query reads what the '
                                              'report did not say'),
                                             ('responses[3].then_query[0]',
                                              "responses[3].then_query[0]: reads 'x', which this rule has "
                                              'just written for that child. then_query reads what the report '
                                              'did not say'),
                                             ('responses[3].then_query[0]',
                                              "responses[3].then_query[0]: query_for 'x' is not a state "
                                              "variable of child type 'bare', the child {child_id} names "
                                              'here')],
 'then_query_reads_back_what_it_wrote': [('child_entity_types.bare',
                                          'child_entity_types.bare: must be a mapping'),
                                         ('responses[2].then_query[0]',
                                          "responses[2].then_query[0]: reads 'volume', which this rule has "
                                          'just written. then_query reads what the report did not say'),
                                         ('responses[3].then_query[0]',
                                          "responses[3].then_query[0]: reads 'mode', which this rule has "
                                          'just written. then_query reads what the report did not say')],
 'then_query_entries_get_the_entry_checks': [('child_entity_types.bare',
                                              'child_entity_types.bare: must be a mapping'),
                                             ('responses[2].then_query[0]',
                                              "responses[2].then_query[0]: 'when' field 'nope' is not a "
                                              'declared config field (config_schema / default_config / '
                                              'config_derived)'),
                                             ('responses[2].then_query[1]',
                                              "responses[2].then_query[1]: when_state 'nope' is not a "
                                              'declared state variable'),
                                             ('responses[2].then_query[2]',
                                              "responses[2].then_query[2]: query_for 'nope' is not a "
                                              'declared state variable'),
                                             ('responses[2].then_query[3]',
                                              'responses[2].then_query[3]: mapping entries must be '
                                              '{each_child, send} or {send, when} or {send, when_state} or '
                                              '{send, query_for}'),
                                             ('responses[2].then_query[4]',
                                              'responses[2].then_query[4]: page_size applies to a per-child '
                                              'query (each_child)'),
                                             ('responses[2].then_query[5]',
                                              "responses[2].then_query[5]: 'send' must contain {child_id} (a "
                                              'format spec like {child_id:02d} works too)')],
 'then_query_with_responses_not_a_list': [('responses', 'responses: must be a list'),
                                          ('child_entity_types.bare',
                                           'child_entity_types.bare: must be a mapping')],
 'every_list_together': [('child_entity_types.bare', 'child_entity_types.bare: must be a mapping'),
                         ('polling',
                          'polling.interval is not read by the runtime. Remove the interval: key and set '
                          'default_config.poll_interval instead'),
                         ('polling.queries[0]',
                          "polling.queries[0]: 'when' field 'nope' is not a declared config field "
                          '(config_schema / default_config / config_derived)'),
                         ('polling', "polling: when_state 'nope' is not a declared state variable"),
                         ('on_connect[0]',
                          "on_connect[0]: 'when' field 'nope' is not a declared config field (config_schema "
                          '/ default_config / config_derived)'),
                         ('responses[2].then_query[0]',
                          "responses[2].then_query[0]: 'when' field 'nope' is not a declared config field "
                          '(config_schema / default_config / config_derived)'),
                         ('responses[3]', 'Response 3: then_query must list at least one query'),
                         ('responses[4].then_query[0]',
                          "responses[4].then_query[0]: 'when' field 'nope' is not a declared config field "
                          '(config_schema / default_config / config_derived)')]}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    issues = validate_driver_issues(definition)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [m for _, m in got]


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED)


def test_an_each_child_that_is_a_list_is_named_as_written() -> None:
    # Errors only: the poll-length warning raises on this entry before any
    # message comes back, so validate_driver_issues cannot be asked.
    definition = {**BASE, **poll({"each_child": ["zone"], "send": "X{child_id}"})}
    assert list(validate_driver_definition(definition)) == [
        "child_entity_types.bare: must be a mapping",
        "polling.queries[0]: each_child type ['zone'] is not a declared child_entity_type",
    ]
