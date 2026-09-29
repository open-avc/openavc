"""Soft discovery hints surface a device as POSSIBLE (vendor-neutral).

A "soft" hint — OUI, hostname pattern, or an observed open port — never
identifies a device on its own, but each must be enough to surface it as a
``possible`` candidate so the user gets a suggestion to act on. The index and
lookup contract is covered structurally in ``test_discovery_tier_matcher.py``;
this file pins the end-to-end ``TierMatcher.match([one soft evidence]) ->
POSSIBLE`` path.

Uses invented devices and hint values — no real product, no read of the
community drivers repo. Validation that a *specific* shipped driver participates
in matching lives next to that driver in openavc-drivers.
"""

from __future__ import annotations

from openavc.discovery.hints import build_signal_index, parse_driver_discovery
from openavc.discovery.result import DeviceState
from openavc.discovery.tier_matcher import (
    TierMatcher,
    evidence_hostname,
    evidence_open_port,
    evidence_oui,
)


def _matcher(discovery: dict, driver_id: str = "acme_widget") -> TierMatcher:
    """Build a TierMatcher over one synthetic driver with the given hints."""
    hint = parse_driver_discovery({
        "id": driver_id,
        "name": "Acme Widget",
        "discovery": discovery,
    })
    assert hint is not None
    return TierMatcher(build_signal_index([hint]))


def test_oui_hint_produces_possible():
    """An OUI hint alone surfaces the driver as a possible candidate."""
    matcher = _matcher({"oui": ["00:0e:dd", "d8:34:ee"]})
    for mac in ("00:0e:dd:11:22:33", "d8:34:ee:44:55:66"):
        result = matcher.match([evidence_oui(mac)])
        assert result.state == DeviceState.POSSIBLE, (
            f"MAC {mac} did not produce POSSIBLE "
            f"(got {result.state}, candidates={result.candidates})"
        )
        assert "acme_widget" in result.candidates


def test_oui_hint_matches_regardless_of_separator_style():
    """A non-canonical OUI hint (bare hex, dashed, dotted) still matches an
    observed MAC in any format — registration and lookup canonicalize to the
    same key. Regression for hints silently ignored unless already xx:xx:xx.
    """
    for hint_val in ("001122", "00-11-22", "0011.22"):
        matcher = _matcher({"oui": [hint_val]})
        for mac in ("00:11:22:33:44:55", "00-11-22-33-44-55", "0011.2233.4455"):
            result = matcher.match([evidence_oui(mac)])
            assert result.state == DeviceState.POSSIBLE, (
                f"hint {hint_val!r} vs MAC {mac!r} did not produce POSSIBLE "
                f"(got {result.state}, candidates={result.candidates})"
            )
            assert "acme_widget" in result.candidates


def test_a_medium_ieee_block_claims_only_the_macs_inside_it():
    """A maker whose only IEEE block is an MA-M claims its 28 bits, not the
    /24 the Registration Authority split among other companies."""
    matcher = _matcher({"oui": ["00:11:22:4"]})
    inside = matcher.match([evidence_oui("00:11:22:4a:bb:cc")])
    assert inside.state == DeviceState.POSSIBLE and "acme_widget" in inside.candidates
    outside = matcher.match([evidence_oui("00:11:22:5a:bb:cc")])
    assert "acme_widget" not in outside.candidates


def test_the_longest_declared_block_wins_over_a_claim_on_the_whole_24():
    widget = parse_driver_discovery({
        "id": "acme_widget", "name": "Acme Widget", "discovery": {"oui": ["00:11:22:4"]},
    })
    wide = parse_driver_discovery({
        "id": "bolt_panel", "name": "Bolt Panel", "discovery": {"oui": ["00:11:22"]},
    })
    index = build_signal_index([widget, wide])
    assert index.find_soft_oui_block("00:11:22:4a:bb:cc") == ("00:11:22:4", ["acme_widget"])
    assert index.find_soft_oui_block("00:11:22:5a:bb:cc") == ("00:11:22", ["bolt_panel"])
    assert index.find_soft_oui_block("00:11:22:4") == ("00:11:22:4", ["acme_widget"])
    assert index.find_soft_oui_block("99:88:77:66:55:44") == ("99:88:77", [])
    result = TierMatcher(index).match([evidence_oui("00:11:22:4a:bb:cc")])
    assert result.candidates == ["acme_widget"]


def test_invalid_oui_entry_is_skipped_not_fatal():
    """A garbage OUI entry is dropped (with a warning); the driver's other
    hints still load — parse must not reject the whole driver over one bad OUI.
    """
    hint = parse_driver_discovery({
        "id": "acme_widget",
        "name": "Acme Widget",
        "discovery": {"oui": ["nope", "00:11:22"], "port_open": [9876]},
    })
    assert hint is not None
    assert hint.oui == ["00:11:22"]  # garbage dropped, valid kept
    assert 9876 in hint.port_open


def test_hostname_hint_produces_possible():
    """A hostname pattern hint alone surfaces the driver as possible."""
    matcher = _matcher({"hostname": ["acme-*", "widget-*"]})
    for host in ("acme-AABBCC", "widget-1"):
        result = matcher.match([evidence_hostname(host)])
        assert result.state == DeviceState.POSSIBLE, (
            f"hostname {host!r} did not produce POSSIBLE "
            f"(got {result.state}, candidates={result.candidates})"
        )
        assert "acme_widget" in result.candidates


def test_open_port_hint_produces_possible():
    """An observed open port hint alone surfaces the driver as possible."""
    matcher = _matcher({"port_open": [9876]})
    result = matcher.match([evidence_open_port(9876)])
    assert result.state == DeviceState.POSSIBLE
    assert "acme_widget" in result.candidates
