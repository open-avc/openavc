"""A YAML response rule that spreads a list across children (``child_list:``),
and the paged per-child query that asks for one (``page_size:``).

A matrix answers every output's tie, or every output's mute, in one line.
``child_list`` reads such a line into one child per item: by the item's place
in the list (from ``start``, which a page-at-a-time reply can carry in a
capture), or by an id the item carries. Runtime, validator and the
poll-length count, on an invented Acme matrix.
"""

from __future__ import annotations

import copy
from typing import Any

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_warnings
from openavc.drivers.child_ids import page_starts
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition


def _matrix(responses: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "manufacturer": "Acme",
        "category": "switcher",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 23, "output_count": 4},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 23, "label": "Port"},
            "output_count": {"type": "integer", "default": 4, "label": "Outputs"},
        },
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "child_entity_types": {
            "output": {
                "label": "Output",
                "id_format": {"type": "integer", "min": 1, "max": 64},
                "state_variables": {
                    "input": {"type": "integer", "label": "Routed Input"},
                    "video_mute": {"type": "boolean", "label": "Video Muted"},
                    "audio_mute": {"type": "boolean", "label": "Audio Muted"},
                },
                "instances": {"count_from": "output_count", "label": "Output {id}"},
            },
        },
        "commands": {"noop": {"label": "No-op", "send": "NOOP\\r"}},
        "responses": responses,
        "polling": {"queries": ["PWR?\\r\\n"]},
    }
    definition.update(over)
    return definition


def _driver(definition: dict[str, Any], outputs: int = 4):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls(
        "dev1",
        {"host": "127.0.0.1", "port": 23, "output_count": outputs},
        state,
        events,
    )
    driver._register_declared_children()
    return driver


def _inputs(driver, count: int) -> list[Any]:
    return [
        driver.get_child_state("output", n).get("input") for n in range(1, count + 1)
    ]


_BY_PLACE = {
    "match": r"^Ties (.+)$",
    "child_list": {
        "type": "output",
        "element": r"(\d+)",
        "separator": ",",
        "state": {"input": "$1"},
    },
}

_BY_ID = {
    "match": r"^~\d+@VID (.+)$",
    "child_list": {
        "type": "output",
        "element": r"(\d+)>(\d+)",
        "separator": ",",
        "id": "$2",
        "state": {"input": "$1"},
    },
}

_MUTES = {
    "match": r"^Mut(\d+)$",
    "child_list": {
        "type": "output",
        "element": r"(\d)",
        "state": {
            "video_mute": {"group": 1, "map": {"0": False, "1": True, "2": False, "3": True}},
            "audio_mute": {"group": 1, "map": {"0": False, "1": False, "2": True, "3": True}},
        },
    },
}

_PAGED = {
    "match": r"^Vgp\d+\*Out(\d+) ((?:(?:\d\d|--) ?)+) ?Vid$",
    "child_list": {
        "type": "output",
        "group": 2,
        "element": r"(\d\d)|--",
        "start": "$1",
        "state": {"input": "$1"},
    },
}


# ---------------------------------------------------------------------------
# Reading a list
# ---------------------------------------------------------------------------


async def test_items_are_children_by_their_place_in_the_list():
    driver = _driver(_matrix([_BY_PLACE]))
    await driver.on_data_received(b"Ties 3,1,2,4")
    assert _inputs(driver, 4) == [3, 1, 2, 4]


async def test_an_item_that_does_not_match_still_holds_its_place():
    driver = _driver(_matrix([_BY_PLACE]))
    await driver.on_data_received(b"Ties 3,1,2,4")
    await driver.on_data_received(b"Ties 5, --,7")
    assert _inputs(driver, 4) == [5, 1, 7, 4]


async def test_items_name_their_own_child_by_id():
    driver = _driver(_matrix([_BY_ID]))
    await driver.on_data_received(b"~01@VID 3>2,5>4")
    assert _inputs(driver, 4) == [None, 3, None, 5]


async def test_a_one_item_list_is_read_by_the_same_rule():
    driver = _driver(_matrix([_BY_ID]))
    await driver.on_data_received(b"~01@VID 6>3")
    assert _inputs(driver, 4) == [None, None, 6, None]


async def test_items_written_side_by_side_are_found_one_after_another():
    driver = _driver(_matrix([_MUTES]))
    await driver.on_data_received(b"Mut0123")
    reads = [
        (driver.get_child_state("output", n).get("video_mute"),
         driver.get_child_state("output", n).get("audio_mute"))
        for n in range(1, 5)
    ]
    assert reads == [(False, False), (True, False), (False, True), (True, True)]


async def test_a_page_starts_where_the_reply_says():
    driver = _driver(_matrix([_PAGED]), outputs=20)
    await driver.on_data_received(b"Vgp00*Out17 05 06 -- 08 Vid")
    assert _inputs(driver, 20)[16:] == [5, 6, None, 8]
    assert _inputs(driver, 16) == [None] * 16


