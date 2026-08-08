"""An inconsistent community catalog must degrade discovery by one rule,
not abort it.

Historically, a strong-signal collision anywhere in the folded catalog
raised out of ``build_signal_index`` and the engine fell back to an
installed-only index — every catalog driver's fingerprints (not just the
colliding ones) dropped out of scans at once. The index builder now
isolates the colliding rule (drops it, logs it) and keeps everything
else live.

Uses invented drivers (no real products) — this exercises the engine's
fold, not any specific device.
"""

from __future__ import annotations

import logging

from openavc.discovery import community_index as ci
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.tier_matcher import KIND_ACTIVE_PROBE, KIND_SSDP


def _catalog_entry(driver_id: str, discovery: dict) -> dict:
    return {
        "id": driver_id,
        "name": driver_id.replace("_", " ").title(),
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "tcp",
        "version": "1.0.0",
        "discovery": discovery,
    }


def _poisoned_catalog() -> list[dict]:
    """Two drivers claim the same URN unfiltered (the shape an old parser
    produces when it drops description filters it doesn't understand),
    plus a healthy third driver."""
    return [
        _catalog_entry("acme_mixer_a", {
            "ssdp": ["urn:acme:device:MixerFamily:1"],
        }),
        _catalog_entry("acme_mixer_b", {
            "ssdp": ["urn:acme:device:MixerFamily:1"],
        }),
        _catalog_entry("acme_widget", {
            "tcp_probe": {"port": 4999, "expect_regex": "ACME"},
        }),
    ]


def test_colliding_catalog_keeps_other_catalog_drivers_live(caplog):
    engine = DiscoveryEngine()
    engine._installed_registry = []
    with caplog.at_level(logging.ERROR):
        engine._rebuild_signal_index(_poisoned_catalog())

    # The healthy catalog driver's fingerprint survived the collision.
    probe = engine.signal_index.find_strong(KIND_ACTIVE_PROBE, "custom_acme_widget_tcp")
    assert probe is not None and probe.driver_id == "acme_widget"
    # One collider holds the contested claim; the other was dropped, logged.
    ssdp = engine.signal_index.find_strong(KIND_SSDP, "urn:acme:device:MixerFamily:1")
    assert ssdp is not None and ssdp.driver_id == "acme_mixer_a"
    assert "Dropping colliding discovery rule" in caplog.text
    # No installed-only fallback fired.
    assert "falling back to installed-only" not in caplog.text


def test_colliding_catalog_keeps_installed_claim_over_catalog(caplog):
    # Installed hints register first, so on a collision the installed
    # driver's claim survives and the catalog's collider is dropped.
    engine = DiscoveryEngine()
    engine._installed_registry = [_catalog_entry("acme_mixer_installed", {
        "ssdp": ["urn:acme:device:MixerFamily:1"],
    })]
    with caplog.at_level(logging.ERROR):
        engine._rebuild_signal_index(_poisoned_catalog())

    ssdp = engine.signal_index.find_strong(KIND_SSDP, "urn:acme:device:MixerFamily:1")
    assert ssdp is not None and ssdp.driver_id == "acme_mixer_installed"


def test_requires_gated_catalog_entry_skips_without_side_effects(monkeypatch, caplog):
    # A catalog entry gated on a newer platform contributes nothing on
    # this one — and the rest of the catalog folds in normally.
    monkeypatch.setattr(
        "openavc.discovery.hints._platform_version", lambda: "0.22.0",
    )
    engine = DiscoveryEngine()
    engine._installed_registry = []
    catalog = [
        _catalog_entry("acme_mixer_a", {
            "requires": "0.23.0",
            "ssdp": [{"device_type": "urn:acme:device:MixerFamily:1",
                      "model": "Mixer-6"}],
        }),
        _catalog_entry("acme_widget", {
            "tcp_probe": {"port": 4999, "expect_regex": "ACME"},
        }),
    ]
    with caplog.at_level(logging.ERROR):
        engine._rebuild_signal_index(catalog)

    assert engine.signal_index.find_strong(
        KIND_SSDP, "urn:acme:device:MixerFamily:1", txt={"model": "Mixer-6"},
    ) is None
    probe = engine.signal_index.find_strong(KIND_ACTIVE_PROBE, "custom_acme_widget_tcp")
    assert probe is not None and probe.driver_id == "acme_widget"
    assert "falling back to installed-only" not in caplog.text


# --- A malformed (non-object) catalog element must be skipped, not fatal ---


def _fetch_returning(data):
    async def _fetch(_path):
        return data
    return _fetch


async def test_index_cache_skips_non_object_driver_entries(monkeypatch, caplog):
    """A non-dict element in index.json is dropped; the rest survive."""
    payload = {"drivers": [
        {"id": "acme_widget", "version": "1.0.0"},
        "not-a-dict",
        None,
        {"id": "acme_gadget", "version": "1.0.0"},
    ]}
    monkeypatch.setattr(ci, "_fetch_json_with_retry", _fetch_returning(payload))
    cache = ci.CommunityIndexCache()
    with caplog.at_level(logging.WARNING):
        drivers = await cache.get_drivers()
    assert [d["id"] for d in drivers] == ["acme_widget", "acme_gadget"]
    assert all(isinstance(d, dict) for d in drivers)
    assert "skipped 2 malformed" in caplog.text


async def test_index_cache_unexpected_shape_ignored(monkeypatch):
    """A non-list `drivers` value is ignored rather than cached blindly."""
    monkeypatch.setattr(ci, "_fetch_json_with_retry", _fetch_returning({"drivers": "oops"}))
    cache = ci.CommunityIndexCache()
    assert await cache.get_drivers() == []


async def test_devices_cache_skips_non_object_entries(monkeypatch):
    """A non-dict element in devices.json is dropped; lookups still resolve."""
    payload = {"devices": [
        {"manufacturer": "Acme", "model": "X1", "drivers": [{"id": "acme_widget"}]},
        "not-a-dict",
        {"manufacturer": "Acme", "model": "X2", "drivers": [{"id": "acme_gadget"}]},
    ]}
    monkeypatch.setattr(ci, "_fetch_json_with_retry", _fetch_returning(payload))
    cache = ci.CommunityDevicesCache()
    assert await cache.find_drivers("Acme", "X2") == [{"id": "acme_gadget"}]
    assert all(isinstance(d, dict) for d in await cache.get_devices())


def test_rebuild_signal_index_skips_non_dict_catalog_entry(caplog):
    """Defense in depth: a non-dict catalog element folds to nothing, not a crash."""
    engine = DiscoveryEngine()
    engine._installed_registry = []
    catalog = [
        "not-a-dict",
        None,
        _catalog_entry("acme_widget", {"tcp_probe": {"port": 4999, "expect_regex": "ACME"}}),
    ]
    with caplog.at_level(logging.ERROR):
        engine._rebuild_signal_index(catalog)  # must not raise
    probe = engine.signal_index.find_strong(KIND_ACTIVE_PROBE, "custom_acme_widget_tcp")
    assert probe is not None and probe.driver_id == "acme_widget"
