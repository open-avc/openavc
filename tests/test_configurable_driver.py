"""Tests for ConfigurableDriver — JSON-defined drivers."""

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import CommandParamError
from server.drivers.configurable import (
    ConfigurableDriver,
    _normalize_and_validate_command_params,
    create_configurable_driver_class,
)

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
            "send": "{input}!\\r\\n",
            "params": {
                "input": {"type": "integer", "required": True},
            },
        },
        "set_volume": {
            "label": "Set Volume",
            "send": "{level}V\\r\\n",
            "params": {
                "level": {"type": "integer", "required": True},
            },
        },
        "mute_on": {
            "label": "Mute On",
            "send": "1Z\\r\\n",
            "params": {},
        },
        "query_input": {
            "label": "Query Input",
            "send": "!\\r\\n",
            "params": {},
        },
    },
    "responses": [
        {
            "match": r"In(\d+) All",
            "mappings": [
                {"group": 1, "state": "input", "type": "integer"},
            ],
        },
        {
            "match": r"Vol(\d+)",
            "mappings": [
                {"group": 1, "state": "volume", "type": "integer"},
            ],
        },
        {
            "match": r"Amt(\d+)",
            "mappings": [
                {"group": 1, "state": "mute", "type": "boolean"},
            ],
        },
    ],
    "polling": {
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


async def test_static_set_values_coerce_to_declared_type(state, events):
    """Static (non-$) set: shorthand values coerce by the target state var's
    declared type, exactly like captured values. A boolean var fed
    set: {mute: "true"} used to store the string "true" — breaking
    automation `== true` comparisons and the flat-primitives contract.
    Covers the inverted-protocol case too (a response meaning muted=True
    carried as a real YAML bool)."""
    definition = {
        "id": "acme_display",
        "name": "Acme Display",
        "manufacturer": "Acme",
        "category": "display",
        "version": "1.0.0",
        "transport": "tcp",
        "state_variables": {
            "mute": {"type": "boolean", "label": "Mute"},
            "screen_mute": {"type": "boolean", "label": "Screen Mute"},
            "last_action": {"type": "integer", "label": "Last Action"},
            "power": {"type": "enum", "label": "Power", "values": ["on", "off"]},
        },
        "commands": {},
        "responses": [
            # String statics on a boolean var (the common authored form).
            {"match": r"MUTE ON", "set": {"mute": "true"}},
            {"match": r"MUTE OFF", "set": {"mute": "false"}},
            # Real YAML bool static, inverted protocol (00 = muted).
            {"match": r"OK00", "set": {"screen_mute": True}},
            {"match": r"OK01", "set": {"screen_mute": False}},
            # Integer static.
            {"match": r"PRESS", "set": {"last_action": "9"}},
            # Enum statics stay strings.
            {"match": r"PWR1", "set": {"power": "on"}},
        ],
    }
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    drv = cls("disp1", {"host": "127.0.0.1"}, state, events)

    await drv.on_data_received(b"MUTE ON")
    assert drv.get_state("mute") is True
    await drv.on_data_received(b"MUTE OFF")
    assert drv.get_state("mute") is False

    await drv.on_data_received(b"OK00")
    assert drv.get_state("screen_mute") is True
    await drv.on_data_received(b"OK01")
    assert drv.get_state("screen_mute") is False

    await drv.on_data_received(b"PRESS")
    assert drv.get_state("last_action") == 9

    await drv.on_data_received(b"PWR1")
    assert drv.get_state("power") == "on"


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
                "match": r"POWR=(\d)",
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
            {"match": "[invalid", "mappings": []},
        ],
        "state_variables": {},
    }
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls("test", {}, state, events)
    assert len(drv._compiled_responses) == 0


# --- Retired legacy YAML keys ---


def test_retired_alias_keys_are_ignored_at_compile():
    """The old `string:` (for send) and `pattern:` (for match) spellings are
    retired: the loader rejects them, and the compile path must not read them
    either — a definition still carrying one contributes no response rules."""
    definition = {
        "id": "legacy_alias_test",
        "name": "legacy_alias_test",
        "transport": "tcp",
        "commands": {
            "ping": {"label": "Ping", "string": "PING\\r", "params": {}},
        },
        "responses": [
            {"pattern": r"PONG", "mappings": []},
        ],
        "state_variables": {},
    }
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls("test", {}, state, events)
    assert len(drv._compiled_responses) == 0


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


# --- Command param validation + normalization (§69 Phase 3) ---
#
# The runtime gate for command values: every value (whatever the caller) is
# trimmed and validated against the command's declared param schema before it
# reaches the wire. The IDE pickers/inline validation are an authoring aid; the
# runtime is the source of truth, so these test the platform mechanism with an
# invented device + synthetic params.

