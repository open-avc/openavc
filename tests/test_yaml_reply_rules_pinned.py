"""What a YAML driver's reply rules do with the replies nobody else tests.

The text (regex), OSC and JSON-body rules each read a reply into flat state
and child state. These pin the edges: a line that is only whitespace, a rule
whose ``only_when`` does not hold, a capture or an argument the reply does
not carry, a child id that cannot be placed, a body that is not JSON, and
the typed args an OSC command sends. On an invented Acme device.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import ConfigurableDriver, create_configurable_driver_class
from openavc.transport.osc_codec import osc_encode_message

_CHANNELS = {
    "channel": {
        "label": "Channel",
        "id_format": {"type": "integer", "min": 1, "max": 8},
        "state_variables": {
            "level": {"type": "integer", "label": "Level"},
            "name": {"type": "string", "label": "Name"},
        },
        "instances": {"count": 4, "label": "Channel {id}"},
    },
}


def _definition(transport: str, responses: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_mixer",
        "name": "Acme Mixer",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": transport,
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 5000},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
        },
        "state_variables": {
            "mode": {"type": "string", "label": "Mode"},
            "power": {"type": "string", "label": "Power"},
            "level": {"type": "integer", "label": "Level"},
            "label": {"type": "string", "label": "Label"},
        },
        "child_entity_types": _CHANNELS,
        "commands": {},
        "responses": responses,
    }
    definition.update(over)
    return definition


def _driver(definition: dict[str, Any]) -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 5000}, state, events)
    driver._register_declared_children()
    seen: list[tuple[str, dict[str, Any]]] = []
    driver.contract_observer = lambda kind, detail: seen.append((kind, detail))
    return driver, seen


def _kinds(seen: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [kind for kind, _ in seen]


# --- text (regex) rules ----------------------------------------------------


@pytest.mark.parametrize("line", [b"\r\n", b"   ", b""])
async def test_a_line_that_is_only_whitespace_is_not_a_message(line):
    driver, seen = _driver(_definition("tcp", [{"match": r"PWR(\w+)", "set": {"power": "$1"}}]))
    before = (driver._framed_messages, driver._parsed_messages, driver._last_unread)
    await driver.on_data_received(line)
    assert (driver._framed_messages, driver._parsed_messages, driver._last_unread) == before
    assert seen == []


async def test_a_line_no_rule_reads_is_counted_framed_and_told_unread():
    driver, seen = _driver(_definition("tcp", [{"match": r"PWR(\w+)", "set": {"power": "$1"}}]))
    await driver.on_data_received(b"  HELLO there  \r\n")
    assert driver._framed_messages == 1
    assert driver._parsed_messages == 0
    assert driver._last_unread == "HELLO there"
    assert seen == [("unmatched_response", {"text": "HELLO there"})]


async def test_a_rule_whose_only_when_does_not_hold_leaves_the_line_to_the_next():
    driver, seen = _driver(_definition("tcp", [
        {"match": r"LVL(\d+)", "only_when": {"key": "mode", "value": "live"},
         "set": {"level": "$1"}},
        {"match": r"LVL(\d+)", "set": {"label": "$1"}},
    ]))
    await driver.on_data_received(b"LVL7")
    assert driver.get_state("level") is None
    assert driver.get_state("label") == "7"
    assert driver._parsed_messages == 1
    driver.set_state("mode", "live")
    await driver.on_data_received(b"LVL9")
    assert driver.get_state("level") == 9
    assert driver.get_state("label") == "7"


async def test_a_mapping_naming_a_capture_the_rule_lacks_is_skipped_alone():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"ST(\w+)",
        "mappings": [
            {"group": 3, "state": "mode"},
            {"group": 1, "state": "power"},
        ],
    }]))
    await driver.on_data_received(b"STON")
    assert driver.get_state("mode") is None
    assert driver.get_state("power") == "ON"
    assert driver._parsed_messages == 1


async def test_an_optional_capture_that_did_not_take_part_writes_nothing():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"ST(\w+)(?: M(\w+))?",
        "mappings": [
            {"group": 2, "state": "mode"},
            {"group": 1, "state": "power"},
        ],
    }]))
    await driver.on_data_received(b"STON")
    assert driver.get_state("mode") is None
    assert driver.get_state("power") == "ON"
    assert seen == []


@pytest.mark.parametrize(
    ("capture", "path", "expected"),
    [
        ('{"data": {"name": "Intro"}}', "data.name", "Intro"),
        ('{"data": [4, 5, 6]}', "data", "3"),
        ('{"data": [4, 5, 6]}', "data.1", "5"),
        ('{"data": {"a": 1, "b": 2}}', "data..b", "2"),
        ('{"data": 7}', "", "1"),
        ('{"data": 7}', "missing", None),
        ('{"data": [1]}', "data.4", None),
        ('{"data": [1]}', "data.x", None),
        ('{"data": "text"}', "data.inner", None),
        ("not json at all", "data", None),
    ],
)
async def test_a_capture_read_through_json_path(capture, path, expected):
    driver, _ = _driver(_definition("tcp", [{
        "match": r"^R (.*)$",
        "mappings": [{"group": 1, "state": "label", "json_path": path}],
    }]))
    await driver.on_data_received(f"R {capture}".encode())
    assert driver.get_state("label") == expected


async def test_a_mapping_value_map_names_the_value_before_conversion():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"LV(\d+)",
        "mappings": [{"group": 1, "state": "level", "type": "integer",
                      "map": {"1": "10", "2": None}}],
    }]))
    await driver.on_data_received(b"LV1")
    assert driver.get_state("level") == 10
    await driver.on_data_received(b"LV2")
    assert driver.get_state("level") is None
    await driver.on_data_received(b"LV3")
    assert driver.get_state("level") == 3


async def test_child_set_ids_that_cannot_be_placed_are_skipped():
    driver, seen = _driver(_definition("tcp", [
        # A capture the pattern does not have, an optional one that did not
        # take part, a literal that is not an integer id, and one beyond the
        # roster: none writes, and only the last is told to an observer.
        {"match": r"A(\d+)", "child_set": [{"type": "channel", "id": "$4", "state": {"level": "$1"}}]},
        {"match": r"B(\d+)(?:@(\d+))?", "child_set": [{"type": "channel", "id": "$2", "state": {"level": "$1"}}]},
        {"match": r"C(\d+)", "child_set": [{"type": "channel", "id": "abc", "state": {"level": "$1"}}]},
        {"match": r"D(\d+)", "child_set": [{"type": "channel", "id": 7, "state": {"level": "$1"}}]},
    ]))
    for line in (b"A5", b"B5", b"C5", b"D5"):
        await driver.on_data_received(line)
    assert [driver.get_child_state("channel", n).get("level") for n in range(1, 5)] == [None] * 4
    assert seen == [("child_unregistered", {"child_type": "channel", "local_id": 7})]
    assert driver._parsed_messages == 4


async def test_a_child_prop_naming_a_capture_the_rule_lacks_is_skipped_alone():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"CH(\d+) (\d+)",
        "child_set": [{"type": "channel", "id": "$1",
                       "state": {"level": "$2", "name": "$5"}}],
    }]))
    await driver.on_data_received(b"CH2 40")
    child = driver.get_child_state("channel", 2)
    assert child.get("level") == 40
    assert child.get("name") is None


async def test_a_child_set_id_map_places_the_wire_id():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"CH(\w+) (\d+)",
        "child_set": [{"type": "channel", "id": {"group": 1, "map": {"A": 1, "B": 2}},
                       "state": {"level": "$2"}}],
    }]))
    await driver.on_data_received(b"CHB 33")
    await driver.on_data_received(b"CHZ 44")
    assert driver.get_child_state("channel", 2).get("level") == 33
    assert [driver.get_child_state("channel", n).get("level") for n in (1, 3, 4)] == [None] * 3
    assert seen == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # The list capture the rule names is not in the pattern: nothing.
        (b"BAD 1,2", [None, None, None, None]),
        # An empty list writes nothing.
        (b"LST ", [None, None, None, None]),
    ],
)
async def test_child_list_with_nothing_to_spread(line, expected):
    driver, _ = _driver(_definition("tcp", [
        {"match": r"BAD (.*)",
         "child_list": {"type": "channel", "group": 3, "separator": ",",
                        "element": r"(\d+)", "state": {"level": "$1"}}},
        {"match": r"LST (.*)",
         "child_list": {"type": "channel", "group": 1, "separator": ",",
                        "element": r"(\d+)", "state": {"level": "$1"}}},
    ]))
    await driver.on_data_received(line)
    assert [driver.get_child_state("channel", n).get("level") for n in range(1, 5)] == expected


async def test_child_list_items_without_their_id_hold_their_place():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"LST (.*)",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(?:(\d+)=)?(\d+)", "id": "$1",
                       "state": {"level": "$2"}},
    }]))
    await driver.on_data_received(b"LST 1=10, 20 ,3=30,x")
    assert [driver.get_child_state("channel", n).get("level") for n in range(1, 5)] == [10, None, 30, None]


async def test_child_list_items_numbered_from_a_start_capture():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"PG(\d+) (.*)",
        "child_list": {"type": "channel", "group": 2, "start": "$1",
                       "element": r"\d+", "state": {"level": "$0"}},
    }]))
    await driver.on_data_received(b"PG2 11 12 13 14")
    assert [driver.get_child_state("channel", n).get("level") for n in range(1, 5)] == [None, 11, 12, 13]


# --- OSC rules -------------------------------------------------------------


async def test_osc_bytes_that_do_not_decode_are_dropped_with_a_warning(caplog):
    driver, seen = _driver(_definition("osc", [{"address": "/acme/*", "mappings": [{"arg": 0, "state": "level"}]}]))
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.configurable"):
        await driver.on_data_received(b"\x00\x01garbage")
    assert "Failed to decode OSC message" in caplog.text
    assert driver._framed_messages == 0
    assert seen == []


async def test_osc_rule_whose_only_when_does_not_hold_leaves_the_message_to_the_next():
    driver, seen = _driver(_definition("osc", [
        {"address": "/acme/level", "only_when": {"key": "mode", "value": "live"},
         "mappings": [{"arg": 0, "state": "level"}]},
        {"address": "/acme/*", "mappings": [{"arg": 0, "state": "label"}]},
    ]))
    await driver.on_data_received(osc_encode_message("/acme/level", [("i", 12)]))
    assert driver.get_state("level") is None
    assert driver.get_state("label") == "12"
    assert (driver._framed_messages, driver._parsed_messages) == (1, 1)
    assert seen == []


async def test_osc_mapping_naming_an_arg_the_message_lacks_is_skipped_alone():
    driver, _ = _driver(_definition("osc", [{
        "address": "/acme/st",
        "mappings": [{"arg": 3, "state": "mode"}, {"arg": 0, "state": "power"}],
    }]))
    await driver.on_data_received(osc_encode_message("/acme/st", [("s", "on")]))
    assert driver.get_state("mode") is None
    assert driver.get_state("power") == "on"


async def test_osc_message_no_rule_reads_is_told_unread_with_its_args():
    driver, seen = _driver(_definition("osc", [{"address": "/acme/level", "mappings": [{"arg": 0, "state": "level"}]}]))
    await driver.on_data_received(osc_encode_message("/other/thing", [("i", 3), ("s", "x")]))
    assert driver._last_unread == "/other/thing"
    assert seen == [("unmatched_response", {"address": "/other/thing", "args": [3, "x"]})]


async def test_osc_child_set_entries_that_cannot_be_placed_or_read():
    driver, seen = _driver(_definition("osc", [{
        "address": "/ch/*/fader",
        "child_set": [
            # Segment beyond the address: skipped.
            {"type": "channel", "id": {"segment": 9}, "state": {"level": {"arg": 0}}},
            # The prop's arg is not in the message: no write for this entry.
            {"type": "channel", "id": {"segment": 1}, "state": {"level": {"arg": 5}}},
            # Mapped id, a literal prop and a positional prop.
            {"type": "channel", "id": {"segment": 1, "map": {"03": 3}},
             "state": {"level": {"arg": 0}, "name": "Fixed"}},
            # A wire id outside the map: skipped quietly.
            {"type": "channel", "id": {"segment": 1, "map": {"09": 4}},
             "state": {"level": {"arg": 0}}},
        ],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/03/fader", [("i", 42)]))
    assert driver.get_child_state("channel", 3).get("level") == 42
    assert driver.get_child_state("channel", 3).get("name") == "Fixed"
    assert [driver.get_child_state("channel", n).get("level") for n in (1, 2, 4)] == [None] * 3
    assert seen == []


async def test_osc_child_set_beyond_the_roster_is_told_unregistered():
    driver, seen = _driver(_definition("osc", [{
        "address": "/ch/*/fader",
        "child_set": [{"type": "channel", "id": {"segment": 1}, "state": {"level": {"arg": 0}}}],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/7/fader", [("i", 1)]))
    assert seen == [("child_unregistered", {"child_type": "channel", "local_id": 7})]


async def test_osc_value_that_does_not_convert_is_told_to_an_observer():
    driver, seen = _driver(_definition("osc", [{"address": "/acme/level", "mappings": [{"arg": 0, "state": "level", "type": "integer"}]}]))
    await driver.on_data_received(osc_encode_message("/acme/level", [("s", "loud")]))
    assert ("coercion_failure", {"raw": "loud", "type": "integer", "stored": "loud"}) in seen


async def test_osc_json_path_arg_and_value_map():
    driver, _ = _driver(_definition("osc", [{
        "address": "/reply/*",
        "mappings": [
            {"arg": 0, "state": "label", "json_path": "data"},
            {"arg": 0, "state": "mode", "json_path": "nope"},
            {"arg": 1, "state": "power", "map": {"1": "On", "0": "Off"}},
        ],
    }]))
    await driver.on_data_received(
        osc_encode_message("/reply/cue", [("s", '{"data": "Intro"}'), ("i", 1)])
    )
    assert driver.get_state("label") == "Intro"
    assert driver.get_state("mode") is None
    assert driver.get_state("power") == "On"


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ({"type": "f", "value": "1.5"}, ("f", 1.5)),
        ({"type": "i", "value": "2.9"}, ("i", 2)),
        ({"type": "s", "value": "{name}"}, ("s", "Lead")),
        ({"type": "h", "value": "12"}, ("h", 12)),
        ({"type": "d", "value": "0.25"}, ("d", 0.25)),
        ({"type": "T"}, ("T", True)),
        ({"type": "F"}, ("F", False)),
        ({"type": "N"}, ("N", None)),
        ({"value": "3"}, ("f", 3.0)),
    ],
)
def test_osc_args_by_type_tag(arg, expected):
    assert ConfigurableDriver._build_osc_args([arg], {"name": "Lead"}) == [expected]
    value = ConfigurableDriver._build_osc_args([arg], {"name": "Lead"})[0][1]
    assert type(value) is type(expected[1])


@pytest.mark.parametrize("tag", ["h", "d"])
def test_osc_numeric_arg_that_will_not_read_names_its_tag(tag):
    with pytest.raises(ValueError, match=f"OSC arg of type '{tag}' requires a numeric value, got 'x'"):
        ConfigurableDriver._build_osc_args([{"type": tag, "value": "x"}], {})


# --- JSON-body rules -------------------------------------------------------


async def test_json_rule_whose_only_when_does_not_hold_leaves_the_body_to_the_regex_rules():
    driver, seen = _driver(_definition("tcp", [
        {"json": True, "only_when": {"key": "mode", "value": "live"},
         "set": {"level": {"key": "level", "type": "integer"}}},
        {"match": r'"level": (\d+)', "set": {"label": "$1"}},
    ]))
    await driver.on_data_received(b'{"level": 12}')
    assert driver.get_state("level") is None
    assert driver.get_state("label") == "12"
    assert driver._parsed_messages == 1
    assert seen == []


async def test_json_null_with_a_declared_type_is_no_reading_and_not_a_coercion_failure():
    driver, seen = _driver(_definition("tcp", [
        {"json": True, "set": {"level": {"key": "level", "type": "integer"}}},
    ]))
    await driver.on_data_received(b'{"level": 4}')
    await driver.on_data_received(b'{"level": null}')
    assert seen == []


async def test_json_child_set_literal_that_is_not_a_child_id_is_skipped_quietly():
    driver, seen = _driver(_definition("tcp", [{
        "json": True,
        "child_set": [
            {"type": "channel", "id": "abc", "state": {"level": "lvl"}},
            {"type": "channel", "id": 2, "state": {"level": "lvl", "name": "nm"}},
        ],
    }]))
    assert driver._apply_json_responses('{"lvl": 5}') is True
    assert driver.get_child_state("channel", 2).get("level") == 5
    assert driver.get_child_state("channel", 2).get("name") is None
    assert seen == []


async def test_json_child_set_beyond_the_roster_is_told_only_when_the_body_carries_it():
    driver, seen = _driver(_definition("tcp", [{
        "json": True,
        "child_set": [{"type": "channel", "id": 6, "state": {"level": "lvl6"}}],
        "set": {"mode": "mode"},
    }]))
    assert driver._apply_json_responses('{"mode": "a"}') is True
    assert seen == []
    assert driver._apply_json_responses('{"lvl6": 1}') is False
    assert seen == [("child_unregistered", {"child_type": "channel", "local_id": 6})]


@pytest.mark.parametrize(
    ("body", "applied"),
    [
        ('[{"mode": "a"}]', True),
        ('[{"mode": "a"}, {"mode": "b"}]', False),
        ('["mode"]', False),
        ('"mode"', False),
        ("{not json", False),
        ('{"other": 1}', False),
    ],
)
def test_json_body_shapes(body, applied):
    driver, _ = _driver(_definition("tcp", [{"json": True, "set": {"mode": "mode"}}]))
    assert driver._apply_json_responses(body) is applied
    assert driver.get_state("mode") == ("a" if applied else None)


@pytest.mark.parametrize(
    ("value", "needle", "expected"),
    [
        (["NoLink", 3], 3, True),
        (["NoLink", 3], "3", True),
        ([1, 2], "9", False),
        ({"LowBattery": 1}, "LowBattery", True),
        ({"LowBattery": 1}, 1, False),
        ("abcdef", "cd", True),
        (5, 5, True),
        (5, "5", True),
        (True, "True", True),
        (5, 6, False),
    ],
)
def test_json_contains_mapping(value, needle, expected):
    obj = {"flags": value}
    assert ConfigurableDriver._read_json_mapping(obj, {"key": "flags", "contains": needle}) is expected


def test_json_contains_on_a_missing_key_is_missing():
    missing = ConfigurableDriver._read_json_mapping({}, {"key": "flags", "contains": "x"})
    assert missing is ConfigurableDriver._extract_json_path({}, "flags")
    assert ConfigurableDriver._read_json_mapping({}, {"key": "flags"}) is missing


def test_json_path_on_an_already_parsed_node():
    assert ConfigurableDriver._extract_json_path({"a": [1, 2]}, "a") == 2
    assert ConfigurableDriver._extract_json_path([{"a": 1}], "0.a") == 1
    assert ConfigurableDriver._extract_json_path({"a": [1, 2]}, "a", collapse=False) == [1, 2]
    assert ConfigurableDriver._extract_json_path("[1, 2]", None) == 2


# --- what a reading is counted and told as ---------------------------------


async def test_a_json_path_value_is_read_as_text_before_its_map():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"^R (.*)$",
        "mappings": [
            {"group": 1, "state": "power", "json_path": "on", "map": {"1": "On", "True": "Yes"}},
            {"group": 1, "state": "level", "json_path": "lvl", "type": "integer"},
        ],
    }]))
    await driver.on_data_received(b'R {"on": 1, "lvl": "x"}')
    assert driver.get_state("power") == "On"
    assert ("coercion_failure", {"raw": "x", "type": "integer", "stored": "x"}) in seen
    await driver.on_data_received(b'R {"on": true, "lvl": 3}')
    assert driver.get_state("power") == "Yes"
    assert driver.get_state("level") == 3


async def test_a_body_read_by_json_and_an_after_json_rule_counts_once():
    driver, _ = _driver(_definition("tcp", [
        {"json": True, "set": {"mode": "mode"}},
        {"match": r'"error": "(\w+)"', "after_json": True, "set": {"label": "$1"}},
    ]))
    await driver.on_data_received(b'{"mode": "a", "error": "jam"}')
    assert driver.get_state("mode") == "a"
    assert driver.get_state("label") == "jam"
    assert (driver._framed_messages, driver._parsed_messages) == (1, 1)
    await driver.on_data_received(b'{"mode": "b"}')
    assert (driver._framed_messages, driver._parsed_messages) == (2, 2)
    assert driver._last_unread == ""


async def test_a_held_osc_rule_consumes_the_message_inside_its_window():
    driver, _ = _driver(_definition("osc", [
        {"address": "/acme/level", "throttle": 60, "mappings": [{"arg": 0, "state": "level"}]},
        {"address": "/acme/*", "mappings": [{"arg": 0, "state": "label"}]},
    ]))
    await driver.on_data_received(osc_encode_message("/acme/level", [("i", 1)]))
    await driver.on_data_received(osc_encode_message("/acme/level", [("i", 2)]))
    # An OSC mapping with no type of its own reads as text.
    assert driver.get_state("level") == "1"
    assert driver.get_state("label") is None
    assert driver._parsed_messages == 2
    driver._drop_all_held()


@pytest.mark.parametrize("value_type", ["number", "float", "integer"])
async def test_a_number_that_does_not_read_is_told_to_an_observer(value_type):
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LV(\S+)",
        "mappings": [{"group": 1, "state": "label", "type": value_type}],
    }]))
    await driver.on_data_received(b"LVloud")
    assert ("coercion_failure", {"raw": "loud", "type": value_type, "stored": "loud"}) in seen


async def test_a_value_that_is_not_flat_is_told_as_its_repr():
    driver, seen = _driver(_definition("osc", [{"address": "/acme/blob", "mappings": [{"arg": 0, "state": "level", "type": "integer"}]}]))
    await driver.on_data_received(osc_encode_message("/acme/blob", [("b", b"\x01\x02")]))
    failures = [detail for kind, detail in seen if kind == "coercion_failure"]
    assert failures == [{"raw": repr(b"\x01\x02"), "type": "integer", "stored": repr(b"\x01\x02")}]


# --- the edges a deliberate break found unwatched --------------------------


async def test_an_optional_capture_that_did_not_take_part_keeps_the_last_value():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"ST(\w+)(?: M(\w+))?",
        "mappings": [{"group": 2, "state": "mode"}, {"group": 1, "state": "power"}],
    }]))
    await driver.on_data_received(b"STON Mlive")
    await driver.on_data_received(b"STOFF")
    assert driver.get_state("mode") == "live"
    assert driver.get_state("power") == "OFF"


async def test_an_absent_child_id_capture_is_not_looked_up_in_the_id_map():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"CH(\d+)(?:@(\w+))?",
        "child_set": [{"type": "channel", "id": {"group": 2, "map": {"None": 1}},
                       "state": {"level": "$1"}}],
    }]))
    await driver.on_data_received(b"CH5")
    assert driver.get_child_state("channel", 1).get("level") is None


async def test_a_numeric_wire_id_outside_the_id_map_is_skipped():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"CH(\w+) (\d+)",
        "child_set": [{"type": "channel", "id": {"group": 1, "map": {"A": 1}},
                       "state": {"level": "$2"}}],
    }]))
    await driver.on_data_received(b"CH3 44")
    assert driver.get_child_state("channel", 3).get("level") is None


async def test_a_child_entry_that_read_nothing_routes_no_follow_up():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"CH(\d+)",
        "child_set": [{"type": "channel", "id": "$1", "state": {"level": "$4"}}],
        "then_query": [{"send": "NAME? {child_id}\r", "query_for": "name"}],
    }]))
    routed: list[Any] = []
    driver._follow_up = lambda *args: routed.append(args)
    await driver.on_data_received(b"CH2")
    assert len(routed) == 1
    assert routed[0][3] == []


async def test_child_list_items_are_trimmed_before_they_are_matched_whole():
    driver, _ = _driver(_definition("tcp", [{
        "match": r"LST (.*)",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(\d+)=(\d+)", "id": "$1", "state": {"level": "$2"}},
    }]))
    await driver.on_data_received(b"LST 1=10, 2=20 ,3=30")
    assert [driver.get_child_state("channel", n).get("level") for n in range(1, 4)] == [10, 20, 30]


async def test_child_list_empty_matches_are_not_places_and_an_empty_list_writes_nothing():
    driver, _ = _driver(_definition("tcp", [
        {"match": r"RUN (.*)",
         "child_list": {"type": "channel", "group": 1, "element": r"\d*", "state": {"level": "$0"}}},
        {"match": r"SEP(.*)",
         "child_list": {"type": "channel", "group": 1, "separator": ",",
                        "element": r"(\d*)", "state": {"name": "$1"}}},
    ]))
    await driver.on_data_received(b"RUN 11 12")
    assert [driver.get_child_state("channel", n).get("level") for n in range(1, 5)] == [11, 12, None, None]
    await driver.on_data_received(b"SEP")
    assert [driver.get_child_state("channel", n).get("name") for n in range(1, 5)] == [None] * 4


async def test_an_unread_line_is_remembered_to_eighty_characters():
    driver, _ = _driver(_definition("tcp", [{"match": r"^PWR(\w+)$", "set": {"power": "$1"}}]))
    line = "X" * 100
    await driver.on_data_received(line.encode())
    assert driver._last_unread == "X" * 80


async def test_an_unread_osc_address_is_remembered_to_eighty_characters():
    driver, _ = _driver(_definition("osc", [{"address": "/acme/level", "mappings": [{"arg": 0, "state": "level"}]}]))
    address = "/" + "a" * 99
    await driver.on_data_received(osc_encode_message(address, [("i", 1)]))
    assert driver._last_unread == address[:80]


async def test_an_osc_mapping_naming_the_arg_one_past_the_end_is_skipped():
    driver, _ = _driver(_definition("osc", [{
        "address": "/acme/st",
        "mappings": [{"arg": 1, "state": "mode"}, {"arg": 0, "state": "power"}],
    }]))
    await driver.on_data_received(osc_encode_message("/acme/st", [("s", "on")]))
    assert driver.get_state("mode") is None
    assert driver.get_state("power") == "on"


async def test_an_osc_child_prop_with_a_negative_arg_is_skipped():
    driver, _ = _driver(_definition("osc", [{
        "address": "/ch/*/fader",
        "child_set": [{"type": "channel", "id": {"segment": 1},
                       "state": {"level": {"arg": -1}, "name": {"arg": 0}}}],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/2/fader", [("s", "Vox")]))
    assert driver.get_child_state("channel", 2).get("level") is None
    assert driver.get_child_state("channel", 2).get("name") == "Vox"


async def test_a_held_osc_child_rule_throttles_each_address_on_its_own():
    driver, _ = _driver(_definition("osc", [{
        "address": "/ch/*/fader", "throttle": 60,
        "child_set": [{"type": "channel", "id": {"segment": 1}, "state": {"level": {"arg": 0}}}],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/1/fader", [("i", 10)]))
    await driver.on_data_received(osc_encode_message("/ch/2/fader", [("i", 20)]))
    await driver.on_data_received(osc_encode_message("/ch/1/fader", [("i", 11)]))
    assert [driver.get_child_state("channel", n).get("level") for n in (1, 2)] == [10, 20]
    driver._drop_all_held()


async def test_an_osc_json_path_value_is_converted_as_the_device_sent_it():
    driver, seen = _driver(_definition("osc", [{
        "address": "/reply/*",
        "mappings": [
            {"arg": 0, "state": "level", "json_path": "n", "type": "integer"},
            {"arg": 0, "state": "label", "json_path": "on", "type": "string"},
        ],
    }]))
    await driver.on_data_received(osc_encode_message("/reply/x", [("s", '{"n": 1.5, "on": true}')]))
    readings = (driver.get_state("level"), driver.get_state("label"))
    failures = [detail for kind, detail in seen if kind == "coercion_failure"]
    assert (readings, failures) == ((1, "True"), [])


def test_a_json_body_that_is_a_list_is_never_read_by_key_or_index():
    driver, _ = _driver(_definition("tcp", [{"json": True, "set": {"mode": "0.mode"}}]))
    assert driver._apply_json_responses('[{"mode": "a"}, {"mode": "b"}]') is False
    assert driver.get_state("mode") is None


async def test_an_unread_osc_message_tells_args_that_are_not_flat_as_their_repr():
    driver, seen = _driver(_definition("osc", [{"address": "/acme/level", "mappings": [{"arg": 0, "state": "level"}]}]))
    await driver.on_data_received(osc_encode_message("/other", [("b", b"\x07"), ("i", 2)]))
    assert seen == [("unmatched_response", {"address": "/other", "args": [repr(b"\x07"), 2]})]


async def test_an_osc_child_id_at_a_negative_segment_is_skipped():
    driver, _ = _driver(_definition("osc", [{
        "address": "/ch/*/fader",
        "child_set": [{"type": "channel", "id": {"segment": -1, "map": {"fader": 2}},
                       "state": {"level": {"arg": 0}}}],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/1/fader", [("i", 9)]))
    assert driver.get_child_state("channel", 2).get("level") is None


def test_a_json_mapping_with_an_empty_key_reads_nothing():
    driver, _ = _driver(_definition("tcp", [{"json": True, "set": {"mode": ""}}]))
    assert driver._apply_json_responses('{"a": 1, "b": 2}') is False
    assert driver.get_state("mode") is None


def test_a_json_child_prop_value_map_names_the_value_as_text():
    driver, _ = _driver(_definition("tcp", [{
        "json": True,
        "child_set": [{"type": "channel", "id": 1,
                       "state": {"name": {"key": "on", "map": {"1": "Live", "True": "Yes"}}}}],
    }]))
    assert driver._apply_json_responses('{"on": 1}') is True
    assert driver.get_child_state("channel", 1).get("name") == "Live"
    assert driver._apply_json_responses('{"on": true}') is True
    assert driver.get_child_state("channel", 1).get("name") == "Yes"


def test_a_json_value_that_does_not_convert_is_told_to_an_observer():
    driver, seen = _driver(_definition("tcp", [
        {"json": True, "set": {"level": {"key": "level", "type": "integer"}}},
    ]))
    driver._apply_json_responses('{"level": "loud"}')
    assert ("coercion_failure", {"raw": "loud", "type": "integer", "stored": "loud"}) in seen


async def test_an_osc_command_substitutes_config_and_params_into_address_and_args():
    from unittest.mock import AsyncMock, MagicMock

    from openavc.transport.osc import OSCTransport
    from openavc.transport.osc_codec import osc_decode_bundle

    definition = _definition("osc", [], commands={
        "go": {"address": "/ws/{workspace}/cue/{cue}", "args": [{"type": "s", "value": "{workspace}"}]},
    })
    definition["config_schema"]["workspace"] = {"type": "string", "label": "Workspace"}
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 5000, "workspace": "W1"}, state, events)
    transport = MagicMock(spec=OSCTransport)
    transport.send = AsyncMock()
    driver.transport = transport
    assert await driver._send_osc_command("go", definition["commands"]["go"], {"cue": "7"}) is True
    assert osc_decode_bundle(transport.send.call_args.args[0]) == [("/ws/W1/cue/7", [("s", "W1")])]
