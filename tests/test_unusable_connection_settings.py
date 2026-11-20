"""A connection setting that can never work is refused before anything is dialed.

A parity of "X", nine data bits, a line ending that is not text, a port that
is not a number: each fails while the device's connection is being built from
its config, before the first socket is opened, and retrying can never change
the answer. Every one of them reaches the device card as ``invalid_config``,
naming the setting and what it may be, and the device manager stops retrying
it. A failure once dialing starts is still the transport's to word.

An invented device throughout.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openavc.core import device_reachability
from openavc.core.connection_fault import (
    INVALID_CONFIG,
    classify_connection_fault,
    is_permanent_fault,
    no_port_message,
    no_serial_port_message,
)
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectionFaultError
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.transport.frame_parsers import build_frame_parser

DRIVER_ID = "acme_widget_unusable"


class _AcmeWidget(BaseDriver):
    """BaseDriver's own connect(), so the platform's transport ladder runs."""

    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER_ID,
        "name": "Acme Widget",
        "transport": "serial",
        "default_config": {},
        "config_schema": {},
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _driver(config: dict, *, transport: str = "serial", **info: Any) -> BaseDriver:
    merged = {**_AcmeWidget.DRIVER_INFO, "transport": transport, **info}
    sub = type("_Widget", (_AcmeWidget,), {"DRIVER_INFO": merged})
    return sub("w1", config, StateStore(), EventBus())


async def _refused(driver: BaseDriver, dial: str) -> ConnectionFaultError:
    """Connect, expecting an invalid_config refusal with nothing dialed."""
    opened = AsyncMock()
    with patch(dial, opened):
        with pytest.raises(ConnectionFaultError) as caught:
            await driver.connect()
    assert caught.value.fault_code == INVALID_CONFIG
    assert opened.await_count == 0
    return caught.value


SERIAL = "openavc.transport.serial_transport.SerialTransport.create"
TCP = "openavc.transport.tcp.TCPTransport.create"


# --- serial line settings -------------------------------------------------------


@pytest.mark.parametrize(
    "setting, value, label, remedy",
    [
        ("parity", "X", "Parity", "N (none), E (even), O (odd), M (mark) or S (space)"),
        ("bytesize", 9, "Data bits", "5, 6, 7 or 8"),
        ("bytesize", "eight", "Data bits", "5, 6, 7 or 8"),
        ("stopbits", 3, "Stop bits", "1, 1.5 or 2"),
        ("stopbits", "one", "Stop bits", "1, 1.5 or 2"),
        ("baudrate", "fast", "Baud rate", "a whole number, such as 9600"),
    ],
)
async def test_a_serial_line_setting_that_cannot_work_names_itself(
    setting, value, label, remedy,
):
    """Whether the device set it or its driver's default did (merged into
    every device's config), the card names the setting and what to choose."""
    refusal = await _refused(_driver({"port": "/dev/ttyUSB0", setting: value}), SERIAL)
    assert label in str(refusal)
    assert repr(value) in str(refusal)
    assert remedy in str(refusal)


@pytest.mark.parametrize("port", ["", "   ", None])
async def test_a_serial_device_with_no_port_says_so(port):
    """The card says what the connection test says, instead of "Can't open
    serial port" and a retry that never ends."""
    refusal = await _refused(_driver({"port": port}), SERIAL)
    assert str(refusal) == no_serial_port_message()


def test_the_card_and_the_connection_test_name_the_missing_ports_alike():
    assert device_reachability._NO_SERIAL_PORT == no_serial_port_message()
    assert device_reachability._NO_PORT == no_port_message()


# --- the line ending and message framing ------------------------------------------


@pytest.mark.parametrize("delimiter", [13, [13, 10], 0, False])
async def test_a_line_ending_in_the_device_config_that_is_not_text(delimiter):
    refusal = await _refused(
        _driver({"host": "192.0.2.10", "port": 5500, "delimiter": delimiter}, transport="tcp"),
        TCP,
    )
    assert "line ending" in str(refusal)
    assert repr(delimiter) in str(refusal)


async def test_a_line_ending_in_the_driver_that_is_not_text():
    """The driver's own delimiter wins over the device's, so the device page
    cannot fix it: the sentence says it is the driver's."""
    refusal = await _refused(
        _driver({"host": "192.0.2.10", "port": 5500}, transport="tcp", delimiter=13),
        TCP,
    )
    assert "driver" in str(refusal)
    assert "13" in str(refusal)


async def test_a_frame_parser_a_python_driver_cannot_build():
    class _Framed(_AcmeWidget):
        DRIVER_INFO = {**_AcmeWidget.DRIVER_INFO, "transport": "tcp"}

        def _create_frame_parser(self):
            return build_frame_parser({"type": "length_prefix", "header_size": 3})

    refusal = await _refused(_Framed("w1", {"host": "192.0.2.10", "port": 5500},
                                     StateStore(), EventBus()), TCP)
    assert "framing" in str(refusal)
    assert "header_size must be 1, 2, or 4" in str(refusal)


@pytest.mark.parametrize(
    "frame_parser, reason",
    [
        ({"type": "length_prefix", "header_size": 3}, "header_size must be 1, 2, or 4"),
        ({"type": "fixed_length", "length": 0}, "length must be a positive integer (got 0)"),
        ({"type": "struct_frame", "length_size": 3}, "length_size must be 1, 2, or 4"),
    ],
)
async def test_a_frame_parser_a_yaml_driver_declares_that_cannot_be_built(frame_parser, reason):
    from openavc.drivers.configurable import create_configurable_driver_class

    cls = create_configurable_driver_class({
        "id": "acme_widget_framed", "name": "Acme Widget", "manufacturer": "Acme",
        "category": "utility", "version": "1.0.0", "transport": "tcp",
        "default_config": {"host": "192.0.2.10", "port": 5500}, "config_schema": {},
        "frame_parser": frame_parser, "state_variables": {}, "responses": [],
        "commands": {},
    })
    refusal = await _refused(cls("w1", {"host": "192.0.2.10", "port": 5500},
                                 StateStore(), EventBus()), TCP)
    assert "framing" in str(refusal)
    assert reason in str(refusal)


