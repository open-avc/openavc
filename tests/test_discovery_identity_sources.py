"""Which of a scan's sources a found device's maker, model and names are taken
from, when they say different things.

* A device identified through one of its mDNS services shows the model that
  service gave. A device that announces a product code on its control service
  and a board code on another reads the same model whichever was heard last,
  scan after scan, and a re-match to another driver reads the model of the
  service that identified it then.
* An mDNS listener that heard several services from one device takes each
  field from one service's own record, the same one whatever order they
  arrived in.
* Otherwise each field is taken from the most trusted source that gave it,
  never from whichever string is longer: the UPnP description, then the AMX
  beacon, then SNMP, then the network's own names for the address, then mDNS.
  The MAC address's registered maker, a driver probe's reply and the driver's
  category only fill a field nothing else gave, the first the scan reads
  filling it. The same source read again (a later scan) replaces its own
  value.
"""
from __future__ import annotations

import itertools
import socket
import struct

import pytest

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.mdns_scanner import (
    DNS_TYPE_A,
    DNS_TYPE_PTR,
    DNS_TYPE_SRV,
    DNS_TYPE_TXT,
    MDNSScanner,
    encode_dns_name,
)
from openavc.discovery.result import (
    DiscoveredDevice,
    device_info_from_evidence,
    merge_device_info,
)
from openavc.discovery.tier_matcher import evidence_active_probe

DEVICE_IP = "192.0.2.81"
PLAYER_TYPE = "_acme-player._tcp.local."
INFO_TYPE = "_acme-info._tcp.local."
SHARED_TYPE = "_acme-shared-ctl._tcp.local."

# A driver for one maker's product, identified by its own control service.
_PLAYER = {
    "id": "acme_player_sources",
    "name": "Acme Media Player",
    "manufacturer": "Acme",
    "category": "streaming",
    "transport": "tcp",
    "discovery": {"mdns": [PLAYER_TYPE]},
}

# A driver that knows the same device by its other service.
_INFO = {
    "id": "acme_info_sources",
    "name": "Acme Info Panel",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "discovery": {"mdns": [INFO_TYPE]},
}

# A driver for a protocol many makers' equipment speaks.
_SHARED = {
    "id": "acme_shared_sources",
    "name": "Shared Control Protocol Projector",
    "manufacturer": "Generic",
    "category": "projector",
    "transport": "tcp",
    "discovery": {"mdns": [{"service": SHARED_TYPE, "cross_vendor": True}]},
}


# --- mDNS packets ------------------------------------------------------------

def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return encode_dns_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _txt(*pairs: str) -> bytes:
    return b"".join(bytes([len(p.encode())]) + p.encode() for p in pairs)


def _service(service_type: str, port: int, *txt: str) -> bytes:
    """One service's whole answer: PTR, SRV, TXT and the host's A record."""
    full = f"Den.{service_type}"
    records = [
        _rr(service_type, DNS_TYPE_PTR, encode_dns_name(full)),
        _rr(full, DNS_TYPE_SRV, struct.pack("!HHH", 0, 0, port) + encode_dns_name("den.local.")),
        _rr("den.local.", DNS_TYPE_A, socket.inet_aton(DEVICE_IP)),
        _rr(full, DNS_TYPE_TXT, _txt(*txt)),
    ]
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


# The product code on the service that identifies the device, a board code on
# another; the board code's service sorts first.
PRODUCT = _service(PLAYER_TYPE, 7000, "model=AP-200")
BOARD = _service(INFO_TYPE, 7100, "model=Z123BRD")
BOTH_ORDERS = [(PRODUCT, BOARD), (BOARD, PRODUCT)]


def _heard(*packets: bytes):
    scanner = MDNSScanner()
    for packet in packets:
        scanner._process_response(packet, DEVICE_IP)
    return scanner.results[DEVICE_IP]


def _engine(*drivers) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.load_driver_hints_from_registry([dict(d) for d in drivers])
    return engine


