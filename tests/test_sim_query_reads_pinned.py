"""Pins for which query handlers the auto-simulator builds from a driver's
query entries (a poll query, a start-up entry, a rule's then_query, a
command's read_back) and how it answers a list read. An entry's shape decides
which handler takes it, a flat variable, each child, a list sent once or one
page of a list, or none at all; the empty cells are pinned as well as the
full ones. Recorded from the code as it is, on an invented device.
"""

from __future__ import annotations

import logging
import re

import pytest

from openavc.simulator.yaml_auto import ListQueryHandler, YAMLAutoSimulator


def _base() -> dict:
    return {
        "id": "acme_matrix", "name": "Acme Matrix", "transport": "tcp", "delimiter": "\\r",
        "default_config": {"unit": "4"},
        "state_variables": {
            "power": {"type": "boolean"}, "volume": {"type": "integer"},
            "status": {"type": "string"},
        },
        "child_entity_types": {"output": {
            "id_format": "int", "instances": {"count": 3},
            "state_variables": {"input": {"type": "integer"}, "name": {"type": "string"}},
        }},
        "commands": {
            "get_power": {"send": "PWR?"},
            "volume_query": {"send": "VQ?", "query_for": "volume"},
            "status_with_param": {"send": "ST?", "params": {"x": {"type": "integer"}}},
        },
        "responses": [
            {"match": "PWR(\\d)", "set": {"power": "$1"}},
            {"match": "VOL(\\d+)", "set": {"volume": "$1"}},
            {"match": "STATUS (\\w+)", "set": {"status": "$1"}},
            {"match": "Out(\\d+) In(\\d+)", "child_set": [
                {"type": "output", "id": "$1", "state": {"input": "$2"}}]},
            {"match": "NAMES (.*)", "child_list": {
                "type": "output", "element": "([^,]*)", "separator": ",",
                "state": {"name": "$1"}}},
        ],
    }


def _follow_ups_and_read_back(d: dict) -> None:
    d["responses"][1]["then_query"] = ["PWR?", {"send": "ZZ?", "query_for": "status"}]
    d["commands"]["set_vol"] = {
        "send": "SV {level}", "params": {"level": {"type": "integer"}},
        "sets": {"volume": "{level}"}, "read_back": "RB?",
    }


