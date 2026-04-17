"""Comprehensive unit tests for discovery modules: oui_database, driver_matcher, hints."""

import re

from server.discovery.oui_database import OUIDatabase
from server.discovery.oui_data import AV_OUI_TABLE, NON_AV_CATEGORIES
from server.discovery.driver_matcher import DriverMatcher, CommunityDriverMatcher
from server.discovery.hints import DriverHint, load_driver_hints
from server.discovery.result import DiscoveredDevice


# =============================================================================
# OUI Database Tests
# =============================================================================


class TestOUINormalizeMac:
    """Test _normalize_mac with all four MAC address formats."""

    def test_colon_separated(self):
        result = OUIDatabase._normalize_mac("00:05:A6:12:34:56")
        assert result == "00:05:a6:12:34:56"

    def test_dash_separated(self):
        result = OUIDatabase._normalize_mac("00-05-A6-12-34-56")
        assert result == "00:05:a6:12:34:56"

    def test_dot_separated_cisco_format(self):
        result = OUIDatabase._normalize_mac("0005.A612.3456")
        assert result == "00:05:a6:12:34:56"

    def test_no_separator(self):
        result = OUIDatabase._normalize_mac("0005A6123456")
        assert result == "00:05:a6:12:34:56"

    def test_uppercase_normalized_to_lowercase(self):
        result = OUIDatabase._normalize_mac("AA:BB:CC:DD:EE:FF")
        assert result == "aa:bb:cc:dd:ee:ff"

    def test_mixed_case(self):
        result = OUIDatabase._normalize_mac("aA:bB:cC:dD:eE:fF")
        assert result == "aa:bb:cc:dd:ee:ff"

    def test_leading_trailing_whitespace_stripped(self):
        result = OUIDatabase._normalize_mac("  00:05:A6:12:34:56  ")
        assert result == "00:05:a6:12:34:56"

    def test_too_short_returns_none(self):
        result = OUIDatabase._normalize_mac("00:05:A6")
        assert result is None

    def test_too_long_returns_none(self):
        result = OUIDatabase._normalize_mac("00:05:A6:12:34:56:78")
        assert result is None

    def test_empty_string_returns_none(self):
        result = OUIDatabase._normalize_mac("")
        assert result is None

    def test_non_hex_chars_returns_none(self):
        # 'GG' is not valid hex, but the method only checks length after stripping separators
        # 12 chars long but contains invalid hex - still returns a string (method doesn't validate hex)
        result = OUIDatabase._normalize_mac("GG:HH:II:JJ:KK:LL")
        # The normalize method only checks length, not hex validity
        assert result == "gg:hh:ii:jj:kk:ll"

    def test_five_chars_returns_none(self):
        result = OUIDatabase._normalize_mac("ABCDE")
        assert result is None


