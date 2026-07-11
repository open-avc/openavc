"""Tests for the deterministic TierMatcher.

These tests use synthetic SignalRules — driver hint loading is wired
in `hints.build_signal_index`. The matcher's contract must hold
regardless of where rules come from.
"""

import pytest

from server.discovery.result import (
    DeviceState,
    SignalTier,
)
from server.discovery.tier_matcher import (
    KIND_AMX_DDP,
    KIND_BROADCAST,
    KIND_MDNS,
    KIND_OPEN_PORT,
    KIND_VENDOR_STRING,
    SignalIndex,
    SignalRule,
    TierMatcher,
    evidence_active_probe,
    evidence_amx_ddp,
    evidence_broadcast,
    evidence_hostname,
    evidence_mdns,
    evidence_open_port,
    evidence_oui,
    evidence_snmp_pen,
    evidence_vendor_string,
    extract_vendor_strings,
)


# ===== SignalRule factories =====


class TestSignalRuleFactories:
    def test_mdns_normalizes_service_type(self):
        r = SignalRule.for_mdns("dante_generic", "_NetAudio-CMC._UDP.Local")
        assert r.kind == KIND_MDNS
        assert r.source_id == "_netaudio-cmc._udp.local."
        assert r.tier == SignalTier.PASSIVE_LISTENER

    def test_mdns_with_txt_match(self):
        r = SignalRule.for_mdns(
            "shure_p300",
            "_http._tcp.local.",
            txt_match={"manufacturer": "Shure", "model": "P300"},
        )
        assert dict(r.txt_match) == {"manufacturer": "Shure", "model": "P300"}

    def test_oui_normalizes_prefix(self):
        r = SignalRule.for_oui("qsc_qrc", "00-60-74-aa-bb-cc")
        assert r.source_id == "00:60:74"

    def test_amx_ddp_combines_make_model(self):
        r = SignalRule.for_amx_ddp("polycom_ssc", "Polycom", "SoundStructureC*")
        assert r.kind == KIND_AMX_DDP
        assert r.source_id == "Polycom/SoundStructureC*"


# ===== SignalIndex =====


class TestSignalIndexBasic:
    def test_register_and_lookup_mdns(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("dante_generic", "_netaudio-cmc._udp.local."))

        rule = idx.find_strong(KIND_MDNS, "_netaudio-cmc._udp.local.")
        assert rule is not None
        assert rule.driver_id == "dante_generic"

    def test_lookup_normalizes_case_and_trailing_dot(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("ndi_source", "_ndi._tcp.local."))

        # Caller may pass non-normalized service type — lookup normalizes.
        assert idx.find_strong(KIND_MDNS, "_NDI._tcp.local") is not None
        assert idx.find_strong(KIND_MDNS, "_ndi._tcp.local") is not None

    def test_unknown_kind_raises(self):
        idx = SignalIndex()
        with pytest.raises(ValueError, match="Unknown rule kind"):
            idx.add_rule(SignalRule(
                driver_id="x", tier=SignalTier.PASSIVE_LISTENER,
                kind="bogus", source_id="y",
            ))