# label: (polling.queries, on_connect, a change to the definition)
SCENARIOS = {
    "s01_bare_cmd": (["PWR?"], None, None),
    "s02_bare_nocmd": (["NOPE?"], None, None),
    "s03_declared": ([{"send": "VOL?", "query_for": "volume"}], None, None),
    "s04_cmd_qf": ([{"send": "VQ?"}], None, None),
    "s05_cmd_params": ([{"send": "ST?"}], None, None),
    "s06_empty_send": ([{"send": "", "query_for": "volume"}, {"query_for": "volume"}], None, None),
    "s07_dotted_answer": ([{"send": "NAMES?", "query_for": "output.name"}], None, None),
    "s08_dotted_noanswer": ([{"send": "INPUTS?", "query_for": "output.input"}], None, None),
    "s09_each_child": (
        [{"send": "Q{child_id}?", "each_child": "output", "query_for": "input"}], None, None),
    "s10_page2": ([{"send": "N{child_id}?", "each_child": "output", "query_for": "name",
                    "page_size": 2}], None, None),
    "s11_page_bad": ([
        {"send": "A{child_id}?", "each_child": "output", "query_for": "name", "page_size": 1},
        {"send": "B{child_id}?", "each_child": "output", "query_for": "name", "page_size": True},
        {"send": "C{child_id}?", "each_child": "output", "query_for": "name", "page_size": "4"},
        {"send": "D{child_id}?", "each_child": "output", "query_for": "input", "page_size": 2},
    ], None, None),
    "s12_each_none_dotted": (
        [{"send": "NAMES?", "each_child": None, "query_for": "output.name"}], None, None),
    "s13_each_none_plain": (
        [{"send": "VOL?", "each_child": None, "query_for": "volume"}], None, None),
    "s14_each_int": ([{"send": "Q{child_id}?", "each_child": 5, "query_for": "input"}], None, None),
    "s15_each_dotted": (
        [{"send": "Q{child_id}?", "each_child": "output", "query_for": "output.input"}], None, None),
    "s16_onconnect": (None, [
        {"send": "STATUS?", "query_for": "status"},
        {"send": "VOL?"},
        {"send": "PWR?", "query_for": "power", "address": "/x"},
        "VQ?",
        {"send": "Q{child_id}?", "each_child": "output", "query_for": "input"},
        {"send": "NAMES?", "query_for": "output.name"},
        {"send": "", "query_for": "status"},
    ], None),
    "s17_order": (["PWR?", {"send": "VOL?", "query_for": "volume"}],
                  [{"send": "PWR?", "query_for": "status"}], _follow_ups_and_read_back),
    "s18_qf_nonstr": ([{"send": "VOL?", "query_for": 5}, {"send": "PWR?", "query_for": ""}],
                      None, None),
    "s19_config_sub": ([{"send": "U{unit}?\\r", "query_for": "volume"}], None, None),
    "s20_child_and_list_order": ([
        {"send": "Q{child_id}?", "each_child": "output", "query_for": "input"},
        {"send": "NAMES?", "query_for": "output.name"},
    ], None, None),
    "s21_child_bad": ([
        {"send": "", "each_child": "output", "query_for": "input"},
        {"send": "Q{child_id}?", "each_child": "output", "query_for": ""},
        {"send": 7, "each_child": "output", "query_for": "input"},
    ], None, None),
    "s22_list_nonstr_qf": (
        [{"send": "NAMES?", "query_for": None}, {"send": "NAMES?", "query_for": "output."}],
        None, None),
    "s23_send_nonstr": ([{"send": 5, "query_for": "volume"}], None, None),
    "s24_page_unhashable": ([{"send": "N{child_id}?", "each_child": ["output"],
                              "query_for": "name", "page_size": 2}], None, None),
    "s25_page_nonstr_qf": ([{"send": "N{child_id}?", "each_child": "output", "query_for": 3,
                             "page_size": 2}], None, None),
}

