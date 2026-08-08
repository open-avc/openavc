"""Probe-engine feature tests (vendor-neutral).

These validate the discovery probe *runtime* with invented devices and
synthetic payloads:

  * ``server.discovery.hints.parse_driver_discovery`` — how a declared
    ``tcp_probe`` / ``udp_probe`` block is parsed (port, send, matcher, tls,
    extract rules, and the schema's error cases).
  * ``server.discovery.probe_runner._matches`` / ``_apply_extract`` — how a
    response is matched (``expect`` / ``expect_hex`` / ``expect_regex``) and
    mined (static + regex extract, reserved manufacturer/make lifting).

There are deliberately no real product names, captured fixtures, or reads of
the community drivers repo here. Core tests the *feature*; validation that a
specific driver's probe matches a real captured response lives next to that
driver in openavc-drivers (``tests/test_discovery_probe_fixtures.py``). See the
testing rule in CLAUDE.md.
"""

from __future__ import annotations

import pytest

from openavc.discovery.hints import DiscoveryHintError, parse_driver_discovery
from openavc.discovery.probe_runner import _apply_extract, _matches


def _driver(discovery: dict, driver_id: str = "acme_widget") -> dict:
    """A minimal driver-info dict carrying a discovery block.

    ``Acme`` is an invented vendor — the point is to exercise the engine, not
    any real device.
    """
    return {
        "id": driver_id,
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "discovery": discovery,
    }


def _tcp(**block):
    """Parse a tcp_probe block and return its CustomProbeSpec."""
    return parse_driver_discovery(_driver({"tcp_probe": block})).tcp_probe


# ─────────────────────────── Parsing ───────────────────────────


class TestProbeParsing:
    def test_tcp_ascii_send_and_expect(self):
        spec = _tcp(port=4321, send_ascii="ID?\r", expect="ACME")
        assert spec is not None
        assert spec.kind == "tcp"
        assert spec.port == 4321
        assert spec.send == b"ID?\r"
        assert spec.response_match.contains == "ACME"

    def test_tcp_hex_send_and_expect_hex(self):
        spec = _tcp(port=5000, send_hex="aa01", expect_hex="bb02")
        assert spec.send == bytes.fromhex("aa01")
        assert spec.response_match.starts_with == bytes.fromhex("bb02")

    def test_tcp_expect_regex_compiles(self):
        spec = _tcp(port=23, send_ascii="x", expect_regex=r"v(\d+\.\d+)")
        assert spec.response_match.regex is not None
        assert spec.response_match.regex_source == r"v(\d+\.\d+)"

    def test_udp_probe_parsed(self):
        hint = parse_driver_discovery(_driver({
            "udp_probe": {"port": 6454, "send_hex": "4172742d", "expect_hex": "4172742d"}
        }))
        assert hint.udp_probe is not None
        assert hint.udp_probe.kind == "udp"
        assert hint.udp_probe.port == 6454

    def test_tls_flag_parsed_on_tcp(self):
        spec = _tcp(port=443, tls=True, send_ascii="GET /\r\n\r\n", expect="OK")
        assert spec.tls is True

    def test_default_timeout_differs_by_kind(self):
        tcp = _tcp(port=1, send_ascii="x", expect="A")
        udp = parse_driver_discovery(_driver({
            "udp_probe": {"port": 1, "send_hex": "00", "expect_hex": "00"}
        })).udp_probe
        assert tcp.timeout_ms == 3000
        assert udp.timeout_ms == 2000

    def test_connect_only_tcp_probe_allowed(self):
        # No send, no matcher — a banner-grab probe.
        spec = _tcp(port=9999)
        assert spec is not None
        assert spec.send == b""

    def test_cross_vendor_flag(self):
        spec = _tcp(port=1, send_ascii="x", expect="A", cross_vendor=True)
        assert spec.cross_vendor is True

    # ── extract parsing ──

    def test_extract_static_value(self):
        spec = _tcp(port=1, send_ascii="x", expect="A", extract={"region": "us"})
        rules = {r.field_name: r for r in spec.extract}
        assert rules["region"].value == "us"

    def test_extract_regex_rule(self):
        spec = _tcp(
            port=1, send_ascii="x", expect="A",
            extract={"model": {"regex": r"model=(\w+)", "group": 1}},
        )
        rules = {r.field_name: r for r in spec.extract}
        assert rules["model"].regex is not None
        assert rules["model"].group == 1

    def test_extract_manufacturer_sugar(self):
        spec = _tcp(port=1, send_ascii="x", expect="A", extract_manufacturer="Acme")
        assert any(
            r.field_name == "manufacturer" and r.value == "Acme" for r in spec.extract
        )

    def test_generic_driver_opts_out(self):
        assert parse_driver_discovery(
            _driver({"tcp_probe": {"port": 1}}, driver_id="generic_tcp")
        ) is None

    # ── error cases ──

    def test_tls_rejected_on_udp(self):
        with pytest.raises(DiscoveryHintError, match="tls"):
            parse_driver_discovery(_driver({
                "udp_probe": {"port": 1, "tls": True, "send_hex": "00", "expect_hex": "00"}
            }))

    def test_udp_without_send_rejected(self):
        with pytest.raises(DiscoveryHintError):
            parse_driver_discovery(_driver({
                "udp_probe": {"port": 1, "expect_hex": "00"}
            }))

    def test_multiple_matchers_rejected(self):
        with pytest.raises(DiscoveryHintError, match="exactly one"):
            _tcp(port=1, send_ascii="x", expect="A", expect_regex="B")

    def test_tcp_send_without_matcher_rejected(self):
        with pytest.raises(DiscoveryHintError, match="no matcher"):
            _tcp(port=1, send_ascii="x")

    def test_both_send_forms_rejected(self):
        with pytest.raises(DiscoveryHintError, match="pick one"):
            _tcp(port=1, send_ascii="x", send_hex="00", expect="A")

    def test_unknown_probe_key_rejected(self):
        with pytest.raises(DiscoveryHintError, match="unknown keys"):
            _tcp(port=1, send_ascii="x", expect="A", bogus=True)

    def test_disallowed_generic_open_port_rejected(self):
        with pytest.raises(DiscoveryHintError, match="too generic"):
            parse_driver_discovery(_driver({"port_open": [80]}))


