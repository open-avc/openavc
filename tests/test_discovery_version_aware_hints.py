"""Discovery signal-index collision resolution is version-aware.

When a driver is both installed locally and present in the community catalog,
the NEWER copy is authoritative for discovery fingerprints. A stale installed
driver must not mask a newer catalog's improved fingerprints — otherwise a
device a clean machine would identify outright would show only as a weak
"possible" just because an older copy happens to be installed.

Uses an invented driver (no real product) — this exercises the platform's
collision logic, not any specific device.
"""

from __future__ import annotations

from openavc.discovery.engine import DiscoveryEngine
import pytest

from openavc.discovery.known_drivers import driver_copies


def _entry(version: str, *, with_probe: bool) -> dict:
    """A registry/catalog entry for the invented `acme_widget` driver. With the
    probe it carries a strong tcp_probe fingerprint; without it only a soft OUI."""
    discovery: dict = {"oui": ["aa:bb:cc"]}
    if with_probe:
        discovery["tcp_probe"] = {"port": 4999, "expect_regex": "ACME"}
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "version": version,
        "discovery": discovery,
    }


def _acme_hints(engine: DiscoveryEngine):
    return [h for h in engine.discovery_hints if h.driver_id == "acme_widget"]


def test_newer_catalog_supersedes_stale_installed_hints():
    # Installed v1.1.0 has only the soft OUI; catalog v1.2.0 adds the strong
    # tcp_probe. The newer catalog must win so the device can be identified.
    engine = DiscoveryEngine()
    engine._installed_registry = [_entry("1.1.0", with_probe=False)]
    engine._rebuild_signal_index([_entry("1.2.0", with_probe=True)])

    hints = _acme_hints(engine)
    assert len(hints) == 1  # the stale installed hint was dropped, not merged
    assert hints[0].tcp_probe is not None  # catalog's strong fingerprint is live


def test_installed_wins_when_newer():
    # Installed v2.0.0 (with a probe) must not be downgraded by a stale catalog.
    engine = DiscoveryEngine()
    engine._installed_registry = [_entry("2.0.0", with_probe=True)]
    engine._rebuild_signal_index([_entry("1.0.0", with_probe=False)])

    hints = _acme_hints(engine)
    assert len(hints) == 1
    assert hints[0].tcp_probe is not None  # the installed (newer) copy kept


def test_installed_wins_on_equal_version():
    engine = DiscoveryEngine()
    engine._installed_registry = [_entry("1.0.0", with_probe=True)]
    engine._rebuild_signal_index([_entry("1.0.0", with_probe=False)])

    hints = _acme_hints(engine)
    assert len(hints) == 1
    assert hints[0].tcp_probe is not None  # tie -> installed authoritative


def test_catalog_only_driver_contributes():
    # No installed counterpart: the catalog covers it ("Install & Add").
    engine = DiscoveryEngine()
    engine._installed_registry = []
    engine._rebuild_signal_index([_entry("1.0.0", with_probe=True)])

    hints = _acme_hints(engine)
    assert len(hints) == 1
    assert hints[0].tcp_probe is not None


def _which_copy(installed_version, catalog_version) -> str:
    """Which copy of one driver Discovery reads: "installed" or "catalog"."""
    installed = [{"id": "acme_widget", "version": installed_version}]
    catalog = [{"id": "acme_widget", "version": catalog_version}]
    kept, read = driver_copies(installed, catalog)
    assert len(kept) + len(read) == 1
    return "catalog" if read else "installed"


@pytest.mark.parametrize("installed,catalog,expected", [
    ("1.2.0", "1.3.0", "catalog"),
    ("1.9.0", "1.10.0", "catalog"),
    ("1.1.9", "1.2.0", "catalog"),
    ("1.3.0", "1.2.0", "installed"),
    ("1.2.0", "1.2.0", "installed"),
    # A short version is its padded spelling, so this is a tie.
    ("2.0", "2.0.0", "installed"),
    # A pre-release is older than its release, whichever way it is spelled.
    ("1.2.3-beta", "1.2.3", "catalog"),
    ("1.2.3rc1", "1.2.3", "catalog"),
    ("1.2.3", "1.2.3-rc1", "installed"),
    # An unreadable or missing version sorts lowest.
    (None, "1.0.0", "catalog"),
    ("", "1.0.0", "catalog"),
    ("garbage", "1.0.0", "catalog"),
    ("1.0.0", "garbage", "installed"),
    ("1.0.0", None, "installed"),
    ("garbage", "garbage", "installed"),
    (None, None, "installed"),
])
def test_the_newer_copy_is_read_by_the_version_rule(installed, catalog, expected):
    assert _which_copy(installed, catalog) == expected
