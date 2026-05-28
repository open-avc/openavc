"""Tests for ConfigurableDriver — JSON-defined drivers."""

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


async def test_response_with_value_map():
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

    await drv.on_data_received(b"POWR=1")
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


# --- Legacy YAML key deprecation warnings ---


def _build_minimal_definition(driver_id: str, *, response_key: str, command_key: str) -> dict:
    """Build a minimal definition that uses the requested response/command key."""
    return {
        "id": driver_id,
        "name": driver_id,
        "transport": "tcp",
        "commands": {
            "ping": {
                "label": "Ping",
                command_key: "PING\\r",
                "params": {},
            },
        },
        "responses": [
            {response_key: r"PONG", "mappings": []},
        ],
        "state_variables": {},
    }


def test_legacy_pattern_key_warns_once_per_driver_id(caplog):
    from server.drivers.configurable import _WARNED_LEGACY_KEYS
    _WARNED_LEGACY_KEYS.clear()

    definition = _build_minimal_definition(
        "legacy_pattern_test", response_key="pattern", command_key="send"
    )
    with caplog.at_level("WARNING"):
        create_configurable_driver_class(definition)
    matches = [r for r in caplog.records if "deprecated YAML key 'pattern'" in r.getMessage()]
    assert len(matches) == 1, f"Expected exactly one warning, got {[r.getMessage() for r in matches]}"

    # Same driver_id, second class build — no new warning
    caplog.clear()
    with caplog.at_level("WARNING"):
        create_configurable_driver_class(definition)
    matches = [r for r in caplog.records if "deprecated YAML key 'pattern'" in r.getMessage()]
    assert matches == []


def test_legacy_string_key_warns_once_per_driver_id(caplog):
    from server.drivers.configurable import _WARNED_LEGACY_KEYS
    _WARNED_LEGACY_KEYS.clear()

    definition = _build_minimal_definition(
        "legacy_string_test", response_key="match", command_key="string"
    )
    with caplog.at_level("WARNING"):
        create_configurable_driver_class(definition)
    matches = [r for r in caplog.records if "deprecated YAML key 'string'" in r.getMessage()]
    assert len(matches) == 1, f"Expected exactly one warning, got {[r.getMessage() for r in matches]}"

    caplog.clear()
    with caplog.at_level("WARNING"):
        create_configurable_driver_class(definition)
    matches = [r for r in caplog.records if "deprecated YAML key 'string'" in r.getMessage()]
    assert matches == []


def test_canonical_keys_emit_no_deprecation_warning(caplog):
    from server.drivers.configurable import _WARNED_LEGACY_KEYS
    _WARNED_LEGACY_KEYS.clear()

    definition = _build_minimal_definition(
        "canonical_keys_test", response_key="match", command_key="send"
    )
    with caplog.at_level("WARNING"):
        create_configurable_driver_class(definition)
    matches = [r for r in caplog.records if "deprecated YAML key" in r.getMessage()]
    assert matches == []


# --- child_entity_types passthrough (P8) ---
#
# Locks in that a YAML driver declaring child_entity_types ends up with
# those types on DRIVER_INFO, and that the resulting class drives the
# existing BaseDriver register_child / set_child_state path the same way
# a hand-coded Python driver would. The plan calls this out as the gate
# that unblocks YAML-authored controller drivers.

CHILD_TYPES_DEFINITION: dict = {
    "id": "test_controller",
    "name": "Test Controller",
    "transport": "tcp",
    "default_config": {"host": "", "port": 23},
    "config_schema": {},
    "state_variables": {},
    "commands": {},
    "responses": [],
    "child_entity_types": {
        "encoder": {
            "label": "Encoder",
            "label_plural": "Encoders",
            "id_format": {
                "type": "integer", "min": 1, "max": 762, "pad_width": 3,
            },
            "state_variables": {
                "name": {"type": "string"},
                "ip": {"type": "string"},
                "signal_present": {
                    "type": "boolean", "cloud_priority": "high",
                },
                "edid_block": {
                    "type": "string", "cloud_priority": "low",
                },
            },
            "summary_fields": ["name", "ip", "signal_present"],
            "label_field": "name",
        },
    },
}


def test_yaml_child_entity_types_land_in_driver_info():
    """The factory copies child_entity_types straight onto DRIVER_INFO
    so BaseDriver's child APIs activate for YAML drivers."""
    cls = create_configurable_driver_class(CHILD_TYPES_DEFINITION)
    types = cls.DRIVER_INFO.get("child_entity_types")
    assert types is not None, "child_entity_types missing from DRIVER_INFO"
    assert "encoder" in types
    encoder = types["encoder"]
    assert encoder["label"] == "Encoder"
    assert encoder["id_format"]["pad_width"] == 3
    # cloud_priority survives the round-trip — the cloud relay reads it
    # off DRIVER_INFO to decide tier (high/low) per child property.
    encoder_vars = encoder["state_variables"]
    assert encoder_vars["signal_present"]["cloud_priority"] == "high"
    assert encoder_vars["edid_block"]["cloud_priority"] == "low"


def test_yaml_driver_without_child_types_has_no_key():
    """Drivers that don't declare child types don't get an empty key
    written — keeps DRIVER_INFO clean for the existing fleet."""
    cls = create_configurable_driver_class(SAMPLE_DEFINITION)
    assert "child_entity_types" not in cls.DRIVER_INFO


def test_yaml_driver_register_child_through_base_path(state, events):
    """End-to-end: YAML-defined child schema -> register_child -> child
    state keys live at device.<id>.<type>.<padded>.<prop>, defaults from
    the schema, and the platform-injected `online` / `label` keys ride
    along."""
    state.set_event_bus(events)
    cls = create_configurable_driver_class(CHILD_TYPES_DEFINITION)
    drv = cls("ctrl_a", {"host": "127.0.0.1", "port": 23}, state, events)

    drv.register_child("encoder", 5, initial_state={
        "name": "Lobby TX", "signal_present": True,
    })

    assert state.get("device.ctrl_a.encoder.005.name") == "Lobby TX"
    assert state.get("device.ctrl_a.encoder.005.signal_present") is True
    # ip defaulted from the declared "string" type.
    assert state.get("device.ctrl_a.encoder.005.ip") == ""
    # Platform-managed keys present without the YAML having to declare them.
    assert state.get("device.ctrl_a.encoder.005.online") is True
    assert state.get("device.ctrl_a.encoder.005.label") == ""


def test_yaml_driver_set_child_state_validates_against_schema(state, events):
    """set_child_state honours the YAML-declared schema — unknown props
    raise, declared props write through to state."""
    state.set_event_bus(events)
    cls = create_configurable_driver_class(CHILD_TYPES_DEFINITION)
    drv = cls("ctrl_b", {"host": "127.0.0.1", "port": 23}, state, events)
    drv.register_child("encoder", 1)

    drv.set_child_state("encoder", 1, "signal_present", False)
    assert state.get("device.ctrl_b.encoder.001.signal_present") is False

    with pytest.raises(ValueError, match="not declared"):
        drv.set_child_state("encoder", 1, "no_such_prop", "x")


def test_yaml_driver_register_child_unknown_type_raises(state, events):
    """A YAML driver that didn't declare a given type can't register one."""
    state.set_event_bus(events)
    cls = create_configurable_driver_class(CHILD_TYPES_DEFINITION)
    drv = cls("ctrl_c", {"host": "127.0.0.1", "port": 23}, state, events)

    with pytest.raises(ValueError, match="did not declare"):
        drv.register_child("decoder", 1)