class TestSignalIndexCollisions:
    def test_duplicate_same_driver_is_idempotent(self):
        idx = SignalIndex()
        rule = SignalRule.for_broadcast("crestron_3series", "crestron_cip")
        idx.add_rule(rule)
        idx.add_rule(rule)  # No raise.

        assert idx.find_strong(KIND_BROADCAST, "crestron_cip").driver_id == "crestron_3series"

    def test_two_drivers_same_signal_no_filter_raises(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_broadcast("a", "pjlink_class2"))
        with pytest.raises(ValueError, match="Signal collision"):
            idx.add_rule(SignalRule.for_broadcast("b", "pjlink_class2"))

    def test_generic_then_filtered_raises(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("generic_http", "_http._tcp.local."))
        with pytest.raises(ValueError, match="without a TXT filter"):
            idx.add_rule(SignalRule.for_mdns(
                "shure_p300", "_http._tcp.local.",
                txt_match={"manufacturer": "Shure"},
            ))

    def test_filtered_then_generic_raises(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns(
            "shure_p300", "_http._tcp.local.",
            txt_match={"manufacturer": "Shure"},
        ))
        with pytest.raises(ValueError, match="without a TXT filter"):
            idx.add_rule(SignalRule.for_mdns("generic_http", "_http._tcp.local."))

    def test_two_filtered_distinct_filters_ok(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns(
            "shure_p300", "_http._tcp.local.",
            txt_match={"manufacturer": "Shure"},
        ))
        idx.add_rule(SignalRule.for_mdns(
            "qsc_core", "_http._tcp.local.",
            txt_match={"manufacturer": "QSC"},
        ))

        # Lookup respects TXT filter
        r1 = idx.find_strong(KIND_MDNS, "_http._tcp.local.", {"manufacturer": "Shure"})
        assert r1 is not None and r1.driver_id == "shure_p300"
        r2 = idx.find_strong(KIND_MDNS, "_http._tcp.local.", {"manufacturer": "QSC"})
        assert r2 is not None and r2.driver_id == "qsc_core"

    def test_no_txt_no_match_when_filter_required(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns(
            "shure_p300", "_http._tcp.local.",
            txt_match={"manufacturer": "Shure"},
        ))
        # Observation has no TXT — filter not satisfied — no match.
        assert idx.find_strong(KIND_MDNS, "_http._tcp.local.") is None
        # Wrong manufacturer — no match.
        assert idx.find_strong(
            KIND_MDNS, "_http._tcp.local.", {"manufacturer": "Other"},
        ) is None

    def test_txt_match_is_case_insensitive(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns(
            "shure_p300", "_http._tcp.local.",
            txt_match={"manufacturer": "Shure"},
        ))
        r = idx.find_strong(
            KIND_MDNS, "_http._tcp.local.", {"Manufacturer": "SHURE"},
        )
        assert r is not None and r.driver_id == "shure_p300"

    def test_more_specific_filter_wins(self):
        # When two filtered rules both match, prefer the longer filter.
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns(
            "shure_generic", "_http._tcp.local.",
            txt_match={"manufacturer": "Shure"},
        ))
        idx.add_rule(SignalRule.for_mdns(
            "shure_p300", "_http._tcp.local.",
            txt_match={"manufacturer": "Shure", "model": "P300"},
        ))
        r = idx.find_strong(
            KIND_MDNS, "_http._tcp.local.",
            {"manufacturer": "Shure", "model": "P300"},
        )
        assert r is not None and r.driver_id == "shure_p300"


class TestSoftSignals:
    def test_oui_lookup_returns_all(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_oui("qsc_qrc", "00:60:74"))
        idx.add_rule(SignalRule.for_oui("qsc_qsys_external", "00:60:74"))

        hits = idx.find_soft_oui("00:60:74:11:22:33")
        assert set(hits) == {"qsc_qrc", "qsc_qsys_external"}

    def test_oui_no_match(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_oui("qsc_qrc", "00:60:74"))
        assert idx.find_soft_oui("aa:bb:cc:dd:ee:ff") == []

    def test_pen_lookup(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_snmp_pen("extron_sis", 17049))
        assert idx.find_soft_pen(17049) == ["extron_sis"]
        assert idx.find_soft_pen(99999) == []

    def test_hostname_pattern(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_hostname("qsc_core", r"^(qsc|qsys)-"))
        assert idx.find_soft_hostname("QSYS-Core110f") == ["qsc_core"]
        assert idx.find_soft_hostname("printer.local") == []

    def test_invalid_hostname_pattern_raises(self):
        idx = SignalIndex()
        with pytest.raises(ValueError, match="Invalid hostname pattern"):
            idx.add_rule(SignalRule.for_hostname("bad", "[unclosed"))

    def test_open_port_lookup_returns_all(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_open_port("qsc_qrc", 1710))
        idx.add_rule(SignalRule.for_open_port("qsc_qsys_external", 1710))
        assert set(idx.find_soft_open_port(1710)) == {"qsc_qrc", "qsc_qsys_external"}

    def test_open_port_no_match(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_open_port("qsc_qrc", 1710))
        assert idx.find_soft_open_port(9999) == []
        assert idx.find_soft_open_port(None) == []

    def test_open_port_factory(self):
        r = SignalRule.for_open_port("pjlink_class1", 4352)
        assert r.kind == KIND_OPEN_PORT
        assert r.source_id == "4352"
        assert r.tier == SignalTier.ENRICHMENT

    def test_evidence_open_port_shape(self):
        ev = evidence_open_port(1710)
        assert ev.tier == SignalTier.ENRICHMENT
        assert ev.data["kind"] == KIND_OPEN_PORT
        assert ev.data["value"] == 1710
        assert ev.source == "open_port:1710"

    def test_vendor_string_factory_normalizes(self):
        r = SignalRule.for_vendor_string("sharp_nec_projector", "  Sharp NEC  ")
        assert r.kind == KIND_VENDOR_STRING
        assert r.source_id == "sharp nec"
        assert r.tier == SignalTier.ENRICHMENT

    def test_vendor_string_lookup_case_insensitive(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_vendor_string("sharp_nec_projector", "NEC"))
        idx.add_rule(SignalRule.for_vendor_string("nec_display", "NEC"))
        # Probe response carried "NEC" verbatim — matches both drivers.
        assert set(idx.find_soft_vendor_string("NEC")) == {
            "sharp_nec_projector", "nec_display",
        }
        # Lowercase + extra whitespace from upstream parsing also match.
        assert set(idx.find_soft_vendor_string("  nec ")) == {
            "sharp_nec_projector", "nec_display",
        }

    def test_vendor_string_no_match(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_vendor_string("sharp_nec_projector", "NEC"))
        assert idx.find_soft_vendor_string("Sony") == []
        assert idx.find_soft_vendor_string("") == []
        assert idx.find_soft_vendor_string(None) == []
        assert idx.find_soft_vendor_string("   ") == []

    def test_vendor_string_collision_allowed(self):
        # Two drivers can claim the same alias — soft signals don't collide.
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_vendor_string("a", "Sony"))
        idx.add_rule(SignalRule.for_vendor_string("b", "Sony"))
        assert set(idx.find_soft_vendor_string("Sony")) == {"a", "b"}

    def test_evidence_vendor_string_shape(self):
        ev = evidence_vendor_string("NEC", source_probe_id="pjlink_class1")
        assert ev.tier == SignalTier.ENRICHMENT
        assert ev.data["kind"] == KIND_VENDOR_STRING
        assert ev.data["value"] == "nec"
        assert ev.data["raw"] == "NEC"
        assert ev.data["source_probe_id"] == "pjlink_class1"
        assert ev.source == "vendor_string:nec"


