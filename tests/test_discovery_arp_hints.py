"""An ARP entry points the scan at an address; it never invents a device.

The ARP table names addresses the ping sweep missed — real hosts on a cold
address cache lose their echo before it is sent. Those get a second ping and
a port scan, but a host that answers nothing at all is a cache entry, not a
piece of equipment, and must not be reported as one.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from openavc.discovery import icmp

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.result import DiscoveredDevice, Evidence, SignalTier
from openavc.discovery.scan_budget import ArpPlan

_ARP = "openavc.discovery.arp_phase"


async def _quiet_ping(ip, timeout=1.0, source_ip="", method=""):
    return icmp.RESULT_TIMEOUT


def _engine_with(ip: str, **fields) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.results[ip] = DiscoveredDevice(ip=ip, **fields)
    engine._arp_provisional = {ip}
    return engine


async def _finish(engine: DiscoveryEngine) -> None:
    """The matcher, then the scan's ending, where the probation ends."""
    await engine._finalize_scan()
    await engine._end_scan(truncated=False, cancelled=False)


def _count(engine: DiscoveryEngine) -> int:
    return engine.get_status()["devices_found"]


@pytest.mark.parametrize(
    "fields",
    [
        # What the device answered that leaves no fingerprint record.
        {"open_ports": [10001]},
        {"ssdp_info": {"server": "Linux/3.0 UPnP/1.0"}},
        {"snmp_info": {"sysDescr": "switch"}},
        {"device_name": "AMP-1"},
    ],
)
def test_an_arp_host_that_answers_something_is_kept(fields):
    engine = _engine_with("192.168.4.24", mac="aa:bb:cc:dd:ee:ff", **fields)
    assert engine._answered_for_itself(engine.results["192.168.4.24"])


# The tiers whose records are the device's own words. A tier added to
# SignalTier fails here until it is placed on one side or the other.
_THE_DEVICES_OWN = {
    SignalTier.PASSIVE_LISTENER,
    SignalTier.BROADCAST_PROBE,
    SignalTier.ACTIVE_PROBE,
}


@pytest.mark.parametrize("tier", list(SignalTier))
def test_a_record_counts_as_the_device_answering_by_its_tier(tier):
    engine = _engine_with("192.168.4.24", mac="aa:bb:cc:dd:ee:ff")
    device = engine.results["192.168.4.24"]
    device.evidence_log.append(Evidence(tier=tier, source="acme:1"))
    assert engine._answered_for_itself(device) is (tier in _THE_DEVICES_OWN)


@pytest.mark.asyncio
async def test_an_arp_host_with_only_its_card_maker_and_a_dns_name_is_dropped():
    # Through the ARP phase itself, so the host carries the OUI and host-name
    # records every probation host gets: a cache entry and a PTR record are
    # not the device answering.
    engine = DiscoveryEngine()
    engine.scan_status.subnets = ["192.168.4.0/24"]
    engine._ping_method = icmp.METHOD_DGRAM
    with patch.object(icmp, "ping_host", side_effect=_quiet_ping), \
         patch(f"{_ARP}.harvest_arp_table", return_value={"192.168.4.47": "00:60:9f:aa:bb:cc"}), \
         patch(f"{_ARP}._resolve_hostnames", return_value={"192.168.4.47": "old-laptop.lan"}):
        await engine._arp_phase([], ArpPlan(name_lookup_hosts=None, netbios=False, budget=10.0))
    device = engine.results["192.168.4.47"]
    assert {ev.source.split(":")[0] for ev in device.evidence_log} == {"oui", "hostname"}
    assert engine._arp_provisional == {"192.168.4.47"}

    await _finish(engine)

    assert "192.168.4.47" not in engine.results


@pytest.mark.asyncio
async def test_an_arp_host_that_answered_netbios_is_kept():
    # A NetBIOS name is the device answering, though its record is the same
    # host-name record reverse DNS leaves.
    engine = DiscoveryEngine()
    engine.scan_status.subnets = ["192.168.4.0/24"]
    engine._ping_method = icmp.METHOD_DGRAM
    with patch.object(icmp, "ping_host", side_effect=_quiet_ping), \
         patch(f"{_ARP}.harvest_arp_table", return_value={"192.168.4.48": "00:60:9f:aa:bb:cd"}), \
         patch(f"{_ARP}._resolve_hostnames", return_value={}), \
         patch(f"{_ARP}.netbios_sweep", return_value={"192.168.4.48": {"hostname": "AMP-1"}}):
        await engine._arp_phase([], ArpPlan(name_lookup_hosts=None, netbios=True, budget=10.0))
    assert engine._arp_provisional == {"192.168.4.48"}

    await _finish(engine)

    assert "192.168.4.48" in engine.results


