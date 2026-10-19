"""An adapter chosen in Reach devices through that has no address refuses
device traffic instead of letting the operating system route it through
another adapter.

The case: the adapter is chosen while it has no address (unplugged, Wi-Fi
off, no DHCP lease yet), right after another choice, so the address kept for
a missing adapter is the previous choice's and is not kept. Every connection,
the connection test, a discovery scan and an audit's network check then
refuse with one sentence that names the adapter. A device at a 169.254
address is still reached from the adapter's own 169.254 address, and a host
on this computer is never bound. The adapters are invented.
"""

from __future__ import annotations

import json

import pytest

from openavc import listen, reach_devices
from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError
from openavc.listen import Adapter
from openavc.system_config import SystemConfig
from openavc.transport.endpoint import control_bind_address
from tests.listen_helpers import ETHERNET

# Chosen, with no address: its cable is out and it never had a lease here.
SPARE = Adapter("Ethernet 2", "00-05-1B-00-00-07", (), False)
SPARE_LINK_LOCAL = Adapter("Ethernet 2", "00-05-1B-00-00-07", (), True, (("169.254.7.7", "169.254.0.0/16"),))

SENTENCE = (
    "Ethernet 2, chosen in Settings > Network > Reach devices through, has no address. "
    "Connect it, or choose another adapter (or Every connected adapter) there."
)


@pytest.fixture
def chosen(tmp_path, monkeypatch):
    """Ethernet 2 chosen just after Ethernet: the rollback mirror still holds
    Ethernet's address, which Ethernet holds now, so it is not kept."""

    def choose(spare: Adapter = SPARE):
        path = tmp_path / "system.json"
        path.write_text(json.dumps({"network": {
            "reach_devices_through": spare.identity(),
            "control_interface": "10.20.30.5",
        }}), encoding="utf-8")
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = path
        cfg.load()
        monkeypatch.setattr("openavc.system_config.get_system_config", lambda: cfg)
        monkeypatch.setattr(listen, "list_adapters", lambda: [ETHERNET, spare])
        reach_devices.forget()
        return cfg

    return choose


def test_nothing_is_kept_to_bind(chosen):
    chosen()
    assert reach_devices.address() == ""
    assert reach_devices.unusable_choice().adapter_name == "Ethernet 2"


def test_a_connection_is_refused_naming_the_adapter(chosen):
    chosen()
    with pytest.raises(ConnectionFaultError) as refused:
        control_bind_address("192.0.2.10")
    assert refused.value.fault_code == UNREACHABLE
    assert str(refused.value) == "Can't reach 192.0.2.10. " + SENTENCE


def test_a_host_on_this_computer_is_still_left_unbound(chosen):
    chosen()
    assert control_bind_address("127.0.0.1") is None
    assert control_bind_address("localhost") is None


def test_a_169_254_device_is_reached_from_the_adapters_own_169_254_address(chosen):
    chosen(SPARE_LINK_LOCAL)
    assert control_bind_address("169.254.20.1") == "169.254.7.7"
    with pytest.raises(ConnectionFaultError):
        control_bind_address("192.0.2.10")


def test_every_adapter_and_an_adapter_with_an_address_are_unchanged(chosen, monkeypatch):
    cfg = chosen()
    monkeypatch.setattr(listen, "list_adapters", lambda: [ETHERNET, SPARE])
    cfg.set("network", "reach_devices_through", {})
    reach_devices.forget()
    assert reach_devices.unusable_choice() is None
    assert control_bind_address("192.0.2.10") is None
    cfg.set("network", "reach_devices_through", ETHERNET.identity())
    reach_devices.forget()
    assert control_bind_address("192.0.2.10") == "10.20.30.5"


async def test_the_connection_test_says_so(chosen):
    from openavc.core.device_reachability import _open_tcp

    chosen()
    assert await _open_tcp("192.0.2.10", 4352, {}) == "Can't reach 192.0.2.10. " + SENTENCE


async def test_a_discovery_scan_is_refused(chosen):
    from openavc.discovery.engine import DiscoveryEngine

    chosen()
    engine = DiscoveryEngine.__new__(DiscoveryEngine)
    DiscoveryEngine.__init__(engine)
    with pytest.raises(ValueError, match="Ethernet 2, chosen in Settings"):
        await engine.start_scan()


async def test_a_device_card_says_it_and_keeps_retrying(chosen, monkeypatch):
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver
    from openavc.drivers.registry import _DRIVER_REGISTRY

    class AcmeWidget(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_widget_no_address_test", "name": "Acme Widget", "manufacturer": "Acme",
            "category": "utility", "transport": "tcp",
            "default_config": {"host": "", "port": 4999}, "commands": {},
            "state_variables": {}, "config_schema": {},
        }

        async def send_command(self, command, params=None):
            return None

    chosen()
    monkeypatch.setitem(_DRIVER_REGISTRY, AcmeWidget.DRIVER_INFO["id"], AcmeWidget)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    monkeypatch.setattr(dm, "_reconnect_delay", lambda device_id, attempt: 60.0)
    try:
        await dm.add_device({
            "id": "widget", "driver": AcmeWidget.DRIVER_INFO["id"], "name": "Widget",
            "config": {"host": "192.0.2.10", "port": 4999},
        })
        assert state.get("device.widget.connected") is False
        assert state.get("device.widget.offline_reason") == UNREACHABLE
        assert state.get("device.widget.offline_detail") == "Can't reach 192.0.2.10. " + SENTENCE
        assert "widget" in dm._reconnect_tasks
    finally:
        await dm.disconnect_all()


