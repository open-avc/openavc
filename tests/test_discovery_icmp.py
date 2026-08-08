"""Tests for the pure-Python ICMP ping module (discovery.icmp).

Covers packet build/parse + checksum vectors, tiered method selection with
mocked socket failures, exec-ping failure classification, and the sweep's
error-vs-timeout accounting. All synthetic — no network access — except the
final loopback echo test, which exercises the real socket-tier send/receive
path against 127.0.0.1 where the kernel allows it (and skips elsewhere).
"""

from __future__ import annotations

import errno
import platform
import socket
import struct
import types
from unittest.mock import AsyncMock, patch

import pytest

from openavc.discovery import icmp
from openavc.discovery.network_scanner import ping_sweep


def _make_reply(request: bytes) -> bytes:
    """Turn an echo request into the matching echo reply (type 0)."""
    body = b"\x00" + request[1:2] + b"\x00\x00" + request[4:]
    csum = icmp.checksum_rfc1071(body)
    return body[:2] + struct.pack("!H", csum) + body[4:]


def _ipv4_header(total_payload: int) -> bytes:
    """Minimal-but-plausible 20-byte IPv4 header for prepend tests."""
    return struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, 20 + total_payload, 0, 0, 64, 1, 0,
        socket.inet_aton("192.168.1.50"), socket.inet_aton("192.168.1.10"),
    )


class TestChecksum:
    def test_known_vector_even_length(self):
        # Words: 0x0800 + 0x0000 + 0x0001 + 0x0001 = 0x0802 -> ~0x0802 = 0xF7FD
        data = b"\x08\x00\x00\x00\x00\x01\x00\x01"
        assert icmp.checksum_rfc1071(data) == 0xF7FD

    def test_known_vector_odd_length_pads_with_zero(self):
        # Words: 0x0102 + 0x0300 = 0x0402 -> ~0x0402 = 0xFBFD
        assert icmp.checksum_rfc1071(b"\x01\x02\x03") == 0xFBFD

    def test_carry_folding(self):
        # 0xFFFF + 0xFFFF = 0x1FFFE -> fold -> 0xFFFF -> ~ = 0x0000
        assert icmp.checksum_rfc1071(b"\xff\xff\xff\xff") == 0x0000

    def test_packet_with_valid_checksum_sums_to_zero(self):
        packet = icmp.build_echo_request(ident=0x1234, seq=7)
        assert icmp.checksum_rfc1071(packet) == 0


class TestEchoBuildParse:
    def test_request_structure(self):
        packet = icmp.build_echo_request(ident=0xABCD, seq=42)
        ptype, code, _csum, ident, seq = struct.unpack("!BBHHH", packet[:8])
        assert ptype == 8
        assert code == 0
        assert ident == 0xABCD
        assert seq == 42
        assert packet[8:] == icmp.ECHO_PAYLOAD

    def test_parse_accepts_matching_reply(self):
        reply = _make_reply(icmp.build_echo_request(ident=1, seq=42))
        assert icmp.parse_echo_reply(reply, expected_seq=42)

    def test_parse_accepts_reply_with_ipv4_header(self):
        # Raw sockets (and BSD dgram ICMP) deliver the IP header first
        reply = _make_reply(icmp.build_echo_request(ident=1, seq=9))
        assert icmp.parse_echo_reply(_ipv4_header(len(reply)) + reply, expected_seq=9)

    def test_parse_rejects_wrong_sequence(self):
        reply = _make_reply(icmp.build_echo_request(ident=1, seq=42))
        assert not icmp.parse_echo_reply(reply, expected_seq=43)

    def test_parse_rejects_echo_request(self):
        # Our own outgoing packet looped back must not count as a reply
        request = icmp.build_echo_request(ident=1, seq=42)
        assert not icmp.parse_echo_reply(request, expected_seq=42)

    def test_parse_rejects_corrupted_checksum(self):
        reply = bytearray(_make_reply(icmp.build_echo_request(ident=1, seq=42)))
        reply[2] ^= 0xFF
        assert not icmp.parse_echo_reply(bytes(reply), expected_seq=42)

    def test_parse_rejects_wrong_payload(self):
        reply = bytearray(_make_reply(icmp.build_echo_request(ident=1, seq=42)))
        reply[8] ^= 0xFF
        assert not icmp.parse_echo_reply(bytes(reply), expected_seq=42)

    def test_parse_rejects_short_packet(self):
        assert not icmp.parse_echo_reply(b"\x00\x00\x00", expected_seq=0)

    def test_kernel_rewritten_identifier_still_matches(self):
        # Linux dgram ICMP sockets rewrite the identifier; seq + payload
        # must be enough to correlate.
        request = icmp.build_echo_request(ident=0x1111, seq=5)
        rewritten = b"\x08\x00\x00\x00" + struct.pack("!HH", 0x9999, 5) + request[8:]
        reply = _make_reply(rewritten)
        assert icmp.parse_echo_reply(reply, expected_seq=5)


