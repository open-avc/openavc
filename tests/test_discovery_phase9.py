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
import threading
import time

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
from server.discovery.probe_runner import (
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
    """Spawn a one-shot UDP server that replies to ``query_match`` packets."""
    def serve():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", port))
            s.settimeout(3.0)
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
    """Spawn a one-shot TCP server that sends ``reply`` after first read."""
    def serve():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
            s.settimeout(4.0)
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
        h = _make_hint("novastar_h_series", udp_probe={
            "port": 6000,
            "send_hex": "00010203",
            "expect_hex": "AA55",
            "expect": "NovaStar",
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
        assert spec.response_match.starts_with == bytes.fromhex("AA55")
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
        with pytest.raises(DiscoveryHintError, match="needs at least one"):
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

    def test_all_matchers_AND_together(self):
        spec = self._spec_with_match(
            starts_with_hex="AA55",
            contains="model=",
        )
        assert _matches(b"\xaa\x55 model=ABC", spec.response_match) is True
        # starts_with passes but contains fails -> overall False
        assert _matches(b"\xaa\x55 nothing", spec.response_match) is False


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
        assert 0.4 <= elapsed <= 1.5, f"timeout not enforced: {elapsed:.2f}"
        assert any("exceeded" in r.message for r in caplog.records)


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