async def _scan(engine: DiscoveryEngine, *packets: bytes) -> dict:
    """One scan's mDNS merge and match for the device, as a scan runs them;
    the record every exit sends for it."""
    await engine._merge_passive_source(
        "mdns", {DEVICE_IP: _heard(*packets)}, engine._add_mdns_evidence,
    )
    engine.rematch()
    return next(d for d in engine.get_results() if d["ip"] == DEVICE_IP)


# --- the model of a device identified through one of its mDNS services -------

class TestTheIdentifyingServicesModel:
    @pytest.mark.parametrize("packets", BOTH_ORDERS)
    async def test_an_identified_device_shows_the_model_its_identifying_service_gave(
        self, packets,
    ):
        record = await _scan(_engine(_PLAYER), *packets)
        assert record["identification"]["driver_id"] == "acme_player_sources"
        assert record["model"] == "AP-200"

    async def test_the_model_stays_the_same_in_two_scans_running(self):
        engine = _engine(_PLAYER)
        first = await _scan(engine, PRODUCT, BOARD)
        engine._reset_for_scan(*engine._scan_policy(None))
        second = await _scan(engine, BOARD, PRODUCT)
        assert first["model"] == second["model"] == "AP-200"
        assert first["name"] == second["name"] == "Acme Media Player"

    async def test_a_re_match_to_another_driver_reads_its_own_services_model(self):
        engine = _engine(_PLAYER)
        assert (await _scan(engine, PRODUCT, BOARD))["model"] == "AP-200"
        engine.load_driver_hints_from_registry([dict(_INFO)])
        engine.rematch()
        record = next(d for d in engine.get_results() if d["ip"] == DEVICE_IP)
        assert record["identification"]["driver_id"] == "acme_info_sources"
        assert record["model"] == "Z123BRD"

    @pytest.mark.parametrize("packets", [
        (_service(SHARED_TYPE, 4352, "model=PX-1"), BOARD),
        (BOARD, _service(SHARED_TYPE, 4352, "model=PX-1")),
    ])
    async def test_a_shared_protocol_device_is_headed_by_the_model_that_identified_it(
        self, packets,
    ):
        record = await _scan(_engine(_SHARED), *packets)
        assert record["model"] == "PX-1"
        assert record["name"] == "PX-1"


class TestTheListenersOwnPick:
    def test_the_model_is_the_same_whichever_service_was_heard_last(self):
        first, second = (_heard(*p).to_device_info() for p in BOTH_ORDERS)
        assert first["model"] == second["model"]

    def test_a_txt_alias_is_read_on_the_service_that_carries_it(self):
        info = _heard(_service(PLAYER_TYPE, 7000, "md=AP-300", "mf=Acme")).to_device_info()
        assert info["model"] == "AP-300"
        assert info["manufacturer"] == "Acme"


# --- each field from its most trusted source ----------------------------------

# What each source says about one device. Each source's strings are the
# longest for a field where it is NOT the most trusted, so a length rule fails.
_SAID = [
    ("mdns", {"model": "Acme AP-200 Board Revision Seven", "manufacturer": "Acme Incorporated Ltd",
              "device_name": "Den (Living Room) Player", "hostname": "den-player-board-revision-seven"}),
    ("ssdp", {"model": "AP-200", "device_name": "Lobby Display"}),
    ("snmp", {"model": "AP-200 Series Rev B", "manufacturer": "Acme", "device_name": "LOBBY-SNMP-SYSNAME-01"}),
    ("arp", {"hostname": "lobby.example.edu", "device_name": "LOBBY1", "mac": "02:00:00:00:00:81"}),
    ("probe", {"model": "AP-200 (reply text, longest of all the model strings)",
               "manufacturer": "Acme Corporation International Holdings"}),
    ("oui", {"manufacturer": "Network Card Maker Incorporated Worldwide", "category": "networking"}),
    ("driver_category", {"category": "streaming-and-everything-else"}),
]


