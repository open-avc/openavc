"""Tests for driver matching and hints (Chunk 3)."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from server.discovery.hints import DriverHint, load_driver_hints
from server.discovery.driver_matcher import DriverMatcher
from server.discovery.result import DiscoveredDevice


# ===== Hint Loading Tests =====


class TestLoadDriverHints:
    def test_basic_loading(self):
        registry = [
            {
                "id": "extron_sis",
                "name": "Extron SIS",
                "manufacturer": "Extron",
                "category": "switcher",
                "transport": "tcp",
                "default_config": {"host": "", "port": 23},
                "config_schema": {"port": {"type": "integer", "default": 23}},
            },
        ]
        hints = load_driver_hints(registry)
        assert len(hints) == 1
        assert hints[0].driver_id == "extron_sis"
        assert hints[0].manufacturer == "Extron"
        assert hints[0].default_port == 23
        assert 23 in hints[0].ports

    def test_skips_generic_drivers(self):
        registry = [
            {"id": "generic_tcp", "name": "Generic TCP", "manufacturer": "Generic",
             "category": "other", "transport": "tcp"},
            {"id": "generic_http", "name": "Generic HTTP", "manufacturer": "Generic",
             "category": "other", "transport": "http"},
            {"id": "extron_sis", "name": "Extron SIS", "manufacturer": "Extron",
             "category": "switcher", "transport": "tcp", "default_config": {"port": 23}},
        ]
        hints = load_driver_hints(registry)
        assert len(hints) == 1
        assert hints[0].driver_id == "extron_sis"

    def test_port_from_config_schema(self):
        registry = [
            {
                "id": "samsung_mdc",
                "name": "Samsung MDC",
                "manufacturer": "Samsung",
                "category": "display",
                "transport": "tcp",
                "default_config": {},
                "config_schema": {"port": {"type": "integer", "default": 1515}},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].default_port == 1515

    def test_port_from_default_config(self):
        registry = [
            {
                "id": "pjlink",
                "name": "PJLink",
                "manufacturer": "Generic",
                "category": "projector",
                "transport": "tcp",
                "default_config": {"port": 4352},
                "config_schema": {},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].default_port == 4352

    def test_explicit_discovery_hints(self):
        registry = [
            {
                "id": "extron_sis",
                "name": "Extron SIS",
                "manufacturer": "Extron",
                "category": "switcher",
                "transport": "tcp",
                "default_config": {"port": 23},
                "discovery": {
                    "ports": [23],
                    "mac_prefixes": ["00:05:A6"],
                    "snmp_pattern": "Extron",
                    "hostname_patterns": ["^Extron", "^DTP"],
                },
            },
        ]
        hints = load_driver_hints(registry)
        h = hints[0]
        assert h.mac_prefixes == ["00:05:a6"]
        assert h.snmp_pattern == "Extron"
        assert len(h.hostname_patterns) == 2

    def test_empty_registry(self):
        hints = load_driver_hints([])
        assert hints == []

    def test_missing_id_skipped(self):
        hints = load_driver_hints([{"name": "No ID"}])
        assert hints == []


# ===== Driver Matcher Tests =====


def _make_hints() -> list[DriverHint]:
    """Create a realistic set of driver hints for testing."""
    return [
        DriverHint(
            driver_id="extron_sis",
            driver_name="Extron SIS Protocol",
            manufacturer="Extron",
            category="switcher",
            transport="tcp",
            ports=[23],
            default_port=23,
            mac_prefixes=["00:05:a6"],
        ),
        DriverHint(
            driver_id="biamp_tesira_ttp",
            driver_name="Biamp Tesira TTP",
            manufacturer="Biamp",
            category="audio",
            transport="tcp",
            ports=[23],
            default_port=23,
        ),
        DriverHint(
            driver_id="samsung_mdc",
            driver_name="Samsung MDC",
            manufacturer="Samsung",
            category="display",
            transport="tcp",
            ports=[1515],
            default_port=1515,
        ),
        DriverHint(
            driver_id="pjlink",
            driver_name="PJLink Projector",
            manufacturer="Generic",
            category="projector",
            transport="tcp",
            ports=[4352],
            default_port=4352,
        ),
        DriverHint(
            driver_id="lg_sicp",
            driver_name="LG SICP Display",
            manufacturer="LG",
            category="display",
            transport="tcp",
            ports=[9761],
            default_port=9761,
        ),
    ]


class TestDriverMatcher:
    def setup_method(self):
        self.matcher = DriverMatcher(_make_hints())

    def test_protocol_match_pjlink(self):
        """PJLink protocol should match pjlink driver."""
        device = DiscoveredDevice(
            ip="192.168.1.72",
            manufacturer="NEC",
            category="projector",
            open_ports=[4352],
            protocols=["pjlink"],
        )
        matches = self.matcher.match_device(device)
        assert len(matches) >= 1
        assert matches[0].driver_id == "pjlink"
        assert matches[0].confidence > 0.4

    def test_protocol_match_extron(self):
        """Extron SIS protocol should match extron_sis driver."""
        device = DiscoveredDevice(
            ip="192.168.1.50",
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
            protocols=["extron_sis"],
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "extron_sis"
        assert top.confidence > 0.7  # protocol + manufacturer + category + port

    def test_protocol_match_samsung(self):
        """Samsung MDC protocol should match samsung_mdc driver."""
        device = DiscoveredDevice(
            ip="192.168.1.80",
            manufacturer="Samsung",
            category="display",
            open_ports=[1515],
            protocols=["samsung_mdc"],
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "samsung_mdc"
        assert top.confidence > 0.7

    def test_manufacturer_only_match(self):
        """Device with manufacturer but no protocol still gets a match."""
        device = DiscoveredDevice(
            ip="192.168.1.80",
            manufacturer="Samsung",
            category="display",
            open_ports=[1515],
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "samsung_mdc"
        # Should have manufacturer + category + port
        assert top.confidence >= 0.40

    def test_port_and_category_match(self):
        """Device with matching port and category but unknown manufacturer."""
        device = DiscoveredDevice(
            ip="192.168.1.72",
            category="projector",
            open_ports=[4352],
        )
        matches = self.matcher.match_device(device)
        assert len(matches) >= 1
        assert matches[0].driver_id == "pjlink"
        assert matches[0].confidence >= 0.20

    def test_mac_prefix_match(self):
        """MAC OUI hint adds to match confidence."""
        device = DiscoveredDevice(
            ip="192.168.1.50",
            mac="00:05:a6:12:34:56",
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "extron_sis"
        assert "MAC prefix" in " ".join(top.match_reasons)

    def test_no_match_for_unknown_device(self):
        """Device with no AV indicators shouldn't match anything."""
        device = DiscoveredDevice(
            ip="192.168.1.1",
            open_ports=[80, 443],
        )
        matches = self.matcher.match_device(device)
        # Should have no matches or only very low-confidence ones
        assert all(m.confidence < 0.3 for m in matches)

    def test_multiple_matches_sorted(self):
        """Multiple drivers may match; they should be sorted by confidence."""
        device = DiscoveredDevice(
            ip="192.168.1.50",
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
            protocols=["extron_sis"],
        )
        matches = self.matcher.match_device(device)
        # Extron should be first (protocol + manufacturer + category + port)
        assert matches[0].driver_id == "extron_sis"
        # Verify descending confidence
        for i in range(len(matches) - 1):
            assert matches[i].confidence >= matches[i + 1].confidence

    def test_suggested_config_has_host_and_port(self):
        """Suggested config should pre-fill host and port."""
        device = DiscoveredDevice(
            ip="192.168.1.72",
            open_ports=[4352],
            protocols=["pjlink"],
        )
        matches = self.matcher.match_device(device)
        config = matches[0].suggested_config
        assert config["host"] == "192.168.1.72"
        assert config["port"] == 4352

    def test_two_port23_devices_disambiguated(self):
        """Two drivers using port 23 should be disambiguated by manufacturer."""
        # Extron device
        extron = DiscoveredDevice(
            ip="192.168.1.50",
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
            protocols=["extron_sis"],
        )
        matches = self.matcher.match_device(extron)
        assert matches[0].driver_id == "extron_sis"

        # Biamp device
        biamp = DiscoveredDevice(
            ip="192.168.1.60",
            manufacturer="Biamp",
            category="audio",
            open_ports=[23],
            protocols=["biamp_tesira"],
        )
        matches = self.matcher.match_device(biamp)
        assert matches[0].driver_id == "biamp_tesira_ttp"

    def test_hostname_match(self):
        """Hostname patterns should contribute to matching."""
        import re
        hints = [
            DriverHint(
                driver_id="extron_sis",
                driver_name="Extron SIS",
                manufacturer="Extron",
                category="switcher",
                transport="tcp",
                ports=[23],
                default_port=23,
                hostname_patterns=[re.compile(r"^DTP", re.IGNORECASE)],
            ),
        ]
        matcher = DriverMatcher(hints)
        device = DiscoveredDevice(
            ip="192.168.1.50",
            hostname="DTP-CrossPoint-84",
            manufacturer="Extron",
            open_ports=[23],
        )
        matches = matcher.match_device(device)
        assert len(matches) >= 1
        assert "Hostname" in " ".join(matches[0].match_reasons)