_PHASE3_PARAMS = {
    "host": {"type": "string", "pattern": r"^\d{1,3}(\.\d{1,3}){3}$"},
    "level": {"type": "integer", "min": 0, "max": 100},
    "ratio": {"type": "number", "min": 0.0, "max": 1.0},
    "name": {"type": "string"},
    "note": {"type": "string"},
}


def test_normalize_trims_strings():
    out = _normalize_and_validate_command_params(
        "cmd", _PHASE3_PARAMS, {"name": "  hello  "}
    )
    assert out["name"] == "hello"


def test_normalize_whitespace_only_optional_passes():
    # A blank-after-trim value is left empty and not validated further.
    out = _normalize_and_validate_command_params(
        "cmd", _PHASE3_PARAMS, {"note": "   "}
    )
    assert out["note"] == ""


def test_validate_integer_in_range_passes_through():
    out = _normalize_and_validate_command_params(
        "cmd", _PHASE3_PARAMS, {"level": "50"}
    )
    # value handling stays string-convention; validation doesn't coerce.
    assert out["level"] == "50"


def test_validate_integer_out_of_range_raises():
    with pytest.raises(CommandParamError):
        _normalize_and_validate_command_params("cmd", _PHASE3_PARAMS, {"level": 150})
    with pytest.raises(CommandParamError):
        _normalize_and_validate_command_params("cmd", _PHASE3_PARAMS, {"level": -1})


def test_validate_integer_non_integral_raises():
    with pytest.raises(CommandParamError):
        _normalize_and_validate_command_params("cmd", _PHASE3_PARAMS, {"level": "5.5"})


def test_validate_integer_non_numeric_raises():
    with pytest.raises(CommandParamError):
        _normalize_and_validate_command_params("cmd", _PHASE3_PARAMS, {"level": "loud"})


def test_validate_bool_is_not_a_number():
    # a stray boolean for a numeric param is rejected, not coerced to 0/1.
    with pytest.raises(CommandParamError):
        _normalize_and_validate_command_params("cmd", _PHASE3_PARAMS, {"level": True})


def test_validate_number_range_with_trim():
    out = _normalize_and_validate_command_params(
        "cmd", _PHASE3_PARAMS, {"ratio": " 0.5 "}
    )
    assert out["ratio"] == "0.5"
    with pytest.raises(CommandParamError):
        _normalize_and_validate_command_params("cmd", _PHASE3_PARAMS, {"ratio": "2"})


def test_validate_pattern_match_and_mismatch():
    out = _normalize_and_validate_command_params(
        "cmd", _PHASE3_PARAMS, {"host": " 192.168.1.10 "}
    )
    assert out["host"] == "192.168.1.10"  # trimmed, then matched
    with pytest.raises(CommandParamError):
        _normalize_and_validate_command_params(
            "cmd", _PHASE3_PARAMS, {"host": "not-an-ip"}
        )


def test_undeclared_param_passthrough():
    out = _normalize_and_validate_command_params(
        "cmd", _PHASE3_PARAMS, {"extra": "  kept  "}
    )
    # An undeclared param is neither trimmed nor validated.
    assert out["extra"] == "  kept  "


async def test_send_command_gates_and_trims(state, events):
    """send_command applies the gate before anything hits the transport."""
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {
            "set_level": {
                "send": "LVL {level}\r",
                "params": {"level": {"type": "integer", "min": 0, "max": 100}},
            },
            "connect": {
                "send": "HOST {host}\r",
                "params": {"host": {"type": "string"}},
            },
        },
        "state_variables": {},
    }
    cls = create_configurable_driver_class(definition)
    state.set_event_bus(events)
    driver = cls("acme1", {"host": "127.0.0.1", "port": 23}, state, events)

    sent: list[bytes] = []

    class FakeTransport:
        connected = True

        async def send(self, data: bytes) -> None:
            sent.append(data)

    driver.transport = FakeTransport()

    # In-range value is sent.
    await driver.send_command("set_level", {"level": "50"})
    assert b"LVL 50\r" in sent[-1]

    # Out-of-range value is rejected before any byte is written.
    before = len(sent)
    with pytest.raises(CommandParamError):
        await driver.send_command("set_level", {"level": "150"})
    assert len(sent) == before

    # String value is trimmed before substitution.
    await driver.send_command("connect", {"host": "  10.0.0.5  "})
    assert b"HOST 10.0.0.5\r" in sent[-1]
