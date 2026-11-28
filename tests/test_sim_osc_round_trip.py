"""A generated OSC simulator's answers, read back by the driver they answer.

Each case builds the running driver and its generated simulator from one
invented definition, has the simulator answer (a query, or a change pushed to
the peer), sends the answer through the OSC codec, and lets the driver's own
reply rules read it. What the driver then holds is what the simulated device
holds, or the device said nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.osc_codec import osc_decode_message, osc_encode_message


def _definition(responses: list[dict], state_variables: dict) -> dict[str, Any]:
    return {
        "id": "acme_mixer",
        "name": "Acme Mixer",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": "osc",
        "default_config": {"host": "", "port": 10023},
        "state_variables": state_variables,
        "commands": {},
        "responses": responses,
        "simulator": {"push_state": True},
    }


class _Pair:
    """The driver and its simulated device, built from one definition."""

    def __init__(self, responses: list[dict], state_variables: dict) -> None:
        definition = _definition(responses, state_variables)
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        cls = create_configurable_driver_class(definition)
        self.driver = cls("m1", {**definition["default_config"], "host": "127.0.0.1"}, state, events)
        self.sim = YAMLAutoSimulator("m1", {}, driver_def=definition)
        self.pushed: list[bytes] = []
        self.sim.push_datagram = self.pushed.append
        self.sim._peer_heard = lambda: True

    async def query(self, address: str) -> list[tuple[str, list]]:
        """The simulator's answer to a query of ``address``, as it goes out
        on the wire, after the driver has read it."""
        answer = self.sim.handle_message(address, []) or []
        wire = [osc_encode_message(addr, args) for addr, args in answer]
        for data in wire:
            await self.driver._handle_osc_response(data)
        return [osc_decode_message(data) for data in wire]

    async def push(self, key: str, value: Any) -> list[tuple[str, list]]:
        """What the simulator pushes when ``key`` changes outside a message,
        after the driver has read it."""
        self.pushed.clear()
        self.sim.set_state(key, value)
        for data in self.pushed:
            await self.driver._handle_osc_response(data)
        return [osc_decode_message(data) for data in self.pushed]

    def held(self, *keys: str) -> dict[str, Any]:
        return {key: self.driver.get_state(key) for key in keys}


_POWER = {"power": {"type": "enum", "values": ["on", "off"]}}


def _two_addresses() -> _Pair:
    """One variable reported at two addresses, each in its own words."""
    return _Pair([
        {"address": "/acme/a", "mappings": [
            {"arg": 0, "state": "power", "type": "string", "map": {"A1": "on", "A0": "off"}},
        ]},
        {"address": "/acme/b", "mappings": [
            {"arg": 0, "state": "power", "type": "integer", "map": {"7": "on", "6": "off"}},
        ]},
    ], _POWER)


@pytest.mark.asyncio
async def test_each_address_answers_a_query_in_its_own_words():
    pair = _two_addresses()
    pair.sim._state["power"] = "on"
    assert await pair.query("/acme/a") == [("/acme/a", [("s", "A1")])]
    assert pair.held("power") == {"power": "on"}
    pair.sim._state["power"] = "off"
    assert await pair.query("/acme/b") == [("/acme/b", [("i", 6)])]
    assert pair.held("power") == {"power": "off"}


@pytest.mark.asyncio
async def test_each_variable_answers_at_the_argument_its_mapping_names():
    pair = _Pair([{"address": "/acme/pair", "mappings": [
        {"arg": 1, "state": "left", "type": "integer"},
        {"arg": 0, "state": "right", "type": "integer"},
    ]}], {"left": {"type": "integer"}, "right": {"type": "integer"}})
    pair.sim._state.update(left=1, right=2)
    assert await pair.query("/acme/pair") == [("/acme/pair", [("i", 2), ("i", 1)])]
    assert pair.held("left", "right") == {"left": 1, "right": 2}


@pytest.mark.asyncio
async def test_an_argument_no_mapping_reads_is_sent_empty():
    pair = _Pair([{"address": "/acme/gap", "mappings": [
        {"arg": 2, "state": "level", "type": "integer"},
    ]}], {"level": {"type": "integer"}})
    pair.sim._state["level"] = 5
    assert await pair.query("/acme/gap") == [("/acme/gap", [("N", None), ("N", None), ("i", 5)])]
    assert pair.held("level") == {"level": 5}


@pytest.mark.asyncio
async def test_a_push_carries_the_whole_message_of_its_address():
    pair = _Pair([{"address": "/acme/pair", "mappings": [
        {"arg": 0, "state": "left", "type": "integer"},
        {"arg": 1, "state": "right", "type": "integer"},
    ]}], {"left": {"type": "integer"}, "right": {"type": "integer"}})
    pair.sim._state["left"] = 3
    assert await pair.push("right", 4) == [("/acme/pair", [("i", 3), ("i", 4)])]
    assert pair.held("left", "right") == {"left": 3, "right": 4}


def _fader() -> _Pair:
    """A level whose map names only the device's word for its bottom."""
    return _Pair([{"address": "/acme/level", "mappings": [
        {"arg": 0, "state": "level", "type": "float", "map": {"-inf": -90}},
    ]}], {"level": {"type": "number", "min": -90, "max": 10}})


@pytest.mark.asyncio
async def test_a_value_the_map_does_not_name_is_answered_and_pushed_as_the_device_number():
    pair = _fader()
    pair.sim._state["level"] = -20
    assert await pair.query("/acme/level") == [("/acme/level", [("f", -20.0)])]
    assert pair.held("level") == {"level": -20.0}
    assert await pair.push("level", -12) == [("/acme/level", [("f", -12.0)])]
    assert pair.held("level") == {"level": -12.0}


@pytest.mark.asyncio
async def test_a_value_the_map_names_goes_out_as_the_device_word():
    pair = _fader()
    assert await pair.push("level", -90) == [("/acme/level", [("f", float("-inf"))])]
    assert pair.held("level") == {"level": -90.0}


@pytest.mark.asyncio
async def test_a_value_no_message_can_carry_is_neither_answered_nor_pushed():
    pair = _Pair([{"address": "/acme/power", "mappings": [
        {"arg": 0, "state": "power", "type": "integer", "map": {"1": "on", "0": "off"}},
    ]}], {"power": {"type": "string"}})
    pair.sim._state["power"] = "warming"
    assert await pair.query("/acme/power") == []
    assert await pair.push("power", "cooling") == []
    assert pair.held("power") == {"power": None}


@pytest.mark.asyncio
async def test_a_number_the_map_reads_as_another_value_is_neither_answered_nor_pushed():
    pair = _Pair([{"address": "/acme/gain", "mappings": [
        {"arg": 0, "state": "gain", "type": "integer", "map": {"5": 10}},
    ]}], {"gain": {"type": "integer"}})
    pair.sim._state["gain"] = 5
    assert await pair.query("/acme/gain") == []
    assert await pair.push("gain", 10) == [("/acme/gain", [("i", 5)])]
    assert pair.held("gain") == {"gain": 10}


@pytest.mark.asyncio
async def test_one_argument_no_message_can_carry_holds_back_the_whole_message():
    pair = _Pair([{"address": "/acme/pair", "mappings": [
        {"arg": 0, "state": "left", "type": "integer"},
        {"arg": 1, "state": "right", "type": "integer"},
    ]}], {"left": {"type": "integer"}, "right": {"type": "string"}})
    pair.sim._state.update(left=1, right="loud")
    assert await pair.query("/acme/pair") == []
    assert pair.held("left", "right") == {"left": None, "right": None}