# ===== TierMatcher =====


class TestTierMatcherIdentified:
    def test_mdns_match_identified(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("dante_generic", "_netaudio-cmc._udp.local."))
        m = TierMatcher(idx)

        result = m.match([
            evidence_mdns("_netaudio-cmc._udp.local.", txt={"manufacturer": "Audinate"}),
        ])
        assert result.state == DeviceState.IDENTIFIED
        assert result.driver_id == "dante_generic"
        assert result.source == "mdns:_netaudio-cmc._udp.local."

    def test_broadcast_match_identified(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_broadcast("crestron_3series", "crestron_cip"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_broadcast("crestron_cip", {"hostname": "DIN-AP-7F74F65F"}),
        ])
        assert result.state == DeviceState.IDENTIFIED
        assert result.driver_id == "crestron_3series"

    def test_ssdp_model_filter_end_to_end(self):
        # Two drivers share one family URN; the scanner's device-description
        # fields (SSDPResult.to_evidence -> data["txt"]) pick the right one.
        from server.discovery.ssdp_scanner import SSDPResult

        idx = SignalIndex()
        idx.add_rule(SignalRule.for_ssdp(
            "widget_a", "urn:foo:device:AcmeFamily:1", txt_match={"model": "Widget-6"},
        ))
        idx.add_rule(SignalRule.for_ssdp(
            "widget_b", "urn:foo:device:AcmeFamily:1", txt_match={"model": "Widget-6a"},
        ))
        m = TierMatcher(idx)

        ev = SSDPResult(
            ip="10.0.0.60",
            st="urn:foo:device:AcmeFamily:1",
            manufacturer="AcmeCorp",
            model_name="Widget-6a",
        ).to_evidence()
        result = m.match([ev])
        assert result.state == DeviceState.IDENTIFIED
        assert result.driver_id == "widget_b"

        # Same URN with no description fields -> filtered rules stay silent.
        bare = SSDPResult(
            ip="10.0.0.61", st="urn:foo:device:AcmeFamily:1",
        ).to_evidence()
        result = m.match([bare])
        assert result.state != DeviceState.IDENTIFIED

    def test_active_probe_match_identified(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_active_probe("pjlink", {"manufacturer": "NEC", "model": "PA1004UL"}),
        ])
        assert result.state == DeviceState.IDENTIFIED
        assert result.driver_id == "pjlink_class1"

    def test_amx_ddp_match_identified(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_amx_ddp("polycom_ssc", "Polycom", "SoundStructureC*"))
        m = TierMatcher(idx)

        # A real beacon carries a concrete model, which must glob-match the
        # registered "SoundStructureC*" pattern (not be looked up exactly).
        result = m.match([
            evidence_amx_ddp("Polycom", "SoundStructureC16"),
        ])
        assert result.state == DeviceState.IDENTIFIED
        assert result.driver_id == "polycom_ssc"


