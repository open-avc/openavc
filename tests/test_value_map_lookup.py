"""Every value map a driver file writes is read by one lookup.

A reply's ``map:`` (a text, JSON or OSC rule's mapping, a json ``set:`` spec, a
child's state), a device setting's ``map:`` and a command parameter's ``map:``
each pick their entry the way every authored map does
(``core/condition_eval.match_key``): the exact key, then one apart from case,
then one the reading equals as a boolean or number. The runtime and the
simulator ask it through ``compiled_protocol.map_entry``; the checker names a
map key, or a value meant as text, that YAML did not read as text.

``CASES`` is run through every reader that looks a reading up in an authored
map, so the readers cannot drift apart again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import compiled_protocol
from openavc.drivers.avcdriver_semantic import check_driver_definition, validate_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import read_driver_file
from openavc.simulator.yaml_auto import YAMLAutoSimulator

# (the map's keys, the reading, the key it picks or None). A key that is not
# text is one YAML read from an unquoted key: `01:` arrives as 1, `on:` as True.
CASES: list[tuple[list[Any], Any, Any]] = [
    (["01", "1"], "1", "1"),
    (["01"], "1", None),
    ([1], "01", 1),
    ([1, 0], "1", 1),
    (["ON", "OFF"], "on", "ON"),
    (["on", "ON"], "ON", "ON"),
    (["true", "false"], True, "true"),
    ([True, False], "on", True),
    (["1", "0"], 1.0, "1"),
    (["1"], "1.0", None),
    (["a"], None, None),
]


def _ids(case: tuple[list[Any], Any, Any]) -> str:
    keys, reading, _ = case
    return f"{keys!r}<-{reading!r}"


def _value_map(keys: list[Any]) -> dict[Any, str]:
    """Each key mapped to a word no reading spells."""
    return {key: f"word{i}" for i, key in enumerate(keys)}


def _picked(keys: list[Any], picked: Any) -> str | None:
    return None if picked is None else _value_map(keys)[picked]


@pytest.mark.parametrize("case", CASES, ids=[_ids(c) for c in CASES])
def test_the_lookup_every_reader_asks(case: tuple[list[Any], Any, Any]) -> None:
    keys, reading, picked = case
    found, value = compiled_protocol.map_entry(_value_map(keys), reading)
    assert (found, value) == ((picked is not None), _picked(keys, picked))


@pytest.mark.parametrize("case", CASES, ids=[_ids(c) for c in CASES])
def test_a_setting_or_parameter_map_picks_the_same_word(case: tuple[list[Any], Any, Any]) -> None:
    keys, reading, picked = case
    assert compiled_protocol.map_word(reading, _value_map(keys)) == _picked(keys, picked)


@pytest.mark.parametrize(
    "case", [c for c in CASES if isinstance(c[1], str)], ids=[_ids(c) for c in CASES if isinstance(c[1], str)],
)
def test_a_device_word_reads_back_through_the_same_lookup(case: tuple[list[Any], Any, Any]) -> None:
    # The inverse: the simulator reads a setting's word off the wire, so the
    # map's words are what a reading picks among.
    keys, reading, picked = case
    inverse = {f"value{i}": key for i, key in enumerate(keys)}
    expected = (False, None) if picked is None else (True, f"value{keys.index(picked)}")
    assert compiled_protocol.value_for_map_word(reading, inverse) == expected


def _reply_driver(value_map: dict[Any, Any]) -> Any:
    definition = {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
        "state_variables": {"mode": {"type": "string", "label": "Mode"}},
        "commands": {},
        "responses": [{"match": r"^MODE=(.*)$",
                       "mappings": [{"group": 1, "state": "mode", "map": value_map}]}],
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return create_configurable_driver_class(definition)("w1", {}, state, events)


@pytest.mark.parametrize(
    "case", [c for c in CASES if isinstance(c[1], str)], ids=[_ids(c) for c in CASES if isinstance(c[1], str)],
)
async def test_a_text_reply_picks_the_same_entry(case: tuple[list[Any], Any, Any]) -> None:
    keys, reading, picked = case
    driver = _reply_driver(_value_map(keys))
    await driver.on_data_received(f"MODE={reading}".encode())
    assert driver.get_state("mode") == (reading if picked is None else _picked(keys, picked))


@pytest.mark.parametrize("case", CASES, ids=[_ids(c) for c in CASES])
def test_every_reply_form_reads_a_value_through_the_same_lookup(case: tuple[list[Any], Any, Any]) -> None:
    # The one reader the text, JSON and OSC rules share, given the reading as
    # each form delivers it (a JSON or OSC value arrives typed).
    keys, reading, picked = case
    driver = _reply_driver({})
    mapping = {"map": _value_map(keys), "type": "string"}
    value = driver._mapped_reading(reading, mapping, driver._convert_json_value)
    if picked is None:
        assert value not in _value_map(keys).values()
    else:
        assert value == _picked(keys, picked)


def _osc_device(value_map: dict[Any, Any]) -> YAMLAutoSimulator:
    definition = {
        "id": "acme_console", "name": "Acme Console", "transport": "osc",
        "state_variables": {"mode": {"type": "string", "label": "Mode"}},
        "commands": {},
        "responses": [{"address": "/mode", "mappings": [{"arg": 0, "state": "mode", "map": value_map}]}],
    }
    return YAMLAutoSimulator("w1", {}, driver_def=definition)


_TAGS = {bool: "T", int: "i", float: "f", str: "s"}


@pytest.mark.parametrize(
    "case", [c for c in CASES if c[1] is not None], ids=[_ids(c) for c in CASES if c[1] is not None],
)
def test_the_simulated_osc_device_sets_through_the_same_lookup(case: tuple[list[Any], Any, Any]) -> None:
    keys, reading, picked = case
    device = _osc_device(_value_map(keys))
    device.handle_message("/mode", [(_TAGS[type(reading)], reading)])
    if picked is None:
        assert device.get_state("mode") not in _value_map(keys).values()
    else:
        assert device.get_state("mode") == _picked(keys, picked)


def _notifying_device(templates: dict[Any, str]) -> YAMLAutoSimulator:
    definition = {
        "id": "acme_amp", "name": "Acme Amp", "transport": "tcp", "delimiter": "\\r",
        "state_variables": {"mode": {"type": "string", "label": "Mode"}},
        "commands": {},
        "responses": [{"match": r"^MODE (\w+)$", "set": {"mode": "$1"}}],
        "simulator": {"notifications": {"mode": templates}},
    }
    return YAMLAutoSimulator("w1", {}, driver_def=definition)


@pytest.mark.parametrize("case", CASES, ids=[_ids(c) for c in CASES])
def test_a_notification_template_is_picked_through_the_same_lookup(case: tuple[list[Any], Any, Any]) -> None:
    keys, reading, picked = case
    device = _notifying_device(_value_map(keys))
    assert device._notification_template("mode", reading) == _picked(keys, picked)


# ── The defects, each as an author meets it ──


async def test_an_unquoted_number_key_matches_the_reply_it_names() -> None:
    # `01: "on"` arrives from YAML as {1: "on"}; the reply "01" is that key.
    driver = _reply_driver({1: "on", 0: "off"})
    await driver.on_data_received(b"MODE=01")
    assert driver.get_state("mode") == "on"


async def test_a_quoted_leading_zero_key_matches_only_its_own_text() -> None:
    driver = _reply_driver({"01": "on", "00": "off"})
    await driver.on_data_received(b"MODE=1")
    assert driver.get_state("mode") == "1"
    await driver.on_data_received(b"MODE=01")
    assert driver.get_state("mode") == "on"


async def test_a_json_boolean_matches_a_true_key() -> None:
    definition = {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
        "state_variables": {"link": {"type": "enum", "values": ["up", "down"], "label": "Link"}},
        "commands": {},
        "responses": [{"json": True, "set": {"link": {"key": "linked", "map": {"true": "up", "false": "down"}}}}],
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = create_configurable_driver_class(definition)("w1", {}, state, events)
    await driver.on_data_received(b'{"linked": true}')
    assert driver.get_state("link") == "up"


def test_a_simulated_osc_device_maps_a_float_argument() -> None:
    device = _osc_device({"1": "on", "0": "off"})
    device.handle_message("/mode", [("f", 1.0)])
    assert device.get_state("mode") == "on"


async def test_a_notification_keyed_by_an_unquoted_boolean_is_sent() -> None:
    # `true:` and `false:` arrive from YAML as booleans, and a boolean state
    # value is what they name.
    definition = {
        "id": "acme_amp", "name": "Acme Amp", "transport": "tcp", "delimiter": "\\r",
        "state_variables": {"mute": {"type": "boolean", "label": "Mute"}},
        "commands": {},
        "responses": [{"match": r"^MUTE ([01])$", "set": {"mute": "$1"}}],
        "simulator": {
            "initial_state": {"mute": False},
            "notifications": {"mute": {True: "NOTIFY MUTE 1", False: "NOTIFY MUTE 0"}},
        },
    }
    device = YAMLAutoSimulator("w1", {}, driver_def=definition)
    await device.start(0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", device.port)
        try:
            await asyncio.sleep(0.05)
            device.set_state("mute", True)
            frame = await asyncio.wait_for(reader.readuntil(b"\r"), timeout=3.0)
            assert frame == b"NOTIFY MUTE 1\r"
        finally:
            writer.close()
    finally:
        await device.stop()


# ── What the checker says ──


_QUOTE_IT = "Put it in quotes."


def _driver_with(responses: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
        "state_variables": {
            "power": {"type": "enum", "values": ["on", "off"], "label": "Power"},
            "label": {"type": "string", "label": "Label"},
            "muted": {"type": "boolean", "label": "Muted"},
            "count": {"type": "integer", "label": "Count"},
        },
        "commands": {},
        "responses": responses,
        **extra,
    }


def _quote_it(errors: list[str], place: str) -> list[str]:
    return [e for e in errors if e.startswith(f"{place}:") and _QUOTE_IT in e]


@pytest.mark.parametrize(
    ("rule", "place"),
    [
        ({"match": r"P=(\d+)", "mappings": [{"group": 1, "state": "power", "map": {1: "on"}}]},
         "responses[0].mappings[0].map"),
        ({"json": True, "set": {"power": {"key": "p", "map": {True: "on"}}}},
         "responses[0].set.power.map"),
        ({"match": r"C(\d)=(\d)", "child_set": [{"type": "channel", "id": "$1",
                                                  "state": {"level": {"group": "$2", "map": {0: "low"}}}}]},
         "responses[0].child_set[0].state.level.map"),
    ],
)
def test_a_reply_map_key_yaml_did_not_read_as_text_is_named(rule: dict[str, Any], place: str) -> None:
    definition = _driver_with([rule], child_entity_types={"channel": {
        "label": "Channel", "id_format": {"type": "integer"},
        "state_variables": {"level": {"type": "string", "label": "Level"}},
    }})
    assert len(_quote_it(validate_driver_definition(definition, strict=True), place)) == 1


@pytest.mark.parametrize(
    ("state", "value", "named"),
    [
        ("power", True, True),   # a bare `on` where the enum holds the word "on"
        ("label", False, True),  # a bare `off` where the variable holds text
        ("label", 5, True),      # bare digits where the variable holds text
        ("muted", True, False),  # a boolean is what a boolean variable holds
        ("count", 2, False),     # a number is what an integer variable holds
    ],
)
def test_a_reply_map_value_is_named_only_where_text_is_meant(state: str, value: Any, named: bool) -> None:
    definition = _driver_with([
        {"match": r"P=(\d)", "mappings": [{"group": 1, "state": state, "map": {"1": value}}]},
    ])
    found = _quote_it(validate_driver_definition(definition, strict=True), "responses[0].mappings[0].map")
    assert len(found) == (1 if named else 0), found


def test_a_text_reply_map_is_not_named() -> None:
    definition = _driver_with([
        {"match": r"P=(\d+)", "mappings": [{"group": 1, "state": "power", "map": {"01": "on", "00": "off"}}]},
    ])
    assert _quote_it(validate_driver_definition(definition, strict=True), "responses[0].mappings[0].map") == []


def test_a_reply_map_key_that_is_not_text_is_refused_only_at_the_authors_doors(tmp_path: Path) -> None:
    # The lookup matches it (an unquoted `01:` still matches "01"), so a driver
    # already installed keeps loading, the problem named on it.
    definition = _driver_with([
        {"match": r"P=(\d+)", "mappings": [{"group": 1, "state": "power", "map": {1: "on", 0: "off"}}]},
    ])
    place = "responses[0].mappings[0].map"
    assert _quote_it(validate_driver_definition(definition, strict=True), place)
    verdict = check_driver_definition(definition)
    assert not _quote_it(verdict.errors, place), verdict.errors
    assert _quote_it(verdict.notices, place), verdict.notices

    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    read = read_driver_file(path)
    assert read.definition is not None, read.reasons
    assert any(_QUOTE_IT in n for n in read.notices), read.notices


def test_a_parameter_map_word_yaml_read_as_a_boolean_says_to_quote_it() -> None:
    definition = _driver_with([], commands={"set_mode": {
        "send": "M {mode}\r",
        "params": {"mode": {"type": "string", "label": "Mode", "map": {"auto": True}}},
    }})
    errors = validate_driver_definition(definition, strict=True)
    assert any(_QUOTE_IT in e and "set_mode" in e for e in errors), errors


def test_a_boolean_setting_key_is_true_or_false_as_written() -> None:
    # The lookup reads " true " as no boolean, so the key would never be met.
    definition = _driver_with([], device_settings={"standby": {
        "type": "boolean", "label": "Standby", "state_key": "muted",
        "write": {"send": "STBY {value}\r"}, "map": {" true ": "ON", "false": "OFF"},
    }})
    errors = validate_driver_definition(definition, strict=True)
    assert any("is not a value a boolean setting takes" in e for e in errors), errors
