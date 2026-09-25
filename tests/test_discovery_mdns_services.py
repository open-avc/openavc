"""mDNS keeps every service a device advertises, and a device that drops
ping but advertises a service still gets the rest of its ports scanned.

A device commonly advertises a generic type (``_http._tcp``) beside the vendor
type a driver claims. The listener used to keep one service type per device,
whichever resolved last, so packet order decided whether the device could be
identified. And an mDNS SRV port landed in ``open_ports``, which the phase-7
follow-up read as "already port-scanned", so the device's other ports (and the
probes that need them) were never tried.
"""

from __future__ import annotations

import socket
import struct
from unittest.mock import AsyncMock, MagicMock, patch

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.hints import build_signal_index, parse_driver_discovery
from openavc.discovery.mdns_scanner import (
    DNS_SD_META_QUERY,
    DNS_TYPE_A,
    DNS_TYPE_PTR,
    DNS_TYPE_SRV,
    DNS_TYPE_TXT,
    MDNSResult,
    MDNSScanner,
    MDNSService,
    encode_dns_name,
)
from openavc.discovery.result import DeviceState, Evidence, SignalTier
from openavc.discovery.scan_budget import ScanBudget, resolve_policy
from openavc.discovery.tier_matcher import TierMatcher

DEVICE_IP = "10.77.0.9"
VENDOR_TYPE = "_acmewidget._tcp.local."


# ---------------------------------------------------------------------------
# Packet builders
# ---------------------------------------------------------------------------

def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return encode_dns_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _txt(*pairs: str) -> bytes:
    out = b""
    for p in pairs:
        raw = p.encode()
        out += bytes([len(raw)]) + raw
    return out


def _response(*records: bytes) -> bytes:
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def _service_packet(service_type: str, instance: str, port: int, *txt: str) -> bytes:
    """One service's full answer: PTR, SRV, TXT and the host's A record."""
    full = f"{instance}.{service_type}"
    records = [
        _rr(service_type, DNS_TYPE_PTR, encode_dns_name(full)),
        _rr(full, DNS_TYPE_SRV, struct.pack("!HHH", 0, 0, port) + encode_dns_name("widget.local.")),
        _rr("widget.local.", DNS_TYPE_A, socket.inet_aton(DEVICE_IP)),
    ]
    if txt:
        records.append(_rr(full, DNS_TYPE_TXT, _txt(*txt)))
    return _response(*records)


HTTP_PACKET = _service_packet("_http._tcp.local.", "Widget Web", 80, "path=/")
VENDOR_PACKET = _service_packet(VENDOR_TYPE, "Widget 3000", 4999, "md=Widget 3000")


def _acme_index():
    hint = parse_driver_discovery({
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "tcp",
        "discovery": {"mdns": VENDOR_TYPE},
    })
    return build_signal_index([hint])


def _heard(*packets: bytes) -> MDNSResult:
    scanner = MDNSScanner()
    for packet in packets:
        scanner._process_response(packet, DEVICE_IP)
    return scanner.results[DEVICE_IP]


# ---------------------------------------------------------------------------
# D1: every service survives, in any order
# ---------------------------------------------------------------------------

