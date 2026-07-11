"""Tests for the mDNS scanner additions in the discovery redesign.

Covers Evidence emission, unknown-service-type tracking via
``_services._dns-sd._udp.`` enumeration, and control-interface
binding. Existing mDNS tests in test_discovery_passive.py remain green
unchanged.
"""

from server.discovery.mdns_scanner import MDNSResult, MDNSScanner
from server.discovery.result import SignalTier
from server.discovery.ssdp_scanner import SSDPResult, SSDPScanner
from server.discovery.tier_matcher import KIND_SSDP


class TestMDNSResultEvidence:
    def test_with_service_type_emits_passive_listener_evidence(self):
        r = MDNSResult(
            ip="10.0.0.50",
            service_type="_netaudio-cmc._udp.local.",
            txt_records={"manufacturer": "Audinate", "id": "001122334455"},
            instance_name="Stage-Left",
        )
        ev = r.to_evidence()

        assert ev is not None
        assert ev.tier == SignalTier.PASSIVE_LISTENER
        assert ev.source == "mdns:_netaudio-cmc._udp.local."
        assert ev.data["txt"]["manufacturer"] == "Audinate"
        assert ev.data["instance"] == "Stage-Left"

    def test_a_record_only_returns_none(self):
        # A-record-only resolutions don't carry a service type and
        # cannot produce a passive_listener identification on their own.
        r = MDNSResult(ip="10.0.0.50")
        assert r.to_evidence() is None

    def test_no_txt_records_omits_txt_field(self):
        r = MDNSResult(
            ip="10.0.0.50",
            service_type="_pjlink._tcp.local.",
        )
        ev = r.to_evidence()
        assert ev is not None
        # No TXT was observed, so the evidence carries no txt key.
        # (Filter rules can still match on no-TXT.)
        assert "txt" not in ev.data


class TestMDNSScannerControlIP:
    def test_default_no_control_ip(self):
        scanner = MDNSScanner()
        assert scanner._control_ip == ""

    def test_control_ip_passed_through(self):
        scanner = MDNSScanner(control_ip="192.168.1.50")
        assert scanner._control_ip == "192.168.1.50"


class TestUnknownServiceTypeTracking:
    def test_initial_set_is_empty(self):
        scanner = MDNSScanner()
        assert scanner.unknown_service_types == set()

    def test_track_records_new_service_type(self):
        scanner = MDNSScanner()
        scanner._track_unknown_service_type("_unknown-vendor._tcp.local.")
        assert "_unknown-vendor._tcp.local." in scanner.unknown_service_types

    def test_track_normalizes_trailing_dot(self):
        scanner = MDNSScanner()
        scanner._track_unknown_service_type("_unknown._tcp.local")
        scanner._track_unknown_service_type("_unknown._tcp.local.")
        # Both should normalize to one entry.
        types = scanner.unknown_service_types
        assert len(types) == 1
        assert "_unknown._tcp.local." in types

    def test_track_filters_out_known_types(self):
        scanner = MDNSScanner(service_types=[
            "_pjlink._tcp.local.",
            "_netaudio-cmc._udp.local.",
        ])
        # Types the scanner is configured to query are "known";
        # observing them in the DNS-SD enumeration is not "unknown".
        scanner._track_unknown_service_type("_pjlink._tcp.local.")
        scanner._track_unknown_service_type("_netaudio-cmc._udp.local.")
        assert scanner.unknown_service_types == set()

    def test_track_dedups(self):
        scanner = MDNSScanner()
        for _ in range(5):
            scanner._track_unknown_service_type("_some-vendor._tcp.local.")
        assert len(scanner.unknown_service_types) == 1

    def test_unknown_service_types_returns_copy(self):
        scanner = MDNSScanner()
        scanner._track_unknown_service_type("_x._tcp.local.")
        snapshot = scanner.unknown_service_types
        snapshot.clear()
        # Internal set should be untouched.
        assert "_x._tcp.local." in scanner.unknown_service_types


class TestSSDPControlIP:
    def test_default_no_control_ip(self):
        scanner = SSDPScanner()
        assert scanner._control_ip == ""

    def test_control_ip_passed_through(self):
        scanner = SSDPScanner(control_ip="192.168.1.50")
        assert scanner._control_ip == "192.168.1.50"


