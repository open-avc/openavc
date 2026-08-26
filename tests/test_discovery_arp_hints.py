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
from openavc.discovery.result import DiscoveredDevice


def _engine_with(ip: str, **fields) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.results[ip] = DiscoveredDevice(ip=ip, **fields)
    engine._arp_provisional = {ip}
    return engine


@pytest.mark.parametrize(
    "fields",
    [
        {"open_ports": [10001]},
        {"mdns_services": ["_http._tcp"]},
        {"ssdp_info": {"server": "Linux/3.0 UPnP/1.0"}},
        {"snmp_info": {"sysDescr": "switch"}},
        {"protocols": ["telnet"]},
        {"device_name": "AMP-1"},
    ],
)
def test_an_arp_host_that_answers_something_is_kept(fields):
    engine = _engine_with("192.168.4.24", mac="aa:bb:cc:dd:ee:ff", **fields)
    assert engine._answered_for_itself(engine.results["192.168.4.24"])


def test_a_reverse_dns_name_is_not_the_device_answering():
    # The name server replied, not the equipment. A cached ARP entry plus a
    # stale PTR record must not add up to a discovered device.
    engine = _engine_with(
        "192.168.4.47", mac="aa:bb:cc:dd:ee:ff", hostname="old-laptop.lan"
    )
    assert not engine._answered_for_itself(engine.results["192.168.4.47"])


@pytest.mark.asyncio
async def test_finalize_drops_an_arp_host_that_answered_nothing():
    engine = _engine_with("192.168.4.47", mac="aa:bb:cc:dd:ee:ff")
    await engine._finalize_scan()
    assert "192.168.4.47" not in engine.results
    assert engine.scan_status.devices_found == 0


@pytest.mark.asyncio
async def test_finalize_keeps_an_arp_host_with_an_open_port():
    engine = _engine_with(
        "192.168.4.24", mac="aa:bb:cc:dd:ee:ff", open_ports=[10001]
    )
    await engine._finalize_scan()
    assert "192.168.4.24" in engine.results


@pytest.mark.asyncio
async def test_a_ping_answering_host_is_never_dropped_for_being_quiet():
    # Not provisional: it answered ICMP, so silence on every other channel
    # is just an unidentified device, which is a normal thing to report.
    engine = DiscoveryEngine()
    engine.results["192.168.4.20"] = DiscoveredDevice(ip="192.168.4.20")
    engine._arp_provisional = set()
    await engine._finalize_scan()
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
    engine._on_update = lambda msg: sent.append(msg) or asyncio.sleep(0)
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

    engine._on_update = capture
    await engine._finalize_scan()

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

    engine._on_update = capture
    await engine._finalize_scan()

    assert [m for m in sent if m.get("type") == "discovery.update"] == []
    assert "192.168.4.47" not in engine.results


@pytest.mark.asyncio
async def test_the_live_count_matches_what_the_panel_is_shown():
    # An unproven host is not on screen, so counting it would report more
    # devices than are visible and then tick DOWN when it is dropped.
    engine = DiscoveryEngine()
    engine._arp_provisional = set()

    engine._get_or_create("192.168.4.20")            # answered a ping
    assert engine.scan_status.devices_found == 1

    engine._get_or_create("192.168.4.47")            # ARP named it only
    engine._arp_provisional.add("192.168.4.47")
    engine._refresh_device_count()
    assert engine.scan_status.devices_found == 1

    await engine._finalize_scan()                    # it answered nothing
    assert engine.scan_status.devices_found == 1
    assert "192.168.4.47" not in engine.results


@pytest.mark.asyncio
async def test_a_host_that_earns_its_place_joins_the_count():
    engine = DiscoveryEngine()
    engine._arp_provisional = set()
    engine._get_or_create("192.168.4.24").open_ports = [10001]
    engine._arp_provisional.add("192.168.4.24")
    engine._refresh_device_count()
    assert engine.scan_status.devices_found == 0

    await engine._finalize_scan()
    assert engine.scan_status.devices_found == 1
