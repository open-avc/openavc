"""Tests for the deterministic identification types.

See ``openavc/discovery/result.py`` for the type definitions and
``OpenAVC-Discovery-Spec.md`` §5 for the matcher contract.
"""

from openavc.discovery.result import (
    DeviceState,
    DiscoveredDevice,
    Evidence,
    IdentificationMatch,
    SignalTier,
    device_info_from_evidence,
)


class TestDeviceStateEnum:
    def test_three_states(self):
        assert {s.value for s in DeviceState} == {
            "identified", "possible", "unknown",
        }

    def test_string_values(self):
        # Used as JSON literals on the wire, so the values must be stable.
        assert DeviceState.IDENTIFIED.value == "identified"
        assert DeviceState.POSSIBLE.value == "possible"
        assert DeviceState.UNKNOWN.value == "unknown"


class TestSignalTier:
    def test_four_kinds(self):
        assert {t.value for t in SignalTier} == {
            "passive_listener", "broadcast_probe", "active_probe", "enrichment",
        }

    def test_named_constants(self):
        assert SignalTier.PASSIVE_LISTENER.value == "passive_listener"
        assert SignalTier.BROADCAST_PROBE.value == "broadcast_probe"
        assert SignalTier.ACTIVE_PROBE.value == "active_probe"
        assert SignalTier.ENRICHMENT.value == "enrichment"


class TestEvidence:
    def test_minimal_construction(self):
        ev = Evidence(SignalTier.PASSIVE_LISTENER, "mdns:_ndi._tcp")
        assert ev.tier == SignalTier.PASSIVE_LISTENER
        assert ev.source == "mdns:_ndi._tcp"
        assert ev.data == {}
        assert ev.at > 0

    def test_with_data(self):
        ev = Evidence(
            SignalTier.PASSIVE_LISTENER,
            "mdns:_netaudio-cmc._udp",
            {"manufacturer": "Audinate", "id": "abcdef0123456789"},
        )
        assert ev.data["manufacturer"] == "Audinate"

    def test_to_dict(self):
        ev = Evidence(
            SignalTier.BROADCAST_PROBE,
            "broadcast:custom_widget_udp",
            {"hostname": "WIDGET-7F74F65F"},
            at=12345.0,
        )
        assert ev.to_dict() == {
            "tier": "broadcast_probe",
            "source": "broadcast:custom_widget_udp",
            "data": {"hostname": "WIDGET-7F74F65F"},
            "at": 12345.0,
        }


class TestIdentificationMatch:
    def test_identified_factory(self):
        m = IdentificationMatch.identified(
            "pjlink_class1",
            "probe:pjlink",
            [Evidence(SignalTier.ACTIVE_PROBE, "probe:pjlink", {"class": "1"})],
        )
        assert m.state == DeviceState.IDENTIFIED
        assert m.driver_id == "pjlink_class1"
        assert m.source == "probe:pjlink"
        assert m.candidates == []
        assert m.reason == ""
        assert len(m.evidence) == 1

    def test_possible_factory(self):
        m = IdentificationMatch.possible(
            ["qsc_qrc", "qsc_qsys_external"],
            "oui:00:60:74",
            [Evidence(SignalTier.ENRICHMENT, "oui:00:60:74", {"vendor": "QSC"})],
        )
        assert m.state == DeviceState.POSSIBLE
        assert m.driver_id is None
        assert m.candidates == ["qsc_qrc", "qsc_qsys_external"]
        assert m.source == "oui:00:60:74"
        assert m.reason == ""

    def test_unknown_factory(self):
        m = IdentificationMatch.unknown("port_open_but_no_protocol_match")
        assert m.state == DeviceState.UNKNOWN
        assert m.driver_id is None
        assert m.candidates == []
        assert m.reason == "port_open_but_no_protocol_match"

    def test_unknown_default_reason(self):
        m = IdentificationMatch.unknown()
        assert m.reason == "no_signal_matched"

    def test_to_dict_identified(self):
        m = IdentificationMatch.identified("pjlink_class1", "probe:pjlink")
        d = m.to_dict()
        assert d["state"] == "identified"
        assert d["driver_id"] == "pjlink_class1"
        assert d["candidates"] == []
        assert d["evidence"] == []

    def test_to_dict_possible(self):
        m = IdentificationMatch.possible(["a", "b"], "oui:0c:4d:e9")
        d = m.to_dict()
        assert d["state"] == "possible"
        assert d["driver_id"] is None
        assert d["candidates"] == ["a", "b"]


