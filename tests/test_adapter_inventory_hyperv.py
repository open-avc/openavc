"""The adapter list on a Hyper-V guest and on a Hyper-V host.

Windows describes a guest's network card as "Microsoft Hyper-V Network
Adapter". It is that machine's only network, and it was dropped as virtual:
OpenAVC installed on a Windows virtual machine (Hyper-V on site, or Azure)
listed no adapter in Settings > Network, and a Discovery scan had no network
to sweep.

On a host, an External switch moves the wired card's address and MAC to a
"vEthernet (<switch>)" card. Hyper-V's other cards on the host (the Default
Switch, WSL's, an Internal switch) get a MAC from Hyper-V's own range,
00-15-5D, and stay out of the list; the one that carries the physical card's
MAC is the wired network itself, and it was dropped too.

The modules are invented here; nothing reads this machine's adapters. Names,
descriptions and MAC shapes are as Windows 11 and Windows Server 2025 report
them; the addresses are made up.
"""

from __future__ import annotations

import socket
import sys
import types

from openavc import reach_devices
from openavc.discovery import network_scanner

LINK = -1

# (connection name, hardware description, MAC, IPv4 address or None, prefix)
GUEST = [
    ("Ethernet", "Microsoft Hyper-V Network Adapter", "00-15-5D-64-7F-00", "10.20.30.5", 24),
]

HOST_WITH_EXTERNAL_SWITCH = [
    ("vEthernet (Bench)", "Hyper-V Virtual Ethernet Adapter #2", "50-EB-F6-00-00-01", "10.20.30.7", 24),
    ("vEthernet (Default Switch)", "Hyper-V Virtual Ethernet Adapter", "00-15-5D-28-41-4D", "172.24.176.1", 20),
    ("vEthernet (WSL (Hyper-V firewall))", "Hyper-V Virtual Ethernet Adapter #3", "00-15-5D-A7-A4-DC", "172.30.144.1", 20),
    ("Wi-Fi", "Intel(R) Wi-Fi 6E AX211 160MHz", "F0-57-A6-00-00-02", "192.168.4.152", 22),
]


def _fake_windows(monkeypatch, cards):
    ifaddr = types.ModuleType("ifaddr")
    ifaddr.get_adapters = lambda: [
        types.SimpleNamespace(
            nice_name=description,
            ips=[types.SimpleNamespace(ip=address, network_prefix=prefix)] if address else [],
        )
        for _name, description, _mac, address, prefix in cards
    ]
    monkeypatch.setitem(sys.modules, "ifaddr", ifaddr)
    psutil = types.ModuleType("psutil")
    psutil.AF_LINK = LINK
    psutil.net_if_addrs = lambda: {
        name: [types.SimpleNamespace(family=LINK, address=mac)]
        + ([types.SimpleNamespace(family=socket.AF_INET, address=address)] if address else [])
        for name, _description, mac, address, _prefix in cards
    }
    psutil.net_if_stats = lambda: {
        name: types.SimpleNamespace(isup=True) for name, *_ in cards
    }
    monkeypatch.setitem(sys.modules, "psutil", psutil)
    monkeypatch.setattr(network_scanner, "_IS_MACOS", False)


def _inventory() -> dict[str, dict]:
    return {a["name"]: a for a in network_scanner.get_adapter_inventory()}


def test_a_guest_lists_its_network_card(monkeypatch):
    _fake_windows(monkeypatch, GUEST)
    card = _inventory()["Ethernet"]
    assert card["addresses"] == [{"ip": "10.20.30.5", "subnet": "10.20.30.0/24"}]
    assert card["link"] is True


def test_a_guest_scans_and_broadcasts_on_its_network(monkeypatch):
    _fake_windows(monkeypatch, GUEST)
    assert network_scanner.get_local_subnets() == ["10.20.30.0/24"]
    assert network_scanner.get_interface_ips() == ["10.20.30.5"]
    assert reach_devices.broadcast_sources() == ["10.20.30.5"]


def test_a_host_lists_the_external_switch_and_not_hyper_vs_own_networks(monkeypatch):
    _fake_windows(monkeypatch, HOST_WITH_EXTERNAL_SWITCH)
    assert set(_inventory()) == {"vEthernet (Bench)", "Wi-Fi"}
    assert set(network_scanner.get_local_subnets()) == {"10.20.30.0/24", "192.168.4.0/22"}
    assert set(network_scanner.get_interface_ips()) == {"10.20.30.7", "192.168.4.152"}
    assert set(reach_devices.broadcast_sources()) == {"10.20.30.7", "192.168.4.152"}


def test_an_external_switch_waiting_for_an_address_stays_listed(monkeypatch):
    """Like an unplugged port: the choice must stay visible while it has no
    address. An Internal switch with none stays out."""
    cards = [
        ("vEthernet (Bench)", "Hyper-V Virtual Ethernet Adapter #2", "50-EB-F6-00-00-01", None, 24),
        ("vEthernet (Lab)", "Hyper-V Virtual Ethernet Adapter #4", "00-15-5D-01-02-03", None, 24),
    ]
    _fake_windows(monkeypatch, cards)
    assert set(_inventory()) == {"vEthernet (Bench)"}
    assert _inventory()["vEthernet (Bench)"]["has_address"] is False


def test_hyper_vs_own_cards_on_a_host_stay_out(monkeypatch):
    """No External switch: nothing Hyper-V made is a network to choose, and
    the switch's hidden extension card never was."""
    cards = [
        ("vEthernet (Default Switch)", "Hyper-V Virtual Ethernet Adapter", "00-15-5D-28-41-4D", "172.24.176.1", 20),
        ("vSwitch (Default Switch)", "Hyper-V Virtual Switch Extension Adapter", "", None, 24),
        ("Ethernet", "Realtek Gaming 2.5GbE Family Controller", "50-EB-F6-00-00-01", "10.20.30.7", 24),
    ]
    _fake_windows(monkeypatch, cards)
    assert set(_inventory()) == {"Ethernet"}
    assert network_scanner.get_local_subnets() == ["10.20.30.0/24"]
