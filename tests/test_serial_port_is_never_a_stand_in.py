"""A serial port opens the port, or is refused: never a stand-in that reads connected.

A port typed as ``SIM:<name>`` opened a stand-in that read connected, sent
nowhere and answered nothing, and so did every serial port on a system without
the serial support package. The Simulator is the one way to run a serial
device without the equipment, so both are refused, with the reason and what to
do instead, at every door that opens a serial port: a device's connect (its
card), the Driver Builder's Live Test (a raw Send and a defined command) and
the device page's Test Connection. The stand-in stays only for a caller that
asks for it outright (``simulate=True``), which is how the transport's own
tests drive it.

Synthetic only: an invented ``/dev/ttyACME0`` and an invented device.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import serial

import openavc.transport.serial_transport as st
from openavc.api.models import TestCommandRequest
from openavc.api.routes import driver_test
from openavc.core.connection_fault import CLIENT_MISSING, INVALID_CONFIG, ConnectionFaultError
from openavc.core.device_manager import DeviceManager
from openavc.core.device_reachability import check_reachability
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.transport.serial_transport import (
    SERIAL_SUPPORT_MISSING_MESSAGE,
    SIM_PORT_MESSAGE,
    SerialTransport,
    serial_port_refusal,
)

PORT = "/dev/ttyACME0"
SIM_PORT = "SIM:projector"


@pytest.fixture
def no_serial_package(monkeypatch):
    """This system as it is without pyserial-asyncio installed."""
    monkeypatch.setattr(st, "HAS_SERIAL", False)


# --- The one rule --------------------------------------------------------------


@pytest.mark.parametrize("port", [SIM_PORT, "SIM:", "sim:projector", " SIM:x"])
def test_a_sim_port_is_refused_as_a_setting(port):
    refused = serial_port_refusal(port)
    assert isinstance(refused, ConnectionFaultError)
    assert refused.fault_code == INVALID_CONFIG
    assert str(refused) == SIM_PORT_MESSAGE


def test_the_sentence_names_the_way_to_test_without_the_device():
    sentence = SIM_PORT_MESSAGE
    assert sentence.startswith("SIM: is not a serial port.")
    assert "Start Simulation" in sentence


@pytest.mark.parametrize("port", [PORT, "COM3", "/dev/ttySIM0", "rfc2217://10.0.0.5:4001"])
def test_a_real_port_is_not_refused(port):
    assert serial_port_refusal(port) is None


def test_a_missing_serial_package_is_refused(no_serial_package):
    refused = serial_port_refusal(PORT)
    assert isinstance(refused, ConnectionFaultError)
    assert refused.fault_code == CLIENT_MISSING
    assert str(refused) == SERIAL_SUPPORT_MISSING_MESSAGE


def test_a_sim_port_is_named_before_the_missing_package(no_serial_package):
    """The setting is what the author can fix in the form in front of them."""
    assert str(serial_port_refusal(SIM_PORT)) == SIM_PORT_MESSAGE


# --- The transport -------------------------------------------------------------


async def test_the_transport_refuses_a_sim_port():
    with pytest.raises(ConnectionFaultError) as caught:
        await SerialTransport.create(
            SIM_PORT, on_data=lambda d: None, on_disconnect=lambda: None,
        )
    assert caught.value.fault_code == INVALID_CONFIG
    assert str(caught.value) == SIM_PORT_MESSAGE


async def test_the_transport_refuses_every_port_without_the_serial_package(no_serial_package):
    with pytest.raises(ConnectionFaultError) as caught:
        await SerialTransport.create(
            PORT, on_data=lambda d: None, on_disconnect=lambda: None,
        )
    assert caught.value.fault_code == CLIENT_MISSING
    assert str(caught.value) == SERIAL_SUPPORT_MISSING_MESSAGE


async def test_the_stand_in_opens_only_when_asked_for(no_serial_package):
    """``simulate=True`` is the transport's own tests' stand-in, with or
    without the serial package, whatever the port is called."""
    for port in (PORT, SIM_PORT):
        transport = await SerialTransport.create(
            port, on_data=lambda d: None, on_disconnect=lambda: None, simulate=True,
        )
        assert transport.connected
        await transport.close()


# --- A device's card -----------------------------------------------------------


class _AcmeSerial(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_serial_stand_in",
        "name": "Acme Serial",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "serial",
        "default_config": {"port": ""},
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
async def device_manager():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    _DRIVER_REGISTRY["acme_serial_stand_in"] = _AcmeSerial
    try:
        yield dm
    finally:
        await dm._cancel_reconnect("w1")
        await dm.disconnect_all()
        _DRIVER_REGISTRY.pop("acme_serial_stand_in", None)


async def _added(dm: DeviceManager, port: str) -> None:
    await dm.add_device({
        "id": "w1", "driver": "acme_serial_stand_in", "name": "W1",
        "config": {"port": port},
    })
    await asyncio.sleep(0.05)


async def test_a_device_on_a_sim_port_is_offline_with_the_sentence(device_manager):
    dm = device_manager
    await _added(dm, SIM_PORT)

    assert dm.state.get("device.w1.connected") is False
    assert dm.state.get("device.w1.offline_reason") == INVALID_CONFIG
    assert dm.state.get("device.w1.offline_detail") == SIM_PORT_MESSAGE


async def test_a_serial_device_without_the_package_is_offline_with_the_sentence(
    device_manager, no_serial_package,
):
    dm = device_manager
    await _added(dm, PORT)

    assert dm.state.get("device.w1.connected") is False
    assert dm.state.get("device.w1.offline_reason") == CLIENT_MISSING
    assert dm.state.get("device.w1.offline_detail") == SERIAL_SUPPORT_MISSING_MESSAGE


# --- The Driver Builder's Live Test --------------------------------------------


def _draft() -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "serial",
        "delimiter": "\\r",
        "default_config": {"port": ""},
        "state_variables": {},
        "commands": {"power_query": {"label": "Power?", "send": "PWR?\\r"}},
    }


async def test_live_tests_raw_send_refuses_a_sim_port():
    result = await driver_test._test_raw(TestCommandRequest(
        transport="serial", port=SIM_PORT, command_string="PWR?", definition=_draft(),
    ))

    assert result["success"] is False
    assert result["received"] == []
    assert result["error"] == SIM_PORT_MESSAGE


async def test_live_tests_defined_command_refuses_a_sim_port():
    result = await driver_test._test_via_configurable_driver(TestCommandRequest(
        transport="serial", port=SIM_PORT, command_name="power_query", definition=_draft(),
    ))

    assert result["success"] is False
    assert result["received"] == []
    assert result["error"] == f"Connect failed: {SIM_PORT_MESSAGE}"


async def test_live_tests_raw_send_refuses_without_the_package(no_serial_package):
    result = await driver_test._test_raw(TestCommandRequest(
        transport="serial", port=PORT, command_string="PWR?", definition=_draft(),
    ))

    assert result["success"] is False
    assert result["error"] == SERIAL_SUPPORT_MISSING_MESSAGE


# --- Test Connection -----------------------------------------------------------


@pytest.fixture
def probed(monkeypatch):
    """Each port serial.Serial was asked to open."""
    opened: list[str] = []

    class _Port:
        def __init__(self, port: str, *args: Any, **kwargs: Any) -> None:
            opened.append(port)

        def close(self) -> None:
            return None

    monkeypatch.setattr(serial, "Serial", _Port)
    return opened


def _device(port: str) -> dict[str, Any]:
    return {
        "id": "widget_a",
        "driver": "acme_widget",
        "name": "Widget A",
        "config": {"transport": "serial", "port": port},
    }


async def test_test_connection_refuses_a_sim_port_in_the_cards_words(probed):
    result = await check_reachability(_device(SIM_PORT))

    assert result["success"] is False
    assert result["error"] == SIM_PORT_MESSAGE
    assert probed == []


async def test_test_connection_refuses_without_the_package(probed, no_serial_package):
    result = await check_reachability(_device(PORT))

    assert result["success"] is False
    assert result["error"] == SERIAL_SUPPORT_MISSING_MESSAGE
    assert probed == []


async def test_test_connection_still_opens_a_real_port(probed):
    result = await check_reachability(_device(PORT))

    assert result["success"] is True, result
    assert probed == [PORT]
