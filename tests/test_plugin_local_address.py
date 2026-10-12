"""Reach devices through, for the connections a plugin opens to a device.

``PluginAPI.local_address(host)`` answers the address a plugin's own
connection to AV equipment binds to: the chosen adapter's address, or None
when no adapter is chosen or the host is on this computer. One rule with
``BaseDriver.local_address`` (``transport/endpoint.py``).

How the bind is seen is the method of ``test_reach_devices_own_connections``:
the device listens on this machine's own network address and the pinned
address is 127.0.0.1, so a bound connection arrives from 127.0.0.1 (or, on
Windows, is refused as unreachable) and an unbound one arrives from the
network address.
"""

from __future__ import annotations

import asyncio

import pytest

from openavc import reach_devices
from openavc.core.event_bus import EventBus
from openavc.core.plugin_api import PluginAPI
from openavc.core.plugin_registry import PluginRegistry
from openavc.core.plugin_test_harness import (
    MockDeviceManager,
    MockMacroEngine,
    PluginTestHarness,
)
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from tests.test_reach_devices_own_connections import (
    BOUND_PEERS,
    NOBODYS_ADDRESS,
    OWN,
    PINNED,
    bound,
    needs_network,
)


@pytest.fixture
def pin(monkeypatch):
    """Pin Reach devices through to an address for the test."""

    def set_to(address: str) -> None:
        monkeypatch.setattr(reach_devices, "address", lambda: address)

    set_to("")
    return set_to


class AcmeRelayPlugin:
    """An invented plugin that keeps a TCP link to a relay box on the network."""

    PLUGIN_INFO = {
        "id": "acme_relay",
        "name": "Acme Relay",
        "version": "0.1.0",
        "author": "Test",
        "description": "Talks to an Acme relay box.",
        "category": "integration",
        "license": "MIT",
        "capabilities": [],
    }

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass

    async def dial(self, host: str, port: int) -> None:
        ip = self.api.local_address(host)
        _, writer = await asyncio.open_connection(
            host, port, local_addr=(ip, 0) if ip else None
        )
        writer.close()
        await writer.wait_closed()


async def _started() -> AcmeRelayPlugin:
    """The plugin on a real PluginAPI, declaring no capabilities at all."""
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    api = PluginAPI(
        plugin_id="acme_relay",
        capabilities=[],
        config={},
        registry=PluginRegistry("acme_relay"),
        state_store=state,
        event_bus=events,
        macro_engine=MockMacroEngine(),
        device_manager=MockDeviceManager(),
        platform_id="test",
    )
    plugin = AcmeRelayPlugin()
    await plugin.start(api)
    return plugin


class _RelayBox:
    """A TCP device on ``host`` that notes who connected."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.peers: list[str] = []
        self.port = 0

    async def __aenter__(self) -> "_RelayBox":
        async def handle(reader, writer):
            self.peers.append(writer.get_extra_info("peername")[0])
            writer.close()

        self._server = await asyncio.start_server(handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()


@needs_network
async def test_with_an_adapter_chosen_the_plugins_connection_leaves_from_it(pin):
    pin(PINNED)
    plugin = await _started()
    assert plugin.api.local_address(OWN) == PINNED
    async with _RelayBox(OWN) as box:
        with bound():
            await plugin.dial(OWN, box.port)
        await asyncio.sleep(0.05)
    assert box.peers == BOUND_PEERS


@needs_network
async def test_with_nothing_chosen_the_operating_system_picks(pin):
    plugin = await _started()
    assert plugin.api.local_address(OWN) is None
    async with _RelayBox(OWN) as box:
        await plugin.dial(OWN, box.port)
        await asyncio.sleep(0.05)
    assert box.peers == [OWN]


async def test_a_device_on_this_computer_is_never_bound(pin):
    # Pinned to an address nobody holds: a connection that binds to it fails,
    # so one that succeeds was not bound.
    pin(NOBODYS_ADDRESS)
    plugin = await _started()
    for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        assert plugin.api.local_address(host) is None
    async with _RelayBox("127.0.0.1") as box:
        await plugin.dial("127.0.0.1", box.port)
        await asyncio.sleep(0.05)
    assert box.peers == ["127.0.0.1"]


async def test_the_answer_is_the_drivers_answer(pin):
    plugin = await _started()

    class _Driver(BaseDriver):
        DRIVER_INFO = {"id": "acme_box", "name": "Acme Box", "transport": "tcp"}

        async def send_command(self, command, params=None):
            return None

    state = StateStore()
    state.set_event_bus(EventBus())
    driver = _Driver("box1", {"host": "10.20.0.80", "port": 1}, state, EventBus())
    for address in ("", "10.20.0.5"):
        pin(address)
        for host in ("10.20.0.80", "127.0.0.1", "localhost", "relay.example.com"):
            assert plugin.api.local_address(host) == driver.local_address(host)


async def test_the_harness_answers_from_the_address_the_test_sets(pin):
    # This computer's own choice never reaches a plugin's tests.
    pin("10.99.0.1")
    harness = PluginTestHarness()
    plugin = AcmeRelayPlugin()
    api = await harness.start_plugin(plugin)
    assert api.local_address("10.20.0.80") is None
    harness.reach_devices_address = "10.20.0.5"
    assert api.local_address("10.20.0.80") == "10.20.0.5"
    assert api.local_address("127.0.0.1") is None
    await harness.stop_plugin(plugin)
