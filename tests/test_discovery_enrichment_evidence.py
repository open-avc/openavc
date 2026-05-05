"""Tests for Tier 4 enrichment Evidence emission.

The deterministic matcher consumes Tier 4 soft signals (SNMP PEN,
OUI, hostname pattern) to produce ``possible`` state when no Tier
1/2/3 strong match exists. These tests verify the producers - SNMP
PEN extraction from sysObjectID, plus the standalone evidence
helpers in tier_matcher used by the engine for OUI and hostname
enrichment - emit shape-correct records.
"""

from server.discovery.result import SignalTier
from server.discovery.snmp_scanner import SNMPInfo
from server.discovery.tier_matcher import (
    KIND_HOSTNAME,
    KIND_OUI,
    KIND_SNMP_PEN,
    evidence_hostname,
    evidence_oui,
)


# ===== SNMP PEN -> Tier 4 evidence =====


class TestSNMPInfoPENAccessor:
    def test_extron_pen_extracted(self):
        info = SNMPInfo(sys_object_id="1.3.6.1.4.1.17049.1.2.3")
        assert info.pen == 17049

    def test_qsc_pen_extracted(self):
        info = SNMPInfo(sys_object_id="1.3.6.1.4.1.3872.1")
        assert info.pen == 3872

    def test_crestron_pen_extracted(self):
        info = SNMPInfo(sys_object_id="1.3.6.1.4.1.21317.5.10")
        assert info.pen == 21317

    def test_no_pen_when_oid_unrelated(self):
        info = SNMPInfo(sys_object_id="2.16.840.1.113883")
        assert info.pen is None

    def test_no_pen_when_empty(self):
        assert SNMPInfo().pen is None

    def test_no_pen_when_malformed(self):
        info = SNMPInfo(sys_object_id="1.3.6.1.4.1.notnumeric")
        assert info.pen is None

    def test_pen_with_no_trailing_oid(self):
        # sysObjectID may be just the vendor PEN with no sub-OIDs.
        info = SNMPInfo(sys_object_id="1.3.6.1.4.1.17049")
        assert info.pen == 17049


class TestSNMPInfoToEvidence:
    def test_with_extron_pen_emits_tier4(self):
        info = SNMPInfo(
            sys_object_id="1.3.6.1.4.1.17049.1.2.3",
            sys_descr="Extron DTP CrossPoint 84 IPCP, V1.07.0000",
        )
        ev = info.to_evidence()
        assert ev is not None
        assert ev.tier == SignalTier.ENRICHMENT
        assert ev.source == "snmp_pen:17049"
        assert ev.data["kind"] == KIND_SNMP_PEN
        assert ev.data["value"] == 17049
        assert ev.data["sysdescr"] == "Extron DTP CrossPoint 84 IPCP, V1.07.0000"

    def test_with_no_sysdescr(self):
        info = SNMPInfo(sys_object_id="1.3.6.1.4.1.21317.5")
        ev = info.to_evidence()
        assert ev is not None
        assert ev.data["sysdescr"] is None

    def test_returns_none_without_pen(self):
        info = SNMPInfo(sys_descr="Generic Linux box")
        assert info.to_evidence() is None

    def test_returns_none_with_empty_oid(self):
        info = SNMPInfo()
        assert info.to_evidence() is None


# ===== OUI -> Tier 4 evidence (engine-emitted) =====


class TestOUIEvidence:
    def test_normalizes_mac_to_oui_prefix(self):
        ev = evidence_oui("00:0C:4D:11:22:33", vendor="QSC")
        assert ev.tier == SignalTier.ENRICHMENT
        assert ev.source == "oui:00:0c:4d"
        assert ev.data["kind"] == KIND_OUI
        assert ev.data["value"] == "00:0c:4d"
        assert ev.data["mac"] == "00:0C:4D:11:22:33"
        assert ev.data["vendor"] == "QSC"

    def test_normalizes_dash_separators(self):
        ev = evidence_oui("00-05-A6-aa-bb-cc", vendor="Extron")
        assert ev.data["value"] == "00:05:a6"

    def test_no_vendor_field_optional(self):
        ev = evidence_oui("00:0c:4d:11:22:33")
        assert ev.data["vendor"] is None


# ===== Hostname -> Tier 4 evidence (engine-emitted) =====


class TestHostnameEvidence:
    def test_basic_hostname(self):
        ev = evidence_hostname("QSYS-Core110f")
        assert ev.tier == SignalTier.ENRICHMENT
        assert ev.source == "hostname:QSYS-Core110f"
        assert ev.data["kind"] == KIND_HOSTNAME
        assert ev.data["value"] == "QSYS-Core110f"

    def test_lowercase_hostname(self):
        # Hostname matching is case-insensitive in the SignalIndex,
        # but we preserve original casing in evidence for the audit log.
        ev = evidence_hostname("printer.local")
        assert ev.data["value"] == "printer.local"


# ===== Round-trip: emit + match =====


class TestRoundTrip:
    """Verify the evidence emitters produce records the matcher consumes."""

    def test_snmp_pen_round_trip(self):
        from server.discovery.result import DeviceState
        from server.discovery.tier_matcher import (
            SignalIndex,
            SignalRule,
            TierMatcher,
        )

        idx = SignalIndex()
        idx.add_rule(SignalRule.for_snmp_pen("extron_sis", 17049))
        matcher = TierMatcher(idx)

        info = SNMPInfo(sys_object_id="1.3.6.1.4.1.17049.1.2.3")
        ev = info.to_evidence()
        result = matcher.match([ev])

        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["extron_sis"]

    def test_oui_round_trip(self):
        from server.discovery.result import DeviceState
        from server.discovery.tier_matcher import (
            SignalIndex,
            SignalRule,
            TierMatcher,
        )

        idx = SignalIndex()
        idx.add_rule(SignalRule.for_oui("qsc_qrc", "00:0c:4d"))
        matcher = TierMatcher(idx)

        ev = evidence_oui("00:0c:4d:11:22:33", vendor="QSC")
        result = matcher.match([ev])

        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["qsc_qrc"]

    def test_hostname_round_trip(self):
        from server.discovery.result import DeviceState
        from server.discovery.tier_matcher import (
            SignalIndex,
            SignalRule,
            TierMatcher,
        )

        idx = SignalIndex()
        idx.add_rule(SignalRule.for_hostname("qsc_core", r"^QSYS-"))
        matcher = TierMatcher(idx)

        ev = evidence_hostname("QSYS-Core110f")
        result = matcher.match([ev])

        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["qsc_core"]