class TestTierMatcherSignalOrdering:
    def test_passive_listener_beats_broadcast_probe(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("driver_passive", "_qsc._tcp.local."))
        idx.add_rule(SignalRule.for_broadcast("driver_broadcast", "qsc_qdp"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_broadcast("qsc_qdp"),
            evidence_mdns("_qsc._tcp.local."),
        ])
        # passive_listener wins regardless of order in the evidence log.
        assert result.driver_id == "driver_passive"

    def test_broadcast_probe_beats_active_probe(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_broadcast("driver_broadcast", "pjlink_class2"))
        idx.add_rule(SignalRule.for_active_probe("driver_active", "pjlink"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_active_probe("pjlink"),
            evidence_broadcast("pjlink_class2"),
        ])
        assert result.driver_id == "driver_broadcast"

    def test_active_probe_beats_enrichment(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_active_probe("driver_active", "extron_sis"))
        idx.add_rule(SignalRule.for_oui("driver_active", "00:05:a6"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_oui("00:05:a6:11:22:33"),
            evidence_active_probe("extron_sis"),
        ])
        # Strong active_probe match — not a possible state.
        assert result.state == DeviceState.IDENTIFIED


class TestTierMatcherPossible:
    def test_oui_only_yields_possible(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_oui("qsc_qrc", "00:60:74"))
        m = TierMatcher(idx)

        result = m.match([evidence_oui("00:60:74:11:22:33")])
        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["qsc_qrc"]
        assert result.source.startswith("oui:")

    def test_oui_with_multiple_candidates(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_oui("qsc_qrc", "00:60:74"))
        idx.add_rule(SignalRule.for_oui("qsc_qsys_external", "00:60:74"))
        m = TierMatcher(idx)

        result = m.match([evidence_oui("00:60:74:aa:bb:cc")])
        assert result.state == DeviceState.POSSIBLE
        assert set(result.candidates) == {"qsc_qrc", "qsc_qsys_external"}

    def test_pen_yields_possible(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_snmp_pen("extron_sis", 17049))
        m = TierMatcher(idx)

        result = m.match([evidence_snmp_pen(17049, sysdescr="Extron DTP")])
        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["extron_sis"]

    def test_hostname_yields_possible(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_hostname("qsc_core", r"^qsys-"))
        m = TierMatcher(idx)

        result = m.match([evidence_hostname("QSYS-Core110f")])
        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["qsc_core"]

    def test_open_port_yields_possible(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_open_port("qsc_qrc", 1710))
        m = TierMatcher(idx)

        result = m.match([evidence_open_port(1710)])
        assert result.state == DeviceState.POSSIBLE
        assert result.candidates == ["qsc_qrc"]
        assert result.source == "open_port:1710"

    def test_narrowest_signal_first(self):
        # Two soft hits: OUI matches 5 drivers, PEN matches 1.
        # The PEN-narrow result should be first in candidates.
        idx = SignalIndex()
        for did in ("a", "b", "c", "d", "e"):
            idx.add_rule(SignalRule.for_oui(did, "00:60:74"))
        idx.add_rule(SignalRule.for_snmp_pen("a", 3872))
        m = TierMatcher(idx)

        result = m.match([
            evidence_oui("00:60:74:11:22:33"),
            evidence_snmp_pen(3872),
        ])
        assert result.state == DeviceState.POSSIBLE
        # Narrowest hit (PEN, single candidate "a") leads.
        assert result.candidates[0] == "a"

    def test_no_soft_hit_yields_unknown(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_oui("qsc", "00:60:74"))
        m = TierMatcher(idx)

        result = m.match([evidence_oui("aa:bb:cc:dd:ee:ff")])
        assert result.state == DeviceState.UNKNOWN
        assert result.reason == "no_signal_matched"


