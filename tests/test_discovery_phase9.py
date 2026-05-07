"""Phase 9 tests — driver-declared probes, companions, end-to-end.

Covers the acceptance criteria from discovery-redesign-plan.md §Phase 9
Task 9.8:

- Unit tests for probe_runner: response matching, extract rules, source
  IP binding, rate limiter pacing, schema-validation parser.
- _discovery.py companion loader + ProbeContext + hard timeout.
- End-to-end integration: a synthetic UDP responder identifies via a
  declared udp_broadcast_probe; same for TCP.
- Generic-flag integration: a generic tcp_active_probe + vendor-
  specific driver with vendor_aliases produces the Phase 8.5 best-
  driver-first result.
- Vendor-string integration: a probe whose extract: populates
  manufacturer correctly emits Tier 4 vendor_string evidence.
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
# Schema parser — Phase 9 additions
# ---------------------------------------------------------------------------


class TestPhase9SchemaParsing:
    def test_full_udp_block_parses(self):
        h = _make_hint("novastar_h_series", udp_broadcast_probe={
            "port": 6000,
            "send": {"hex": "00010203"},
            "response_match": {
                "starts_with_hex": "AA55",
                "contains": "NovaStar",
                "regex": r"^NS-([A-Z0-9]+)",
            },
            "timeout_ms": 2000,
            "generic": False,
            "extract": {
                "manufacturer": "NovaStar",
                "model": {"regex": "model=([^,]+)", "group": 1},
            },
        })
        spec = h.udp_broadcast_probe
        assert spec is not None
        assert spec.port == 6000
        assert spec.send == bytes.fromhex("00010203")
        assert spec.probe_id == "custom_novastar_h_series_udp"
        assert spec.response_match.starts_with == bytes.fromhex("AA55")
        assert spec.response_match.regex.pattern == r"^NS-([A-Z0-9]+)"
        assert spec.generic is False
        by_name = {r.field_name: r for r in spec.extract}
        assert by_name["manufacturer"].value == "NovaStar"
        assert by_name["model"].regex.pattern == "model=([^,]+)"

    def test_full_tcp_block_parses(self):
        h = _make_hint("lightware_lw3", tcp_active_probe={
            "port": 6107,
            "send": {"ascii": "GET /sys/version\r\n"},
            "response_match": {"contains": "Lightware"},
            "generic": True,
            "extract": {"manufacturer": "Lightware"},
        })
        spec = h.tcp_active_probe
        assert spec is not None
        assert spec.send == b"GET /sys/version\r\n"
        assert spec.probe_id == "custom_lightware_lw3_tcp"
        assert spec.generic is True

    @pytest.mark.parametrize("port", [1900, 3702, 4352, 5353, 9131, 41794])
    def test_udp_disallowed_ports_rejected(self, port):
        with pytest.raises(DiscoveryHintError, match="reserved for a built-in"):
            _make_hint("bad_port", udp_broadcast_probe={
                "port": port, "send": {"ascii": "x"},
                "response_match": {"contains": "y"},
            })

    @pytest.mark.parametrize("port", [23, 1515, 1688, 1710, 4352, 10500, 49280])
    def test_tcp_disallowed_ports_rejected(self, port):
        with pytest.raises(DiscoveryHintError, match="reserved for a built-in"):
            _make_hint("bad_port", tcp_active_probe={
                "port": port, "send": {"ascii": "x"},
                "response_match": {"contains": "y"},
            })

    def test_send_requires_exactly_one_of_hex_ascii(self):
        with pytest.raises(DiscoveryHintError, match="exactly one"):
            _make_hint("bad", udp_broadcast_probe={
                "port": 6000, "send": {"hex": "aa", "ascii": "x"},
                "response_match": {"contains": "y"},
            })
        with pytest.raises(DiscoveryHintError, match="must declare one of"):
            _make_hint("bad", udp_broadcast_probe={
                "port": 6000, "send": {},
                "response_match": {"contains": "y"},
            })

    def test_response_match_requires_at_least_one_matcher(self):
        with pytest.raises(DiscoveryHintError, match="at least one"):
            _make_hint("bad", udp_broadcast_probe={
                "port": 6000, "send": {"ascii": "x"},
                "response_match": {},
            })

    def test_invalid_regex_rejected(self):
        with pytest.raises(DiscoveryHintError, match="failed to compile"):
            _make_hint("bad", udp_broadcast_probe={
                "port": 6000, "send": {"ascii": "x"},
                "response_match": {"regex": "["},
            })

    def test_timeout_capped(self):
        with pytest.raises(DiscoveryHintError, match="exceeds the max"):
            _make_hint("bad", udp_broadcast_probe={
                "port": 6000, "send": {"ascii": "x"},
                "response_match": {"contains": "y"},
                "timeout_ms": 99999,
            })

    def test_generic_must_be_bool(self):
        with pytest.raises(DiscoveryHintError, match="generic must be a bool"):
            _make_hint("bad", udp_broadcast_probe={
                "port": 6000, "send": {"ascii": "x"},
                "response_match": {"contains": "y"},
                "generic": "yes",
            })
        # Bools are accepted on both ends.
        h_true = _make_hint("g_true", udp_broadcast_probe={
            "port": 6000, "send": {"ascii": "x"},
            "response_match": {"contains": "y"}, "generic": True,
        })
        h_false = _make_hint("g_false", udp_broadcast_probe={
            "port": 6001, "send": {"ascii": "x"},
            "response_match": {"contains": "y"}, "generic": False,
        })
        assert h_true.udp_broadcast_probe.generic is True
        assert h_false.udp_broadcast_probe.generic is False

    def test_signal_index_registers_custom_probes(self):
        h = _make_hint("foo", udp_broadcast_probe={
            "port": 6000, "send": {"ascii": "x"},
            "response_match": {"contains": "y"}, "generic": True,
        }, tcp_active_probe={
            "port": 6107, "send": {"ascii": "x"},
            "response_match": {"contains": "y"},
        })
        idx = build_signal_index([h])
        # Each declared probe registers exactly one rule. The schema's
        # generic flag flows through to SignalRule.generic.
        assert idx.driver_count() == 1


# ---------------------------------------------------------------------------
# Probe runner — unit tests
# ---------------------------------------------------------------------------


class TestResponseMatching:
    def _spec_with_match(self, **match_fields):
        h = _make_hint("test", udp_broadcast_probe={
            "port": 6000, "send": {"ascii": "x"},
            "response_match": match_fields,
        })
        return h.udp_broadcast_probe

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
        h = _make_hint("t", udp_broadcast_probe={
            "port": 6000, "send": {"ascii": "x"},
            "response_match": {"contains": "y"},
            "extract": {"manufacturer": "NovaStar"},
        })
        reserved, extracted = _apply_extract(b"any payload", h.udp_broadcast_probe.extract)
        assert reserved == {"manufacturer": "NovaStar"}
        assert extracted == {}

    def test_regex_capture_group(self):
        h = _make_hint("t", udp_broadcast_probe={
            "port": 6000, "send": {"ascii": "x"},
            "response_match": {"contains": "y"},
            "extract": {"model": {"regex": r"model=(\S+)", "group": 1}},
        })
        reserved, extracted = _apply_extract(
            b"abc model=MMX-FR-9R8 fw=2.5", h.udp_broadcast_probe.extract,
        )
        assert reserved == {}
        assert extracted == {"model": "MMX-FR-9R8"}

    def test_no_match_skipped(self):
        h = _make_hint("t", udp_broadcast_probe={
            "port": 6000, "send": {"ascii": "x"},
            "response_match": {"contains": "y"},
            "extract": {"model": {"regex": r"model=(\S+)", "group": 1}},
        })
        reserved, extracted = _apply_extract(b"no model field", h.udp_broadcast_probe.extract)
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
        h = _make_hint("fake_vendor", udp_broadcast_probe={
            "port": port, "send": {"ascii": "WHOIS\n"},
            "response_match": {"contains": "FAKE-VENDOR"},
            "timeout_ms": 1500,
            "extract": {
                "manufacturer": "FakeVendor",
                "model": {"regex": r"model=(\S+)", "group": 1},
            },
        })
        rl = RateLimiter(rate_per_sec=20)
        results = await run_udp_broadcast_probe(
            h.udp_broadcast_probe,
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
        h = _make_hint("noone_home", udp_broadcast_probe={
            "port": port, "send": {"ascii": "ping"},
            "response_match": {"contains": "pong"},
            "timeout_ms": 300,
        })
        rl = RateLimiter(rate_per_sec=20)
        results = await run_udp_broadcast_probe(
            h.udp_broadcast_probe,
            targets=["127.0.0.1"],
            source_ip="127.0.0.1",
            rate_limiter=rl,
        )
        assert results == {}

    @pytest.mark.asyncio
    async def test_tcp_probe_emits_evidence_and_lifts_manufacturer(self):
        port = _next_port()
        _tcp_responder(port, b"pr Lightware FrameServer 2.7.3\n")
        h = _make_hint("lightware_lw3", tcp_active_probe={
            "port": port, "send": {"ascii": "GET /sys/version\r\n"},
            "response_match": {"contains": "Lightware"},
            "timeout_ms": 2000,
            "extract": {
                "manufacturer": "Lightware",
                "version": {"regex": r"FrameServer (\S+)", "group": 1},
            },
        })
        ev = await run_tcp_active_probe(
            h.tcp_active_probe, target="127.0.0.1",
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
        h = _make_hint("strict", tcp_active_probe={
            "port": port, "send": {"ascii": "x"},
            "response_match": {"contains": "Lightware"},
            "timeout_ms": 1000,
        })
        ev = await run_tcp_active_probe(
            h.tcp_active_probe, target="127.0.0.1",
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
        h = _make_hint("vendor_test", udp_broadcast_probe={
            "port": port, "send": {"ascii": "WHOIS"},
            "response_match": {"contains": "FAKE-VENDOR"},
            "timeout_ms": 1500,
            "extract": {"manufacturer": "FakeVendor"},
        })
        rl = RateLimiter(rate_per_sec=20)
        results = await run_udp_broadcast_probe(
            h.udp_broadcast_probe, targets=["127.0.0.1"],
            source_ip="127.0.0.1", rate_limiter=rl,
        )
        ev = results["127.0.0.1"]
        # Phase 8.6 finalize step lifts manufacturer to vendor_string.
        derived = extract_vendor_strings([ev])
        assert any(
            d.data.get("kind") == "vendor_string"
            and d.data.get("value") == "fakevendor"
            for d in derived
        )


class TestBestDriverFirstIntegration:
    """Plan §9.4: a generic tcp_active_probe + vendor-specific driver
    with vendor_aliases produces the Phase 8.5 best-driver-first
    result (vendor primary, generic alternative).
    """

    @pytest.mark.asyncio
    async def test_generic_probe_yields_to_vendor_specific(self):
        port = _next_port()
        _tcp_responder(port, b"banner Vendor=NovaStar\n")
        # Generic driver: declares the TCP probe with generic=true.
        generic = _make_hint("unbranded_lw3", tcp_active_probe={
            "port": port, "send": {"ascii": "x"},
            "response_match": {"contains": "Vendor="},
            "generic": True,
            "extract": {
                "manufacturer": {"regex": r"Vendor=(\S+)", "group": 1},
            },
        })
        # Vendor-specific driver: claims the manufacturer string via
        # vendor_aliases. No probe of its own — it relies on Tier 4
        # vendor_string evidence lifted from the generic probe.
        vendor = _make_hint("novastar_specific", vendor_aliases=["NovaStar"])
        idx = build_signal_index([generic, vendor])
        matcher = TierMatcher(idx)

        ev = await run_tcp_active_probe(
            generic.tcp_active_probe, target="127.0.0.1",
            source_ip="127.0.0.1", stagger_ms=0,
        )
        assert ev is not None
        log = [ev]
        log.extend(extract_vendor_strings(log))
        match = matcher.match(log)
        # Vendor-specific driver wins as primary; generic relegated to
        # alternative per Phase 8.5 best-driver-first logic.
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
    await ctx.emit_broadcast(
        "custom_fake_vendor_companion",
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
        assert ev.data["source_id"] == "custom_fake_vendor_companion"
        assert ev.data["txt"]["manufacturer"] == "FakeVendor"

    @pytest.mark.asyncio
    async def test_hard_timeout_cuts_off_hung_companion(self, tmp_path, caplog):
        (tmp_path / "hang_discovery.py").write_text(_HANG_COMPANION)
        probes = load_discovery_companions([tmp_path])

        async def emit(host, ev):
            pass

        ctx = ProbeContext(
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
    """Plan §9.8: every probe in a test scan binds to the configured
    source_ip. Failing this test blocks the phase.
    """

    @pytest.mark.asyncio
    async def test_udp_probe_socket_binds_to_source_ip(self):
        # The UDP runner doesn't expose its socket, so verify the
        # _make_udp_socket helper that the runner uses always binds.
        sock = _make_udp_socket("127.0.0.1", broadcast=True)
        assert sock is not None
        try:
            bound_ip, _bound_port = sock.getsockname()
            assert bound_ip == "127.0.0.1", (
                "Phase 9 network-safety contract: UDP probes must bind "
                "to the configured source_ip"
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

        h = _make_hint("net_safety_tcp", tcp_active_probe={
            "port": 5555, "send": {"ascii": "x"},
            "response_match": {"contains": "y"},
            "timeout_ms": 500,
        })
        ev = await run_tcp_active_probe(
            h.tcp_active_probe, target="10.0.0.1",
            source_ip="192.0.2.5", stagger_ms=0,
        )
        assert ev is None
        assert captured["local_addr"] == ("192.0.2.5", 0), (
            "Phase 9 network-safety contract: TCP probes must pass "
            "local_addr=(source_ip, 0) to asyncio.open_connection"
        )
        # Restore (monkeypatch undoes this automatically; explicit for clarity).
        monkeypatch.setattr(asyncio, "open_connection", original)
