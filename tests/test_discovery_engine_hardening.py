"""Discovery-engine hardening regression tests.

Covers the orchestration-layer defects fixed in the bug-fix campaign:

- Background passive-listener + SNMP tasks must be cancelled on EVERY exit
  path (including a non-CancelledError pipeline error), not only on cancel.
- Per-device evidence_log / open_ports / banners must reset at scan start so
  re-scans don't accumulate unbounded evidence + duplicate enrichment records.
- A Python discovery companion must not fabricate device records for IPs
  outside the scanned subnets.
- The passive/SNMP collect cleanup must not swallow a cancellation aimed at
  the pipeline coroutine itself (cooperative cancellation).
- Driver/companion probes must not clobber authoritative passive identity.
- total_hosts_scanned must count only the subnets ping_sweep actually probes,
  and a malformed CIDR must not abort the whole scan.

All scanners are mocked; no real network I/O. Invented IPs/vendors only.
"""

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from server.discovery import engine as engine_mod
from server.discovery.engine import DiscoveryEngine, _ip_in_subnets
from server.discovery.result import (
    DiscoveredDevice,
    Evidence,
    SignalTier,
    merge_device_info,
)


def _hanging_scanner_mocks():
    """Passive + SNMP scanner mocks whose background coroutines hang (sleep
    until cancelled), so the engine's create_task'd listeners stay pending."""

    async def hang():
        await asyncio.sleep(60)

    mdns_cls = MagicMock()
    mdns = MagicMock()
    mdns.start = lambda duration: hang()
    mdns._running = True
    mdns_cls.return_value = mdns

    ssdp_cls = MagicMock()
    ssdp = MagicMock()
    ssdp.scan = lambda timeout, fetch_descriptions: hang()
    ssdp._running = True
    ssdp_cls.return_value = ssdp

    amx_cls = MagicMock()
    amx = MagicMock()
    amx.start = lambda duration: hang()
    amx.stop = AsyncMock()
    amx_cls.return_value = amx

    snmp_cls = MagicMock()
    snmp = MagicMock()
    snmp.scan_devices = lambda *a, **kw: hang()
    snmp_cls.return_value = snmp

    return mdns_cls, ssdp_cls, amx_cls, snmp_cls


def _spy_on_cleanup(engine):
    """Wrap engine._cancel_background_tasks to capture the tasks it's handed
    while still running the real cleanup. Returns a dict updated in place."""
    captured: dict = {}
    real = engine._cancel_background_tasks

    async def spy(*tasks):
        captured["tasks"] = tasks
        return await real(*tasks)

    engine._cancel_background_tasks = spy
    return captured


# --- H-057: background tasks released on a non-CancelledError ---------------


