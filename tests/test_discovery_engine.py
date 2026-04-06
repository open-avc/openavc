"""Tests for discovery engine orchestration.

Mocks all individual scanners (network, port, protocol, passive, SNMP)
to test the orchestration logic in isolation.
"""

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from server.discovery.engine import DiscoveryEngine, ScanStatus
from server.discovery.result import DiscoveredDevice


# --- ScanStatus tests ---


class TestScanStatus:
    def test_default_state(self):
        s = ScanStatus()
        assert s.status == "idle"
        assert s.phase == ""
        assert s.phase_number == 0
        assert s.total_phases == 8
        assert s.progress == 0.0
        assert s.devices_found == 0
        assert s.subnets == []

    def test_to_dict_has_all_fields(self):
        s = ScanStatus()
        s.scan_id = "scan_42"
        s.status = "running"
        s.phase = "ping_sweep"
        s.phase_number = 3
        d = s.to_dict()
        assert d["scan_id"] == "scan_42"
        assert d["status"] == "running"
        assert d["phase"] == "ping_sweep"
        assert d["phase_number"] == 3
        assert d["total_phases"] == 8
        assert isinstance(d["progress"], float)
        assert isinstance(d["duration"], float)

    def test_progress_rounds_to_two_decimals(self):
        s = ScanStatus()
        s.progress = 0.33333333
        d = s.to_dict()
        assert d["progress"] == 0.33

    def test_duration_rounds_to_two_decimals(self):
        s = ScanStatus()
        s.duration = 12.6789
        d = s.to_dict()
        assert d["duration"] == 12.68


# --- DiscoveryEngine basic tests ---


class TestDiscoveryEngineBasic:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    def test_initial_state(self):
        assert self.engine.get_results() == []
        assert self.engine.scan_status.status == "idle"
        assert self.engine.driver_matcher is None

    def test_get_or_create_new(self):
        device = self.engine._get_or_create("192.168.1.1")
        assert device.ip == "192.168.1.1"
        assert isinstance(device, DiscoveredDevice)

    def test_get_or_create_returns_same_object(self):
        d1 = self.engine._get_or_create("10.0.0.1")
        d2 = self.engine._get_or_create("10.0.0.1")
        assert d1 is d2

    def test_get_or_create_different_ips(self):
        d1 = self.engine._get_or_create("10.0.0.1")
        d2 = self.engine._get_or_create("10.0.0.2")
        assert d1 is not d2

    def test_clear_results(self):
        self.engine.results["192.168.1.1"] = DiscoveredDevice(ip="192.168.1.1")
        self.engine.results["192.168.1.2"] = DiscoveredDevice(ip="192.168.1.2")
        assert len(self.engine.results) == 2
        self.engine.clear_results()
        assert len(self.engine.results) == 0
        assert self.engine.scan_status.status == "idle"

    def test_results_sorted_by_confidence_descending(self):
        self.engine.results["a"] = DiscoveredDevice(ip="10.0.0.1", confidence=0.2)
        self.engine.results["b"] = DiscoveredDevice(ip="10.0.0.2", confidence=0.9)
        self.engine.results["c"] = DiscoveredDevice(ip="10.0.0.3", confidence=0.5)
        results = self.engine.get_results()
        confidences = [r["confidence"] for r in results]
        assert confidences == [0.9, 0.5, 0.2]

    def test_get_status_reflects_state(self):
        self.engine.scan_status.status = "running"
        self.engine.scan_status.phase = "port_scan"
        status = self.engine.get_status()
        assert status["status"] == "running"
        assert status["phase"] == "port_scan"

    def test_config_defaults(self):
        assert self.engine.config["snmp_enabled"] is True
        assert self.engine.config["snmp_community"] == "public"
        assert self.engine.config["gentle_mode"] is False


# --- start_scan tests ---


