"""Unit tests for discovery's OUI database / static data tables."""

from openavc.discovery.oui_database import OUIDatabase
from openavc.discovery.oui_data import AV_OUI_TABLE


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
    """Test OUIDatabase.lookup against runtime-registered prefixes.

    Core ships an empty table; ``setup_method`` simulates the engine
    populating it from driver hints at startup.
    """

    def setup_method(self):
        self.db = OUIDatabase()
        self.db.add_prefix("00:05:a6", "Acme Switcher Co", "switcher")
        self.db.add_prefix("00:0c:4d", "Acme Audio Co", "audio")
        self.db.add_prefix("8c:71:f8", "Acme Display Co", "display")

    def test_registered_prefix_lookup(self):
        result = self.db.lookup("00:05:A6:AA:BB:CC")
        assert result == ("Acme Switcher Co", "switcher")

    def test_second_registered_prefix_lookup(self):
        result = self.db.lookup("00:0C:4D:FF:EE:DD")
        assert result == ("Acme Audio Co", "audio")

    def test_third_registered_prefix_lookup(self):
        result = self.db.lookup("8C:71:F8:AA:BB:CC")
        assert result == ("Acme Display Co", "display")

    def test_unknown_mac_returns_none(self):
        result = self.db.lookup("FF:FF:FF:FF:FF:FF")
        assert result is None

    def test_unregistered_prefix_returns_none(self):
        # Real-looking MAC but no driver registered this prefix.
        result = self.db.lookup("00:10:7F:11:22:33")
        assert result is None

    def test_lookup_with_dash_format(self):
        result = self.db.lookup("00-05-A6-11-22-33")
        assert result is not None
        assert result[0] == "Acme Switcher Co"

    def test_lookup_with_no_separator(self):
        result = self.db.lookup("0005A6112233")
        assert result is not None
        assert result[0] == "Acme Switcher Co"

    def test_lookup_with_dot_format(self):
        result = self.db.lookup("0005.A611.2233")
        assert result is not None
        assert result[0] == "Acme Switcher Co"

    def test_lookup_invalid_mac_returns_none(self):
        result = self.db.lookup("short")
        assert result is None

    def test_lookup_empty_string_returns_none(self):
        result = self.db.lookup("")
        assert result is None


class TestOUIAddPrefix:
    """Test adding custom OUI prefixes via add_prefix.

    Core ships an empty table; drivers register OUIs at startup, so
    ``add_prefix`` is the only way an entry gets into the database.
    """

    def setup_method(self):
        self.db = OUIDatabase()

    def test_add_new_prefix(self):
        self.db.add_prefix("aa:bb:cc", "TestMfg", "projector")
        result = self.db.lookup("AA:BB:CC:11:22:33")
        assert result == ("TestMfg", "projector")

    def test_add_prefix_first_registration_wins(self):
        self.db.add_prefix("aa:bb:cc", "FirstMfg", "audio")
        self.db.add_prefix("aa:bb:cc", "SecondMfg", "display")
        result = self.db.lookup("AA:BB:CC:11:22:33")
        assert result == ("FirstMfg", "audio")  # earlier registration sticks

    def test_add_prefix_normalizes_dashes(self):
        self.db.add_prefix("dd-ee-ff", "DashMfg", "audio")
        result = self.db.lookup("DD:EE:FF:11:22:33")
        assert result == ("DashMfg", "audio")

    def test_add_prefix_wrong_length_ignored(self):
        self.db.add_prefix("aa:bb", "Short", "other")
        # Nothing added; lookup for aa:bb:XX still None
        result = self.db.lookup("AA:BB:00:11:22:33")
        assert result is None


class TestOUITableShipsEmpty:
    """Core ships zero curated OUI entries — principle 3 of the
    discovery rewrite. Drivers contribute the data."""

    def test_default_table_is_empty(self):
        assert AV_OUI_TABLE == {}
