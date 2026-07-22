"""Tests for per-interface multicast joins/sends and advertiser resilience.

Covers discovery.multicast helpers (join fallback chain, per-interface
sends), the scanners' env_error surface, response dedup by source IP, and
the mDNS advertiser's never-crash startup with retry/backoff. All sockets
are mocked — no network access.
"""

from __future__ import annotations

import asyncio
import errno
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from server.discovery import amx_ddp_scanner, mdns_advertiser, mdns_scanner
from server.discovery.mdns_advertiser import MDNSAdvertiser
from server.discovery.mdns_scanner import MDNSScanner
from server.discovery.multicast import (
    ANY_INTERFACE,
    join_group_on_interfaces,
    send_per_interface,
)
from server.discovery.ssdp_scanner import SEARCH_TARGETS, SSDPScanner

GROUP = "224.0.0.251"


class FakeJoinSock:
    """Records IP_ADD_MEMBERSHIP joins; fails for configured interfaces."""

    def __init__(self, fail_ifaces: tuple[str, ...] = ()) -> None:
        self.joins: list[str] = []
        self.fail_ifaces = set(fail_ifaces)

    def setsockopt(self, level, opt, value) -> None:
        if opt == socket.IP_ADD_MEMBERSHIP:
            iface = socket.inet_ntoa(value[4:8])
            if iface in self.fail_ifaces:
                raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
            self.joins.append(iface)


class FakeSendSock:
    """Records IP_MULTICAST_IF pins and sendto calls; fails per interface."""

    def __init__(self, fail_ifaces: tuple[str, ...] = ()) -> None:
        self.pins: list[str] = []
        self.sends: list[tuple[str | None, bytes, tuple]] = []
        self.fail_ifaces = set(fail_ifaces)
        self._current: str | None = None

    def setsockopt(self, level, opt, value) -> None:
        if opt == socket.IP_MULTICAST_IF:
            self._current = socket.inet_ntoa(value)
            self.pins.append(self._current)

    def sendto(self, payload, dest) -> None:
        if self._current in self.fail_ifaces:
            raise OSError(errno.ENETUNREACH, "Network is unreachable")
        self.sends.append((self._current, payload, dest))


class TestJoinGroupOnInterfaces:
    def test_control_ip_joins_only_that_interface(self):
        sock = FakeJoinSock()
        joined = join_group_on_interfaces(
            sock, GROUP, control_ip="10.0.0.5",
            interface_ips=["192.168.1.7"],  # must be ignored
        )
        assert joined == ["10.0.0.5"]
        assert sock.joins == ["10.0.0.5"]

    def test_control_ip_failure_returns_empty(self):
        sock = FakeJoinSock(fail_ifaces=("10.0.0.5",))
        joined = join_group_on_interfaces(sock, GROUP, control_ip="10.0.0.5")
        assert joined == []

    def test_per_interface_joins_tolerate_partial_failure(self):
        sock = FakeJoinSock(fail_ifaces=("10.0.0.5",))
        joined = join_group_on_interfaces(
            sock, GROUP, interface_ips=["10.0.0.5", "192.168.1.7"],
        )
        assert joined == ["192.168.1.7"]
        # One interface worked — no INADDR_ANY fallback join
        assert ANY_INTERFACE not in sock.joins

    def test_all_interfaces_fail_falls_back_to_any(self):
        sock = FakeJoinSock(fail_ifaces=("10.0.0.5", "192.168.1.7"))
        joined = join_group_on_interfaces(
            sock, GROUP, interface_ips=["10.0.0.5", "192.168.1.7"],
        )
        assert joined == [ANY_INTERFACE]
        assert sock.joins == [ANY_INTERFACE]

    def test_no_enumerable_interfaces_falls_back_to_any(self):
        sock = FakeJoinSock()
        joined = join_group_on_interfaces(sock, GROUP, interface_ips=[])
        assert joined == [ANY_INTERFACE]

    def test_everything_fails_returns_empty_without_raising(self):
        sock = FakeJoinSock(fail_ifaces=("10.0.0.5", ANY_INTERFACE))
        joined = join_group_on_interfaces(
            sock, GROUP, interface_ips=["10.0.0.5"],
        )
        assert joined == []