class TestStartScan:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_returns_scan_id(self):
        with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
            scan_id = await self.engine.start_scan(subnets=["192.168.1.0/24"])
            assert scan_id.startswith("scan_")
            assert self.engine.scan_status.scan_id == scan_id

    async def test_sets_status_to_running(self):
        with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
            await self.engine.start_scan(subnets=["192.168.1.0/24"])
            assert self.engine.scan_status.status == "running"

    async def test_records_subnets(self):
        with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
            await self.engine.start_scan(subnets=["10.0.0.0/24", "192.168.1.0/24"])
            assert self.engine.scan_status.subnets == ["10.0.0.0/24", "192.168.1.0/24"]

    async def test_no_subnets_raises(self):
        with patch("server.discovery.engine.get_local_subnets", return_value=[]):
            with pytest.raises(ValueError, match="No subnets"):
                await self.engine.start_scan(subnets=[])

    async def test_auto_detect_subnets(self):
        with patch("server.discovery.engine.get_local_subnets", return_value=["192.168.1.0/24"]):
            with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
                await self.engine.start_scan()
                assert self.engine.scan_status.subnets == ["192.168.1.0/24"]

    async def test_extra_subnets_appended(self):
        with patch("server.discovery.engine.get_local_subnets", return_value=["192.168.1.0/24"]):
            with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
                await self.engine.start_scan(extra_subnets=["10.0.0.0/24"])
                assert "10.0.0.0/24" in self.engine.scan_status.subnets
                assert "192.168.1.0/24" in self.engine.scan_status.subnets

    async def test_extra_subnets_no_duplicates(self):
        with patch("server.discovery.engine.get_local_subnets", return_value=["192.168.1.0/24"]):
            with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
                await self.engine.start_scan(extra_subnets=["192.168.1.0/24"])
                assert self.engine.scan_status.subnets.count("192.168.1.0/24") == 1

    async def test_double_start_raises(self):
        never_done = asyncio.Future()
        with patch.object(self.engine, "_run_scan", return_value=never_done):
            await self.engine.start_scan(subnets=["192.168.1.0/24"])
            with pytest.raises(RuntimeError, match="already running"):
                await self.engine.start_scan(subnets=["192.168.1.0/24"])
            never_done.set_result(None)

    async def test_scan_counter_increments(self):
        with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
            id1 = await self.engine.start_scan(subnets=["10.0.0.0/30"])
            # Wait for the mock scan to finish
            await asyncio.sleep(0.05)
            id2 = await self.engine.start_scan(subnets=["10.0.0.0/30"])
            assert id1 != id2

    async def test_marks_existing_devices_as_stale(self):
        self.engine.results["10.0.0.1"] = DiscoveredDevice(ip="10.0.0.1", alive=True)
        with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
            await self.engine.start_scan(subnets=["10.0.0.0/24"])
            assert self.engine.results["10.0.0.1"].alive is False


# --- stop_scan tests ---


class TestStopScan:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_stop_sets_cancelled(self):
        async def slow_scan(subnets, timeout):
            await asyncio.sleep(10)

        with patch.object(self.engine, "_run_scan", side_effect=slow_scan):
            self.engine.scan_status.started_at = 100.0
            await self.engine.start_scan(subnets=["10.0.0.0/24"])
            await asyncio.sleep(0.05)
            await self.engine.stop_scan()
            assert self.engine.scan_status.status == "cancelled"

    async def test_stop_when_no_scan_is_noop(self):
        # Should not raise
        await self.engine.stop_scan()


# --- _set_phase tests ---


class TestSetPhase:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_updates_status_fields(self):
        await self.engine._set_phase(3, "ping_sweep", "Scanning hosts...")
        assert self.engine.scan_status.phase_number == 3
        assert self.engine.scan_status.phase == "ping_sweep"
        assert self.engine.scan_status.message == "Scanning hosts..."

    async def test_calculates_progress(self):
        await self.engine._set_phase(4, "arp_harvest", "ARP harvest...")
        # Weighted progress: sum of preceding phases (standard depth)
        # subnet_detection(0.02) + passive_listen(0.02) + ping_sweep(0.25) = 0.29
        expected = 0.02 + 0.02 + 0.25
        assert self.engine.scan_status.progress == pytest.approx(expected)

    async def test_emits_phase_event(self):
        events = []
        self.engine._on_update = AsyncMock(side_effect=lambda msg: events.append(msg))
        await self.engine._set_phase(2, "passive", "Starting listeners...")
        assert len(events) == 1
        assert events[0]["type"] == "discovery_phase"
        assert events[0]["phase"] == "passive"
        assert events[0]["phase_number"] == 2


# --- _emit tests ---


class TestEmit:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_emit_calls_callback(self):
        callback = AsyncMock()
        self.engine._on_update = callback
        await self.engine._emit({"type": "test"})
        callback.assert_called_once_with({"type": "test"})

    async def test_emit_no_callback_is_noop(self):
        # Should not raise
        await self.engine._emit({"type": "test"})

    async def test_emit_swallows_callback_errors(self):
        callback = AsyncMock(side_effect=Exception("boom"))
        self.engine._on_update = callback
        # Should not raise
        await self.engine._emit({"type": "test"})


# --- _emit_device_update tests ---


class TestEmitDeviceUpdate:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_emits_discovery_update(self):
        events = []
        self.engine._on_update = AsyncMock(side_effect=lambda msg: events.append(msg))
        device = DiscoveredDevice(ip="10.0.0.1")
        await self.engine._emit_device_update(device, "ping_sweep")
        assert len(events) == 1
        assert events[0]["type"] == "discovery_update"
        assert events[0]["device"]["ip"] == "10.0.0.1"
        assert events[0]["phase"] == "ping_sweep"


