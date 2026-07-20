"""Driver-declared probe tests — schema parsing, runner unit tests, end-to-end.

Covers:

- Schema parser: well-formed ``tcp_probe`` / ``udp_probe`` / ``python``
  blocks land on ``DiscoveryHint`` correctly; cross_vendor flag flows
  through to ``SignalRule.generic`` at index-build time.
- ``probe_runner``: response matching, extract rules, rate limiter
  pacing, source-IP binding.
- ``_discovery.py`` companion loader + ``ProbeContext`` + hard timeout.
- End-to-end integration: a synthetic UDP responder identifies via a
  declared ``udp_probe``; same for TCP.
- Cross-vendor demotion integration: a cross_vendor ``tcp_probe`` +
  vendor-specific peer with ``manufacturer_alias`` produces the
  best-driver-first result.
- Vendor-string integration: a probe whose ``extract:`` populates
  manufacturer correctly emits ``vendor_string`` evidence.
- Network-safety regression: probe sockets bind to source_ip.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import threading
import time
from unittest.mock import patch

import pytest

from server.discovery.companion import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ProbeContext,
    load_discovery_companions,
    run_companion,
)
from server.discovery.hints import (
    DiscoveryHintError,
    build_signal_index,
    parse_driver_discovery,
)
from server.discovery import probe_runner as probe_runner_mod
from server.discovery.probe_runner import (
    _MAX_PROBE_RESPONDERS,
    RateLimiter,
    _apply_extract,
    _make_udp_socket,
    _matches,
    run_tcp_active_probe,
    run_udp_broadcast_probe,
)
from server.discovery.result import DeviceState, Evidence
from server.discovery.tier_matcher import (
    TierMatcher,
    extract_vendor_strings,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

def _make_hint(driver_id: str, **discovery):
    """Helper: build a driver registry entry with the given discovery block."""
    return parse_driver_discovery({
        "id": driver_id,
        "name": driver_id,
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "tcp",
        "discovery": discovery,
    })


def _udp_responder(port: int, query_match: bytes, reply: bytes) -> threading.Thread:
    """Spawn a one-shot UDP server that replies to ``query_match`` packets.

    Binds synchronously so callers can probe the moment this returns; the
    background thread just does recvfrom on the already-bound socket. Without
    the sync bind, Linux CI runners are slow enough that the probe goes out
    before the thread's bind() completes and the test KeyErrors.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    s.settimeout(3.0)

    def serve():
        try:
            data, addr = s.recvfrom(2048)
            if query_match in data:
                s.sendto(reply, addr)
        except OSError:
            pass
        finally:
            s.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


def _tcp_responder(port: int, reply: bytes) -> threading.Thread:
    """Spawn a one-shot TCP server that sends ``reply`` after first read.

    Binds and listens synchronously to avoid the same race that bit the UDP
    helper on slow Linux CI runners.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    s.settimeout(4.0)

    def serve():
        try:
            conn, _ = s.accept()
            try:
                conn.settimeout(2.0)
                try:
                    conn.recv(2048)
                except OSError:
                    pass
                conn.sendall(reply)
            finally:
                conn.close()
        except OSError:
            pass
        finally:
            s.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


def _tcp_responder_segments(
    port: int, segments: list[bytes], *, gap: float = 0.2,
) -> threading.Thread:
    """Spawn a one-shot TCP server that emits ``segments`` as separate sends.

    Simulates a device whose identifying banner arrives in a later TCP
    segment than its first — e.g. a telnet controller that sends IAC
    negotiation in one segment and the welcome line in the next. Does not
    read from the client (connect-only banner-grab style), then closes.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    s.settimeout(4.0)

    def serve():
        try:
            conn, _ = s.accept()
            try:
                for i, seg in enumerate(segments):
                    if i > 0:
                        time.sleep(gap)
                    conn.sendall(seg)
            finally:
                conn.close()
        except OSError:
            pass
        finally:
            s.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


_NEXT_PORT = [39600]


def _next_port() -> int:
    """Hand out a unique port per call so concurrent tests don't collide."""
    p = _NEXT_PORT[0]
    _NEXT_PORT[0] += 1
    return p


# ---------------------------------------------------------------------------
# Schema parser — sanity checks for the probe blocks
# ---------------------------------------------------------------------------


