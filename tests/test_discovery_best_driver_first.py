"""Best-driver-first matching.

When a *generic* strong-tier probe (PJLink, unfiltered ONVIF) wins the
strong-tier race, the matcher consults enrichment soft signals for a
vendor-specific driver. If found, the vendor driver becomes the primary
identification and the generic driver demotes to a trailing alternative.

These tests pin the contract from
``OpenAVC-Discovery-Spec.md`` §6 (Cross-Vendor Demotion):

- Generic + vendor soft candidate -> vendor primary, generic alternative.
- Generic alone -> generic primary, no alternatives (regression guard).
- Vendor-specific strong match -> stands alone, soft signals ignored.
- Filtered ONVIF (txt_match) is vendor-specific by construction.
"""

from __future__ import annotations

from openavc.discovery.result import DeviceState
from openavc.discovery.tier_matcher import (
    SignalIndex,
    SignalRule,
    TierMatcher,
    evidence_active_probe,
    evidence_broadcast,
    evidence_hostname,
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
    idx.add_rule(SignalRule.for_active_probe(
        "pjlink_class1", "pjlink_class1", generic=True,
    ))
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
    idx.add_rule(SignalRule.for_active_probe(
        "pjlink_class1", "pjlink_class1", generic=True,
    ))
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


def test_cross_vendor_broadcast_with_vendor_oui_picks_vendor() -> None:
    """Cross-vendor broadcast (declared ``cross_vendor: true``) + a
    vendor OUI -> identified=vendor_driver,
    alternatives=[cross_vendor_anchor].

    Pins the cross-vendor demotion path: when a generic broadcast
    fingerprint wins but enrichment evidence narrows to a vendor-
    specific driver, that vendor driver becomes primary.
    """
    idx = SignalIndex()
    # Anchor driver flags itself cross-vendor at index-build time.
    idx.add_rule(SignalRule.for_broadcast("anchor_driver", "shared_probe", generic=True))
    idx.add_rule(SignalRule.for_oui("vendor_driver", "00:1e:c0"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_broadcast(
            "shared_probe",
            {"endpoint": "http://10.0.0.5/foo"},
        ),
        evidence_oui("00:1e:c0:aa:bb:cc"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "vendor_driver"
    assert result.alternatives == ["anchor_driver"]
    assert result.source == "oui:00:1e:c0"


def test_filtered_broadcast_is_not_generic() -> None:
    """A broadcast rule with a TXT-match filter is vendor-specific —
    even when an unrelated vendor's OUI is also observed, the filtered
    rule wins the strong tier and stands alone, no alternatives.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_broadcast(
        "vendor_a_driver", "shared_probe",
        txt_match={"manufacturer": "VendorA"},
    ))
    idx.add_rule(SignalRule.for_oui("vendor_b_driver", "00:1e:c0"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_broadcast(
            "shared_probe",
            response={"endpoint": "http://10.0.0.5/foo"},
            txt={"manufacturer": "VendorA"},
        ),
        evidence_oui("00:1e:c0:aa:bb:cc"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "vendor_a_driver"
    assert result.alternatives == []
    assert result.source == "broadcast:shared_probe"


def test_vendor_string_alone_yields_possible() -> None:
    """No strong probe, only an enrichment vendor_string evidence ->
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
    """Regression case: PJLink active probe response carries
    ``manufacturer="NEC"``, no OUI is in the catalog for the device's
    actual MAC, but the vendor_string enrichment evidence drives the
    matcher to pick sharp_nec_projector and demote PJLink to
    alternative.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe(
        "pjlink_class1", "pjlink_class1", generic=True,
    ))
    idx.add_rule(SignalRule.for_vendor_string("sharp_nec_projector", "NEC"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_active_probe(
            "pjlink_class1",
            {"manufacturer": "NEC", "model": "PE456_Series"},
        ),
        # Engine emits this enrichment record from the probe response —
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
    idx.add_rule(SignalRule.for_active_probe(
        "pjlink_class1", "pjlink_class1", generic=True,
    ))
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
    idx.add_rule(SignalRule.for_active_probe(
        "pjlink_class1", "pjlink_class1", generic=True,
    ))
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
    idx.add_rule(SignalRule.for_active_probe(
        "pjlink_class1", "pjlink_class1", generic=True,
    ))
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


def test_narrow_signal_uniquely_corroborating_anchor_keeps_anchor() -> None:
    """B2 regression: when the cross-vendor anchor's own hostname
    pattern uniquely matches but a peer driver shares the broader OUI
    and manufacturer alias, the anchor must win.

    The concrete bug: a Crestron CP3 controller (anchor =
    ``crestron_cip``, cross-vendor) with hostname ``CP3-AB12CD`` matches
    the anchor's broad ``^(MC4|CP3|...)-`` regex and ONLY the anchor's
    pattern. Both ``crestron_cip`` and ``crestron_nvx`` share the
    Crestron OUI and the ``"crestron"`` manufacturer alias. The
    pre-fix demotion logic filtered the anchor out and grabbed the
    only remaining peer (``crestron_nvx``) as primary, mislabeling the
    control system as a video endpoint.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_broadcast(
        "anchor_driver", "shared_probe", generic=True,
    ))
    idx.add_rule(SignalRule.for_hostname("anchor_driver", "^(CP3|TSW)-"))
    # Both drivers share the OUI + manufacturer alias.
    idx.add_rule(SignalRule.for_oui("anchor_driver", "00:10:7f"))
    idx.add_rule(SignalRule.for_oui("peer_driver", "00:10:7f"))
    idx.add_rule(SignalRule.for_vendor_string("anchor_driver", "vendor"))
    idx.add_rule(SignalRule.for_vendor_string("peer_driver", "vendor"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_broadcast("shared_probe", {"endpoint": "10.0.0.5"}),
        evidence_hostname("CP3-AB12CD", matched_pattern="^(CP3|TSW)-"),
        evidence_oui("00:10:7f:11:22:33"),
        evidence_vendor_string("vendor", source_probe_id="shared_probe"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "anchor_driver"
    assert result.alternatives == []
    assert result.source == "broadcast:shared_probe"


def test_narrow_signal_uniquely_pointing_to_peer_demotes_anchor() -> None:
    """Companion B2 case: when the narrowest signal points uniquely to
    a peer (not the anchor), the demotion still happens.

    A Crestron DM-NVX-AB12CD device matches both ``crestron_cip``'s
    broad hostname regex and ``crestron_nvx``'s ``^DM-NVX-`` regex
    — at the rule-evaluation level both hostname rules fire — so the
    test is really about a tighter rule belonging to the peer winning
    out. We model this by giving only the peer a hostname rule.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_broadcast(
        "anchor_driver", "shared_probe", generic=True,
    ))
    idx.add_rule(SignalRule.for_hostname("peer_driver", "^DM-NVX-"))
    idx.add_rule(SignalRule.for_oui("anchor_driver", "00:10:7f"))
    idx.add_rule(SignalRule.for_oui("peer_driver", "00:10:7f"))
    matcher = TierMatcher(idx)

    result = matcher.match([
        evidence_broadcast("shared_probe", {"endpoint": "10.0.0.5"}),
        evidence_hostname("DM-NVX-AB12CD", matched_pattern="^DM-NVX-"),
        evidence_oui("00:10:7f:11:22:33"),
    ])

    assert result.state == DeviceState.IDENTIFIED
    assert result.driver_id == "peer_driver"
    assert result.alternatives == ["anchor_driver"]
    assert result.source == "hostname:DM-NVX-AB12CD"


def test_multi_vendor_oui_orders_by_specificity() -> None:
    """Two vendor drivers share an OUI; the one that *also* matches a
    narrower soft signal (SNMP PEN) leads, the broader OUI-only driver
    follows, and the generic PJLink driver trails. Pins the narrowest-
    first ordering of ``_gather_soft_candidates``.
    """
    idx = SignalIndex()
    idx.add_rule(SignalRule.for_active_probe(
        "pjlink_class1", "pjlink_class1", generic=True,
    ))
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