ROUTES = {'s01_bare_cmd': ([('^PWR\\?$', 'power')], []),
 's02_bare_nocmd': ([], []),
 's03_declared': ([('^VOL\\?$', 'volume')], []),
 's04_cmd_qf': ([('^VQ\\?$', 'volume')], []),
 's05_cmd_params': ([], []),
 's06_empty_send': ([], []),
 's07_dotted_answer': ([],
                       [('ListQueryHandler', '^NAMES\\?$', 'output', 'name', None, None, None)]),
 's08_dotted_noanswer': ([], []),
 's09_each_child': ([],
                    [('ChildQueryHandler', '^Q1\\?$', 'output', 'input', '1', None, None),
                     ('ChildQueryHandler', '^Q2\\?$', 'output', 'input', '2', None, None),
                     ('ChildQueryHandler', '^Q3\\?$', 'output', 'input', '3', None, None)]),
 's10_page2': ([],
               [('ListQueryHandler', '^N1\\?$', 'output', 'name', None, '1', 2),
                ('ListQueryHandler', '^N3\\?$', 'output', 'name', None, '3', 2)]),
 's11_page_bad': ([], []),
 's12_each_none_dotted': ([],
                          [('ListQueryHandler', '^NAMES\\?$', 'output', 'name', None, None, None)]),
 's13_each_none_plain': ([], []),
 's14_each_int': ([], []),
 's15_each_dotted': ([],
                     [('ChildQueryHandler', '^Q1\\?$', 'output', 'output.input', '1', None, None),
                      ('ChildQueryHandler', '^Q2\\?$', 'output', 'output.input', '2', None, None),
                      ('ChildQueryHandler', '^Q3\\?$', 'output', 'output.input', '3', None, None)]),
 's16_onconnect': ([('^STATUS\\?$', 'status')],
                   [('ListQueryHandler', '^NAMES\\?$', 'output', 'name', None, None, None),
                    ('ChildQueryHandler', '^Q1\\?$', 'output', 'input', '1', None, None),
                    ('ChildQueryHandler', '^Q2\\?$', 'output', 'input', '2', None, None),
                    ('ChildQueryHandler', '^Q3\\?$', 'output', 'input', '3', None, None)]),
 's17_order': ([('^PWR\\?$', 'power'),
                ('^VOL\\?$', 'volume'),
                ('^ZZ\\?$', 'status'),
                ('^RB\\?$', 'volume'),
                ('^PWR\\?$', 'status')],
               []),
 's18_qf_nonstr': ([('^PWR\\?$', 'power')], []),
 's19_config_sub': ([('^U4\\?$', 'volume')], []),
 's20_child_and_list_order': ([],
                              [('ListQueryHandler',
                                '^NAMES\\?$',
                                'output',
                                'name',
                                None,
                                None,
                                None),
                               ('ChildQueryHandler', '^Q1\\?$', 'output', 'input', '1', None, None),
                               ('ChildQueryHandler', '^Q2\\?$', 'output', 'input', '2', None, None),
                               ('ChildQueryHandler',
                                '^Q3\\?$',
                                'output',
                                'input',
                                '3',
                                None,
                                None)]),
 's21_child_bad': ([], []),
 's22_list_nonstr_qf': ([], []),
 's23_send_nonstr': ('raise', 'TypeError'),
 's24_page_unhashable': ('raise', 'TypeError'),
 's25_page_nonstr_qf': ([], [])}