def _fake_socket_module(allowed_kinds: set[int]):
    """A socket-module stand-in whose socket() only allows certain kinds."""
    closed: list[int] = []

    class _FakeSock:
        def __init__(self, kind: int) -> None:
            self.kind = kind

        def close(self) -> None:
            closed.append(self.kind)

    def _ctor(family, kind, proto):
        assert family == socket.AF_INET
        assert proto == socket.IPPROTO_ICMP
        if kind not in allowed_kinds:
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return _FakeSock(kind)

    mod = types.SimpleNamespace(
        AF_INET=socket.AF_INET,
        SOCK_DGRAM=socket.SOCK_DGRAM,
        SOCK_RAW=socket.SOCK_RAW,
        IPPROTO_ICMP=socket.IPPROTO_ICMP,
        socket=_ctor,
    )
    return mod, closed


class TestMethodSelection:
    """Tier selection. ``_method_round_trips`` (the loopback self-test) is
    patched out here so these stay pure availability tests; the self-test's
    own fallthrough is covered in TestSelfTestFallthrough below."""

    async def test_windows_always_exec(self, monkeypatch):
        monkeypatch.setattr(icmp, "_IS_WINDOWS", True)
        assert await icmp.select_ping_method() == icmp.METHOD_EXEC

    async def test_posix_prefers_dgram(self, monkeypatch):
        monkeypatch.setattr(icmp, "_IS_WINDOWS", False)
        mod, closed = _fake_socket_module({socket.SOCK_DGRAM, socket.SOCK_RAW})
        monkeypatch.setattr(icmp, "socket", mod)
        monkeypatch.setattr(icmp, "_method_round_trips", AsyncMock(return_value=True))
        assert await icmp.select_ping_method() == icmp.METHOD_DGRAM
        assert closed == [socket.SOCK_DGRAM]  # probe socket released

    async def test_posix_falls_back_to_raw(self, monkeypatch):
        monkeypatch.setattr(icmp, "_IS_WINDOWS", False)
        mod, closed = _fake_socket_module({socket.SOCK_RAW})
        monkeypatch.setattr(icmp, "socket", mod)
        monkeypatch.setattr(icmp, "_method_round_trips", AsyncMock(return_value=True))
        assert await icmp.select_ping_method() == icmp.METHOD_RAW
        assert closed == [socket.SOCK_RAW]

    async def test_posix_falls_back_to_exec_ping(self, monkeypatch):
        monkeypatch.setattr(icmp, "_IS_WINDOWS", False)
        mod, _ = _fake_socket_module(set())
        monkeypatch.setattr(icmp, "socket", mod)
        monkeypatch.setattr(
            icmp, "shutil", types.SimpleNamespace(which=lambda _: "/usr/bin/ping"),
        )
        monkeypatch.setattr(icmp, "_method_round_trips", AsyncMock(return_value=True))
        assert await icmp.select_ping_method() == icmp.METHOD_EXEC

    async def test_posix_nothing_available(self, monkeypatch):
        monkeypatch.setattr(icmp, "_IS_WINDOWS", False)
        mod, _ = _fake_socket_module(set())
        monkeypatch.setattr(icmp, "socket", mod)
        monkeypatch.setattr(
            icmp, "shutil", types.SimpleNamespace(which=lambda _: None),
        )
        assert await icmp.select_ping_method() == icmp.METHOD_NONE