def test_a_reverse_dns_name_is_not_the_device_answering():
    # The name server replied, not the equipment. A cached ARP entry plus a
    # stale PTR record must not add up to a discovered device.
    engine = _engine_with(
        "192.168.4.47", mac="aa:bb:cc:dd:ee:ff", hostname="old-laptop.lan"
    )
    assert not engine._answered_for_itself(engine.results["192.168.4.47"])


@pytest.mark.asyncio
async def test_the_scans_end_drops_an_arp_host_that_answered_nothing():
    engine = _engine_with("192.168.4.47", mac="aa:bb:cc:dd:ee:ff")
    await _finish(engine)
    assert "192.168.4.47" not in engine.results
    assert _count(engine) == 0


@pytest.mark.asyncio
async def test_the_scans_end_keeps_an_arp_host_with_an_open_port():
    engine = _engine_with(
        "192.168.4.24", mac="aa:bb:cc:dd:ee:ff", open_ports=[10001]
    )
    await _finish(engine)
    assert "192.168.4.24" in engine.results


@pytest.mark.asyncio
async def test_a_ping_answering_host_is_never_dropped_for_being_quiet():
    # Not provisional: it answered ICMP, so silence on every other channel
    # is just an unidentified device, which is a normal thing to report.
    engine = DiscoveryEngine()
    engine.results["192.168.4.20"] = DiscoveredDevice(ip="192.168.4.20")
    engine._arp_provisional = set()
    await _finish(engine)
    assert "192.168.4.20" in engine.results


@pytest.mark.asyncio
async def test_reping_records_answers_as_they_arrive():
    # The caller owns the set precisely so that cutting this pass short on
    # its ceiling keeps every host that had already replied.
    engine = DiscoveryEngine()
    engine._ping_method = icmp.METHOD_DGRAM

    async def fake_ping(ip, timeout=1.0, source_ip="", method=""):
        return icmp.RESULT_ALIVE if ip.endswith(".7") else icmp.RESULT_TIMEOUT

    answered: set[str] = set()
    with patch.object(icmp, "ping_host", side_effect=fake_ping):
        await engine._reping(["10.0.0.7", "10.0.0.8"], answered)

    assert answered == {"10.0.0.7"}


@pytest.mark.asyncio
async def test_a_cancelled_reping_keeps_what_already_answered():
    engine = DiscoveryEngine()
    engine._ping_method = icmp.METHOD_DGRAM
    answered: set[str] = set()

    async def slow_ping(ip, timeout=1.0, source_ip="", method=""):
        if ip.endswith(".1"):
            return icmp.RESULT_ALIVE
        await asyncio.sleep(30)  # never returns inside the ceiling
        return icmp.RESULT_TIMEOUT

    with patch.object(icmp, "ping_host", side_effect=slow_ping):
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(
                engine._reping(["10.0.0.1", "10.0.0.2"], answered), timeout=0.3
            )

    # The host that replied before the cut is still known.
    assert "10.0.0.1" in answered


@pytest.mark.asyncio
async def test_an_unproven_host_is_never_announced_mid_scan():
    # A panel cannot un-see a device, so one we might drop must not go out.
    engine = _engine_with("192.168.4.47", mac="aa:bb:cc:dd:ee:ff")
    sent = []
    engine.on_update = lambda msg: sent.append(msg) or asyncio.sleep(0)
    await engine._emit_device_update(engine.results["192.168.4.47"], "arp_harvest")
    assert sent == []


@pytest.mark.asyncio
async def test_a_host_that_earns_its_place_is_announced_at_the_end():
    engine = _engine_with(
        "192.168.4.24", mac="aa:bb:cc:dd:ee:ff", open_ports=[10001]
    )
    sent = []

    async def capture(msg):
        sent.append(msg)

    engine.on_update = capture
    await _finish(engine)

    announced = [m for m in sent if m.get("type") == "discovery.update"]
    assert [m["device"]["ip"] for m in announced] == ["192.168.4.24"]
    # A phase the panel already has a label for. An invented name would
    # fall through its lookup and relabel a finished scan "Scanning...".
    assert announced[0]["phase"] == "finalize"
    assert "192.168.4.24" in engine.results


