"""The adapter list on a Mac (Settings > Network's two pickers).

A Mac lists about ten interfaces of its own that hold no address and are no
network a person would choose: AirDrop's, the Wi-Fi access point's, Apple
silicon's internal ones, and each Thunderbolt port, whose network is
Thunderbolt Bridge. Shown, they read as adapters "with no address yet". Some
are told apart by name; the Thunderbolt ports and the "Ethernet Adapter"
interfaces take the same names (en1, en2, ...) a real second Ethernet port
takes on another Mac, so for those macOS's own list of network services
decides: an interface no service uses never gets an address.

The modules and the networksetup output are invented here; nothing reads this
machine's adapters.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import types

import pytest

from openavc.discovery import network_scanner

LINK = -1

# What one Apple silicon laptop reported: Wi-Fi and a USB Ethernet adapter in
# use, and the interfaces of its own.
MAC_INTERFACES = {
    "lo0": [(socket.AF_INET, "127.0.0.1")],
    "en0": [(LINK, "aa:00:00:00:00:00"), (socket.AF_INET, "192.168.1.228")],
    "en6": [(LINK, "aa:00:00:00:00:06"), (socket.AF_INET, "192.168.100.20")],
    "anpi0": [(LINK, "aa:00:00:00:01:00")],
    "anpi1": [(LINK, "aa:00:00:00:01:01")],
    "ap1": [(LINK, "aa:00:00:00:02:01")],
    "awdl0": [(LINK, "aa:00:00:00:03:00")],
    "llw0": [(LINK, "aa:00:00:00:04:00")],
    "en1": [(LINK, "aa:00:00:00:00:01")],
    "en2": [(LINK, "aa:00:00:00:00:02")],
    "en3": [(LINK, "aa:00:00:00:00:03")],
    "en4": [(LINK, "aa:00:00:00:00:04")],
    "bridge0": [(LINK, "aa:00:00:00:05:00")],
}

SERVICES = """An asterisk (*) denotes that a network service is disabled.
(1) Acme USB LAN
(Hardware Port: Acme USB LAN, Device: en6)

(2) Wi-Fi
(Hardware Port: Wi-Fi, Device: en0)

(3) Thunderbolt Bridge
(Hardware Port: Thunderbolt Bridge, Device: bridge0)

(4) Office VPN
(Hardware Port: L2TP, Device: )

"""

INTERNAL = {"anpi0", "anpi1", "ap1", "awdl0", "llw0"}
NO_SERVICE = {"en1", "en2", "en3", "en4"}


def _fake_mac(monkeypatch, interfaces, *, macos=True, services=SERVICES, calls=None):
    def ifaddr_adapter(name, ip):
        return types.SimpleNamespace(
            nice_name=name, ips=[types.SimpleNamespace(ip=ip, network_prefix=24)],
        )

    ifaddr = types.ModuleType("ifaddr")
    ifaddr.get_adapters = lambda: [
        ifaddr_adapter(name, address)
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
        name: types.SimpleNamespace(isup=True) for name in interfaces
    }
    monkeypatch.setitem(sys.modules, "psutil", psutil)
    monkeypatch.setattr(network_scanner, "_IS_MACOS", macos)

    def run(args, **kwargs):
        if calls is not None:
            calls.append(args)
        assert args == ["/usr/sbin/networksetup", "-listnetworkserviceorder"]
        assert "creationflags" in kwargs
        if isinstance(services, Exception):
            raise services
        if services is None:
            return subprocess.CompletedProcess(args, 1, "", "error")
        return subprocess.CompletedProcess(args, 0, services, "")

    monkeypatch.setattr(network_scanner.subprocess, "run", run)


def _names() -> set[str]:
    return {a["name"] for a in network_scanner.get_adapter_inventory()}


def test_a_mac_lists_only_the_adapters_a_person_would_choose(monkeypatch):
    _fake_mac(monkeypatch, MAC_INTERFACES)
    assert _names() == {"en0", "en6", "bridge0"}


def test_an_adapter_waiting_for_an_address_with_a_service_stays(monkeypatch):
    """A USB adapter just plugged in, before DHCP answers: macOS made it a
    service, and it is the port someone is about to use."""
    interfaces = dict(MAC_INTERFACES, en7=[(LINK, "aa:00:00:00:00:07")])
    services = SERVICES + "(5) Acme USB LAN 2\n(Hardware Port: Acme USB LAN 2, Device: en7)\n"
    _fake_mac(monkeypatch, interfaces, services=services)
    by_name = {a["name"]: a for a in network_scanner.get_adapter_inventory()}
    assert "en7" in by_name
    assert by_name["en7"]["has_address"] is False


def test_an_adapter_with_an_address_is_never_hidden(monkeypatch):
    """Someone gave a Thunderbolt port an address by hand: that is a network."""
    interfaces = dict(MAC_INTERFACES, en3=[(LINK, "aa:00:00:00:00:03"), (socket.AF_INET, "10.20.30.5")])
    _fake_mac(monkeypatch, interfaces)
    assert "en3" in _names()


@pytest.mark.parametrize("services", [
    None,                                  # networksetup failed
    OSError("no networksetup"),            # not there at all
    subprocess.TimeoutExpired("networksetup", 5),
    "something else entirely\n",           # a format this does not read
])
def test_an_unreadable_service_list_hides_nothing_more(monkeypatch, services):
    _fake_mac(monkeypatch, MAC_INTERFACES, services=services)
    assert _names() == {"en0", "en6", "bridge0"} | NO_SERVICE


def test_the_list_is_read_once_and_only_when_something_needs_it(monkeypatch):
    calls: list = []
    _fake_mac(monkeypatch, MAC_INTERFACES, calls=calls)
    network_scanner.get_adapter_inventory()
    assert len(calls) == 1

    calls.clear()
    in_use = {k: v for k, v in MAC_INTERFACES.items() if k in {"lo0", "en0", "en6"} | INTERNAL}
    _fake_mac(monkeypatch, in_use, calls=calls)
    network_scanner.get_adapter_inventory()
    assert calls == []


def test_other_systems_keep_these_names(monkeypatch):
    """ap0 is a Raspberry Pi's access point, a network someone chose to run."""
    calls: list = []
    interfaces = {"eth0": [(LINK, "aa:00:00:00:00:00"), (socket.AF_INET, "10.20.30.5")],
                  "ap0": [(LINK, "aa:00:00:00:00:01")]}
    _fake_mac(monkeypatch, interfaces, macos=False, calls=calls)
    assert _names() == {"eth0", "ap0"}
    assert calls == []


def test_a_chosen_adapter_the_list_leaves_out_reads_as_not_connected(monkeypatch):
    """Both pickers and the server read this one list, so an adapter it
    leaves out is the same "not connected" choice everywhere."""
    from openavc import listen

    _fake_mac(monkeypatch, MAC_INTERFACES)
    plan = listen.resolve(
        listen.ListenChoice("adapter", {"name": "en1", "mac": "aa:00:00:00:00:01"}),
        listen.list_adapters(),
    )
    assert plan.local_only
    assert plan.problem.code == "adapter_not_connected"