class TestSelfTestFallthrough:
    """A candidate that opens (or execs) but can't round-trip a loopback echo
    is skipped — this is the silent-empty-network guard."""

    async def test_dgram_opens_but_fails_selftest_falls_to_raw(self, monkeypatch):
        monkeypatch.setattr(icmp, "_IS_WINDOWS", False)
        mod, _ = _fake_socket_module({socket.SOCK_DGRAM, socket.SOCK_RAW})
        monkeypatch.setattr(icmp, "socket", mod)

        async def rt(method):
            return method == icmp.METHOD_RAW  # dgram self-test fails, raw works

        monkeypatch.setattr(icmp, "_method_round_trips", rt)
        assert await icmp.select_ping_method() == icmp.METHOD_RAW

    async def test_sockets_fail_selftest_fall_to_exec(self, monkeypatch):
        monkeypatch.setattr(icmp, "_IS_WINDOWS", False)
        mod, _ = _fake_socket_module({socket.SOCK_DGRAM, socket.SOCK_RAW})
        monkeypatch.setattr(icmp, "socket", mod)
        monkeypatch.setattr(
            icmp, "shutil", types.SimpleNamespace(which=lambda _: "/usr/bin/ping"),
        )

        async def rt(method):
            return method == icmp.METHOD_EXEC  # only exec round-trips

        monkeypatch.setattr(icmp, "_method_round_trips", rt)
        assert await icmp.select_ping_method() == icmp.METHOD_EXEC

    async def test_nothing_round_trips_is_none(self, monkeypatch):
        # A dgram socket opens but never answers and nothing else works:
        # report METHOD_NONE (loud warning) rather than "succeed" with a
        # method that would read every host as dead.
        monkeypatch.setattr(icmp, "_IS_WINDOWS", False)
        mod, _ = _fake_socket_module({socket.SOCK_DGRAM, socket.SOCK_RAW})
        monkeypatch.setattr(icmp, "socket", mod)
        monkeypatch.setattr(
            icmp, "shutil", types.SimpleNamespace(which=lambda _: "/usr/bin/ping"),
        )

        async def rt(method):
            return False

        monkeypatch.setattr(icmp, "_method_round_trips", rt)
        assert await icmp.select_ping_method() == icmp.METHOD_NONE


class TestPingHost:
    async def test_method_none_is_error(self):
        assert await icmp.ping_host("192.0.2.1", method=icmp.METHOD_NONE) == icmp.RESULT_ERROR

    async def test_exec_missing_binary_is_error_not_dead_host(self):
        # FileNotFoundError is an OSError: the old code returned False
        # ("host dead") here, which made a missing binary look like an
        # empty network.
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("ping"),
        ):
            result = await icmp.ping_host("192.0.2.1", method=icmp.METHOD_EXEC)
        assert result == icmp.RESULT_ERROR

    async def test_exec_zero_exit_is_alive(self):
        proc = AsyncMock()
        proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await icmp.ping_host("192.0.2.1", method=icmp.METHOD_EXEC)
        assert result == icmp.RESULT_ALIVE

    async def test_exec_nonzero_exit_is_timeout(self):
        proc = AsyncMock()
        proc.returncode = 1
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await icmp.ping_host("192.0.2.1", method=icmp.METHOD_EXEC)
        assert result == icmp.RESULT_TIMEOUT


