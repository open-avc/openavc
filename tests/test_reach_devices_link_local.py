"""Reach devices through and a device at a 169.254 (link-local) address.

With an adapter chosen, a connection to a 169.254 device leaves from that
adapter's own 169.254 address: many such devices answer only a source on
their own link. An adapter that holds none still binds its other address,
and the device card and the connection test then say why the device cannot
be reached. The 169.254 addresses are kept apart from an adapter's other
addresses, so nothing that lists, chooses or listens on an adapter sees them.

The adapters, the device and its addresses are invented.
"""

from __future__ import annotations

import asyncio
import errno
import json
import socket
import sys
import types
from unittest.mock import AsyncMock

import pytest

from openavc import listen, reach_devices
from openavc.core.connection_fault import CONNECTION_REFUSED, UNREACHABLE, link_local_fault
from openavc.listen import Adapter, ListenChoice, ListenWatcher
from openavc.system_config import SystemConfig
from openavc.transport.endpoint import control_bind_address, is_link_local, local_address_for
from tests.listen_helpers import ETHERNET, WIFI

DEVICE = "169.254.20.30"
OUR_LINK_LOCAL = "169.254.7.7"
SENTENCE = (
    f"Can't reach {DEVICE}:8080. Ethernet, chosen in Settings > Network > Reach "
    "devices through, has no 169.254 address to reach it from. Give Ethernet "
    "one, or choose another adapter (or Every connected adapter) there."
)

ETHERNET_LL = Adapter(
    "Ethernet", ETHERNET.mac, ETHERNET.addresses, True,
    ((OUR_LINK_LOCAL, "169.254.0.0/16"),),
)
ONLY_LL = Adapter("Ethernet", ETHERNET.mac, (), True, ((OUR_LINK_LOCAL, "169.254.0.0/16"),))


@pytest.fixture
def choose(tmp_path, monkeypatch):
    """Choose ``identity`` in Reach devices through, on a computer with
    ``adapters``."""

    def set_to(identity: dict, adapters: list[Adapter]) -> None:
        path = tmp_path / "system.json"
        path.write_text(json.dumps({"network": {"reach_devices_through": identity}}))
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = path
        cfg.load()
        monkeypatch.setattr("openavc.system_config.get_system_config", lambda: cfg)
        monkeypatch.setattr(listen, "list_adapters", lambda: list(adapters))
        reach_devices.forget()

    return set_to


# ---------------------------------------------------------------------------
# The adapter inventory
# ---------------------------------------------------------------------------


def _fake_enumeration(monkeypatch, ifaddr_adapters, psutil_addrs):
    """ifaddr names adapters by description, psutil by the OS's name, as on
    Windows; each entry is (name, [(ip, prefix_or_netmask), ...])."""

    monkeypatch.setattr("openavc.discovery.network_scanner._IS_MACOS", False)

    def ip(addr, prefix):
        return types.SimpleNamespace(ip=addr, network_prefix=prefix)

    ifaddr = types.ModuleType("ifaddr")
    ifaddr.get_adapters = lambda: [
        types.SimpleNamespace(nice_name=name, ips=[ip(a, p) for a, p in ips])
        for name, ips in ifaddr_adapters
    ]
    monkeypatch.setitem(sys.modules, "ifaddr", ifaddr)

    psutil = types.ModuleType("psutil")
    psutil.AF_LINK = -1

    def entry(family, address, netmask=None):
        return types.SimpleNamespace(family=family, address=address, netmask=netmask)

    def net_if_addrs():
        out = {}
        for name, mac, ips in psutil_addrs:
            rows = [entry(-1, mac)] if mac else []
            rows += [entry(socket.AF_INET, a, m) for a, m in ips]
            out[name] = rows
        return out

    psutil.net_if_addrs = net_if_addrs
    psutil.net_if_stats = lambda: {
        name: types.SimpleNamespace(isup=True) for name, _mac, _ips in psutil_addrs
    }
    monkeypatch.setitem(sys.modules, "psutil", psutil)


