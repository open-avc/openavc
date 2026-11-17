"""Where a reply's child value goes: one placement result, read by every door.

A YAML reply rule that writes a child (``child_set:``, ``child_list:``) first
has to find the child its reply names. Every form (regex, OSC, JSON) asks one
function, and its answer is one of three:

* **placed**: a registered child, written;
* **unregistered**: an id the roster lacks. Told to a contract observer only
  when the device named it; a place a by-place page covers that the roster
  skips is a gap, and a wire id an entry's id map leaves to another entry is
  not that entry's, and neither is a fault;
* **unplaceable**, with a reason: the reply carries the child's id, or its
  page's start, in a form that cannot be read. Told as ``child_unplaced``.

A reply that does not carry an entry's id at all (an optional capture that
did not take part) is not about that entry, and nothing is told: one rule
may serve replies of several widths.

The poll loop's unread-replies count, a device audit and the Driver Builder's
live test read the same result. On an invented Acme device.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.osc_codec import osc_encode_message


def _channels(instances: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": {
            "label": "Channel",
            "id_format": {"type": "integer", "min": 1, "max": 64},
            "state_variables": {
                "level": {"type": "integer", "label": "Level"},
                "name": {"type": "string", "label": "Name"},
            },
            "instances": instances,
        },
    }


def _definition(
    transport: str,
    responses: list[dict[str, Any]],
    instances: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "manufacturer": "Acme",
        "category": "switcher",
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
        },
        "child_entity_types": _channels(instances or {"count": 4, "label": "Channel {id}"}),
        "commands": {},
        "responses": responses,
    }


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


def _levels(driver: Any, ids: range | list[int]) -> list[Any]:
    return [driver.get_child_state("channel", n).get("level") for n in ids]


# --- a by-place page over a roster with gaps --------------------------------


async def test_a_page_over_a_sparse_roster_writes_its_children_and_tells_no_gap():
    """A page of 16 from channel 1 over a roster of 1, 2, 17 and 18 covers 14
    places the roster skips on purpose. Those are not channels the device
    named, so nothing is told; the two it has are written."""
    driver, seen = _driver(_definition("tcp", [{
        "match": r"PG(\d+) (.*)",
        "child_list": {"type": "channel", "group": 2, "start": "$1",
                       "element": r"\d+", "separator": " ", "state": {"level": "$0"}},
    }], instances={"ids": [1, 2, 17, 18], "label": "Channel {id}"}))
    values = " ".join(str(10 + n) for n in range(16))
    await driver.on_data_received(f"PG1 {values}".encode())
    assert _levels(driver, [1, 2, 17, 18]) == [10, 11, None, None]
    assert seen == []
    assert driver._parsed_messages == 1


async def test_a_by_id_list_naming_a_channel_the_roster_lacks_is_told():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LST (.*)",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(\d+)=(\d+)", "id": "$1", "state": {"level": "$2"}},
    }]))
    await driver.on_data_received(b"LST 1=10,9=90")
    assert _levels(driver, [1]) == [10]
    assert seen == [("child_unregistered", {"child_type": "channel", "local_id": 9})]
    assert driver._parsed_messages == 1


# --- a reply that cannot be placed ------------------------------------------


async def test_a_page_whose_start_does_not_read_is_told_and_counted_unread():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LST(\S+) (.*)",
        "child_list": {"type": "channel", "group": 2, "start": "$1",
                       "element": r"\d+", "separator": " ", "state": {"level": "$0"}},
    }]))
    await driver.on_data_received(b"LST0x11 05 06")
    assert _levels(driver, range(1, 5)) == [None] * 4
    assert seen == [("child_unplaced", {"child_type": "channel", "reason": "no_start", "id": "0x11"})]
    assert driver._parsed_messages == 0
    assert driver._last_unread == "LST0x11 05 06"


async def test_a_list_capture_that_did_not_take_part_is_an_empty_list():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LST(?: (.*))?$",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(\d+)", "state": {"level": "$1"}},
    }]))
    await driver.on_data_received(b"LST")
    assert seen == []
    assert driver._parsed_messages == 1


async def test_an_empty_list_is_read_and_tells_nothing():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LST:(.*)$",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(\d+)", "state": {"level": "$1"}},
    }]))
    await driver.on_data_received(b"LST:")
    assert seen == []
    assert driver._parsed_messages == 1


async def test_a_by_id_item_with_no_id_of_its_own_is_passed_over_quietly():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LST (.*)",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(?:(\d+)=)?(\d+|--)", "id": "$1", "state": {"level": "$2"}},
    }]))
    await driver.on_data_received(b"LST 1=10, 20 ,3=30,--,x")
    assert _levels(driver, range(1, 5)) == [10, None, 30, None]
    assert seen == []
    assert driver._parsed_messages == 1


async def test_a_by_id_item_whose_id_does_not_read_is_told():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LST (.*)",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(\w+)=(\d+)", "id": "$1", "state": {"level": "$2"}},
    }]))
    await driver.on_data_received(b"LST 1=10,0x2=20")
    assert _levels(driver, [1]) == [10]
    assert seen == [("child_unplaced", {"child_type": "channel", "reason": "bad_id", "id": "0x2"})]
    assert driver._parsed_messages == 1


async def test_one_rule_for_every_width_tells_nothing_of_the_pairs_a_reply_leaves_out():
    """Optional id captures that did not take part: the reply names fewer
    children than the rule can read, which is how the rule is written."""
    driver, seen = _driver(_definition("tcp", [{
        "match": r"R (\d) (\d+)(?: (\d) (\d+))?(?: (\d) (\d+))?$",
        "child_set": [
            {"type": "channel", "id": "$1", "state": {"level": "$2"}},
            {"type": "channel", "id": "$3", "state": {"level": "$4"}},
            {"type": "channel", "id": "$5", "state": {"level": "$6"}},
        ],
    }]))
    await driver.on_data_received(b"R 1 10")
    await driver.on_data_received(b"R 2 20 3 30")
    assert _levels(driver, range(1, 4)) == [10, 20, 30]
    assert seen == []
    assert driver._parsed_messages == 2


async def test_a_by_place_item_that_does_not_match_holds_its_place_quietly():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"LST (.*)",
        "child_list": {"type": "channel", "group": 1, "separator": ",",
                       "element": r"(\d+)", "state": {"level": "$1"}},
    }]))
    await driver.on_data_received(b"LST 10,--,30")
    assert _levels(driver, range(1, 4)) == [10, None, 30]
    assert seen == []


@pytest.mark.parametrize(
    ("rule", "line", "detail"),
    [
        # A capture the reply carries in a form the ids do not take.
        ({"match": r"C(\w+) (\d+)",
          "child_set": [{"type": "channel", "id": "$1", "state": {"level": "$2"}}]},
         b"C0x11 5", {"child_type": "channel", "reason": "bad_id", "id": "0x11"}),
        # A literal that is not an integer id.
        ({"match": r"D(\d+)",
          "child_set": [{"type": "channel", "id": "abc", "state": {"level": "$1"}}]},
         b"D5", {"child_type": "channel", "reason": "bad_id", "id": "abc"}),
    ],
)
async def test_a_child_set_that_cannot_be_placed_is_told_and_counted_unread(rule, line, detail):
    driver, seen = _driver(_definition("tcp", [rule]))
    await driver.on_data_received(line)
    assert seen == [("child_unplaced", detail)]
    assert driver._parsed_messages == 0
    assert driver._last_unread == line.decode()


async def test_an_unplaceable_child_beside_a_flat_value_still_reads_the_reply():
    driver, seen = _driver(_definition("tcp", [{
        "match": r"M(\w+)@(\w+)",
        "set": {"mode": "$1"},
        "child_set": [{"type": "channel", "id": "$2", "state": {"name": "$1"}}],
    }]))
    await driver.on_data_received(b"Mlive@x1")
    assert driver.get_state("mode") == "live"
    assert seen == [("child_unplaced", {"child_type": "channel", "reason": "bad_id", "id": "x1"})]
    assert driver._parsed_messages == 1


async def test_a_wire_id_an_id_map_leaves_to_another_entry_is_not_told():
    """Two entries on one rule, each mapping its own wire ids: on every reply
    one of them finds its id outside its map, which is how the rule is written."""
    definition = _definition("tcp", [{
        "match": r"REP (\d+) GAIN (\d+)",
        "child_set": [
            {"type": "channel", "id": {"group": 1, "map": {"21": 1, "22": 2}}, "state": {"level": "$2"}},
            {"type": "channel", "id": {"group": 1, "map": {"41": 3}}, "state": {"level": "$2"}},
        ],
    }])
    driver, seen = _driver(definition)
    await driver.on_data_received(b"REP 22 GAIN 7")
    await driver.on_data_received(b"REP 41 GAIN 9")
    await driver.on_data_received(b"REP 99 GAIN 1")
    assert _levels(driver, [2, 3]) == [7, 9]
    assert seen == []
    assert driver._parsed_messages == 3


async def test_a_device_with_no_children_set_up_still_reads_every_reply():
    """A roster of none: every channel the device names is unregistered, which
    is read, so the unread-replies check never says the driver reads none."""
    driver, seen = _driver(_definition("tcp", [{
        "match": r"CH(\d+) (\d+)",
        "child_set": [{"type": "channel", "id": "$1", "state": {"level": "$2"}}],
    }], instances={"ids": [], "label": "Channel {id}"}))
    for n in range(1, 4):
        await driver.on_data_received(f"CH{n} 5".encode())
    assert driver._parsed_messages == 3
    assert [kind for kind, _ in seen] == ["child_unregistered"] * 3


# --- OSC --------------------------------------------------------------------


async def test_an_osc_id_that_does_not_read_is_told_and_counted_unread():
    driver, seen = _driver(_definition("osc", [{
        "address": "/ch/*/fader",
        "child_set": [{"type": "channel", "id": {"segment": 1}, "state": {"level": {"arg": 0}}}],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/main/fader", [("i", 42)]))
    assert seen == [("child_unplaced", {"child_type": "channel", "reason": "bad_id", "id": "main"})]
    assert driver._parsed_messages == 0
    assert driver._last_unread == "/ch/main/fader"


async def test_an_osc_address_shorter_than_the_segment_tells_nothing():
    driver, seen = _driver(_definition("osc", [{
        "address": "/ch/*",
        "child_set": [{"type": "channel", "id": {"segment": 4}, "state": {"level": {"arg": 0}}}],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/03", [("i", 42)]))
    assert seen == []
    assert driver._parsed_messages == 1


async def test_an_osc_id_outside_the_map_is_not_told():
    driver, seen = _driver(_definition("osc", [{
        "address": "/ch/*/fader",
        "child_set": [{"type": "channel", "id": {"segment": 1, "map": {"03": 3}},
                       "state": {"level": {"arg": 0}}}],
    }]))
    await driver.on_data_received(osc_encode_message("/ch/09/fader", [("i", 1)]))
    assert seen == []
    assert driver._parsed_messages == 1


# --- JSON -------------------------------------------------------------------


async def test_a_json_body_naming_a_child_the_roster_lacks_is_read():
    """As a text reply naming one is: the json rule read it, so the regex
    rules are not offered it and it is not unmatched."""
    driver, seen = _driver(_definition("tcp", [{
        "json": True,
        "child_set": [{"type": "channel", "id": 6, "state": {"level": "lvl6"}}],
    }]))
    await driver.on_data_received(b'{"lvl6": 1}')
    assert seen == [("child_unregistered", {"child_type": "channel", "local_id": 6})]
    assert driver._parsed_messages == 1


async def test_a_json_literal_that_is_no_child_id_is_told_when_the_body_carries_its_value():
    driver, seen = _driver(_definition("tcp", [{
        "json": True,
        "child_set": [
            {"type": "channel", "id": "abc", "state": {"level": "lvl"}},
            {"type": "channel", "id": 2, "state": {"level": "lvl"}},
        ],
    }]))
    await driver.on_data_received(b'{"lvl": 5}')
    assert _levels(driver, [2]) == [5]
    assert seen == [("child_unplaced", {"child_type": "channel", "reason": "bad_id", "id": "abc"})]
    assert driver._parsed_messages == 1
    # A body with nothing at the entry's paths says nothing about the child.
    await driver.on_data_received(b'{"other": 5}')
    assert [kind for kind, _ in seen].count("child_unplaced") == 1


# --- what a device audit and the Builder's live test say ---------------------


def test_the_audit_words_each_placement_event_with_its_child():
    from openavc.audit.observe import CONTRACT_TEXT, contract_example

    assert contract_example({"child_type": "output", "local_id": 9}) == "output 9"
    assert contract_example(
        {"child_type": "output", "reason": "no_start", "id": "0x11"}
    ) == 'output: the reply does not say which output its list starts at ("0x11")'
    assert contract_example(
        {"child_type": "output", "reason": "bad_id", "id": "x1"}
    ) == 'output: the reply\'s output id is not in a form this driver reads ("x1")'
    assert "child_unplaced" in CONTRACT_TEXT


def test_the_audit_wizard_reads_its_labels_from_the_server():
    """One wording table: the live view sends each kind's words beside its
    count, and the wizard keeps no list of its own."""
    from pathlib import Path

    from openavc.audit.observe import CONTRACT_TEXT, contract_labels

    assert contract_labels({"child_unplaced": 2}) == {"child_unplaced": CONTRACT_TEXT["child_unplaced"]}
    step = Path(__file__).parents[1] / (
        "openavc/web/programmer/src/views/devices/audit/steps/ListenStep.tsx"
    )
    text = step.read_text(encoding="utf-8")
    assert "CONTRACT_LABELS" not in text
    assert "contract.labels" in text


async def _fake_device(reply: bytes) -> tuple[Any, int]:
    async def handle(reader: Any, writer: Any) -> None:
        await reader.read(100)
        writer.write(reply)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _live_test(definition: dict[str, Any], reply: bytes) -> dict[str, Any]:
    from openavc.api.models import TestCommandRequest as CommandRequest
    from openavc.api.routes.driver_test import _test_via_configurable_driver

    definition["commands"] = {"query": {"label": "Query", "send": "Q\\r"}}
    definition["default_config"] = {"host": "127.0.0.1", "port": 0, "poll_interval": 0}
    server, port = await _fake_device(reply)
    try:
        return await _test_via_configurable_driver(CommandRequest(
            host="127.0.0.1", port=str(port), transport="tcp",
            definition=definition, command_name="query", timeout=2,
        ))
    finally:
        server.close()
        await server.wait_closed()


async def test_the_live_test_says_a_reply_it_could_not_place():
    result = await _live_test(_definition("tcp", [{
        "match": r"LST(\S+) (.*)",
        "child_list": {"type": "channel", "group": 2, "start": "$1",
                       "element": r"\d+", "separator": " ", "state": {"level": "$0"}},
    }]), b"LST0x11 05 06\r\n")
    assert result["success"] is False
    assert result["contract_errors"] == [
        "A reply's status could not be matched to a channel or zone: channel: the "
        'reply does not say which channel its list starts at ("0x11").'
    ]


async def test_the_live_test_does_not_fail_a_channel_beyond_its_roster():
    """A device answering for more channels than the test's roster holds is
    doing nothing wrong, and neither is the driver."""
    definition = _definition("tcp", [{
        "match": r"CH(\d+) (\d+)",
        "child_set": [{"type": "channel", "id": "$1", "state": {"level": "$2"}}],
    }])
    result = await _live_test(definition, b"CH9 5\r\n")
    assert result["contract_errors"] == []
    assert result["success"] is True


async def test_a_json_body_for_a_child_the_roster_lacks_does_not_stamp_the_throttle():
    """It was read, but wrote nothing, so a body for a child the roster has
    right after it is written at once, not held for a window it never opened."""
    driver, _ = _driver(_definition("tcp", [{
        "json": True, "throttle": 5,
        "child_set": [
            {"type": "channel", "id": 6, "state": {"level": "lvl6"}},
            {"type": "channel", "id": 2, "state": {"level": "lvl2"}},
        ],
    }]))
    await driver.on_data_received(b'{"lvl6": 1}')
    await driver.on_data_received(b'{"lvl2": 4}')
    assert _levels(driver, [2]) == [4]