class TestTierMatcherUnknown:
    def test_no_evidence_unknown(self):
        m = TierMatcher(SignalIndex())
        result = m.match([])
        assert result.state == DeviceState.UNKNOWN

    def test_evidence_no_rules_unknown(self):
        m = TierMatcher(SignalIndex())
        result = m.match([
            evidence_mdns("_unknown-service._tcp.local."),
            evidence_active_probe("unknown_probe"),
        ])
        assert result.state == DeviceState.UNKNOWN
        # Evidence preserved on unknown match (drives the "what we saw" UI).
        assert len(result.evidence) == 2

    def test_unmatched_strong_still_falls_through_to_unknown(self):
        # Strong-tier signal observed but no rule for it. Soft tier also empty.
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink"))
        m = TierMatcher(idx)

        result = m.match([evidence_active_probe("not_pjlink")])
        assert result.state == DeviceState.UNKNOWN


class TestEvidenceShape:
    """The evidence helpers must produce records the matcher can consume."""

    def test_evidence_mdns_round_trip(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("test_driver", "_test._tcp.local."))
        m = TierMatcher(idx)

        ev = evidence_mdns("_test._tcp.local.", txt={"manufacturer": "Test"})
        result = m.match([ev])
        assert result.state == DeviceState.IDENTIFIED

    def test_evidence_with_txt_filter(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns(
            "filtered_driver", "_http._tcp.local.",
            txt_match={"manufacturer": "Special"},
        ))
        m = TierMatcher(idx)

        # Without the right TXT, falls through.
        result = m.match([evidence_mdns("_http._tcp.local.")])
        assert result.state == DeviceState.UNKNOWN

        # With the right TXT, matches.
        result2 = m.match([
            evidence_mdns("_http._tcp.local.", txt={"manufacturer": "Special"}),
        ])
        assert result2.state == DeviceState.IDENTIFIED


class TestExtractVendorStrings:
    """``extract_vendor_strings`` mines manufacturer/make from probe responses."""

    def test_active_probe_response_manufacturer(self):
        log = [
            evidence_active_probe(
                "pjlink_class1",
                {"manufacturer": "NEC", "model": "PA1004UL"},
            ),
        ]
        out = extract_vendor_strings(log)
        assert len(out) == 1
        assert out[0].data["value"] == "nec"
        assert out[0].data["raw"] == "NEC"
        assert out[0].data["source_probe_id"] == "pjlink_class1"

    def test_broadcast_probe_txt_manufacturer(self):
        log = [
            evidence_broadcast(
                "onvif",
                response={"endpoint": "..."},
                txt={"manufacturer": "Sony"},
            ),
        ]
        out = extract_vendor_strings(log)
        assert len(out) == 1
        assert out[0].data["value"] == "sony"
        assert out[0].data["source_probe_id"] == "onvif"

    def test_mdns_txt_manufacturer(self):
        log = [
            evidence_mdns(
                "_http._tcp.local.",
                txt={"manufacturer": "Shure"},
            ),
        ]
        out = extract_vendor_strings(log)
        assert len(out) == 1
        assert out[0].data["value"] == "shure"

    def test_amx_ddp_make(self):
        log = [evidence_amx_ddp("Polycom", "SoundStructureC*")]
        out = extract_vendor_strings(log)
        assert len(out) == 1
        assert out[0].data["value"] == "polycom"
        assert out[0].data["source_probe_id"] == "Polycom/SoundStructureC*"

    def test_dedups_same_value_same_probe(self):
        # Same probe response yielding manufacturer in both 'manufacturer'
        # and 'make' fields shouldn't emit two records.
        log = [
            evidence_broadcast(
                "onvif",
                response={"manufacturer": "Sony", "make": "SONY"},
            ),
        ]
        out = extract_vendor_strings(log)
        assert len(out) == 1

    def test_does_not_recurse_into_existing_vendor_strings(self):
        # If extract_vendor_strings is called on a log that already has
        # vendor_string enrichment records, it must not consume them as input.
        log = [
            evidence_active_probe("pjlink_class1", {"manufacturer": "NEC"}),
            evidence_vendor_string("NEC", source_probe_id="pjlink_class1"),
        ]
        out = extract_vendor_strings(log)
        # One emission from the strong evidence; the existing enrichment
        # record is ignored, not re-emitted.
        assert len(out) == 1

    def test_empty_or_missing_manufacturer_emits_nothing(self):
        log = [
            evidence_active_probe("pjlink_class1", {"model": "PA1004UL"}),
            evidence_active_probe("extron_sis", {"manufacturer": "  "}),
            evidence_mdns("_test._tcp.local."),
        ]
        assert extract_vendor_strings(log) == []

    def test_distinct_strings_from_same_probe_emit_separately(self):
        # Manufacturer "NEC" + make "Sharp" in one response — distinct values.
        log = [
            evidence_active_probe(
                "pjlink_class1",
                {"manufacturer": "NEC", "make": "Sharp"},
            ),
        ]
        out = extract_vendor_strings(log)
        assert len(out) == 2
        values = sorted(ev.data["value"] for ev in out)
        assert values == ["nec", "sharp"]