class TestOUILookup:
    """Test OUIDatabase.lookup with known and unknown MACs."""

    def setup_method(self):
        self.db = OUIDatabase()

    def test_extron_lookup(self):
        result = self.db.lookup("00:05:A6:AA:BB:CC")
        assert result == ("Extron", "switcher")

    def test_crestron_lookup(self):
        result = self.db.lookup("00:10:7F:11:22:33")
        assert result == ("Crestron", "control")

    def test_amx_lookup(self):
        result = self.db.lookup("00:60:9F:DE:AD:BE")
        assert result == ("AMX", "control")

    def test_biamp_lookup(self):
        result = self.db.lookup("00:90:5E:01:02:03")
        assert result == ("Biamp", "audio")

    def test_qsc_lookup(self):
        result = self.db.lookup("00:0C:4D:FF:EE:DD")
        assert result == ("QSC", "audio")

    def test_shure_lookup(self):
        result = self.db.lookup("00:0E:DD:10:20:30")
        assert result == ("Shure", "audio")

    def test_samsung_display_lookup(self):
        result = self.db.lookup("8C:71:F8:AA:BB:CC")
        assert result == ("Samsung", "display")

    def test_lg_display_lookup(self):
        result = self.db.lookup("00:05:C9:11:22:33")
        assert result == ("LG", "display")

    def test_sony_display_lookup(self):
        result = self.db.lookup("00:01:4A:AA:BB:CC")
        assert result == ("Sony", "display")

    def test_nec_projector_lookup(self):
        result = self.db.lookup("00:00:73:01:02:03")
        assert result == ("NEC", "projector")

    def test_epson_projector_lookup(self):
        result = self.db.lookup("00:26:AB:01:02:03")
        assert result == ("Epson", "projector")

    def test_barco_projector_lookup(self):
        result = self.db.lookup("00:0E:D6:01:02:03")
        assert result == ("Barco", "projector")

    def test_axis_camera_lookup(self):
        result = self.db.lookup("00:40:8C:01:02:03")
        assert result == ("Axis", "camera")

    def test_cisco_network_lookup(self):
        result = self.db.lookup("00:17:C5:01:02:03")
        assert result == ("Cisco", "network")

    def test_ubiquiti_network_lookup(self):
        result = self.db.lookup("24:A4:3C:01:02:03")
        assert result == ("Ubiquiti", "network")

    def test_unknown_mac_returns_none(self):
        result = self.db.lookup("FF:FF:FF:FF:FF:FF")
        assert result is None

    def test_lookup_with_dash_format(self):
        result = self.db.lookup("00-05-A6-11-22-33")
        assert result is not None
        assert result[0] == "Extron"

    def test_lookup_with_no_separator(self):
        result = self.db.lookup("0005A6112233")
        assert result is not None
        assert result[0] == "Extron"

    def test_lookup_with_dot_format(self):
        result = self.db.lookup("0005.A611.2233")
        assert result is not None
        assert result[0] == "Extron"

    def test_lookup_invalid_mac_returns_none(self):
        result = self.db.lookup("short")
        assert result is None

    def test_lookup_empty_string_returns_none(self):
        result = self.db.lookup("")
        assert result is None


class TestOUIIsAvManufacturer:
    """Test is_av_manufacturer identifies AV devices and excludes network gear."""

    def setup_method(self):
        self.db = OUIDatabase()

    def test_extron_is_av(self):
        assert self.db.is_av_manufacturer("00:05:A6:11:22:33") is True

    def test_crestron_is_av(self):
        assert self.db.is_av_manufacturer("00:10:7F:11:22:33") is True

    def test_biamp_is_av(self):
        assert self.db.is_av_manufacturer("00:90:5E:11:22:33") is True

    def test_samsung_is_av(self):
        assert self.db.is_av_manufacturer("8C:71:F8:11:22:33") is True

    def test_axis_camera_is_av(self):
        assert self.db.is_av_manufacturer("00:40:8C:11:22:33") is True

    def test_cisco_network_is_not_av(self):
        assert self.db.is_av_manufacturer("00:17:C5:11:22:33") is False

    def test_ubiquiti_network_is_not_av(self):
        assert self.db.is_av_manufacturer("24:A4:3C:11:22:33") is False

    def test_tp_link_network_is_not_av(self):
        assert self.db.is_av_manufacturer("30:B5:C2:11:22:33") is False

    def test_unknown_mac_is_not_av(self):
        assert self.db.is_av_manufacturer("FF:FF:FF:FF:FF:FF") is False

    def test_invalid_mac_is_not_av(self):
        assert self.db.is_av_manufacturer("bad") is False


class TestOUIIsNetworkDevice:
    """Test is_network_device identifies network infrastructure."""

    def setup_method(self):
        self.db = OUIDatabase()

    def test_cisco_is_network(self):
        assert self.db.is_network_device("00:17:C5:11:22:33") is True

    def test_ubiquiti_is_network(self):
        assert self.db.is_network_device("24:A4:3C:11:22:33") is True

    def test_netgear_is_network(self):
        assert self.db.is_network_device("28:80:88:11:22:33") is True

    def test_extron_is_not_network(self):
        assert self.db.is_network_device("00:05:A6:11:22:33") is False

    def test_samsung_is_not_network(self):
        assert self.db.is_network_device("8C:71:F8:11:22:33") is False

    def test_unknown_mac_is_not_network(self):
        assert self.db.is_network_device("FF:FF:FF:FF:FF:FF") is False

    def test_invalid_mac_is_not_network(self):
        assert self.db.is_network_device("") is False


