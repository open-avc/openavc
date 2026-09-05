"""A driver that holds no connection at all.

Some devices are contacted, never connected to: a relay or power controller
that takes one datagram and never answers. The platform supports that shape
by letting a driver leave ``self.transport`` as None and carry ``connected``
itself, and by letting a command declare ``available_offline`` so it can run
against a device that is unreachable by definition.

This file drives that path with the real ``BaseDriver`` lifecycle and an
invented driver (``tests/drivers/acme_power_relay.py``) — connect with no
transport, no liveness watchdog, a command that really puts a datagram on a
loopback socket, and the offline gate reached through a real DeviceManager.

Related but different: ``test_offline_command_gate.py`` covers the same gate
with a stub driver that overrides ``connect``/``send_command`` outright. That
isolates the gate; this isolates the transport-less lifecycle underneath it.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from tests.drivers.acme_power_relay import AcmePowerRelayDriver, build_wake_payload


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def listener():
    """A bound UDP socket standing in for the relay. Yields (port, socket)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    yield sock.getsockname()[1], sock
    sock.close()


def _relay(core, port, **overrides):
    state, events = core
    config = {"host": "127.0.0.1", "port": port, "unit_id": "A17"}
    config.update(overrides)
    return AcmePowerRelayDriver("relay1", config, state, events)


async def _recv(sock, timeout=1.0):
    """Read one datagram, waiting up to ``timeout`` seconds."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            return sock.recv(1024)
        except BlockingIOError:
            await asyncio.sleep(0.01)
    raise AssertionError("no datagram arrived")


# --- lifecycle --------------------------------------------------------------


async def test_connect_declares_ready_with_no_transport(core, listener):
    port, _ = listener
    driver = _relay(core, port)
    await driver.connect()
    assert driver.transport is None
    assert driver.connected is True
    assert driver.get_state("connected") is True


async def test_connect_emits_the_connected_event(core, listener):
    port, _ = listener
    state, events = core
    seen: list[str] = []
    events.on("device.connected.relay1", lambda *a, **k: seen.append("up"))
    driver = _relay(core, port)
    await driver.connect()
    await asyncio.sleep(0.05)
    assert seen == ["up"]


async def test_disconnect_clears_connected(core, listener):
    port, _ = listener
    driver = _relay(core, port)
    await driver.connect()
    await driver.disconnect()
    assert driver.connected is False
    assert driver.get_state("connected") is False


async def test_reconnect_is_clean(core, listener):
    """A second connect on a transport-less driver leaves nothing stale."""
    port, _ = listener
    driver = _relay(core, port)
    await driver.connect()
    await driver.connect()
    assert driver.connected is True
    assert driver.transport is None
    await driver.disconnect()


async def test_state_variables_get_a_key_from_the_declaration(core, listener):
    """Declared, so the keys are there; unreported, so they hold nothing."""
    port, _ = listener
    driver = _relay(core, port)
    await driver.connect()
    assert driver.state.has("device.relay1.wakes_sent")
    assert driver.state.has("device.relay1.last_wake")
    assert driver.get_state("wakes_sent") is None
    assert driver.get_state("last_wake") is None


async def test_no_liveness_watchdog_without_a_probe(core, listener):
    """No probe means no health loop — otherwise it would tear down a device
    that was never going to answer."""
    port, _ = listener
    driver = _relay(core, port)
    assert driver._health_enabled() is False
    await driver.connect()
    assert driver._health_task is None


# --- sending ----------------------------------------------------------------


async def test_command_puts_its_payload_on_the_wire(core, listener):
    port, sock = listener
    driver = _relay(core, port)
    await driver.connect()
    result = await driver.send_command("wake")
    assert result is True
    assert await _recv(sock) == b"ACME/1 WAKE A17"
    assert driver.get_state("wakes_sent") == 1
    assert driver.get_state("last_wake") == "ACME/1 WAKE A17"


async def test_missing_config_is_reported_not_raised(core, listener):
    """A device configured without its unit id fails the command, quietly."""
    port, _ = listener
    driver = _relay(core, port, unit_id="")
    await driver.connect()
    assert await driver.send_command("wake") is None
    assert driver.get_state("wakes_sent") is None


def test_payload_builder_rejects_a_malformed_unit_id():
    with pytest.raises(ValueError):
        build_wake_payload("A/17")
    with pytest.raises(ValueError):
        build_wake_payload("")


# --- the offline gate, over the real lifecycle ------------------------------


async def test_offline_capable_command_runs_while_disconnected(core, listener):
    port, sock = listener
    state, events = core
    dm = DeviceManager(state, events)
    driver = _relay(core, port)
    # Deliberately never connected: this is the state a relay's device is in
    # whenever it matters.
    dm._devices["relay1"] = driver

    assert await dm.send_command("relay1", "wake") is True
    assert await _recv(sock) == b"ACME/1 WAKE A17"


async def test_normal_command_still_blocked_while_disconnected(core, listener):
    port, _ = listener
    state, events = core
    dm = DeviceManager(state, events)
    driver = _relay(core, port)
    dm._devices["relay1"] = driver

    with pytest.raises(ConnectionError):
        await dm.send_command("relay1", "identify")