@pytest.fixture
def windows_laptop(monkeypatch):
    """Ethernet holding a routable and a 169.254 address, a USB port with a
    169.254 address only (no DHCP server), Wi-Fi, and the Bluetooth and Wi-Fi
    Direct entries Windows gives a 169.254 address."""
    _fake_enumeration(
        monkeypatch,
        [
            ("Acme 2.5GbE Controller", [("192.168.100.127", 24), (OUR_LINK_LOCAL, 16)]),
            ("Acme USB Ethernet", [("169.254.40.2", 16)]),
            ("Acme Wireless AX", [("192.168.1.78", 24)]),
            ("Acme Bluetooth Device (Personal Area Network)", [("169.254.9.9", 16)]),
            ("Acme Wi-Fi Direct Virtual Adapter", [("169.254.8.8", 16)]),
        ],
        [
            ("Ethernet", "AA-00-00-00-00-01", [
                ("192.168.100.127", "255.255.255.0"), (OUR_LINK_LOCAL, "255.255.0.0"),
            ]),
            ("Ethernet 3", "AA-00-00-00-00-03", [("169.254.40.2", "255.255.0.0")]),
            ("Wi-Fi", "AA-00-00-00-00-02", [("192.168.1.78", "255.255.255.0")]),
            ("Bluetooth Network Connection", "AA-00-00-00-00-04", [("169.254.9.9", "255.255.0.0")]),
            ("Local Area Connection* 1", "AA-00-00-00-00-05", [("169.254.8.8", "255.255.0.0")]),
        ],
    )


class TestTheInventory:
    def test_an_adapters_169_254_addresses_are_kept_apart(self, windows_laptop):
        from openavc.discovery.network_scanner import get_adapter_inventory

        by_name = {a["name"]: a for a in get_adapter_inventory()}
        ethernet = by_name["Ethernet"]
        assert ethernet["addresses"] == [{"ip": "192.168.100.127", "subnet": "192.168.100.0/24"}]
        assert ethernet["link_local"] == [{"ip": OUR_LINK_LOCAL, "subnet": "169.254.0.0/16"}]
        assert ethernet["has_address"] is True
        assert by_name["Wi-Fi"]["link_local"] == []

    def test_an_adapter_with_only_a_169_254_address_still_has_no_address(self, windows_laptop):
        from openavc.discovery.network_scanner import get_adapter_inventory

        usb = {a["name"]: a for a in get_adapter_inventory()}["Ethernet 3"]
        assert usb["addresses"] == []
        assert usb["has_address"] is False
        assert usb["link_local"] == [{"ip": "169.254.40.2", "subnet": "169.254.0.0/16"}]

    def test_bluetooth_and_wifi_direct_never_appear(self, windows_laptop):
        from openavc.discovery.network_scanner import get_adapter_inventory

        names = {a["name"] for a in get_adapter_inventory()}
        assert names == {"Ethernet", "Ethernet 3", "Wi-Fi"}

    def test_discovery_and_the_address_lists_still_leave_169_254_out(self, windows_laptop):
        from openavc.discovery.network_scanner import (
            get_interface_ips,
            get_local_subnets,
            get_network_adapters,
        )

        assert not [a for a in get_network_adapters() if a["ip"].startswith("169.254.")]
        assert get_interface_ips() == ["192.168.100.127", "192.168.1.78"]
        assert get_local_subnets() == ["192.168.100.0/24", "192.168.1.0/24"]

    def test_a_linux_address_added_under_a_label_belongs_to_its_adapter(self, monkeypatch):
        from openavc.discovery.network_scanner import get_adapter_inventory

        _fake_enumeration(
            monkeypatch,
            [("eth0", [("10.20.30.5", 24), ("169.254.3.4", 16)])],
            [
                ("eth0", "aa:00:00:00:00:01", [("10.20.30.5", "255.255.255.0")]),
                ("eth0:avahi", None, [("169.254.3.4", "255.255.0.0")]),
            ],
        )
        (eth0,) = get_adapter_inventory()
        assert eth0["name"] == "eth0"
        assert eth0["link_local"] == [{"ip": "169.254.3.4", "subnet": "169.254.0.0/16"}]

    def test_the_adapter_model_reads_them_apart(self, windows_laptop):
        adapters = {a.name: a for a in listen.list_adapters()}
        assert adapters["Ethernet"].ips == ("192.168.100.127",)
        assert adapters["Ethernet"].link_local == ((OUR_LINK_LOCAL, "169.254.0.0/16"),)
        assert adapters["Ethernet 3"].ips == ()


# ---------------------------------------------------------------------------
# Nothing that lists, chooses or listens on an adapter sees them
# ---------------------------------------------------------------------------


