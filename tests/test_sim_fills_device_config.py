"""The simulated device fills a driver's templates with the device's settings, as the driver does.

A YAML driver's templates may name a config field: a display's address in
every command (``{set_id:02d}PWR1``) and in every reply rule, a prefix a
``config_derived`` entry builds from another field, an OSC address under a
workspace. The running driver fills each from the device's config: the
driver's defaults, the device's own settings over them, then the derived
values worked out from both. The auto-generated simulator reads the same
templates to recognize what arrives and to write what it answers, so it has
to fill them from the same config, or it matches ``{set_id:02d}PWR1`` as
literal text and answers nothing the driver sends.

Each case here runs one invented device through both readers: the driver's
own bytes (``build_wire``, or its OSC address) into the simulator, and the
simulator's answer back through the driver's reply rules. A command's own
parameter fills before a config field of the same name, as on the driver's
send path.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.driver_sends import effective_config
from openavc.simulator.validate import validate_yaml_driver
from openavc.simulator.yaml_auto import YAMLAutoSimulator


def _widget() -> dict[str, Any]:
    """An addressed display: every line carries the unit's set id, and a zone
    prefix that is there only when a zone is set (``config_derived``)."""
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "display",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 5000, "set_id": 1, "zone": ""},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
            "set_id": {"type": "integer", "default": 1, "label": "Set ID"},
            "zone": {"type": "string", "default": "", "label": "Zone"},
        },
        "config_derived": {"zone_prefix": "Z{zone}:"},
        "state_variables": {
            "power": {"type": "enum", "values": ["on", "off"], "label": "Power"},
            "level": {"type": "integer", "label": "Level"},
            "tap": {"type": "integer", "label": "Tap"},
        },
        "commands": {
            "power_on": {"send": "{set_id:02d}PWR on\r", "sets": {"power": "on"},
                         "query_for": "power"},
            "set_level": {
                "send": "{zone_prefix}{set_id:02d}LVL{level}\r",
                "params": {"level": {"type": "integer", "min": 0, "max": 100,
                                     "required": True}},
                "sets": {"level": "{level}"},
                "query_for": "level",
            },
            # The command's own parameter is named like a config field: the
            # parameter's value is what goes on the wire.
            "set_tap": {
                "send": "TAP{port}\r",
                "params": {"port": {"type": "integer", "min": 1, "max": 8,
                                    "required": True}},
                "sets": {"tap": "{port}"},
                "query_for": "tap",
            },
        },
        "responses": [
            {"match": r"{set_id:02d}PWR (on|off)", "set": {"power": "$1"}},
            {"match": r"{zone_prefix}{set_id:02d}LVL(\d+)", "set": {"level": "$1"}},
            {"match": r"TAP(\d+)", "set": {"tap": "$1"}},
        ],
        "polling": {
            "interval": 5,
            "queries": [{"send": "{zone_prefix}{set_id:02d}LVL?\r", "query_for": "level"}],
        },
    }


def _driver(definition: dict[str, Any], device: dict[str, Any]) -> Any:
    """The running driver, with the config the platform resolves for it: the
    driver's defaults under the device's own settings."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    config = {**definition["default_config"], "host": "127.0.0.1", **device}
    return cls("w1", config, state, events)


def _sim(definition: dict[str, Any], device: dict[str, Any]) -> YAMLAutoSimulator:
    return YAMLAutoSimulator("w1", dict(device), driver_def=definition)


async def _round_trip(
    definition: dict[str, Any], device: dict[str, Any], command: str, params: dict[str, Any]
) -> tuple[Any, bytes | None]:
    """The driver's bytes for ``command`` answered by the simulator, and the
    answer read back by the driver's reply rules; the driver and the reply."""
    driver = _driver(definition, device)
    sim = _sim(definition, device)
    wire = driver.build_wire(command, params)
    reply = sim.handle_command(wire)
    if reply:
        for line in reply.split(b"\r"):
            if line:
                await driver.on_data_received(line)
    return driver, reply


DEVICES = [
    pytest.param({}, id="the-driver-defaults"),
    pytest.param({"set_id": 7}, id="the-device-own-set-id"),
    pytest.param({"set_id": 12, "zone": "A"}, id="a-zone-builds-the-derived-prefix"),
]


@pytest.mark.parametrize("device", DEVICES)
async def test_a_command_naming_a_config_field_is_recognized_and_its_reply_read(device):
    driver, reply = await _round_trip(_widget(), device, "power_on", {})
    set_id = device.get("set_id", 1)
    assert reply == f"{set_id:02d}PWR on\r".encode()
    assert driver.get_state("power") == "on"


