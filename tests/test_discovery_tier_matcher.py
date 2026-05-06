"""Tests for the deterministic TierMatcher.

These tests use synthetic SignalRules — driver hint loading is wired
in Phase 6. The matcher's contract must hold regardless of where rules
come from.
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

        result = m.match([
            evidence_amx_ddp("Polycom", "SoundStructureC*"),
        ])
        assert result.state == DeviceState.IDENTIFIED
        assert result.driver_id == "polycom_ssc"


class TestTierMatcherTierOrdering:
    def test_tier1_beats_tier2(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_mdns("driver_t1", "_qsc._tcp.local."))
        idx.add_rule(SignalRule.for_broadcast("driver_t2", "qsc_qdp"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_broadcast("qsc_qdp"),
            evidence_mdns("_qsc._tcp.local."),
        ])
        # Tier 1 wins regardless of order in the evidence log.
        assert result.driver_id == "driver_t1"

    def test_tier2_beats_tier3(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_broadcast("driver_t2", "pjlink_class2"))
        idx.add_rule(SignalRule.for_active_probe("driver_t3", "pjlink"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_active_probe("pjlink"),
            evidence_broadcast("pjlink_class2"),
        ])
        assert result.driver_id == "driver_t2"

    def test_tier3_beats_tier4_soft(self):
        idx = SignalIndex()
        idx.add_rule(SignalRule.for_active_probe("driver_t3", "extron_sis"))
        idx.add_rule(SignalRule.for_oui("driver_t3", "00:05:a6"))
        m = TierMatcher(idx)

        result = m.match([
            evidence_oui("00:05:a6:11:22:33"),
            evidence_active_probe("extron_sis"),
        ])
        # Strong tier 3 match — not a possible state.
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