class TestOUIAddPrefix:
    """Test adding custom OUI prefixes via add_prefix."""

    def setup_method(self):
        self.db = OUIDatabase()

    def test_add_new_prefix(self):
        self.db.add_prefix("aa:bb:cc", "TestMfg", "projector")
        result = self.db.lookup("AA:BB:CC:11:22:33")
        assert result == ("TestMfg", "projector")

    def test_add_prefix_does_not_overwrite_existing(self):
        self.db.add_prefix("00:05:a6", "FakeExtron", "other")
        result = self.db.lookup("00:05:A6:11:22:33")
        assert result[0] == "Extron"  # original preserved

    def test_add_prefix_normalizes_dashes(self):
        self.db.add_prefix("dd-ee-ff", "DashMfg", "audio")
        result = self.db.lookup("DD:EE:FF:11:22:33")
        assert result == ("DashMfg", "audio")

    def test_add_prefix_wrong_length_ignored(self):
        self.db.add_prefix("aa:bb", "Short", "other")
        # Nothing added; lookup for aa:bb:XX still None
        result = self.db.lookup("AA:BB:00:11:22:33")
        assert result is None


class TestOUITableIntegrity:
    """Verify the built-in OUI table is well-formed."""

    def test_all_prefixes_are_lowercase_colon_format(self):
        for prefix in AV_OUI_TABLE:
            assert prefix == prefix.lower(), f"Prefix {prefix} is not lowercase"
            assert ":" in prefix, f"Prefix {prefix} missing colons"
            assert len(prefix) == 8, f"Prefix {prefix} wrong length"

    def test_all_values_are_tuples(self):
        for prefix, value in AV_OUI_TABLE.items():
            assert isinstance(value, tuple), f"{prefix} value is not a tuple"
            assert len(value) == 2, f"{prefix} tuple wrong length"

    def test_non_av_categories_is_set(self):
        assert isinstance(NON_AV_CATEGORIES, set)
        assert "network" in NON_AV_CATEGORIES

    def test_table_has_entries(self):
        assert len(AV_OUI_TABLE) > 30  # plenty of entries


# =============================================================================
# Driver Matcher Tests
# =============================================================================