LIST_READS = [(False, ('1', '2', '3'), None, None, b'NAMES one,,three\r'),
 (False, ('1', '2', '3'), '2', 2, b'NAMES ,three\r'),
 (False, ('1', '2', '3'), 'x', None, None),
 (False, ('1', '2', '3'), 'x', 2, None),
 (False, ('1', '2', '3'), '5', 2, None),
 (False, ('1', '2', '3'), '2', None, b'NAMES ,three\r'),
 (False, ('1', '2', '3'), '0', 3, b'NAMES ,one,\r'),
 (False, ('1', '3'), None, None, b'NAMES one,,three\r'),
 (False, ('1', '3'), '2', 2, b'NAMES ,three\r'),
 (False, ('1', '3'), 'x', None, None),
 (False, ('1', '3'), 'x', 2, None),
 (False, ('1', '3'), '5', 2, None),
 (False, ('1', '3'), '2', None, b'NAMES ,three\r'),
 (False, ('1', '3'), '0', 3, b'NAMES ,one,\r'),
 (False, (), None, None, None),
 (False, (), '2', 2, None),
 (False, (), 'x', None, None),
 (False, (), 'x', 2, None),
 (False, (), '5', 2, None),
 (False, (), '2', None, None),
 (False, (), '0', 3, None),
 (False, ('a', 'b'), None, None, None),
 (False, ('a', 'b'), '2', 2, None),
 (False, ('a', 'b'), 'x', None, None),
 (False, ('a', 'b'), 'x', 2, None),
 (False, ('a', 'b'), '5', 2, None),
 (False, ('a', 'b'), '2', None, None),
 (False, ('a', 'b'), '0', 3, None),
 (False, ('1', 'a', '4'), None, None, b'NAMES one,,three,\r'),
 (False, ('1', 'a', '4'), '2', 2, b'NAMES ,three\r'),
 (False, ('1', 'a', '4'), 'x', None, None),
 (False, ('1', 'a', '4'), 'x', 2, None),
 (False, ('1', 'a', '4'), '5', 2, None),
 (False, ('1', 'a', '4'), '2', None, b'NAMES ,three,\r'),
 (False, ('1', 'a', '4'), '0', 3, b'NAMES ,one,\r'),
 (True, ('1', '2', '3'), None, None, b'NAMES 1=one,2=,3=three\r'),
 (True, ('1', '2', '3'), '2', 2, b'NAMES 2=,3=three\r'),
 (True, ('1', '2', '3'), 'x', None, b'NAMES 1=one,2=,3=three\r'),
 (True, ('1', '2', '3'), 'x', 2, b'NAMES 1=one,2=,3=three\r'),
 (True, ('1', '2', '3'), '5', 2, None),
 (True, ('1', '2', '3'), '2', None, b'NAMES 1=one,2=,3=three\r'),
 (True, ('1', '2', '3'), '0', 3, b'NAMES 1=one,2=\r'),
 (True, ('1', '3'), None, None, b'NAMES 1=one,3=three\r'),
 (True, ('1', '3'), '2', 2, b'NAMES 3=three\r'),
 (True, ('1', '3'), 'x', None, b'NAMES 1=one,3=three\r'),
 (True, ('1', '3'), 'x', 2, b'NAMES 1=one,3=three\r'),
 (True, ('1', '3'), '5', 2, None),
 (True, ('1', '3'), '2', None, b'NAMES 1=one,3=three\r'),
 (True, ('1', '3'), '0', 3, b'NAMES 1=one\r'),
 (True, (), None, None, None),
 (True, (), '2', 2, None),
 (True, (), 'x', None, None),
 (True, (), 'x', 2, None),
 (True, (), '5', 2, None),
 (True, (), '2', None, None),
 (True, (), '0', 3, None),
 (True, ('a', 'b'), None, None, b'NAMES a=,b=\r'),
 (True, ('a', 'b'), '2', 2, None),
 (True, ('a', 'b'), 'x', None, b'NAMES a=,b=\r'),
 (True, ('a', 'b'), 'x', 2, b'NAMES a=,b=\r'),
 (True, ('a', 'b'), '5', 2, None),
 (True, ('a', 'b'), '2', None, b'NAMES a=,b=\r'),
 (True, ('a', 'b'), '0', 3, None),
 (True, ('1', 'a', '4'), None, None, b'NAMES 1=one,a=,4=\r'),
 (True, ('1', 'a', '4'), '2', 2, None),
 (True, ('1', 'a', '4'), 'x', None, b'NAMES 1=one,a=,4=\r'),
 (True, ('1', 'a', '4'), 'x', 2, b'NAMES 1=one,a=,4=\r'),
 (True, ('1', 'a', '4'), '5', 2, None),
 (True, ('1', 'a', '4'), '2', None, b'NAMES 1=one,a=,4=\r'),
 (True, ('1', 'a', '4'), '0', 3, b'NAMES 1=one\r')]


def _built(label: str) -> tuple:
    poll, on_connect, change = SCENARIOS[label]
    d = _base()
    if poll is not None:
        d["polling"] = {"queries": poll}
    if on_connect is not None:
        d["on_connect"] = on_connect
    if change:
        change(d)
    try:
        sim = YAMLAutoSimulator("m1", {}, driver_def=d)
    except Exception as e:  # noqa: BLE001 - which exception is the pin
        return ("raise", type(e).__name__)
    flat = [(h.pattern.pattern, h.response_var) for h in sim._query_handlers]
    child = [(type(h).__name__, h.pattern.pattern, h.child_type, h.response_var,
              getattr(h, "child_id", None), getattr(h, "first", None),
              getattr(h, "count", None))
             for h in sim._child_query_handlers]
    return (flat, child)


@pytest.mark.parametrize("label", list(SCENARIOS))
def test_each_entry_shape_reaches_the_handlers_it_reaches(label):
    assert _built(label) == ROUTES[label]