class TestSendPerInterface:
    def test_pins_and_sends_once_per_interface(self):
        sock = FakeSendSock()
        sent = send_per_interface(
            sock, b"payload", (GROUP, 5353), ["10.0.0.5", "192.168.1.7"],
        )
        assert sent == 2
        assert sock.pins == ["10.0.0.5", "192.168.1.7"]
        assert [s[0] for s in sock.sends] == ["10.0.0.5", "192.168.1.7"]

    def test_tolerates_per_interface_send_failure(self):
        sock = FakeSendSock(fail_ifaces=("10.0.0.5",))
        sent = send_per_interface(
            sock, b"payload", (GROUP, 5353), ["10.0.0.5", "192.168.1.7"],
        )
        assert sent == 1
        assert [s[0] for s in sock.sends] == ["192.168.1.7"]

    def test_any_interface_sends_unpinned(self):
        sock = FakeSendSock()
        sent = send_per_interface(sock, b"payload", (GROUP, 5353), [ANY_INTERFACE])
        assert sent == 1
        assert sock.pins == []  # no IP_MULTICAST_IF pin for the fallback

    def test_empty_interface_list_sends_nothing(self):
        sock = FakeSendSock()
        assert send_per_interface(sock, b"p", (GROUP, 5353), []) == 0


class TestScannerEnvError:
    async def test_mdns_scanner_records_env_error(self, monkeypatch):
        def boom(control_ip=""):
            raise OSError("could not join 224.0.0.251 on any interface")

        monkeypatch.setattr(mdns_scanner, "_create_mdns_socket", boom)
        scanner = MDNSScanner()
        results = await scanner.start(duration=0.1)
        assert results == {}
        assert scanner.env_error is not None
        assert "mDNS" in scanner.env_error

    async def test_amx_ddp_scanner_records_env_error(self, monkeypatch):
        def boom(control_ip=""):
            raise OSError("could not join 239.255.250.250 on any interface")

        monkeypatch.setattr(amx_ddp_scanner, "_create_ddp_socket", boom)
        scanner = amx_ddp_scanner.AMXDDPScanner()
        results = await scanner.start(duration=0.1)
        assert results == {}
        assert scanner.env_error is not None

    async def test_ssdp_all_sends_failed_records_env_error(self):
        scanner = SSDPScanner()
        # M-SEARCH goes out on the dedicated ephemeral search socket.
        sock = MagicMock()
        sock.sendto.side_effect = OSError(errno.ENETUNREACH, "unreachable")
        scanner._search_sock = sock
        scanner._send_ifaces = ["10.0.0.5", "192.168.1.7"]

        await scanner._send_searches()

        assert scanner.env_error is not None
        assert "M-SEARCH" in scanner.env_error

    async def test_ssdp_successful_sends_leave_env_error_unset(self):
        scanner = SSDPScanner()
        sock = MagicMock()
        scanner._search_sock = sock
        scanner._send_ifaces = ["10.0.0.5", "192.168.1.7"]

        await scanner._send_searches()

        assert scanner.env_error is None
        assert sock.sendto.call_count == len(SEARCH_TARGETS) * 2


class TestResponseDedupBySource:
    def test_mdns_results_keyed_by_source_ip(self):
        # The same announcement heard on two interfaces produces ONE result
        scanner = MDNSScanner()
        packet = _mdns_announcement_packet()
        scanner._process_response(packet, "192.168.1.50")
        scanner._process_response(packet, "192.168.1.50")
        assert len(scanner.results) == 1

    def test_ssdp_results_keyed_by_source_ip(self):
        scanner = SSDPScanner()
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"ST: urn:schemas-upnp-org:device:Basic:1\r\n"
            b"USN: uuid:abc\r\n"
            b"\r\n"
        )
        scanner._process_response(response, "192.168.1.60")
        scanner._process_response(response, "192.168.1.60")
        assert len(scanner.results) == 1


def _mdns_announcement_packet() -> bytes:
    """A minimal mDNS response: PTR + SRV + A for one service instance."""
    records = mdns_advertiser.build_announcement_records(
        instance_name="TestDevice",
        service_type="_http._tcp.local.",
        hostname="testdevice",
        ip="192.168.1.50",
        port=80,
        txt_pairs={},
    )
    return mdns_advertiser.build_dns_response(records)


