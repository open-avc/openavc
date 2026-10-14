"""A closed port reads connection_refused on the device card, whatever the
transport and whichever attempt saw it.

The address is right and nothing listens there, so the card has to send the
reader to the service and the port, not to the IP address and the network.
Two places used to lose it: the reconnect loop's cheap check before an
expensive connect (ssh, mqtt, http) reported any failure as unreachable, and
gave up before Windows reports a refusal (about two seconds there); and an
HTTP device's first connect kept only httpx's "All connection attempts
failed", which names no cause.

Every port here is one this test bound and closed again, so nothing listens
on it.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, patch

import pytest

import openavc.core.device_manager as device_manager_module
from openavc.core.connection_fault import CONNECTION_REFUSED, UNREACHABLE
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY


def _closed_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class AcmeShellDriver(BaseDriver):
    """An invented CLI device reached over ssh."""

    DRIVER_INFO = {
        "id": "acme_shell_refused_test",
        "name": "Acme Shell",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "ssh",
        "default_config": {"host": "", "port": 22},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connect_calls = 0
        self.cycles = 0

    async def connect(self):
        self.connect_calls += 1
        raise ConnectionError("a connect the cheap check should have spared")

    async def disconnect(self):
        self._connected = False

    async def send_command(self, command, params=None):
        return None

    async def stop_polling(self):
        # The reconnect loop stops polling at the top of every cycle, before
        # its check: the second cycle ends the test after one full check.
        self.cycles += 1
        if self.cycles > 1:
            raise asyncio.CancelledError


class AcmeWebWidgetDriver(BaseDriver):
    """An invented device with an HTTP API."""

    DRIVER_INFO = {
        "id": "acme_web_widget_refused_test",
        "name": "Acme Web Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "http",
        "default_config": {"host": "", "port": 80},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    async def send_command(self, command, params=None):
        return None


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


def _shell_device(manager, core, port):
    state, events = core
    config = {"host": "127.0.0.1", "port": port, "transport": "ssh"}
    driver = AcmeShellDriver("shell", config, state, events)
    manager._devices["shell"] = driver
    manager._device_configs["shell"] = {
        "id": "shell", "driver": AcmeShellDriver.DRIVER_INFO["id"],
        "config": config,
    }
    return driver


async def test_the_check_before_an_ssh_reconnect_keeps_a_refusal(manager, core):
    state, _ = core
    port = _closed_port()
    driver = _shell_device(manager, core, port)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await manager._reconnect_loop("shell")

    assert driver.cycles == 2
    assert driver.connect_calls == 0
    assert state.get("device.shell.offline_reason") == CONNECTION_REFUSED
    assert f"127.0.0.1:{port}" in state.get("device.shell.offline_detail")


async def test_the_check_before_a_reconnect_still_reads_silence_as_unreachable(
    manager, core,
):
    state, _ = core
    driver = _shell_device(manager, core, _closed_port())

    async def silent(*args, **kwargs):
        raise asyncio.TimeoutError

    with patch("asyncio.sleep", new_callable=AsyncMock), \
            patch.object(device_manager_module.asyncio, "open_connection", silent):
        await manager._reconnect_loop("shell")

    assert driver.connect_calls == 0
    assert state.get("device.shell.offline_reason") == UNREACHABLE


async def test_an_http_device_on_a_closed_port_reads_refused_from_its_first_connect(
    manager, core, monkeypatch,
):
    state, _ = core
    monkeypatch.setitem(
        _DRIVER_REGISTRY, AcmeWebWidgetDriver.DRIVER_INFO["id"], AcmeWebWidgetDriver,
    )
    port = _closed_port()

    await manager.add_device({
        "id": "widget",
        "driver": AcmeWebWidgetDriver.DRIVER_INFO["id"],
        "name": "Widget",
        "config": {"host": "127.0.0.1", "port": port, "poll_interval": 0},
    })

    assert state.get("device.widget.connected") is False
    assert state.get("device.widget.offline_reason") == CONNECTION_REFUSED
    assert f"127.0.0.1:{port}" in state.get("device.widget.offline_detail")
