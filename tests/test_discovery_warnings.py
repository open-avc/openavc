"""Engine surfaces environment failures as scan-status warnings.

Total environment failure (no ping capability, listeners that never ran)
must be distinguishable from "empty network" — the warnings list rides the
scan status, the /api/discovery/results payload, and the WS events.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from server.discovery import icmp
from server.discovery.engine import DiscoveryEngine, ScanStatus


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
                        min_prefix, source_ip, stats=None):
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
        with patch("server.discovery.engine.ping_sweep", side_effect=fake_ping), \
             patch("server.discovery.engine.MDNSScanner", mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", ssdp_cls), \
             patch("server.discovery.engine.AMXDDPScanner", amx_cls), \
             patch("server.discovery.engine.SNMPScanner", snmp_cls), \
             patch("server.discovery.engine._resolve_hostnames",
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
        self.engine._on_update = AsyncMock(side_effect=lambda m: events.append(m))
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
