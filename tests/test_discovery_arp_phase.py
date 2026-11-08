"""Phase 4 of a Discovery scan, pinned: what the ARP table, reverse DNS and
NetBIOS put on each device, the second look at addresses only the ARP table
named, and the audit's network check reading names and a MAC address the same
way a scan does.

Everything that would touch the network is replaced: the ARP table read, the
reverse-DNS and NetBIOS lookups, and each ping. The addresses are synthetic
and the vendor is invented (Acme, prefix 02:ac:3e).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from openavc.audit.footprint import NetworkCheck
from openavc.discovery import icmp
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.result import DiscoveredDevice
from openavc.discovery.scan_budget import ArpPlan
from openavc.discovery.tier_matcher import KIND_HOSTNAME, KIND_OUI

# Where phase 4 looks up the ARP table, the names and its own ceilings.
PHASE = "openavc.discovery.engine"

SUBNET = "10.20.0.0/24"
ACME_PREFIX = "02:ac:3e"
ACME_MAC = "02:ac:3e:00:00:05"
OTHER_MAC = "06:00:00:00:00:09"


def _engine(*, subnets=(SUBNET,), ping_method=icmp.METHOD_DGRAM) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.oui_db.add_prefix(ACME_PREFIX, "Acme", "display")
    engine.scan_status.subnets = list(subnets)
    engine._ping_method = ping_method
    engine._scan_source_ip = "10.20.0.2"
    return engine


def _alive(engine: DiscoveryEngine, *ips: str) -> list[str]:
    for ip in ips:
        engine._get_or_create(ip).alive = True
    return list(ips)


def _plan(name_lookup_hosts=None, netbios=True) -> ArpPlan:
    return ArpPlan(name_lookup_hosts=name_lookup_hosts, netbios=netbios, budget=30.0)


def _kinds(device: DiscoveredDevice) -> list[str]:
    return [ev.data.get("kind") for ev in device.evidence_log]


def _hostname_values(device: DiscoveredDevice) -> list[str]:
    return [ev.data["value"] for ev in device.evidence_log if ev.data.get("kind") == KIND_HOSTNAME]


async def _run(
    engine: DiscoveryEngine,
    alive: list[str],
    *,
    arp: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
    netbios=None,
    plan: ArpPlan | None = None,
    answers: set[str] = frozenset(),
):
    """Run phase 4; return (announced ips, the reverse-DNS mock, the NetBIOS
    mock, the pinged ips)."""
    announced: list[tuple[str, str]] = []
    pinged: list[tuple] = []

    async def record(device, phase):
        announced.append((device.ip, phase))

    async def ping(ip, timeout=1.0, source_ip="", method=""):
        pinged.append((ip, timeout, source_ip, method))
        return icmp.RESULT_ALIVE if ip in answers else icmp.RESULT_TIMEOUT

    resolve = AsyncMock(return_value=dict(names or {}))
    if isinstance(netbios, BaseException):
        nbt = AsyncMock(side_effect=netbios)
    else:
        nbt = AsyncMock(return_value=dict(netbios or {}))
    with patch(f"{PHASE}.harvest_arp_table", AsyncMock(return_value=dict(arp or {}))), \
         patch(f"{PHASE}._resolve_hostnames", resolve), \
         patch(f"{PHASE}.netbios_sweep", nbt), \
         patch.object(icmp, "ping_host", side_effect=ping), \
         patch.object(engine, "_emit_device_update", side_effect=record):
        await engine._arp_phase(alive, plan or _plan())
    return announced, resolve, nbt, pinged


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


async def test_reverse_dns_names_the_host():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    announced, *_ = await _run(engine, alive, names={"10.20.0.5": "widget-1.example"})

    device = engine.results["10.20.0.5"]
    assert device.hostname == "widget-1.example"
    assert device.device_name is None
    assert _hostname_values(device) == ["widget-1.example"]
    assert announced == [("10.20.0.5", "arp_harvest")]


async def test_a_netbios_name_is_the_device_name_and_the_host_name_when_dns_has_none():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    await _run(engine, alive, netbios={"10.20.0.5": {"hostname": "WIDGET1"}})

    device = engine.results["10.20.0.5"]
    assert device.hostname == "WIDGET1"
    assert device.device_name == "WIDGET1"
    assert _hostname_values(device) == ["WIDGET1"]


async def test_reverse_dns_stays_the_host_name_beside_a_netbios_name():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    await _run(
        engine, alive,
        names={"10.20.0.5": "widget-1.example"},
        netbios={"10.20.0.5": {"hostname": "WIDGET1"}},
    )

    device = engine.results["10.20.0.5"]
    assert device.hostname == "widget-1.example"
    assert device.device_name == "WIDGET1"
    assert _hostname_values(device) == ["widget-1.example"]


async def test_a_netbios_answer_with_no_name_adds_nothing():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    announced, *_ = await _run(engine, alive, netbios={"10.20.0.5": {"mac": OTHER_MAC}})

    device = engine.results["10.20.0.5"]
    assert device.hostname is None and device.device_name is None and device.mac is None
    assert device.evidence_log == []
    assert announced == []


async def test_a_failed_netbios_sweep_keeps_the_reverse_dns_names():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    await _run(
        engine, alive,
        names={"10.20.0.5": "widget-1.example"},
        netbios=OSError("no route"),
    )

    assert engine.results["10.20.0.5"].hostname == "widget-1.example"


async def test_netbios_is_not_asked_when_the_plan_leaves_it_out():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    _, resolve, nbt, _ = await _run(
        engine, alive, names={"10.20.0.5": "widget-1.example"}, plan=_plan(netbios=False),
    )

    nbt.assert_not_called()
    resolve.assert_awaited_once_with(["10.20.0.5"])
    assert engine.results["10.20.0.5"].hostname == "widget-1.example"


async def test_names_are_looked_up_for_the_hosts_the_plan_allows_and_macs_for_all():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5", "10.20.0.6", "10.20.0.7")
    _, resolve, nbt, _ = await _run(
        engine, alive,
        arp={"10.20.0.7": ACME_MAC},
        plan=_plan(name_lookup_hosts=2),
    )

    resolve.assert_awaited_once_with(["10.20.0.5", "10.20.0.6"])
    nbt.assert_awaited_once_with(
        ["10.20.0.5", "10.20.0.6"], concurrency=30, timeout=1.0, source_ip="10.20.0.2",
    )
    assert engine.results["10.20.0.7"].mac == ACME_MAC


async def test_with_no_host_to_name_netbios_is_not_asked():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    _, resolve, nbt, _ = await _run(engine, alive, plan=_plan(name_lookup_hosts=0))

    resolve.assert_awaited_once_with([])
    nbt.assert_not_called()


# ---------------------------------------------------------------------------
# MAC address and the card maker
# ---------------------------------------------------------------------------


async def test_a_known_prefix_names_the_card_maker_after_the_name_evidence():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    announced, *_ = await _run(
        engine, alive, arp={"10.20.0.5": ACME_MAC}, names={"10.20.0.5": "widget-1.example"},
    )

    device = engine.results["10.20.0.5"]
    assert (device.mac, device.manufacturer, device.category) == (ACME_MAC, "Acme", "display")
    assert _kinds(device) == [KIND_HOSTNAME, KIND_OUI]
    [oui] = [ev for ev in device.evidence_log if ev.data.get("kind") == KIND_OUI]
    assert oui.data["vendor"] == "Acme"
    assert announced == [("10.20.0.5", "arp_harvest")]


async def test_an_unknown_prefix_records_the_mac_alone():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    await _run(engine, alive, arp={"10.20.0.5": OTHER_MAC})

    device = engine.results["10.20.0.5"]
    assert device.mac == OTHER_MAC
    assert device.manufacturer is None and device.category is None
    assert _kinds(device) == [KIND_OUI]
    assert device.evidence_log[0].data["vendor"] is None


async def test_the_card_maker_never_replaces_a_manufacturer_already_known():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    engine.results["10.20.0.5"].manufacturer = "AW"
    engine.results["10.20.0.5"].category = "audio"
    await _run(engine, alive, arp={"10.20.0.5": ACME_MAC})

    device = engine.results["10.20.0.5"]
    assert (device.manufacturer, device.category) == ("AW", "audio")
    assert device.mac == ACME_MAC


async def test_a_longer_name_from_this_scan_replaces_the_last_ones():
    # The scan's own reads keep the standard merge rule, so a re-scan can
    # refresh a renamed host; only the card maker is fill-only.
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    engine.results["10.20.0.5"].hostname = "w1"
    engine.results["10.20.0.5"].device_name = "W"
    await _run(
        engine, alive,
        names={"10.20.0.5": "widget-1.example"},
        netbios={"10.20.0.5": {"hostname": "WIDGET1"}},
    )

    device = engine.results["10.20.0.5"]
    assert (device.hostname, device.device_name) == ("widget-1.example", "WIDGET1")


async def test_a_host_nothing_was_learned_about_is_not_announced_again():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5", "10.20.0.6")
    announced, *_ = await _run(engine, alive, names={"10.20.0.5": "widget-1.example"})

    assert announced == [("10.20.0.5", "arp_harvest")]
    assert engine.results["10.20.0.6"].evidence_log == []


# ---------------------------------------------------------------------------
# The second look at addresses only the ARP table named
# ---------------------------------------------------------------------------


async def test_an_arp_only_address_that_answers_a_second_ping_is_a_proven_host():
    engine = _engine()
    alive: list[str] = []
    announced, _, _, pinged = await _run(
        engine, alive, arp={"10.20.0.9": OTHER_MAC}, answers={"10.20.0.9"},
    )

    device = engine.results["10.20.0.9"]
    assert device.alive is True
    assert "10.20.0.9" not in engine._arp_provisional
    assert alive == ["10.20.0.9"]
    assert engine.scan_status.devices_found == 1
    assert pinged[0] == ("10.20.0.9", 1.5, "10.20.0.2", icmp.METHOD_DGRAM)
    # Proven, so its MAC is read and it is announced like any host.
    assert device.mac == OTHER_MAC
    assert announced == [("10.20.0.9", "arp_harvest")]


async def test_an_arp_only_address_that_stays_quiet_is_carried_unproven():
    engine = _engine()
    alive: list[str] = []
    _, _, _, pinged = await _run(engine, alive, arp={"10.20.0.9": OTHER_MAC})

    device = engine.results["10.20.0.9"]
    assert device.alive is True
    assert engine._arp_provisional == {"10.20.0.9"}
    assert alive == ["10.20.0.9"]
    assert engine.scan_status.devices_found == 0
    assert [p[0] for p in pinged] == ["10.20.0.9", "10.20.0.9"]
    # Its MAC is still read: the port scan decides whether it earns a place.
    assert device.mac == OTHER_MAC


async def test_proven_and_unproven_addresses_both_join_the_hosts_after_the_swept_ones():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    await _run(
        engine, alive,
        arp={"10.20.0.9": OTHER_MAC, "10.20.0.8": OTHER_MAC},
        answers={"10.20.0.8"},
    )

    assert alive == ["10.20.0.5", "10.20.0.9", "10.20.0.8"]
    assert engine._arp_provisional == {"10.20.0.9"}
    assert engine.scan_status.devices_found == 2


async def test_the_second_look_leaves_out_a_subnets_network_and_broadcast_addresses():
    engine = _engine()
    alive: list[str] = []
    await _run(
        engine, alive,
        arp={"10.20.0.0": OTHER_MAC, "10.20.0.255": OTHER_MAC, "10.20.0.9": OTHER_MAC},
    )

    assert alive == ["10.20.0.9"]
    assert set(engine.results) == {"10.20.0.9"}


async def test_a_31_and_a_32_have_no_edges_to_leave_out():
    engine = _engine(subnets=("10.30.0.0/31", "10.40.0.7/32"))
    alive: list[str] = []
    await _run(
        engine, alive,
        arp={"10.30.0.0": OTHER_MAC, "10.30.0.1": OTHER_MAC, "10.40.0.7": OTHER_MAC},
    )

    assert alive == ["10.30.0.0", "10.30.0.1", "10.40.0.7"]


async def test_a_malformed_subnet_is_passed_over():
    engine = _engine(subnets=("not-a-subnet", SUBNET))
    alive: list[str] = []
    await _run(engine, alive, arp={"10.20.0.9": OTHER_MAC})

    assert alive == ["10.20.0.9"]


async def test_the_second_look_is_only_for_new_addresses_inside_the_scan():
    engine = _engine()
    alive = _alive(engine, "10.20.0.5")
    _, _, _, pinged = await _run(
        engine, alive, arp={"10.20.0.5": ACME_MAC, "10.99.0.5": OTHER_MAC},
    )

    assert pinged == []
    assert alive == ["10.20.0.5"]
    assert "10.99.0.5" not in engine.results
    assert engine._arp_provisional == set()


@pytest.mark.parametrize("method", ["", icmp.METHOD_NONE])
async def test_with_no_way_to_ping_the_arp_only_addresses_are_left_out(method):
    engine = _engine(ping_method=method)
    alive: list[str] = []
    _, _, _, pinged = await _run(engine, alive, arp={"10.20.0.9": OTHER_MAC})

    assert pinged == []
    assert alive == []
    assert "10.20.0.9" not in engine.results
    assert engine._arp_provisional == set()


async def test_the_second_look_pings_no_more_than_its_ceiling():
    engine = _engine()
    alive: list[str] = []
    arp = {f"10.20.0.{n}": OTHER_MAC for n in (11, 12, 13, 14)}
    with patch(f"{PHASE}._REPING_MAX_HOSTS", 2):
        _, _, _, pinged = await _run(engine, alive, arp=arp, answers={"10.20.0.11"})

    assert {p[0] for p in pinged} == {"10.20.0.11", "10.20.0.12"}
    # The rest carry on unproven, like a host that did not answer.
    assert alive == ["10.20.0.11", "10.20.0.12", "10.20.0.13", "10.20.0.14"]
    assert engine._arp_provisional == {"10.20.0.12", "10.20.0.13", "10.20.0.14"}


async def test_the_second_look_stops_at_its_time_limit_and_keeps_who_answered():
    engine = _engine()
    alive: list[str] = []

    async def ping(ip, timeout=1.0, source_ip="", method=""):
        if ip == "10.20.0.9":
            return icmp.RESULT_ALIVE
        await asyncio.sleep(30)
        return icmp.RESULT_TIMEOUT

    began = time.monotonic()
    with patch(f"{PHASE}._REPING_MAX_SECONDS", 0.2), \
         patch(f"{PHASE}.harvest_arp_table",
               AsyncMock(return_value={"10.20.0.9": OTHER_MAC, "10.20.0.10": OTHER_MAC})), \
         patch(f"{PHASE}._resolve_hostnames", AsyncMock(return_value={})), \
         patch(f"{PHASE}.netbios_sweep", AsyncMock(return_value={})), \
         patch.object(icmp, "ping_host", side_effect=ping):
        await engine._arp_phase(alive, _plan())

    assert time.monotonic() - began < 5
    assert alive == ["10.20.0.9", "10.20.0.10"]
    assert engine._arp_provisional == {"10.20.0.10"}
    assert "10.20.0.9" not in engine._arp_provisional


# ---------------------------------------------------------------------------
# The audit's network check reads names and a MAC address the same way
# ---------------------------------------------------------------------------


def _audit_check(*, reverse_dns=None, netbios=None, mac=None) -> NetworkCheck:
    engine = _engine()
    engine.load_driver_hints_from_registry([])
    engine.oui_db.add_prefix(ACME_PREFIX, "Acme", "display")
    check = NetworkCheck(engine, "10.20.0.5", "10.20.0.5", listeners=(None, None, None))
    check._mdns = check._ssdp = check._amx = None
    check._snmp_info = None
    fp = check.footprint
    fp.reverse_dns = reverse_dns
    fp.netbios = netbios
    fp.mac = mac
    return check


def test_the_audit_takes_a_netbios_name_as_the_host_name_when_dns_has_none():
    check = _audit_check(netbios={"hostname": "WIDGET1"})
    check._build_evidence()

    device = check.footprint.device
    assert (device.hostname, device.device_name) == ("WIDGET1", "WIDGET1")
    assert _hostname_values(device) == ["WIDGET1"]


def test_the_audit_keeps_reverse_dns_as_the_host_name_beside_netbios():
    check = _audit_check(reverse_dns="widget-1.example", netbios={"hostname": "WIDGET1"})
    check._build_evidence()

    device = check.footprint.device
    assert (device.hostname, device.device_name) == ("widget-1.example", "WIDGET1")
    assert _hostname_values(device) == ["widget-1.example"]


def test_the_audit_names_the_card_maker_after_the_name_evidence():
    check = _audit_check(reverse_dns="widget-1.example", mac=ACME_MAC)
    check._build_evidence()

    device = check.footprint.device
    assert (device.mac, device.manufacturer, device.category) == (ACME_MAC, "Acme", "display")
    assert _kinds(device)[:2] == [KIND_HOSTNAME, KIND_OUI]
    assert check.footprint.evidence is device.evidence_log


def test_the_audit_with_no_name_or_mac_records_neither():
    check = _audit_check(netbios={"mac": OTHER_MAC})
    check._build_evidence()

    device = check.footprint.device
    assert device.hostname is None and device.device_name is None and device.mac is None
    assert KIND_HOSTNAME not in _kinds(device) and KIND_OUI not in _kinds(device)