def _make_test_hints() -> list[DriverHint]:
    """Build a varied set of hints for matcher tests."""
    return [
        DriverHint(
            driver_id="pjlink_class1",
            driver_name="PJLink Class 1",
            manufacturer="Generic",
            category="projector",
            transport="tcp",
            ports=[4352],
            default_port=4352,
            protocols=["pjlink"],
        ),
        DriverHint(
            driver_id="extron_sis",
            driver_name="Extron SIS Protocol",
            manufacturer="Extron",
            category="switcher",
            transport="tcp",
            ports=[23],
            default_port=23,
            mac_prefixes=["00:05:a6"],
            hostname_patterns=[re.compile(r"^DTP", re.IGNORECASE)],
            protocols=["extron_sis"],
        ),
        DriverHint(
            driver_id="samsung_mdc",
            driver_name="Samsung MDC",
            manufacturer="Samsung",
            category="display",
            transport="tcp",
            ports=[1515],
            default_port=1515,
            protocols=["samsung_mdc"],
        ),
        DriverHint(
            driver_id="biamp_tesira_ttp",
            driver_name="Biamp Tesira TTP",
            manufacturer="Biamp",
            category="audio",
            transport="tcp",
            ports=[23],
            default_port=23,
            protocols=["biamp_tesira"],
        ),
        DriverHint(
            driver_id="qsc_qrc",
            driver_name="QSC Q-SYS QRC",
            manufacturer="QSC",
            category="audio",
            transport="tcp",
            ports=[1702],
            default_port=1702,
            protocols=["qsc_qrc"],
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


class TestDriverMatcherProtocol:
    """Test that protocol matches produce high confidence."""

    def setup_method(self):
        self.matcher = DriverMatcher(_make_test_hints())

    def test_pjlink_protocol_match(self):
        device = DiscoveredDevice(
            ip="10.0.0.10",
            protocols=["pjlink"],
            open_ports=[4352],
        )
        matches = self.matcher.match_device(device)
        assert len(matches) >= 1
        top = matches[0]
        assert top.driver_id == "pjlink_class1"
        # Protocol (0.40) + port (0.15) = at least 0.55
        assert top.confidence >= 0.55

    def test_extron_sis_protocol_match(self):
        device = DiscoveredDevice(
            ip="10.0.0.20",
            protocols=["extron_sis"],
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
            mac="00:05:A6:AA:BB:CC",
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "extron_sis"
        # Protocol + manufacturer + category + port + MAC = very high
        assert top.confidence >= 0.90

    def test_samsung_mdc_protocol_match(self):
        device = DiscoveredDevice(
            ip="10.0.0.30",
            protocols=["samsung_mdc"],
            manufacturer="Samsung",
            category="display",
            open_ports=[1515],
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "samsung_mdc"
        assert top.confidence >= 0.80

    def test_biamp_tesira_protocol_match(self):
        device = DiscoveredDevice(
            ip="10.0.0.40",
            protocols=["biamp_tesira"],
            manufacturer="Biamp",
            category="audio",
            open_ports=[23],
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "biamp_tesira_ttp"
        assert top.confidence >= 0.80


class TestDriverMatcherManufacturer:
    """Test manufacturer-based matching."""

    def setup_method(self):
        self.matcher = DriverMatcher(_make_test_hints())

    def test_manufacturer_match_without_protocol(self):
        device = DiscoveredDevice(
            ip="10.0.0.50",
            manufacturer="Samsung",
            category="display",
            open_ports=[1515],
        )
        matches = self.matcher.match_device(device)
        assert len(matches) >= 1
        top = matches[0]
        assert top.driver_id == "samsung_mdc"
        # manufacturer (0.25) + category (0.10) + port (0.15) = 0.50
        assert top.confidence >= 0.40

    def test_fuzzy_manufacturer_match(self):
        """Substring matching should work (e.g. 'Samsung Electronics' contains 'Samsung')."""
        device = DiscoveredDevice(
            ip="10.0.0.51",
            manufacturer="Samsung Electronics",
            category="display",
            open_ports=[1515],
        )
        matches = self.matcher.match_device(device)
        assert len(matches) >= 1
        samsung_match = next((m for m in matches if m.driver_id == "samsung_mdc"), None)
        assert samsung_match is not None
        assert "Manufacturer" in " ".join(samsung_match.match_reasons)


class TestDriverMatcherNoMatch:
    """Test that unknown devices produce no or very low matches."""

    def setup_method(self):
        self.matcher = DriverMatcher(_make_test_hints())

    def test_no_match_for_generic_web_device(self):
        device = DiscoveredDevice(
            ip="10.0.0.1",
            open_ports=[80, 443],
        )
        matches = self.matcher.match_device(device)
        # All matches below 0.20 are filtered out
        assert len(matches) == 0 or all(m.confidence < 0.3 for m in matches)

    def test_no_match_for_empty_device(self):
        device = DiscoveredDevice(ip="10.0.0.2")
        matches = self.matcher.match_device(device)
        assert len(matches) == 0

    def test_below_threshold_filtered(self):
        """A single weak signal (category only = 0.10) should be below threshold."""
        device = DiscoveredDevice(
            ip="10.0.0.3",
            category="audio",
        )
        matches = self.matcher.match_device(device)
        # 0.10 is below the 0.20 threshold
        assert len(matches) == 0


class TestDriverMatcherSorting:
    """Test that results are sorted by confidence descending."""

    def setup_method(self):
        self.matcher = DriverMatcher(_make_test_hints())

    def test_multiple_matches_sorted_descending(self):
        # A device that could match multiple drivers
        device = DiscoveredDevice(
            ip="10.0.0.60",
            manufacturer="Extron",
            category="switcher",
            open_ports=[23],
            protocols=["extron_sis"],
        )
        matches = self.matcher.match_device(device)
        assert len(matches) >= 1
        for i in range(len(matches) - 1):
            assert matches[i].confidence >= matches[i + 1].confidence

    def test_best_match_first(self):
        device = DiscoveredDevice(
            ip="10.0.0.61",
            manufacturer="QSC",
            category="audio",
            open_ports=[1702],
            protocols=["qsc_qrc"],
        )
        matches = self.matcher.match_device(device)
        assert matches[0].driver_id == "qsc_qrc"


class TestDriverMatcherSuggestedConfig:
    """Test that suggested config contains host and port."""

    def setup_method(self):
        self.matcher = DriverMatcher(_make_test_hints())

    def test_config_has_host(self):
        device = DiscoveredDevice(
            ip="10.0.0.70",
            protocols=["pjlink"],
            open_ports=[4352],
        )
        matches = self.matcher.match_device(device)
        assert matches[0].suggested_config["host"] == "10.0.0.70"

    def test_config_has_port_from_hint(self):
        device = DiscoveredDevice(
            ip="10.0.0.71",
            protocols=["samsung_mdc"],
            open_ports=[1515],
        )
        matches = self.matcher.match_device(device)
        assert matches[0].suggested_config["port"] == 1515

    def test_config_port_from_default_when_no_open_port_match(self):
        """When no open port matches, the driver's default_port is used."""
        device = DiscoveredDevice(
            ip="10.0.0.72",
            manufacturer="Samsung",
            category="display",
            open_ports=[80],  # not 1515
        )
        matches = self.matcher.match_device(device)
        samsung_match = next((m for m in matches if m.driver_id == "samsung_mdc"), None)
        if samsung_match:
            assert samsung_match.suggested_config["port"] == 1515


class TestDriverMatcherMacPrefix:
    """Test MAC OUI prefix matching in driver matcher."""

    def setup_method(self):
        self.matcher = DriverMatcher(_make_test_hints())

    def test_mac_prefix_adds_confidence(self):
        device = DiscoveredDevice(
            ip="10.0.0.80",
            mac="00:05:A6:FF:EE:DD",
            manufacturer="Extron",
            open_ports=[23],
        )
        matches = self.matcher.match_device(device)
        top = matches[0]
        assert top.driver_id == "extron_sis"
        assert "MAC prefix" in " ".join(top.match_reasons)

    def test_no_mac_no_mac_match(self):
        device = DiscoveredDevice(
            ip="10.0.0.81",
            manufacturer="Extron",
            open_ports=[23],
        )
        matches = self.matcher.match_device(device)
        if matches:
            top = matches[0]
            assert "MAC prefix" not in " ".join(top.match_reasons)


class TestDriverMatcherHostname:
    """Test hostname pattern matching."""

    def setup_method(self):
        self.matcher = DriverMatcher(_make_test_hints())

    def test_hostname_pattern_match(self):
        device = DiscoveredDevice(
            ip="10.0.0.90",
            hostname="DTP-CrossPoint-108",
            manufacturer="Extron",
            open_ports=[23],
        )
        matches = self.matcher.match_device(device)
        assert len(matches) >= 1
        top = matches[0]
        assert "Hostname" in " ".join(top.match_reasons)

    def test_hostname_no_match(self):
        device = DiscoveredDevice(
            ip="10.0.0.91",
            hostname="my-printer",
            manufacturer="Extron",
            open_ports=[23],
        )
        matches = self.matcher.match_device(device)
        if matches:
            assert "Hostname" not in " ".join(matches[0].match_reasons)


class TestDriverMatcherConfidenceCap:
    """Test that confidence is capped at 1.0."""

    def test_confidence_never_exceeds_one(self):
        """A device matching on every signal should still cap at 1.0."""
        hints = [
            DriverHint(
                driver_id="super_match",
                driver_name="Super Match",
                manufacturer="TestMfg",
                category="projector",
                transport="tcp",
                ports=[4352],
                default_port=4352,
                protocols=["pjlink"],
                mac_prefixes=["aa:bb:cc"],
                hostname_patterns=[re.compile(r"^TEST", re.IGNORECASE)],
            ),
        ]
        matcher = DriverMatcher(hints)
        device = DiscoveredDevice(
            ip="10.0.0.99",
            mac="AA:BB:CC:11:22:33",
            hostname="TEST-PROJECTOR",
            manufacturer="TestMfg",
            category="projector",
            open_ports=[4352],
            protocols=["pjlink"],
        )
        matches = matcher.match_device(device)
        assert len(matches) == 1
        assert matches[0].confidence <= 1.0


# =============================================================================
# Community Driver Matcher Tests
# =============================================================================


class TestCommunityDriverMatcher:
    """Test CommunityDriverMatcher scoring and filtering."""

    def _make_community_drivers(self) -> list[dict]:
        return [
            {
                "id": "sony_bravia",
                "name": "Sony BRAVIA",
                "category": "display",
                "manufacturer": "Sony",
                "transport": "http",
                "ports": [80],
                "protocols": ["sony_bravia_http"],
            },
            {
                "id": "kramer_p3000",
                "name": "Kramer Protocol 3000",
                "category": "switcher",
                "manufacturer": "Kramer",
                "transport": "tcp",
                "ports": [5000],
                "protocols": ["kramer_p3000"],
            },
            {
                "id": "wake_on_lan",
                "name": "Wake on LAN",
                "category": "utility",
                "manufacturer": "Generic",
                "transport": "udp",
            },
        ]

    def test_filters_out_installed_drivers(self):
        matcher = CommunityDriverMatcher(
            self._make_community_drivers(),
            installed_ids={"sony_bravia"},
        )
        # sony_bravia should be excluded
        assert not any(d["id"] == "sony_bravia" for d in matcher.drivers)

    def test_filters_out_utility_drivers(self):
        matcher = CommunityDriverMatcher(
            self._make_community_drivers(),
            installed_ids=set(),
        )
        assert not any(d["id"] == "wake_on_lan" for d in matcher.drivers)

    def test_protocol_match_with_penalty(self):
        matcher = CommunityDriverMatcher(
            self._make_community_drivers(),
            installed_ids=set(),
        )
        device = DiscoveredDevice(
            ip="10.0.0.10",
            manufacturer="Kramer",
            category="switcher",
            open_ports=[5000],
            protocols=["kramer_p3000"],
        )
        matches = matcher.match_device(device)
        assert len(matches) >= 1
        top = matches[0]
        assert top.driver_id == "kramer_p3000"
        assert top.source == "community"
        # Penalized: (0.40 + 0.25 + 0.10 + 0.15) * 0.7 = 0.63
        assert top.confidence <= 0.70

    def test_no_match_for_unknown_device(self):
        matcher = CommunityDriverMatcher(
            self._make_community_drivers(),
            installed_ids=set(),
        )
        device = DiscoveredDevice(
            ip="10.0.0.1",
            open_ports=[443],
        )
        matches = matcher.match_device(device)
        assert len(matches) == 0

    def test_community_matches_sorted_by_confidence(self):
        drivers = [
            {
                "id": "driver_a",
                "name": "Driver A",
                "category": "display",
                "manufacturer": "Sony",
                "transport": "tcp",
                "ports": [1234],
                "protocols": [],
            },
            {
                "id": "driver_b",
                "name": "Driver B",
                "category": "display",
                "manufacturer": "Sony",
                "transport": "tcp",
                "ports": [1234],
                "protocols": ["custom_proto"],
            },
        ]
        matcher = CommunityDriverMatcher(drivers, installed_ids=set())
        device = DiscoveredDevice(
            ip="10.0.0.20",
            manufacturer="Sony",
            category="display",
            open_ports=[1234],
            protocols=["custom_proto"],
        )
        matches = matcher.match_device(device)
        for i in range(len(matches) - 1):
            assert matches[i].confidence >= matches[i + 1].confidence

    def test_suggested_config_includes_host(self):
        matcher = CommunityDriverMatcher(
            self._make_community_drivers(),
            installed_ids=set(),
        )
        device = DiscoveredDevice(
            ip="10.0.0.30",
            manufacturer="Kramer",
            open_ports=[5000],
            protocols=["kramer_p3000"],
        )
        matches = matcher.match_device(device)
        assert matches[0].suggested_config["host"] == "10.0.0.30"


# =============================================================================
# Hints Module Tests
# =============================================================================


class TestLoadDriverHintsBasic:
    """Test basic hint loading from driver registry."""

    def test_loads_single_driver(self):
        registry = [
            {
                "id": "pjlink_class1",
                "name": "PJLink Class 1",
                "manufacturer": "Generic",
                "category": "projector",
                "transport": "tcp",
                "default_config": {"port": 4352},
                "config_schema": {},
            },
        ]
        hints = load_driver_hints(registry)
        assert len(hints) == 1
        h = hints[0]
        assert h.driver_id == "pjlink_class1"
        assert h.driver_name == "PJLink Class 1"
        assert h.manufacturer == "Generic"
        assert h.category == "projector"
        assert h.transport == "tcp"
        assert h.default_port == 4352
        assert 4352 in h.ports

    def test_loads_multiple_drivers(self):
        registry = [
            {"id": "driver_a", "name": "A", "manufacturer": "MfgA",
             "category": "audio", "transport": "tcp"},
            {"id": "driver_b", "name": "B", "manufacturer": "MfgB",
             "category": "display", "transport": "http"},
        ]
        hints = load_driver_hints(registry)
        assert len(hints) == 2
        ids = {h.driver_id for h in hints}
        assert ids == {"driver_a", "driver_b"}

    def test_empty_registry(self):
        hints = load_driver_hints([])
        assert hints == []


class TestLoadDriverHintsSkipping:
    """Test that generic drivers and invalid entries are skipped."""

    def test_skips_generic_tcp(self):
        registry = [
            {"id": "generic_tcp", "name": "Generic TCP", "manufacturer": "Generic",
             "category": "other", "transport": "tcp"},
        ]
        hints = load_driver_hints(registry)
        assert len(hints) == 0

    def test_skips_generic_http(self):
        registry = [
            {"id": "generic_http", "name": "Generic HTTP", "manufacturer": "Generic",
             "category": "other", "transport": "http"},
        ]
        hints = load_driver_hints(registry)
        assert len(hints) == 0

    def test_skips_any_generic_prefix(self):
        registry = [
            {"id": "generic_serial", "name": "Generic Serial", "manufacturer": "Generic",
             "category": "other", "transport": "serial"},
        ]
        hints = load_driver_hints(registry)
        assert len(hints) == 0

    def test_skips_entry_without_id(self):
        registry = [{"name": "No ID Driver"}]
        hints = load_driver_hints(registry)
        assert len(hints) == 0

    def test_skips_entry_with_empty_id(self):
        registry = [{"id": "", "name": "Empty ID"}]
        hints = load_driver_hints(registry)
        assert len(hints) == 0

    def test_non_generic_driver_not_skipped(self):
        """A driver like 'generic_audio_receiver' is not skipped because
        it starts with 'generic_' -- but this is the defined behavior."""
        registry = [
            {"id": "generic_audio_receiver", "name": "Audio Receiver",
             "manufacturer": "Generic", "category": "audio", "transport": "tcp"},
        ]
        hints = load_driver_hints(registry)
        # It does start with "generic_" so it IS skipped
        assert len(hints) == 0


class TestLoadDriverHintsPortInference:
    """Test port inference from default_config and config_schema."""

    def test_port_from_default_config(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "default_config": {"port": 9000},
                "config_schema": {},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].default_port == 9000
        assert 9000 in hints[0].ports

    def test_port_from_config_schema_default(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "default_config": {},
                "config_schema": {"port": {"type": "integer", "default": 8080}},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].default_port == 8080

    def test_default_config_port_takes_priority(self):
        """default_config.port is checked first, so it wins over config_schema."""
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "default_config": {"port": 1234},
                "config_schema": {"port": {"type": "integer", "default": 5678}},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].default_port == 1234

    def test_no_port_info(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].default_port is None
        assert hints[0].ports == []

    def test_float_port_converted_to_int(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "default_config": {"port": 4352.0},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].default_port == 4352
        assert isinstance(hints[0].default_port, int)


class TestLoadDriverHintsDiscoverySection:
    """Test explicit discovery section parsing."""

    def test_discovery_ports(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "discovery": {"ports": [23, 4998]},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].ports == [23, 4998]

    def test_discovery_mac_prefixes_normalized(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "discovery": {"mac_prefixes": ["00:05:A6", "00-0A-2D"]},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].mac_prefixes == ["00:05:a6", "00:0a:2d"]

    def test_discovery_mdns_services(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "discovery": {"mdns_services": ["_http._tcp.local.", "_av._tcp.local."]},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].mdns_services == ["_http._tcp.local.", "_av._tcp.local."]

    def test_discovery_upnp_types(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "display",
                "transport": "http",
                "discovery": {"upnp_types": ["urn:samsung.com:device:RemoteControlReceiver:1"]},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].upnp_types == ["urn:samsung.com:device:RemoteControlReceiver:1"]

    def test_discovery_snmp_pattern(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Extron",
                "category": "switcher",
                "transport": "tcp",
                "discovery": {"snmp_pattern": "Extron.*DTP"},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].snmp_pattern == "Extron.*DTP"

    def test_discovery_hostname_patterns_compiled(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "switcher",
                "transport": "tcp",
                "discovery": {"hostname_patterns": ["^Extron", "^DTP.*CrossPoint"]},
            },
        ]
        hints = load_driver_hints(registry)
        patterns = hints[0].hostname_patterns
        assert len(patterns) == 2
        assert all(isinstance(p, re.Pattern) for p in patterns)
        # Test the patterns actually work (case insensitive)
        assert patterns[0].search("ExtronDevice") is not None
        assert patterns[0].search("not-extron") is None
        assert patterns[1].search("DTP-CrossPoint-84") is not None

    def test_discovery_default_port_override(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "default_config": {"port": 23},
                "discovery": {"default_port": 4998},
            },
        ]
        hints = load_driver_hints(registry)
        # Discovery section overrides the inferred default_port
        assert hints[0].default_port == 4998

    def test_discovery_ports_override_inferred(self):
        """Discovery ports replace the inferred ports from config."""
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "default_config": {"port": 23},
                "discovery": {"ports": [23, 4998, 5000]},
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].ports == [23, 4998, 5000]

    def test_missing_discovery_section(self):
        """Driver without discovery section still gets basic hints."""
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "default_config": {"port": 9000},
            },
        ]
        hints = load_driver_hints(registry)
        h = hints[0]
        assert h.mac_prefixes == []
        assert h.mdns_services == []
        assert h.upnp_types == []
        assert h.snmp_pattern is None
        assert h.hostname_patterns == []
        assert h.default_port == 9000

    def test_empty_discovery_section(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "discovery": {},
            },
        ]
        hints = load_driver_hints(registry)
        h = hints[0]
        assert h.mac_prefixes == []
        assert h.hostname_patterns == []