class TestEveryServiceIsKept:
    def test_both_services_held_whichever_resolves_last(self):
        for order in ((HTTP_PACKET, VENDOR_PACKET), (VENDOR_PACKET, HTTP_PACKET)):
            result = _heard(*order)
            types = {svc.service_type for svc in result.service_list()}
            assert types == {"_http._tcp.local", "_acmewidget._tcp.local"}

    def test_identifies_by_the_vendor_type_whichever_resolves_last(self):
        matcher = TierMatcher(_acme_index())
        for order in ((HTTP_PACKET, VENDOR_PACKET), (VENDOR_PACKET, HTTP_PACKET)):
            evidence = _heard(*order).to_evidence_records()
            assert len(evidence) == 2
            match = matcher.match(evidence)
            assert match.state == DeviceState.IDENTIFIED
            assert match.driver_id == "acme_widget"
            assert match.source == "mdns:_acmewidget._tcp.local."

    def test_mdns_services_lists_every_type(self):
        info = _heard(VENDOR_PACKET, HTTP_PACKET).to_device_info()
        assert info["mdns_services"] == ["_acmewidget._tcp.local", "_http._tcp.local"]
        # The advertised non-web port counts as open; 80 does not.
        assert info["open_ports"] == [4999]

    def test_each_record_carries_its_own_txt_and_instance(self):
        records = {
            ev.data["source_id"]: ev for ev in _heard(HTTP_PACKET, VENDOR_PACKET).to_evidence_records()
        }
        vendor = records["_acmewidget._tcp.local."]
        web = records["_http._tcp.local."]
        assert vendor.data["txt"] == {"md": "Widget 3000"}
        assert vendor.data["instance"] == "Widget 3000"
        assert web.data["txt"] == {"path": "/"}
        assert web.data["instance"] == "Widget Web"
        assert all(ev.tier == SignalTier.PASSIVE_LISTENER for ev in records.values())

    def test_a_later_packet_about_the_same_instance_updates_its_slot(self):
        full = f"Widget 3000.{VENDOR_TYPE}"
        late_txt = _response(_rr(full, DNS_TYPE_TXT, _txt("fw=2.1", "bare")))
        result = _heard(VENDOR_PACKET, late_txt)
        assert len(result.services) == 1
        svc = result.service_list()[0]
        assert svc.port == 4999
        assert svc.target == "widget.local"
        # Bare TXT keys are kept.
        assert svc.txt_records == {"md": "Widget 3000", "fw": "2.1", "bare": ""}

    def test_single_service_fields_still_report_the_last_resolved(self):
        """The plugin API's mdns_browse reads one service per host."""
        result = _heard(VENDOR_PACKET, HTTP_PACKET)
        assert result.service_type == "_http._tcp.local"
        assert result.instance_name == "Widget Web"

    def test_result_built_without_services_reads_as_one(self):
        result = MDNSResult(ip=DEVICE_IP, service_type=VENDOR_TYPE, port=4999,
                            txt_records={"md": "x"})
        assert [svc.service_type for svc in result.service_list()] == [VENDOR_TYPE]
        assert len(result.to_evidence_records()) == 1
        assert MDNSResult(ip=DEVICE_IP).to_evidence_records() == []

    def test_service_without_a_type_emits_no_evidence(self):
        result = MDNSResult(ip=DEVICE_IP, services={"x": MDNSService(port=5000)})
        assert result.to_evidence_records() == []
        assert result.to_device_info()["open_ports"] == [5000]


# ---------------------------------------------------------------------------
# The DNS-SD enumeration option
# ---------------------------------------------------------------------------

def _enumeration(*types: str) -> bytes:
    return _response(*(
        _rr(DNS_SD_META_QUERY, DNS_TYPE_PTR, encode_dns_name(t)) for t in types
    ))


class TestEnumeratedTypes:
    def test_enumeration_is_recorded_per_source(self):
        scanner = MDNSScanner(service_types=[VENDOR_TYPE])
        scanner._process_response(_enumeration("_http._tcp.local.", "_acmeother._udp.local."), DEVICE_IP)
        scanner._process_response(_enumeration("_acmeother._udp.local."), "10.77.0.10")
        assert scanner.enumerated_service_types == {
            DEVICE_IP: {"_http._tcp.local.", "_acmeother._udp.local."},
            "10.77.0.10": {"_acmeother._udp.local."},
        }

    def test_off_by_default_queues_nothing(self):
        scanner = MDNSScanner(service_types=[VENDOR_TYPE])
        scanner._process_response(_enumeration("_acmeother._udp.local."), DEVICE_IP)
        assert scanner._query_queue == []

    def test_on_queues_each_unqueried_type_once(self):
        scanner = MDNSScanner(service_types=[VENDOR_TYPE], query_enumerated_types=True)
        scanner._process_response(
            _enumeration(VENDOR_TYPE, "_acmeother._udp.local.", "_acmethird._tcp.local."), DEVICE_IP,
        )
        scanner._process_response(_enumeration("_acmeother._udp.local."), "10.77.0.10")
        # The declared type is already queried; each new one is queued once.
        assert scanner._query_queue == ["_acmeother._udp.local.", "_acmethird._tcp.local."]
        # Unknown-type tracking is unchanged by the option.
        assert scanner.unknown_service_types == {"_acmeother._udp.local.", "_acmethird._tcp.local."}

    def test_enumerated_from_queries_only_those_sources(self):
        """A single-device check chases the device's own types, not every
        type the network lists."""
        scanner = MDNSScanner(
            service_types=[VENDOR_TYPE], query_enumerated_types=True,
            enumerated_from=[DEVICE_IP],
        )
        scanner._process_response(_enumeration("_acmeother._udp.local."), "10.77.0.10")
        scanner._process_response(_enumeration("_acmethird._tcp.local."), DEVICE_IP)
        assert scanner._query_queue == ["_acmethird._tcp.local."]
        # Every source's list is still recorded.
        assert set(scanner.enumerated_service_types) == {DEVICE_IP, "10.77.0.10"}

    async def test_queued_types_are_sent(self):
        scanner = MDNSScanner(service_types=[VENDOR_TYPE], query_enumerated_types=True)
        scanner._process_response(_enumeration("_acmeother._udp.local."), DEVICE_IP)
        scanner._sock = MagicMock()
        scanner._running = True
        sent: list[str] = []

        async def fake_send(service_type):
            sent.append(service_type)

        with patch.object(scanner, "_send_query", side_effect=fake_send):
            await scanner._send_queued_queries()
        assert sent == ["_acmeother._udp.local."]
        assert scanner._query_queue == []