class TestDriverCount:
    def test_driver_count_strong(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("a", "_a._tcp.local."))
        idx.add_rule(SignalRule.for_mdns("b", "_b._tcp.local."))
        # Same driver multiple kinds counts once.
        idx.add_rule(SignalRule.for_oui("a", "00:60:74"))
        assert idx.driver_count() == 2

    def test_driver_count_includes_hostname(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_hostname("qsc_core", r"^qsys-"))
        assert idx.driver_count() == 1


class TestAmxDdpGlobMatching:
    """AMX-DDP rules register a ``<make>/<model_glob>`` pattern, but beacons
    carry a concrete model. The matcher must glob-match (case-insensitively),
    not look up by exact key — otherwise the whole DDP identification path is
    dead (audit C5)."""

    def _index(self, *rules: SignalRule) -> SignalIndex:
        idx = SignalIndex()
        for r in rules:
            idx.add_rule(r)
        return idx

    def test_concrete_model_matches_glob(self):
        # The audit's verify case: make + "SoundStructure*" glob, real beacon.
        idx = self._index(
            SignalRule.for_amx_ddp("acme_ss", "AcmeCo", "SoundStructure*"),
        )
        rule = idx.find_strong(KIND_AMX_DDP, "AcmeCo/SoundStructure SR12")
        assert rule is not None
        assert rule.driver_id == "acme_ss"

    def test_match_is_case_insensitive(self):
        idx = self._index(
            SignalRule.for_amx_ddp("acme_ss", "AcmeCo", "SoundStructure*"),
        )
        # Beacon make/model differ in case from the registered rule.
        rule = idx.find_strong(KIND_AMX_DDP, "acmeco/soundstructure sr12")
        assert rule is not None
        assert rule.driver_id == "acme_ss"

    def test_bare_make_wildcard_matches_any_model(self):
        idx = self._index(SignalRule.for_amx_ddp("acme_any", "AcmeCo", "*"))
        rule = idx.find_strong(KIND_AMX_DDP, "AcmeCo/AnyModel-9000")
        assert rule is not None
        assert rule.driver_id == "acme_any"

    def test_non_matching_make_is_unknown(self):
        idx = self._index(
            SignalRule.for_amx_ddp("acme_ss", "AcmeCo", "SoundStructure*"),
        )
        m = TierMatcher(idx)
        result = m.match([evidence_amx_ddp("OtherCo", "SoundStructure SR12")])
        assert result.state == DeviceState.UNKNOWN

    def test_non_matching_model_is_unknown(self):
        idx = self._index(
            SignalRule.for_amx_ddp("acme_ss", "AcmeCo", "SoundStructure*"),
        )
        rule = idx.find_strong(KIND_AMX_DDP, "AcmeCo/Projector-Z")
        assert rule is None

    def test_most_specific_pattern_wins(self):
        # A device matching both a broad and a narrow pattern resolves to the
        # narrower (more literal) one.
        idx = self._index(
            SignalRule.for_amx_ddp("acme_generic", "AcmeCo", "*"),
            SignalRule.for_amx_ddp("acme_ss", "AcmeCo", "SoundStructure*"),
        )
        rule = idx.find_strong(KIND_AMX_DDP, "AcmeCo/SoundStructureC16")
        assert rule is not None
        assert rule.driver_id == "acme_ss"

    def test_end_to_end_match_via_evidence(self):
        idx = self._index(
            SignalRule.for_amx_ddp("acme_ss", "AcmeCo", "SoundStructure*"),
        )
        m = TierMatcher(idx)
        result = m.match([evidence_amx_ddp("AcmeCo", "SoundStructure SR12")])
        assert result.state == DeviceState.IDENTIFIED
        assert result.driver_id == "acme_ss"