class TestSSDPResultEvidence:
    def test_with_st_emits_passive_listener_evidence(self):
        r = SSDPResult(
            ip="10.0.0.50",
            st="urn:schemas-upnp-org:device:MediaRenderer:1",
            usn="uuid:abc::urn:schemas-upnp-org:device:MediaRenderer:1",
            friendly_name="Sonos Kitchen",
            manufacturer="Sonos Inc.",
            model_name="ZP100",
        )
        ev = r.to_evidence()
        assert ev is not None
        assert ev.tier == SignalTier.PASSIVE_LISTENER
        assert ev.data["kind"] == KIND_SSDP
        assert ev.data["source_id"] == "urn:schemas-upnp-org:device:MediaRenderer:1"
        assert ev.data["manufacturer"] == "Sonos Inc."
        assert ev.data["model"] == "ZP100"

    def test_no_st_returns_none(self):
        # No ST means we can't deterministically match.
        r = SSDPResult(ip="10.0.0.50", usn="uuid:abc", friendly_name="thing")
        assert r.to_evidence() is None

    def test_description_fields_emitted_as_observed_field_map(self):
        # model/manufacturer/friendly_name double as the matcher's
        # observed-field map so ssdp rules can filter on them.
        r = SSDPResult(
            ip="10.0.0.50",
            st="urn:foo:device:AcmeFamily:1",
            friendly_name="Widget 6a",
            manufacturer="AcmeCorp",
            model_name="Widget-6a",
        )
        ev = r.to_evidence()
        assert ev is not None
        assert ev.data["txt"] == {
            "model": "Widget-6a",
            "manufacturer": "AcmeCorp",
            "friendly_name": "Widget 6a",
        }

    def test_no_description_fields_omits_field_map(self):
        r = SSDPResult(ip="10.0.0.50", st="urn:foo:device:AcmeFamily:1")
        ev = r.to_evidence()
        assert ev is not None
        assert "txt" not in ev.data


class TestDriverDeclaredServiceTypes:
    """Service types come from driver catalog declarations.

    The hard-coded AV_SERVICE_TYPES list is gone. The MDNSScanner
    accepts a ``service_types`` constructor argument and queries
    whatever the engine populates from loaded drivers' mdns_services:
    blocks. The DNS-SD meta-query is always included so unknown
    service types surface for catalog growth.
    """

    def test_scanner_queries_declared_types(self):
        from server.discovery.mdns_scanner import MDNSScanner

        declared = [
            "_netaudio-cmc._udp.local.",
            "_nmos-node._tcp.local.",
            "_ndi._tcp.local.",
            "_leap._tcp.local.",
            "_ssc._udp.local.",
        ]
        scanner = MDNSScanner(service_types=declared)
        for st in declared:
            assert st in scanner._service_types

    def test_dns_sd_meta_query_included_even_when_caller_omits_it(self):
        from server.discovery.mdns_scanner import MDNSScanner, DNS_SD_META_QUERY

        scanner = MDNSScanner(service_types=["_ndi._tcp.local."])
        assert DNS_SD_META_QUERY in scanner._service_types

    def test_normalization_adds_trailing_dot_and_dedupes(self):
        from server.discovery.mdns_scanner import MDNSScanner

        scanner = MDNSScanner(service_types=[
            "_FOO._tcp.local",     # missing trailing dot, mixed case
            "_foo._tcp.local.",    # duplicate after normalization
            "_bar._udp.local.",
        ])
        assert scanner._service_types.count("_FOO._tcp.local.") == 1
        assert "_bar._udp.local." in scanner._service_types

    def test_unknown_service_filter_uses_configured_list(self):
        from server.discovery.mdns_scanner import MDNSScanner

        scanner = MDNSScanner(service_types=["_known._tcp.local."])
        # A type configured for the scanner is "known" — never logged.
        scanner._track_unknown_service_type("_known._tcp.local.")
        assert "_known._tcp.local." not in scanner.unknown_service_types
        # An unfamiliar type from DNS-SD enumeration surfaces.
        scanner._track_unknown_service_type("_someweirdservice._tcp.local.")
        assert "_someweirdservice._tcp.local." in scanner.unknown_service_types
