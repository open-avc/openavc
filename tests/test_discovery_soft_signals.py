"""Soft discovery hints surface a device as POSSIBLE (vendor-neutral).

A "soft" hint — OUI, hostname pattern, or an observed open port — never
identifies a device on its own, but each must be enough to surface it as a
``possible`` candidate so the user gets a suggestion to act on. The index and
lookup contract is covered structurally in ``test_discovery_tier_matcher.py``;
this file pins the end-to-end ``TierMatcher.match([one soft evidence]) ->
POSSIBLE`` path.

Uses invented devices and hint values — no real product, no read of the
community drivers repo. Validation that a *specific* shipped driver participates
in matching lives next to that driver in openavc-drivers. See CLAUDE.md.
"""

from __future__ import annotations

from server.discovery.hints import build_signal_index, parse_driver_discovery
from server.discovery.result import DeviceState
from server.discovery.tier_matcher import (
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
