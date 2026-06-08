"""Late ARP harvest for passive-discovered devices.

A device found only via mDNS/SSDP on a subnet the ping sweep skipped (e.g. a
switch at its factory-default link-local /16) never goes through the phase-4 ARP
harvest, so it has no MAC and an OUI-only driver hint can't surface it. The
engine's late harvest (phase 7, after the passive-only port scan populates the
OS ARP cache) fills that gap. The full scan pipeline does live network I/O and
isn't unit-testable; this exercises the harvest step in isolation with a stubbed
ARP table.
"""

from server.discovery import engine as engine_mod
from server.discovery.engine import DiscoveryEngine
from server.discovery.result import DeviceState, DiscoveredDevice
from server.discovery.tier_matcher import (
    KIND_OUI,
    SignalRule,
    TierMatcher,
)


def _engine_with_netgear_oui() -> DiscoveryEngine:
    eng = DiscoveryEngine()
    # OUI 28:94:01 is NETGEAR; register it the way load_driver_hints would.
    eng.oui_db.add_prefix("28:94:01", "NETGEAR", "utility")
    eng.signal_index.add_rule(
        SignalRule.for_oui("netgear_m4250_m4350", "28:94:01"))
    eng.tier_matcher = TierMatcher(eng.signal_index)
    return eng


async def test_late_harvest_enriches_passive_only_device(monkeypatch):
    eng = _engine_with_netgear_oui()
    # Passive-only device (mDNS-discovered, no MAC) + a ping-swept device that
    # already has its MAC from phase 4.
    passive = DiscoveredDevice(ip="169.254.100.100")
    passive.alive = True
    swept = DiscoveredDevice(ip="10.0.0.5", mac="aa:bb:cc:00:00:01")
    swept.alive = True
    eng.results = {passive.ip: passive, swept.ip: swept}

    async def fake_arp():
        return {"169.254.100.100": "28:94:01:7F:D8:F7"}

    monkeypatch.setattr(engine_mod, "harvest_arp_table", fake_arp)

    await eng._late_arp_harvest()

    # The passive device picked up its MAC, vendor, and an OUI evidence record.
    assert passive.mac == "28:94:01:7F:D8:F7"
    assert passive.manufacturer == "NETGEAR"
    oui_evs = [e for e in passive.evidence_log if e.data.get("kind") == KIND_OUI]
    assert len(oui_evs) == 1
    assert oui_evs[0].data["value"] == "28:94:01"

    # The ping-swept device is left untouched — no re-harvest, no extra evidence.
    assert swept.mac == "aa:bb:cc:00:00:01"
    assert swept.evidence_log == []

    # End to end: the harvested OUI now drives a possible match, which is what
    # turns the live-scan UNKNOWN into a NETGEAR candidate.
    match = eng.tier_matcher.match(passive.evidence_log)
    assert match.state == DeviceState.POSSIBLE
    assert "netgear_m4250_m4350" in match.candidates


async def test_late_harvest_noop_when_all_have_macs(monkeypatch):
    eng = _engine_with_netgear_oui()
    swept = DiscoveredDevice(ip="10.0.0.5", mac="aa:bb:cc:00:00:01")
    swept.alive = True
    eng.results = {swept.ip: swept}

    called = False

    async def fake_arp():
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(engine_mod, "harvest_arp_table", fake_arp)

    await eng._late_arp_harvest()

    # No MAC-less alive device, so the ARP table is never even read.
    assert called is False
    assert swept.evidence_log == []
