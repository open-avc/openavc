"""A value written to a simulated variable is the value the driver would
have stored: its declared type read the way the driver runtime reads it
(``compiled_protocol``), ``float`` as a number, a boolean by its token,
and only the device's bounds applied on top.

Invented devices only (platform-feature tests).
"""

import asyncio
import math

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.compiled_protocol import coerce_osc_value, coerce_value
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.simulator.yaml_auto import YAMLAutoSimulator


def _sim(state_variables: dict) -> YAMLAutoSimulator:
    return YAMLAutoSimulator(
        "dev1",
        config={},
        driver_def={
            "id": "acme_levels",
            "name": "Acme Levels",
            "transport": "tcp",
            "state_variables": state_variables,
            "commands": {},
            "responses": [],
        },
    )


# What a device's text carries, read as each type. Each one is compared with
# what the driver stores for the same capture (compiled_protocol.coerce_value).
_TEXT = [
    "0", "1", "2", "01", "00", "-1", "1.0", "0.0", "2.5", "-0", " 1", "1 ",
    "true", "TRUE", "True", "false", "on", "ON", "off", "yes", "no",
    "abc", "", "nan", "inf", "1e3", "0x10",
]


@pytest.mark.parametrize("var_type", ["integer", "number", "float", "boolean", "string", "enum"])
@pytest.mark.parametrize("text", _TEXT)
def test_text_is_stored_as_the_driver_reads_it(var_type, text):
    sim = _sim({"v": {"type": var_type, "label": "V"}})
    stored = sim._coerce_value("v", text)
    expected = coerce_value(text, var_type)
    assert repr(stored) == repr(expected)


# A value that arrives typed: an OSC argument, a YAML literal, a converted
# number. Compared with what the driver stores for an argument that arrives
# typed (compiled_protocol.coerce_osc_value).
_TYPED = [0, 1, 2, -1, 0.0, 1.0, 0.5, 2.5, -0.0, True, False, None]


@pytest.mark.parametrize("var_type", ["integer", "number", "float", "boolean", "string", "enum"])
@pytest.mark.parametrize("value", _TYPED, ids=repr)
def test_a_typed_value_is_stored_as_the_driver_reads_it(var_type, value):
    sim = _sim({"v": {"type": var_type, "label": "V"}})
    assert repr(sim._coerce_value("v", value)) == repr(coerce_osc_value(value, var_type))


def test_a_float_variable_holds_a_number():
    sim = _sim({"level": {"type": "float", "label": "Level"}})
    assert sim._coerce_value("level", "2.5") == 2.5
    assert isinstance(sim._coerce_value("level", "2"), float)
    assert sim._coerce_value("level", "loud") == "loud"


@pytest.mark.parametrize("text, expected", [
    ("01", True), ("2", True), ("1.0", True), (" on ", True),
    ("00", False), ("0", False), ("off", False), ("", False),
])
def test_a_boolean_is_read_by_its_token(text, expected):
    sim = _sim({"mute": {"type": "boolean", "label": "Mute"}})
    assert sim._coerce_value("mute", text) is expected


@pytest.mark.parametrize("bound", [math.inf, math.nan])
def test_an_integer_whose_upper_bound_is_not_finite_still_stores(bound):
    sim = _sim({"vol": {"type": "integer", "label": "Vol", "max": bound}})
    sim.set_state("vol", sim._coerce_value("vol", "5"))
    assert sim.get_state("vol") == 5


def test_the_bounds_still_apply_once_the_value_is_stored():
    sim = _sim({
        "vol": {"type": "integer", "label": "Vol", "min": 2.5, "max": 7.5},
        "gain": {"type": "float", "label": "Gain", "min": -10, "max": 10},
    })
    sim.set_state("vol", sim._coerce_value("vol", "0"))
    assert sim.get_state("vol") == 3
    sim.set_state("vol", sim._coerce_value("vol", "100"))
    assert sim.get_state("vol") == 7
    sim.set_state("gain", sim._coerce_value("gain", "-40.5"))
    assert sim.get_state("gain") == -10
    sim.set_state("gain", sim._coerce_value("gain", "12.25"))
    assert sim.get_state("gain") == 10


def test_a_number_that_is_not_a_number_is_kept_as_the_driver_keeps_it():
    sim = _sim({"gain": {"type": "number", "label": "Gain", "min": 0, "max": 1}})
    sim.set_state("gain", sim._coerce_value("gain", "nan"))
    assert math.isnan(sim.get_state("gain"))


def test_no_value_stays_no_value_whatever_the_type():
    sim = _sim({
        "name": {"type": "string", "label": "Name"},
        "level": {"type": "float", "label": "Level"},
    })
    assert sim._coerce_value("name", None) is None
    assert sim._coerce_value("level", None) is None


# ── The driver and the simulator, over a real socket ──


def _dimmer_def() -> dict:
    return {
        "id": "acme_dimmer",
        "name": "Acme Dimmer",
        "manufacturer": "Acme",
        "category": "lighting",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "command_suffix": "\r",
        "default_config": {"host": "", "port": 5000},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
        },
        "state_variables": {
            "level": {"type": "float", "label": "Level", "min": 0, "max": 1},
            "active": {"type": "boolean", "label": "Active"},
        },
        "commands": {
            "set_level": {
                "send": "LVL {level}",
                "params": {"level": {"type": "number", "min": 0, "max": 1, "required": True}},
                "sets": {"level": "{level}"},
                "query_for": "level",
            },
            "set_active": {
                "send": "ACT {active:02d}",
                "params": {"active": {"type": "integer", "min": 0, "max": 1, "required": True}},
                "sets": {"active": "{active}"},
                "query_for": "active",
            },
        },
        "responses": [
            {"match": r"^LVL ([\d.]+)$", "set": {"level": "$1"}},
            {"match": r"^ACT (\d+)$", "set": {"active": "$1"}},
        ],
    }


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def test_the_dimmer_definition_is_valid():
    assert validate_driver_definition(_dimmer_def()) == []


async def test_a_float_and_a_padded_boolean_round_trip_to_the_same_value():
    definition = _dimmer_def()
    sim = YAMLAutoSimulator(definition["id"], config={}, driver_def=definition)
    await sim.start(0)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    config = dict(definition["default_config"])
    config.update({"host": "127.0.0.1", "port": sim.port, "poll_interval": 0})
    drv = create_configurable_driver_class(definition)("dev1", config, state, events)
    try:
        await drv.connect()

        await drv.send_command("set_level", {"level": 0.25})
        await _wait_for(lambda: drv.get_state("level") == 0.25)
        assert sim.get_state("level") == 0.25
        assert isinstance(sim.get_state("level"), float)

        await drv.send_command("set_active", {"active": 1})
        await _wait_for(lambda: drv.get_state("active") is True)
        assert sim.get_state("active") is True

        for name in ("level", "active"):
            assert repr(drv.get_state(name)) == repr(sim.get_state(name))
    finally:
        await drv.disconnect()
        await sim.stop()


# ── A scaffolded Python simulator seeds a float variable as a number ──


def test_a_scaffolded_float_variable_starts_at_a_number():
    from openavc.simulator.scaffold import _initial_value_for

    assert _initial_value_for("reading", {"type": "float"}) == "0.0"
    assert _initial_value_for("reading", {"type": "float"}) == _initial_value_for(
        "reading", {"type": "number"}
    )