# ---------------------------------------------------------------------------
# D8: an mDNS SRV port does not count as "already port-scanned"
# ---------------------------------------------------------------------------

def _passive_mocks(mdns_results: dict) -> tuple:
    mdns_cls, mdns = MagicMock(), MagicMock()
    mdns.start = AsyncMock(return_value=mdns_results)
    mdns.results = mdns_results
    mdns.env_error = None
    mdns_cls.return_value = mdns

    ssdp_cls, ssdp = MagicMock(), MagicMock()
    ssdp.scan = AsyncMock(return_value={})
    ssdp.results = {}
    ssdp.env_error = None
    ssdp_cls.return_value = ssdp

    amx_cls, amx = MagicMock(), MagicMock()
    amx.start = AsyncMock(return_value={})
    amx.stop = AsyncMock()
    amx.results = {}
    amx.env_error = None
    amx_cls.return_value = amx
    return mdns_cls, ssdp_cls, amx_cls


class TestPassiveOnlyFollowUp:
    async def test_ping_silent_device_with_srv_port_is_scanned_and_probed(self):
        engine = DiscoveryEngine()
        engine._budget = ScanBudget(resolve_policy("standard"), 300.0)
        engine.load_driver_hints_from_registry([{
            "id": "acme_widget",
            "name": "Acme Widget",
            "manufacturer": "Acme",
            "category": "audio",
            "transport": "tcp",
            "discovery": {"tcp_probe": {"port": 23, "expect_regex": "ACME"}},
        }])
        advertised = MDNSResult(ip=DEVICE_IP, services={
            "widget": MDNSService(service_type="_acmecfg._tcp.local", port=4999),
        })
        mdns_cls, ssdp_cls, amx_cls = _passive_mocks({DEVICE_IP: advertised})

        scanned: list[str] = []

        async def fake_scan(ip, ports, timeout=1.0, source_ip=""):
            scanned.append(ip)
            return [23, 4999]

        probed: list[tuple[int, str]] = []

        async def fake_probe(spec, target, **kwargs):
            probed.append((spec.port, target))
            return Evidence(
                tier=SignalTier.ACTIVE_PROBE,
                source="probe:custom_acme_widget_tcp",
                data={"kind": "active_probe", "source_id": "custom_acme_widget_tcp"},
            )

        with patch("openavc.discovery.engine.ping_sweep", new_callable=AsyncMock, return_value=[]), \
             patch("openavc.discovery.engine.scan_host_ports", side_effect=fake_scan), \
             patch("openavc.discovery.engine.grab_banners", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine.harvest_arp_table", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine.run_tcp_active_probe", side_effect=fake_probe), \
             patch.object(engine.community_index, "get_drivers", new_callable=AsyncMock, return_value=[]), \
             patch("openavc.discovery.engine.MDNSScanner", mdns_cls), \
             patch("openavc.discovery.engine.SSDPScanner", ssdp_cls), \
             patch("openavc.discovery.engine.AMXDDPScanner", amx_cls):
            engine.scan_status.subnets = ["10.77.0.0/24"]
            await engine._scan_pipeline_inner(["10.77.0.0/24"])

        assert scanned == [DEVICE_IP]
        assert sorted(engine.results[DEVICE_IP].open_ports) == [23, 4999]
        assert probed == [(23, DEVICE_IP)]