class TestSweepAccounting:
    """ping_sweep counts errors separately from timeouts via PingSweepStats."""

    async def test_mixed_results(self):
        outcomes = {
            "192.168.77.1": icmp.RESULT_ALIVE,
            "192.168.77.2": icmp.RESULT_TIMEOUT,
            "192.168.77.3": icmp.RESULT_ERROR,
            "192.168.77.4": icmp.RESULT_ALIVE,
            "192.168.77.5": icmp.RESULT_TIMEOUT,
            "192.168.77.6": icmp.RESULT_TIMEOUT,
        }

        async def fake_ping(ip, timeout=1.0, source_ip="", method=""):
            return outcomes[ip]

        found: list[str] = []

        async def on_found(ip: str) -> None:
            found.append(ip)

        stats = icmp.PingSweepStats()
        with patch.object(icmp, "select_ping_method", return_value=icmp.METHOD_DGRAM), \
             patch.object(icmp, "ping_host", side_effect=fake_ping):
            alive = await ping_sweep(
                ["192.168.77.0/29"], on_found=on_found, stats=stats,
            )

        assert alive == ["192.168.77.1", "192.168.77.4"]
        assert sorted(found) == alive
        assert stats.method == icmp.METHOD_DGRAM
        assert stats.total == 6
        assert stats.alive == 2
        assert stats.timeouts == 3
        assert stats.errors == 1

    async def test_no_method_available_marks_all_errors(self):
        stats = icmp.PingSweepStats()
        ping_called = False

        async def fake_ping(*args, **kwargs):
            nonlocal ping_called
            ping_called = True
            return icmp.RESULT_ALIVE

        with patch.object(icmp, "select_ping_method", return_value=icmp.METHOD_NONE), \
             patch.object(icmp, "ping_host", side_effect=fake_ping):
            alive = await ping_sweep(["192.168.77.0/29"], stats=stats)

        assert alive == []
        assert not ping_called  # no point attempting per-host pings
        assert stats.method == icmp.METHOD_NONE
        assert stats.total == 6
        assert stats.errors == 6
        assert stats.alive == 0

    async def test_source_ip_passed_through(self):
        seen_kwargs: list[dict] = []

        async def fake_ping(ip, timeout=1.0, source_ip="", method=""):
            seen_kwargs.append({"source_ip": source_ip, "method": method})
            return icmp.RESULT_TIMEOUT

        with patch.object(icmp, "select_ping_method", return_value=icmp.METHOD_RAW), \
             patch.object(icmp, "ping_host", side_effect=fake_ping):
            await ping_sweep(["192.168.77.0/30"], source_ip="192.168.77.10")

        assert seen_kwargs
        assert all(k["source_ip"] == "192.168.77.10" for k in seen_kwargs)
        assert all(k["method"] == icmp.METHOD_RAW for k in seen_kwargs)


class TestSocketPingErrnoClassification:
    """OSError classification in the socket ping path."""

    @staticmethod
    def _raising_socket_module(exc: OSError):
        def _ctor(family, kind, proto):
            raise exc

        return types.SimpleNamespace(
            AF_INET=socket.AF_INET,
            SOCK_DGRAM=socket.SOCK_DGRAM,
            SOCK_RAW=socket.SOCK_RAW,
            IPPROTO_ICMP=socket.IPPROTO_ICMP,
            socket=_ctor,
        )

    async def test_host_unreachable_is_timeout(self, monkeypatch):
        exc = OSError(errno.EHOSTUNREACH, "No route to host")
        monkeypatch.setattr(icmp, "socket", self._raising_socket_module(exc))
        result = await icmp._ping_socket("192.0.2.1", 0.1, "", icmp.METHOD_DGRAM)
        assert result == icmp.RESULT_TIMEOUT

    async def test_permission_error_is_error(self, monkeypatch):
        exc = OSError(errno.EPERM, "Operation not permitted")
        monkeypatch.setattr(icmp, "socket", self._raising_socket_module(exc))
        result = await icmp._ping_socket("192.0.2.1", 0.1, "", icmp.METHOD_DGRAM)
        assert result == icmp.RESULT_ERROR


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="ICMP sockets need Administrator on Windows (exec tier is used there)",
)
class TestLiveLoopbackEcho:
    """Real socket-tier ping against 127.0.0.1.

    The mocked tests above can't catch integration faults between the
    socket tiers and the event loop (e.g. address resolution applied to a
    literal IP with an ICMP socket type, which glibc rejects). One real
    echo against loopback proves the full send/receive path on any POSIX
    machine whose kernel permits ICMP sockets; skips where it doesn't.
    """

    async def test_loopback_echo_is_alive(self):
        method = await icmp.select_ping_method()
        if method not in (icmp.METHOD_DGRAM, icmp.METHOD_RAW):
            pytest.skip("no ICMP socket available in this environment")
        result = await icmp.ping_host("127.0.0.1", timeout=2.0, method=method)
        assert result == icmp.RESULT_ALIVE