class TestTheListenSide:
    def test_one_adapter_listens_on_its_other_addresses_only(self):
        plan = listen.resolve(ListenChoice(mode="adapter", adapter=ETHERNET.identity()), [ETHERNET_LL])
        assert plan.network_addresses == ("10.20.30.5",)
        assert plan.hosts == ("10.20.30.5", "127.0.0.1")
        assert listen.panel_addresses(plan) == ["10.20.30.5"]

    def test_an_adapter_with_only_a_169_254_address_listens_on_this_computer(self):
        plan = listen.resolve(ListenChoice(mode="adapter", adapter=ETHERNET.identity()), [ONLY_LL])
        assert plan.hosts == ("127.0.0.1",)
        assert plan.problem is not None

    async def test_a_169_254_address_coming_and_going_restarts_nothing(self):
        plan = listen.resolve(ListenChoice(mode="adapter", adapter=ETHERNET.identity()), [ETHERNET])
        seen = iter([[ETHERNET_LL], [ETHERNET], [ETHERNET_LL], [ETHERNET]])
        restart = AsyncMock()
        watcher = ListenWatcher(
            restart, adapters=lambda: next(seen), plan=lambda: plan,
            available=lambda ip: True, confirm_polls=1,
        )
        for _ in range(4):
            await watcher.tick()
        restart.assert_not_awaited()

    def test_settings_is_told_nothing_new(self):
        told = reach_devices.resolve(ETHERNET.identity(), [ETHERNET_LL]).to_dict()
        assert set(told) == {"mode", "adapter", "adapter_name", "address", "connected", "label"}
        assert told["address"] == "10.20.30.5"


# ---------------------------------------------------------------------------
# Resolving the choice
# ---------------------------------------------------------------------------


class TestResolving:
    def test_the_chosen_adapters_169_254_address(self):
        resolved = reach_devices.resolve(ETHERNET.identity(), [WIFI, ETHERNET_LL])
        assert resolved.address == "10.20.30.5"
        assert resolved.link_local_address == OUR_LINK_LOCAL

    def test_none_held(self):
        assert reach_devices.resolve(ETHERNET.identity(), [ETHERNET]).link_local_address == ""

    def test_every_adapter_has_none(self):
        assert reach_devices.resolve({}, [ETHERNET_LL]).link_local_address == ""

    def test_a_literal_address_is_bound_as_it_is(self):
        resolved = reach_devices.resolve({"address": "10.20.30.5"}, [ETHERNET_LL])
        assert resolved.address == "10.20.30.5"
        assert resolved.link_local_address == ""

    def test_an_adapter_with_only_a_169_254_address_keeps_it_for_169_254_devices(self):
        resolved = reach_devices.resolve(ETHERNET.identity(), [ONLY_LL], last="10.20.30.5")
        assert resolved.address == "10.20.30.5"
        assert resolved.connected is False
        assert resolved.link_local_address == OUR_LINK_LOCAL


# ---------------------------------------------------------------------------
# The rule every connection asks
# ---------------------------------------------------------------------------


