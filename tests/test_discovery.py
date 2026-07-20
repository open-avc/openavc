"""Tests for the device discovery module."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from server.discovery.oui_database import OUIDatabase
from server.discovery.oui_data import AV_OUI_TABLE
from server.discovery.result import (
    DiscoveredDevice,
    merge_device_info,
)
from server.discovery.network_scanner import (
    get_local_subnets,
    get_ranked_interface_ips,
    _parse_cidr,
)
from server.discovery.port_scanner import BANNER_PORTS, BASELINE_PORTS
from server.discovery.engine import DiscoveryEngine, ScanStatus


# ===== OUI Database Tests =====


class TestOUIDatabase:
    """Core ships an empty table; drivers register OUIs at startup via
    ``add_prefix``. These tests validate the runtime registration path."""

    def setup_method(self):
        self.db = OUIDatabase()
        # Simulate the engine populating the table from a driver hint.
        self.db.add_prefix("00:05:a6", "Acme Switcher Co", "switcher")
        self.db.add_prefix("8c:71:f8", "Acme Display Co", "display")

    def test_lookup_registered_prefix(self):
        result = self.db.lookup("00:05:A6:12:34:56")
        assert result is not None
        assert result[0] == "Acme Switcher Co"
        assert result[1] == "switcher"

    def test_lookup_unregistered_returns_none(self):
        result = self.db.lookup("AA:BB:CC:DD:EE:FF")
        assert result is None

    def test_lookup_normalizes_dash_format(self):
        result = self.db.lookup("00-05-A6-12-34-56")
        assert result is not None
        assert result[0] == "Acme Switcher Co"

    def test_lookup_normalizes_no_separator(self):
        result = self.db.lookup("0005A6123456")
        assert result is not None
        assert result[0] == "Acme Switcher Co"

    def test_lookup_case_insensitive(self):
        result = self.db.lookup("00:05:a6:12:34:56")
        assert result is not None
        assert result[0] == "Acme Switcher Co"

    def test_invalid_mac_returns_none(self):
        assert self.db.lookup("invalid") is None
        assert self.db.lookup("") is None
        assert self.db.lookup("00:11") is None

    def test_default_table_is_empty(self):
        # Core ships zero curated entries — the principle-3 contract.
        assert AV_OUI_TABLE == {}

    def test_first_registration_wins_on_collision(self):
        self.db.add_prefix("00:05:a6", "Different Vendor", "audio")
        # Original registration sticks.
        result = self.db.lookup("00:05:A6:11:22:33")
        assert result == ("Acme Switcher Co", "switcher")


# ===== Result Model Tests =====


class TestDiscoveredDevice:
    def test_create_minimal(self):
        d = DiscoveredDevice(ip="192.168.1.1")
        assert d.ip == "192.168.1.1"
        assert d.mac is None
        assert d.identification is None
        assert d.alive is True

    def test_to_dict(self):
        d = DiscoveredDevice(
            ip="192.168.1.50",
            mac="00:05:a6:12:34:56",
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
        )
        result = d.to_dict()
        assert result["ip"] == "192.168.1.50"
        assert result["mac"] == "00:05:a6:12:34:56"
        assert result["manufacturer"] == "Extron"
        assert result["category"] == "switcher"
        assert 23 in result["open_ports"]
        assert result["identification"] is None
        assert result["evidence_log"] == []


class TestMergeDeviceInfo:
    def test_basic_merge(self):
        device = DiscoveredDevice(ip="192.168.1.1")
        merge_device_info(device, {"manufacturer": "Extron"}, "oui")
        assert device.manufacturer == "Extron"

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
    def test_baseline_includes_telnet(self):
        # Telnet stays in the universal baseline because the new schema
        # supports banner-style probes (connect + read + match) that
        # any driver can declare against it.
        assert 23 in BASELINE_PORTS

    def test_baseline_includes_web_management(self):
        for p in (80, 443, 8080):
            assert p in BASELINE_PORTS

    def test_banner_ports_are_baseline_subset(self):
        # Banner-friendly ports must be ports we always scan, otherwise
        # we'd never have a banner to grab.
        for p in BANNER_PORTS:
            assert p in BASELINE_PORTS


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

    def test_results_sorted_identified_first(self):
        from server.discovery.result import IdentificationMatch

        self.engine.results["192.168.1.1"] = DiscoveredDevice(ip="192.168.1.1")
        self.engine.results["192.168.1.2"] = DiscoveredDevice(
            ip="192.168.1.2",
            identification=IdentificationMatch.identified(
                driver_id="x", source="probe:x",
            ),
        )
        self.engine.results["192.168.1.3"] = DiscoveredDevice(
            ip="192.168.1.3",
            identification=IdentificationMatch.possible(
                candidates=["y"], source="oui:00:11:22",
            ),
        )
        ips = [r["ip"] for r in self.engine.get_results()]
        assert ips == ["192.168.1.2", "192.168.1.3", "192.168.1.1"]

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

                        # Mock passive listeners + SNMP. Mock the community
                        # catalog fetch so the signal index is deterministically
                        # empty — otherwise the live GitHub fetch leaks 50+
                        # drivers into the index.
                        with patch("server.discovery.engine.MDNSScanner") as mock_mdns_cls, \
                             patch("server.discovery.engine.SSDPScanner") as mock_ssdp_cls, \
                             patch("server.discovery.engine.AMXDDPScanner") as mock_amx_cls, \
                             patch("server.discovery.engine.SNMPScanner") as mock_snmp_cls, \
                             patch.object(self.engine.community_index, "get_drivers", new_callable=AsyncMock, return_value=[]), \
                             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):
                            mock_mdns = MagicMock()
                            mock_mdns.start = AsyncMock(return_value={})
                            mock_mdns_cls.return_value = mock_mdns
                            mock_ssdp = MagicMock()
                            mock_ssdp.scan = AsyncMock(return_value={})
                            mock_ssdp_cls.return_value = mock_ssdp
                            mock_amx = MagicMock()
                            mock_amx.start = AsyncMock(return_value={})
                            mock_amx.stop = AsyncMock()
                            mock_amx_cls.return_value = mock_amx
                            mock_snmp = MagicMock()
                            mock_snmp.scan_devices = AsyncMock(return_value={})
                            mock_snmp_cls.return_value = mock_snmp

                            await self.engine._scan_pipeline(["192.168.1.0/24"])

        # Should have found both devices
        assert len(self.engine.results) == 2

        # First device — MAC + open ports merged from ARP/port scan;
        # manufacturer / category stay blank because no driver hint
        # registered the OUI prefix in this test (core ships an empty
        # OUI table by design).
        d1 = self.engine.results.get("192.168.1.50")
        assert d1 is not None
        assert d1.mac == "00:05:a6:12:34:56"
        assert 23 in d1.open_ports
        # Empty signal index → unknown identification.
        assert d1.identification is not None
        assert d1.identification.state.value == "unknown"

        # Second device
        d2 = self.engine.results.get("192.168.1.72")
        assert d2 is not None
        assert d2.mac == "04:fe:31:aa:bb:cc"
        assert 4352 in d2.open_ports
        assert 80 in d2.open_ports


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


# ===== Ranked Address Tests =====


class TestGetRankedInterfaceIps:
    """A multi-homed host has no single right answer for "my address", so the
    ranking decides which leg leads and callers that show one address take the
    first. Patched at the module's own names, so no real adapters involved."""

    def _ranked(self, ips, default_ip):
        with patch(
            "server.discovery.network_scanner.get_interface_ips", return_value=ips
        ), patch(
            "server.discovery.network_scanner.get_default_route_ip",
            return_value=default_ip,
        ):
            return get_ranked_interface_ips()

    def test_default_route_leg_leads(self):
        """The leg that reaches the internet is the one a laptop most likely
        shares, so it wins even when enumeration lists it last."""
        assert self._ranked(
            ["10.50.0.20", "192.168.1.20"], "192.168.1.20"
        ) == ["192.168.1.20", "10.50.0.20"]

    def test_private_beats_public(self):
        """A LAN address is the one a laptop in the room can reach."""
        assert self._ranked(["72.14.192.5", "192.168.1.20"], None) == [
            "192.168.1.20",
            "72.14.192.5",
        ]

    def test_ties_keep_adapter_order(self):
        """Two private legs and no default route: stable, not reshuffled."""
        ips = ["192.168.1.20", "10.50.0.20", "172.16.0.20"]
        assert self._ranked(ips, None) == ips

    def test_falls_back_to_route_lookup_when_enumeration_empty(self):
        """ifaddr missing -- the socket path still yields an address."""
        assert self._ranked([], "192.168.1.20") == ["192.168.1.20"]

    def test_empty_when_host_has_no_network(self):
        assert self._ranked([], None) == []

    def test_default_route_on_excluded_adapter_is_not_added(self):
        """A VPN leg is filtered out of enumeration on purpose; the route
        lookup must not smuggle it back in as the top pick."""
        assert self._ranked(["192.168.1.20"], "10.8.0.6") == ["192.168.1.20"]
