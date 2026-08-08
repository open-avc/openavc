"""Tests for {value, label} enum command params (human-readable labels).

A static enum param may carry {value, label} entries; the runtime accepts
either the label or the wire value from any caller and normalizes to the wire
value before it goes on the wire. Uses an invented receiver so no real product
is named.
"""

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import (
    DeviceSettingValueError,
    normalize_and_validate_command_params,
    validate_device_setting_value,
)
from openavc.drivers.configurable import create_configurable_driver_class

DSP_OPTIONS = [
    {"value": "00", "label": "Stereo"},
    {"value": "0f", "label": "Multi Channel Stereo"},
    "ff",  # plain-string option (value == label)
]


def _norm(value, options=DSP_OPTIONS, ptype="enum"):
    defs = {"mode": {"type": ptype, "values": options}}
    return normalize_and_validate_command_params("set_dsp", defs, {"mode": value})["mode"]


def test_label_maps_to_wire_value():
    assert _norm("Multi Channel Stereo") == "0f"


def test_wire_value_passes_through():
    assert _norm("0f") == "0f"


def test_plain_string_option_passes_through():
    assert _norm("ff") == "ff"


def test_unrecognized_value_passes_through():
    # Forgiving: a $var may resolve to a computed wire value outside the list.
    assert _norm("7a") == "7a"


def test_string_typed_param_with_enum_maps_too():
    assert _norm("Stereo", ptype="string") == "00"


def test_plain_string_enum_unaffected():
    opts = ["PWR00", "PWR01"]
    assert _norm("PWR01", options=opts) == "PWR01"
    assert _norm("PWR00", options=opts) == "PWR00"


# ---------------------------------------------------------------------------
# End to end through a driver — labels compose with command framing.
# ---------------------------------------------------------------------------

ACME_RECEIVER = {
    "id": "acme_receiver",
    "name": "Acme Receiver",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "tcp",
    "command_prefix": "!1",
    "command_suffix": "\\r",
    "default_config": {"host": "", "port": 60128},
    "config_schema": {"host": {"type": "string", "required": True, "label": "IP"}},
    "state_variables": {},
    "commands": {
        "set_dsp": {
            "label": "DSP Mode",
            "send": "LMD{mode}",
            "params": {
                "mode": {
                    "type": "enum",
                    "required": True,
                    "values": [
                        {"value": "00", "label": "Stereo"},
                        {"value": "0f", "label": "Multi Channel Stereo"},
                    ],
                },
            },
        },
    },
}


class FakeTransport:
    connected = True

    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _make_driver(definition=ACME_RECEIVER, config=None):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", config or {"host": "127.0.0.1", "port": 60128}, state, events)
    driver.transport = FakeTransport()
    return driver


async def test_send_with_label_puts_wire_value_on_the_wire():
    driver = _make_driver()
    await driver.send_command("set_dsp", {"mode": "Multi Channel Stereo"})
    assert driver.transport.sent == [b"!1LMD0f\r"]


async def test_send_with_wire_value_still_works():
    driver = _make_driver()
    await driver.send_command("set_dsp", {"mode": "0f"})
    assert driver.transport.sent == [b"!1LMD0f\r"]


# ---------------------------------------------------------------------------
# Device settings — the write-side half. Same {value, label} treatment, but a
# setting is persisted config, so an unrecognized value is rejected (no
# forgiving-free-text path like a command picker).
# ---------------------------------------------------------------------------

DS_ENUM = {
    "type": "enum",
    "values": [
        {"value": "00", "label": "Stereo"},
        {"value": "0f", "label": "Multi Channel Stereo"},
        "ff",  # plain-string option
    ],
}


def test_setting_label_maps_to_wire_value():
    assert validate_device_setting_value("dsp", DS_ENUM, "Multi Channel Stereo") == "0f"


def test_setting_wire_value_passes_through():
    assert validate_device_setting_value("dsp", DS_ENUM, "0f") == "0f"


def test_setting_plain_string_option_passes_through():
    assert validate_device_setting_value("dsp", DS_ENUM, "ff") == "ff"


def test_setting_unrecognized_value_rejected():
    with pytest.raises(DeviceSettingValueError):
        validate_device_setting_value("dsp", DS_ENUM, "7a")


def test_setting_plain_string_enum_unaffected():
    sdef = {"type": "enum", "values": ["auto", "manual"]}
    assert validate_device_setting_value("mode", sdef, "manual") == "manual"
    with pytest.raises(DeviceSettingValueError):
        validate_device_setting_value("mode", sdef, "nope")