def _list_sim(by_id: bool) -> YAMLAutoSimulator:
    rule = {"match": "NAMES (.*)", "child_list": {
        "type": "output", "element": "(\\d+)=([^,]*)" if by_id else "([^,]*)",
        "separator": ",", "state": {"name": "$2" if by_id else "$1"}}}
    if by_id:
        rule["child_list"]["id"] = "$1"
    else:
        rule["child_list"]["start"] = 1
    d = {
        "id": "acme_matrix", "name": "Acme Matrix", "transport": "tcp", "delimiter": "\\r",
        "state_variables": {},
        "child_entity_types": {"output": {"id_format": "int", "state_variables": {
            "name": {"type": "string", "default": "dflt"}}}},
        "commands": {}, "responses": [rule],
    }
    return YAMLAutoSimulator("m1", {}, driver_def=d)


@pytest.mark.parametrize("row", LIST_READS, ids=lambda r: repr(r[:4]))
def test_a_list_read_lists_the_children_it_covers(row):
    by_id, roster, first, count, expected = row
    sim = _list_sim(by_id)
    sim._child_roster = {"output": list(roster)}
    sim._state["output.1.name"] = "one"
    sim._state["output.3.name"] = "three"
    handler = ListQueryHandler(re.compile("^Z$"), "output", "name", first, count)
    assert sim._execute_list_query_handler(handler) == expected


@pytest.mark.parametrize("by_id", [False, True])
def test_a_list_read_no_rule_fills_is_unanswered(by_id):
    sim = _list_sim(by_id)
    handler = ListQueryHandler(re.compile("^Z$"), "output", "input")
    assert sim._execute_list_query_handler(handler) is None


def test_a_list_by_place_says_where_its_page_starts():
    d = {
        "id": "acme_matrix", "name": "Acme Matrix", "transport": "tcp", "delimiter": "\\r",
        "state_variables": {},
        "child_entity_types": {"output": {"id_format": "int", "instances": {"count": 5},
                                          "state_variables": {"name": {"type": "string"}}}},
        "commands": {},
        "responses": [{"match": "NAMES (\\d+):(.*)", "child_list": {
            "type": "output", "group": 2, "start": "$1", "element": "([^,]*)",
            "separator": ",", "state": {"name": "$1"}}}],
        "polling": {"queries": [
            {"send": "N{child_id}?", "each_child": "output", "query_for": "name", "page_size": 2},
            {"send": "ALL?", "query_for": "output.name"},
        ]},
    }
    sim = YAMLAutoSimulator("m1", {}, driver_def=d)
    sim._state["output.4.name"] = "four"
    replies = {h.pattern.pattern: sim._execute_child_query_handler(h)
               for h in sim._child_query_handlers}
    assert replies == {
        "^N1\\?$": b"NAMES 1:,\r",
        "^N3\\?$": b"NAMES 3:,four\r",
        "^N5\\?$": b"NAMES 5:\r",
        "^ALL\\?$": b"NAMES 1:,,,four,\r",
    }


def test_project_children_rebuild_the_child_handlers_lists_first(caplog):
    d = _base()
    d["polling"] = {"queries": [
        {"send": "Q{child_id}?", "each_child": "output", "query_for": "input"},
        {"send": "N{child_id}?", "each_child": "output", "query_for": "name", "page_size": 2},
    ]}
    sim = YAMLAutoSimulator("m1", {}, driver_def=d)
    with caplog.at_level(logging.DEBUG):
        sim.set_child_entities({"output": {"004": {"label": "Four"}, "x": {}}})
    assert [(type(h).__name__, h.pattern.pattern) for h in sim._child_query_handlers] == [
        ("ListQueryHandler", "^N1\\?$"),
        ("ListQueryHandler", "^N3\\?$"),
        ("ListQueryHandler", "^Nx\\?$"),
        ("ChildQueryHandler", "^Q1\\?$"),
        ("ChildQueryHandler", "^Q2\\?$"),
        ("ChildQueryHandler", "^Q3\\?$"),
        ("ChildQueryHandler", "^Q4\\?$"),
        ("ChildQueryHandler", "^Qx\\?$"),
    ]
