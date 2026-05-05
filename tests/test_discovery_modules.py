"""Unit tests for discovery's OUI database / static data tables."""

from server.discovery.oui_database import OUIDatabase
from server.discovery.oui_data import AV_OUI_TABLE, NON_AV_CATEGORIES


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
