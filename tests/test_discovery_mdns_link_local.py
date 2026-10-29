"""A device that lists a link-local address beside its real one is found once, at the real one.

A device on DHCP can answer for its name with two A records, its link-local
(169.254) address and its DHCP one, from its DHCP address. The listener kept
one address per name, whichever A record came last, so the device's services
were filed under the link-local address. The scan then showed it twice: a
card at 169.254 with its name and services, and a card at its real address
with its MAC and serial and none of its services. The records now go where
the device can be reached, and a link-local address holds them only when the
device has no other.
"""

from __future__ import annotations

import socket
import struct

import pytest

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.mdns_scanner import (
    DNS_TYPE_A,
    DNS_TYPE_PTR,
    DNS_TYPE_SRV,
    DNS_TYPE_TXT,
    MAX_ADDRESSES_PER_HOSTNAME,
    MDNSScanner,
    encode_dns_name,
)

DEVICE_IP = "10.77.0.9"
LINK_LOCAL = "169.254.12.34"
OTHER_IP = "10.77.0.10"
VENDOR_TYPE = "_acmewidget._tcp.local."
HTTP_TYPE = "_http._tcp.local."
HOST = "widget-3000.local."


def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return encode_dns_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _response(*records: bytes) -> bytes:
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def _a(ip: str, host: str = HOST) -> bytes:
    return _rr(host, DNS_TYPE_A, socket.inet_aton(ip))


def _service(service_type: str = VENDOR_TYPE, port: int = 4999) -> list[bytes]:
    full = f"Widget 3000.{service_type}"
    return [
        _rr(service_type, DNS_TYPE_PTR, encode_dns_name(full)),
        _rr(full, DNS_TYPE_SRV, struct.pack("!HHH", 0, 0, port) + encode_dns_name(HOST)),
        _rr(full, DNS_TYPE_TXT, bytes([7]) + b"md=3000"),
    ]


def _heard(*packets: tuple[bytes, str]) -> MDNSScanner:
    scanner = MDNSScanner()
    for packet, sender in packets:
        scanner._process_response(packet, sender)
    return scanner


class TestTheRecordsGoWhereTheDeviceIsReached:
    def test_link_local_listed_last(self):
        """The case seen on a real camera: real address first, link-local last."""
        packet = _response(*_service(), _a(DEVICE_IP), _a(LINK_LOCAL))
        results = _heard((packet, DEVICE_IP)).results
        assert list(results) == [DEVICE_IP]
        assert results[DEVICE_IP].hostname == "widget-3000"
        assert [s.service_type for s in results[DEVICE_IP].service_list()] == ["_acmewidget._tcp.local"]

    def test_link_local_listed_first(self):
        packet = _response(*_service(), _a(LINK_LOCAL), _a(DEVICE_IP))
        assert list(_heard((packet, DEVICE_IP)).results) == [DEVICE_IP]

    def test_the_routable_address_wins_when_the_sender_is_not_listed(self):
        """A host answering for another (a sleep proxy) still files it at the name's address."""
        packet = _response(*_service(), _a(DEVICE_IP), _a(LINK_LOCAL))
        assert list(_heard((packet, OTHER_IP)).results) == [DEVICE_IP]

    def test_the_sender_wins_among_routable_addresses(self):
        packet = _response(*_service(), _a(DEVICE_IP), _a(OTHER_IP))
        assert list(_heard((packet, DEVICE_IP)).results) == [DEVICE_IP]

    def test_a_routable_sender_wins_over_a_name_that_lists_only_link_local(self):
        packet = _response(*_service(), _a(LINK_LOCAL))
        assert list(_heard((packet, DEVICE_IP)).results) == [DEVICE_IP]

    def test_a_device_only_on_link_local_is_still_found_there(self):
        packet = _response(*_service(), _a(LINK_LOCAL))
        assert list(_heard((packet, LINK_LOCAL)).results) == [LINK_LOCAL]

    def test_a_name_with_no_address_still_falls_back_to_the_sender(self):
        packet = _response(*_service())
        assert list(_heard((packet, DEVICE_IP)).results) == [DEVICE_IP]


class TestALaterRoutableAddressMovesTheResult:
    def test_a_result_filed_at_link_local_moves_when_the_real_address_is_heard(self):
        first = _response(*_service(), _a(LINK_LOCAL))
        later = _response(_a(DEVICE_IP))
        results = _heard((first, LINK_LOCAL), (later, DEVICE_IP)).results
        assert list(results) == [DEVICE_IP]
        assert results[DEVICE_IP].ip == DEVICE_IP
        assert results[DEVICE_IP].hostname == "widget-3000"

    def test_it_merges_with_a_result_already_at_the_real_address(self):
        first = _response(*_service(VENDOR_TYPE, 4999), _a(LINK_LOCAL))
        later = _response(*_service(HTTP_TYPE, 80), _a(DEVICE_IP))
        results = _heard((first, LINK_LOCAL), (later, DEVICE_IP)).results
        assert list(results) == [DEVICE_IP]
        types = sorted(s.service_type for s in results[DEVICE_IP].service_list())
        assert types == ["_acmewidget._tcp.local", "_http._tcp.local"]
        assert results[DEVICE_IP].txt_records == {"md": "3000"}

    def test_a_link_local_result_for_another_name_stays(self):
        stranger = _response(*_service(), _a(LINK_LOCAL))
        other_name = _response(_a(DEVICE_IP, host="other.local."), _a(LINK_LOCAL, host="other.local."))
        results = _heard((stranger, LINK_LOCAL), (other_name, DEVICE_IP)).results
        assert LINK_LOCAL in results
        assert results[LINK_LOCAL].hostname == "widget-3000"


class TestBounded:
    def test_addresses_per_name_are_capped(self):
        scanner = MDNSScanner()
        for n in range(MAX_ADDRESSES_PER_HOSTNAME + 5):
            scanner._process_response(_response(_a(f"10.77.1.{n + 1}")), DEVICE_IP)
        assert len(scanner._hostname_to_ips["widget-3000.local"]) == MAX_ADDRESSES_PER_HOSTNAME

    def test_an_address_heard_again_is_the_most_recent(self):
        scanner = _heard(
            (_response(_a(DEVICE_IP)), DEVICE_IP),
            (_response(_a(OTHER_IP)), OTHER_IP),
            (_response(_a(DEVICE_IP)), DEVICE_IP),
        )
        assert scanner._address_for(HOST) == DEVICE_IP


@pytest.mark.asyncio
async def test_the_scan_shows_one_device_at_the_real_address():
    """Through the engine's merge: the device found by ARP at its real address
    carries the mDNS services, and no card appears at the link-local address."""
    scanner = _heard((_response(*_service(), _a(DEVICE_IP), _a(LINK_LOCAL)), DEVICE_IP))

    class _Silent:
        results: dict = {}

    engine = DiscoveryEngine()
    engine._get_or_create(DEVICE_IP).mac = "00:11:22:33:44:55"
    engine._passive_scanners = (scanner, _Silent(), _Silent())
    await engine.merge_passive_results()
    assert list(engine.results) == [DEVICE_IP]
    device = engine.results[DEVICE_IP]
    assert device.mac == "00:11:22:33:44:55"
    assert device.hostname == "widget-3000"
    assert any(e.source == "mdns:_acmewidget._tcp.local." for e in device.evidence_log)