async def test_a_reply_that_does_not_say_where_it_starts_writes_nothing():
    rule = copy.deepcopy(_PAGED)
    rule["match"] = r"^(?:Vgp\d+\*Out(\d+) )?((?:(?:\d\d|--) ?)+) ?Vid$"
    driver = _driver(_matrix([rule]), outputs=20)
    await driver.on_data_received(b"05 06 07 Vid")
    assert _inputs(driver, 20) == [None] * 20


async def test_an_id_the_roster_does_not_hold_is_skipped():
    driver = _driver(_matrix([_BY_PLACE]), outputs=2)
    await driver.on_data_received(b"Ties 3,1,2,4")
    assert _inputs(driver, 2) == [3, 1]
    assert not driver.is_child_registered("output", 3)


async def test_set_beside_child_list_is_written_too():
    rule = {
        "match": r"^Pwr(\d) Ties (.+)$",
        "set": {"power": "$1"},
        "child_list": {**_BY_PLACE["child_list"], "group": 2},
    }
    driver = _driver(_matrix([rule]))
    await driver.on_data_received(b"Pwr1 Ties 4,3")
    assert driver.get_state("power") is True
    assert _inputs(driver, 2) == [4, 3]


async def test_a_list_reply_counts_as_read():
    driver = _driver(_matrix([_BY_PLACE]))
    before = driver._parsed_count()
    await driver.on_data_received(b"Ties 1,2")
    assert driver._parsed_count() == before + 1


async def test_a_throttled_list_keeps_one_window_per_page():
    rule = {**copy.deepcopy(_PAGED), "throttle": 60}
    driver = _driver(_matrix([rule]), outputs=20)
    await driver.on_data_received(b"Vgp00*Out01 01 02 Vid")
    await driver.on_data_received(b"Vgp00*Out17 07 08 Vid")
    assert _inputs(driver, 2) == [1, 2]
    assert _inputs(driver, 18)[16:] == [7, 8]
    # A second reply for the first page inside its window is held, not written.
    await driver.on_data_received(b"Vgp00*Out01 09 09 Vid")
    assert _inputs(driver, 2) == [1, 2]
    driver._drop_all_held()


async def test_the_value_conversion_applies_to_each_item():
    definition = _matrix([_BY_PLACE])
    definition["child_entity_types"]["output"]["state_variables"]["input"]["offset"] = 1
    driver = _driver(definition)
    await driver.on_data_received(b"Ties 0,1")
    assert _inputs(driver, 2) == [1, 2]


# ---------------------------------------------------------------------------
# Asking for a list a page at a time
# ---------------------------------------------------------------------------


def test_page_starts_follow_id_ranges():
    assert page_starts(list(range(1, 33)), 16) == [1, 17]
    assert page_starts(list(range(1, 9)), 16) == [1]
    assert page_starts([1, 2, 5, 20, 21, 40], 16) == [1, 20, 40]
    assert page_starts(["a", "b", "c"], 2) == ["a", "c"]
    assert page_starts([3, 1, 2], None) == [3, 1, 2]


async def test_a_paged_query_goes_out_once_per_page():
    definition = _matrix(
        [_PAGED],
        polling={"queries": [{
            "each_child": "output", "page_size": 16,
            "send": "VC{child_id}\\r", "query_for": "input",
        }]},
    )
    driver = _driver(definition, outputs=40)

    class Recorder:
        connected = True

        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send(self, data: bytes) -> None:
            self.sent.append(data)

    driver.transport = Recorder()
    driver._pace_lines = False
    await driver.poll()
    assert driver.transport.sent == [b"VC1\r", b"VC17\r", b"VC33\r"]


def test_the_poll_length_counts_pages():
    definition = _matrix(
        [_PAGED],
        default_config={
            "host": "", "port": 23, "output_count": 4,
            "poll_interval": 1, "inter_command_delay": 0.1,
        },
        polling={"queries": [{
            "each_child": "output", "page_size": 16,
            "send": "VC{child_id}\\r", "query_for": "input",
        }]},
    )
    # 64 outputs is four pages: 0.4 s, inside the 1 s interval.
    assert not [w for w in validate_driver_warnings(definition) if "poll sends" in w]
    per_child = copy.deepcopy(definition)
    del per_child["polling"]["queries"][0]["page_size"]
    assert [w for w in validate_driver_warnings(per_child) if "poll sends" in w]


# ---------------------------------------------------------------------------
# What the validator refuses
# ---------------------------------------------------------------------------


def _errors(definition: dict[str, Any]) -> list[str]:
    return validate_driver_definition(definition, strict=True)


def test_a_valid_list_rule_and_list_read_pass():
    definition = _matrix(
        [_BY_PLACE, _MUTES],
        polling={"queries": [
            {"send": "TIES?\\r\\n", "query_for": "output.input"},
            {"send": "MUT?\\r\\n", "query_for": "output.video_mute"},
        ]},
    )
    assert _errors(definition) == []


