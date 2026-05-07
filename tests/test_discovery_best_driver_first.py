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
    evidence_vendor_string,
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


def test_vendor_string_alone_yields_possible() -> None:
    """No strong probe, only a Tier 4 vendor_string evidence ->
    ``possible`` with the matching driver as candidate. Same shape as
    OUI-only or hostname-only soft matches.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_vendor_string("sharp_nec_projector", "NEC"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_vendor_string("NEC", source_probe_id="pjlink_class1"),
    ])

    assert result.state == DeviceState.POSSIBLE
    assert result.candidates == ["sharp_nec_projector"]
    assert result.source == "vendor_string:nec"


def test_pjlink_plus_vendor_string_picks_vendor() -> None:
    """The Phase 8.6 regression case: PJLink active probe response
    carries ``manufacturer="NEC"``, no OUI is in the catalog for the
    device's actual MAC, but the vendor_string Tier 4 evidence drives
    the matcher to pick sharp_nec_projector and demote PJLink to
    alternative.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink_class1"))
    idx.add_rule(SignalRule.for_vendor_string("sharp_nec_projector", "NEC"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe(
            "pjlink_class1",
            {"manufacturer": "NEC", "model": "PE456_Series"},
        ),
        # Engine emits this Tier 4 record from the probe response —
        # simulated here for unit-level isolation.
        evidence_vendor_string("NEC", source_probe_id="pjlink_class1"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "sharp_nec_projector"
    assert result.alternatives == ["pjlink_class1"]
    assert result.source == "vendor_string:nec"


def test_vendor_string_case_insensitive_match() -> None:
    """Driver declares alias ``"Sharp NEC"``; probe emits ``"sharp nec"``
    (already lowercased by ``evidence_vendor_string``) — must match.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink_class1"))
    idx.add_rule(SignalRule.for_vendor_string(
        "sharp_nec_projector", "Sharp NEC",
    ))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe("pjlink_class1", {"manufacturer": "Sharp NEC"}),
        evidence_vendor_string("Sharp NEC", source_probe_id="pjlink_class1"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "sharp_nec_projector"
    assert result.alternatives == ["pjlink_class1"]


def test_multiple_drivers_share_vendor_alias() -> None:
    """Two drivers both claim ``"Sony"`` — generic PJLink + vendor_string
    evidence yields both as candidates with PJLink trailing. Pins that
    vendor strings behave like OUIs when more than one driver matches.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink_class1"))
    idx.add_rule(SignalRule.for_vendor_string("sony_vpl", "Sony"))
    idx.add_rule(SignalRule.for_vendor_string("sony_bravia_display", "Sony"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe("pjlink_class1", {"manufacturer": "Sony"}),
        evidence_vendor_string("Sony", source_probe_id="pjlink_class1"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    # Primary is the first vendor candidate; the second + the generic
    # PJLink driver fill out alternatives.
    assert result.driver_id in {"sony_vpl", "sony_bravia_display"}
    assert "pjlink_class1" in result.alternatives
    assert any(
        c in result.alternatives for c in ("sony_vpl", "sony_bravia_display")
    )


def test_oui_and_vendor_string_resolve_same_driver_no_dupe() -> None:
    """OUI evidence and vendor_string evidence both point at the same
    vendor driver — the alternatives list shouldn't include the driver
    twice. De-dup is the existing _gather_soft_candidates contract.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe("pjlink_class1", "pjlink_class1"))
    idx.add_rule(SignalRule.for_oui("sharp_nec_projector", "00:30:13"))
    idx.add_rule(SignalRule.for_vendor_string("sharp_nec_projector", "NEC"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe("pjlink_class1", {"manufacturer": "NEC"}),
        evidence_oui("00:30:13:11:22:33"),
        evidence_vendor_string("NEC", source_probe_id="pjlink_class1"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "sharp_nec_projector"
    # sharp_nec_projector appears exactly once across primary + alternatives.
    full = [result.driver_id, *result.alternatives]
    assert full.count("sharp_nec_projector") == 1
    assert "pjlink_class1" in result.alternatives


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