# ─────────────────────────── Matching ───────────────────────────


class TestMatcher:
    def _match(self, **block):
        return _tcp(port=1, send_ascii="x", **block).response_match

    def test_contains_ascii(self):
        m = self._match(expect="ACME")
        assert _matches(b"hello ACME-1000 ready", m) is True
        assert _matches(b"nope", m) is False

    def test_contains_matches_inside_binary(self):
        # contains tries the utf-8 bytes first, then latin-1 text.
        m = self._match(expect="ACME")
        assert _matches(b"\x00\x01ACME\xff", m) is True

    def test_starts_with_hex_prefix_only(self):
        m = self._match(expect_hex="aabb")
        assert _matches(b"\xaa\xbb\x01\x02", m) is True
        assert _matches(b"\x01\xaa\xbb", m) is False  # not at the start

    def test_regex(self):
        m = self._match(expect_regex=r"FW (\d+\.\d+)")
        assert _matches(b"unit FW 1.20 ok", m) is True
        assert _matches(b"unit FW x", m) is False

    def test_connect_only_matches_anything(self):
        # An empty ResponseMatch (banner-grab probe) accepts any payload.
        m = _tcp(port=1).response_match
        assert _matches(b"anything", m) is True
        assert _matches(b"", m) is True


# ─────────────────────────── Extract ───────────────────────────


class TestExtract:
    def _rules(self, **block):
        return _tcp(port=1, send_ascii="x", expect="A", **block).extract

    def test_static_value(self):
        reserved, extracted = _apply_extract(b"whatever", self._rules(extract={"region": "us"}))
        assert extracted["region"] == "us"
        assert reserved == {}

    def test_regex_group(self):
        rules = self._rules(extract={"model": {"regex": r"MODEL:(\w+)", "group": 1}})
        reserved, extracted = _apply_extract(b"MODEL:WIDGET9 v1", rules)
        assert extracted["model"] == "WIDGET9"

    def test_reserved_manufacturer_lifted(self):
        reserved, extracted = _apply_extract(b"x", self._rules(extract_manufacturer="Acme"))
        assert reserved["manufacturer"] == "Acme"
        assert "manufacturer" not in extracted

    def test_regex_miss_skips_field(self):
        rules = self._rules(extract={"model": {"regex": r"NOPE:(\w+)", "group": 1}})
        reserved, extracted = _apply_extract(b"no match here", rules)
        assert "model" not in extracted

    def test_no_rules_returns_empty(self):
        reserved, extracted = _apply_extract(b"x", ())
        assert reserved == {} and extracted == {}


class TestVendorNarrowingContract:
    """Cross-vendor narrowing depends on the extracted manufacturer/make value
    appearing (case-insensitively) in the driver's declared manufacturer_alias.
    Validate that contract generically so the feature can't silently regress."""

    def test_extracted_manufacturer_matches_declared_alias(self):
        hint = parse_driver_discovery(_driver({
            "tcp_probe": {
                "port": 1, "send_ascii": "x", "expect": "ACME",
                "extract_manufacturer": "Acme Corp",
            },
            "manufacturer_alias": ["Acme Corp", "Acme"],
        }))
        reserved, _ = _apply_extract(b"ACME ready", hint.tcp_probe.extract)
        vendor = (reserved.get("manufacturer") or reserved.get("make") or "").strip().lower()
        # parse normalizes aliases to lowercase.
        assert vendor in set(hint.manufacturer_alias)
