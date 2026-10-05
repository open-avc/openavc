"""A device setting's ``map``: the device's own word for each setting value.

A device that writes a flag as ON / OFF, or a mode as AUTO / MANUAL, declares
the words on the setting. The editor keeps writing true / false and the
declared values; the runtime sends the word in their place, the simulator
reads the word back, and a write queued while the device was offline is
confirmed by the device's report of the same value.

Invented device ("acme_widget") and synthetic payloads throughout.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from openavc.core import device_manager
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.compiled_protocol import setting_value_for_word, setting_word
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.spec import platform_requirements
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.http_client import HTTPClientTransport

WIDGET: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {
        "high_density": {"type": "boolean", "label": "High Density"},
        "encryption": {"type": "enum", "label": "Encryption"},
        "gain": {"type": "integer", "label": "Gain", "offset": -18},
    },
    "commands": {},
    "responses": [
        {
            "match": r"^REP HIGH_DENSITY (\w+)$",
            "mappings": [{
                "group": 1, "state": "high_density", "type": "boolean",
                "map": {"ON": True, "OFF": False},
            }],
        },
        {
            "match": r"^REP ENCRYPTION (\w+)$",
            "mappings": [{
                "group": 1, "state": "encryption",
                "map": {"OFF": "off", "AUTO": "auto", "MANUAL": "manual"},
            }],
        },
        {"match": r"^REP GAIN (\d+)$", "set": {"gain": "$1"}},
    ],
    "device_settings": {
        "high_density": {
            "type": "boolean",
            "label": "High Density",
            "state_key": "high_density",
            "default": False,
            "setup": False,
            "map": {"true": "ON", "false": "OFF"},
            "write": {"send": "SET HIGH_DENSITY {value}\\r"},
        },
        "encryption": {
            "type": "enum",
            "label": "Encryption",
            "state_key": "encryption",
            "values": [
                {"value": "off", "label": "Off"},
                {"value": "auto", "label": "Automatic"},
                {"value": "manual", "label": "Manual"},
            ],
            "map": {"off": "OFF", "auto": "AUTO", "manual": "MANUAL"},
            "write": {"send": "SET ENCRYPTION {value}\\r"},
        },
        # A map that names one value: every other value is written as the
        # number its state variable converts it to.
        "gain": {
            "type": "integer",
            "label": "Gain",
            "state_key": "gain",
            "map": {"-18": "MIN"},
            "write": {"send": "SET GAIN {value}\\r"},
        },
    },
}


class FakeTransport:
    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _driver(definition: dict[str, Any] = WIDGET, state: StateStore | None = None,
            events: EventBus | None = None, device_id: str = "dev1"):
    if state is None:
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls(device_id, {"host": "127.0.0.1", "port": 23}, state, events)
    driver.transport = FakeTransport()
    return driver


def _sim(definition: dict[str, Any] = WIDGET) -> YAMLAutoSimulator:
    return YAMLAutoSimulator(device_id="dev1", config={}, driver_def=definition)


# ── The lookup the runtime and the simulator share ──


@pytest.mark.parametrize("key", [True, "true", "True", "TRUE"])
def test_a_boolean_value_matches_its_key_however_it_is_spelled(key):
    assert setting_word(True, {key: "ON"}) == "ON"
    assert setting_word(False, {key: "ON"}) is None


def test_a_boolean_value_never_matches_a_number_key():
    assert setting_word(True, {"1": "ON"}) is None
    assert setting_word(False, {0: "OFF"}) is None


def test_other_values_match_on_their_text():
    assert setting_word("auto", {"auto": "AUTO"}) == "AUTO"
    assert setting_word(3, {3: "THREE"}) == "THREE"
    assert setting_word(3, {"3": "THREE"}) == "THREE"
    assert setting_word("3", {3: "THREE"}) == "THREE"
    assert setting_word("Auto", {"auto": "AUTO"}) is None


def test_the_word_is_text_and_no_map_maps_nothing():
    assert setting_word("low", {"low": 5}) == "5"
    assert setting_word("low", None) is None
    assert setting_word("low", {}) is None


def test_a_word_reads_back_exactly_first_then_ignoring_case():
    value_map = {"a": "On", "b": "ON"}
    assert setting_value_for_word("ON", value_map) == (True, "b")
    assert setting_value_for_word("on", value_map) == (True, "a")
    assert setting_value_for_word(" ON ", {"true": "ON"}) == (True, "true")
    assert setting_value_for_word("5", {"low": 5}) == (True, "low")
    assert setting_value_for_word("MAYBE", {"true": "ON"}) == (False, None)
    assert setting_value_for_word("ON", None) == (False, None)


# ── The runtime writes the device's word ──


@pytest.mark.asyncio
async def test_a_boolean_setting_writes_the_device_word():
    driver = _driver()
    await driver.set_device_setting("high_density", True)
    await driver.set_device_setting("high_density", False)
    assert driver.transport.sent == [
        b"SET HIGH_DENSITY ON\r",
        b"SET HIGH_DENSITY OFF\r",
    ]


@pytest.mark.asyncio
async def test_a_bare_yaml_key_maps_the_same_as_a_quoted_one():
    definition = copy.deepcopy(WIDGET)
    definition["device_settings"]["high_density"]["map"] = {True: "ON", False: "OFF"}
    driver = _driver(definition)
    await driver.set_device_setting("high_density", True)
    assert driver.transport.sent[-1] == b"SET HIGH_DENSITY ON\r"


@pytest.mark.asyncio
async def test_an_enum_setting_writes_the_device_word():
    driver = _driver()
    await driver.set_device_setting("encryption", "auto")
    assert driver.transport.sent[-1] == b"SET ENCRYPTION AUTO\r"


@pytest.mark.asyncio
async def test_a_value_not_in_the_map_converts_through_its_state_variable():
    driver = _driver()
    await driver.set_device_setting("gain", -18)
    assert driver.transport.sent[-1] == b"SET GAIN MIN\r"
    # -6 dB is not in the map: the gain variable's offset makes it 12.
    await driver.set_device_setting("gain", -6)
    assert driver.transport.sent[-1] == b"SET GAIN 12\r"


class _RecordingHTTP(HTTPClientTransport):
    """An HTTP transport that records each request instead of sending it."""

    def __init__(self) -> None:
        super().__init__("http://127.0.0.1", name="dev1")
        self.requests: list[tuple[str, str, Any, Any]] = []

    async def request(self, method, path, params=None, json_body=None,
                      form_data=None, headers=None, content=None, **_: Any):
        self.requests.append((method, path, json_body, content))
        return None


@pytest.mark.asyncio
async def test_an_http_write_puts_the_word_in_its_path_and_body():
    definition = copy.deepcopy(WIDGET)
    definition["transport"] = "http"
    definition["device_settings"]["high_density"]["write"] = {
        "method": "PUT",
        "path": "/api/hd/{value}",
        "body": '{"high_density": "{value}"}',
    }
    driver = _driver(definition)
    driver.transport = _RecordingHTTP()
    await driver.set_device_setting("high_density", True)
    assert driver.transport.requests == [
        ("PUT", "/api/hd/ON", {"high_density": "ON"}, None),
    ]


# ── The simulator reads the word back and answers in it ──


def test_the_simulator_reads_the_word_back_to_the_setting_value():
    sim = _sim()
    reply = sim.handle_command(b"SET HIGH_DENSITY ON")
    assert sim._state.get("high_density") is True
    assert reply == b"REP HIGH_DENSITY ON\r"

    reply = sim.handle_command(b"SET HIGH_DENSITY OFF")
    assert sim._state.get("high_density") is False
    assert reply == b"REP HIGH_DENSITY OFF\r"


def test_the_simulator_reads_a_word_in_another_case():
    sim = _sim()
    sim.handle_command(b"SET HIGH_DENSITY on")
    assert sim._state.get("high_density") is True


def test_the_simulator_reads_an_enum_word_back_to_its_value():
    sim = _sim()
    reply = sim.handle_command(b"SET ENCRYPTION AUTO")
    assert sim._state.get("encryption") == "auto"
    assert reply == b"REP ENCRYPTION AUTO\r"


def test_a_word_not_in_the_map_is_read_as_it_is():
    sim = _sim()
    sim.handle_command(b"SET GAIN 12")
    assert sim._state.get("gain") == -6
    sim.handle_command(b"SET GAIN MIN")
    assert sim._state.get("gain") == -18
    sim.handle_command(b"SET ENCRYPTION CUSTOM")
    assert sim._state.get("encryption") == "CUSTOM"


def test_a_script_handler_reports_the_setting_in_the_device_word():
    sim = _sim()
    sim.handle_command(b"SET HIGH_DENSITY ON")
    assert sim._script_reply("high_density") == "REP HIGH_DENSITY ON"
    sim.handle_command(b"SET ENCRYPTION MANUAL")
    assert sim._script_reply("encryption") == "REP ENCRYPTION MANUAL"


@pytest.mark.asyncio
async def test_the_driver_reads_the_reply_the_simulator_sends():
    sim = _sim()
    driver = _driver()
    await driver.set_device_setting("high_density", True)
    reply = sim.handle_command(driver.transport.sent[-1].rstrip(b"\r"))
    await driver.on_data_received(reply.rstrip(b"\r"))
    assert driver.get_state("high_density") is True


# ── A write queued while offline is confirmed by the device ──


class _SimulatedDevice(FakeTransport):
    """Hands each write to the simulator and the reply back to the driver."""

    def __init__(self, sim: YAMLAutoSimulator) -> None:
        super().__init__()
        self.sim = sim
        self.driver: Any = None

    async def send(self, data: bytes) -> None:
        await super().send(data)
        reply = self.sim.handle_command(data.rstrip(b"\r"))
        if reply:
            await self.driver.on_data_received(reply.rstrip(b"\r"))


@pytest.fixture
def dm(monkeypatch):
    monkeypatch.setattr(device_manager, "_CONFIRM_UNPOLLED_WINDOW", 0.05)
    monkeypatch.setattr(device_manager, "_CONFIRM_POLL_STEP", 0.01)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state, events)


@pytest.mark.asyncio
async def test_a_queued_mapped_write_is_confirmed_by_the_device_report(dm):
    driver = _driver(state=dm.state, events=dm.events, device_id="dev")
    device = _SimulatedDevice(_sim())
    device.driver = driver
    driver.transport = device
    driver.set_state("connected", True)
    # The device last reported the old values.
    await driver.on_data_received(b"REP HIGH_DENSITY OFF")
    await driver.on_data_received(b"REP ENCRYPTION OFF")
    dm._devices["dev"] = driver
    dm._device_configs["dev"] = {}
    errors: list[dict] = []
    dm.events.on("device.error.dev", lambda n, p: errors.append(p))

    await dm.store_pending_settings(
        "dev", {"high_density": True, "encryption": "Automatic"}
    )
    await dm._apply_pending_settings("dev")
    await dm._pending_confirm_tasks["dev"]

    assert device.sent == [b"SET HIGH_DENSITY ON\r", b"SET ENCRYPTION AUTO\r"]
    assert driver.get_state("high_density") is True
    assert driver.get_state("encryption") == "auto"
    assert "pending_settings" not in dm._device_configs["dev"]
    assert errors == []


# ── Validation ──


def test_the_example_driver_is_valid():
    assert validate_driver_definition(WIDGET, strict=True) == []


def _with_map(setting: str, value_map: Any) -> dict[str, Any]:
    definition = copy.deepcopy(WIDGET)
    definition["device_settings"][setting]["map"] = value_map
    return definition


def test_a_boolean_setting_maps_only_true_and_false():
    errors = validate_driver_definition(
        _with_map("high_density", {"true": "ON", "yes": "ON"})
    )
    assert errors == [
        "Device setting 'high_density': map key 'yes' is not a value a "
        "boolean setting takes; use \"true\" and \"false\""
    ]


def test_an_enum_setting_maps_only_its_declared_values():
    errors = validate_driver_definition(
        _with_map("encryption", {"auto": "AUTO", "Automatic": "AUTO"})
    )
    assert errors == [
        "Device setting 'encryption': map key 'Automatic' is not one of the "
        "setting's values (off, auto, manual)"
    ]


def test_a_bare_yaml_key_or_word_is_refused_with_the_remedy():
    # `true: ON` in YAML loads as {True: True}: both halves need quotes.
    errors = validate_driver_definition(
        _with_map("high_density", {True: "ON", "false": False})
    )
    assert errors == [
        "Device setting 'high_density': map key 'True' is not text; YAML reads "
        "a bare true, false, on, off, yes or no as a boolean. Put it in quotes.",
        "Device setting 'high_density': map word for 'false' is not text; YAML "
        "reads a bare ON, OFF, YES or NO as a boolean. Put it in quotes.",
    ]


def test_a_map_on_a_setting_with_no_write_is_refused():
    definition = copy.deepcopy(WIDGET)
    del definition["device_settings"]["high_density"]["write"]
    errors = validate_driver_definition(definition)
    assert errors == [
        "Device setting 'high_density': missing 'write' block (send / path / "
        "address) — a device setting must be writable"
    ]


def test_a_python_driver_is_refused_the_map():
    info = {
        "id": "acme_widget",
        "state_variables": {"high_density": {"type": "boolean", "label": "High Density"}},
        "device_settings": {
            "high_density": {
                "type": "boolean", "label": "High Density",
                "state_key": "high_density", "map": {"true": "ON"},
            },
        },
    }
    issues = python_driver_info_issues(info)
    assert (
        "Device setting 'high_density': remove map; the platform maps a "
        "setting's value only for YAML drivers, and a Python driver writes "
        "its settings in its own code"
    ) in issues


def test_using_the_map_needs_the_platform_that_understands_it():
    floors = dict(platform_requirements(WIDGET))
    assert floors["device_settings.high_density.map"] == "0.37.0"
    assert floors["device_settings.encryption.map"] == "0.37.0"
