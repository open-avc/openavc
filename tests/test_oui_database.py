"""OUIDatabase canonicalizes hints in any separator style.

A driver's ``discovery.oui`` hint may be written bare (``001122``), dashed,
dotted, or as a full MAC; all must register the same key an observed MAC
resolves to, or the vendor label and OUI tier match silently never fire.
Uses an invented vendor — no real product, no read of the community drivers
repo. See CLAUDE.md.
"""

from __future__ import annotations

from server.discovery.oui_database import OUIDatabase, normalize_oui_prefix


def test_normalize_accepts_common_formats():
    for value in (
        "00:11:22",
        "00-11-22",
        "001122",
        "0011.22",
        "00:11:22:33:44:55",
        "001122334455",
        "0011.2233.4455",
        "  00:11:22  ",
        "00:1A:2b",
    ):
        assert normalize_oui_prefix(value) is not None, value

    # Every 00:11:22 spelling collapses to the one canonical key.
    assert normalize_oui_prefix("001122") == "00:11:22"
    assert normalize_oui_prefix("0011.2233.4455") == "00:11:22"
    assert normalize_oui_prefix("00:1A:2b") == "00:1a:2b"


def test_normalize_rejects_short_or_garbage():
    for value in ("", "00:11", "0011", "xyz", "gg:hh:ii", "0011.2"):
        assert normalize_oui_prefix(value) is None, value


def test_add_prefix_registers_bare_hex_hint():
    """A bare-hex hint resolves for an observed MAC — the core M-290 regression.

    Pre-fix ``add_prefix`` only kept prefixes already 8 chars after normalizing,
    so ``001122`` (6 chars) was dropped and the lookup returned None.
    """
    db = OUIDatabase()
    db.add_prefix("001122", "Acme", "audio")
    assert db.lookup("00:11:22:33:44:55") == ("Acme", "audio")


def test_add_prefix_dotted_hint_matches_dotted_mac():
    db = OUIDatabase()
    db.add_prefix("0011.22", "Acme", "display")
    assert db.lookup("0011.2233.4455") == ("Acme", "display")


def test_add_prefix_skips_unparseable():
    db = OUIDatabase()
    n_before = len(db._table)
    db.add_prefix("nope", "Acme", "audio")
    assert len(db._table) == n_before  # nothing registered


def test_add_prefix_earlier_registration_wins():
    """Colliding canonical keys keep the first registration — even when the
    later hint is spelled differently (001122 vs 00:11:22)."""
    db = OUIDatabase()
    db.add_prefix("00:11:22", "First", "audio")
    db.add_prefix("001122", "Second", "display")
    assert db.lookup("00:11:22:33:44:55") == ("First", "audio")
