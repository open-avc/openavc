"""Everything that tells the network this server exists follows who can
connect (Settings > Network > Accept connections from): mDNS, the
Inter-System beacon, the Inter-System status, and /api/status.

The adapters are invented (tests/listen_helpers.py); no real interface is
joined or broadcast on.
"""

from __future__ import annotations

import pytest

from openavc.core import isc as isc_module
from openavc.discovery import multicast
from openavc.listen import Adapter
from tests.listen_helpers import ETHERNET, WIFI, use_plan

TWO_SUBNETS = Adapter(
    "Ethernet",
    ETHERNET.mac,
    (("10.20.30.5", "10.20.30.0/24"), ("172.16.4.9", "172.16.0.0/16")),
    True,
)


class TestTheMulticastJoin:
    def _joins(self, monkeypatch, refuse: set[str]):
        joined: list[str] = []

        def join(sock, group, ip):
            if ip in refuse:
                raise OSError("no such interface")
            joined.append(ip)

        monkeypatch.setattr(multicast, "_join", join)
        return joined

    def test_a_restricted_list_never_falls_back_to_the_default_interface(self, monkeypatch):
        joined = self._joins(monkeypatch, refuse={"10.20.30.5"})
        result = multicast.join_group_on_interfaces(
            object(), "224.0.0.251", interface_ips=["10.20.30.5"], allow_any=False
        )
        assert result == [] and joined == []

    def test_the_guess_still_falls_back(self, monkeypatch):
        joined = self._joins(monkeypatch, refuse={"10.20.30.5"})
        result = multicast.join_group_on_interfaces(
            object(), "224.0.0.251", interface_ips=["10.20.30.5"]
        )
        assert result == [multicast.ANY_INTERFACE] and joined == [multicast.ANY_INTERFACE]


class TestMdns:
    def test_the_advertiser_joins_only_the_chosen_adapter(self, monkeypatch):
        from openavc.discovery import mdns_advertiser

        seen: dict = {}

        def join(sock, group, *, interface_ips=None, allow_any=True, control_ip=""):
            seen.update(interface_ips=interface_ips, allow_any=allow_any)
            return list(interface_ips or ["10.0.0.1"])

        monkeypatch.setattr(mdns_advertiser, "join_group_on_interfaces", join)
        monkeypatch.setattr(mdns_advertiser.socket.socket, "bind", lambda self, addr: None)
        sock, joined = mdns_advertiser._create_advertiser_socket(["10.20.30.5"])
        sock.close()
        assert joined == ["10.20.30.5"]
        assert seen == {"interface_ips": ["10.20.30.5"], "allow_any": False}

    def test_a_restricted_join_that_fails_is_a_failure_not_a_fallback(self, monkeypatch):
        from openavc.discovery import mdns_advertiser

        monkeypatch.setattr(
            mdns_advertiser, "join_group_on_interfaces", lambda *a, **k: []
        )
        monkeypatch.setattr(mdns_advertiser.socket.socket, "bind", lambda self, addr: None)
        with pytest.raises(OSError):
            mdns_advertiser._create_advertiser_socket(["10.20.30.5"])

    @pytest.mark.parametrize("mode, adapter, wanted, interfaces", [
        ("any", None, True, None),
        ("adapter", ETHERNET, True, ["10.20.30.5"]),
        ("this_computer", None, False, None),
    ])
    async def test_the_engine_advertises_where_panels_can_connect(
        self, monkeypatch, mode, adapter, wanted, interfaces
    ):
        from types import SimpleNamespace

        from openavc.core.engine import Engine
        from openavc.discovery import mdns_advertiser

        created: list[dict] = []

        class FakeAdvertiser:
            def __init__(self, **kwargs):
                created.append(kwargs)

            async def start(self):
                pass

        monkeypatch.setattr(mdns_advertiser, "MDNSAdvertiser", FakeAdvertiser)
        use_plan(mode, adapter)
        engine = Engine.__new__(Engine)
        engine.mdns_advertiser = None
        engine._instance_id = "test-instance"
        engine.project = SimpleNamespace(project=SimpleNamespace(name="Lecture Hall"))
        assert engine._mdns_wanted() is wanted
        await engine._start_mdns_advertiser()
        if wanted:
            assert created and created[0]["interface_ips"] == interfaces
        else:
            assert created == []

    async def test_a_missing_adapter_announces_nothing(self, monkeypatch):
        from openavc.core.engine import Engine

        use_plan("adapter", ETHERNET, adapters=[WIFI])
        engine = Engine.__new__(Engine)
        assert engine._mdns_wanted() is False


class TestTheInterSystemBeacon:
    def test_any_network_broadcasts_everywhere(self):
        use_plan("any")
        assert isc_module._beacon_targets() == ["255.255.255.255"]

    def test_one_adapter_broadcasts_on_its_own_subnets(self):
        use_plan("adapter", TWO_SUBNETS)
        assert isc_module._beacon_targets() == ["10.20.30.255", "172.16.255.255"]

    def test_this_computer_only_sends_nothing(self):
        use_plan("this_computer")
        assert isc_module._beacon_targets() == []

    def test_the_status_says_whether_peers_can_connect(self):
        manager = isc_module.ISCManager.__new__(isc_module.ISCManager)
        manager._peers = {}
        manager.instance_id = "x"
        manager.instance_name = "Lecture Hall"
        manager._shared_patterns = []
        manager._auth_key = ""
        use_plan("this_computer")
        status = manager.get_status()
        assert status["accepts_peers"] is False
        assert status["accepts_peers_note"] == (
            "Other OpenAVC systems can't reach this one: it accepts connections "
            "from this computer only."
        )
        use_plan("any")
        assert manager.get_status()["accepts_peers"] is True


class TestStatus:
    def test_status_offers_only_the_chosen_adapters_addresses(self, monkeypatch):
        from openavc.core.engine import Engine

        engine = Engine.__new__(Engine)
        monkeypatch.setattr(
            engine, "_detect_network_info",
            lambda: ("192.168.1.40", "avc-1", ["192.168.1.40", "10.20.30.5"]),
            raising=False,
        )
        use_plan("adapter", ETHERNET)
        status = _sensitive_status(engine)
        assert status["local_ip"] == "10.20.30.5"
        assert status["local_ips"] == ["10.20.30.5"]
        assert status["bind_address"] == "10.20.30.5"
        assert status["listen"]["label"] == "Ethernet only"

    def test_status_keeps_every_address_for_any_network(self, monkeypatch):
        from openavc.core.engine import Engine

        engine = Engine.__new__(Engine)
        monkeypatch.setattr(
            engine, "_detect_network_info",
            lambda: ("192.168.1.40", "avc-1", ["192.168.1.40", "10.20.30.5"]),
            raising=False,
        )
        use_plan("any")
        status = _sensitive_status(engine)
        assert status["local_ips"] == ["192.168.1.40", "10.20.30.5"]
        assert status["bind_address"] == "0.0.0.0"


def _sensitive_status(engine) -> dict:
    """get_status's network block, on an engine built without start()."""
    from types import SimpleNamespace

    engine._start_time = 0
    engine._running = True
    engine.project = None
    engine.devices = SimpleNamespace(list_devices=lambda: [])
    engine.macros = SimpleNamespace(_macros={})
    engine.scripts = None
    engine.ws = SimpleNamespace(client_count=0)
    engine.isc = None
    engine.cloud_agent = None
    engine._instance_id = "x"
    return engine.get_status(include_sensitive=True)

