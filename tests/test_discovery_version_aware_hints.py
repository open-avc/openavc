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

from server.discovery.engine import DiscoveryEngine, _driver_version_tuple


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


def test_driver_version_tuple():
    assert _driver_version_tuple("1.2.0") == (1, 2, 0)
    assert _driver_version_tuple("2.0") == (2, 0, 0)
    assert _driver_version_tuple("1") == (1, 0, 0)
    assert _driver_version_tuple(None) == (0, 0, 0)
    assert _driver_version_tuple("") == (0, 0, 0)
    assert _driver_version_tuple("1.2.3-beta") == (1, 2, 3)
    assert _driver_version_tuple("garbage") == (0, 0, 0)
    assert _driver_version_tuple("1.10.0") > _driver_version_tuple("1.9.0")
    assert _driver_version_tuple("1.2.0") > _driver_version_tuple("1.1.9")
