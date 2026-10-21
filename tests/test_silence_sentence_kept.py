"""A device dropped for silence keeps saying so while its reconnects get no answer.

The poll loop drops a device that stopped answering with "The device stopped
answering: 3 polls in a row failed." Each reconnect then fails on its own
terms: an HTTP device's connect check reads "Device at <host>:<port> is not
responding", which on its own is a generic no_response ("check that the
driver, transport and port are right"), wrong advice for a device that was
working. The card replaced the right sentence with it about a second after the
drop. A reconnect that fails some other way (a refused port) still says so.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, patch

import pytest

from openavc.core.connection_fault import CONNECTION_REFUSED, NO_RESPONSE, ConnectionFault
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver

STOPPED = "The device stopped answering: 3 polls in a row failed."


class AcmeWebWidgetDriver(BaseDriver):
    """An invented device with an HTTP API whose connect check fails."""

    DRIVER_INFO = {
        "id": "acme_web_widget_silence_test",
        "name": "Acme Web Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "http",
        "default_config": {"host": "", "port": 80},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connect_error: BaseException = ConnectionError("not set")
        self.cycles = 0

    async def connect(self):
        raise self.connect_error

    async def send_command(self, command, params=None):
        return None

    async def stop_polling(self):
        # The reconnect loop stops polling at the top of every cycle: the
        # second call ends it after one full attempt. Later calls (the
        # fixture's disconnect) are ordinary.
        self.cycles += 1
        if self.cycles == 2:
            raise asyncio.CancelledError


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
async def manager(core):
    state, events = core
    dm = DeviceManager(state, events)
    yield dm
    await dm.disconnect_all()


@pytest.fixture
def listening_port():
    """A port that accepts connections and never answers, so the cheap check
    before an HTTP reconnect passes and the connect itself is what fails."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    yield sock.getsockname()[1]
    sock.close()


def _dropped_widget(manager, core, port):
    """The widget as the poll loop leaves it after dropping it for silence."""
    state, events = core
    config = {"host": "127.0.0.1", "port": port}
    driver = AcmeWebWidgetDriver("widget", config, state, events)
    driver._silence_verdict = ConnectionFault(NO_RESPONSE, STOPPED)
    manager._devices["widget"] = driver
    manager._device_configs["widget"] = {
        "id": "widget", "driver": AcmeWebWidgetDriver.DRIVER_INFO["id"], "config": config,
    }
    state.set("device.widget.offline_reason", NO_RESPONSE)
    state.set("device.widget.offline_detail", STOPPED)
    return driver


async def test_a_reconnect_that_gets_no_answer_keeps_the_stopped_answering_sentence(
    manager, core, listening_port,
):
    state, _ = core
    driver = _dropped_widget(manager, core, listening_port)
    driver.connect_error = ConnectionError(
        f"Device at 127.0.0.1:{listening_port} is not responding"
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await manager._reconnect_loop("widget")

    assert driver.cycles == 2
    assert state.get("device.widget.offline_reason") == NO_RESPONSE
    assert state.get("device.widget.offline_detail") == STOPPED


async def test_without_a_silence_drop_the_same_failure_reads_as_before(
    manager, core, listening_port,
):
    state, _ = core
    driver = _dropped_widget(manager, core, listening_port)
    driver._silence_verdict = None
    driver.connect_error = ConnectionError(
        f"Device at 127.0.0.1:{listening_port} is not responding"
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await manager._reconnect_loop("widget")

    assert state.get("device.widget.offline_reason") == NO_RESPONSE
    assert state.get("device.widget.offline_detail") != STOPPED
    assert "driver, transport and port" in state.get("device.widget.offline_detail")


async def test_a_reconnect_refused_by_the_port_says_so_instead(
    manager, core, listening_port,
):
    state, _ = core
    driver = _dropped_widget(manager, core, listening_port)
    driver.connect_error = ConnectionRefusedError(111, "Connection refused")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await manager._reconnect_loop("widget")

    assert state.get("device.widget.offline_reason") == CONNECTION_REFUSED


async def test_pressing_reconnect_keeps_it_too(manager, core, listening_port):
    state, _ = core
    driver = _dropped_widget(manager, core, listening_port)
    driver.connect_error = ConnectionError(
        f"Device at 127.0.0.1:{listening_port} is not responding"
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await manager.reconnect_device("widget")
        # The failed press hands the device to the automatic reconnect; let
        # that loop reach its end (the stand-in's second stop_polling).
        loop = manager._reconnect_tasks.get("widget")
        if loop is not None:
            await loop

    assert state.get("device.widget.offline_reason") == NO_RESPONSE
    assert state.get("device.widget.offline_detail") == STOPPED


async def test_a_reason_given_outright_is_not_replaced(manager, core, listening_port):
    state, _ = core
    driver = _dropped_widget(manager, core, listening_port)
    held = ConnectionFault(NO_RESPONSE, "The connection is open again.")

    manager._set_offline_reason("widget", driver, fault=held)

    assert state.get("device.widget.offline_detail") == "The connection is open again."