class TestBackgroundTaskCleanup:
    async def test_pipeline_error_after_snmp_cancels_all_background_tasks(self):
        """A non-CancelledError raised mid-pipeline (here in passive collect,
        after SNMP launched) must still cancel/await all four background tasks
        — not just the cancellation path the old except-only handler covered."""
        engine = DiscoveryEngine()
        captured = _spy_on_cleanup(engine)
        mdns_cls, ssdp_cls, amx_cls, snmp_cls = _hanging_scanner_mocks()

        with patch("server.discovery.engine.ping_sweep",
                   new_callable=AsyncMock, return_value=["10.77.0.10"]), \
             patch("server.discovery.engine.harvest_arp_table",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine.netbios_sweep",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine.scan_host_ports",
                   new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.grab_banners",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine._resolve_hostnames",
                   new_callable=AsyncMock, return_value={}), \
             patch.object(engine.community_index, "get_drivers",
                          new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.MDNSScanner", mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", ssdp_cls), \
             patch("server.discovery.engine.AMXDDPScanner", amx_cls), \
             patch("server.discovery.engine.SNMPScanner", snmp_cls), \
             patch.object(engine, "_collect_passive_results",
                          side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                await engine._scan_pipeline_inner(["10.77.0.0/24"])

        tasks = [t for t in captured["tasks"] if t is not None]
        assert len(tasks) == 4  # mdns, ssdp, amx, snmp all created
        assert all(t.done() for t in tasks)  # all cancelled/awaited, no leak

    async def test_pipeline_error_before_snmp_cancels_passive_tasks(self):
        """An error during the ping phase (before SNMP launches) must still
        cancel the three passive listeners (SNMP task is None, not created)."""
        engine = DiscoveryEngine()
        captured = _spy_on_cleanup(engine)
        mdns_cls, ssdp_cls, amx_cls, snmp_cls = _hanging_scanner_mocks()

        async def ping_boom(*a, **kw):
            raise RuntimeError("ping failed")

        with patch("server.discovery.engine.ping_sweep", side_effect=ping_boom), \
             patch("server.discovery.engine.MDNSScanner", mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", ssdp_cls), \
             patch("server.discovery.engine.AMXDDPScanner", amx_cls), \
             patch("server.discovery.engine.SNMPScanner", snmp_cls):
            with pytest.raises(RuntimeError, match="ping failed"):
                await engine._scan_pipeline_inner(["10.77.0.0/24"])

        mdns_t, ssdp_t, amx_t, snmp_t = captured["tasks"]
        assert snmp_t is None  # never launched
        assert all(t is not None and t.done() for t in (mdns_t, ssdp_t, amx_t))

    async def test_cancel_background_tasks_is_noop_when_done(self):
        engine = DiscoveryEngine()
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        # None + already-finished task: must not raise.
        await engine._cancel_background_tasks(None, done)
        assert done.done()


# --- H-058: per-scan observation reset at scan start ------------------------


class TestEvidenceResetOnScanStart:
    async def test_start_scan_clears_evidence_ports_banners(self):
        engine = DiscoveryEngine()
        dev = DiscoveredDevice(ip="10.77.0.5", alive=True)
        dev.evidence_log = [
            Evidence(tier=SignalTier.ENRICHMENT, source="oui:aa"),
            Evidence(tier=SignalTier.ENRICHMENT, source="hostname:x"),
        ]
        dev.open_ports = [23, 80]
        dev.banners = {23: "old banner"}
        engine.results["10.77.0.5"] = dev

        with patch.object(engine, "_run_scan", new_callable=AsyncMock):
            await engine.start_scan(subnets=["10.77.0.0/24"])

        assert dev.alive is False
        assert dev.evidence_log == []
        assert dev.open_ports == []
        assert dev.banners == {}

    async def test_evidence_does_not_grow_across_rescans(self):
        """Two full scans (driven through start_scan, which owns the reset)
        that re-find the same device must not stack evidence."""
        engine = DiscoveryEngine()
        mdns_cls, ssdp_cls, amx_cls, snmp_cls = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        for cls, ms in ((mdns_cls, "start"), (ssdp_cls, "scan"), (amx_cls, "start")):
            inst = MagicMock()
            setattr(inst, ms, AsyncMock(return_value={}))
            inst._running = True
            inst.stop = AsyncMock()
            cls.return_value = inst
        snmp_inst = MagicMock()
        snmp_inst.scan_devices = AsyncMock(return_value={})
        snmp_cls.return_value = snmp_inst

        async def fake_ping(subnets, *, concurrency, on_found, on_progress,
                            min_prefix, source_ip, stats=None):
            # The real ping_sweep marks each live host alive via on_found; the
            # rescan path relies on that to un-stale a persisted device.
            await on_found("10.77.0.7")
            return ["10.77.0.7"]

        async def fake_run_scan(subnets, timeout):
            await engine._scan_pipeline_inner(subnets)

        async def run_one():
            with patch.object(engine, "_run_scan", fake_run_scan), \
                 patch("server.discovery.engine.ping_sweep",
                       side_effect=fake_ping), \
                 patch("server.discovery.engine.harvest_arp_table",
                       new_callable=AsyncMock,
                       return_value={"10.77.0.7": "aa:bb:cc:dd:ee:ff"}), \
                 patch("server.discovery.engine.netbios_sweep",
                       new_callable=AsyncMock, return_value={}), \
                 patch("server.discovery.engine.scan_host_ports",
                       new_callable=AsyncMock, return_value=[]), \
                 patch("server.discovery.engine.grab_banners",
                       new_callable=AsyncMock, return_value={}), \
                 patch("server.discovery.engine._resolve_hostnames",
                       new_callable=AsyncMock, return_value={}), \
                 patch.object(engine.community_index, "get_drivers",
                              new_callable=AsyncMock, return_value=[]), \
                 patch("server.discovery.engine.MDNSScanner", mdns_cls), \
                 patch("server.discovery.engine.SSDPScanner", ssdp_cls), \
                 patch("server.discovery.engine.AMXDDPScanner", amx_cls), \
                 patch("server.discovery.engine.SNMPScanner", snmp_cls):
                await engine.start_scan(subnets=["10.77.0.0/24"])
                await engine._scan_task  # run the inline pipeline to completion

        await run_one()
        first = len(engine.results["10.77.0.7"].evidence_log)
        await run_one()
        second = len(engine.results["10.77.0.7"].evidence_log)
        assert first > 0
        assert second == first  # no cross-scan accumulation


# --- H-059: companion can't fabricate off-subnet records --------------------


class TestCompanionSubnetGuard:
    def test_ip_in_subnets(self):
        nets = ["10.77.0.0/24", "192.168.5.0/25"]
        assert _ip_in_subnets("10.77.0.42", nets)
        assert _ip_in_subnets("192.168.5.10", nets)
        assert not _ip_in_subnets("8.8.8.8", nets)
        assert not _ip_in_subnets("192.168.5.200", nets)  # outside the /25
        assert not _ip_in_subnets("not-an-ip", nets)
        assert not _ip_in_subnets("10.77.0.1", ["garbage/99"])  # bad CIDR skipped

    async def test_companion_offsubnet_host_ignored(self):
        engine = DiscoveryEngine()

        async def probe(ctx):
            # One in-range host, one fabricated off-subnet host.
            await ctx.emit_broadcast("10.77.0.50", txt={"manufacturer": "Acme"})
            await ctx.emit_broadcast("8.8.8.8", txt={"manufacturer": "Evil"})

        engine._discovery_companions = {"acme_probe": probe}

        await engine._run_custom_probes(["10.77.0.0/24"], "")

        assert "10.77.0.50" in engine.results
        assert "8.8.8.8" not in engine.results


# --- M-104: collect cleanup preserves cooperative cancellation --------------


class TestCollectCancellation:
    def setup_method(self):
        # Shrink the otherwise 5–30s passive-collect window for the tests.
        self._tick = engine_mod._PASSIVE_COLLECT_TICK_SECONDS
        self._wait = engine_mod._PASSIVE_COLLECT_DEFAULT_WAIT_SECONDS
        engine_mod._PASSIVE_COLLECT_TICK_SECONDS = 0.01
        engine_mod._PASSIVE_COLLECT_DEFAULT_WAIT_SECONDS = 0.02

    def teardown_method(self):
        engine_mod._PASSIVE_COLLECT_TICK_SECONDS = self._tick
        engine_mod._PASSIVE_COLLECT_DEFAULT_WAIT_SECONDS = self._wait

    async def test_child_cancellation_is_swallowed(self):
        """Listeners still pending past the window are cancelled and the
        collect returns normally — a child's CancelledError isn't propagated."""
        engine = DiscoveryEngine()
        mdns = asyncio.create_task(asyncio.sleep(60))
        ssdp = asyncio.create_task(asyncio.sleep(60))
        amx = asyncio.create_task(asyncio.sleep(60))

        await engine._collect_passive_results(mdns, ssdp, amx)  # must not raise

        assert mdns.cancelled() and ssdp.cancelled() and amx.cancelled()

    async def test_outer_cancellation_propagates_through_cleanup(self):
        """A cancellation aimed at the collect coroutine itself, arriving while
        it awaits the cleanup, must propagate (not be swallowed)."""
        engine = DiscoveryEngine()
        cleanup_started = asyncio.Event()

        async def stubborn():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                # Hold the cleanup's gather() open so the test can land an
                # outer cancellation while it's awaiting.
                cleanup_started.set()
                await asyncio.Event().wait()  # never set; re-cancelled below

        mdns = asyncio.create_task(stubborn())
        ssdp = asyncio.create_task(asyncio.sleep(60))
        amx = asyncio.create_task(asyncio.sleep(60))

        coro = asyncio.create_task(
            engine._collect_passive_results(mdns, ssdp, amx)
        )
        await asyncio.wait_for(cleanup_started.wait(), timeout=2.0)
        coro.cancel()
        with pytest.raises(asyncio.CancelledError):
            await coro


# --- M-105: probes don't clobber authoritative passive identity -------------


class TestMergePriority:
    def test_fill_only_keeps_existing_identity(self):
        dev = DiscoveredDevice(ip="10.77.0.9")
        dev.manufacturer = "QSC"  # set by an authoritative passive source
        # A longer probe-derived value would win under length-only merge.
        merge_device_info(
            dev, {"manufacturer": "QSC Audio Products LLC", "model": "Core110"},
            "probe", fill_only=True,
        )
        assert dev.manufacturer == "QSC"   # not clobbered
        assert dev.model == "Core110"      # empty field still filled

    def test_default_merge_still_prefers_longer(self):
        dev = DiscoveredDevice(ip="10.77.0.9")
        dev.manufacturer = "QSC"
        merge_device_info(dev, {"manufacturer": "QSC Audio"}, "snmp")
        assert dev.manufacturer == "QSC Audio"

    async def test_companion_probe_does_not_overwrite_passive_manufacturer(self):
        engine = DiscoveryEngine()
        dev = engine._get_or_create("10.77.0.60")
        dev.manufacturer = "Acme"  # pretend a passive listener set this

        async def probe(ctx):
            await ctx.emit_active(
                "10.77.0.60",
                {"manufacturer": "Acme Corporation International"},
            )

        engine._discovery_companions = {"acme_probe": probe}
        await engine._run_custom_probes(["10.77.0.0/24"], "")

        assert engine.results["10.77.0.60"].manufacturer == "Acme"

    async def test_late_arp_oui_does_not_overwrite_passive_identity(self):
        # The late ARP harvest runs after passive collection and merges
        # the IEEE OUI registrant name — the NIC vendor, which is often
        # not the device vendor. A longer registrant string must not
        # clobber what the device self-reported via mDNS/SSDP/SNMP.
        engine = DiscoveryEngine()
        dev = engine._get_or_create("10.77.0.61")
        dev.alive = True
        dev.manufacturer = "Barco"     # SSDP self-reported
        dev.category = "projector"
        assert not dev.mac             # eligible for the late harvest

        with patch("server.discovery.engine.harvest_arp_table",
                   new_callable=AsyncMock,
                   return_value={"10.77.0.61": "AA:BB:CC:DD:EE:FF"}), \
             patch.object(engine.oui_db, "lookup",
                          return_value=("ASUSTek COMPUTER INC.", "networking")):
            await engine._late_arp_harvest()

        d = engine.results["10.77.0.61"]
        assert d.mac == "AA:BB:CC:DD:EE:FF"   # enrichment still lands
        assert d.manufacturer == "Barco"       # NIC vendor didn't clobber
        assert d.category == "projector"

    async def test_rescan_refreshes_hostname_but_not_oui_vendor(self):
        # Identity carried over from a previous scan must not pin the
        # phase-4 fields forever: this scan's own reverse-DNS/NetBIOS
        # reads refresh hostname/device_name (a renamed or reassigned
        # host updates on re-scan), while the OUI registrant still only
        # fills what nothing else populated.
        engine = DiscoveryEngine()
        dev = engine._get_or_create("10.77.0.61")
        dev.hostname = "old-projector"        # carried from a prior scan
        dev.manufacturer = "Barco"            # SSDP self-reported
        dev.category = "projector"

        mdns_cls, ssdp_cls, amx_cls, snmp_cls = _hanging_scanner_mocks()
        mdns_cls.return_value.start = AsyncMock(return_value={})
        ssdp_cls.return_value.scan = AsyncMock(return_value={})
        amx_cls.return_value.start = AsyncMock(return_value={})
        snmp_cls.return_value.scan_devices = AsyncMock(return_value={})

        with patch("server.discovery.engine.ping_sweep",
                   new_callable=AsyncMock, return_value=["10.77.0.61"]), \
             patch("server.discovery.engine.harvest_arp_table",
                   new_callable=AsyncMock,
                   return_value={"10.77.0.61": "AA:BB:CC:DD:EE:FF"}), \
             patch("server.discovery.engine.netbios_sweep",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine.scan_host_ports",
                   new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.grab_banners",
                   new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine._resolve_hostnames",
                   new_callable=AsyncMock,
                   return_value={"10.77.0.61": "projector-b.example.edu"}), \
             patch.object(engine.community_index, "get_drivers",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(engine.oui_db, "lookup",
                          return_value=("ASUSTek COMPUTER INC.", "networking")), \
             patch("server.discovery.engine.MDNSScanner", mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", ssdp_cls), \
             patch("server.discovery.engine.AMXDDPScanner", amx_cls), \
             patch("server.discovery.engine.SNMPScanner", snmp_cls):
            await engine._scan_pipeline_inner(["10.77.0.0/24"])

        d = engine.results["10.77.0.61"]
        assert d.hostname == "projector-b.example.edu"  # refreshed this scan
        assert d.mac == "AA:BB:CC:DD:EE:FF"
        assert d.manufacturer == "Barco"                # OUI still fill-only
        assert d.category == "projector"


# --- M-252: TCP active probes honor the shared send-rate limiter ------------


class TestTcpProbeRateLimit:
    """The engine documents a global 10/sec send cap for custom probes, but
    only the UDP path used to receive the shared RateLimiter — the TCP path
    fired connects via gather() with no limiter. The engine must pass the same
    limiter to the TCP active probe."""

    async def test_engine_hands_rate_limiter_to_tcp_probe(self):
        from server.discovery.hints import parse_driver_discovery
        from server.discovery.probe_runner import RateLimiter

        engine = DiscoveryEngine()
        hint = parse_driver_discovery({
            "id": "acme_ctl", "name": "acme_ctl", "manufacturer": "Acme",
            "category": "audio", "transport": "tcp",
            "discovery": {"tcp_probe": {
                "port": 4321, "send_ascii": "id\r\n",
                "expect": "Acme", "timeout_ms": 500,
            }},
        })
        engine.discovery_hints = [hint]
        dev = engine._get_or_create("10.77.0.5")
        dev.alive = True
        dev.open_ports = [4321]  # matches the tcp_probe port

        captured = {}

        async def fake_probe(spec, *, target, source_ip,
                             stagger_ms=0.0, rate_limiter=None):
            captured["rate_limiter"] = rate_limiter
            return None

        with patch.object(engine_mod, "run_tcp_active_probe", fake_probe):
            await engine._run_custom_probes(["10.77.0.0/24"], "10.77.0.1")

        # Before the fix this was None (no limiter threaded through the TCP
        # path); the shared cap only applied to UDP.
        assert isinstance(captured.get("rate_limiter"), RateLimiter)


# --- L-069 / L-070: total_hosts_scanned accuracy + malformed-CIDR safety ----


class TestPingTotalAccounting:
    async def test_oversized_and_malformed_subnets_excluded(self):
        engine = DiscoveryEngine()
        engine.config["max_subnet_size"] = 24  # /16 is too large, skipped

        mdns_cls, ssdp_cls, amx_cls, snmp_cls = _hanging_scanner_mocks()
        # Override the hang() coroutines with instant empties for this test.
        mdns_cls.return_value.start = AsyncMock(return_value={})
        ssdp_cls.return_value.scan = AsyncMock(return_value={})
        amx_cls.return_value.start = AsyncMock(return_value={})
        snmp_cls.return_value.scan_devices = AsyncMock(return_value={})

        subnets = ["10.77.0.0/24", "10.0.0.0/16", "garbage/99"]

        with patch("server.discovery.engine.ping_sweep",
                   new_callable=AsyncMock, return_value=[]) as mock_ping, \
             patch.object(engine.community_index, "get_drivers",
                          new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.MDNSScanner", mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", ssdp_cls), \
             patch("server.discovery.engine.AMXDDPScanner", amx_cls), \
             patch("server.discovery.engine.SNMPScanner", snmp_cls), \
             patch("server.discovery.engine._resolve_hostnames",
                   new_callable=AsyncMock, return_value={}):
            # Must NOT raise on the malformed CIDR.
            await engine._scan_pipeline_inner(subnets)
            # ping_sweep still receives the raw subnet list (it filters too).
            assert mock_ping.await_count == 1

        # Only the /24 counts: 256 - 2 network/broadcast = 254.
        assert engine.scan_status.total_hosts_scanned == 254


class TestPassiveResultCap:
    """Aggregate passive-source cap on engine.results.

    Each passive listener caps its own distinct sources per scan window;
    this engine-level bound covers their sum (defense-in-depth for the
    DiscoveredDevice + WebSocket fan-out each new entry costs). Active
    results — a ping sweep on a /16 is legitimately thousands of hosts —
    are never counted against it.
    """

    def test_new_passive_source_counted_and_created(self):
        engine = DiscoveryEngine()
        device = engine._get_or_create_passive("10.0.0.1")
        assert device is not None
        assert "10.0.0.1" in engine.results
        assert engine._passive_sources_created == 1

    def test_capped_returns_none_and_warns_once(self):
        engine = DiscoveryEngine()
        engine._passive_sources_created = engine_mod.MAX_PASSIVE_RESULT_SOURCES
        assert engine._get_or_create_passive("10.0.0.1") is None
        assert engine._get_or_create_passive("10.0.0.2") is None
        assert "10.0.0.1" not in engine.results
        hits = [w for w in engine.scan_status.warnings if "result cap" in w]
        assert len(hits) == 1

    def test_existing_entry_updates_past_cap(self):
        engine = DiscoveryEngine()
        device = engine._get_or_create("10.0.0.1")
        engine._passive_sources_created = engine_mod.MAX_PASSIVE_RESULT_SOURCES
        assert engine._get_or_create_passive("10.0.0.1") is device

    def test_active_creation_not_counted(self):
        engine = DiscoveryEngine()
        engine._get_or_create("10.0.0.1")
        engine._get_or_create("10.0.0.2")
        assert engine._passive_sources_created == 0
