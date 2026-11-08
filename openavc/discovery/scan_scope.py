"""What a discovery scan may contact: the addresses inside the subnets it was
asked to scan.

The passive listeners hear devices anywhere on the attached networks, and
those are listed from what they announced, but nothing is sent to them: no
port scan, no driver probe, no UPnP description fetch. Without that, a scan
aimed at one address would probe every device that announced itself while it
listened, and a device that serves one control connection at a time would
lose its controller's. The same test keeps a community Python discovery
companion from fabricating a device record for an address outside the
scanned ranges (discovery/probe_phase.py).

A /31 or /32 has no network or broadcast address: its addresses are hosts (a
/32 is a single-device scan), which is why a sweep counts every address and a
UDP probe goes to each one unicast.
"""

from __future__ import annotations

import ipaddress


def ip_in_subnets(ip: str, subnets: list[str]) -> bool:
    """True if ``ip`` parses as an address inside one of ``subnets``.

    A malformed ``ip`` or CIDR is treated as "not in range": the parse is
    guarded so one bad value can't raise into the probe path.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in subnets:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def broadcast_addresses_for(subnets: list[str]) -> list[str]:
    """Return the directed broadcast address for each CIDR.

    Skips invalid CIDRs and prefixes with no meaningful broadcast
    address (/31 and /32).
    """
    out: list[str] = []
    for cidr in subnets:
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError:
            continue
        if net.prefixlen >= 31:
            continue
        out.append(str(net.broadcast_address))
    return out


def udp_probe_targets_for(subnets: list[str]) -> list[str]:
    """Where a driver's UDP probe goes for these subnets.

    The directed broadcast address where a subnet has one. A /31 or /32 has
    none, so the probe goes to each of its addresses unicast instead.
    """
    out = broadcast_addresses_for(subnets)
    for cidr in subnets:
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError:
            continue
        if net.prefixlen >= 31:
            out.extend(str(ip) for ip in net.hosts() if str(ip) not in out)
    return out


def host_count(net: ipaddress.IPv4Network) -> int:
    """Addresses a sweep of ``net`` pings: all of them from /31 up, since a
    /31 or /32 has no network or broadcast address to leave out."""
    if net.prefixlen >= 31:
        return net.num_addresses
    return max(0, net.num_addresses - 2)