class TestProbeSchemaParsing:
    def test_full_udp_block_parses(self):
        # B9: at most one of expect / expect_regex / expect_hex per probe.
        # Pick expect_regex here — it's the most expressive of the three
        # for a structured fingerprint reply.
        h = _make_hint("novastar_h_series", udp_probe={
            "port": 6000,
            "send_hex": "00010203",
            "expect_regex": r"^NS-([A-Z0-9]+)",
            "timeout_ms": 2000,
            "cross_vendor": False,
            "extract": {
                "manufacturer": "NovaStar",
                "model": {"regex": "model=([^,]+)", "group": 1},
            },
        })
        spec = h.udp_probe
        assert spec is not None
        assert spec.port == 6000
        assert spec.send == bytes.fromhex("00010203")
        assert spec.probe_id == "custom_novastar_h_series_udp"
        assert spec.response_match.regex.pattern == r"^NS-([A-Z0-9]+)"
        assert spec.cross_vendor is False
        by_name = {r.field_name: r for r in spec.extract}
        assert by_name["manufacturer"].value == "NovaStar"
        assert by_name["model"].regex.pattern == "model=([^,]+)"

    def test_full_tcp_block_parses(self):
        h = _make_hint("lightware_lw3", tcp_probe={
            "port": 6107,
            "send_ascii": "GET /sys/version\r\n",
            "expect": "Lightware",
            "cross_vendor": True,
            "extract_manufacturer": "Lightware",
        })
        spec = h.tcp_probe
        assert spec is not None
        assert spec.send == b"GET /sys/version\r\n"
        assert spec.probe_id == "custom_lightware_lw3_tcp"
        assert spec.cross_vendor is True

    def test_send_hex_and_ascii_both_rejected(self):
        with pytest.raises(DiscoveryHintError, match="send_hex and send_ascii"):
            _make_hint("bad", udp_probe={
                "port": 6000, "send_hex": "aa", "send_ascii": "x",
                "expect": "y",
            })

    def test_udp_requires_send_and_match(self):
        with pytest.raises(DiscoveryHintError, match="must declare send_ascii or send_hex"):
            _make_hint("bad", udp_probe={
                "port": 6000, "expect": "y",
            })
        with pytest.raises(DiscoveryHintError, match="needs exactly one"):
            _make_hint("bad", udp_probe={
                "port": 6000, "send_ascii": "x",
            })

    def test_invalid_regex_rejected(self):
        with pytest.raises(DiscoveryHintError, match="failed to compile"):
            _make_hint("bad", udp_probe={
                "port": 6000, "send_ascii": "x", "expect_regex": "[",
            })

    def test_timeout_capped(self):
        with pytest.raises(DiscoveryHintError, match="timeout_ms"):
            _make_hint("bad", udp_probe={
                "port": 6000, "send_ascii": "x", "expect": "y",
                "timeout_ms": 99999,
            })

    def test_cross_vendor_must_be_bool(self):
        with pytest.raises(DiscoveryHintError, match="cross_vendor"):
            _make_hint("bad", udp_probe={
                "port": 6000, "send_ascii": "x", "expect": "y",
                "cross_vendor": "yes",
            })
        # Bools are accepted on both ends.
        h_true = _make_hint("g_true", udp_probe={
            "port": 6000, "send_ascii": "x", "expect": "y",
            "cross_vendor": True,
        })
        h_false = _make_hint("g_false", udp_probe={
            "port": 6001, "send_ascii": "x", "expect": "y",
            "cross_vendor": False,
        })
        assert h_true.udp_probe.cross_vendor is True
        assert h_false.udp_probe.cross_vendor is False

    def test_signal_index_registers_custom_probes(self):
        h = _make_hint("foo", udp_probe={
            "port": 6000, "send_ascii": "x", "expect": "y",
            "cross_vendor": True,
        }, tcp_probe={
            "port": 6107, "send_ascii": "x", "expect": "y",
        })
        idx = build_signal_index([h])
        # Each declared probe registers exactly one rule. The schema's
        # cross_vendor flag flows through to SignalRule.generic.
        assert idx.driver_count() == 1


# ---------------------------------------------------------------------------
# Probe runner — unit tests
# ---------------------------------------------------------------------------


class TestResponseMatching:
    def _spec_with_match(self, **match_fields):
        # Map from the test's old key names to the new schema field names.
        translated: dict = {}
        if "starts_with_hex" in match_fields:
            translated["expect_hex"] = match_fields["starts_with_hex"]
        if "contains" in match_fields:
            translated["expect"] = match_fields["contains"]
        if "regex" in match_fields:
            translated["expect_regex"] = match_fields["regex"]
        h = _make_hint("test", udp_probe={
            "port": 6000, "send_ascii": "x", **translated,
        })
        return h.udp_probe

    def test_starts_with_hex(self):
        spec = self._spec_with_match(starts_with_hex="AA55")
        assert _matches(b"\xaa\x55hello", spec.response_match) is True
        assert _matches(b"\xff\xffhello", spec.response_match) is False

    def test_contains_in_text(self):
        spec = self._spec_with_match(contains="NovaStar")
        assert _matches(b"hello NovaStar world", spec.response_match) is True
        assert _matches(b"hello world", spec.response_match) is False

    def test_regex_matches_decoded_text(self):
        spec = self._spec_with_match(regex=r"^NS-([A-Z0-9]+)$")
        assert _matches(b"NS-ABC123", spec.response_match) is True
        assert _matches(b"junk", spec.response_match) is False

    # B9 removed the multi-matcher AND-together case from the schema —
    # probes now declare exactly one of expect / expect_regex /
    # expect_hex. The schema-level rejection is exercised in
    # test_discovery_hints_schema.test_udp_rejects_multiple_matchers.


class TestExtractRules:
    def test_static_value_for_reserved_key(self):
        h = _make_hint("t", udp_probe={
            "port": 6000, "send_ascii": "x", "expect": "y",
            "extract": {"manufacturer": "NovaStar"},
        })
        reserved, extracted = _apply_extract(b"any payload", h.udp_probe.extract)
        assert reserved == {"manufacturer": "NovaStar"}
        assert extracted == {}

    def test_regex_capture_group(self):
        h = _make_hint("t", udp_probe={
            "port": 6000, "send_ascii": "x", "expect": "y",
            "extract": {"model": {"regex": r"model=(\S+)", "group": 1}},
        })
        reserved, extracted = _apply_extract(
            b"abc model=MMX-FR-9R8 fw=2.5", h.udp_probe.extract,
        )
        assert reserved == {}
        assert extracted == {"model": "MMX-FR-9R8"}

    def test_no_match_skipped(self):
        h = _make_hint("t", udp_probe={
            "port": 6000, "send_ascii": "x", "expect": "y",
            "extract": {"model": {"regex": r"model=(\S+)", "group": 1}},
        })
        reserved, extracted = _apply_extract(b"no model field", h.udp_probe.extract)
        assert extracted == {}


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_paces_to_configured_rate(self):
        # 10/sec means 100ms interval; first acquire is immediate.
        rl = RateLimiter(rate_per_sec=10)
        t0 = time.monotonic()
        for _ in range(5):
            await rl.acquire()
        elapsed = time.monotonic() - t0
        # 4 intervals of 100ms = 400ms minimum. Allow some scheduler slack.
        assert 0.35 <= elapsed <= 0.9, f"unexpected pacing: {elapsed:.3f}s"

    def test_zero_rate_rejected(self):
        with pytest.raises(ValueError):
            RateLimiter(0)


