"""What rebuilding the signal index hands the matcher besides the index: one
profile per driver discovery reads (the copy it reads), and the category
table an identification fills from."""

from __future__ import annotations

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.tier_matcher import DriverProfile


def _driver(driver_id: str, version: str, category: str, models: list[str]) -> dict:
    return {
        "id": driver_id, "version": version, "category": category,
        "compatible_models": [{"manufacturer": "Acme", "models": models}],
    }


def test_the_matcher_gets_a_profile_for_every_copy_read():
    engine = DiscoveryEngine()
    engine._installed_registry = [
        _driver("acme_widget", "1.0.0", "Display", ["W-100"]),
        _driver("acme_gadget", "2.0.0", "audio", ["G-1"]),
    ]
    engine._rebuild_signal_index([
        # Newer than the installed copy: the catalog's profile is read.
        _driver("acme_widget", "1.1.0", "projector", ["W-200"]),
        # Older than the installed copy: the installed profile stays.
        _driver("acme_gadget", "1.0.0", "display", ["G-0"]),
        # Not installed at all.
        _driver("acme_lamp", "1.0.0", "lighting", []),
        {"version": "9.0.0", "category": "audio"},
    ])
    profiles = engine.tier_matcher.profiles
    assert set(profiles) == {"acme_widget", "acme_gadget", "acme_lamp"}
    assert profiles["acme_widget"] == DriverProfile.of(
        _driver("acme_widget", "1.1.0", "projector", ["W-200"]),
    )
    assert profiles["acme_gadget"] == DriverProfile.of(
        _driver("acme_gadget", "2.0.0", "audio", ["G-1"]),
    )
    assert profiles["acme_lamp"].category == "lighting"
    assert engine._driver_categories == {
        "acme_widget": "projector", "acme_gadget": "audio", "acme_lamp": "lighting",
    }


def test_a_driver_with_no_usable_category_fills_nothing():
    engine = DiscoveryEngine()
    engine._installed_registry = [
        {"id": "acme_widget", "category": ""},
        {"id": "acme_gadget", "category": 7},
        {"id": "acme_lamp"},
    ]
    engine._rebuild_signal_index([])
    assert set(engine.tier_matcher.profiles) == {"acme_widget", "acme_gadget", "acme_lamp"}
    assert engine._driver_categories.get("acme_gadget") is None
    assert engine._driver_categories.get("acme_lamp") is None
    assert not engine._driver_categories.get("acme_widget")
