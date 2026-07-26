"""The engine runs each scan phase under its own workload-derived budget.

Companion to test_discovery_scan_budget.py, which covers the projections and
narrowing ladders in isolation. These cover the wiring: that the plans reach
the scanners, that a phase which overruns takes only itself down, and that a
scan covering less than was asked for says so.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from server.discovery import scan_budget as budgeting
from server.discovery.engine import DiscoveryEngine
from server.discovery.scan_budget import ScanBudget, resolve_policy


def _mock_passive_scanners():
    """Passive listener + SNMP classes that resolve instantly."""
    mdns_cls = MagicMock()
    mdns = MagicMock()
    mdns.start = AsyncMock(return_value={})
    mdns.results = {}
    mdns._running = True
    mdns.env_error = None
    mdns_cls.return_value = mdns

    ssdp_cls = MagicMock()
    ssdp = MagicMock()
    ssdp.scan = AsyncMock(return_value={})
    ssdp.results = {}
    ssdp._running = True
    ssdp.env_error = None
    ssdp_cls.return_value = ssdp

    amx_cls = MagicMock()
    amx = MagicMock()
    amx.start = AsyncMock(return_value={})
    amx.stop = AsyncMock()
    amx.results = {}
    amx.env_error = None
    amx_cls.return_value = amx

    snmp_cls = MagicMock()
    snmp = MagicMock()
    snmp.scan_devices = AsyncMock(return_value={})
    snmp.results = {}
    snmp_cls.return_value = snmp

    return mdns_cls, ssdp_cls, amx_cls, snmp_cls


class _Pipeline:
    """Runs `_scan_pipeline_inner` with every network call stubbed out."""

    def __init__(self, engine: DiscoveryEngine) -> None:
        self.engine = engine
        self.ping_calls: list[dict] = []
        self.port_scan_hosts: list[str] = []
        self.inflight = 0
        self.max_inflight = 0
        self.probe_calls: list[dict] = []
        self.ping_delay = 0.0
        self.port_delay = 0.0
        self.alive: list[str] = []

    async def _fake_ping(self, subnets, **kwargs):
        self.ping_calls.append(kwargs)
        # Report hosts as they respond, like the real sweep — that is what
        # makes them recoverable when the phase is cut short.
        for ip in self.alive:
            await kwargs["on_found"](ip)
        if self.ping_delay:
            await asyncio.sleep(self.ping_delay)
        return list(self.alive)

    async def _fake_scan_host_ports(self, ip, ports, timeout=1.0):
        self.port_scan_hosts.append(ip)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(self.port_delay or 0.01)
        finally:
            self.inflight -= 1
        return []

    async def _fake_probes(self, subnets, control_ip, max_tcp_jobs=None):
        self.probe_calls.append(
            {"subnets": subnets, "max_tcp_jobs": max_tcp_jobs}
        )

    async def run(self, subnets=("10.77.0.0/24",)):
        mdns_cls, ssdp_cls, amx_cls, snmp_cls = _mock_passive_scanners()
        engine = self.engine
        with patch("server.discovery.engine.ping_sweep", side_effect=self._fake_ping), \
             patch("server.discovery.engine.scan_host_ports",
                   side_effect=self._fake_scan_host_ports), \
             patch("server.discovery.engine.grab_banners",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine.harvest_arp_table",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine.netbios_sweep",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine._resolve_hostnames",
                   new_callable=AsyncMock, return_value={}), \
             patch.object(engine, "_run_custom_probes", side_effect=self._fake_probes), \
             patch.object(engine.community_index, "get_drivers",
                          new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.MDNSScanner", mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", ssdp_cls), \
             patch("server.discovery.engine.AMXDDPScanner", amx_cls), \
             patch("server.discovery.engine.SNMPScanner", snmp_cls):
            await engine._scan_pipeline_inner(list(subnets))


def _engine(total_seconds: float = 300.0, depth: str = "standard") -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.config["scan_depth"] = depth
    engine._budget = ScanBudget(resolve_policy(depth), total_seconds)
    engine._narrowed = False
    return engine


class TestBudgetRidesTheStatus:
    async def test_phase_budgets_are_reported(self):
        engine = _engine()
        pipeline = _Pipeline(engine)
        pipeline.alive = ["10.77.0.5"]
        await pipeline.run()

        budgets = engine.scan_status.phase_budgets
        # Every phase that ran has a recorded deadline, so a support case can
        # see where the time was allowed to go, not just where it went.
        for key in (budgeting.PHASE_PING, budgeting.PHASE_ARP,
                    budgeting.PHASE_PORT, budgeting.PHASE_PASSIVE,
                    budgeting.PHASE_PROBE):
            assert budgets.get(key, 0) > 0, key
        assert sum(budgets.values()) <= engine._budget.total_seconds

    async def test_status_dict_carries_budget_fields(self):
        engine = _engine()
        engine.scan_status.budget_seconds = 300.0
        status = engine.get_status()
        assert status["budget_seconds"] == 300.0
        assert status["phase_budgets"] == {}


class TestDepthSetsTheCeiling:
    async def test_no_explicit_timeout_takes_the_depth_policy(self):
        engine = DiscoveryEngine()
        engine.config["scan_depth"] = "thorough"
        with patch.object(engine, "_run_scan", new_callable=AsyncMock):
            await engine.start_scan(subnets=["10.77.0.0/30"])
        assert engine.scan_status.budget_seconds == resolve_policy("thorough").total_seconds

    async def test_explicit_timeout_still_caps_the_scan(self):
        engine = DiscoveryEngine()
        with patch.object(engine, "_run_scan", new_callable=AsyncMock):
            await engine.start_scan(subnets=["10.77.0.0/30"], timeout=45.0)
        assert engine.scan_status.budget_seconds == 45.0
        # ... and the phases divide it rather than the early ones spending it
        # all: every share is measured against the override.
        assert engine._budget.total_seconds == 45.0

    async def test_gentle_mode_reaches_the_budget_policy(self):
        engine = DiscoveryEngine()
        engine.config["gentle_mode"] = True
        with patch.object(engine, "_run_scan", new_callable=AsyncMock):
            await engine.start_scan(subnets=["10.77.0.0/30"])
        assert (
            engine._budget.policy.ping_concurrency
            == budgeting.GENTLE_PING_CONCURRENCY
        )


class TestPlansReachTheScanners:
    async def test_a_sweep_that_fits_is_not_capped(self):
        engine = _engine()
        pipeline = _Pipeline(engine)
        await pipeline.run(subnets=["10.77.0.0/24"])
        assert pipeline.ping_calls[0]["max_hosts"] is None

    async def test_an_oversized_sweep_is_capped_and_the_scan_says_so(self):
        # A /16 with a deliberately small ceiling: the sweep cannot cover it,
        # so it covers what it can and names what it skipped.
        engine = _engine(total_seconds=30.0)
        engine.config["max_subnet_size"] = 16
        engine.config["gentle_mode"] = True
        engine._budget = ScanBudget(resolve_policy("standard", gentle=True), 30.0)
        pipeline = _Pipeline(engine)
        await pipeline.run(subnets=["10.60.0.0/16"])

        assert pipeline.ping_calls[0]["max_hosts"] is not None
        assert engine._narrowed is True
        assert any("skipped" in w for w in engine.scan_status.warnings)

    async def test_port_scan_overlaps_hosts_instead_of_running_them_serially(self):
        engine = _engine()
        pipeline = _Pipeline(engine)
        pipeline.alive = [f"10.77.0.{i}" for i in range(1, 21)]
        pipeline.port_delay = 0.05
        await pipeline.run()

        # The budget formula's effective_concurrency term was silently 1
        # before: hosts were scanned strictly one after another, so there was
        # no parallelism to trade for time.
        assert pipeline.max_inflight > 1
        assert pipeline.max_inflight <= engine._budget.policy.port_host_concurrency

    async def test_probe_phase_is_planned_with_a_job_cap(self):
        engine = _engine()
        pipeline = _Pipeline(engine)
        pipeline.alive = ["10.77.0.5"]
        await pipeline.run()
        assert len(pipeline.probe_calls) == 1
        # No hints loaded, so nothing to cap.
        assert pipeline.probe_calls[0]["max_tcp_jobs"] is None


class TestOverrunIsContained:
    async def test_a_phase_that_overruns_does_not_stop_the_later_phases(self):
        """The whole point of budgeting per phase.

        Under the old flat stopwatch, a sweep that ran long consumed the
        entire scan and the probe phase never executed. Now the sweep is cut
        at its own deadline and everything downstream still runs.
        """
        engine = _engine(total_seconds=12.0)
        pipeline = _Pipeline(engine)
        pipeline.ping_delay = 30.0  # far past the sweep's share of 12s
        await pipeline.run()

        assert pipeline.probe_calls, "the probe phase must still run"
        assert engine._narrowed is True
        assert any("host sweep" in w.lower() for w in engine.scan_status.warnings)

    async def test_hosts_found_before_the_cut_are_kept(self):
        engine = _engine(total_seconds=12.0)
        pipeline = _Pipeline(engine)
        pipeline.alive = ["10.77.0.5", "10.77.0.6"]
        pipeline.ping_delay = 30.0
        await pipeline.run()

        # on_found already recorded them, so the identification phases run on
        # what the sweep reached rather than the phase being lost whole.
        assert set(pipeline.port_scan_hosts) == {"10.77.0.5", "10.77.0.6"}


class TestNarrowedScansAreNotComplete:
    async def test_a_narrowed_scan_reports_partial(self):
        engine = _engine(total_seconds=12.0)
        pipeline = _Pipeline(engine)
        pipeline.ping_delay = 30.0

        async def fake_pipeline(subnets):
            await pipeline.run(subnets=subnets)

        engine.scan_status.started_at = 0.0
        engine._budget = ScanBudget(resolve_policy("standard"), 12.0)
        with patch.object(engine, "_scan_pipeline", side_effect=fake_pipeline):
            await engine._run_scan(["10.77.0.0/24"], timeout=60.0)

        # Results that cover less of the network than was asked for are not a
        # complete scan, even though nothing errored and the outer deadline
        # was never reached.
        assert engine.scan_status.status == "partial"
        assert engine.scan_status.warnings

    async def test_an_unnarrowed_scan_still_reports_complete(self):
        engine = _engine()
        pipeline = _Pipeline(engine)
        pipeline.alive = ["10.77.0.5"]

        async def fake_pipeline(subnets):
            await pipeline.run(subnets=subnets)

        engine.scan_status.started_at = 0.0
        with patch.object(engine, "_scan_pipeline", side_effect=fake_pipeline):
            await engine._run_scan(["10.77.0.0/24"], timeout=60.0)

        assert engine.scan_status.status == "complete"
