"""A wake with no usable MAC address, as each door says it.

A YAML Wake-on-LAN command (``udp: {magic_packet: <field>}``) on a device
whose MAC address is not filled in yet, or is not a MAC address, is refused
before anything is sent. Somebody at a panel can open no setting, so a panel
press and a macro say the device's setup is not finished. The Programmer, the
Send Command card and a device action name the device and the setting to fill
in, as Edit Device shows it: no device id, no setting key, no "Invalid value".

The refusals come from a real YAML driver for an invented device (Acme), so a
door is tested with what a driver actually raises.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from openavc import reach_devices
from openavc.api import rest, ws
from openavc.api.error_messages import friendly_error
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import load_project
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.main import app

FOR_THE_ROOM = "Lobby Display can't be turned on until its setup is finished. Contact support."
FILL_IT_IN = (
    "Lobby Display can't be woken yet: MAC Address (for Wake-on-LAN) is empty. "
    "It is filled in when Lobby Display connects; until then, enter it under "
    "Edit Device."
)


def _definition(*, reported: str = "state", label: str | None = "MAC Address (for Wake-on-LAN)"):
    """An Acme display whose Power On is a Wake-on-LAN packet.

    ``reported``: how the driver learns the MAC from the device -- "state" (a
    state variable of the same name), "learned" (the field is learned_from
    one) or "" (it does not).
    """
    mac_field: dict = {"type": "string"}
    if label is not None:
        mac_field["label"] = label
    state_variables = {"power": {"type": "boolean", "label": "Power"}}
    if reported == "state":
        state_variables["mac_address"] = {"type": "string", "label": "MAC Address"}
    elif reported == "learned":
        state_variables["reported_mac"] = {"type": "string", "label": "Reported MAC"}
        mac_field["learned_from"] = "reported_mac"
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "display",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 5000, "mac_address": ""},
        "config_schema": {"host": {"type": "string"}, "mac_address": mac_field},
        "state_variables": state_variables,
        "commands": {
            "power_on": {
                "label": "Power On",
                "available_offline": True,
                "udp": {"magic_packet": "mac_address"},
                "sets": {"power": True},
            },
        },
    }


class _CaptureUdp:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str, int]] = []

    async def send_to(self, data: bytes, host: str, port: int) -> None:
        self.sent.append((bytes(data), host, port))

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_adapters(monkeypatch):
    monkeypatch.setattr(reach_devices, "broadcast_sources", lambda: [])
    monkeypatch.setattr(reach_devices, "pinned", lambda: None)


def _driver(mac: str = "", **definition):
    cls = create_configurable_driver_class(_definition(**definition))
    state = StateStore()
    state.set_event_bus(EventBus())
    drv = cls("acme_display", {"host": "10.0.0.5", "port": 5000, "mac_address": mac},
              state, EventBus())
    capture = _CaptureUdp()

    async def _open(local_addr=None):
        return capture

    drv._open_udp = _open
    return drv, capture


def _refusal(mac: str = "", **definition) -> Exception:
    """What the driver raises for Power On, and that it sent nothing."""
    drv, capture = _driver(mac, **definition)
    with pytest.raises(ValueError) as caught:
        asyncio.run(drv.send_command("power_on"))
    assert capture.sent == []
    return caught.value


# ── What the Programmer is told ────────────────────────────────────────────


def test_an_empty_mac_names_the_setting_and_says_it_fills_itself_in():
    assert friendly_error(_refusal(), device="Lobby Display") == FILL_IT_IN


def test_a_field_learned_from_the_device_fills_itself_in_too():
    assert friendly_error(_refusal(reported="learned"), device="Lobby Display") == FILL_IT_IN


def test_a_mac_nothing_reports_is_only_to_be_entered():
    assert friendly_error(_refusal(reported=""), device="Lobby Display") == (
        "Lobby Display can't be woken: MAC Address (for Wake-on-LAN) is empty. "
        "Enter it under Edit Device."
    )


def test_a_field_with_no_label_is_named_from_its_key():
    assert friendly_error(_refusal(label=None, reported=""), device="Lobby Display") == (
        "Lobby Display can't be woken: MAC Address is empty. Enter it under Edit Device."
    )


def test_a_typed_value_that_is_not_a_mac_is_quoted_with_the_shape_expected():
    assert friendly_error(_refusal("kettle"), device="Lobby Display") == (
        "Lobby Display can't be woken: MAC Address (for Wake-on-LAN) is 'kettle', "
        "which is not a MAC address (six pairs of hex digits, like "
        "aa:bb:cc:dd:ee:ff). Correct it under Edit Device."
    )


def test_a_reported_mac_that_is_not_one_gives_way_to_the_typed_one():
    """The all-zero address a device reports before it knows its own."""
    drv, capture = _driver("AA-BB-CC-DD-EE-FF")
    drv.set_state("mac_address", "00:00:00:00:00:00")
    assert asyncio.run(drv.send_command("power_on")) is True
    assert capture.sent[0][0][6:12] == bytes.fromhex("aabbccddeeff")


def test_no_door_needs_a_device_name_to_say_it():
    assert friendly_error(_refusal(reported="")) == (
        "The device can't be woken: MAC Address (for Wake-on-LAN) is empty. "
        "Enter it under Edit Device."
    )
    assert friendly_error(_refusal()) == str(_refusal()) == (
        "The device can't be woken yet: MAC Address (for Wake-on-LAN) is empty. "
        "It is filled in when the device connects; until then, enter it under "
        "Edit Device."
    )


def test_a_python_drivers_own_sentence_is_kept_for_the_programmer():
    from openavc.drivers.base import WakeAddressError

    own = WakeAddressError("The TV cannot be woken: no MAC address is set.")
    assert friendly_error(own, device="Lobby Display") == (
        "The TV cannot be woken: no MAC address is set."
    )
    assert friendly_error(own, device="Lobby Display", at_panel=True) == FOR_THE_ROOM


def test_the_platforms_wake_refuses_a_value_that_is_not_a_mac_the_same_way():
    """A Python driver handing BaseDriver.wake_on_lan a bad MAC."""
    drv, capture = _driver()
    with pytest.raises(ValueError) as caught:
        asyncio.run(drv.wake_on_lan("kettle"))
    assert capture.sent == []
    assert friendly_error(caught.value, device="Lobby Display", at_panel=True) == FOR_THE_ROOM


# ── Each door ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0",
        "project": {"id": "test", "name": "Test"},
        "devices": [{"id": "acme_display", "name": "Lobby Display", "driver": "acme_widget"}],
        "macros": [{"id": "wake", "name": "Wake", "steps": [
            {"action": "device.command", "device": "acme_display", "command": "power_on"},
        ]}],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [{
            "id": "wake", "type": "button", "bindings": {"do": {"press": [{
                "action": "device.command", "device": "acme_display", "command": "power_on",
            }]}},
        }]}]},
    }))
    result = Engine(str(path))
    result.project = load_project(path)
    result.macros.load_macros([m.model_dump() for m in result.project.macros])
    result.devices.send_command = AsyncMock(side_effect=_refusal())
    return result


@pytest.mark.asyncio
async def test_a_panel_press_is_told_the_setup_is_not_finished(engine):
    result = await engine.handle_ui_event("press", "wake")
    assert result[0]["sent"] is False
    assert result[0]["error"] == FOR_THE_ROOM


@pytest.mark.asyncio
async def test_a_macro_step_says_it_for_the_room(engine):
    errors = []
    engine.events.on("macro.step_error.*", lambda event, payload: errors.append(payload))
    await engine.macros.execute("wake")
    assert len(errors) == 1
    assert errors[0]["message"] == FOR_THE_ROOM


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type,expected", [
    ("panel", FOR_THE_ROOM),
    ("programmer", FILL_IT_IN),
])
async def test_a_direct_command_answers_whoever_sent_it(engine, monkeypatch, client_type, expected):
    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {"type": "command", "device_id": "acme_display",
                                   "command": "power_on"}, client_type=client_type)
    assert sent.await_args.args[1]["error"] == expected


@pytest.fixture
def client(engine):
    rest.set_engine(engine)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        rest.set_engine(None)


def test_the_send_command_card_names_the_device_and_the_setting(client):
    resp = client.post("/api/devices/acme_display/command", json={"command": "power_on"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == FILL_IT_IN


def test_a_device_action_answers_the_same(client, engine):
    from unittest.mock import MagicMock

    driver = MagicMock()
    driver.DRIVER_INFO = {
        "commands": {"power_on": {"label": "Power On", "params": {}}},
        "actions": [{"id": "power_on", "kind": "command"}],
    }
    engine.devices.get_driver = MagicMock(return_value=driver)
    resp = client.post("/api/devices/acme_display/actions/power_on", json={"params": {}})
    assert resp.status_code == 409
    assert resp.json()["detail"] == FILL_IT_IN
