"""A reverse-address record names a host, never a service.

Devices announce their own name for each address they hold
(``9.0.77.10.in-addr.arpa. PTR widget.local.``). The listener used to take
that record down the service path, so the address showed up as an mDNS
"service", became an evidence record, and, when it arrived after a real
service, replaced that service's name and type. It is now the device's name
for itself: the device is still found, at the same address and with the same
name, and nothing else changes.
"""

from __future__ import annotations

import socket
import struct

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.mdns_scanner import (
    DNS_TYPE_A,
    DNS_TYPE_PTR,
    DNS_TYPE_SRV,
    MDNSScanner,
    encode_dns_name,
)
from openavc.discovery.result import DiscoveredDevice, merge_device_info

DEVICE_IP = "10.77.0.9"
VENDOR_TYPE = "_acmewidget._tcp.local."
INSTANCE = f"Widget 3000.{VENDOR_TYPE}"


def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return encode_dns_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _response(*records: bytes) -> bytes:
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


A_RECORD = _rr("widget.local.", DNS_TYPE_A, socket.inet_aton(DEVICE_IP))
REVERSE_V4 = _rr("9.0.77.10.in-addr.arpa.", DNS_TYPE_PTR, encode_dns_name("widget.local."))
SERVICE = (
    _rr(VENDOR_TYPE, DNS_TYPE_PTR, encode_dns_name(INSTANCE)),
    _rr(INSTANCE, DNS_TYPE_SRV, struct.pack("!HHH", 0, 0, 4999) + encode_dns_name("widget.local.")),
)


def _heard(*packets: bytes):
    scanner = MDNSScanner()
    for packet in packets:
        scanner._process_response(packet, DEVICE_IP)
    return scanner.results.get(DEVICE_IP)


class TestReverseAddressRecords:
    def test_a_device_that_only_names_itself_is_still_found_and_named(self):
        result = _heard(_response(REVERSE_V4, A_RECORD))
        assert result is not None
        assert result.service_list() == []
        assert result.to_evidence_records() == []
        assert result.service_type is None and result.instance_name is None
        info = result.to_device_info()
        assert info == {"device_name": "widget.local", "hostname": "widget"}
        assert "mdns_services" not in info

    def test_it_still_counts_as_the_device_answering(self):
        # The record came from the device itself, so it is not a cache ghost.
        device = DiscoveredDevice(ip=DEVICE_IP)
        merge_device_info(device, _heard(_response(REVERSE_V4, A_RECORD)).to_device_info(), "mdns")
        assert device.mdns_services == []
        assert DiscoveryEngine._answered_for_itself(device) is True

    def test_packet_order_no_longer_decides_the_name_or_the_service(self):
        orders = (
            (_response(*SERVICE, A_RECORD), _response(REVERSE_V4, A_RECORD)),
            (_response(REVERSE_V4, A_RECORD), _response(*SERVICE, A_RECORD)),
            (_response(REVERSE_V4, *SERVICE, A_RECORD),),
        )
        for packets in orders:
            result = _heard(*packets)
            # The single-service fields a plugin's mdns_browse reads.
            assert result.service_type == "_acmewidget._tcp.local"
            assert result.instance_name == "Widget 3000"
            info = result.to_device_info()
            assert info["device_name"] == "Widget 3000"
            assert info["mdns_services"] == ["_acmewidget._tcp.local"]
            assert [e.source for e in result.to_evidence_records()] == [
                "mdns:_acmewidget._tcp.local.",
            ]

    def test_the_first_name_heard_stands(self):
        other = _rr("9.0.77.10.in-addr.arpa.", DNS_TYPE_PTR, encode_dns_name("renamed.local."))
        result = _heard(_response(REVERSE_V4, A_RECORD), _response(other))
        assert result.address_name == "widget.local"

    def test_a_service_host_name_is_kept_over_the_address_name(self):
        other = _rr("9.0.77.10.in-addr.arpa.", DNS_TYPE_PTR, encode_dns_name("other-name.local."))
        result = _heard(_response(*SERVICE, A_RECORD, other))
        assert result.to_device_info()["hostname"] == "widget"
