"""Best-driver-first matching (Phase 8.5).

When a *generic* strong-tier probe (PJLink, unfiltered ONVIF) wins the
strong-tier race, the matcher consults Tier 4 soft signals for a
vendor-specific driver. If found, the vendor driver becomes the primary
identification and the generic driver demotes to a trailing alternative.

These tests pin the contract from
``discovery-redesign-plan.md`` §"Phase 8.5 — Best-driver-first matching":

- Generic + vendor soft candidate -> vendor primary, generic alternative.
- Generic alone -> generic primary, no alternatives (regression guard).
- Vendor-specific strong match -> stands alone, soft signals ignored.
- Filtered ONVIF (txt_match) is vendor-specific by construction.
"""

from __future__ import annotations

from server.discovery.result import DeviceState
from server.discovery.tier_matcher import (
    SignalIndex,
    SignalRule,
    TierMatcher,
    evidence_active_probe,
    evidence_broadcast,
    evidence_oui,
    evidence_snmp_pen,
)


def test_generic_pjlink_with_vendor_oui_picks_vendor() -> None:
    """PJLink Class 1 active probe + NEC OUI evidence ->
    identified=sharp_nec_projector, alternatives=[pjlink_class1].

    The regression case from the plan: NEC projector on the network
    must surface the brand-specific driver, not the generic PJLink one.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink_class1"))
    idx.add_rule(SignalRule.for_oui("sharp_nec_projector", "00:30:13"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe(
            "pjlink_class1",
            {"manufacturer": "NEC", "model": "PA1004UL"},
        ),
        evidence_oui("00:30:13:11:22:33"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "sharp_nec_projector"
    assert result.alternatives == ["pjlink_class1"]
    assert result.source == "oui:00:30:13"


def test_generic_pjlink_alone_identifies_pjlink() -> None:
    """PJLink Class 1 alone, no OUI -> identified=pjlink_class1,
    alternatives=[]. Regression guard for the no-OUI case.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink_class1"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe(
            "pjlink_class1",
            {"manufacturer": "Generic", "model": "PJLink"},
        ),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "pjlink_class1"
    assert result.alternatives == []
    assert result.source == "probe:pjlink_class1"


def test_vendor_specific_probe_ignores_soft_candidates() -> None:
    """Extron SIS active probe (vendor-specific) + Extron OUI ->
    identified=extron_dtp_cross_point, alternatives=[]. The strong
    vendor signal stands alone; soft signals are silently dropped.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe(
        "extron_dtp_cross_point", "extron_sis",
    ))
    idx.add_rule(SignalRule.for_oui("extron_dtp_cross_point", "00:05:a6"))
    # An unrelated driver also matching the OUI must not leak in.
    idx.add_rule(SignalRule.for_oui("extron_other", "00:05:a6"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe("extron_sis", {"part": "DTP CrossPoint"}),
        evidence_oui("00:05:a6:11:22:33"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "extron_dtp_cross_point"
    assert result.alternatives == []
    assert result.source == "probe:extron_sis"


def test_unfiltered_onvif_with_vendor_oui_picks_vendor() -> None:
    """Unfiltered ONVIF broadcast + Vaddio OUI ->
    identified=vaddio_roboshot, alternatives=[generic_onvif_camera].

    Pins that ``onvif`` broadcast probe is generic when no txt_match
    constrains it, and the vendor-specific OUI driver wins primary.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_broadcast("generic_onvif_camera", "onvif"))
    idx.add_rule(SignalRule.for_oui("vaddio_roboshot", "00:1e:c0"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_broadcast("onvif", {"endpoint": "http://10.0.0.5/onvif/device_service"}),
        evidence_oui("00:1e:c0:aa:bb:cc"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "vaddio_roboshot"
    assert result.alternatives == ["generic_onvif_camera"]
    assert result.source == "oui:00:1e:c0"


def test_filtered_onvif_is_not_generic() -> None:
    """ONVIF with a manufacturer txt_match filter is vendor-specific —
    even when a Vaddio OUI is also observed, the filtered Sony match
    wins the strong tier and stands alone, no alternatives.

    Pins the txt_match-aware ``generic`` tagging logic from Task 8.5.1.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_broadcast(
        "sony_onvif_camera", "onvif",
        txt_match={"manufacturer": "Sony"},
    ))
    idx.add_rule(SignalRule.for_oui("vaddio_roboshot", "00:1e:c0"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_broadcast(
            "onvif",
            response={"endpoint": "http://10.0.0.5/onvif/device_service"},
            txt={"manufacturer": "Sony"},
        ),
        evidence_oui("00:1e:c0:aa:bb:cc"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "sony_onvif_camera"
    assert result.alternatives == []
    assert result.source == "broadcast:onvif"


def test_multi_vendor_oui_orders_by_specificity() -> None:
    """Two vendor drivers share an OUI; the one that *also* matches a
    narrower soft signal (SNMP PEN) leads, the broader OUI-only driver
    follows, and the generic PJLink driver trails. Pins the narrowest-
    first ordering of ``_gather_soft_candidates``.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink_class1"))
    # Both vendors share the OUI...
    idx.add_rule(SignalRule.for_oui("vendor_a_projector", "00:60:b9"))
    idx.add_rule(SignalRule.for_oui("vendor_b_projector", "00:60:b9"))
    # ...but only vendor_b also declares a SNMP PEN, making it the
    # narrowest soft hit when the device responds with that PEN.
    idx.add_rule(SignalRule.for_snmp_pen("vendor_b_projector", 12345))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe("pjlink_class1", {"manufacturer": "Other"}),
        evidence_oui("00:60:b9:11:22:33"),
        evidence_snmp_pen(12345, sysdescr="Vendor B Projector"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "vendor_b_projector"
    # vendor_a (broader OUI hit) follows, generic pjlink trails.
    assert result.alternatives == ["vendor_a_projector", "pjlink_class1"]
    assert result.source == "snmp_pen:12345"
