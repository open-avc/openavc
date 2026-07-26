"""Tests for the ARP harvest (discovery.network_scanner).

Covers the Linux /proc/net/arp reader and the BSD/macOS ``arp -a`` parser.
Synthetic content only — no real network or OS ARP table.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from server.discovery import network_scanner
from server.discovery.network_scanner import (
    _harvest_arp_linux,
    _parse_bsd_arp,
    _parse_proc_net_arp,
    harvest_arp_table,
)

_HEADER = "IP address       HW type     Flags       HW address            Mask     Device\n"


class TestProcNetArpParser:
    def test_parses_complete_entries(self):
        text = _HEADER + (
            "192.168.1.1      0x1         0x2         a4:91:b1:aa:bb:cc     *        wlan0\n"
            "192.168.1.20     0x1         0x2         00:1A:2B:3C:4D:5E     *        eth0\n"
        )
        result = _parse_proc_net_arp(text)
        assert result == {
            "192.168.1.1": "a4:91:b1:aa:bb:cc",
            "192.168.1.20": "00:1a:2b:3c:4d:5e",  # normalized to lowercase
        }

    def test_skips_incomplete_entries(self):
        # Flags 0x0 = incomplete (ARP request never answered)
        text = _HEADER + (
            "192.168.1.99     0x1         0x0         00:00:00:00:00:00     *        wlan0\n"
            "192.168.1.1      0x1         0x2         a4:91:b1:aa:bb:cc     *        wlan0\n"
        )
        assert _parse_proc_net_arp(text) == {"192.168.1.1": "a4:91:b1:aa:bb:cc"}

    def test_skips_zero_mac_even_when_flags_complete(self):
        text = _HEADER + (
            "192.168.1.99     0x1         0x2         00:00:00:00:00:00     *        wlan0\n"
        )
        assert _parse_proc_net_arp(text) == {}

    def test_skips_broadcast_mac(self):
        text = _HEADER + (
            "192.168.1.255    0x1         0x2         ff:ff:ff:ff:ff:ff     *        wlan0\n"
        )
        assert _parse_proc_net_arp(text) == {}

    def test_skips_malformed_lines(self):
        text = _HEADER + (
            "garbage\n"
            "\n"
            "192.168.1.5      0x1         notahexflag a4:91:b1:aa:bb:cc     *        wlan0\n"
            "192.168.1.6      0x1         0x2         not-a-mac             *        wlan0\n"
            "192.168.1.7      0x1         0x2         a4:91:b1:aa:bb:cc     *        wlan0\n"
        )
        assert _parse_proc_net_arp(text) == {"192.168.1.7": "a4:91:b1:aa:bb:cc"}

    def test_permanent_entries_kept(self):
        # Flags 0x6 = ATF_COM | ATF_PERM (static entry) — still a valid MAC
        text = _HEADER + (
            "192.168.1.2      0x1         0x6         a4:91:b1:aa:bb:cc     *        eth0\n"
        )
        assert _parse_proc_net_arp(text) == {"192.168.1.2": "a4:91:b1:aa:bb:cc"}

    def test_empty_table(self):
        assert _parse_proc_net_arp(_HEADER) == {}


class TestHarvestArpLinux:
    async def test_reads_proc_net_arp(self, monkeypatch, tmp_path):
        arp_file = tmp_path / "arp"
        arp_file.write_text(
            _HEADER
            + "192.168.1.1      0x1         0x2         a4:91:b1:aa:bb:cc     *        wlan0\n"
        )
        monkeypatch.setattr(network_scanner, "_PROC_NET_ARP", str(arp_file))
        fallback = AsyncMock(return_value={})
        monkeypatch.setattr(network_scanner, "_harvest_arp_ip_neigh", fallback)

        result = await _harvest_arp_linux()

        assert result == {"192.168.1.1": "a4:91:b1:aa:bb:cc"}
        fallback.assert_not_awaited()

    async def test_falls_back_to_ip_neigh_when_unreadable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            network_scanner, "_PROC_NET_ARP", str(tmp_path / "does-not-exist"),
        )
        fallback = AsyncMock(return_value={"192.168.1.9": "aa:bb:cc:dd:ee:ff"})
        monkeypatch.setattr(network_scanner, "_harvest_arp_ip_neigh", fallback)

        result = await _harvest_arp_linux()

        assert result == {"192.168.1.9": "aa:bb:cc:dd:ee:ff"}
        fallback.assert_awaited_once()

    async def test_harvest_arp_table_routes_posix_to_proc(self, monkeypatch, tmp_path):
        arp_file = tmp_path / "arp"
        arp_file.write_text(
            _HEADER
            + "10.0.0.1         0x1         0x2         11:22:33:44:55:66     *        eth0\n"
        )
        monkeypatch.setattr(network_scanner, "_IS_WINDOWS", False)
        monkeypatch.setattr(network_scanner, "_IS_MACOS", False)
        monkeypatch.setattr(network_scanner, "_PROC_NET_ARP", str(arp_file))

        result = await harvest_arp_table()

        assert result == {"10.0.0.1": "11:22:33:44:55:66"}


# Real-shaped macOS output: unresolved hosts print "?", resolved ones print a
# name, and octets lose their leading zeros.
_BSD_SAMPLE = (
    "? (192.168.4.1) at d0:cb:dd:4c:3e:4d on en0 ifscope [ethernet]\n"
    "? (192.168.4.59) at 0:a:45:2a:f6:7 on en0 ifscope [ethernet]\n"
    "? (192.168.4.71) at (incomplete) on en0 ifscope [ethernet]\n"
    "? (192.168.7.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]\n"
    "mdns.mcast.net (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]\n"
)


class TestBsdArpParser:
    def test_parses_macos_output(self):
        result = _parse_bsd_arp(_BSD_SAMPLE)
        assert result == {
            "192.168.4.1": "d0:cb:dd:4c:3e:4d",
            # Zero-padded: without this, OUI lookup can never match.
            "192.168.4.59": "00:0a:45:2a:f6:07",
            "224.0.0.251": "01:00:5e:00:00:fb",
        }

    def test_zero_pads_every_short_octet(self):
        text = "? (10.0.0.5) at 0:0:0:1:2:3 on en0 ifscope [ethernet]\n"
        assert _parse_bsd_arp(text) == {"10.0.0.5": "00:00:00:01:02:03"}

    def test_skips_incomplete_entries(self):
        text = "? (10.0.0.9) at (incomplete) on en0 ifscope [ethernet]\n"
        assert _parse_bsd_arp(text) == {}

    def test_skips_broadcast_and_zero_macs(self):
        text = (
            "? (10.0.0.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]\n"
            "? (10.0.0.7) at 0:0:0:0:0:0 on en0 ifscope [ethernet]\n"
        )
        assert _parse_bsd_arp(text) == {}

    def test_reads_ip_from_parentheses_not_hostname(self):
        text = "host.example.lan (10.0.0.3) at aa:bb:cc:dd:ee:ff on en0 [ethernet]\n"
        assert _parse_bsd_arp(text) == {"10.0.0.3": "aa:bb:cc:dd:ee:ff"}

    def test_uppercase_normalized(self):
        text = "? (10.0.0.4) at AA:BB:CC:DD:EE:FF on en0 [ethernet]\n"
        assert _parse_bsd_arp(text) == {"10.0.0.4": "aa:bb:cc:dd:ee:ff"}

    def test_empty_and_garbage(self):
        assert _parse_bsd_arp("") == {}
        assert _parse_bsd_arp("arp: no entries\n") == {}


class TestHarvestArpMacos:
    async def test_harvest_arp_table_routes_darwin_to_bsd(self, monkeypatch):
        async def fake_exec(*args, **kwargs):
            assert args[:2] == ("arp", "-a")

            class _Proc:
                async def communicate(self):
                    return _BSD_SAMPLE.encode(), b""

            return _Proc()

        monkeypatch.setattr(network_scanner, "_IS_WINDOWS", False)
        monkeypatch.setattr(network_scanner, "_IS_MACOS", True)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        result = await harvest_arp_table()

        assert result["192.168.4.59"] == "00:0a:45:2a:f6:07"
        assert "192.168.4.71" not in result  # incomplete entry
