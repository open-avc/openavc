"""Tests for the Tier 2 broadcast probes (PJLink Class 2 + Crestron CIP).

The probes themselves require live UDP sockets and broadcast traffic,
so the integration paths are exercised in Phase 8 with the simulator.
These tests cover the parsers (the load-bearing logic) plus the helper
utilities that compute broadcast addresses, build sockets, and emit
Evidence records.
"""

import pytest

from server.discovery.broadcast_probes import (
    CRESTRON_CIP_PORT,
    CRESTRON_CIP_PROBE,
    PJLINK_PORT,
    PJLINK_SRCH_REQUEST,
    PJLinkClass2Reply,
    _broadcast_addresses_for,
    _parse_crestron_cip,
    _parse_pjlink_ackn,
)
from server.discovery.result import SignalTier


# ===== PJLink Class 2 SRCH =====


class TestPJLinkSRCHConstants:
    def test_request_format_matches_spec(self):
        # Per JBMIA spec v2.10: %2SRCH<CR>. No CRLF, no LF-only.
        assert PJLINK_SRCH_REQUEST == b"%2SRCH\r"

    def test_port_is_4352(self):
        assert PJLINK_PORT == 4352


class TestParsePJLinkAckn:
    def test_standard_response(self):
        # %2ACKN=001122aabbcc<CR>
        data = b"%2ACKN=001122aabbcc\r"
        reply = _parse_pjlink_ackn(data, "10.0.0.50")
        assert reply is not None
        assert reply.ip == "10.0.0.50"
        assert reply.mac == "001122aabbcc"

    def test_uppercase_mac_normalized_to_lowercase(self):
        data = b"%2ACKN=001122AABBCC\r"
        reply = _parse_pjlink_ackn(data, "10.0.0.50")
        assert reply is not None
        assert reply.mac == "001122aabbcc"

    def test_trailing_whitespace_tolerated(self):
        data = b"%2ACKN=001122aabbcc \r\n"
        reply = _parse_pjlink_ackn(data, "10.0.0.50")
        assert reply is not None
        assert reply.mac == "001122aabbcc"

    def test_invalid_payload_returns_none(self):
        for bogus in (
            b"",
            b"random garbage",
            b"%1ACKN=001122aabbcc",  # Class 1 has no SRCH
            b"%2ACKN=GGGGGGGGGGGG",  # non-hex
            b"%2ACKN=00112233",       # too short
        ):
            assert _parse_pjlink_ackn(bogus, "10.0.0.50") is None


class TestPJLinkReplyAccessors:
    def test_to_evidence_emits_tier2(self):
        reply = PJLinkClass2Reply(ip="10.0.0.50", mac="001122aabbcc")
        ev = reply.to_evidence()
        assert ev.tier == SignalTier.BROADCAST_PROBE
        assert ev.source == "broadcast:pjlink_class2"
        assert ev.data["response"]["mac"] == "001122aabbcc"

    def test_to_device_info_formats_mac(self):
        reply = PJLinkClass2Reply(ip="10.0.0.50", mac="001122aabbcc")
        info = reply.to_device_info()
        assert info["mac"] == "00:11:22:aa:bb:cc"
        assert info["protocols"] == ["pjlink"]


# ===== Crestron CIP =====


class TestCrestronCIPConstants:
    def test_probe_is_one_byte(self):
        assert CRESTRON_CIP_PROBE == b"\x14"

    def test_port_is_41794(self):
        assert CRESTRON_CIP_PORT == 41794