class TestAdvertiserResilience:
    async def test_start_does_not_raise_when_network_down(self, monkeypatch):
        def boom():
            raise OSError(errno.EADDRNOTAVAIL, "no network")

        monkeypatch.setattr(mdns_advertiser, "_create_advertiser_socket", boom)
        adv = MDNSAdvertiser("Test", "instance-id", 8080, "1.0")

        await adv.start()  # must not raise

        assert adv._sock is None
        assert adv._retry_task is not None
        assert not adv._retry_task.done()

        await adv.stop()
        assert adv._retry_task.done()

    async def test_retry_recovers_when_network_appears(self, monkeypatch):
        attempts = {"n": 0}
        mock_sock = MagicMock()

        def fake_create():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError(errno.EADDRNOTAVAIL, "no network yet")
            return mock_sock, ["10.0.0.5"]

        monkeypatch.setattr(mdns_advertiser, "_create_advertiser_socket", fake_create)
        monkeypatch.setattr(mdns_advertiser, "RETRY_DELAYS", (0.01,))
        monkeypatch.setattr(mdns_advertiser, "ANNOUNCEMENT_INTERVAL", 0)

        adv = MDNSAdvertiser("Test", "instance-id", 8080, "1.0")
        monkeypatch.setattr(adv, "_send_announcement", AsyncMock())
        monkeypatch.setattr(adv, "_responder_loop", AsyncMock())

        await adv.start()
        assert adv._sock is None  # first attempt failed

        for _ in range(100):
            await asyncio.sleep(0.01)
            if adv._sock is not None:
                break

        assert adv._sock is mock_sock
        assert adv._joined_ips == ["10.0.0.5"]
        assert attempts["n"] == 2

        monkeypatch.setattr(adv, "_send_goodbye", AsyncMock())
        await adv.stop()

    async def test_stop_during_retry_cancels_cleanly(self, monkeypatch):
        def boom():
            raise OSError("still no network")

        monkeypatch.setattr(mdns_advertiser, "_create_advertiser_socket", boom)
        monkeypatch.setattr(mdns_advertiser, "RETRY_DELAYS", (30.0,))

        adv = MDNSAdvertiser("Test", "instance-id", 8080, "1.0")
        await adv.start()
        await adv.stop()  # must return promptly, not wait 30s

        assert adv._retry_task.done()


class TestAdvertiserPerInterfaceAnnouncements:
    async def test_announcement_per_interface_with_own_a_record(self):
        adv = MDNSAdvertiser("Test", "instance-id", 8080, "1.0")
        adv._sock = FakeSendSock()
        adv._hostname = "testhost"
        adv._joined_ips = ["10.0.0.5", "192.168.2.6"]

        await adv._send_announcement()

        sent_packets = [packet for _iface, packet, _dest in adv._sock.sends]
        assert len(sent_packets) == 2
        # Each interface's packet advertises that interface's own IP
        assert socket.inet_aton("10.0.0.5") in sent_packets[0]
        assert socket.inet_aton("192.168.2.6") not in sent_packets[0]
        assert socket.inet_aton("192.168.2.6") in sent_packets[1]
        # Outbound pinned per interface
        assert adv._sock.pins == ["10.0.0.5", "192.168.2.6"]


class TestNoUnimplementedLoopSocketAPIs:
    """uvloop, the event loop on Linux deployments, does not implement
    loop.sock_sendto or loop.sock_recvfrom — both raise NotImplementedError
    at runtime. Server code must use executor-based sendto/recvfrom instead.
    The calls work fine on Windows dev (selector loop implements them), so
    only this guard catches the mistake before it ships.
    """

    def test_no_sock_sendto_or_recvfrom_in_server(self):
        server_root = Path(__file__).resolve().parents[1] / "server"
        offenders: list[str] = []
        for path in server_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if ".sock_sendto(" in text or ".sock_recvfrom(" in text:
                offenders.append(path.name)
        assert offenders == [], (
            f"loop.sock_sendto/sock_recvfrom found in {offenders} — "
            "uvloop raises NotImplementedError for these; use "
            "run_in_executor with sock.sendto/recvfrom instead"
        )
