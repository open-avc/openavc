"""Tests for the deterministic identification types added in the discovery redesign.

These types live alongside the legacy ``DriverMatch`` /
``DiscoveredDevice.confidence`` fields until the orchestrator swap.
See ``server/discovery/result.py`` and ``discovery-redesign-plan.md``.
"""

from server.discovery.result import (
    DeviceState,
    DiscoveredDevice,
    Evidence,
    IdentificationMatch,
    SignalTier,
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
    def test_four_tiers(self):
        assert {t.value for t in SignalTier} == {
            "tier1", "tier2", "tier3", "tier4",
        }

    def test_named_constants(self):
        assert SignalTier.PASSIVE_LISTENER.value == "tier1"
        assert SignalTier.BROADCAST_PROBE.value == "tier2"
        assert SignalTier.ACTIVE_PROBE.value == "tier3"
        assert SignalTier.ENRICHMENT.value == "tier4"


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
            "broadcast:crestron_cip",
            {"hostname": "DIN-AP-7F74F65F"},
            at=12345.0,
        )
        assert ev.to_dict() == {
            "tier": "tier2",
            "source": "broadcast:crestron_cip",
            "data": {"hostname": "DIN-AP-7F74F65F"},
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
        # The Phase 6 cleanup removed `matched_drivers`, `confidence`,
        # and `sources` from the JSON shape entirely.
        d = DiscoveredDevice(ip="192.168.1.10")
        out = d.to_dict()
        assert "matched_drivers" not in out
        assert "confidence" not in out
        assert "sources" not in out