class TestDiscoveredDeviceIntegration:
    def test_default_has_no_identification(self):
        d = DiscoveredDevice(ip="192.168.1.1")
        assert d.identification is None
        assert d.evidence_log == []

    def test_serializes_identification_when_set(self):
        d = DiscoveredDevice(ip="192.168.1.50")
        d.identification = IdentificationMatch.identified(
            "extron_sis", "probe:extron_sis",
        )
        d.evidence_log.append(
            Evidence(SignalTier.ACTIVE_PROBE, "probe:extron_sis", {"model": "DTP CrossPoint"}),
        )

        out = d.to_dict()
        assert out["identification"]["state"] == "identified"
        assert out["identification"]["driver_id"] == "extron_sis"
        assert out["evidence_log"][0]["source"] == "probe:extron_sis"

    def test_serializes_no_identification_as_null(self):
        d = DiscoveredDevice(ip="192.168.1.99")
        out = d.to_dict()
        assert out["identification"] is None
        assert out["evidence_log"] == []

    def test_to_dict_omits_legacy_keys(self):
        # The matcher rewrite removed `matched_drivers`, `confidence`,
        # and `sources` from the JSON shape entirely.
        d = DiscoveredDevice(ip="192.168.1.10")
        out = d.to_dict()
        assert "matched_drivers" not in out
        assert "confidence" not in out
        assert "sources" not in out


class TestDeviceInfoFromEvidence:
    """device_info_from_evidence lifts hostname/model/firmware out of the
    three probe evidence shapes so the engine's merge_device_info call
    surfaces the data on the device card. Regression test for the audit's
    B1 finding (passive listeners merged; probes did not)."""

    def test_udp_broadcast_shape(self):
        # probe_runner.run_udp_broadcast_probe puts extracted fields in
        # data.txt at top level; data.response only carries the IP.
        ev = Evidence(
            SignalTier.BROADCAST_PROBE,
            "broadcast:custom_foo_udp",
            data={
                "kind": "broadcast",
                "source_id": "custom_foo_udp",
                "response": {"ip": "10.0.0.5"},
                "txt": {"manufacturer": "Foo", "model": "Bar-100", "firmware": "1.2.3"},
            },
        )
        assert device_info_from_evidence(ev) == {
            "manufacturer": "Foo", "model": "Bar-100", "firmware": "1.2.3",
        }

    def test_tcp_active_shape(self):
        # probe_runner.run_tcp_active_probe nests extract: outputs under
        # data.response.extracted; reserved keys (manufacturer/make) are
        # at top of data.response. data.response.text is raw payload.
        ev = Evidence(
            SignalTier.ACTIVE_PROBE,
            "probe:custom_extron_sis_tcp",
            data={
                "kind": "probe",
                "source_id": "custom_extron_sis_tcp",
                "response": {
                    "text": "V1.18 DTP-T-USW-333",
                    "manufacturer": "Extron",
                    "extracted": {"model": "DTP-T-USW-333", "firmware": "1.18"},
                },
                "port": 23,
            },
        )
        assert device_info_from_evidence(ev) == {
            "manufacturer": "Extron", "model": "DTP-T-USW-333", "firmware": "1.18",
        }

    def test_companion_shape_top_level_response(self):
        # A Python companion can put device-info fields directly at the
        # top of data.response (crestron_cip's emit pattern).
        ev = Evidence(
            SignalTier.BROADCAST_PROBE,
            "broadcast:custom_crestron_cip_companion_udp",
            data={
                "kind": "broadcast",
                "source_id": "custom_crestron_cip_companion_udp",
                "response": {
                    "hostname": "CP3-12345",
                    "model": "CP3",
                    "firmware": "2.001.0042",
                },
                "port": 41794,
            },
        )
        assert device_info_from_evidence(ev) == {
            "hostname": "CP3-12345", "model": "CP3", "firmware": "2.001.0042",
        }

    def test_make_alias_folds_into_manufacturer(self):
        # 'make' is a probe extract alias; the device card has only one
        # vendor field, so fold it into manufacturer.
        ev = Evidence(
            SignalTier.BROADCAST_PROBE, "broadcast:custom_x",
            data={
                "kind": "broadcast", "source_id": "custom_x",
                "response": {"ip": "1.1.1.1"},
                "txt": {"make": "Aliased"},
            },
        )
        assert device_info_from_evidence(ev) == {"manufacturer": "Aliased"}

    def test_explicit_manufacturer_wins_over_make(self):
        ev = Evidence(
            SignalTier.BROADCAST_PROBE, "broadcast:custom_x",
            data={
                "kind": "broadcast", "source_id": "custom_x",
                "response": {"manufacturer": "Real", "make": "Alias"},
            },
        )
        assert device_info_from_evidence(ev) == {"manufacturer": "Real"}

    def test_empty_response_yields_empty(self):
        ev = Evidence(
            SignalTier.BROADCAST_PROBE, "broadcast:foo",
            data={"kind": "broadcast", "source_id": "foo", "response": {"ip": "1.1.1.1"}},
        )
        assert device_info_from_evidence(ev) == {}

    def test_passive_listener_evidence_safe(self):
        # mDNS/SSDP evidence isn't supposed to be passed here, but it
        # shouldn't crash if it is — passive paths already merge their
        # own to_device_info().
        ev = Evidence(
            SignalTier.PASSIVE_LISTENER, "mdns:_pjlink._tcp.",
            data={"kind": "mdns", "source_id": "_pjlink._tcp.",
                  "txt": {"vendor": "Sony"}},
        )
        # 'vendor' isn't a recognized key — returns empty.
        assert device_info_from_evidence(ev) == {}
