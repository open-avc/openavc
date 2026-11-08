"""What the scan pipeline's phases hand each scanner and keep from it.

The listeners' start (phase 2), the host sweep (phase 3), the port scan
(phase 5) and the SNMP launch (phase 6), run through ``_scan_pipeline_inner``
with every network call stubbed, plus the per-host port scan and the signal
index's fallbacks on their own. The budget wiring is
test_discovery_budget_engine.py's; what the matcher makes of the evidence is
the tier matcher's tests'.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.discovery import icmp
from openavc.discovery import scan_budget as budgeting
from openavc.discovery.engine import DiscoveryEngine, PortScanSettings
from openavc.discovery.mdns_scanner import BASELINE_SERVICE_TYPES
from openavc.discovery.network_scanner import _parse_cidr
from openavc.discovery.scan_budget import ScanBudget, resolve_policy
from openavc.discovery.tier_matcher import SignalIndex

# Where each phase looks its scanners up.
LISTENERS = "openavc.discovery.engine"
SWEEP = "openavc.discovery.engine"
PORTS = "openavc.discovery.engine"
SNMP = "openavc.discovery.engine"


def _listener(start_attr: str, env_error: str | None = None) -> MagicMock:
    inst = MagicMock()
    setattr(inst, start_attr, AsyncMock(return_value={}))
    inst.stop = AsyncMock()
    inst.results = {}
    inst._running = True
    inst.env_error = env_error
    return inst


def _engine(depth: str = "standard", gentle: bool = False) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.config["scan_depth"] = depth
    engine.config["gentle_mode"] = gentle
    engine._budget = ScanBudget(resolve_policy(depth, gentle), 300.0)
    engine._narrowed = False
    return engine


class _Run:
    """One pass of ``_scan_pipeline_inner`` that records what each phase sent."""

    def __init__(self, engine: DiscoveryEngine) -> None:
        self.engine = engine
        self.alive: list[str] = []
        self.ping_subnets: list[list[str]] = []
        self.ping_kwargs: list[dict] = []
        self.ping_method = icmp.METHOD_DGRAM
        self.ping_errors = 0
        self.ping_total = 0
        self.progress_ticks: list[tuple[int, int]] = []
        self.scanned: list[tuple[str, list[int]]] = []
        self.open_ports: dict[str, list[int]] = {}
        self.collect_args: tuple | None = None
        self.env_errors: dict[str, str | None] = {"mdns": None, "ssdp": None, "amx": None}
        self.mdns_cls = MagicMock()
        self.ssdp_cls = MagicMock()
        self.amx_cls = MagicMock()
        self.snmp_cls = MagicMock()
        self.snmp_inst = MagicMock()
        self.snmp_inst.scan_devices = AsyncMock(return_value={})
        self.snmp_inst.results = {}
        self.snmp_cls.return_value = self.snmp_inst

    async def _ping(self, subnets, **kwargs):
        self.ping_subnets.append(list(subnets))
        self.ping_kwargs.append(kwargs)
        stats = kwargs["stats"]
        stats.method = self.ping_method
        stats.errors = self.ping_errors
        stats.total = self.ping_total
        for done, total in self.progress_ticks:
            await kwargs["on_progress"](done, total)
        for ip in self.alive:
            await kwargs["on_found"](ip)
        return list(self.alive)

    async def _scan_host_ports(self, ip, ports, timeout=1.0, source_ip="", guard=None):
        self.scanned.append((ip, list(ports)))
        return list(self.open_ports.get(ip, []))

    async def _collect(self, background, wait, alive_ips, followup):
        self.collect_args = (wait, list(alive_ips), followup)

    async def run(self, subnets=("10.77.0.0/24",), collect=True):
        engine = self.engine
        self.mdns_cls.return_value = _listener("start", self.env_errors["mdns"])
        self.ssdp_cls.return_value = _listener("scan", self.env_errors["ssdp"])
        self.amx_cls.return_value = _listener("start", self.env_errors["amx"])
        patches = [
            patch(f"{SWEEP}.ping_sweep", side_effect=self._ping),
            patch(f"{PORTS}.scan_host_ports", side_effect=self._scan_host_ports),
            patch(f"{PORTS}.grab_banners", new_callable=AsyncMock, return_value={}),
            patch("openavc.discovery.arp_phase.harvest_arp_table",
                  new_callable=AsyncMock, return_value={}),
            patch("openavc.discovery.arp_phase.netbios_sweep",
                  new_callable=AsyncMock, return_value={}),
            patch("openavc.discovery.arp_phase._resolve_hostnames",
                  new_callable=AsyncMock, return_value={}),
            patch.object(engine, "_run_custom_probes", new_callable=AsyncMock),
            patch.object(engine.community_index, "get_drivers",
                         new_callable=AsyncMock, return_value=[]),
            patch(f"{LISTENERS}.MDNSScanner", self.mdns_cls),
            patch(f"{LISTENERS}.SSDPScanner", self.ssdp_cls),
            patch(f"{LISTENERS}.AMXDDPScanner", self.amx_cls),
            patch(f"{SNMP}.SNMPScanner", self.snmp_cls),
        ]
        if not collect:
            patches.append(patch.object(engine, "_collect_phase", side_effect=self._collect))
        for p in patches:
            p.start()
        try:
            await engine._scan_pipeline_inner(list(subnets))
        finally:
            for p in reversed(patches):
                p.stop()


# --- Phase 2: the listeners ----------------------------------------------


class TestListeners:
    async def test_mdns_asks_for_the_baseline_then_each_driver_service_in_order(self):
        engine = _engine()
        engine.discovery_hints = [
            SimpleNamespace(mdns=[SimpleNamespace(service="_acme._tcp.local."),
                                  SimpleNamespace(service="")],
                            tcp_probe=None, port_open=[]),
            SimpleNamespace(mdns=[SimpleNamespace(service="_widget._udp.local.")],
                            tcp_probe=None, port_open=[]),
        ]
        run = _Run(engine)
        with patch.object(engine, "_get_control_interface", return_value="10.77.0.9"), \
             patch.object(engine, "_count_probe_jobs", return_value=(0, 0, 0, 0.0, 0.0)):
            await run.run()

        kwargs = run.mdns_cls.call_args.kwargs
        assert kwargs["service_types"] == list(BASELINE_SERVICE_TYPES) + [
            "_acme._tcp.local.", "_widget._udp.local.",
        ]
        assert kwargs["control_ip"] == "10.77.0.9"
        assert run.amx_cls.call_args.kwargs == {"control_ip": "10.77.0.9"}

    async def test_ssdp_fetches_descriptions_only_inside_the_scanned_subnets(self):
        engine = _engine()
        run = _Run(engine)
        await run.run(subnets=["10.77.0.0/24"])

        kwargs = run.ssdp_cls.call_args.kwargs
        assert kwargs["control_ip"] == ""
        assert kwargs["fetch_scope"]("10.77.0.40") is True
        assert kwargs["fetch_scope"]("10.78.0.40") is False
        ssdp = run.ssdp_cls.return_value
        ssdp.scan.assert_called_once_with(timeout=600.0, fetch_descriptions=True)
        run.mdns_cls.return_value.start.assert_called_once_with(duration=600.0)
        run.amx_cls.return_value.start.assert_called_once_with(duration=600.0)

    async def test_a_pin_ignored_for_this_scan_binds_nothing(self):
        engine = _engine()
        engine._scan_ignores_pin = True
        run = _Run(engine)
        with patch.object(engine, "_get_control_interface", return_value="10.77.0.9"):
            await run.run()
        assert run.mdns_cls.call_args.kwargs["control_ip"] == ""
        assert run.ping_kwargs[0]["source_ip"] == ""
        assert engine._scan_source_ip == ""

    async def test_each_listener_that_could_not_run_says_why_in_order(self):
        engine = _engine()
        run = _Run(engine)
        run.env_errors = {"mdns": "mDNS could not join", "ssdp": None, "amx": "AMX could not bind"}
        await run.run()
        warnings = engine.scan_status.warnings
        assert "mDNS could not join" in warnings
        assert "AMX could not bind" in warnings
        assert warnings.index("mDNS could not join") < warnings.index("AMX could not bind")
        assert sum(1 for w in warnings if "could not" in w) == 2


# --- Phase 3: the host sweep ---------------------------------------------


class TestSweep:
    async def test_the_sweep_gets_the_plan_the_limit_and_the_source(self):
        engine = _engine()
        engine.config["max_subnet_size"] = 22
        run = _Run(engine)
        plan = budgeting.PingPlan(concurrency=37, max_hosts=None, budget=50.0)
        with patch.object(budgeting, "plan_ping_sweep", return_value=plan) as planner, \
             patch.object(engine, "_get_control_interface", return_value="10.77.0.9"):
            await run.run(subnets=["10.77.0.0/24"])

        planner.assert_called_once_with(engine._budget, 254, False)
        kwargs = run.ping_kwargs[0]
        assert run.ping_subnets == [["10.77.0.0/24"]]
        assert kwargs["concurrency"] == 37
        assert kwargs["min_prefix"] == 22
        assert kwargs["source_ip"] == "10.77.0.9"
        assert kwargs["max_hosts"] is None
        assert isinstance(kwargs["stats"], icmp.PingSweepStats)
        assert engine._scan_source_ip == "10.77.0.9"

    async def test_gentle_mode_reaches_the_sweep_plan(self):
        engine = _engine(gentle=True)
        run = _Run(engine)
        plan = budgeting.PingPlan(concurrency=5, max_hosts=None, budget=50.0)
        with patch.object(budgeting, "plan_ping_sweep", return_value=plan) as planner:
            await run.run(subnets=["10.77.0.0/30"])
        planner.assert_called_once_with(engine._budget, 2, True)

    @pytest.mark.parametrize("subnets, min_prefix, expected", [
        (["10.77.0.0/24"], 20, 254),
        (["10.77.0.0/24", "garbage", "10.60.0.0/16"], 20, 254),
        (["10.60.0.0/16"], 16, 65534),
        (["10.77.0.4/31"], 20, 2),
        (["10.77.0.9/32"], 20, 1),
        (["10.77.0.0/30", "10.77.1.0/30"], 20, 4),
        (["not-a-cidr"], 20, 0),
        (["10.77.0.0/19"], 20, 0),
    ])
    async def test_hosts_scanned_counts_exactly_what_the_sweep_expands(
        self, subnets, min_prefix, expected,
    ):
        engine = _engine()
        engine.config["max_subnet_size"] = min_prefix
        run = _Run(engine)
        plan = budgeting.PingPlan(concurrency=10, max_hosts=None, budget=50.0)
        with patch.object(budgeting, "plan_ping_sweep", return_value=plan) as planner:
            await run.run(subnets=subnets)

        assert engine.scan_status.total_hosts_scanned == expected
        assert planner.call_args.args[1] == expected
        expanded = sum(len(_parse_cidr(s, min_prefix=min_prefix)) for s in subnets)
        assert expanded == expected

    async def test_a_capped_sweep_reports_the_cap_as_hosts_scanned(self):
        engine = _engine()
        run = _Run(engine)
        plan = budgeting.PingPlan(concurrency=10, max_hosts=40, budget=50.0,
                                  warnings=["Skipped 214 addresses."])
        with patch.object(budgeting, "plan_ping_sweep", return_value=plan):
            await run.run(subnets=["10.77.0.0/24"])
        assert engine.scan_status.total_hosts_scanned == 40
        assert run.ping_kwargs[0]["max_hosts"] == 40
        assert "Skipped 214 addresses." in engine.scan_status.warnings
        assert engine._narrowed is True

    async def test_a_found_host_is_alive_and_announced_from_the_sweep(self):
        engine = _engine()
        run = _Run(engine)
        run.alive = ["10.77.0.5"]
        sent: list[dict] = []

        async def _on_update(msg):
            sent.append(msg)

        engine._on_update = _on_update
        await run.run()
        assert engine.results["10.77.0.5"].alive is True
        first = next(m for m in sent if m["type"] == "discovery.update")
        assert first["phase"] == "ping_sweep"
        assert first["device"]["ip"] == "10.77.0.5"

    async def test_the_method_the_sweep_settled_on_is_kept_for_phase_4(self):
        engine = _engine()
        run = _Run(engine)
        run.ping_method = "system_ping"
        await run.run()
        assert engine._ping_method == "system_ping"

    async def test_no_way_to_ping_is_named(self):
        engine = _engine()
        run = _Run(engine)
        run.ping_method = icmp.METHOD_NONE
        run.ping_errors = 254
        await run.run()
        assert engine.scan_status.warnings.count(
            "Host scan could not run: this system has no permission "
            "to send pings and no ping command was found. Only "
            "devices that announce themselves can be discovered."
        ) == 1
        assert not any("Pings failed" in w for w in engine.scan_status.warnings)

    async def test_pings_that_errored_are_counted_in_the_warning(self):
        engine = _engine()
        run = _Run(engine)
        run.ping_errors = 3
        run.ping_total = 254
        await run.run()
        assert (
            "Pings failed with system errors on 3 of 254 addresses (not "
            "timeouts) — results may be incomplete."
        ) in engine.scan_status.warnings

    async def test_a_clean_sweep_warns_nothing(self):
        engine = _engine()
        run = _Run(engine)
        await run.run()
        assert not any("ping" in w.lower() for w in engine.scan_status.warnings)

    async def test_sweep_progress_moves_the_bar_inside_phase_3(self):
        engine = _engine()
        run = _Run(engine)
        run.progress_ticks = [(0, 0), (127, 254)]
        seen: list[float] = []
        real = engine._update_intra_progress

        async def _spy(fraction):
            seen.append(fraction)
            await real(fraction)

        with patch.object(engine, "_update_intra_progress", side_effect=_spy):
            await run.run()
        # (0, 0) is skipped: nothing to divide by. The port scan's own
        # ticks come after and are not the sweep's.
        assert seen[0] == pytest.approx(0.5)


# --- Phase 5: the port scan ----------------------------------------------


class TestPortPhase:
    async def test_alive_hosts_are_scanned_with_the_planned_ports_and_width(self):
        engine = _engine()
        run = _Run(engine)
        run.alive = ["10.77.0.5", "10.77.0.6", "10.77.0.7"]
        plan = budgeting.PortScanPlan(ports=[22, 80], host_limit=2, concurrency=4, budget=40.0)
        with patch.object(budgeting, "plan_port_scan", return_value=plan) as planner:
            await run.run(collect=False)

        args = planner.call_args.args
        assert args[0] is engine._budget
        assert args[1] == 3
        assert args[4] == engine._budget.policy.port_host_concurrency
        assert set(args[3]) <= set(args[2])
        assert sorted(ip for ip, _ in run.scanned) == ["10.77.0.5", "10.77.0.6"]
        assert all(ports == [22, 80] for _, ports in run.scanned)
        # Phase 7's follow-up scans with the narrowed list at the policy's width.
        wait, alive, followup = run.collect_args
        assert alive == ["10.77.0.5", "10.77.0.6", "10.77.0.7"]
        assert followup == PortScanSettings(
            [22, 80], engine._budget.policy.port_host_concurrency, False,
        )

    async def test_with_no_alive_host_the_follow_up_keeps_the_full_list(self):
        engine = _engine(gentle=True)
        run = _Run(engine)
        with patch.object(budgeting, "plan_port_scan") as planner:
            await run.run(collect=False)
        planner.assert_not_called()
        assert run.scanned == []
        _, alive, followup = run.collect_args
        assert alive == []
        _, full = engine.port_scan_lists([], extended=engine._budget.policy.extended_ports)
        assert followup == PortScanSettings(full, engine._budget.policy.port_host_concurrency, True)

    async def test_a_thorough_scan_adds_the_extra_ports(self):
        engine = _engine(depth="thorough")
        run = _Run(engine)
        run.alive = ["10.77.0.5"]
        await run.run()
        _, full = engine.port_scan_lists([], extended=True)
        _, standard = engine.port_scan_lists([], extended=False)
        assert set(full) > set(standard)
        assert run.scanned[0][1] == full


class TestPortScanHosts:
    async def _scan(self, engine, hosts, settings, opened, banners, **kw):
        scan = AsyncMock(side_effect=lambda ip, ports, **_: list(opened.get(ip, [])))
        grab = AsyncMock(side_effect=lambda ip, ports, **_: dict(banners.get(ip, {})))
        sent: list[dict] = []

        async def _on_update(msg):
            sent.append(msg)

        engine._on_update = _on_update
        engine._scan_source_ip = "10.77.0.9"
        with patch(f"{PORTS}.scan_host_ports", scan), patch(f"{PORTS}.grab_banners", grab), \
             patch(f"{PORTS}.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await engine._port_scan_hosts(hosts, settings, **kw)
        return scan, grab, sleep, sent

    async def test_nothing_to_scan_touches_nothing(self):
        engine = _engine()
        for hosts, ports in (([], [80]), (["10.77.0.5"], [])):
            scan, grab, _, sent = await self._scan(
                engine, hosts, PortScanSettings(ports, 4, False), {}, {},
            )
            scan.assert_not_called()
            assert engine.results == {}
            assert engine._port_scanned == set()
            assert sent == []

    async def test_open_ports_and_banners_land_on_the_device(self):
        engine = _engine()
        scan, grab, sleep, sent = await self._scan(
            engine, ["10.77.0.5", "10.77.0.6"], PortScanSettings([23, 80], 2, False),
            {"10.77.0.5": [23]}, {"10.77.0.5": {23: "Acme Widget v1"}},
        )
        dev = engine.results["10.77.0.5"]
        assert dev.open_ports == [23]
        assert dev.banners == {23: "Acme Widget v1"}
        scan.assert_any_call("10.77.0.5", [23, 80], timeout=1.0,
                             source_ip="10.77.0.9", guard=engine._guard)
        grab.assert_called_once_with("10.77.0.5", [23], timeout=2.0, source_ip="10.77.0.9")
        # A host with nothing open is scanned, recorded and not announced.
        assert engine._port_scanned == {"10.77.0.5", "10.77.0.6"}
        assert "10.77.0.6" in engine.results
        assert [m["device"]["ip"] for m in sent if m["type"] == "discovery.update"] == ["10.77.0.5"]
        assert all(m["phase"] == "port_scan" for m in sent if m["type"] == "discovery.update")
        sleep.assert_not_called()

    async def test_no_banner_leaves_the_banners_empty(self):
        engine = _engine()
        await self._scan(
            engine, ["10.77.0.5"], PortScanSettings([80], 1, False), {"10.77.0.5": [80]}, {},
        )
        assert engine.results["10.77.0.5"].banners == {}

    async def test_a_follow_up_announces_every_host_under_its_phase(self):
        engine = _engine()
        _, _, _, sent = await self._scan(
            engine, ["10.77.0.5"], PortScanSettings([80], 1, False), {}, {},
            phase="passive_followup", always_emit=True,
        )
        updates = [m for m in sent if m["type"] == "discovery.update"]
        assert [(m["device"]["ip"], m["phase"]) for m in updates] == [
            ("10.77.0.5", "passive_followup"),
        ]

    async def test_gentle_mode_pauses_after_each_host(self):
        engine = _engine()
        _, _, sleep, _ = await self._scan(
            engine, ["10.77.0.5", "10.77.0.6"], PortScanSettings([80], 1, True), {}, {},
        )
        assert sleep.await_count == 2
        sleep.assert_awaited_with(0.1)

    async def test_each_host_done_moves_the_bar(self):
        engine = _engine()
        seen: list[float] = []

        async def _spy(fraction):
            seen.append(fraction)

        with patch.object(engine, "_update_intra_progress", side_effect=_spy):
            await self._scan(
                engine, ["10.77.0.5", "10.77.0.6", "10.77.0.7", "10.77.0.8"],
                PortScanSettings([80], 1, False), {}, {},
            )
        assert seen == [0.25, 0.5, 0.75, 1.0]

    async def test_a_width_below_one_still_scans(self):
        engine = _engine()
        scan, _, _, _ = await self._scan(
            engine, ["10.77.0.5"], PortScanSettings([80], 0, False), {}, {},
        )
        assert scan.await_count == 1


# --- Phase 6: the SNMP launch --------------------------------------------


class TestSnmpLaunch:
    @pytest.mark.parametrize("gentle, width", [(False, 20), (True, 10)])
    async def test_snmp_asks_every_alive_host_with_the_scan_settings(self, gentle, width):
        engine = _engine(gentle=gentle)
        engine.config["snmp_community"] = "acme-ro"
        run = _Run(engine)
        run.alive = ["10.77.0.5", "10.77.0.6"]
        with patch.object(engine, "_get_control_interface", return_value="10.77.0.9"):
            await run.run()
        run.snmp_cls.assert_called_once_with(source_ip="10.77.0.9")
        run.snmp_inst.scan_devices.assert_called_once_with(
            ["10.77.0.5", "10.77.0.6"], community="acme-ro", timeout=2.0,
            concurrency=width, entity_mib=engine._budget.policy.snmp_entity_mib,
        )

    async def test_no_snmp_when_it_is_off_or_nobody_answered_the_sweep(self):
        engine = _engine()
        engine.config["snmp_enabled"] = False
        run = _Run(engine)
        run.alive = ["10.77.0.5"]
        await run.run()
        run.snmp_cls.assert_not_called()

        engine = _engine()
        run = _Run(engine)
        await run.run()
        run.snmp_cls.assert_not_called()


# --- The phases in order -------------------------------------------------


async def test_the_pipeline_announces_phases_1_to_7_in_order():
    engine = _engine()
    run = _Run(engine)
    run.alive = ["10.77.0.5"]
    calls: list[tuple[int, str, str]] = []
    real = engine._set_phase

    async def _spy(number, phase, message):
        calls.append((number, phase, message))
        await real(number, phase, message)

    with patch.object(engine, "_set_phase", side_effect=_spy):
        await run.run()
    assert calls == [
        (1, "subnet_detection", "Detecting network interfaces..."),
        (2, "passive_listen", "Starting mDNS, SSDP, and AMX DDP listeners..."),
        (3, "ping_sweep", "Scanning for live hosts..."),
        (4, "arp_harvest", "Reading MAC addresses and hostnames..."),
        (5, "port_scan", "Probing device ports..."),
        (6, "protocol_probe", "Identifying device protocols..."),
        (7, "passive_collect", "Collecting passive and SNMP results..."),
    ]


# --- The signal index's fallbacks ------------------------------------------


def _named_hint(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, oui=[], manufacturer="", category="",
                           mdns=[], tcp_probe=None, port_open=[])


class TestSignalIndexFallback:
    def test_a_catalog_the_index_rejects_falls_back_to_the_installed_drivers(self, caplog):
        engine = DiscoveryEngine()
        installed_index = SignalIndex()
        calls: list[list] = []

        def _build(hints):
            calls.append(list(hints))
            if len(calls) == 1:
                raise ValueError("two rules collide")
            return installed_index

        with patch("openavc.discovery.engine.build_signal_index", side_effect=_build), \
             patch("openavc.discovery.engine.load_discovery_hints",
                   side_effect=lambda drivers: [_named_hint(d["id"]) for d in drivers]), \
             caplog.at_level(logging.ERROR, logger="discovery"):
            engine._installed_registry = [{"id": "acme_widget", "version": "1.0.0"}]
            engine._rebuild_signal_index([{"id": "acme_gadget", "version": "1.0.0"}])

        assert [[h.name for h in c] for c in calls] == [
            ["acme_widget", "acme_gadget"], ["acme_widget"],
        ]
        assert engine.signal_index is installed_index
        assert engine.tier_matcher.index is installed_index
        assert [h.name for h in engine.discovery_hints] == ["acme_widget", "acme_gadget"]
        assert (
            "Discovery signal index rejected with catalog: two rules collide; "
            "falling back to installed-only"
        ) in caplog.messages

    def test_an_installed_index_rejected_too_leaves_an_empty_one(self, caplog):
        engine = DiscoveryEngine()
        with patch("openavc.discovery.engine.build_signal_index",
                   side_effect=ValueError("bad")), \
             caplog.at_level(logging.ERROR, logger="discovery"):
            engine._rebuild_signal_index([])
        assert isinstance(engine.signal_index, SignalIndex)
        assert engine.signal_index.driver_count() == 0
        assert "Installed-only signal index also rejected: bad" in caplog.messages

    def test_a_catalog_entry_with_no_id_is_left_out(self):
        engine = DiscoveryEngine()
        seen: list[list] = []
        with patch("openavc.discovery.engine.load_discovery_hints",
                   side_effect=lambda drivers: seen.append(list(drivers)) or []):
            engine._rebuild_signal_index([{"id": "", "version": "9.0.0"},
                                          {"version": "9.0.0"},
                                          {"id": "acme_gadget"}])
        assert seen[1] == [{"id": "acme_gadget"}]
        assert "" not in engine._driver_categories

    def test_each_new_oui_prefix_is_counted_once(self, caplog):
        engine = DiscoveryEngine()
        hints = [
            SimpleNamespace(oui=["00:11:22", "00:11:22"], manufacturer="Acme", category="display",
                            mdns=[], tcp_probe=None, port_open=[]),
            SimpleNamespace(oui=["00:11:23"], manufacturer="Acme", category="display",
                            mdns=[], tcp_probe=None, port_open=[]),
        ]
        with patch("openavc.discovery.engine.load_discovery_hints",
                   side_effect=[hints, []]), \
             patch("openavc.discovery.engine.build_signal_index", return_value=SignalIndex()), \
             caplog.at_level(logging.INFO, logger="discovery"):
            engine._rebuild_signal_index([])
        assert engine.oui_db.lookup("00:11:22:33:44:55") is not None
        assert any("2 new OUI prefixes" in m for m in caplog.messages)
