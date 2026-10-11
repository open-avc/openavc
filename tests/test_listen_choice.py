"""Who can connect: the choice, its default per install type, and what it
resolves to (openavc/listen.py).

The adapters here are invented and handed in; nothing reads this machine's
real adapters, so the results do not depend on where the suite runs.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openavc import listen
from openavc.listen import (
    Adapter,
    ListenChoice,
    ListenPlan,
    ListenWatcher,
    match_adapter,
    resolve,
)
from openavc.system_config import SystemConfig, parse_bind_variable
from tests.listen_helpers import ETHERNET, WIFI

TWO_ADDRESSES = Adapter(
    "Ethernet",
    "50-EB-F6-D2-FC-29",
    (("10.20.30.5", "10.20.30.0/24"), ("10.20.31.5", "10.20.31.0/24")),
    True,
)


def _config_at(tmp_path, file: dict | None = None) -> SystemConfig:
    if file is not None:
        (tmp_path / "system.json").write_text(json.dumps(file), encoding="utf-8")
    cfg = SystemConfig()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    cfg.load()
    return cfg


def _on_disk(tmp_path) -> dict:
    return json.loads((tmp_path / "system.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_bind_variable(monkeypatch):
    monkeypatch.delenv("OPENAVC_BIND", raising=False)


class TestFindingTheAdapter:
    def test_by_mac_first_so_a_renamed_adapter_is_still_found(self):
        renamed = Adapter("Ethernet 3", ETHERNET.mac, ETHERNET.addresses, True)
        found = match_adapter({"name": "Ethernet", "mac": ETHERNET.mac}, [WIFI, renamed])
        assert found is renamed

    def test_by_name_when_the_card_was_replaced(self):
        replaced = Adapter("Ethernet", "11-22-33-44-55-66", ETHERNET.addresses, True)
        found = match_adapter({"name": "Ethernet", "mac": ETHERNET.mac}, [WIFI, replaced])
        assert found is replaced

    def test_mac_spelling_does_not_matter(self):
        found = match_adapter({"mac": "50:eb:f6:d2:fc:29"}, [WIFI, ETHERNET])
        assert found is ETHERNET

    def test_an_address_identity_finds_the_adapter_holding_it(self):
        assert match_adapter({"address": "192.168.1.40"}, [ETHERNET, WIFI]) is WIFI

    def test_nothing_matches_nothing(self):
        assert match_adapter({"name": "Ethernet 9", "mac": "00-00-00-00-00-01"}, [WIFI]) is None

    def test_by_name_when_the_system_reports_no_mac(self):
        """Appliance hardware lists its adapters without a MAC. An empty MAC
        matches nothing, so the name decides."""
        eth = Adapter("eth0", "", (("192.168.4.61", "192.168.4.0/22"),), True)
        wlan = Adapter("wlan0", "", (("10.0.0.8", "10.0.0.0/24"),), True)
        assert match_adapter({"name": "wlan0", "mac": ""}, [eth, wlan]) is wlan
        assert match_adapter({"name": "eth1", "mac": ""}, [eth, wlan]) is None


class TestWhatEachChoiceListensOn:
    def test_any_network_is_the_wildcard_alone(self):
        plan = resolve(ListenChoice("any"), [ETHERNET, WIFI])
        assert plan.hosts == ("0.0.0.0",)
        assert not plan.local_only
        assert plan.legacy_bind == "0.0.0.0"

    def test_this_computer_only_is_loopback(self):
        plan = resolve(ListenChoice("this_computer"), [ETHERNET, WIFI])
        assert plan.hosts == ("127.0.0.1",)
        assert plan.local_only
        assert plan.problem is None

    def test_one_adapter_is_its_address_plus_loopback(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [ETHERNET, WIFI])
        assert plan.hosts == ("10.20.30.5", "127.0.0.1")
        assert plan.network_addresses == ("10.20.30.5",)
        assert plan.adapter_name == "Ethernet"
        assert plan.label == "Ethernet only"
        assert plan.legacy_bind == "10.20.30.5"
        assert not plan.local_only

    def test_an_adapter_with_two_addresses_listens_on_both(self):
        plan = resolve(ListenChoice("adapter", TWO_ADDRESSES.identity()), [TWO_ADDRESSES])
        assert plan.hosts == ("10.20.30.5", "10.20.31.5", "127.0.0.1")

    def test_a_missing_adapter_listens_on_this_computer_only(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [WIFI])
        assert plan.hosts == ("127.0.0.1",)
        assert plan.local_only
        assert plan.problem.code == "adapter_not_connected"
        assert plan.problem.message == (
            "Not accepting connections from the network: "
            "the Ethernet adapter isn't connected."
        )

    def test_an_unplugged_adapter_is_not_connected_even_with_a_static_address(self):
        unplugged = Adapter("Ethernet", ETHERNET.mac, ETHERNET.addresses, False)
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [unplugged])
        assert plan.hosts == ("127.0.0.1",)
        assert plan.problem.code == "adapter_not_connected"

    def test_an_adapter_waiting_for_an_address_says_so(self):
        waiting = Adapter("Ethernet", ETHERNET.mac, (), True)
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [waiting])
        assert plan.hosts == ("127.0.0.1",)
        assert plan.problem.message == (
            "Not accepting connections from the network: "
            "the Ethernet adapter has no address yet."
        )

    def test_a_literal_address_listens_on_that_address_only(self):
        plan = resolve(ListenChoice("adapter", {"address": "10.20.31.5"}), [TWO_ADDRESSES])
        assert plan.hosts == ("10.20.31.5", "127.0.0.1")
        assert plan.adapter_name == "Ethernet"

    def test_a_literal_address_nobody_holds(self):
        plan = resolve(ListenChoice("adapter", {"address": "10.9.9.9"}), [ETHERNET])
        assert plan.local_only
        assert plan.problem.message == (
            "Not accepting connections from the network: "
            "this computer has no address 10.9.9.9."
        )

    def test_an_address_the_bind_refused_falls_back_with_a_reason(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [ETHERNET])
        fallen = plan.without(["10.20.30.5"])
        assert fallen.hosts == ("127.0.0.1",)
        assert fallen.problem.code == "adapter_not_connected"
        assert fallen.legacy_bind == "127.0.0.1"

    def test_one_refused_address_of_two_keeps_the_other(self):
        plan = resolve(ListenChoice("adapter", TWO_ADDRESSES.identity()), [TWO_ADDRESSES])
        fallen = plan.without(["10.20.31.5"])
        assert fallen.hosts == ("10.20.30.5", "127.0.0.1")
        assert fallen.problem is None

    def test_what_each_choice_serves(self):
        one = resolve(ListenChoice("adapter", ETHERNET.identity()), [ETHERNET, WIFI])
        assert one.serves("10.20.30.5") and one.serves("127.0.0.1")
        assert not one.serves("192.168.1.40")
        assert resolve(ListenChoice("any"), []).serves("192.168.1.40")
        local = resolve(ListenChoice("this_computer"), [])
        assert local.serves("localhost") and not local.serves("192.168.1.40")


class TestTheEnvironmentVariable:
    @pytest.mark.parametrize("raw, mode", [
        ("0.0.0.0", "any"),
        ("", "any"),
        ("::", "any"),
        ("127.0.0.1", "this_computer"),
        ("localhost", "this_computer"),
        ("::1", "this_computer"),
    ])
    def test_spellings(self, raw, mode):
        assert parse_bind_variable(raw) == (mode, {})

    def test_another_address_is_that_address(self):
        assert parse_bind_variable(" 10.20.30.5 ") == ("adapter", {"address": "10.20.30.5"})

    def test_it_holds_the_choice_and_locks_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAVC_BIND", "10.20.30.5")
        cfg = _config_at(tmp_path)
        assert cfg.get("network", "accept_connections_from") == "adapter"
        assert cfg.get("network", "accept_connections_adapter") == {"address": "10.20.30.5"}
        held = cfg.env_overrides()
        assert held["network.accept_connections_from"] == "OPENAVC_BIND"
        assert held["network.accept_connections_adapter"] == "OPENAVC_BIND"
        assert held["network.bind_address"] == "OPENAVC_BIND"
        assert ListenChoice.from_config(cfg).held_by == "OPENAVC_BIND"

    def test_settling_never_writes_the_environment_into_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAVC_BIND", "0.0.0.0")
        cfg = _config_at(tmp_path)
        listen.settle_choice(cfg, [], deployment_type="git_dev")
        cfg.save()
        network = _on_disk(tmp_path)["network"]
        assert network["accept_connections_from"] == "this_computer"
        assert network["bind_address"] == "127.0.0.1"
        assert cfg.get("network", "accept_connections_from") == "any"


class TestTheDefaultPerInstallType:
    @pytest.mark.parametrize("kind", sorted(listen.INSTALLED_TYPES))
    def test_every_installed_system_accepts_any_network(self, tmp_path, kind):
        # Nearly every installed system has the written default 127.0.0.1 in
        # its file; it never took effect there and must not decide anything.
        cfg = _config_at(tmp_path, {"network": {"bind_address": "127.0.0.1"}})
        assert listen.settle_choice(cfg, [], deployment_type=kind) is True
        assert cfg.get("network", "accept_connections_from") == "any"
        cfg.save()
        assert _on_disk(tmp_path)["network"]["accept_connections_from"] == "any"
        assert _on_disk(tmp_path)["network"]["bind_address"] == "0.0.0.0"

    @pytest.mark.parametrize("kind", ["git_dev", "unknown"])
    def test_a_run_from_source_keeps_this_computer_only(self, tmp_path, kind):
        cfg = _config_at(tmp_path)
        listen.settle_choice(cfg, [], deployment_type=kind)
        assert cfg.get("network", "accept_connections_from") == "this_computer"

    def test_from_source_keeps_a_wide_bind(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {"bind_address": "0.0.0.0"}})
        listen.settle_choice(cfg, [], deployment_type="git_dev")
        assert cfg.get("network", "accept_connections_from") == "any"

    def test_from_source_turns_an_address_into_its_adapter(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {"bind_address": "192.168.1.40"}})
        listen.settle_choice(cfg, [ETHERNET, WIFI], deployment_type="git_dev")
        assert cfg.get("network", "accept_connections_from") == "adapter"
        assert cfg.get("network", "accept_connections_adapter") == WIFI.identity()

    def test_from_source_keeps_an_address_no_adapter_holds(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {"bind_address": "10.9.9.9"}})
        listen.settle_choice(cfg, [ETHERNET], deployment_type="git_dev")
        assert cfg.get("network", "accept_connections_adapter") == {"address": "10.9.9.9"}
        plan = resolve(ListenChoice.from_config(cfg), [ETHERNET])
        assert plan.local_only and plan.problem.code == "address_not_found"

    def test_a_choice_already_made_is_kept(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {
            "accept_connections_from": "adapter",
            "accept_connections_adapter": ETHERNET.identity(),
        }})
        assert listen.settle_choice(cfg, [ETHERNET], deployment_type="windows_installer") is False
        assert cfg.get("network", "accept_connections_from") == "adapter"

    def test_a_value_that_is_not_a_choice_gets_the_default(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {"accept_connections_from": "everyone"}})
        listen.settle_choice(cfg, [], deployment_type="linux_package")
        assert cfg.get("network", "accept_connections_from") == "any"

    def test_adapters_are_only_enumerated_when_the_choice_needs_them(self, tmp_path):
        def refuse():
            raise AssertionError("enumerated adapters for a choice that names none")

        cfg = _config_at(tmp_path)
        listen.settle_choice(cfg, refuse, deployment_type="docker")
        assert cfg.get("network", "accept_connections_from") == "any"


class TestTheRollbackMirror:
    def test_an_adapter_choice_mirrors_its_address(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {
            "accept_connections_from": "adapter",
            "accept_connections_adapter": ETHERNET.identity(),
        }})
        listen.settle_choice(cfg, [ETHERNET], deployment_type="windows_installer")
        cfg.save()
        assert _on_disk(tmp_path)["network"]["bind_address"] == "10.20.30.5"

    def test_an_adapter_that_cannot_be_found_mirrors_to_loopback(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {
            "accept_connections_from": "adapter",
            "accept_connections_adapter": ETHERNET.identity(),
            "bind_address": "10.20.30.5",
        }})
        listen.settle_choice(cfg, [WIFI], deployment_type="windows_installer")
        cfg.save()
        assert _on_disk(tmp_path)["network"]["bind_address"] == "127.0.0.1"

    def test_this_computer_only_mirrors_to_loopback(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {"accept_connections_from": "this_computer"}})
        listen.settle_choice(cfg, [], deployment_type="macos_app")
        cfg.save()
        assert _on_disk(tmp_path)["network"]["bind_address"] == "127.0.0.1"


class TestStart:
    def test_start_settles_resolves_and_becomes_current(self, tmp_path):
        cfg = _config_at(tmp_path, {"network": {
            "accept_connections_from": "adapter",
            "accept_connections_adapter": WIFI.identity(),
        }})
        plan = listen.start(cfg, adapters=[ETHERNET, WIFI], deployment_type="linux_package")
        assert listen.current() is plan
        assert plan.hosts == ("192.168.1.40", "127.0.0.1")

    def test_a_choice_naming_no_adapter_never_enumerates(self, tmp_path, monkeypatch):
        def refuse():
            raise AssertionError("enumerated adapters for a choice that names none")

        monkeypatch.setattr(listen, "list_adapters", refuse)
        cfg = _config_at(tmp_path)
        plan = listen.start(cfg, deployment_type="docker")
        assert plan.hosts == ("0.0.0.0",)


class TestPreview:
    def _preview(self, choice, through, **kwargs):
        return listen.preview(
            choice, [ETHERNET, WIFI],
            connected_through=through,
            from_this_computer=kwargs.pop("from_this_computer", False),
            **kwargs,
        )

    def test_any_network_keeps_every_connection(self, monkeypatch):
        monkeypatch.setattr(
            "openavc.discovery.network_scanner.get_ranked_interface_ips",
            lambda: ["10.20.30.5", "192.168.1.40"],
        )
        result = self._preview(ListenChoice("any"), "192.168.1.40")
        assert result["keeps_this_connection"] and not result["safety_net"]
        assert result["panel_addresses"] == ["10.20.30.5", "192.168.1.40"]
        assert result["consequences"] == []

    def test_dropping_the_network_in_use_arms_the_safety_net(self):
        result = self._preview(ListenChoice("adapter", ETHERNET.identity()), "192.168.1.40")
        assert result["keeps_this_connection"] is False
        assert result["safety_net"] is True
        assert result["panel_addresses"] == ["10.20.30.5"]
        assert result["connected_through"] == "192.168.1.40"

    def test_staying_on_the_network_in_use_does_not(self):
        result = self._preview(ListenChoice("adapter", ETHERNET.identity()), "10.20.30.5")
        assert result["keeps_this_connection"] is True

    def test_this_computers_own_screen_and_the_tunnel_always_keep_access(self):
        assert self._preview(ListenChoice("this_computer"), "127.0.0.1")["keeps_this_connection"]
        tunnelled = self._preview(
            ListenChoice("this_computer"), "10.20.30.5", from_this_computer=True
        )
        assert tunnelled["keeps_this_connection"]

    def test_this_computer_only_lists_what_stops(self):
        result = self._preview(
            ListenChoice("this_computer"), "127.0.0.1",
            approved_panel_addresses=["10.20.30.20", "10.20.30.21", "192.168.1.9"],
            isc_enabled=True,
        )
        assert [c["message"] for c in result["consequences"]] == [
            "3 approved touch panels will stop connecting.",
            "Other OpenAVC systems can't reach this one (Inter-System is on).",
        ]

    def test_one_adapter_counts_only_panels_on_other_networks(self):
        result = self._preview(
            ListenChoice("adapter", ETHERNET.identity()), "10.20.30.5",
            approved_panel_addresses=["10.20.30.20", "192.168.1.9"],
            isc_enabled=True,
        )
        assert [c["message"] for c in result["consequences"]] == [
            "1 approved touch panel will stop connecting.",
        ]

    def test_a_missing_adapter_is_said(self):
        result = self._preview(ListenChoice("adapter", {"name": "Ethernet 9"}), "127.0.0.1")
        assert result["problem"]["code"] == "adapter_not_connected"


class TestTheWatcher:
    def _watcher(self, plan: ListenPlan, adapters: list[Adapter], **kwargs):
        restarts: list[str] = []

        async def restart(reason: str) -> None:
            restarts.append(reason)

        watcher = ListenWatcher(
            restart,
            adapters=lambda: adapters,
            plan=lambda: plan,
            interval=0,
            **kwargs,
        )
        return watcher, restarts

    def _run(self, watcher: ListenWatcher, polls: int) -> None:
        async def go():
            for _ in range(polls):
                await watcher.tick()

        asyncio.run(go())

    def test_nothing_changes_nothing_restarts(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [ETHERNET])
        watcher, restarts = self._watcher(plan, [ETHERNET])
        self._run(watcher, 5)
        assert restarts == []

    def test_a_new_dhcp_address_restarts_after_two_polls(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [ETHERNET])
        renewed = Adapter("Ethernet", ETHERNET.mac, (("10.20.30.77", "10.20.30.0/24"),), True)
        watcher, restarts = self._watcher(plan, [renewed])
        self._run(watcher, 1)
        assert restarts == []
        self._run(watcher, 1)
        assert restarts == [
            "the Ethernet adapter's address changed from 10.20.30.5 to 10.20.30.77"
        ]

    def test_the_adapter_coming_back_restarts(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [WIFI])
        watcher, restarts = self._watcher(plan, [ETHERNET, WIFI])
        self._run(watcher, 2)
        assert restarts == ["the Ethernet adapter is connected (10.20.30.5)"]

    def test_unplugging_restarts_onto_this_computer_only(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [ETHERNET])
        unplugged = Adapter("Ethernet", ETHERNET.mac, ETHERNET.addresses, False)
        watcher, restarts = self._watcher(plan, [unplugged])
        self._run(watcher, 2)
        assert restarts == ["the Ethernet adapter is no longer connected"]

    def test_a_refused_address_is_retried_by_a_trial_bind(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [ETHERNET]).without(
            ["10.20.30.5"]
        )
        allowed = {"now": False}
        watcher, restarts = self._watcher(
            plan, [ETHERNET], available=lambda ip: allowed["now"]
        )
        self._run(watcher, 3)
        assert restarts == []  # still refused: no restart loop
        allowed["now"] = True
        self._run(watcher, 2)
        assert restarts == ["the Ethernet adapter can be listened on now (10.20.30.5)"]

    def test_a_choice_naming_no_adapter_is_never_enumerated(self):
        def refuse():
            raise AssertionError("enumerated adapters for a choice that names none")

        ticks: list[int] = []

        async def on_tick() -> None:
            ticks.append(1)

        watcher = ListenWatcher(
            lambda reason: None,
            adapters=refuse,
            plan=lambda: resolve(ListenChoice("any"), []),
            on_tick=on_tick,
            interval=0,
        )
        self._run(watcher, 3)
        assert ticks == [1, 1, 1]

    def test_it_restarts_once(self):
        plan = resolve(ListenChoice("adapter", ETHERNET.identity()), [WIFI])
        watcher, restarts = self._watcher(plan, [ETHERNET, WIFI])
        self._run(watcher, 6)
        assert len(restarts) == 1
