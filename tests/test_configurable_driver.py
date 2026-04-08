"""Tests for ConfigurableDriver — JSON-defined drivers."""

import asyncio

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import ConfigurableDriver, create_configurable_driver_class

# Sample JSON driver definition (like an Extron switcher)
SAMPLE_DEFINITION = {
    "id": "test_switcher",
    "name": "Test Video Switcher",
    "manufacturer": "TestCo",
    "category": "switcher",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "default_config": {
        "host": "",
        "port": 23,
    },
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "IP Address"},
        "port": {"type": "integer", "default": 23, "label": "Port"},
    },
    "state_variables": {
        "input": {"type": "integer", "label": "Current Input"},
        "volume": {"type": "integer", "label": "Volume"},
        "mute": {"type": "boolean", "label": "Mute"},
    },
    "commands": {
        "set_input": {
            "label": "Set Input",
            "string": "{input}!\\r\\n",
            "params": {
                "input": {"type": "integer", "required": True},
            },
        },
        "set_volume": {
            "label": "Set Volume",
            "string": "{level}V\\r\\n",
            "params": {
                "level": {"type": "integer", "required": True},
            },
        },
        "mute_on": {
            "label": "Mute On",
            "string": "1Z\\r\\n",
            "params": {},
        },
        "query_input": {
            "label": "Query Input",
            "string": "!\\r\\n",
            "params": {},
        },
    },
    "responses": [
        {
            "pattern": r"In(\d+) All",
            "mappings": [
                {"group": 1, "state": "input", "type": "integer"},
            ],
        },
        {
            "pattern": r"Vol(\d+)",
            "mappings": [
                {"group": 1, "state": "volume", "type": "integer"},
            ],
        },
        {
            "pattern": r"Amt(\d+)",
            "mappings": [
                {"group": 1, "state": "mute", "type": "boolean"},
            ],
        },
    ],
    "polling": {
        "interval": 10,
        "queries": ["!\\r\\n"],
    },
}


@pytest.fixture
def driver_class():
    return create_configurable_driver_class(SAMPLE_DEFINITION)


@pytest.fixture
def driver(driver_class, state, events):
    state.set_event_bus(events)
    return driver_class(
        "sw1",
        {"host": "127.0.0.1", "port": 23},
        state,
        events,
    )


def test_create_class(driver_class):
    """Factory creates a class with correct DRIVER_INFO."""
    assert driver_class.DRIVER_INFO["id"] == "test_switcher"
    assert driver_class.DRIVER_INFO["name"] == "Test Video Switcher"
    assert driver_class.DRIVER_INFO["transport"] == "tcp"
    assert "set_input" in driver_class.DRIVER_INFO["commands"]


def test_driver_info_commands(driver_class):
    """Commands metadata is properly built."""
    cmds = driver_class.DRIVER_INFO["commands"]
    assert cmds["set_input"]["label"] == "Set Input"
    assert "input" in cmds["set_input"]["params"]


def test_state_initialization(driver):
    """State variables are initialized from definition."""
    assert driver.get_state("input") == 0
    assert driver.get_state("volume") == 0
    assert driver.get_state("mute") is False
    assert driver.get_state("connected") is False


async def test_on_data_input_response(driver):
    """Response pattern matching updates state correctly."""
    await driver.on_data_received(b"In3 All")
    assert driver.get_state("input") == 3


async def test_on_data_volume_response(driver):
    """Volume response updates state."""
    await driver.on_data_received(b"Vol45")
    assert driver.get_state("volume") == 45


async def test_on_data_mute_response(driver):
    """Mute response with boolean coercion."""
    await driver.on_data_received(b"Amt1")
    assert driver.get_state("mute") is True

    await driver.on_data_received(b"Amt0")
    assert driver.get_state("mute") is False


async def test_on_data_no_match(driver):
    """Unmatched data doesn't change state."""
    await driver.on_data_received(b"GARBAGE")
    # State should be unchanged from defaults
    assert driver.get_state("input") == 0


async def test_on_data_empty(driver):
    """Empty data is handled gracefully."""
    await driver.on_data_received(b"")
    await driver.on_data_received(b"   ")


def test_response_with_value_map():
    """Value maps translate raw values to mapped values."""
    definition = {
        "id": "test_map",
        "name": "Map Test",
        "transport": "tcp",
        "commands": {},
        "responses": [
            {
                "pattern": r"POWR=(\d)",
                "mappings": [
                    {
                        "group": 1,
                        "state": "power",
                        "type": "string",
                        "map": {"0": "off", "1": "on"},
                    },
                ],
            },
        ],
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
        },
    }
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls("test", {}, state, events)

    asyncio.get_event_loop().run_until_complete(drv.on_data_received(b"POWR=1"))
    assert drv.get_state("power") == "on"


def test_coerce_value():
    """Type coercion helper works correctly."""
    assert ConfigurableDriver._coerce_value("42", "integer") == 42
    assert ConfigurableDriver._coerce_value("bad", "integer") == "bad"  # returns raw on failure
    assert ConfigurableDriver._coerce_value("3.14", "float") == 3.14
    assert ConfigurableDriver._coerce_value("bad", "float") == "bad"  # returns raw on failure
    assert ConfigurableDriver._coerce_value("1", "boolean") is True
    assert ConfigurableDriver._coerce_value("true", "boolean") is True
    assert ConfigurableDriver._coerce_value("0", "boolean") is False
    assert ConfigurableDriver._coerce_value("hello", "string") == "hello"


def test_invalid_regex_skipped():
    """Invalid regex in definition logs warning but doesn't crash."""
    definition = {
        "id": "test_bad_regex",
        "name": "Bad Regex",
        "transport": "tcp",
        "commands": {},
        "responses": [
            {"pattern": "[invalid", "mappings": []},
        ],
        "state_variables": {},
    }
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls("test", {}, state, events)
    assert len(drv._compiled_responses) == 0
