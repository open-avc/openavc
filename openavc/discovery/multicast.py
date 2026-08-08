"""Shared IPv4 multicast join/send helpers for discovery.

The classic recipe — one ``IP_ADD_MEMBERSHIP`` join on INADDR_ANY and one
send via the default route — quietly breaks on hosts whose main routing
table has no multicast route (Android's policy routing leaves it empty;
some hardened servers strip it too). Joining and sending explicitly per
interface works regardless of the routing table, and is also more correct
on multi-NIC AV hosts (corporate / AV / control VLANs on one box): every
interface hears its own VLAN's announcements.

Used by the mDNS, SSDP, and AMX DDP scanners and the mDNS advertiser:

- ``control_interface`` configured → join/send via that interface only
  (unchanged multi-NIC pinning behavior).
- Otherwise → join the group once per non-loopback IPv4 interface address,
  tolerating per-interface failures; the INADDR_ANY join is the fallback,
  not the primary. Sends go out once per interface (``IP_MULTICAST_IF``
  pinned per send); receivers dedup responses by source address.
- Every join failed → the caller surfaces a scan environment warning
  instead of silently reporting an empty network.
"""

from __future__ import annotations

import logging
import socket
import struct

from openavc.discovery.network_scanner import get_interface_ips

log = logging.getLogger("discovery.multicast")

# Sentinel interface meaning "the OS default route, unpinned".
ANY_INTERFACE = "0.0.0.0"


def set_shared_port_reuse(sock: socket.socket) -> None:
    """Allow a multicast socket to share a fixed port already held by another process.

    SO_REUSEADDR is enough on Linux and Windows, but BSD-derived stacks
    (macOS) also require SO_REUSEPORT to bind a port another process is
    already using — without it, binding e.g. mDNS 5353 alongside macOS's
    mDNSResponder fails with EADDRINUSE. SO_REUSEPORT does not exist on
    Windows, where SO_REUSEADDR already grants the same sharing.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)


def _join(sock: socket.socket, group: str, interface_ip: str) -> None:
    mreq = struct.pack(
        "4s4s",
        socket.inet_aton(group),
        socket.inet_aton(interface_ip),
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)


def join_group_on_interfaces(
    sock: socket.socket,
    group: str,
    control_ip: str = "",
    interface_ips: list[str] | None = None,
) -> list[str]:
    """Join an IPv4 multicast group, preferring explicit per-interface joins.

    Args:
        control_ip: When set, join via this interface only (multi-NIC
            pinning — unchanged legacy behavior).
        interface_ips: Override the interface enumeration (tests).

    Returns the interface IPs the join succeeded on (``ANY_INTERFACE`` for
    the INADDR_ANY fallback). An empty list means every join failed — the
    caller should surface an environment warning rather than listen on a
    socket that will never hear the group.
    """
    if control_ip:
        try:
            _join(sock, group, control_ip)
            return [control_ip]
        except OSError as exc:
            log.warning(
                "Could not join %s via control interface %s: %s",
                group, control_ip, exc,
            )
            return []

    if interface_ips is None:
        interface_ips = get_interface_ips()

    joined: list[str] = []
    for ip in interface_ips:
        try:
            _join(sock, group, ip)
            joined.append(ip)
        except OSError as exc:
            log.debug("Could not join %s via %s: %s", group, ip, exc)

    if joined:
        return joined

    try:
        _join(sock, group, ANY_INTERFACE)
        return [ANY_INTERFACE]
    except OSError as exc:
        log.warning(
            "Could not join multicast group %s on any interface: %s",
            group, exc,
        )
        return []


def send_per_interface(
    sock: socket.socket,
    payload: bytes,
    dest: tuple[str, int],
    interface_ips: list[str],
) -> int:
    """Send ``payload`` to ``dest`` once per interface IP.

    Pins ``IP_MULTICAST_IF`` before each send so the packet leaves through
    that interface even without a multicast route; ``ANY_INTERFACE``
    entries send unpinned via the OS default. Per-interface failures are
    tolerated. Returns the number of successful sends.

    Blocking — run in an executor from async code.
    """
    sent = 0
    for ip in interface_ips:
        try:
            if ip != ANY_INTERFACE:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(ip),
                )
            sock.sendto(payload, dest)
            sent += 1
        except OSError as exc:
            log.debug("Multicast send via %s to %s failed: %s", ip, dest, exc)
    return sent