class TestTheRule:
    @pytest.mark.parametrize("host, expected", [
        (DEVICE, OUR_LINK_LOCAL),
        ("169.254.0.1", OUR_LINK_LOCAL),
        ("10.20.30.80", "10.20.30.5"),
        ("acme-widget.local", "10.20.30.5"),  # a name is never resolved here
        ("127.0.0.1", None),
        ("localhost", None),
    ])
    def test_pinned_with_a_169_254_address(self, host, expected):
        assert local_address_for(host, "10.20.30.5", OUR_LINK_LOCAL) == expected

    def test_pinned_without_one_never_widens(self):
        assert local_address_for(DEVICE, "10.20.30.5", "") == "10.20.30.5"
        assert local_address_for(DEVICE, "10.20.30.5") == "10.20.30.5"

    def test_nothing_pinned_binds_nothing(self):
        assert local_address_for(DEVICE, "", OUR_LINK_LOCAL) is None

    @pytest.mark.parametrize("host, expected", [
        ("169.254.20.30", True), (" 169.254.20.30 ", True), ("169.254.0.0", True),
        ("169.255.0.1", False), ("10.20.30.80", False), ("fe80::1", False),
        ("acme-widget.local", False), ("", False), (None, False),
    ])
    def test_what_counts_as_a_169_254_address(self, host, expected):
        assert is_link_local(host) is expected

    def test_every_connection_asks_the_chosen_adapter(self, choose):
        choose(ETHERNET.identity(), [ETHERNET_LL, WIFI])
        assert control_bind_address(DEVICE) == OUR_LINK_LOCAL
        assert control_bind_address("10.20.30.80") == "10.20.30.5"
        assert control_bind_address("127.0.0.1") is None

    def test_another_adapter_chosen_binds_its_own(self, choose):
        choose(WIFI.identity(), [ETHERNET_LL, WIFI])
        assert control_bind_address(DEVICE) == "192.168.1.40"

    def test_every_adapter_chosen_binds_nothing(self, choose):
        choose({}, [ETHERNET_LL, WIFI])
        assert control_bind_address(DEVICE) is None

    def test_one_enumeration_serves_both_addresses(self, choose, monkeypatch):
        choose(ETHERNET.identity(), [ETHERNET_LL])
        calls = []
        monkeypatch.setattr(listen, "list_adapters", lambda: calls.append(1) or [ETHERNET_LL])
        for _ in range(3):
            assert control_bind_address(DEVICE) == OUR_LINK_LOCAL
            assert control_bind_address("10.20.30.80") == "10.20.30.5"
        assert len(calls) == 1

    def test_a_driver_and_a_plugin_get_the_same_answer(self, choose):
        from openavc.core.event_bus import EventBus
        from openavc.core.plugin_api import PluginAPI
        from openavc.core.plugin_registry import PluginRegistry
        from openavc.core.plugin_test_harness import MockDeviceManager, MockMacroEngine
        from openavc.core.state_store import StateStore
        from openavc.drivers.base import BaseDriver

        class _Driver(BaseDriver):
            DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}

            async def send_command(self, command, params=None):
                return None

        choose(ETHERNET.identity(), [ETHERNET_LL])
        state, events = StateStore(), EventBus()
        state.set_event_bus(events)
        driver = _Driver("widget1", {"host": DEVICE, "port": 8080}, state, events)
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
        assert driver.local_address() == OUR_LINK_LOCAL
        assert api.local_address(DEVICE) == OUR_LINK_LOCAL

    async def test_the_plugin_harness_answers_from_what_the_test_sets(self):
        from openavc.core.plugin_test_harness import PluginTestHarness

        class AcmeRelayPlugin:
            PLUGIN_INFO = {
                "id": "acme_relay", "name": "Acme Relay", "version": "0.1.0",
                "author": "Test", "description": "Talks to an Acme relay box.",
                "category": "integration", "license": "MIT", "capabilities": [],
            }

            async def start(self, api):
                self.api = api

            async def stop(self):
                pass

        harness = PluginTestHarness()
        plugin = AcmeRelayPlugin()
        api = await harness.start_plugin(plugin)
        harness.reach_devices_address = "10.20.30.5"
        assert api.local_address(DEVICE) == "10.20.30.5"
        harness.reach_devices_link_local_address = OUR_LINK_LOCAL
        assert api.local_address(DEVICE) == OUR_LINK_LOCAL
        assert api.local_address("10.20.30.80") == "10.20.30.5"
        await harness.stop_plugin(plugin)


# ---------------------------------------------------------------------------
# What the device card and the connection test say
# ---------------------------------------------------------------------------


def _unreachable() -> OSError:
    return OSError(errno.ENETUNREACH, "Network is unreachable")