async def test_an_audits_network_check_is_refused(chosen):
    from unittest.mock import MagicMock

    from openavc.audit.footprint import NetworkCheck

    chosen()
    check = NetworkCheck(MagicMock(), "192.0.2.10", "192.0.2.10")
    with pytest.raises(ConnectionError, match="^Ethernet 2, chosen in Settings"):
        await check.run()


async def test_a_drivers_own_http_client_is_refused_not_left_unbound(chosen):
    import httpx

    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver
    from openavc.transport.http_client import ReachDevicesTransport

    class AcmeOwnClient(BaseDriver):
        DRIVER_INFO = {"id": "acme_own_client_no_address_test", "name": "Acme", "transport": "http"}

        async def send_command(self, command, params=None):
            return None

        def build_client(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(base_url="http://192.0.2.10:8080")

    chosen()
    state = StateStore()
    state.set_event_bus(EventBus())
    driver = AcmeOwnClient("acme1", {"host": "192.0.2.10", "port": 8080}, state, EventBus())
    async with driver.build_client() as client:
        assert isinstance(client._transport, ReachDevicesTransport)
        with pytest.raises(httpx.ConnectError) as refused:
            await client.get("/status")
    assert str(refused.value) == "Can't reach 192.0.2.10. " + SENTENCE


def _reconnect_once(dm, device_id, driver, config):
    """Run one cycle of the automatic reconnect loop for ``driver``: the
    second wait ends it."""
    import asyncio
    from unittest.mock import patch

    dm._devices[device_id] = driver
    dm._device_configs[device_id] = {"id": device_id, "driver": driver.DRIVER_INFO["id"], "config": config}
    waits = 0

    async def wait(_seconds):
        nonlocal waits
        waits += 1
        if waits > 1:
            raise asyncio.CancelledError

    async def run():
        with patch("asyncio.sleep", wait):
            try:
                await dm._reconnect_loop(device_id)
            except asyncio.CancelledError:
                pass

    return run()


async def test_a_driver_that_keeps_off_the_adapter_still_reconnects(chosen, monkeypatch):
    # A driver may keep a connection off the chosen adapter (a cloud service
    # is not equipment on site), so the check before an automatic reconnect
    # leaves the adapter to the connect.
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver
    from openavc.drivers.registry import _DRIVER_REGISTRY

    class AcmeCloud(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_cloud_no_address_test", "name": "Acme Cloud", "manufacturer": "Acme",
            "category": "utility", "transport": "http",
            "default_config": {"host": "", "port": 443}, "commands": {},
            "state_variables": {}, "config_schema": {},
        }
        connects = 0

        async def connect(self):
            AcmeCloud.connects += 1
            self._connected = True
            self.state.set(f"device.{self.device_id}.connected", True, source="driver")

        async def send_command(self, command, params=None):
            return None

    chosen()
    monkeypatch.setitem(_DRIVER_REGISTRY, AcmeCloud.DRIVER_INFO["id"], AcmeCloud)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    config = {"host": "cloud.acme.example", "port": 443}
    driver = AcmeCloud("cloud", dict(config), state, events)
    try:
        await _reconnect_once(dm, "cloud", driver, config)
        assert AcmeCloud.connects == 1
        assert state.get("device.cloud.connected") is True
    finally:
        await dm.disconnect_all()


async def test_an_http_device_retrying_still_names_the_adapter(chosen, monkeypatch):
    # The connect refuses with the sentence before it dials, so a device on
    # the platform's own connection reads the same as before.
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver
    from openavc.drivers.registry import _DRIVER_REGISTRY

    class AcmeHttp(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_http_no_address_test", "name": "Acme Http", "manufacturer": "Acme",
            "category": "utility", "transport": "http",
            "default_config": {"host": "", "port": 80}, "commands": {},
            "state_variables": {}, "config_schema": {},
        }

        async def send_command(self, command, params=None):
            return None

    chosen()
    monkeypatch.setitem(_DRIVER_REGISTRY, AcmeHttp.DRIVER_INFO["id"], AcmeHttp)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    config = {"host": "192.0.2.10", "port": 80}
    driver = AcmeHttp("panel_http", dict(config), state, events)
    try:
        await _reconnect_once(dm, "panel_http", driver, config)
        assert state.get("device.panel_http.connected") is not True
        assert state.get("device.panel_http.offline_reason") == UNREACHABLE
        assert state.get("device.panel_http.offline_detail") == "Can't reach 192.0.2.10. " + SENTENCE
    finally:
        await dm.disconnect_all()
