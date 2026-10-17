"""The adapter list on Linux and Android (Settings > Network's two pickers).

The kernel creates interfaces of its own that hold no address and are no
network a person would choose: the fallback device of each tunnel module
(sit0, ip6tnl0, ip_vti0, ...), dummy0, and the ifb devices traffic shaping
uses. Android loads all of them; shown, they read as adapters "not connected"
or "with no address yet".

The modules are invented here; nothing reads this machine's adapters.
"""

from __future__ import annotations

import socket
import sys
import types

from openavc.discovery import network_scanner

LINK = -1

# What one Android appliance reported with its Ethernet in use and its Wi-Fi
# switched on but not joined.
ANDROID_INTERFACES = {
    "lo": [(LINK, "00:00:00:00:00:00"), (socket.AF_INET, "127.0.0.1")],
    "eth0": [(LINK, "aa:00:00:00:00:00"), (socket.AF_INET, "10.20.30.5")],
    "wlan0": [(LINK, "aa:00:00:00:00:01")],
    "dummy0": [(LINK, "aa:00:00:00:01:00")],
    "ifb0": [(LINK, "aa:00:00:00:02:00")],
    "ifb1": [(LINK, "aa:00:00:00:02:01")],
    "ip_vti0": [(LINK, "00:00:00:00")],
    "ip6_vti0": [(LINK, "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00")],
    "sit0": [(LINK, "00:00:00:00")],
    "ip6tnl0": [(LINK, "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00")],
}


def _fake_linux(monkeypatch, interfaces):
    ifaddr = types.ModuleType("ifaddr")
    ifaddr.get_adapters = lambda: [
        types.SimpleNamespace(
            nice_name=name, ips=[types.SimpleNamespace(ip=address, network_prefix=24)],
        )
        for name, entries in interfaces.items()
        for family, address in entries
        if family == socket.AF_INET
    ]
    monkeypatch.setitem(sys.modules, "ifaddr", ifaddr)
    psutil = types.ModuleType("psutil")
    psutil.AF_LINK = LINK
    psutil.net_if_addrs = lambda: {
        name: [types.SimpleNamespace(family=f, address=a) for f, a in entries]
        for name, entries in interfaces.items()
    }
    psutil.net_if_stats = lambda: {
        name: types.SimpleNamespace(isup=name != "wlan0") for name in interfaces
    }
    monkeypatch.setitem(sys.modules, "psutil", psutil)
    monkeypatch.setattr(network_scanner, "_IS_MACOS", False)


def _names() -> set[str]:
    return {a["name"] for a in network_scanner.get_adapter_inventory()}


def test_android_lists_only_its_ethernet_and_wifi(monkeypatch):
    _fake_linux(monkeypatch, ANDROID_INTERFACES)
    assert _names() == {"eth0", "wlan0"}


def test_wifi_with_no_address_stays_as_not_connected(monkeypatch):
    """Wi-Fi switched off or not joined is still the adapter someone may choose."""
    _fake_linux(monkeypatch, ANDROID_INTERFACES)
    by_name = {a["name"]: a for a in network_scanner.get_adapter_inventory()}
    assert by_name["wlan0"]["has_address"] is False
    assert by_name["wlan0"]["link"] is False


def test_a_kernel_interface_with_an_address_is_never_hidden(monkeypatch):
    """Someone gave dummy0 an address on purpose: that is a network."""
    interfaces = dict(ANDROID_INTERFACES, dummy0=[(LINK, "aa:00:00:00:01:00"), (socket.AF_INET, "10.99.0.1")])
    _fake_linux(monkeypatch, interfaces)
    assert "dummy0" in _names()


def test_names_that_only_start_alike_stay(monkeypatch):
    """A USB adapter or a bridge someone named is not the kernel's own device."""
    interfaces = {
        "eth0": [(LINK, "aa:00:00:00:00:00"), (socket.AF_INET, "10.20.30.5")],
        "dummynet": [(LINK, "aa:00:00:00:00:02")],
        "sitelan": [(LINK, "aa:00:00:00:00:03")],
        "enx782d7e168d45": [(LINK, "aa:00:00:00:00:04")],
    }
    _fake_linux(monkeypatch, interfaces)
    assert _names() == {"eth0", "dummynet", "sitelan", "enx782d7e168d45"}