@pytest.mark.asyncio
async def test_a_dropped_host_is_never_announced_at_all():
    engine = _engine_with("192.168.4.47", mac="aa:bb:cc:dd:ee:ff")
    sent = []

    async def capture(msg):
        sent.append(msg)

    engine.on_update = capture
    await _finish(engine)

    assert [m for m in sent if m.get("type") == "discovery.update"] == []
    assert "192.168.4.47" not in engine.results


@pytest.mark.asyncio
async def test_the_live_count_matches_what_the_panel_is_shown():
    # An unproven host is not on screen, so counting it would report more
    # devices than are visible and then tick DOWN when it is dropped.
    engine = DiscoveryEngine()
    engine._arp_provisional = set()

    engine._get_or_create("192.168.4.20")            # answered a ping
    assert _count(engine) == 1

    engine._get_or_create("192.168.4.47")            # ARP named it only
    engine._arp_provisional.add("192.168.4.47")
    assert _count(engine) == 1

    await _finish(engine)                            # it answered nothing
    assert _count(engine) == 1
    assert "192.168.4.47" not in engine.results


@pytest.mark.asyncio
async def test_a_host_that_earns_its_place_joins_the_count():
    engine = DiscoveryEngine()
    engine._arp_provisional = set()
    engine._get_or_create("192.168.4.24").open_ports = [10001]
    engine._arp_provisional.add("192.168.4.24")
    assert _count(engine) == 0

    await _finish(engine)
    assert _count(engine) == 1


# ---------------------------------------------------------------------------
# A host the ARP table named that then spoke for itself, through the scan's
# own paths: the second look that puts it on probation, the listener or probe
# merge that records what it said, the matcher, and the scan's end.

_ACME_WIDGET = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "category": "audio",
    "manufacturer": "Acme",
    "transport": "tcp",
    "discovery": {"amx_ddp": [{"make": "Acme", "model_pattern": "Widget*"}]},
}


class _Silent:
    """A passive listener that heard nobody."""

    results: dict = {}


async def _on_probation(ip: str) -> DiscoveryEngine:
    """An engine whose second look put ``ip`` on probation: the ARP table
    named it and it ignored the ping."""
    engine = DiscoveryEngine()
    engine.load_driver_hints_from_registry([dict(_ACME_WIDGET)])
    engine._ping_method = icmp.METHOD_DGRAM
    with patch.object(icmp, "ping_host", side_effect=_quiet_ping):
        await engine._second_look([ip], [])
    assert engine._arp_provisional == {ip}
    return engine


@pytest.mark.asyncio
async def test_an_arp_host_identified_by_its_amx_beacon_is_kept():
    from openavc.discovery.amx_ddp_scanner import AMXDDPScanner

    engine = await _on_probation("10.77.0.9")
    beacons = AMXDDPScanner()
    # Make and Model, no Config-Name: the beacon names no device.
    beacons._handle_datagram(
        b"AMXB<-UUID=00:60:9f:aa:bb:cc><-SDKClass=AudioProcessor>"
        b"<-Make=Acme><-Model=Widget 3000><-Revision=1.0.0>",
        "10.77.0.9",
    )
    engine._passive_scanners = (_Silent(), _Silent(), beacons)
    await engine.merge_passive_results()

    await _finish(engine)

    device = engine.results.get("10.77.0.9")
    assert device is not None
    assert device.identification.state.value == "identified"
    assert device.identification.driver_id == "acme_widget"
    assert _count(engine) == 1


@pytest.mark.asyncio
async def test_an_arp_host_that_answered_a_udp_probe_is_kept():
    from openavc.discovery.tier_matcher import evidence_broadcast

    engine = await _on_probation("10.77.0.9")
    # A reply that named nothing: the device answered, and that is all.
    engine._probe_finished[0] = [
        ("10.77.0.9", evidence_broadcast("custom_acme_widget_udp", {"ip": "10.77.0.9"}, port=9999),
         "broadcast_probe"),
    ]
    await engine.merge_probe_results()

    await _finish(engine)

    assert "10.77.0.9" in engine.results