def test_child_set_and_child_list_on_one_rule_are_refused():
    rule = {
        **_BY_ID,
        "child_set": [{"type": "output", "id": 1, "state": {"input": 1}}],
    }
    assert any("both child_set and child_list" in e for e in _errors(_matrix([rule])))


def test_child_list_on_a_json_or_osc_rule_is_refused():
    json_rule = {"json": True, "set": {"power": "power"}, "child_list": _BY_PLACE["child_list"]}
    assert any("text rules" in e for e in _errors(_matrix([json_rule])))


def test_references_are_counted_against_the_right_pattern():
    rule = copy.deepcopy(_BY_ID)
    rule["child_list"]["id"] = "$3"  # the item has two captures
    rule["child_list"]["group"] = "$2"  # the rule has one
    errors = _errors(_matrix([rule]))
    assert any("id $3 exceeds the 2 group(s) of the element" in e for e in errors)
    assert any("group $2 exceeds the 1 group(s) of the rule's match" in e for e in errors)


def test_an_element_that_matches_nothing_at_all_is_refused():
    rule = copy.deepcopy(_BY_PLACE)
    rule["child_list"]["element"] = r"(\d*)"
    assert any("matches empty text" in e for e in _errors(_matrix([rule])))


def test_start_beside_id_is_refused():
    rule = copy.deepcopy(_BY_ID)
    rule["child_list"]["start"] = 1
    assert any("start counts places" in e for e in _errors(_matrix([rule])))


def test_a_bare_child_variable_on_a_query_sent_once_is_refused():
    definition = _matrix(
        [_BY_PLACE],
        polling={"queries": [{"send": "TIES?\\r\\n", "query_for": "input"}]},
    )
    assert any("output.input" in e for e in _errors(definition))


def test_a_dotted_query_for_needs_a_list_rule_that_fills_it():
    definition = _matrix(
        [_BY_PLACE],
        polling={"queries": [{"send": "MUT?\\r\\n", "query_for": "output.audio_mute"}]},
    )
    assert any("no child_list rule fills it" in e for e in _errors(definition))


def test_page_size_belongs_to_a_per_child_query():
    definition = _matrix(
        [_PAGED],
        polling={"queries": [{"send": "VC1\\r", "page_size": 16, "query_for": "power"}]},
    )
    assert any("page_size applies to a per-child query" in e for e in _errors(definition))
    definition["polling"]["queries"] = [{
        "each_child": "output", "page_size": 1, "send": "VC{child_id}\\r",
    }]
    assert any("2 or more" in e for e in _errors(definition))



# ---------------------------------------------------------------------------
# The auto-simulator answers a list read from every child
# ---------------------------------------------------------------------------


def _sim(definition: dict[str, Any], outputs: int):
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    return YAMLAutoSimulator(
        "sim1", {"output_count": outputs}, driver_def=copy.deepcopy(definition)
    )


async def _round_trip(definition: dict[str, Any], outputs: int, query: bytes,
                      seed: dict[str, Any]) -> Any:
    sim = _sim(definition, outputs)
    for key, value in seed.items():
        sim.set_state(key, value)
    reply = sim.handle_command(query)
    assert reply, f"the simulator did not answer {query!r}"
    driver = _driver(definition, outputs)
    for line in reply.decode().splitlines():
        await driver.on_data_received(line.encode())
    return driver, reply


async def test_a_list_read_sent_once_is_answered_with_every_child():
    definition = _matrix(
        [_BY_ID],
        polling={"queries": [{"send": "#VID? *\r", "query_for": "output.input"}]},
    )
    driver, reply = await _round_trip(
        definition, 3, b"#VID? *\r",
        {"output.1.input": 4, "output.2.input": 2, "output.3.input": 7},
    )
    assert reply.startswith(b"~0@VID 4>1,2>2,7>3")
    assert _inputs(driver, 3) == [4, 2, 7]


async def test_a_list_by_place_with_a_value_map_round_trips():
    definition = _matrix(
        [_MUTES],
        polling={"queries": [{"send": "MUT?\r", "query_for": "output.audio_mute"}]},
    )
    driver, _ = await _round_trip(
        definition, 4, b"MUT?\r",
        {f"output.{n}.audio_mute": n % 2 == 0 for n in range(1, 5)},
    )
    assert [driver.get_child_state("output", n).get("audio_mute") for n in range(1, 5)] == [
        False, True, False, True,
    ]


async def test_each_page_of_a_paged_read_is_answered_from_its_first_child():
    definition = _matrix(
        [_PAGED],
        polling={"queries": [{
            "each_child": "output", "page_size": 16,
            "send": "VC{child_id}\r", "query_for": "input",
        }]},
    )
    seed = {f"output.{n}.input": n % 9 + 1 for n in range(1, 21)}
    driver, reply = await _round_trip(definition, 20, b"VC17\r", seed)
    assert reply.startswith(b"Vgp0*Out17 ")
    assert _inputs(driver, 20)[16:] == [seed[f"output.{n}.input"] for n in range(17, 21)]
    assert _inputs(driver, 16) == [None] * 16
