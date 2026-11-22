"""Engine surfaces environment failures as scan-status warnings.

Total environment failure (no ping capability, listeners that never ran)
must be distinguishable from "empty network" — the warnings list rides the
scan status, the /api/discovery/results payload, and the WS events.

A listener's failure is added when the listeners stop, which every ending
does: a scan cut short by its time limit or an error, or stopped, still says
which listener could not work, and so does the assistant's answer.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from openavc.api import discovery as discovery_api
from openavc.cloud.tools.system_tools import SystemToolsMixin
from openavc.discovery import icmp
from openavc.discovery.engine import DiscoveryEngine, ScanStatus


def _mock_passive_scanners(
    mdns_err: str | None = None,
    ssdp_err: str | None = None,
    amx_err: str | None = None,
):
    """Mocked passive scanner classes with explicit env_error values."""
    mdns_cls = MagicMock()
    mdns = MagicMock()
    mdns.start = AsyncMock(return_value={})
    mdns._running = True
    mdns.env_error = mdns_err
    mdns_cls.return_value = mdns

    ssdp_cls = MagicMock()
    ssdp = MagicMock()
    ssdp.scan = AsyncMock(return_value={})
    ssdp._running = True
    ssdp.env_error = ssdp_err
    ssdp_cls.return_value = ssdp

    amx_cls = MagicMock()
    amx = MagicMock()
    amx.start = AsyncMock(return_value={})
    amx.stop = AsyncMock()
    amx.env_error = amx_err
    amx_cls.return_value = amx

    snmp_cls = MagicMock()
    snmp = MagicMock()
    snmp.scan_devices = AsyncMock(return_value={})
    snmp_cls.return_value = snmp

    return mdns_cls, ssdp_cls, amx_cls, snmp_cls


def _make_fake_ping(method: str, total: int, errors: int):
    async def fake_ping(subnets, *, concurrency, on_found, on_progress,
                        min_prefix, source_ip, stats=None, max_hosts=None):
        if stats is not None:
            stats.method = method
            stats.total = total
            stats.errors = errors
        return []

    return fake_ping


class TestScanWarnings:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def _run_pipeline(self, fake_ping, scanner_mocks):
        mdns_cls, ssdp_cls, amx_cls, snmp_cls = scanner_mocks
        with patch("openavc.discovery.sweep_phase.ping_sweep", side_effect=fake_ping), \
             patch("openavc.discovery.passive_phase.MDNSScanner", mdns_cls), \
             patch("openavc.discovery.passive_phase.SSDPScanner", ssdp_cls), \
             patch("openavc.discovery.passive_phase.AMXDDPScanner", amx_cls), \
             patch("openavc.discovery.passive_phase.SNMPScanner", snmp_cls), \
             patch("openavc.discovery.arp_phase._resolve_hostnames",
                   new_callable=AsyncMock, return_value={}):
            await self.engine._scan_pipeline_inner(["192.168.1.0/30"])

    async def test_no_ping_method_produces_warning(self):
        fake_ping = _make_fake_ping(icmp.METHOD_NONE, total=254, errors=254)
        await self._run_pipeline(fake_ping, _mock_passive_scanners())

        warnings = self.engine.scan_status.warnings
        assert any("Host scan could not run" in w for w in warnings)

    async def test_partial_ping_errors_produce_warning(self):
        fake_ping = _make_fake_ping(icmp.METHOD_DGRAM, total=254, errors=40)
        await self._run_pipeline(fake_ping, _mock_passive_scanners())

        warnings = self.engine.scan_status.warnings
        assert any("40 of 254" in w for w in warnings)

    async def test_clean_sweep_produces_no_warnings(self):
        fake_ping = _make_fake_ping(icmp.METHOD_DGRAM, total=254, errors=0)
        await self._run_pipeline(fake_ping, _mock_passive_scanners())

        assert self.engine.scan_status.warnings == []

    async def test_listener_env_errors_become_warnings(self):
        fake_ping = _make_fake_ping(icmp.METHOD_RAW, total=2, errors=0)
        scanner_mocks = _mock_passive_scanners(
            mdns_err="mDNS listener unavailable: could not join group",
            ssdp_err=None,
            amx_err="AMX DDP listener unavailable: could not join group",
        )
        await self._run_pipeline(fake_ping, scanner_mocks)

        warnings = self.engine.scan_status.warnings
        assert "mDNS listener unavailable: could not join group" in warnings
        assert "AMX DDP listener unavailable: could not join group" in warnings
        assert len(warnings) == 2

    async def test_warnings_ride_status_dict_and_complete_event(self):
        import time

        fake_ping = _make_fake_ping(icmp.METHOD_NONE, total=254, errors=254)

        events = []
        self.engine.on_update = AsyncMock(side_effect=lambda m: events.append(m))
        self.engine.scan_status.started_at = time.time()
        self.engine.scan_status.scan_id = "test_scan"

        async def run_inner(subnets):
            await self._run_pipeline(fake_ping, _mock_passive_scanners())

        with patch.object(self.engine, "_scan_pipeline", side_effect=run_inner):
            await self.engine._run_scan(["192.168.1.0/30"], timeout=10.0)

        status = self.engine.get_status()
        assert any("Host scan could not run" in w for w in status["warnings"])

        complete = [e for e in events if e.get("type") == "discovery.complete"]
        assert len(complete) == 1
        assert any("Host scan could not run" in w for w in complete[0]["warnings"])

    def test_scan_status_dict_always_has_warnings_key(self):
        assert ScanStatus().to_dict()["warnings"] == []


MDNS_DOWN = "mDNS listener unavailable: could not join group"


@contextmanager
def _scan_that_reaches_the_arp_phase(engine: DiscoveryEngine, arp_phase):
    """The real phases with mocked listeners (mDNS could not join its group)
    and an empty sweep, the ARP phase replaced by ``arp_phase``."""
    mdns_cls, ssdp_cls, amx_cls, snmp_cls = _mock_passive_scanners(mdns_err=MDNS_DOWN)
    fake_ping = _make_fake_ping(icmp.METHOD_RAW, total=2, errors=0)
    with patch("openavc.discovery.sweep_phase.ping_sweep", side_effect=fake_ping), \
         patch("openavc.discovery.passive_phase.MDNSScanner", mdns_cls), \
         patch("openavc.discovery.passive_phase.SSDPScanner", ssdp_cls), \
         patch("openavc.discovery.passive_phase.AMXDDPScanner", amx_cls), \
         patch("openavc.discovery.passive_phase.SNMPScanner", snmp_cls), \
         patch.object(engine, "_scan_pipeline", side_effect=engine._scan_pipeline_inner), \
         patch.object(engine, "_arp_harvest_phase", side_effect=arp_phase):
        yield


class _Assistant(SystemToolsMixin):
    pass


class TestListenerWarningsOnEveryEnding:
    def setup_method(self):
        self.engine = DiscoveryEngine()
        self.engine.scan_status.started_at = time.time()
        self.engine.scan_status.scan_id = "cut_short"
        self.engine.scan_status.status = "running"
        self.events: list[dict] = []

        async def on_update(message: dict) -> None:
            self.events.append(message)

        self.engine.on_update = on_update

    def _assert_ended(self, status: str) -> None:
        outcome = self.engine.outcome()
        assert outcome.status == status
        assert outcome.warnings.count(MDNS_DOWN) == 1
        complete = [e for e in self.events if e.get("type") == "discovery.complete"]
        assert len(complete) == 1
        assert complete[0]["status"] == status
        assert complete[0]["warnings"].count(MDNS_DOWN) == 1

    async def _cut_short_by_the_ceiling(self) -> None:
        async def hangs(*args):
            await asyncio.Event().wait()

        with _scan_that_reaches_the_arp_phase(self.engine, hangs):
            await self.engine._run_scan(["192.0.2.0/30"], timeout=0.3)

    async def test_a_scan_cut_short_by_its_time_limit_says_which_listener_failed(self):
        await self._cut_short_by_the_ceiling()

        self._assert_ended("partial")
        assert any("limit" in w for w in self.engine.outcome().warnings)

    async def test_a_scan_ended_by_an_error_says_which_listener_failed(self):
        async def fails(*args):
            raise RuntimeError("arp table unreadable")

        with _scan_that_reaches_the_arp_phase(self.engine, fails):
            await self.engine._run_scan(["192.0.2.0/30"], timeout=10.0)

        self._assert_ended("partial")
        assert any("unexpected error" in w for w in self.engine.outcome().warnings)

    async def test_a_stopped_scan_says_which_listener_failed(self):
        started = asyncio.Event()

        async def hangs(*args):
            started.set()
            await asyncio.Event().wait()

        with _scan_that_reaches_the_arp_phase(self.engine, hangs):
            self.engine._scan_task = asyncio.create_task(
                self.engine._run_scan(["192.0.2.0/30"], timeout=10.0),
            )
            await asyncio.wait_for(started.wait(), 5.0)
            await self.engine.stop_scan()

        self._assert_ended("cancelled")

    async def test_the_assistants_answer_carries_the_scans_warnings(self, monkeypatch):
        await self._cut_short_by_the_ceiling()
        monkeypatch.setattr(discovery_api, "_engine", self.engine)

        answer = await _Assistant()._get_discovery_results({})

        assert answer["scan_status"] == "partial"
        assert answer["warnings"] == list(self.engine.outcome().warnings)
        assert MDNS_DOWN in answer["warnings"]