class TestWhatTheCardSays:
    def test_the_chosen_adapter_holds_no_169_254_address(self, choose):
        choose(ETHERNET.identity(), [ETHERNET, WIFI])
        fault = link_local_fault(DEVICE, 8080)
        assert fault.code == UNREACHABLE
        assert fault.message == SENTENCE

    def test_without_the_adapters_name(self, choose, monkeypatch):
        choose(ETHERNET.identity(), [ETHERNET])
        monkeypatch.setattr(
            reach_devices, "missing_link_local",
            lambda host: reach_devices.Resolved(identity={"mac": "x"}, address="10.20.30.5"),
        )
        assert link_local_fault(DEVICE, 8080).message == (
            f"Can't reach {DEVICE}:8080. The adapter chosen in Settings > Network > "
            "Reach devices through has no 169.254 address to reach it from. Give it "
            "one, or choose another adapter (or Every connected adapter) there."
        )

    @pytest.mark.parametrize("identity, adapters, host", [
        (ETHERNET.identity(), [ETHERNET_LL], DEVICE),  # it holds one
        ({}, [ETHERNET], DEVICE),  # every adapter
        ({"address": "10.20.30.5"}, [ETHERNET], DEVICE),  # a literal address
        (ETHERNET.identity(), [WIFI], DEVICE),  # the adapter is not connected
        (ETHERNET.identity(), [ETHERNET], "10.20.30.80"),  # not a 169.254 device
        (ETHERNET.identity(), [ETHERNET], "acme-widget.local"),
    ])
    def test_said_only_when_it_is_the_reason(self, choose, identity, adapters, host):
        choose(identity, adapters)
        assert link_local_fault(host, 8080) is None

    def _manager_with(self, host: str):
        from types import SimpleNamespace

        from openavc.core.device_manager import DeviceManager
        from openavc.core.event_bus import EventBus
        from openavc.core.state_store import StateStore

        state = StateStore()
        manager = DeviceManager(state, EventBus())
        driver = SimpleNamespace(
            config={"host": host, "port": 8080, "transport": "tcp"},
            DRIVER_INFO={"transport": "tcp"},
            last_transport_error="", transport=None, last_fault=None,
        )
        return manager, driver, state

    def test_the_device_card(self, choose):
        choose(ETHERNET.identity(), [ETHERNET, WIFI])
        manager, driver, state = self._manager_with(DEVICE)
        assert manager._set_offline_reason("widget1", driver, exc=_unreachable()) == UNREACHABLE
        assert state.get("device.widget1.offline_reason") == UNREACHABLE
        assert state.get("device.widget1.offline_detail") == SENTENCE

    def test_a_device_that_answered_keeps_its_own_reason(self, choose):
        choose(ETHERNET.identity(), [ETHERNET, WIFI])
        manager, driver, state = self._manager_with(DEVICE)
        refused = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        assert manager._set_offline_reason("widget1", driver, exc=refused) == CONNECTION_REFUSED
        assert "refused" in state.get("device.widget1.offline_detail")

    def test_the_card_is_unchanged_when_the_adapter_holds_one(self, choose):
        choose(ETHERNET.identity(), [ETHERNET_LL])
        manager, driver, state = self._manager_with(DEVICE)
        manager._set_offline_reason("widget1", driver, exc=_unreachable())
        assert state.get("device.widget1.offline_detail") == (
            f"Can't reach {DEVICE}:8080. Check the IP address and network."
        )

    def test_the_connection_test_says_the_same(self, choose):
        from openavc.core.device_reachability import _in_words

        choose(ETHERNET.identity(), [ETHERNET, WIFI])
        assert _in_words(_unreachable(), DEVICE, 8080, "tcp") == SENTENCE

    def test_an_http_connection_test_says_it_too(self, choose):
        import httpx

        from openavc.core.device_reachability import _in_words

        choose(ETHERNET.identity(), [ETHERNET, WIFI])
        timeout = httpx.ConnectTimeout("")
        assert _in_words(timeout, DEVICE, 8080, "http") == SENTENCE
        choose(ETHERNET.identity(), [ETHERNET_LL])
        assert _in_words(timeout, DEVICE, 8080, "http").startswith(
            f"Could not connect to {DEVICE}:8080"
        )

    async def test_a_connection_test_that_times_out_says_it_too(self, choose, monkeypatch):
        from openavc.core import device_reachability

        choose(ETHERNET.identity(), [ETHERNET, WIFI])
        dialed = {}

        async def never(host, port, local_addr=None):
            dialed["local_addr"] = local_addr
            raise asyncio.TimeoutError

        monkeypatch.setattr(device_reachability.asyncio, "open_connection", never)
        assert await device_reachability._open_tcp(DEVICE, 8080, {}) == SENTENCE
        assert dialed["local_addr"] == ("10.20.30.5", 0)


# ---------------------------------------------------------------------------
# Device Audit's network check
# ---------------------------------------------------------------------------


