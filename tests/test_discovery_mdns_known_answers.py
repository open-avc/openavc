"""A device's mDNS query never puts another device's name on its card.

A device browsing for a service type lists the instances it has already heard
in the query's answer section (known-answer suppression), so a controller
asking for the widget's type carries the widget's own records. The listener
read those as the controller announcing them: its card got the widget's name
and service.
"""

from __future__ import annotations

import struct

from openavc.discovery.mdns_scanner import DNS_TYPE_PTR, MDNSScanner, encode_dns_name

WIDGET_IP = "10.77.0.9"
CONTROLLER_IP = "10.77.0.20"
WIDGET_TYPE = "_acmewidget._tcp.local."


def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return encode_dns_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _query_with_known_answer() -> bytes:
    """The controller asks for the widget's type, listing the widget it already knows."""
    question = encode_dns_name(WIDGET_TYPE) + struct.pack("!HH", DNS_TYPE_PTR, 1)
    known = _rr(WIDGET_TYPE, DNS_TYPE_PTR, encode_dns_name(f"Widget 3000.{WIDGET_TYPE}"))
    return struct.pack("!HHHHHH", 0, 0x0000, 1, 1, 0, 0) + question + known


def test_a_query_files_nothing_under_the_device_that_asked():
    scanner = MDNSScanner()
    scanner._process_response(_query_with_known_answer(), CONTROLLER_IP)
    assert CONTROLLER_IP not in scanner.results


def test_a_query_is_not_heard_as_an_announcement():
    heard = []
    scanner = MDNSScanner(on_message=lambda ip, detail: heard.append(ip))
    scanner._process_response(_query_with_known_answer(), CONTROLLER_IP)
    assert heard == []
