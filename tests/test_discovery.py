"""Tests for the device discovery module."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from server.discovery.oui_database import OUIDatabase
from server.discovery.oui_data import AV_OUI_TABLE
from server.discovery.result import (
    DiscoveredDevice,
    DriverMatch,
    compute_confidence,
    merge_device_info,
)
from server.discovery.network_scanner import get_local_subnets, _parse_cidr
from server.discovery.port_scanner import AV_PORTS, BANNER_PORTS
from server.discovery.engine import DiscoveryEngine, ScanStatus


# ===== OUI Database Tests =====


class TestOUIDatabase:
    def setup_method(self):
        self.db = OUIDatabase()

    def test_lookup_known_extron_mac(self):
        result = self.db.lookup("00:05:A6:12:34:56")
        assert result is not None
        assert result[0] == "Extron"
        assert result[1] == "switcher"

    def test_lookup_known_crestron_mac(self):
        result = self.db.lookup("00:10:7F:AA:BB:CC")
        assert result is not None
        assert result[0] == "Crestron"
        assert result[1] == "control"

    def test_lookup_samsung_mac(self):
        result = self.db.lookup("8c:71:f8:11:22:33")
        assert result is not None
        assert result[0] == "Samsung"
        assert result[1] == "display"

    def test_lookup_unknown_mac_returns_none(self):
        result = self.db.lookup("AA:BB:CC:DD:EE:FF")
        assert result is None

    def test_lookup_normalizes_dash_format(self):
        result = self.db.lookup("00-05-A6-12-34-56")
        assert result is not None
        assert result[0] == "Extron"

    def test_lookup_normalizes_no_separator(self):
        result = self.db.lookup("0005A6123456")
        assert result is not None
        assert result[0] == "Extron"

    def test_lookup_case_insensitive(self):
        result = self.db.lookup("00:05:a6:12:34:56")
        assert result is not None
        assert result[0] == "Extron"

    def test_is_av_manufacturer_true(self):
        assert self.db.is_av_manufacturer("00:05:A6:12:34:56") is True  # Extron

    def test_is_av_manufacturer_false_network(self):
        assert self.db.is_av_manufacturer("24:A4:3C:12:34:56") is False  # Ubiquiti

    def test_is_av_manufacturer_false_unknown(self):
        assert self.db.is_av_manufacturer("AA:BB:CC:DD:EE:FF") is False

    def test_is_network_device(self):
        assert self.db.is_network_device("24:A4:3C:12:34:56") is True  # Ubiquiti
        assert self.db.is_network_device("00:05:A6:12:34:56") is False  # Extron

    def test_invalid_mac_returns_none(self):
        assert self.db.lookup("invalid") is None
        assert self.db.lookup("") is None
        assert self.db.lookup("00:11") is None

    def test_oui_table_has_entries(self):
        assert len(AV_OUI_TABLE) > 50  # We should have a decent number


# ===== Result Model Tests =====


class TestDiscoveredDevice:
    def test_create_minimal(self):
        d = DiscoveredDevice(ip="192.168.1.1")
        assert d.ip == "192.168.1.1"
        assert d.mac is None
        assert d.confidence == 0.0
        assert d.alive is True

    def test_to_dict(self):
        d = DiscoveredDevice(
            ip="192.168.1.50",
            mac="00:05:a6:12:34:56",
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
            sources=["alive", "oui_av_mfg"],
            confidence=0.2,
        )
        result = d.to_dict()
        assert result["ip"] == "192.168.1.50"
        assert result["mac"] == "00:05:a6:12:34:56"
        assert result["manufacturer"] == "Extron"
        assert result["category"] == "switcher"
        assert 23 in result["open_ports"]
        assert result["confidence"] == 0.2

    def test_driver_match_in_dict(self):
        d = DiscoveredDevice(ip="192.168.1.1")
        d.matched_drivers.append(
            DriverMatch(
                driver_id="extron_sis",
                driver_name="Extron SIS",
                confidence=0.8,
                match_reasons=["Port match"],
                suggested_config={"host": "192.168.1.1", "port": 23},
            )
        )
        result = d.to_dict()
        assert len(result["matched_drivers"]) == 1
        assert result["matched_drivers"][0]["driver_id"] == "extron_sis"


class TestConfidenceScoring:
    def test_empty_sources(self):
        assert compute_confidence([]) == 0.0

    def test_alive_only(self):
        assert compute_confidence(["alive"]) == 0.05

    def test_full_identification(self):
        sources = ["alive", "mac_known", "oui_av_mfg", "av_port_open",
                    "banner_matched", "probe_confirmed"]
        score = compute_confidence(sources)
        assert score == pytest.approx(0.70)

    def test_capped_at_one(self):
        all_sources = list(
            __import__("server.discovery.result", fromlist=["CONFIDENCE_WEIGHTS"]).CONFIDENCE_WEIGHTS.keys()
        )
        score = compute_confidence(all_sources)
        assert score == 1.0

    def test_unknown_source_ignored(self):
        assert compute_confidence(["alive", "unknown_source"]) == 0.05


class TestMergeDeviceInfo:
    def test_basic_merge(self):
        device = DiscoveredDevice(ip="192.168.1.1")
        merge_device_info(device, {"manufacturer": "Extron"}, "oui")
        assert device.manufacturer == "Extron"
        assert "oui" in device.sources

    def test_does_not_overwrite_with_none(self):
        device = DiscoveredDevice(ip="192.168.1.1", manufacturer="Extron")
        merge_device_info(device, {"manufacturer": None}, "probe")
        assert device.manufacturer == "Extron"

    def test_more_specific_wins(self):
        device = DiscoveredDevice(ip="192.168.1.1", manufacturer="NEC")
        merge_device_info(device, {"manufacturer": "NEC Display Solutions"}, "snmp")
        assert device.manufacturer == "NEC Display Solutions"

    def test_shorter_does_not_overwrite(self):
        device = DiscoveredDevice(ip="192.168.1.1", model="PA1004UL Projector")
        merge_device_info(device, {"model": "PA1004UL"}, "probe")
        assert device.model == "PA1004UL Projector"

    def test_merge_open_ports(self):
        device = DiscoveredDevice(ip="192.168.1.1", open_ports=[23])
        merge_device_info(device, {"open_ports": [23, 80]}, "port_scan")
        assert sorted(device.open_ports) == [23, 80]

    def test_merge_banners(self):
        device = DiscoveredDevice(ip="192.168.1.1")
        merge_device_info(device, {"banners": {23: "Extron Banner"}}, "banner")
        assert device.banners[23] == "Extron Banner"

    def test_source_not_duplicated(self):
        device = DiscoveredDevice(ip="192.168.1.1")
        merge_device_info(device, {"manufacturer": "Extron"}, "oui")
        merge_device_info(device, {"model": "DTP"}, "oui")
        assert device.sources.count("oui") == 1


# ===== Network Scanner Tests =====


class TestParseCIDR:
    def test_parse_24(self):
        ips = _parse_cidr("192.168.1.0/24")
        assert len(ips) == 254  # .1 to .254
        assert "192.168.1.1" in ips
        assert "192.168.1.254" in ips
        assert "192.168.1.0" not in ips
        assert "192.168.1.255" not in ips

    def test_parse_30(self):
        ips = _parse_cidr("192.168.1.0/30")
        assert len(ips) == 2  # .1 and .2

    def test_reject_too_large(self):
        ips = _parse_cidr("10.0.0.0/16")  # Too large (< /20)
        assert len(ips) == 0

    def test_invalid_cidr(self):
        ips = _parse_cidr("not-a-cidr")
        assert len(ips) == 0


# ===== Port Scanner Tests =====


class TestPortConstants:
    def test_pjlink_port_in_table(self):
        assert 4352 in AV_PORTS

    def test_samsung_mdc_port_in_table(self):
        assert 1515 in AV_PORTS

    def test_telnet_port_in_table(self):
        assert 23 in AV_PORTS

    def test_banner_ports_are_subset(self):
        for p in BANNER_PORTS:
            assert p in AV_PORTS


# ===== Discovery Engine Tests =====


class TestScanStatus:
    def test_initial_state(self):
        s = ScanStatus()
        assert s.status == "idle"
        assert s.progress == 0.0

    def test_to_dict(self):
        s = ScanStatus()
        s.scan_id = "test_1"
        d = s.to_dict()
        assert d["scan_id"] == "test_1"
        assert d["status"] == "idle"


class TestDiscoveryEngine:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    def test_initial_state(self):
        assert self.engine.get_results() == []
        assert self.engine.scan_status.status == "idle"

    def test_clear_results(self):
        self.engine.results["192.168.1.1"] = DiscoveredDevice(ip="192.168.1.1")
        assert len(self.engine.get_results()) == 1
        self.engine.clear_results()
        assert len(self.engine.get_results()) == 0

    def test_get_or_create(self):
        d = self.engine._get_or_create("192.168.1.1")
        assert d.ip == "192.168.1.1"
        # Second call returns same object
        d2 = self.engine._get_or_create("192.168.1.1")
        assert d is d2

    def test_results_sorted_by_confidence(self):
        self.engine.results["192.168.1.1"] = DiscoveredDevice(
            ip="192.168.1.1", confidence=0.3
        )
        self.engine.results["192.168.1.2"] = DiscoveredDevice(
            ip="192.168.1.2", confidence=0.8
        )
        self.engine.results["192.168.1.3"] = DiscoveredDevice(
            ip="192.168.1.3", confidence=0.5
        )
        results = self.engine.get_results()
        confidences = [r["confidence"] for r in results]
        assert confidences == [0.8, 0.5, 0.3]

    @pytest.mark.asyncio
    async def test_start_scan_no_subnets_raises(self):
        with patch("server.discovery.engine.get_local_subnets", return_value=[]):
            with pytest.raises(ValueError, match="No subnets"):
                await self.engine.start_scan(subnets=[])

    @pytest.mark.asyncio
    async def test_start_scan_returns_scan_id(self):
        with patch("server.discovery.engine.get_local_subnets", return_value=["192.168.1.0/24"]):
            with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
                scan_id = await self.engine.start_scan(subnets=["192.168.1.0/30"])
                assert scan_id.startswith("scan_")

    @pytest.mark.asyncio
    async def test_double_start_raises(self):
        """Cannot start a scan while one is running."""
        with patch("server.discovery.engine.get_local_subnets", return_value=["192.168.1.0/24"]):
            # Create a mock that doesn't complete immediately
            never_done = asyncio.Future()
            with patch.object(self.engine, "_run_scan", return_value=never_done):
                await self.engine.start_scan(subnets=["192.168.1.0/30"])
                with pytest.raises(RuntimeError, match="already running"):
                    await self.engine.start_scan(subnets=["192.168.1.0/30"])
                # Clean up
                never_done.set_result(None)

    @pytest.mark.asyncio
    async def test_stop_scan(self):
        with patch("server.discovery.engine.get_local_subnets", return_value=["192.168.1.0/24"]):
            with patch.object(self.engine, "_run_scan", new_callable=AsyncMock):
                await self.engine.start_scan(subnets=["192.168.1.0/30"])
                await self.engine.stop_scan()
                # Status should be cancelled or complete
                assert self.engine.scan_status.status in ("cancelled", "complete")

    def test_config_defaults(self):
        assert self.engine.config["snmp_enabled"] is True
        assert self.engine.config["snmp_community"] == "public"
        assert self.engine.config["gentle_mode"] is False

    @pytest.mark.asyncio
    async def test_scan_pipeline_with_mocked_network(self):
        """Full pipeline test with mocked network calls."""
        with patch("server.discovery.engine.ping_sweep", new_callable=AsyncMock) as mock_ping:
            mock_ping.return_value = ["192.168.1.50", "192.168.1.72"]

            with patch("server.discovery.engine.harvest_arp_table", new_callable=AsyncMock) as mock_arp:
                mock_arp.return_value = {
                    "192.168.1.50": "00:05:a6:12:34:56",
                    "192.168.1.72": "04:fe:31:aa:bb:cc",
                }

                with patch("server.discovery.engine.scan_host_ports", new_callable=AsyncMock) as mock_ports:
                    mock_ports.side_effect = lambda ip, *a, **kw: {
                        "192.168.1.50": [23],
                        "192.168.1.72": [4352, 80],
                    }.get(ip, [])

                    with patch("server.discovery.engine.grab_banners", new_callable=AsyncMock) as mock_banners:
                        mock_banners.return_value = {}

                        # Mock passive scanners (Chunk 4) and SNMP (Chunk 5)
                        with patch("server.discovery.engine.MDNSScanner") as mock_mdns_cls, \
                             patch("server.discovery.engine.SSDPScanner") as mock_ssdp_cls, \
                             patch("server.discovery.engine.SNMPScanner") as mock_snmp_cls, \
                             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):
                            mock_mdns = MagicMock()
                            mock_mdns.start = AsyncMock(return_value={})
                            mock_mdns_cls.return_value = mock_mdns
                            mock_ssdp = MagicMock()
                            mock_ssdp.scan = AsyncMock(return_value={})
                            mock_ssdp_cls.return_value = mock_ssdp
                            mock_snmp = MagicMock()
                            mock_snmp.scan_devices = AsyncMock(return_value={})
                            mock_snmp_cls.return_value = mock_snmp

                            updates = []
                            async def capture_update(msg):
                                updates.append(msg)

                            await self.engine._scan_pipeline(["192.168.1.0/24"])

        # Should have found both devices
        assert len(self.engine.results) == 2

        # Check Extron device
        extron = self.engine.results.get("192.168.1.50")
        assert extron is not None
        assert extron.mac == "00:05:a6:12:34:56"
        assert extron.manufacturer == "Extron"
        assert extron.category == "switcher"
        assert 23 in extron.open_ports
        assert extron.confidence > 0

        # Check NEC device
        nec = self.engine.results.get("192.168.1.72")
        assert nec is not None
        assert nec.mac == "04:fe:31:aa:bb:cc"
        assert nec.manufacturer == "NEC"
        assert nec.category == "projector"
        assert 4352 in nec.open_ports
        assert 80 in nec.open_ports


# ===== Subnet Detection Tests =====


class TestGetLocalSubnets:
    def test_returns_list(self):
        """Should always return a list (may be empty if ifaddr not installed)."""
        result = get_local_subnets()
        assert isinstance(result, list)

    def test_excludes_loopback(self):
        result = get_local_subnets()
        for subnet in result:
            assert not subnet.startswith("127.")