class TestTrust:
    @pytest.mark.parametrize("order", list(itertools.permutations(range(len(_SAID))))[::97])
    def test_each_field_comes_from_its_most_trusted_source_whatever_the_order(self, order):
        device = DiscoveredDevice(ip=DEVICE_IP)
        for i in order:
            source, info = _SAID[i]
            merge_device_info(device, dict(info), source)
        assert device.model == "AP-200"                      # the UPnP description
        assert device.manufacturer == "Acme"                 # SNMP over mDNS and the probe
        assert device.device_name == "Lobby Display"         # the UPnP description
        assert device.hostname == "lobby.example.edu"        # reverse DNS over mDNS
        assert device.mac == "02:00:00:00:00:81"
        assert device.category == "networking"               # the MAC registry, then the driver

    @pytest.mark.parametrize("first,then", [("ssdp", "snmp"), ("snmp", "ssdp")])
    def test_a_upnp_model_is_kept_over_a_longer_snmp_one(self, first, then):
        said = {"ssdp": "WP-3000", "snmp": "WP-3000 Series Rev B"}
        device = DiscoveredDevice(ip=DEVICE_IP)
        merge_device_info(device, {"model": said[first]}, first)
        merge_device_info(device, {"model": said[then]}, then)
        assert device.model == "WP-3000"

    def test_the_same_source_read_again_replaces_its_own_value(self):
        device = DiscoveredDevice(ip=DEVICE_IP)
        merge_device_info(device, {"hostname": "projector-b.example.edu"}, "arp")
        merge_device_info(device, {"hostname": "tv1"}, "arp")
        assert device.hostname == "tv1"

    def test_a_more_trusted_value_outlasts_a_scan_that_did_not_hear_its_source(self):
        engine = DiscoveryEngine()
        device = engine._get_or_create(DEVICE_IP)
        merge_device_info(device, {"model": "WP-3000"}, "ssdp")
        engine._reset_for_scan(*engine._scan_policy(None))
        merge_device_info(device, {"model": "WP-3000 (mDNS board name)"}, "mdns")
        assert device.model == "WP-3000"

    @pytest.mark.parametrize("order", [("oui", "probe", "driver_category"),
                                       ("driver_category", "probe", "oui")])
    def test_the_sources_that_only_fill_keep_the_order_the_scan_reads_them(self, order):
        said = {"oui": "networking", "probe": "projector", "driver_category": "display"}
        device = DiscoveredDevice(ip=DEVICE_IP)
        for source in order:
            merge_device_info(device, {"category": said[source]}, source)
        assert device.category == "networking"

    def test_a_fill_only_source_never_replaces_a_value_of_unknown_source(self):
        device = DiscoveredDevice(ip=DEVICE_IP, manufacturer="Acme")
        for source in ("probe", "oui", "driver_category"):
            merge_device_info(device, {"manufacturer": "Other Vendor Corporation"}, source)
        assert device.manufacturer == "Acme"

    def test_a_source_nobody_ranked_is_refused(self):
        with pytest.raises(ValueError):
            merge_device_info(DiscoveredDevice(ip=DEVICE_IP), {"model": "AP-200"}, "rumour")

    def test_the_trust_record_is_not_sent(self):
        device = DiscoveredDevice(ip=DEVICE_IP)
        merge_device_info(device, {"model": "AP-200"}, "ssdp")
        assert set(device.to_dict()) == set(DiscoveredDevice(ip=DEVICE_IP).to_dict())


class TestAProbeReply:
    def test_a_maker_the_drivers_own_rule_supplied_is_not_read_as_the_devices(self):
        ev = evidence_active_probe(
            "acme_probe", {"manufacturer": "Acme", "extracted": {"model": "AP-200"}},
            driver_supplied=["manufacturer"],
        )
        assert device_info_from_evidence(ev) == {"model": "AP-200"}
