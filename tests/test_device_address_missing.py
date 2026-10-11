"""A network device with no address says so, dials nothing, and is not retried.

A blank address does not fail to dial: it reaches this computer, so the device
card read "Connection refused on 5500. Is the service enabled and the port
correct?" for a device nobody had given an address. BaseDriver.connect() now
refuses it before any transport or driver-owned session is built, with the
connection test's own sentence, and the device manager starts no retry loop
for it. Only a driver that marks its ``host`` field required is asked; serial,
bridge-routed and IR devices dial no address. An invented device throughout.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openavc.core import device_reachability
from openavc.core.connection_fault import no_address_message
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectionFaultError
from openavc.drivers.registry import _DRIVER_REGISTRY

NO_ADDRESS_MESSAGE = no_address_message()
HOST_REQUIRED = {"host": {"type": "string", "required": True, "label": "IP Address"}}


class _AcmeWidget(BaseDriver):
    """BaseDriver's own connect(), so the platform's checks run."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_no_address",
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5500},
        "config_schema": HOST_REQUIRED,
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _AcmeOwnSession(_AcmeWidget):
    """Builds its own session, the way a websocket or raw-client driver does."""

    built = 0

    async def _create_transport(self, transport_type: str) -> None:
        type(self).built += 1


def _driver(config: dict, *, transport: str = "tcp", schema: Any = HOST_REQUIRED,
            cls: type = _AcmeWidget) -> BaseDriver:
    info = {**cls.DRIVER_INFO, "transport": transport, "config_schema": schema}
    sub = type("_Widget", (cls,), {"DRIVER_INFO": info})
    return sub("w1", config, StateStore(), EventBus())


@pytest.mark.parametrize("transport", ["tcp", "udp", "osc", "http", "ssh", "mqtt", "snmp"])
@pytest.mark.parametrize("host", ["", "   ", None])
def test_a_network_device_with_no_address_is_missing_one(transport, host):
    assert _driver({"host": host, "port": 5500}, transport=transport).address_missing()


@pytest.mark.parametrize(
    "config, transport, schema",
    [
        ({"host": "10.0.0.5"}, "tcp", HOST_REQUIRED),
        ({"port": "/dev/ttyUSB0"}, "serial", HOST_REQUIRED),
        ({"transport": "serial", "port": "COM3"}, "tcp", HOST_REQUIRED),
        ({"bridge": "itach1", "bridge_port": "ir:1"}, "bridge", HOST_REQUIRED),
        # A blank address that means something: a multicast default.
        ({"host": ""}, "udp", {"host": {"type": "string", "label": "Unicast Address"}}),
        # A driver with no address field at all: a broadcast.
        ({}, "udp", {}),
    ],
)
def test_no_address_is_asked_where_none_is_dialed(config, transport, schema):
    assert not _driver(config, transport=transport, schema=schema).address_missing()


def test_a_simulated_device_has_the_simulators_address():
    """Simulation points the live config at the simulator; that is dialed."""
    driver = _driver({"host": "", "port": 5500})
    assert driver.address_missing()
    driver.config["host"] = "127.0.0.1"
    assert not driver.address_missing()


async def test_connect_refuses_before_any_session_is_built():
    _AcmeOwnSession.built = 0
    driver = _driver({"host": "", "port": 5500}, cls=_AcmeOwnSession)
    with pytest.raises(ConnectionFaultError) as caught:
        await driver.connect()
    assert caught.value.fault_code == "invalid_config"
    assert str(caught.value) == NO_ADDRESS_MESSAGE
    assert _AcmeOwnSession.built == 0


def test_the_card_and_the_connection_test_say_the_same_sentence():
    assert device_reachability._NO_HOST == NO_ADDRESS_MESSAGE


async def test_the_card_names_it_nothing_is_dialed_and_nothing_retries():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    _DRIVER_REGISTRY["acme_widget_no_address"] = _AcmeWidget
    dial = AsyncMock()
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", dial):
            await dm.add_device({
                "id": "w1", "driver": "acme_widget_no_address", "name": "W1",
                "config": {"host": "", "port": 5500},
            })
            await asyncio.sleep(0.05)
        assert dial.await_count == 0
        assert dm.state.get("device.w1.connected") is False
        assert dm.state.get("device.w1.offline_reason") == "invalid_config"
        assert dm.state.get("device.w1.offline_detail") == NO_ADDRESS_MESSAGE
        assert dm.state.get("device.w1.reconnect_failed") is True
        assert "w1" not in dm._reconnect_tasks
    finally:
        await dm._cancel_reconnect("w1")
        _DRIVER_REGISTRY.pop("acme_widget_no_address", None)


async def test_a_device_with_an_address_still_retries(monkeypatch):
    """The stop is for a missing address only; a refused port keeps retrying."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    monkeypatch.setattr(dm, "_reconnect_delay", lambda device_id, attempt: 60.0)
    _DRIVER_REGISTRY["acme_widget_no_address"] = _AcmeWidget
    try:
        with patch(
            "openavc.transport.tcp.TCPTransport.create",
            AsyncMock(side_effect=ConnectionRefusedError("refused")),
        ):
            await dm.add_device({
                "id": "w1", "driver": "acme_widget_no_address", "name": "W1",
                "config": {"host": "192.0.2.10", "port": 5500},
            })
            await asyncio.sleep(0.05)
        assert dm.state.get("device.w1.offline_reason") == "connection_refused"
        assert "w1" in dm._reconnect_tasks
        assert not dm.state.get("device.w1.reconnect_failed")
    finally:
        await dm._cancel_reconnect("w1")
        _DRIVER_REGISTRY.pop("acme_widget_no_address", None)