@pytest.mark.parametrize("device", DEVICES)
async def test_a_derived_prefix_fills_the_command_the_reply_and_the_poll_line(device):
    driver, reply = await _round_trip(_widget(), device, "set_level", {"level": 42})
    prefix = f"Z{device['zone']}:" if device.get("zone") else ""
    line = f"{prefix}{device.get('set_id', 1):02d}LVL"
    assert reply == f"{line}42\r".encode()
    assert driver.get_state("level") == 42

    sim = _sim(_widget(), device)
    sim.handle_command(driver.build_wire("set_level", {"level": 9}))
    assert sim.handle_command(f"{line}?\r".encode()) == f"{line}9\r".encode()


async def test_a_parameter_named_like_a_config_field_fills_its_own_value():
    """``{port}`` is the command's parameter (3), not the connection's port
    (5000): the driver sends TAP3, and the simulator reads 3 from it."""
    driver, reply = await _round_trip(_widget(), {}, "set_tap", {"port": 3})
    assert driver.build_wire("set_tap", {"port": 3}) == b"TAP3\r"
    assert reply == b"TAP3\r"
    assert driver.get_state("tap") == 3


@pytest.mark.parametrize("device, payload", [({}, "01WAKE"), ({"set_id": 7}, "07WAKE")])
def test_a_side_send_payload_naming_a_config_field_is_matched_as_sent(device, payload):
    definition = _widget()
    definition["commands"]["wake"] = {"udp": {"payload": "{set_id:02d}WAKE", "port": 9}}
    handlers = {h.name: h for h in _sim(definition, device)._command_handlers}
    assert handlers["wake"].pattern.fullmatch(payload)


def test_effective_config_layers_defaults_the_device_and_the_derived_values():
    definition = _widget()
    assert effective_config(definition)["zone_prefix"] == ""
    config = effective_config(definition, {"zone": "B", "set_id": 4})
    assert (config["set_id"], config["zone"], config["zone_prefix"]) == (4, "B", "ZB:")
    # A derived value is worked out after the device's settings go in, so a
    # device that names the derived field itself is overwritten, as the
    # driver overwrites it.
    assert effective_config(definition, {"zone_prefix": "X"})["zone_prefix"] == ""
    # A new dict each time: a caller may change its copy.
    assert effective_config(definition) is not effective_config(definition)


def test_the_validator_passes_a_driver_whose_commands_name_config_fields(tmp_path):
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(_widget(), sort_keys=False), encoding="utf-8")
    result = validate_yaml_driver(path)
    assert [issue.message for issue in result.errors] == []


def _osc_console() -> dict[str, Any]:
    """An OSC console whose addresses sit under a workspace, the prefix
    there only when a workspace id is set."""
    return {
        "id": "acme_console",
        "name": "Acme Console",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": "osc",
        "default_config": {"host": "", "port": 53000, "workspace_id": ""},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 53000, "label": "Port"},
            "workspace_id": {"type": "string", "default": "", "label": "Workspace"},
        },
        "config_derived": {"ws": "/workspace/{workspace_id}"},
        "state_variables": {"fader": {"type": "number", "label": "Fader"}},
        "commands": {
            "set_fader": {
                "address": "{ws}/fader",
                "args": [{"type": "f", "value": "{level}"}],
                "params": {"level": {"type": "number", "min": 0, "max": 1,
                                     "required": True}},
            },
        },
        "responses": [
            {"address": "{ws}/fader", "mappings": [{"arg": 0, "state": "fader",
                                                     "type": "float"}]},
        ],
    }


@pytest.mark.parametrize("device", [
    pytest.param({}, id="no-workspace"),
    pytest.param({"workspace_id": "7"}, id="under-a-workspace"),
])
def test_an_osc_address_under_a_derived_prefix_is_answered_and_read_back(device):
    definition = _osc_console()
    driver = _driver(definition, device)
    sim = _sim(definition, device)
    address = driver._safe_substitute(
        definition["commands"]["set_fader"]["address"], driver.config
    )
    assert address == ("/workspace/7/fader" if device else "/fader")
    answer = sim.handle_message(address, [("f", 0.5)])
    assert answer == [(address, [("f", 0.5)])]
    assert sim.get_state("fader") == 0.5
    assert driver._apply_osc_message(address, [("f", 0.25)])
    assert driver.get_state("fader") == 0.25


@pytest.mark.parametrize("workspace", ["", "7"])
def test_the_validator_passes_an_osc_driver_whose_addresses_name_config(tmp_path, workspace):
    definition = _osc_console()
    definition["default_config"]["workspace_id"] = workspace
    path = tmp_path / "acme_console.avcdriver"
    path.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
    result = validate_yaml_driver(path)
    assert [issue.message for issue in result.errors] == []
