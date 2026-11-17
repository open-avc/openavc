"""Where the check before each automatic reconnect dials, and what the device
card's sentence names: the address ``core/device_config.connection_of``
answers for the driver's connection as it dials now.

The check exists so an absent ssh, mqtt or http device costs one short socket
per retry instead of a full connect (an ssh process, a TLS handshake). It
dials the address the connect would: a transport's own port when the device
names none, the name and port of a host written as a URL, and the simulator
while a simulation points the device there.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.device_away import connection_descriptor
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _AcmeDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_reconnect_check",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
async def manager():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    yield dm
    await dm.disconnect_all()


@pytest.fixture
def dials(monkeypatch) -> list[tuple[Any, Any]]:
    """Every address the check dials; each is refused."""
    calls: list[tuple[Any, Any]] = []

    async def open_connection(name, port, **kwargs):
        calls.append((name, port))
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    return calls


def _running(dm: DeviceManager, device_id: str, config: dict[str, Any]) -> _AcmeDriver:
    driver = _AcmeDriver(device_id, dict(config), dm.state, dm.events)
    dm._devices[device_id] = driver
    dm._device_configs[device_id] = {
        "id": device_id, "driver": _AcmeDriver.DRIVER_INFO["id"], "config": dict(config),
    }
    return driver


@pytest.mark.parametrize(("transport", "port"), [("ssh", 22), ("mqtt", 1883)])
async def test_a_device_on_its_transports_own_port_is_checked_there(
    manager, dials, transport, port,
):
    """An ssh device that names no port connects on 22, so the check dials
    22: skipping it would start the ssh process on every retry, which is what
    the check is there to spare."""
    driver = _running(manager, "acme1", {"host": "192.0.2.30", "transport": transport})

    failure = await manager._probe_failure("acme1", driver)

    assert dials == [("192.0.2.30", port)]
    assert failure is not None


async def test_a_port_that_is_not_a_number_is_left_to_the_connect(manager, dials):
    """The connect refuses a port that is not a number as a setting to fix,
    which stops the retries; the check never dials it, so it cannot turn that
    into an address that did not answer."""
    driver = _running(manager, "acme1", {
        "host": "192.0.2.30", "transport": "ssh", "port": "telnet",
    })

    assert await manager._probe_failure("acme1", driver) is None
    assert dials == []


@pytest.mark.parametrize("port", [0, 70000, -1])
async def test_a_port_out_of_range_is_left_to_the_connect(manager, dials, port):
    driver = _running(manager, "acme1", {"host": "192.0.2.30", "transport": "ssh", "port": port})

    assert await manager._probe_failure("acme1", driver) is None
    assert dials == []


@pytest.mark.parametrize("host", ["http://[::1", "https://[acme]"])
async def test_a_url_host_no_reader_can_take_apart_is_dialed_as_written(manager, dials, host):
    """Asking where such a device connects never fails: the check dials the
    host as written and the device reads as unreachable, so its retries go
    on until the host is corrected."""
    driver = _running(manager, "acme1", {"host": host, "transport": "http"})

    assert await manager._probe_failure("acme1", driver) is not None
    assert dials == [(host, 80)]


async def test_a_simulated_device_is_checked_at_the_simulator(manager, dials):
    """While a simulation points the device at the simulator, the check dials
    the simulator, never the equipment's own address."""
    driver = _running(manager, "acme1", {"host": "192.0.2.30", "transport": "ssh"})
    driver.config.update(host="127.0.0.1", port=19022)

    await manager._probe_failure("acme1", driver)

    assert dials == [("127.0.0.1", 19022)]


@pytest.mark.parametrize(("config", "named"), [
    ({"host": "192.0.2.30", "transport": "ssh"}, ("192.0.2.30", 22, "ssh")),
    ({"host": "192.0.2.30", "transport": "mqtt"}, ("192.0.2.30", 1883, "mqtt")),
    ({"host": "192.0.2.30", "transport": "http", "ssl": True}, ("192.0.2.30", 443, "http")),
    ({"host": "https://acme.example", "transport": "http"}, ("acme.example", 443, "http")),
    (
        {"host": "http://acme.example:8080/api", "transport": "http", "port": 443, "ssl": True},
        ("acme.example", 8080, "http"),
    ),
    # A serial device has no host, whatever its settings kept from another
    # transport: the card names the port alone.
    ({"host": "192.0.2.30", "transport": "serial", "port": "COM3"}, ("", "COM3", "serial")),
    ({"host": "192.0.2.30", "port": 4352}, ("192.0.2.30", 4352, "tcp")),
], ids=["ssh", "mqtt", "https", "url", "url-own-port", "serial", "tcp"])
def test_the_device_card_names_the_address_the_connect_dials(config, named):
    driver = _AcmeDriver("acme1", dict(config), StateStore(), EventBus())
    assert connection_descriptor(driver) == named
