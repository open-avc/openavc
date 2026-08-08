"""Tests for enrichment Evidence emission.

The deterministic matcher consumes enrichment soft signals (SNMP PEN,
OUI, hostname pattern) to produce ``possible`` state when no strong
1/2/3 strong match exists. These tests verify the producers - SNMP
PEN extraction from sysObjectID, plus the standalone evidence
helpers in tier_matcher used by the engine for OUI and hostname
enrichment - emit shape-correct records.
"""

from openavc.discovery.result import SignalTier
from openavc.discovery.snmp_scanner import SNMPInfo
from openavc.discovery.tier_matcher import (
    KIND_HOSTNAME,
    KIND_OUI,
    KIND_SNMP_PEN,
    evidence_hostname,
    evidence_oui,
)


# ===== SNMP PEN -> enrichment evidence =====


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
    def test_with_extron_pen_emits_enrichment(self):
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


# ===== OUI -> enrichment evidence (engine-emitted) =====


class TestOUIEvidence:
    def test_normalizes_mac_to_oui_prefix(self):
        ev = evidence_oui("00:60:74:11:22:33", vendor="QSC")
        assert ev.tier == SignalTier.ENRICHMENT
        assert ev.source == "oui:00:60:74"
        assert ev.data["kind"] == KIND_OUI
        assert ev.data["value"] == "00:60:74"
        assert ev.data["mac"] == "00:60:74:11:22:33"
        assert ev.data["vendor"] == "QSC"

    def test_normalizes_dash_separators(self):
        ev = evidence_oui("00-05-A6-aa-bb-cc", vendor="Extron")
        assert ev.data["value"] == "00:05:a6"

    def test_no_vendor_field_optional(self):
        ev = evidence_oui("00:60:74:11:22:33")
        assert ev.data["vendor"] is None


# ===== Hostname -> enrichment evidence (engine-emitted) =====


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
        from openavc.discovery.result import DeviceState
        from openavc.discovery.tier_matcher import (
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
        from openavc.discovery.result import DeviceState
        from openavc.discovery.tier_matcher import (
            SignalIndex,
            SignalRule,
            TierMatcher,
        )

        idx = SignalIndex()
        idx.add_rule(SignalRule.for_oui("qsc_qrc", "00:60:74"))
        matcher = TierMatcher(idx)

        ev = evidence_oui("00:60:74:11:22:33", vendor="QSC")
        result = matcher.match([ev])

        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["qsc_qrc"]

    def test_hostname_round_trip(self):
        from openavc.discovery.result import DeviceState
        from openavc.discovery.tier_matcher import (
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


# ===== SSDP/UPnP rootDesc manufacturer -> vendor_string enrichment =====


class TestSSDPManufacturerVendorString:
    """SSDP puts the rootDesc.xml <manufacturer> at the top level of its
    evidence data, and its device type is frequently the generic
    InternetGatewayDevice URN (no usable strong fingerprint). The vendor
    string is then the only identity signal, so ``extract_vendor_strings``
    must mine the top-level manufacturer for the manufacturer_alias path.
    Invented device throughout (acme_widget / AcmeAV).
    """

    def _ssdp_evidence(self):
        from openavc.discovery.ssdp_scanner import SSDPResult

        (ev,) = SSDPResult(
            ip="192.0.2.10",
            # Generic UPnP device type — every gateway advertises it, so it is
            # not (and must not be) a strong fingerprint on its own.
            st="urn:schemas-upnp-org:device:InternetGatewayDevice:1",
            server="Acme_Switch UPnP/1.1 AW9000/1.0",
            manufacturer="AcmeAV",
            model_name="AW-9000",
        ).to_evidence_records()
        return ev

    def test_extract_mines_top_level_manufacturer(self):
        from openavc.discovery.tier_matcher import (
            KIND_VENDOR_STRING,
            extract_vendor_strings,
        )

        ev = self._ssdp_evidence()
        assert ev is not None
        assert ev.data["manufacturer"] == "AcmeAV"  # top-level, not under response/txt

        mined = extract_vendor_strings([ev])
        values = {e.data["value"] for e in mined if e.data["kind"] == KIND_VENDOR_STRING}
        assert "acmeav" in values

    def test_generic_device_type_alone_does_not_identify(self):
        # Without mining the manufacturer, the generic device type yields no
        # strong match — proving the vendor string is what produces a result.
        from openavc.discovery.result import DeviceState
        from openavc.discovery.tier_matcher import (
            SignalIndex,
            SignalRule,
            TierMatcher,
        )

        idx = SignalIndex()
        idx.add_rule(SignalRule.for_vendor_string("acme_widget", "acmeav"))
        matcher = TierMatcher(idx)

        result = matcher.match([self._ssdp_evidence()])  # no vendor mining yet
        assert result.state == DeviceState.UNKNOWN

    def test_ssdp_manufacturer_round_trip_to_possible(self):
        # Mirrors discovery engine phase 8: append mined vendor strings to the
        # evidence log, then match. The manufacturer_alias rule fires.
        from openavc.discovery.result import DeviceState
        from openavc.discovery.tier_matcher import (
            SignalIndex,
            SignalRule,
            TierMatcher,
            extract_vendor_strings,
        )

        idx = SignalIndex()
        idx.add_rule(SignalRule.for_vendor_string("acme_widget", "acmeav"))
        matcher = TierMatcher(idx)

        ev = self._ssdp_evidence()
        evidence_log = [ev, *extract_vendor_strings([ev])]
        result = matcher.match(evidence_log)

        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["acme_widget"]