# --- the port ---------------------------------------------------------------------


@pytest.mark.parametrize("port", [None, ""])
async def test_a_network_device_with_no_port(port):
    refusal = await _refused(_driver({"host": "192.0.2.10", "port": port}, transport="tcp"), TCP)
    assert str(refusal) == no_port_message()


@pytest.mark.parametrize("port", ["telnet", "4999x", -1, 70000])
async def test_a_port_that_is_not_a_port_number(port):
    refusal = await _refused(_driver({"host": "192.0.2.10", "port": port}, transport="tcp"), TCP)
    assert repr(port) in str(refusal)
    assert "1 to 65535" in str(refusal)


@pytest.mark.parametrize(
    "transport, dial",
    [
        ("ssh", "openavc.transport.ssh.SSHTransport.create"),
        ("mqtt", "openavc.transport.mqtt.MQTTTransport.create"),
        ("snmp", "openavc.transport.snmp.SNMPTransport.open"),
    ],
)
async def test_a_port_that_is_not_a_number_on_the_other_transports(
    transport, dial, monkeypatch, tmp_path,
):
    monkeypatch.setattr("openavc.system_config.get_data_dir", lambda: tmp_path)
    driver = _driver(
        {"host": "192.0.2.10", "port": "telnet", "username": "admin",
         "ssh_auth_method": "password"},
        transport=transport,
    )
    refusal = await _refused(driver, dial)
    assert "'telnet'" in str(refusal)


async def test_a_transport_openavc_does_not_have():
    refusal = await _refused(
        _driver({"host": "192.0.2.10", "port": 5500, "transport": "carrier_pigeon"}),
        TCP,
    )
    assert "'carrier_pigeon'" in str(refusal)


# --- what the guard covers --------------------------------------------------------


async def test_a_driver_hook_that_refuses_its_arguments():
    """A driver's _transport_kwargs runs before anything is dialed too."""

    class _Picky(_AcmeWidget):
        DRIVER_INFO = {**_AcmeWidget.DRIVER_INFO, "transport": "tcp"}

        def _transport_kwargs(self, transport_type, kwargs):
            raise ValueError("timeout must be a number of seconds")

    refusal = await _refused(_Picky("w1", {"host": "192.0.2.10", "port": 5500},
                                    StateStore(), EventBus()), TCP)
    assert "timeout must be a number of seconds" in str(refusal)


async def test_a_failure_once_dialing_starts_keeps_its_own_reason():
    """The guard ends where the dial begins: a refused port is still a
    refused port, retried as a network condition."""
    driver = _driver({"host": "192.0.2.10", "port": 5500}, transport="tcp")
    refused = ConnectionRefusedError("Connection refused")
    with patch(TCP, AsyncMock(side_effect=refused)):
        with pytest.raises(ConnectionRefusedError) as caught:
            await driver.connect()
    fault = classify_connection_fault(
        last_error="", exc=caught.value, host="192.0.2.10", port=5500, transport="tcp",
    )
    assert fault.code == "connection_refused"


async def test_an_adapter_with_no_address_still_reads_unreachable(monkeypatch):
    """A refusal already typed passes through the guard as it is."""

    def no_address(host):
        raise ConnectionFaultError("The chosen adapter has no address", code="unreachable")

    monkeypatch.setattr("openavc.transport.endpoint.control_bind_address", no_address)
    driver = _driver({"host": "192.0.2.10", "port": 5500}, transport="tcp")
    with patch(TCP, AsyncMock()) as opened:
        with pytest.raises(ConnectionFaultError) as caught:
            await driver.connect()
    assert caught.value.fault_code == "unreachable"
    assert opened.await_count == 0


def test_the_classifier_reads_the_refusal_as_permanent():
    fault = classify_connection_fault(
        last_error="", exc=ConnectionFaultError("x", code=INVALID_CONFIG),
        host="", port="/dev/ttyUSB0", transport="serial",
    )
    assert fault.code == INVALID_CONFIG
    assert is_permanent_fault(fault.code)


# --- on the device card ------------------------------------------------------------


async def test_the_card_names_the_setting_and_retrying_stops(monkeypatch):
    """The card names the setting, nothing is dialed, and the device manager
    gives up instead of retrying forever."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    monkeypatch.setattr(dm, "_reconnect_delay", lambda device_id, attempt: 0.0)
    _DRIVER_REGISTRY[DRIVER_ID] = _AcmeWidget
    dial = AsyncMock()
    try:
        with patch(SERIAL, dial):
            await dm.add_device({
                "id": "w1", "driver": DRIVER_ID, "name": "W1",
                "config": {"port": "/dev/ttyUSB0", "parity": "X"},
            })
            for _ in range(100):
                if dm.state.get("device.w1.reconnect_failed"):
                    break
                await asyncio.sleep(0.01)
        assert dial.await_count == 0
        assert dm.state.get("device.w1.connected") is False
        assert dm.state.get("device.w1.offline_reason") == INVALID_CONFIG
        assert "Parity" in dm.state.get("device.w1.offline_detail")
        assert dm.state.get("device.w1.reconnect_failed") is True
        assert "w1" not in dm._reconnect_tasks
    finally:
        await dm._cancel_reconnect("w1")
        _DRIVER_REGISTRY.pop(DRIVER_ID, None)