class TestTheAuditsNetworkCheck:
    def _check(self, ip: str, **kwargs):
        from unittest.mock import MagicMock

        from openavc.audit.footprint import NetworkCheck

        return NetworkCheck(MagicMock(), ip, ip, **kwargs)

    def test_checks_to_a_169_254_device_leave_from_the_adapters_169_254_address(self, choose):
        choose(ETHERNET.identity(), [ETHERNET_LL])
        check = self._check(DEVICE)
        assert check.source_ip == OUR_LINK_LOCAL
        assert check.footprint.source_ip == OUR_LINK_LOCAL
        assert check._listen_ip == "10.20.30.5"  # the listeners' multicast joins

    def test_the_report_names_the_adapter_they_left_from(self, choose, monkeypatch):
        from openavc.audit import footprint

        choose(ETHERNET.identity(), [ETHERNET_LL])
        monkeypatch.setattr(footprint, "get_network_adapters", lambda: [])
        monkeypatch.setattr(footprint, "get_local_subnets", lambda: [])
        check = self._check(DEVICE)
        check._network_facts()
        assert check.footprint.local_ip == OUR_LINK_LOCAL
        assert check.footprint.interface == "Ethernet"

    @pytest.mark.parametrize("identity, adapters, ip, expected", [
        (ETHERNET.identity(), [ETHERNET], DEVICE, "10.20.30.5"),  # none held
        (ETHERNET.identity(), [ETHERNET_LL], "10.20.30.80", "10.20.30.5"),
        ({}, [ETHERNET_LL], DEVICE, ""),
    ])
    def test_otherwise_unchanged(self, choose, identity, adapters, ip, expected):
        choose(identity, adapters)
        assert self._check(ip).source_ip == expected

    def test_an_address_given_is_used_as_it_is(self, choose):
        choose(ETHERNET.identity(), [ETHERNET_LL])
        assert self._check(DEVICE, source_ip="10.20.30.5").source_ip == "10.20.30.5"

    @pytest.mark.parametrize("identity, adapters, routed, expected", [
        (ETHERNET.identity(), [ETHERNET_LL], "10.20.30.5", True),
        ({}, [ETHERNET_LL], OUR_LINK_LOCAL, True),  # the OS picked the 169.254 source
        (ETHERNET.identity(), [ETHERNET], "10.20.30.5", False),  # none held
    ])
    def test_a_169_254_device_reached_from_one_is_on_this_network(
        self, choose, monkeypatch, identity, adapters, routed, expected,
    ):
        from openavc.audit import footprint

        choose(identity, adapters)
        monkeypatch.setattr(footprint, "get_network_adapters", lambda: [])
        monkeypatch.setattr(footprint, "get_local_subnets", lambda: ["10.20.30.0/24"])
        monkeypatch.setattr(footprint, "local_route_ip", lambda ip: routed)
        check = self._check(DEVICE)
        check._network_facts()
        assert check.footprint.same_subnet is expected

    @pytest.mark.asyncio
    async def test_an_unread_mac_is_not_blamed_on_another_network(self, choose, monkeypatch):
        from openavc.audit import footprint

        choose(ETHERNET.identity(), [ETHERNET_LL])
        monkeypatch.setattr(footprint, "get_network_adapters", lambda: [])
        monkeypatch.setattr(footprint, "get_local_subnets", lambda: ["10.20.30.0/24"])
        monkeypatch.setattr(footprint, "harvest_arp_table", AsyncMock(return_value={}))
        check = self._check(DEVICE)
        check._network_facts()
        await check._late_mac()
        assert [limit["id"] for limit in check.footprint.to_dict()["limits"]
                if limit["id"].startswith("mac_")] == ["mac_unread"]


# ---------------------------------------------------------------------------
# Where a device is told to post its updates (push: http_listener)
# ---------------------------------------------------------------------------


class TestTheCallbackAddress:
    @pytest.fixture(autouse=True)
    def _plain_http(self, monkeypatch):
        from openavc import config

        monkeypatch.setattr(config, "TLS_ENABLED", False)
        monkeypatch.setattr(config, "HTTP_PORT", 8080)

    def test_a_169_254_device_posts_to_the_adapters_169_254_address(self, choose):
        from openavc.transport.http_listener import callback_url

        choose(ETHERNET.identity(), [ETHERNET_LL, WIFI])
        assert callback_url(DEVICE, "/api/push/acme") == f"http://{OUR_LINK_LOCAL}:8080/api/push/acme"
        assert callback_url("10.20.30.80", "/api/push/acme") == "http://10.20.30.5:8080/api/push/acme"

    def test_an_adapter_without_one_never_widens(self, choose):
        from openavc.transport.http_listener import callback_url

        choose(ETHERNET.identity(), [ETHERNET, WIFI])
        assert callback_url(DEVICE, "/api/push/acme") == "http://10.20.30.5:8080/api/push/acme"

    @pytest.mark.asyncio
    async def test_updates_are_listened_for_there(self, choose, monkeypatch):
        """Accept connections from never lists a 169.254 address, so the
        updates-only listener opens on it."""
        from openavc.listen_sockets import push_listeners
        from openavc.transport import http_listener

        choose(ETHERNET.identity(), [ETHERNET_LL, WIFI])
        ensure = AsyncMock()
        monkeypatch.setattr(push_listeners, "_enabled", True)
        monkeypatch.setattr(push_listeners, "ensure", ensure)
        sub = await http_listener.subscribe("acme", DEVICE, lambda request: None, "acme")
        try:
            ensure.assert_awaited_once_with(OUR_LINK_LOCAL)
        finally:
            await sub.close()