# --- _run_scan lifecycle tests ---


class TestRunScan:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_completes_with_status(self):
        """_run_scan sets status to complete on success."""
        import time
        self.engine.scan_status.started_at = time.time()
        self.engine.scan_status.scan_id = "test_scan"

        with patch.object(self.engine, "_scan_pipeline", new_callable=AsyncMock):
            await self.engine._run_scan(["192.168.1.0/24"], timeout=10.0)

        assert self.engine.scan_status.status == "complete"
        assert self.engine.scan_status.duration >= 0

    async def test_timeout_still_completes(self):
        """If pipeline exceeds timeout, scan still completes."""
        import time
        self.engine.scan_status.started_at = time.time()
        self.engine.scan_status.scan_id = "test_scan"

        async def slow_pipeline(subnets):
            await asyncio.sleep(10)

        with patch.object(self.engine, "_scan_pipeline", side_effect=slow_pipeline):
            await self.engine._run_scan(["192.168.1.0/24"], timeout=0.1)

        assert self.engine.scan_status.status == "complete"

    async def test_exception_in_pipeline_still_completes(self):
        """If pipeline raises, scan still marks complete."""
        import time
        self.engine.scan_status.started_at = time.time()
        self.engine.scan_status.scan_id = "test_scan"

        with patch.object(
            self.engine, "_scan_pipeline",
            new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ):
            await self.engine._run_scan(["192.168.1.0/24"], timeout=10.0)

        assert self.engine.scan_status.status == "complete"

    async def test_emits_discovery_complete(self):
        """_run_scan emits discovery_complete event."""
        import time
        self.engine.scan_status.started_at = time.time()
        self.engine.scan_status.scan_id = "test_scan"

        events = []
        self.engine._on_update = AsyncMock(side_effect=lambda msg: events.append(msg))

        with patch.object(self.engine, "_scan_pipeline", new_callable=AsyncMock):
            await self.engine._run_scan(["192.168.1.0/24"], timeout=10.0)

        complete_events = [e for e in events if e.get("type") == "discovery_complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["scan_id"] == "test_scan"


# --- Full pipeline test with mocked scanners ---


class TestScanPipeline:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    def _mock_passive_scanners(self):
        """Helper to create mocked passive scanners."""
        mock_mdns_cls = MagicMock()
        mock_mdns = MagicMock()
        mock_mdns.start = AsyncMock(return_value={})
        mock_mdns._running = True
        mock_mdns_cls.return_value = mock_mdns

        mock_ssdp_cls = MagicMock()
        mock_ssdp = MagicMock()
        mock_ssdp.scan = AsyncMock(return_value={})
        mock_ssdp._running = True
        mock_ssdp_cls.return_value = mock_ssdp

        mock_snmp_cls = MagicMock()
        mock_snmp = MagicMock()
        mock_snmp.scan_devices = AsyncMock(return_value={})
        mock_snmp_cls.return_value = mock_snmp

        return mock_mdns_cls, mock_ssdp_cls, mock_snmp_cls

    async def test_pipeline_with_no_live_hosts(self):
        """Pipeline completes when no hosts respond to ping."""
        mock_mdns_cls, mock_ssdp_cls, mock_snmp_cls = self._mock_passive_scanners()

        with patch("server.discovery.engine.ping_sweep", new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.MDNSScanner", mock_mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", mock_ssdp_cls), \
             patch("server.discovery.engine.SNMPScanner", mock_snmp_cls), \
             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):
            await self.engine._scan_pipeline(["192.168.1.0/30"])

        # Should complete without error, even with no devices
        assert self.engine.scan_status.devices_found == 0

    async def test_pipeline_finds_devices(self):
        """Pipeline discovers devices through ping + port + ARP."""
        mock_mdns_cls, mock_ssdp_cls, mock_snmp_cls = self._mock_passive_scanners()

        with patch("server.discovery.engine.ping_sweep", new_callable=AsyncMock) as mock_ping, \
             patch("server.discovery.engine.harvest_arp_table", new_callable=AsyncMock) as mock_arp, \
             patch("server.discovery.engine.scan_host_ports", new_callable=AsyncMock) as mock_ports, \
             patch("server.discovery.engine.grab_banners", new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine.run_protocol_probes", new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.MDNSScanner", mock_mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", mock_ssdp_cls), \
             patch("server.discovery.engine.SNMPScanner", mock_snmp_cls), \
             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):

            mock_ping.return_value = ["192.168.1.10", "192.168.1.20"]
            mock_arp.return_value = {"192.168.1.10": "00:05:A6:11:22:33"}
            mock_ports.side_effect = lambda ip, *a, **kw: {
                "192.168.1.10": [23],
                "192.168.1.20": [4352],
            }.get(ip, [])

            await self.engine._scan_pipeline(["192.168.1.0/24"])

        assert len(self.engine.results) == 2
        d1 = self.engine.results["192.168.1.10"]
        assert d1.mac == "00:05:A6:11:22:33"
        assert 23 in d1.open_ports
        assert d1.alive is True

        d2 = self.engine.results["192.168.1.20"]
        assert 4352 in d2.open_ports

    async def test_stale_devices_removed_after_scan(self):
        """Devices not re-discovered in a new scan are removed."""
        # Pre-populate a device that won't be found
        self.engine.results["192.168.1.99"] = DiscoveredDevice(
            ip="192.168.1.99", alive=False
        )

        mock_mdns_cls, mock_ssdp_cls, mock_snmp_cls = self._mock_passive_scanners()

        with patch("server.discovery.engine.ping_sweep", new_callable=AsyncMock, return_value=[]), \
             patch("server.discovery.engine.MDNSScanner", mock_mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", mock_ssdp_cls), \
             patch("server.discovery.engine.SNMPScanner", mock_snmp_cls), \
             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):
            await self.engine._scan_pipeline(["192.168.1.0/24"])

        # Stale device should have been removed
        assert "192.168.1.99" not in self.engine.results

    async def test_pipeline_cancellation_cleans_up(self):
        """Cancelling during pipeline cleans up background tasks."""
        mock_mdns_cls, mock_ssdp_cls, mock_snmp_cls = self._mock_passive_scanners()

        async def cancel_during_ping(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch("server.discovery.engine.ping_sweep", side_effect=cancel_during_ping), \
             patch("server.discovery.engine.MDNSScanner", mock_mdns_cls), \
             patch("server.discovery.engine.SSDPScanner", mock_ssdp_cls), \
             patch("server.discovery.engine.SNMPScanner", mock_snmp_cls):
            with pytest.raises(asyncio.CancelledError):
                await self.engine._scan_pipeline(["192.168.1.0/24"])


# --- Driver matching tests ---


class TestDriverMatching:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    def test_load_driver_hints_creates_matcher(self):
        """Loading driver hints from registry creates a DriverMatcher."""
        registry = [
            {
                "id": "pjlink_class1",
                "name": "PJLink Class 1",
                "category": "projector",
                "manufacturer": "Generic",
                "transport": "tcp",
            }
        ]
        self.engine.load_driver_hints_from_registry(registry)
        assert self.engine.driver_matcher is not None

    async def test_refresh_device_matches_nonexistent(self):
        """Refreshing matches for a device not in results returns None."""
        result = await self.engine.refresh_device_matches("10.0.0.99")
        assert result is None

    async def test_refresh_device_matches_existing(self):
        """Refreshing matches for an existing device works."""
        self.engine.results["10.0.0.1"] = DiscoveredDevice(
            ip="10.0.0.1",
            open_ports=[4352],
            sources=["alive", "av_port_open"],
        )

        # Mock community index to return empty
        with patch.object(
            self.engine.community_index, "get_drivers",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await self.engine.refresh_device_matches("10.0.0.1")

        assert result is not None
        assert result["ip"] == "10.0.0.1"


# --- collect passive results tests ---


class TestCollectPassiveResults:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_handles_cancelled_tasks(self):
        """Cancelled mDNS/SSDP tasks don't crash the collector."""
        mdns_task = asyncio.create_task(asyncio.sleep(100))
        ssdp_task = asyncio.create_task(asyncio.sleep(100))
        mdns_task.cancel()
        ssdp_task.cancel()
        try:
            await mdns_task
        except asyncio.CancelledError:
            pass
        try:
            await ssdp_task
        except asyncio.CancelledError:
            pass

        # Should not raise
        await self.engine._collect_passive_results(mdns_task, ssdp_task)

    async def test_handles_failed_tasks(self):
        """Failed mDNS/SSDP tasks don't crash the collector."""
        async def failing():
            raise RuntimeError("network error")

        mdns_task = asyncio.create_task(failing())
        ssdp_task = asyncio.create_task(failing())
        await asyncio.sleep(0.05)  # let tasks fail

        # Should not raise
        await self.engine._collect_passive_results(mdns_task, ssdp_task)


class TestCollectSnmpResults:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    async def test_none_task_is_noop(self):
        """If SNMP was disabled, snmp_task is None."""
        await self.engine._collect_snmp_results(None)

    async def test_handles_cancelled_snmp(self):
        """Cancelled SNMP task doesn't crash."""
        task = asyncio.create_task(asyncio.sleep(100))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await self.engine._collect_snmp_results(task)