class TestParseCrestronCIP:
    def _build_reply(
        self,
        hostname: str = "DIN-AP-7F74F65F",
        model: str = "DIN-AP3",
        firmware: str = "v1.502.0058.001",
    ) -> bytes:
        # Synthesize a minimal but realistic CIP discovery response.
        # Header byte 0x15, 9 zero bytes (per spec preamble), then a
        # 16-byte hostname field, NUL-padded.
        hostname_bytes = hostname.encode("ascii")[:16].ljust(16, b"\x00")
        header = bytes([0x15]) + b"\x00" * 9
        # Tail: NUL-separated fields the parser scans for printable runs.
        tail = (
            b"\x00" * 4
            + model.encode("ascii") + b"\x00"
            + b"\x00" * 8
            + firmware.encode("ascii") + b"\x00"
            + b"2024-03-15\x00"
            + b"SN-12345\x00"
        )
        return header + hostname_bytes + tail

    def test_parses_hostname(self):
        data = self._build_reply()
        reply = _parse_crestron_cip(data, "192.168.1.50")
        assert reply is not None
        assert reply.ip == "192.168.1.50"
        assert reply.hostname == "DIN-AP-7F74F65F"

    def test_extracts_model_and_firmware(self):
        data = self._build_reply()
        reply = _parse_crestron_cip(data, "192.168.1.50")
        assert reply is not None
        assert reply.model == "DIN-AP3"
        assert reply.firmware == "1.502.0058.001"

    def test_to_device_info_complete(self):
        data = self._build_reply()
        reply = _parse_crestron_cip(data, "192.168.1.50")
        info = reply.to_device_info()
        assert info["manufacturer"] == "Crestron"
        assert info["category"] == "control"
        assert info["device_name"] == "DIN-AP-7F74F65F"
        assert info["hostname"] == "DIN-AP-7F74F65F"
        assert info["model"] == "DIN-AP3"
        assert info["firmware"] == "1.502.0058.001"
        assert info["protocols"] == ["crestron_cip"]

    def test_returns_none_without_magic(self):
        # Random UDP traffic on the listening port must not be mis-parsed.
        assert _parse_crestron_cip(b"", "192.168.1.50") is None
        assert _parse_crestron_cip(b"\x00garbage", "192.168.1.50") is None
        assert _parse_crestron_cip(b"\x14response", "192.168.1.50") is None  # 0x14 is the request, not response

    def test_short_response_still_parses(self):
        # Some non-controller endpoints (DM-NVX, TSW) return a shorter
        # payload. Verify we don't crash and at least extract what we can.
        short = bytes([0x15]) + b"\x00" * 5
        reply = _parse_crestron_cip(short, "192.168.1.50")
        # Magic matched -> returns a reply object even if metadata empty.
        assert reply is not None
        assert reply.ip == "192.168.1.50"

    def test_to_evidence_emits_tier2(self):
        data = self._build_reply()
        reply = _parse_crestron_cip(data, "192.168.1.50")
        ev = reply.to_evidence()
        assert ev.tier == SignalTier.BROADCAST_PROBE
        assert ev.source == "broadcast:crestron_cip"


# ===== Helpers =====


class TestBroadcastAddresses:
    def test_ipv4_24(self):
        assert _broadcast_addresses_for(["192.168.1.0/24"]) == ["192.168.1.255"]

    def test_ipv4_22(self):
        # /22 covers 192.168.4.0 - 192.168.7.255
        assert _broadcast_addresses_for(["192.168.4.0/22"]) == ["192.168.7.255"]

    def test_invalid_cidr_skipped(self):
        result = _broadcast_addresses_for(["not.a.cidr", "192.168.1.0/24"])
        assert result == ["192.168.1.255"]

    def test_p2p_subnets_skipped(self):
        # /31 and /32 have no useful broadcast.
        assert _broadcast_addresses_for(["10.0.0.0/31"]) == []
        assert _broadcast_addresses_for(["10.0.0.5/32"]) == []

    def test_multiple_subnets(self):
        result = _broadcast_addresses_for([
            "192.168.1.0/24",
            "10.0.0.0/24",
        ])
        assert sorted(result) == ["10.0.0.255", "192.168.1.255"]


# ===== Smoke =====


@pytest.mark.asyncio
class TestProbeSmoke:
    async def test_probe_pjlink_no_subnets(self):
        from server.discovery.broadcast_probes import probe_pjlink_class2

        result = await probe_pjlink_class2([], duration=0.1)
        assert result == {}

    async def test_probe_crestron_no_targets(self):
        from server.discovery.broadcast_probes import probe_crestron_cip

        result = await probe_crestron_cip([], duration=0.1)
        assert result == {}