class TestSourceIPBinding:
    def test_udp_socket_bound_to_source_ip(self):
        sock = _make_udp_socket("127.0.0.1", broadcast=True)
        assert sock is not None
        try:
            bound_ip, _bound_port = sock.getsockname()
            assert bound_ip == "127.0.0.1"
        finally:
            sock.close()

    def test_udp_socket_unbindable_returns_none(self):
        # 127.255.255.254 isn't usually bindable as a source on Windows /
        # Linux — confirms the helper logs+returns None instead of raising.
        # Some kernels do allow this; skip if so.
        sock = _make_udp_socket("127.255.255.254", broadcast=True)
        if sock is not None:
            sock.close()
            pytest.skip("This kernel allows arbitrary loopback source IPs")
        assert sock is None


# ---------------------------------------------------------------------------
# End-to-end UDP / TCP integration
# ---------------------------------------------------------------------------


class TestProbeRunnerIntegration:
    @pytest.mark.asyncio
    async def test_udp_probe_emits_evidence_with_extracted_fields(self):
        port = _next_port()
        _udp_responder(port, b"WHOIS", b"FAKE-VENDOR ack model=ABC123 fw=2.5\n")
        h = _make_hint("fake_vendor", udp_probe={
            "port": port, "send_ascii": "WHOIS\n",
            "expect": "FAKE-VENDOR",
            "timeout_ms": 1500,
            "extract": {
                "manufacturer": "FakeVendor",
                "model": {"regex": r"model=(\S+)", "group": 1},
            },
        })
        rl = RateLimiter(rate_per_sec=20)
        results = await run_udp_broadcast_probe(
            h.udp_probe,
            targets=["127.0.0.1"],
            source_ip="127.0.0.1",
            rate_limiter=rl,
        )
        assert "127.0.0.1" in results
        ev = results["127.0.0.1"]
        assert ev.data["source_id"] == "custom_fake_vendor_udp"
        assert ev.data["txt"]["manufacturer"] == "FakeVendor"
        assert ev.data["txt"]["model"] == "ABC123"
        # Matched pattern lands on the evidence so the scan-results
        # "Why?" reveal can render "UDP probe on port <p> matched
        # contains:FAKE-VENDOR".
        assert ev.data["port"] == port
        assert ev.data["matched_pattern"] == "contains:FAKE-VENDOR"

    @pytest.mark.asyncio
    async def test_udp_probe_silent_on_no_responder(self):
        port = _next_port()
        h = _make_hint("noone_home", udp_probe={
            "port": port, "send_ascii": "ping",
            "expect": "pong",
            "timeout_ms": 300,
        })
        rl = RateLimiter(rate_per_sec=20)
        results = await run_udp_broadcast_probe(
            h.udp_probe,
            targets=["127.0.0.1"],
            source_ip="127.0.0.1",
            rate_limiter=rl,
        )
        assert results == {}

    @pytest.mark.asyncio
    async def test_tcp_probe_emits_evidence_and_lifts_manufacturer(self):
        port = _next_port()
        _tcp_responder(port, b"pr Lightware FrameServer 2.7.3\n")
        h = _make_hint("lightware_lw3", tcp_probe={
            "port": port, "send_ascii": "GET /sys/version\r\n",
            "expect": "Lightware",
            "timeout_ms": 2000,
            "extract": {
                "manufacturer": "Lightware",
                "version": {"regex": r"FrameServer (\S+)", "group": 1},
            },
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0,
        )
        assert ev is not None
        assert ev.data["source_id"] == "custom_lightware_lw3_tcp"
        # manufacturer is lifted to top of response so vendor_string finds it
        assert ev.data["response"]["manufacturer"] == "Lightware"
        # other fields land under 'extracted'
        assert ev.data["response"]["extracted"]["version"] == "2.7.3"
        # Port + matched pattern feed the §10 phrasing — the UI
        # prefers the response excerpt for readable text but falls
        # back to "TCP probe on port <p> matched contains:Lightware"
        # for binary protocols whose excerpt would be gibberish.
        assert ev.data["port"] == port
        assert ev.data["matched_pattern"] == "contains:Lightware"

    @pytest.mark.asyncio
    async def test_tcp_probe_connect_only_omits_matched_pattern(self):
        port = _next_port()
        # Banner-grab style: the responder sends as soon as it accepts,
        # without reading first. Using the segments helper (one segment =
        # no gap) keeps this deterministic. The earlier version reused
        # _tcp_responder, whose banner only goes out after a 2 s client-recv
        # timeout — that left ~1.5 s of slack against the probe deadline and
        # flaked under full-suite load whenever the responder thread was
        # scheduled late. Nothing here is testing timeout behavior, so the
        # wall-clock dependence was pure liability.
        _tcp_responder_segments(port, [b"banner\n"])
        h = _make_hint("connect_only", tcp_probe={
            "port": port,
            "timeout_ms": 1000,
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0,
        )
        assert ev is not None
        # No `expect_*` declared, so the response_match is empty;
        # describe_response_match() returns "" and we coerce to None
        # rather than persisting an empty matcher string.
        assert "matched_pattern" not in ev.data
        assert ev.data["port"] == port

    @pytest.mark.asyncio
    async def test_tcp_probe_no_match_returns_none(self):
        port = _next_port()
        _tcp_responder(port, b"unrelated banner")
        h = _make_hint("strict", tcp_probe={
            "port": port, "send_ascii": "x",
            "expect": "Lightware",
            "timeout_ms": 1000,
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0,
        )
        assert ev is None

    @pytest.mark.asyncio
    async def test_tcp_probe_matches_banner_in_later_segment(self):
        # Regression: a single read captures only the first TCP segment, so
        # a device that sends telnet IAC negotiation first and its
        # identifying banner in a later segment would never match. The
        # runner must accumulate across segments. Mirrors the TurtleAV
        # controllers (IAC, then "Welcome To Controller(h)...").
        port = _next_port()
        iac = b"\xff\xfb\x03\xff\xfb\x01\xff\xfe\x01\xff\xfd\x00"
        banner = (
            b"\r\n====\r\nWelcome To Controller(h) Terminal Control System"
            b"\r\nFW Version: 1.50.02\r\nCONTROLLER> "
        )
        _tcp_responder_segments(port, [iac, banner], gap=0.2)
        h = _make_hint("darwinish", tcp_probe={
            "port": port,
            "expect_regex": r"Controller\(h\)|DARWIN CONTROL",
            "timeout_ms": 4000,
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0,
        )
        assert ev is not None
        assert ev.data["source_id"] == "custom_darwinish_tcp"
        assert "Controller(h)" in ev.data["response"]["text"]

    @pytest.mark.asyncio
    async def test_tcp_probe_iac_only_no_banner_returns_none(self):
        # A telnet host that negotiates but never sends the expected banner
        # must not match — and must not hang to the full budget.
        port = _next_port()
        iac = b"\xff\xfb\x03\xff\xfb\x01"
        _tcp_responder_segments(port, [iac], gap=0.0)
        h = _make_hint("darwinish_neg", tcp_probe={
            "port": port,
            "expect_regex": r"Controller\(h\)",
            "timeout_ms": 5000,
        })
        t0 = time.monotonic()
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0,
        )
        elapsed = time.monotonic() - t0
        assert ev is None
        # Released on EOF / quiet gap, well under the 5s budget.
        assert elapsed < 4.0, f"runner hung past the quiet gap: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Send-rate cap (M-252) + distinct-responder flood guard (M-253)
# ---------------------------------------------------------------------------


class _CountingRateLimiter(RateLimiter):
    """RateLimiter that records how many slots were acquired."""

    def __init__(self, rate_per_sec: float) -> None:
        super().__init__(rate_per_sec)
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1
        await super().acquire()


class _FakeUDPSocket:
    """Stand-in socket that replays a scripted list of (data, (ip, port))
    datagrams so the UDP runner's recv loop can be driven with many distinct
    spoofed source IPs (impossible to generate for real over loopback)."""

    def __init__(self, packets):
        self._packets = list(packets)
        self.closed = False

    def settimeout(self, _t):
        pass

    def sendto(self, data, _addr):
        return len(data)

    def recvfrom(self, _n):
        if self._packets:
            return self._packets.pop(0)
        # Non-timeout OSError -> the runner's recv loop breaks out cleanly
        # instead of busy-spinning until the probe budget elapses.
        raise OSError("no more scripted datagrams")

    def close(self):
        self.closed = True


class TestTcpProbeRateLimiting:
    """M-252: the TCP active probe must honor the shared RateLimiter so the
    documented global 10/sec send cap actually bounds the SYN rate."""

    @pytest.mark.asyncio
    async def test_tcp_probe_acquires_rate_limiter_before_connect(self):
        port = _next_port()
        _tcp_responder(port, b"pr Lightware FrameServer 2.7.3\n")
        h = _make_hint("lightware_lw3", tcp_probe={
            "port": port, "send_ascii": "GET /sys/version\r\n",
            "expect": "Lightware",
            "timeout_ms": 2000,
        })
        rl = _CountingRateLimiter(rate_per_sec=50)
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0, rate_limiter=rl,
        )
        assert ev is not None
        # The runner drew exactly one slot from the shared limiter for its SYN.
        assert rl.acquired == 1

    @pytest.mark.asyncio
    async def test_tcp_probe_without_limiter_still_runs(self):
        # A direct caller (no limiter) is still allowed — the param is optional.
        port = _next_port()
        _tcp_responder(port, b"pr Lightware FrameServer 2.7.3\n")
        h = _make_hint("lightware_lw3", tcp_probe={
            "port": port, "send_ascii": "x",
            "expect": "Lightware", "timeout_ms": 2000,
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="127.0.0.1", source_ip="127.0.0.1",
        )
        assert ev is not None


class TestUdpProbeResponderCap:
    """M-253: a spoofed-source responder storm must not grow the results dict
    without bound — distinct matching senders are capped per probe window."""

    @pytest.mark.asyncio
    async def test_distinct_responders_capped(self, caplog):
        # More matching datagrams than the cap, each from a distinct source IP.
        overflow = 25
        packets = [
            (b"FAKE-VENDOR ok", (f"10.90.{n // 256}.{n % 256}", 4001))
            for n in range(_MAX_PROBE_RESPONDERS + overflow)
        ]
        fake = _FakeUDPSocket(packets)
        h = _make_hint("floody", udp_probe={
            "port": 4001, "send_ascii": "WHOIS",
            "expect": "FAKE-VENDOR", "timeout_ms": 2000,
        })
        rl = RateLimiter(rate_per_sec=1000)
        with patch.object(
            probe_runner_mod, "_make_udp_socket", return_value=fake,
        ):
            with caplog.at_level("WARNING"):
                results = await run_udp_broadcast_probe(
                    h.udp_probe, targets=["255.255.255.255"],
                    source_ip="127.0.0.1", rate_limiter=rl,
                )
        assert len(results) == _MAX_PROBE_RESPONDERS
        assert fake.closed
        hits = [r for r in caplog.records if "distinct-responder cap" in r.message]
        assert hits, "expected a one-time distinct-responder cap warning"
        assert len(hits) == 1  # warned once, not per dropped datagram


# ---------------------------------------------------------------------------
# Vendor-string + best-driver-first integration
# ---------------------------------------------------------------------------


class TestVendorStringIntegration:
    @pytest.mark.asyncio
    async def test_udp_extract_manufacturer_emits_vendor_string_evidence(self):
        port = _next_port()
        _udp_responder(port, b"WHOIS", b"FAKE-VENDOR\n")
        h = _make_hint("vendor_test", udp_probe={
            "port": port, "send_ascii": "WHOIS",
            "expect": "FAKE-VENDOR",
            "timeout_ms": 1500,
            "extract": {"manufacturer": "FakeVendor"},
        })
        rl = RateLimiter(rate_per_sec=20)
        results = await run_udp_broadcast_probe(
            h.udp_probe, targets=["127.0.0.1"],
            source_ip="127.0.0.1", rate_limiter=rl,
        )
        ev = results["127.0.0.1"]
        # The finalize step lifts manufacturer to vendor_string evidence.
        derived = extract_vendor_strings([ev])
        assert any(
            d.data.get("kind") == "vendor_string"
            and d.data.get("value") == "fakevendor"
            for d in derived
        )


class TestBestDriverFirstIntegration:
    """A cross_vendor ``tcp_probe`` plus a vendor-specific peer driver
    with ``manufacturer_alias`` produces the best-driver-first result
    (vendor primary, cross-vendor alternative).
    """

    @pytest.mark.asyncio
    async def test_cross_vendor_probe_yields_to_vendor_specific(self):
        port = _next_port()
        _tcp_responder(port, b"banner Vendor=NovaStar\n")
        # Cross-vendor driver: declares the TCP probe with cross_vendor: true.
        cross = _make_hint("unbranded_lw3", tcp_probe={
            "port": port, "send_ascii": "x",
            "expect": "Vendor=",
            "cross_vendor": True,
            "extract": {
                "manufacturer": {"regex": r"Vendor=(\S+)", "group": 1},
            },
        })
        # Vendor-specific driver: claims the manufacturer string via
        # manufacturer_alias. No probe of its own — it relies on
        # vendor_string evidence lifted from the cross-vendor probe.
        vendor = _make_hint("novastar_specific", manufacturer_alias=["NovaStar"])
        idx = build_signal_index([cross, vendor])
        matcher = TierMatcher(idx)

        ev = await run_tcp_active_probe(
            cross.tcp_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0,
        )
        assert ev is not None
        log = [ev]
        log.extend(extract_vendor_strings(log))
        match = matcher.match(log)
        # Vendor-specific driver wins as primary; cross-vendor relegated
        # to alternative per the best-driver-first logic.
        assert match.driver_id == "novastar_specific"
        assert match.state == DeviceState.IDENTIFIED
        alt_ids = [a if isinstance(a, str) else a.driver_id for a in match.alternatives]
        assert "unbranded_lw3" in alt_ids


# ---------------------------------------------------------------------------
# Companion loader
# ---------------------------------------------------------------------------


_COMPANION_SOURCE = '''
import asyncio

async def probe(ctx):
    ctx.log.info("companion ran")
    # Default probe_id = ctx.companion_broadcast_probe_id
    # i.e. ``custom_<driver_id>_companion_udp``.
    await ctx.emit_broadcast(
        "10.0.0.99",
        txt={"manufacturer": "FakeVendor"},
    )
'''


_HANG_COMPANION = '''
import asyncio

async def probe(ctx):
    await asyncio.sleep(60)
'''


_BAD_COMPANION = '''
def probe(ctx):  # not async
    pass
'''


class TestCompanionLoader:
    def test_loads_async_probe(self, tmp_path):
        (tmp_path / "fake_vendor_discovery.py").write_text(_COMPANION_SOURCE)
        probes = load_discovery_companions([tmp_path])
        assert "fake_vendor" in probes
        assert asyncio.iscoroutinefunction(probes["fake_vendor"])

    def test_skips_module_without_probe(self, tmp_path):
        (tmp_path / "broken_discovery.py").write_text("x = 1\n")
        probes = load_discovery_companions([tmp_path])
        assert probes == {}

    def test_skips_non_async_probe(self, tmp_path):
        (tmp_path / "not_async_discovery.py").write_text(_BAD_COMPANION)
        probes = load_discovery_companions([tmp_path])
        assert probes == {}

    def test_missing_directory_silently_skipped(self, tmp_path):
        probes = load_discovery_companions([tmp_path / "doesnotexist"])
        assert probes == {}

    @pytest.mark.asyncio
    async def test_probe_invocation_emits_through_context(self, tmp_path):
        (tmp_path / "vend_discovery.py").write_text(_COMPANION_SOURCE)
        probes = load_discovery_companions([tmp_path])

        captured: list[tuple[str, Evidence]] = []

        async def emit(host, ev):
            captured.append((host, ev))

        ctx = ProbeContext(
            driver_id="vend",
            source_ip="127.0.0.1",
            target_subnets=("192.168.1.0/24",),
            timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
            log=logging.getLogger("test"),
            _emit_for_host=emit,
        )
        await run_companion("vend", probes["vend"], ctx)
        assert len(captured) == 1
        host, ev = captured[0]
        assert host == "10.0.0.99"
        # Default probe_id resolves to the canonical synthetic ID
        # built from ctx.driver_id.
        assert ev.data["source_id"] == "custom_vend_companion_udp"
        assert ev.data["txt"]["manufacturer"] == "FakeVendor"

    @pytest.mark.asyncio
    async def test_hard_timeout_cuts_off_hung_companion(self, tmp_path, caplog):
        (tmp_path / "hang_discovery.py").write_text(_HANG_COMPANION)
        probes = load_discovery_companions([tmp_path])

        async def emit(host, ev):
            pass

        ctx = ProbeContext(
            driver_id="hang",
            source_ip="127.0.0.1",
            target_subnets=(),
            timeout_seconds=0.5,
            log=logging.getLogger("test"),
            _emit_for_host=emit,
        )

        t0 = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="discovery.companion"):
            await run_companion("hang", probes["hang"], ctx)
        elapsed = time.monotonic() - t0
        # The hang sleeps 60s; the cap is 0.5s. The upper bound only
        # needs to sit far below 60 while absorbing CI runner overhead.
        assert 0.4 <= elapsed <= 4.0, f"timeout not enforced: {elapsed:.2f}"
        assert any("exceeded" in r.message for r in caplog.records)


# A deliberately WRONG companion: parks its thread in C-level blocking
# sleep (stands in for a sync socket.recv()/connect() with no timeout),
# which no asyncio cancellation can interrupt. The sleep is much longer
# than any elapsed-time bound asserted below, so a loaded CI runner's
# scheduling noise can't blur the pass/fail line: the isolated path
# returns in a few seconds, the broken (inline) path takes the full
# sleep. The daemon worker thread outlives the test harmlessly.
_BLOCKING_COMPANION = '''
import time

async def probe(ctx):
    time.sleep(20.0)
'''


class TestCompanionThreadIsolation:
    """Blocking sync I/O in a companion must never stall the engine loop.

    Companions are community code; one that blocks in C-level I/O
    ignores CancelledError, so run inline it would freeze the whole
    server past every timeout. The runner therefore executes each
    companion on its own event loop in a daemon worker thread and
    abandons the thread shortly after the cap.
    """

    @pytest.mark.asyncio
    async def test_blocking_companion_keeps_event_loop_responsive(
        self, tmp_path, caplog,
    ):
        (tmp_path / "block_discovery.py").write_text(_BLOCKING_COMPANION)
        probes = load_discovery_companions([tmp_path])

        async def emit(host, ev):
            pass

        ctx = ProbeContext(
            driver_id="block",
            source_ip="127.0.0.1",
            target_subnets=(),
            timeout_seconds=0.5,
            log=logging.getLogger("test"),
            _emit_for_host=emit,
        )

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            t0 = time.monotonic()
            with caplog.at_level(
                logging.WARNING, logger="discovery.companion",
            ):
                await run_companion("block", probes["block"], ctx)
            elapsed = time.monotonic() - t0
        finally:
            ticker_task.cancel()

        # Isolated execution abandons the worker at cap+grace (~2.5s);
        # inline execution would park the loop in time.sleep(20),
        # returning only after the full sleep with the ticker starved.
        # The bound sits far from both: wide enough that a slow CI
        # runner's overhead can't trip it (4.5s flaked at 5.59s on a
        # loaded runner), far below the 20s broken path.
        assert elapsed < 10.0, (
            f"blocking companion stalled run_companion for {elapsed:.2f}s"
        )
        assert ticks >= 5, (
            "event loop was starved while the companion blocked"
        )
        assert any("abandoning" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_blocking_companion_cancelled_promptly(self, tmp_path):
        # stop_scan / scan-level timeout deliver CancelledError; it must
        # take effect immediately even while the companion is wedged.
        (tmp_path / "block_discovery.py").write_text(_BLOCKING_COMPANION)
        probes = load_discovery_companions([tmp_path])

        async def emit(host, ev):
            pass

        ctx = ProbeContext(
            driver_id="block",
            source_ip="127.0.0.1",
            target_subnets=(),
            timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
            log=logging.getLogger("test"),
            _emit_for_host=emit,
        )

        t0 = time.monotonic()
        task = asyncio.create_task(
            run_companion("block", probes["block"], ctx),
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, (
            f"cancel was not delivered until the blocking call "
            f"returned ({elapsed:.2f}s)"
        )

    @pytest.mark.asyncio
    async def test_emit_marshalled_to_engine_loop_thread(self, tmp_path):
        # Engine state is only safe to touch from the engine's loop, so
        # the emit callback must run there — not on the worker thread
        # the companion executes on.
        (tmp_path / "vend_discovery.py").write_text(_COMPANION_SOURCE)
        probes = load_discovery_companions([tmp_path])

        engine_thread = threading.get_ident()
        emit_threads: list[int] = []
        captured: list[tuple[str, Evidence]] = []

        async def emit(host, ev):
            emit_threads.append(threading.get_ident())
            captured.append((host, ev))

        ctx = ProbeContext(
            driver_id="vend",
            source_ip="127.0.0.1",
            target_subnets=("192.168.1.0/24",),
            timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
            log=logging.getLogger("test"),
            _emit_for_host=emit,
        )
        await run_companion("vend", probes["vend"], ctx)
        assert len(captured) == 1
        assert captured[0][0] == "10.0.0.99"
        assert emit_threads == [engine_thread]


# ---------------------------------------------------------------------------
# Network-safety regression
# ---------------------------------------------------------------------------


class TestNetworkSafety:
    """Every probe in a test scan binds to the configured source_ip."""

    @pytest.mark.asyncio
    async def test_udp_probe_socket_binds_to_source_ip(self):
        # The UDP runner doesn't expose its socket, so verify the
        # _make_udp_socket helper that the runner uses always binds.
        sock = _make_udp_socket("127.0.0.1", broadcast=True)
        assert sock is not None
        try:
            bound_ip, _bound_port = sock.getsockname()
            assert bound_ip == "127.0.0.1", (
                "Network-safety contract: UDP probes must bind to the "
                "configured source_ip"
            )
        finally:
            sock.close()

    @pytest.mark.asyncio
    async def test_tcp_probe_passes_local_addr_to_open_connection(
        self, monkeypatch,
    ):
        captured = {}

        original = asyncio.open_connection

        async def spy_open_connection(host, port, *, local_addr=None, **kw):
            captured["local_addr"] = local_addr
            captured["host"] = host
            captured["port"] = port
            # Defer to the real implementation but force it to fail so
            # the test doesn't need a live responder.
            raise OSError("synthetic connect refused")

        monkeypatch.setattr(asyncio, "open_connection", spy_open_connection)

        h = _make_hint("net_safety_tcp", tcp_probe={
            "port": 5555, "send_ascii": "x",
            "expect": "y",
            "timeout_ms": 500,
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="10.0.0.1",
            source_ip="192.0.2.5", stagger_ms=0,
        )
        assert ev is None
        assert captured["local_addr"] == ("192.0.2.5", 0), (
            "Network-safety contract: TCP probes must pass "
            "local_addr=(source_ip, 0) to asyncio.open_connection"
        )
        # Restore (monkeypatch undoes this automatically; explicit for clarity).
        monkeypatch.setattr(asyncio, "open_connection", original)


class TestTLSProbe:
    """``tls: true`` tcp probes TLS-wrap the connection before send/read.

    Lets an HTTPS-only device be fingerprinted from its own landing page —
    the only vendor-specific signal a device that ships encrypted-only and
    advertises a generic Dante mDNS service exposes pre-install.
    """

    def test_tls_flag_parses_tcp_and_defaults_false(self):
        h = _make_hint("https_dev", tcp_probe={
            "port": 443, "tls": True,
            "send_ascii": "GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
            "expect": "SoundCoreHero",
        })
        assert h.tcp_probe.tls is True
        # Omitted -> False (every existing probe stays plain TCP).
        h2 = _make_hint("plain_dev", tcp_probe={
            "port": 23, "send_ascii": "x", "expect": "y",
        })
        assert h2.tcp_probe.tls is False

    def test_tls_must_be_bool(self):
        with pytest.raises(DiscoveryHintError, match="tls"):
            _make_hint("bad", tcp_probe={
                "port": 443, "tls": "yes", "send_ascii": "x", "expect": "y",
            })

    def test_tls_rejected_on_udp_probe(self):
        with pytest.raises(DiscoveryHintError, match="tls is only valid on a tcp_probe"):
            _make_hint("bad", udp_probe={
                "port": 6000, "tls": True, "send_ascii": "x", "expect": "y",
            })

    @pytest.mark.asyncio
    async def test_tls_probe_reads_banner_over_real_tls(self, tmp_path):
        """End-to-end: handshake a real self-signed TLS server, read its
        landing page, match the fingerprint. A non-None evidence return only
        happens when the expect string matched over the encrypted channel."""
        from server.tls import generate_self_signed

        certs = generate_self_signed(tmp_path, hostnames=["localhost"], ips=["127.0.0.1"])
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(certs.cert_path, certs.key_path)

        async def handle(reader, writer):
            await reader.read(1024)  # consume the GET request
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
                b"<title>SoundCoreHero</title>"
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
        try:
            port = server.sockets[0].getsockname()[1]
            h = _make_hint("sch_tls", tcp_probe={
                "port": port, "tls": True,
                "send_ascii": "GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
                "expect": "SoundCoreHero",
                "timeout_ms": 3000,
            })
            ev = await run_tcp_active_probe(
                h.tcp_probe, target="127.0.0.1", source_ip="127.0.0.1", stagger_ms=0,
            )
        finally:
            server.close()
            await server.wait_closed()

        assert ev is not None, "tls probe should match the banner over TLS"

    @pytest.mark.asyncio
    async def test_tls_probe_drops_plain_tcp_host(self):
        """A plain (non-TLS) listener on the probed port fails the handshake,
        so the probe returns None instead of a false match — even though the
        host would have sent the matching string in cleartext."""
        async def handle(reader, writer):
            try:
                await reader.read(1024)
                writer.write(b"<title>SoundCoreHero</title>")
                await writer.drain()
            except OSError:
                pass
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)  # no ssl
        try:
            port = server.sockets[0].getsockname()[1]
            h = _make_hint("sch_plain", tcp_probe={
                "port": port, "tls": True,
                "send_ascii": "GET /\r\n", "expect": "SoundCoreHero",
                "timeout_ms": 1500,
            })
            ev = await run_tcp_active_probe(
                h.tcp_probe, target="127.0.0.1", source_ip="127.0.0.1", stagger_ms=0,
            )
        finally:
            server.close()
            await server.wait_closed()

        assert ev is None, "tls probe must not match a plain-TCP host"

    @pytest.mark.asyncio
    async def test_tls_probe_passes_ssl_context_to_open_connection(self, monkeypatch):
        captured = {}

        async def spy(host, port, *, local_addr=None, ssl=None, server_hostname=None, **kw):
            captured["ssl"] = ssl
            captured["server_hostname"] = server_hostname
            raise OSError("synthetic connect refused")

        monkeypatch.setattr(asyncio, "open_connection", spy)
        h = _make_hint("tls_args", tcp_probe={
            "port": 443, "tls": True, "send_ascii": "x", "expect": "y",
            "timeout_ms": 500,
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="10.0.0.1", source_ip="192.0.2.5", stagger_ms=0,
        )
        assert ev is None
        assert isinstance(captured["ssl"], ssl.SSLContext), (
            "tls probe must hand an SSLContext to asyncio.open_connection"
        )
        assert captured["server_hostname"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_plain_probe_passes_no_ssl_context(self, monkeypatch):
        captured = {}

        async def spy(host, port, *, local_addr=None, ssl=None, server_hostname=None, **kw):
            captured["ssl"] = ssl
            raise OSError("synthetic connect refused")

        monkeypatch.setattr(asyncio, "open_connection", spy)
        h = _make_hint("plain_args", tcp_probe={
            "port": 23, "send_ascii": "x", "expect": "y", "timeout_ms": 500,
        })
        ev = await run_tcp_active_probe(
            h.tcp_probe, target="10.0.0.1", source_ip="192.0.2.5", stagger_ms=0,
        )
        assert ev is None
        assert captured["ssl"] is None, "plain tcp probe must not pass an ssl context"


class TestCertSubjectProbe:
    """`cert_subject:` identifies a device by its self-signed TLS cert's own
    subject — the strongest pre-auth signal for gear that ships an identifying
    cert but no discovery beacon. Invented ACME device, never a real product."""

    def test_cert_subject_parses_and_compiles(self):
        h = _make_hint("acme_https", tcp_probe={
            "port": 443, "tls": True, "cert_subject": r"CN=ACME-WIDGET-",
        })
        assert h.tcp_probe.cert_subject is not None
        assert h.tcp_probe.cert_subject.search("CN=ACME-WIDGET-9000,O=Acme")
        assert h.tcp_probe.cert_subject_source == r"CN=ACME-WIDGET-"

    def test_cert_subject_requires_tls(self):
        with pytest.raises(DiscoveryHintError, match="cert_subject requires tls"):
            _make_hint("bad", tcp_probe={"port": 443, "cert_subject": "CN=x"})

    def test_cert_subject_bad_regex_raises(self):
        with pytest.raises(DiscoveryHintError, match="cert_subject failed to compile"):
            _make_hint("bad", tcp_probe={
                "port": 443, "tls": True, "cert_subject": "CN=(unclosed",
            })

    def test_cert_subject_empty_raises(self):
        with pytest.raises(DiscoveryHintError, match="cert_subject must be"):
            _make_hint("bad", tcp_probe={
                "port": 443, "tls": True, "cert_subject": "",
            })

    async def _serve_tls(self, tmp_path, cn):
        from server.tls import generate_self_signed
        certs = generate_self_signed(tmp_path, hostnames=[cn], ips=["127.0.0.1"])
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certs.cert_path, certs.key_path)

        async def handle(reader, writer):
            try:
                await reader.read(64)
            except OSError:
                pass
            writer.close()

        return await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)

    @pytest.mark.asyncio
    async def test_cert_only_probe_matches_and_extracts_model(self, tmp_path):
        """A cert-only probe (no send, no payload matcher) identifies the device
        purely from its cert subject and pulls the model out of the CN — without
        waiting for a banner the HTTPS server never sends unprompted."""
        server = await self._serve_tls(tmp_path, "ACME-WIDGET-9000")
        try:
            port = server.sockets[0].getsockname()[1]
            h = _make_hint("acme_https", tcp_probe={
                "port": port, "tls": True,
                "cert_subject": r"CN=ACME-WIDGET-",
                "extract": {"model": {"regex": r"ACME-WIDGET-[0-9]+", "group": 0}},
                "timeout_ms": 3000,
            })
            ev = await run_tcp_active_probe(
                h.tcp_probe, target="127.0.0.1", source_ip="127.0.0.1", stagger_ms=0,
            )
        finally:
            server.close()
            await server.wait_closed()

        assert ev is not None, "cert-only probe should match on the cert subject"
        data = ev.data.get("response", ev.data)
        assert (data.get("extracted") or {}).get("model") == "ACME-WIDGET-9000"

    @pytest.mark.asyncio
    async def test_cert_subject_no_match_returns_none(self, tmp_path):
        """A cert whose subject doesn't match the regex is not this device."""
        server = await self._serve_tls(tmp_path, "OTHER-VENDOR-1")
        try:
            port = server.sockets[0].getsockname()[1]
            h = _make_hint("acme_https", tcp_probe={
                "port": port, "tls": True, "cert_subject": r"CN=ACME-WIDGET-",
                "timeout_ms": 2000,
            })
            ev = await run_tcp_active_probe(
                h.tcp_probe, target="127.0.0.1", source_ip="127.0.0.1", stagger_ms=0,
            )
        finally:
            server.close()
            await server.wait_closed()

        assert ev is None, "non-matching cert subject must not identify the device"
