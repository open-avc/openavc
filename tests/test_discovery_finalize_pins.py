"""What the matcher phase (``DiscoveryEngine._finalize_scan``) leaves behind:
the category an identification brings, the progress bar at its end, the
updates it sends, and the lines it logs when it drops devices."""

from __future__ import annotations

import logging

import pytest

from openavc.discovery.engine import DiscoveryEngine, PHASE_WEIGHTS
from openavc.discovery.tier_matcher import evidence_mdns

pytestmark = pytest.mark.asyncio

_GADGET = {
    "id": "acme_gadget", "name": "Acme Gadget", "manufacturer": "Acme",
    "category": "audio", "transport": "tcp",
    "discovery": {"mdns": "_acmegadget._tcp.local."},
}


def _engine() -> tuple[DiscoveryEngine, list[dict]]:
    engine = DiscoveryEngine()
    engine.load_driver_hints_from_registry([dict(_GADGET)])
    events: list[dict] = []

    async def on_update(message: dict) -> None:
        events.append(message)

    engine._on_update = on_update
    return engine, events


def _alive(engine: DiscoveryEngine, ip: str, *, mdns: bool = False):
    device = engine._get_or_create(ip)
    device.alive = True
    if mdns:
        device.evidence_log.append(evidence_mdns("_acmegadget._tcp.local."))
    return device


async def test_an_identified_device_takes_its_drivers_category():
    engine, _ = _engine()
    device = _alive(engine, "192.0.2.20", mdns=True)
    await engine._finalize_scan()
    assert device.identification.driver_id == "acme_gadget"
    assert device.category == "audio"


async def test_a_category_already_on_the_device_stays():
    engine, _ = _engine()
    device = _alive(engine, "192.0.2.20", mdns=True)
    device.category = "display"
    await engine._finalize_scan()
    assert device.category == "display"


async def test_an_unidentified_device_gets_no_category():
    engine, _ = _engine()
    device = _alive(engine, "192.0.2.21")
    await engine._finalize_scan()
    assert device.category is None


async def test_the_bar_ends_full_after_the_last_device():
    engine, _ = _engine()
    _alive(engine, "192.0.2.20", mdns=True)
    _alive(engine, "192.0.2.21")
    await engine._finalize_scan()
    assert engine.scan_status.progress == pytest.approx(1.0)


async def test_with_no_devices_the_bar_stays_where_the_phase_starts():
    engine, _ = _engine()
    await engine._finalize_scan()
    weights = PHASE_WEIGHTS["standard"]
    assert engine.scan_status.progress == pytest.approx(1.0 - weights["finalize"])
    assert engine.scan_status.phase == "finalize"
    assert engine.scan_status.phase_number == 8


async def test_every_shown_device_is_updated_once_as_driver_match():
    engine, events = _engine()
    _alive(engine, "192.0.2.20", mdns=True)
    _alive(engine, "192.0.2.21")
    await engine._finalize_scan()
    updates = [(e["device"]["ip"], e["phase"]) for e in events if e["type"] == "discovery.update"]
    assert updates == [("192.0.2.20", "driver_match"), ("192.0.2.21", "driver_match")]


async def test_the_drops_are_logged_with_their_counts(caplog):
    engine, _ = _engine()
    _alive(engine, "192.0.2.20")
    engine._get_or_create("192.0.2.30").alive = False   # not re-found this scan
    _alive(engine, "192.0.2.40")
    engine._arp_provisional.add("192.0.2.40")    # named by ARP, answered nothing
    with caplog.at_level(logging.INFO, logger="discovery"):
        await engine._finalize_scan()
    assert "Removed 1 stale devices not found in this scan" in caplog.messages
    assert "Dropped 1 ARP-named host(s) that answered nothing" in caplog.messages
    assert sorted(engine.results) == ["192.0.2.20"]
    assert engine._arp_provisional == set()


async def test_nothing_dropped_logs_neither_line(caplog):
    engine, _ = _engine()
    _alive(engine, "192.0.2.20")
    with caplog.at_level(logging.INFO, logger="discovery"):
        await engine._finalize_scan()
    assert not any("stale" in m or "ARP-named" in m for m in caplog.messages)