# ===== Integration: Engine with Driver Matching =====


class TestEngineDriverMatching:
    @pytest.mark.asyncio
    async def test_scan_pipeline_matches_drivers(self):
        from server.discovery.engine import DiscoveryEngine
        from server.discovery.protocol_prober import ProbeResult

        engine = DiscoveryEngine()

        # Load hints
        registry = [
            {
                "id": "extron_sis",
                "name": "Extron SIS Protocol",
                "manufacturer": "Extron",
                "category": "switcher",
                "transport": "tcp",
                "default_config": {"port": 23},
                "config_schema": {},
            },
            {
                "id": "pjlink",
                "name": "PJLink Projector",
                "manufacturer": "Generic",
                "category": "projector",
                "transport": "tcp",
                "default_config": {"port": 4352},
                "config_schema": {},
            },
        ]
        engine.load_driver_hints_from_registry(registry)

        with patch("server.discovery.engine.ping_sweep", new_callable=AsyncMock) as mock_ping, \
             patch("server.discovery.engine.harvest_arp_table", new_callable=AsyncMock) as mock_arp, \
             patch("server.discovery.engine.scan_host_ports", new_callable=AsyncMock) as mock_ports, \
             patch("server.discovery.engine.grab_banners", new_callable=AsyncMock, return_value={}), \
             patch("server.discovery.engine.run_protocol_probes", new_callable=AsyncMock) as mock_probes, \
             patch("server.discovery.engine.MDNSScanner") as mdns_cls, \
             patch("server.discovery.engine.SSDPScanner") as ssdp_cls, \
             patch("server.discovery.engine.SNMPScanner") as snmp_cls, \
             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):

            # Configure passive scanner mocks to return immediately
            mock_mdns = MagicMock()
            mock_mdns.start = AsyncMock(return_value={})
            mdns_cls.return_value = mock_mdns
            mock_ssdp = MagicMock()
            mock_ssdp.scan = AsyncMock(return_value={})
            ssdp_cls.return_value = mock_ssdp
            mock_snmp = MagicMock()
            mock_snmp.scan_devices = AsyncMock(return_value={})
            snmp_cls.return_value = mock_snmp

            mock_ping.return_value = ["192.168.1.50", "192.168.1.72"]
            mock_arp.return_value = {
                "192.168.1.50": "00:05:a6:12:34:56",
                "192.168.1.72": "04:fe:31:aa:bb:cc",
            }
            mock_ports.side_effect = lambda ip, *a, **kw: {
                "192.168.1.50": [23],
                "192.168.1.72": [4352],
            }.get(ip, [])
            mock_probes.side_effect = lambda ip, ports, banners=None: {
                "192.168.1.50": [ProbeResult(
                    protocol="extron_sis", manufacturer="Extron",
                    model="DTP CrossPoint 84", category="switcher",
                )],
                "192.168.1.72": [ProbeResult(
                    protocol="pjlink", manufacturer="NEC",
                    model="PA1004UL", category="projector",
                )],
            }.get(ip, [])

            await engine._scan_pipeline(["192.168.1.0/24"])

        # Check Extron device has matched driver
        extron = engine.results["192.168.1.50"]
        assert len(extron.matched_drivers) >= 1
        assert extron.matched_drivers[0].driver_id == "extron_sis"
        assert extron.matched_drivers[0].suggested_config["host"] == "192.168.1.50"
        assert "driver_matched" in extron.sources

        # Check NEC/PJLink device has matched driver
        nec = engine.results["192.168.1.72"]
        assert len(nec.matched_drivers) >= 1
        assert nec.matched_drivers[0].driver_id == "pjlink"
        assert nec.matched_drivers[0].suggested_config["port"] == 4352