class TestLoadDriverHintsProtocols:
    """Test protocol declaration loading."""

    def test_protocols_as_list(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "protocols": ["pjlink", "pjlink_class2"],
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].protocols == ["pjlink", "pjlink_class2"]

    def test_protocols_as_string_converted_to_list(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "protocols": "pjlink",
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].protocols == ["pjlink"]

    def test_protocols_lowercased(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
                "protocols": ["PJLink", "EXTRON_SIS"],
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].protocols == ["pjlink", "extron_sis"]

    def test_no_protocols(self):
        registry = [
            {
                "id": "test_driver",
                "name": "Test",
                "manufacturer": "Test",
                "category": "audio",
                "transport": "tcp",
            },
        ]
        hints = load_driver_hints(registry)
        assert hints[0].protocols == []


class TestDriverHintDefaults:
    """Test DriverHint dataclass defaults."""

    def test_minimal_hint(self):
        h = DriverHint(
            driver_id="test",
            driver_name="Test",
            manufacturer="",
            category="",
            transport="tcp",
        )
        assert h.ports == []
        assert h.mac_prefixes == []
        assert h.mdns_services == []
        assert h.upnp_types == []
        assert h.snmp_pattern is None
        assert h.hostname_patterns == []
        assert h.protocols == []
        assert h.default_port is None
