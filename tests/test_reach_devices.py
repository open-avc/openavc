"""Reach devices through: the device-side adapter, stored by identity.

What a choice resolves to, how an earlier control_interface converts, the
rollback mirror, the environment, the address every reader binds (and how
often it enumerates adapters), and the config API. The adapters are invented
(tests/listen_helpers.py).
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc import listen, reach_devices
from openavc.listen import Adapter
from openavc.system_config import SystemConfig
from tests.listen_helpers import ETHERNET, WIFI, use_plan

UNPLUGGED = Adapter("Ethernet", ETHERNET.mac, (), False)


def _cfg(tmp_path, network: dict | None = None) -> SystemConfig:
    path = tmp_path / "system.json"
    if network is not None:
        path.write_text(json.dumps({"network": network}), encoding="utf-8")
    cfg = SystemConfig()
    cfg._data_dir = tmp_path
    cfg._file_path = path
    cfg.load()
    return cfg


class TestResolving:
    def test_every_adapter_binds_nothing(self):
        assert reach_devices.resolve({}, [ETHERNET, WIFI]).address == ""

    def test_an_adapter_is_found_by_mac_whatever_its_separators(self):
        identity = {"name": "Renamed", "mac": "50:eb:f6:d2:fc:29"}
        resolved = reach_devices.resolve(identity, [WIFI, ETHERNET])
        assert resolved.address == "10.20.30.5"
        assert resolved.adapter_name == "Ethernet"
        assert resolved.connected is True

    def test_then_by_name(self):
        resolved = reach_devices.resolve({"name": "wi-fi"}, [ETHERNET, WIFI])
        assert resolved.address == "192.168.1.40"

    def test_a_missing_adapter_keeps_its_last_address_and_never_widens(self):
        resolved = reach_devices.resolve(ETHERNET.identity(), [WIFI], last="10.20.30.5")
        assert resolved.address == "10.20.30.5"
        assert resolved.connected is False
        assert resolved.adapter_name == "Ethernet"

    def test_an_adapter_with_no_address_keeps_its_last_address(self):
        resolved = reach_devices.resolve(ETHERNET.identity(), [UNPLUGGED], last="10.20.30.5")
        assert resolved.address == "10.20.30.5"
        assert resolved.connected is False

    def test_a_last_address_another_adapter_holds_now_is_not_kept(self):
        # Wi-Fi was chosen, then Ethernet with no address: the mirror is still Wi-Fi's.
        resolved = reach_devices.resolve(ETHERNET.identity(), [WIFI, UNPLUGGED], last="192.168.1.40")
        assert resolved.address == "", "never Wi-Fi's address, which would send it out Wi-Fi"
        assert resolved.connected is False

    def test_a_broadcast_goes_from_the_169_254_address_of_an_adapter_holding_no_other(self):
        no_dhcp = Adapter("Ethernet", ETHERNET.mac, (), True, (("169.254.7.7", "169.254.0.0/16"),))
        resolved = reach_devices.resolve(ETHERNET.identity(), [WIFI, no_dhcp], last="10.20.30.5")
        assert resolved.broadcast_source == "169.254.7.7"
        assert reach_devices.resolve(ETHERNET.identity(), [ETHERNET]).broadcast_source == "10.20.30.5"
        assert reach_devices.resolve(ETHERNET.identity(), [UNPLUGGED]).broadcast_source == ""

    def test_a_literal_address_is_bound_as_it_is(self):
        resolved = reach_devices.resolve({"address": "10.20.30.5"}, [ETHERNET])
        assert resolved.address == "10.20.30.5"
        assert resolved.adapter_name == "Ethernet"
        resolved = reach_devices.resolve({"address": "192.0.2.77"}, [ETHERNET])
        assert resolved.address == "192.0.2.77"
        assert resolved.connected is False

    def test_what_settings_is_told(self):
        assert reach_devices.resolve({}, []).to_dict() == {
            "mode": "every", "adapter": {}, "adapter_name": "", "address": "",
            "connected": True, "label": "Every connected adapter",
        }
        told = reach_devices.resolve(ETHERNET.identity(), [ETHERNET]).to_dict()
        assert told["mode"] == "adapter"
        assert told["label"] == "Ethernet"


class TestSettling:
    def test_an_empty_control_interface_is_every_adapter(self, tmp_path):
        cfg = _cfg(tmp_path, {"control_interface": ""})
        assert reach_devices.settle(cfg, lambda: pytest.fail("no enumeration needed"))
        assert cfg.stored("network", "reach_devices_through") == {}

    def test_an_address_becomes_the_adapter_holding_it(self, tmp_path):
        cfg = _cfg(tmp_path, {"control_interface": "10.20.30.5"})
        assert reach_devices.settle(cfg, [ETHERNET, WIFI])
        assert cfg.stored("network", "reach_devices_through") == ETHERNET.identity()
        assert cfg.stored("network", "control_interface") == "10.20.30.5"

    def test_an_address_no_adapter_holds_stays_that_address(self, tmp_path):
        cfg = _cfg(tmp_path, {"control_interface": "192.0.2.77"})
        reach_devices.settle(cfg, [ETHERNET, WIFI])
        assert cfg.stored("network", "reach_devices_through") == {"address": "192.0.2.77"}
        assert cfg.stored("network", "control_interface") == "192.0.2.77"

    def test_a_settled_choice_is_kept_and_its_mirror_follows_dhcp(self, tmp_path):
        cfg = _cfg(tmp_path, {
            "reach_devices_through": ETHERNET.identity(),
            "control_interface": "10.20.30.5",
        })
        renewed = Adapter("Ethernet", ETHERNET.mac, (("10.20.30.9", "10.20.30.0/24"),), True)
        assert not reach_devices.settle(cfg, [renewed])
        assert cfg.stored("network", "reach_devices_through") == ETHERNET.identity()
        assert cfg.stored("network", "control_interface") == "10.20.30.9"

    def test_the_mirror_keeps_the_last_address_while_the_adapter_is_missing(self, tmp_path):
        cfg = _cfg(tmp_path, {
            "reach_devices_through": ETHERNET.identity(),
            "control_interface": "10.20.30.5",
        })
        reach_devices.settle(cfg, [WIFI])
        assert cfg.stored("network", "control_interface") == "10.20.30.5"

    def test_listen_start_settles_it_on_the_same_first_start(self, tmp_path):
        cfg = _cfg(tmp_path, {"control_interface": "192.168.1.40"})
        listen.start(cfg, [ETHERNET, WIFI], deployment_type="linux_package")
        assert cfg.stored("network", "reach_devices_through") == WIFI.identity()


class TestTheEnvironment:
    def test_an_address_holds_the_choice(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAVC_CONTROL_INTERFACE", "10.20.30.5")
        cfg = _cfg(tmp_path)
        assert cfg.get("network", "reach_devices_through") == {"address": "10.20.30.5"}
        assert cfg.env_override("network", "reach_devices_through") == "OPENAVC_CONTROL_INTERFACE"
        assert cfg.env_override("network", "control_interface") == "OPENAVC_CONTROL_INTERFACE"

    def test_empty_holds_every_adapter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAVC_CONTROL_INTERFACE", "")
        cfg = _cfg(tmp_path)
        assert cfg.get("network", "reach_devices_through") == {}
        assert cfg.env_override("network", "reach_devices_through") == "OPENAVC_CONTROL_INTERFACE"

    def test_it_never_reaches_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAVC_CONTROL_INTERFACE", "10.20.30.5")
        cfg = _cfg(tmp_path, {"control_interface": ""})
        reach_devices.settle(cfg, [ETHERNET])
        cfg.save()
        on_disk = json.loads((tmp_path / "system.json").read_text())["network"]
        assert on_disk["reach_devices_through"] == {}


class TestTheAddressReadersBind:
    def test_one_enumeration_serves_many_connections(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, {"reach_devices_through": ETHERNET.identity()})
        monkeypatch.setattr("openavc.system_config.get_system_config", lambda: cfg)
        calls = []
        monkeypatch.setattr(listen, "list_adapters", lambda: calls.append(1) or [ETHERNET])
        assert [reach_devices.address() for _ in range(5)] == ["10.20.30.5"] * 5
        assert len(calls) == 1

    def test_refresh_follows_a_dhcp_renewal(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path, {"reach_devices_through": ETHERNET.identity()})
        monkeypatch.setattr("openavc.system_config.get_system_config", lambda: cfg)
        present = [ETHERNET]
        monkeypatch.setattr(listen, "list_adapters", lambda: present)
        assert reach_devices.address() == "10.20.30.5"
        present[:] = [Adapter("Ethernet", ETHERNET.mac, (("10.20.30.9", "10.20.30.0/24"),), True)]
        assert reach_devices.address() == "10.20.30.5"  # kept until the tick
        reach_devices.refresh()
        assert reach_devices.address() == "10.20.30.9"

    def test_every_adapter_and_an_address_never_enumerate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(listen, "list_adapters", lambda: pytest.fail("enumerated"))
        for choice, expected in (({}, ""), ({"address": "192.0.2.77"}, "192.0.2.77")):
            cfg = _cfg(tmp_path, {"reach_devices_through": choice})
            monkeypatch.setattr("openavc.system_config.get_system_config", lambda cfg=cfg: cfg)
            assert reach_devices.address() == expected

    def test_a_failed_lookup_never_fails_a_connect(self, monkeypatch):
        def broken():
            raise RuntimeError("no config")

        monkeypatch.setattr("openavc.system_config.get_system_config", broken)
        assert reach_devices.address() == ""


class TestWhatAFanOutAsks:
    """``pinned``: whether a broadcast or a multicast may go out through
    every adapter, or only the chosen one, or not at all."""

    def test_nothing_chosen_lets_it_go_through_every_adapter(self, monkeypatch):
        monkeypatch.setattr(reach_devices, "choice", lambda cfg=None: {})
        assert reach_devices.pinned() is None
        assert reach_devices.is_chosen() is False

    def test_an_adapter_chosen_answers_with_its_resolution(self, monkeypatch):
        resolved = reach_devices.Resolved(identity=ETHERNET.identity(), address="10.20.30.5")
        monkeypatch.setattr(reach_devices, "choice", lambda cfg=None: ETHERNET.identity())
        monkeypatch.setattr(reach_devices, "current", lambda cfg=None, adapters=None: resolved)
        assert reach_devices.pinned() is resolved
        assert reach_devices.is_chosen() is True

    def test_a_failed_lookup_with_an_adapter_chosen_answers_no_address(self, monkeypatch):
        def broken(cfg=None, adapters=None):
            raise RuntimeError("no adapters")

        monkeypatch.setattr(reach_devices, "choice", lambda cfg=None: {"name": "Ethernet"})
        monkeypatch.setattr(reach_devices, "current", broken)
        answer = reach_devices.pinned()
        assert answer is not None and answer.address == "", "never widen on a failed lookup"
        assert answer.adapter_name == "Ethernet"


class TestWhereABroadcastLeavesFrom:
    def test_once_from_each_connected_adapter(self):
        two_addresses = Adapter(
            "Ethernet", ETHERNET.mac,
            (("10.20.30.5", "10.20.30.0/24"), ("10.20.31.5", "10.20.31.0/24")), True,
        )
        unplugged_static = Adapter("Ethernet 2", "00-05-1B-00-00-01", (("192.0.2.123", "192.0.2.0/24"),), False)
        link_local_only = Adapter("Ethernet 3", "00-05-1B-00-00-02", (), True, (("169.254.7.7", "169.254.0.0/16"),))
        adapters = [two_addresses, unplugged_static, WIFI, link_local_only]
        assert reach_devices.broadcast_sources(adapters) == ["10.20.30.5", "192.168.1.40", "169.254.7.7"]

    def test_a_failed_lookup_leaves_the_broadcast_unbound(self, monkeypatch):
        def broken():
            raise RuntimeError("no adapters")

        monkeypatch.setattr(listen, "list_adapters", broken)
        assert reach_devices.broadcast_sources() == []


class TestASenderThatAsksOften:
    """The beacon and a multicast stream ask for the adapters on every send;
    they are read at most every ten seconds, and never while a send waits."""

    async def test_read_once_then_kept(self, monkeypatch):
        reads: list[int] = []

        def sources():
            reads.append(1)
            return ["10.20.30.5"]

        monkeypatch.setattr(reach_devices, "broadcast_sources", sources)
        assert await reach_devices.kept_broadcast_sources() == ["10.20.30.5"]
        assert await reach_devices.kept_broadcast_sources() == ["10.20.30.5"]
        assert len(reads) == 1

    async def test_an_old_answer_is_used_while_it_is_read_again(self, monkeypatch):
        answers = iter([["10.20.30.5"], ["10.20.30.5", "192.168.1.40"]])
        reading = threading.Event()
        release = threading.Event()

        def sources():
            answer = next(answers)
            if len(answer) == 2:
                reading.set()
                release.wait(5)
            return answer

        monkeypatch.setattr(reach_devices, "broadcast_sources", sources)
        assert await reach_devices.kept_broadcast_sources() == ["10.20.30.5"]
        monkeypatch.setattr(reach_devices, "_SOURCES_FRESH_FOR", 0.0)
        # Stale: the old answer comes back at once, the read goes on beside it.
        assert await reach_devices.kept_broadcast_sources() == ["10.20.30.5"]
        assert await asyncio.to_thread(reading.wait, 5)
        assert await reach_devices.kept_broadcast_sources() == ["10.20.30.5"]
        release.set()
        for _ in range(100):
            if not reach_devices._sources_reading:
                break
            await asyncio.sleep(0.02)
        monkeypatch.setattr(reach_devices, "_SOURCES_FRESH_FOR", 10.0)
        assert await reach_devices.kept_broadcast_sources() == ["10.20.30.5", "192.168.1.40"]


@pytest.fixture
def api(tmp_path, monkeypatch, isolated_auth_config):
    """The real app on a config in tmp_path and two invented adapters."""
    from fastapi.testclient import TestClient

    from openavc.api import rest, ws
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.main import app
    from openavc.system_config import get_system_config, reset_system_config

    monkeypatch.delenv("OPENAVC_BIND", raising=False)
    monkeypatch.delenv("OPENAVC_CONTROL_INTERFACE", raising=False)
    monkeypatch.setattr(listen, "list_adapters", lambda: [ETHERNET, WIFI])
    engine = MagicMock()
    state = StateStore()
    state.set_event_bus(EventBus())
    engine.state = state
    engine.isc = None
    engine.panel_devices = SimpleNamespace(list_devices=lambda: {"approved": []})
    engine.reconcile_runtime_services = AsyncMock()
    rest.set_engine(engine)
    ws.set_engine(engine)

    def start(network: dict | None = None, **env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        if network is not None:
            (tmp_path / "system.json").write_text(json.dumps({"network": network}))
        reset_system_config()
        cfg = get_system_config()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()
        listen.start(cfg, [ETHERNET, WIFI], deployment_type="linux_package")
        use_plan("any")
        return TestClient(app, base_url="http://10.20.30.5:8080"), cfg

    yield start
    rest.set_engine(None)
    ws.set_engine(None)
    reset_system_config()


def _network_on_disk(tmp_path) -> dict:
    return json.loads((tmp_path / "system.json").read_text())["network"]


def _reach(c, value):
    return c.patch("/api/system/config", json={"network": {"reach_devices_through": value}})


class TestTheConfigApi:
    def test_get_reports_the_resolved_adapter(self, api):
        c, _ = api({"control_interface": "10.20.30.5"})
        body = c.get("/api/system/config").json()
        assert body["network"]["reach_devices_through"] == ETHERNET.identity()
        assert body["_reach_devices"]["adapter_name"] == "Ethernet"
        assert body["_reach_devices"]["address"] == "10.20.30.5"

    def test_an_adapter_is_stored_by_identity_with_its_mirror(self, api, tmp_path):
        c, _ = api()
        assert _reach(c, WIFI.identity()).status_code == 200
        network = _network_on_disk(tmp_path)
        assert network["reach_devices_through"] == WIFI.identity()
        assert network["control_interface"] == "192.168.1.40"

    def test_it_applies_to_the_next_connection_with_no_restart(self, api):
        c, _ = api()
        _reach(c, WIFI.identity())
        assert reach_devices.address() == "192.168.1.40"
        _reach(c, {})
        assert reach_devices.address() == ""

    def test_an_adapter_this_computer_lacks_is_refused(self, api):
        c, _ = api()
        resp = _reach(c, {"name": "Thunderbolt", "mac": "00-11-22-33-44-55"})
        assert resp.status_code == 422
        assert "Thunderbolt" in resp.json()["detail"]

    def test_the_adapter_already_chosen_may_stay_while_unplugged(self, api, monkeypatch):
        c, _ = api({"reach_devices_through": {"name": "Thunderbolt", "mac": "00-11"}})
        assert _reach(c, {"name": "Thunderbolt", "mac": "00-11"}).status_code == 200

    def test_a_shape_that_names_no_adapter_is_refused(self, api):
        c, _ = api()
        assert _reach(c, "10.20.30.5").status_code == 422
        assert _reach(c, {"address": "10.20.30.5"}).status_code == 422

    def test_a_changed_control_interface_names_the_new_key(self, api):
        c, _ = api()
        resp = c.patch("/api/system/config", json={"network": {"control_interface": "10.20.30.5"}})
        assert resp.status_code == 422
        assert "network.reach_devices_through" in resp.json()["detail"]

    def test_a_whole_get_body_round_trips(self, api):
        c, _ = api({"control_interface": "10.20.30.5"})
        body = c.get("/api/system/config").json()
        assert c.patch("/api/system/config", json=body).status_code == 200

    def test_held_by_the_environment_is_refused(self, api):
        c, _ = api(OPENAVC_CONTROL_INTERFACE="10.20.30.5")
        resp = _reach(c, WIFI.identity())
        assert resp.status_code == 409
        assert "OPENAVC_CONTROL_INTERFACE" in resp.json()["detail"]


class TestServingIsExact:
    """A listener on 127.0.0.1 refuses a browser that opened 127.0.0.2."""

    def test_this_computer_serves_127_0_0_1_only(self):
        plan = listen.resolve(listen.ListenChoice(mode="this_computer"), [])
        assert plan.serves("127.0.0.1")
        assert plan.serves("localhost")
        assert not plan.serves("127.0.0.2")

    def test_any_network_serves_every_address(self):
        plan = listen.resolve(listen.ListenChoice(mode="any"), [])
        assert plan.serves("127.0.0.2")
        assert plan.serves("10.20.30.5")

    def test_a_browser_on_this_computer_keeps_working_and_moves(self):
        result = listen.preview(
            listen.ListenChoice(mode="this_computer"), [ETHERNET],
            connected_through="127.0.0.2", from_this_computer=True,
        )
        assert result["keeps_this_connection"] is True
        assert result["safety_net"] is False
        assert result["serves_this_address"] is False

    def test_a_browser_on_the_network_is_cut_off(self):
        result = listen.preview(
            listen.ListenChoice(mode="this_computer"), [ETHERNET],
            connected_through="10.20.30.5", from_this_computer=False,
        )
        assert result["keeps_this_connection"] is False
        assert result["serves_this_address"] is False
        assert result["safety_net"] is True
