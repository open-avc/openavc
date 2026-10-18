"""The parts of a device's endpoint a transport fills in for itself.

A device's resolved config names a host and usually a port. Two things about
the connection its driver opens are not in that config: the port a transport
falls back to when the config names none, and the local address the socket is
bound to when Settings names a control interface. ``BaseDriver._create_transport``
asks here for both, and so does anything that has to report or test where a
device really connects (``core/device_config.connection_of``,
``core/device_reachability``), so the answer cannot drift from the dial.

tcp, udp and osc have no fallback port: the driver must declare one
(``BaseDriver._required_port``), and a device without one is a config error.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from openavc.transport.snmp_codec import SNMP_PORT

#: The port each transport dials when the device's config names none. http's
#: depends on ``ssl`` and is answered by :func:`implied_port`. mqtt stays on
#: 1883 with TLS on: that is what the transport has always dialed.
_IMPLIED_PORTS: dict[str, int] = {
    "ssh": 22,
    "mqtt": 1883,
    "snmp": SNMP_PORT,
}


def implied_port(transport: str, config: dict[str, Any]) -> int | None:
    """The port ``transport`` dials when ``config`` names none, or None.

    None means the transport has no fallback: tcp, udp and osc need a port
    from the driver's ``default_config`` or the device, and serial's "port" is
    a device path.
    """
    if transport == "http":
        return 443 if config.get("ssl", False) else 80
    return _IMPLIED_PORTS.get(transport)


_LINK_LOCAL = ipaddress.IPv4Network("169.254.0.0/16")


def is_link_local(host: Any) -> bool:
    """True when ``host`` is written as an IPv4 169.254 address. A hostname
    is never resolved here: a connection asks this for every dial."""
    try:
        return ipaddress.ip_address(str(host or "").strip()) in _LINK_LOCAL
    except ValueError:
        return False


def control_bind_address(host: Any) -> str | None:
    """The local IP a connection to ``host`` is bound to, or None.

    Settings' Reach devices through pins device traffic to one adapter
    (``openavc/reach_devices.py``); :func:`local_address_for` applies it.
    Raises :class:`ConnectionFaultError` (``unreachable``) when that adapter
    has no address at all, except for a host on this computer, which is
    never bound.
    """
    from openavc import reach_devices

    pinned = reach_devices.address()
    if not pinned and not _is_loopback(host):
        # An adapter chosen with nothing to send from: refused, never left to
        # the operating system, which would route it through another adapter.
        unusable = reach_devices.unusable_choice()
        if unusable is not None:
            if unusable.link_local_address and is_link_local(host):
                return unusable.link_local_address
            from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError

            where = str(host or "").strip() or "the device"
            raise ConnectionFaultError(
                f"Can't reach {where}. "
                + reach_devices.no_address_sentence(unusable.adapter_name),
                code=UNREACHABLE,
            )
    link_local = reach_devices.link_local_address() if pinned and is_link_local(host) else ""
    return local_address_for(host, pinned, link_local)


def _is_loopback(host: Any) -> bool:
    name = str(host or "").strip().strip("[]").lower()
    try:
        return name == "localhost" or ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def local_address_for(
    host: Any, control_ip: str | None, link_local_ip: str | None = None,
) -> str | None:
    """The rule :func:`control_bind_address` applies, given the pinned address.

    ``control_ip`` empty means nothing is pinned. A device on this machine (a
    simulator, a local service) is reached over loopback, which a socket bound
    to an adapter's address cannot do (Windows refuses the connect with
    WinError 1214), so a loopback host is never bound. A device at a 169.254
    address is reached from ``link_local_ip``, the pinned adapter's own
    169.254 address, when it holds one: many such devices answer only a
    source on their own link. Without one it gets ``control_ip`` like any
    other host, never None, which would let the OS route it through another
    adapter. Separate so the plugin test harness answers by the same rule
    from addresses a test sets.
    """
    if not control_ip:
        return None
    name = str(host or "").strip().strip("[]").lower()
    if _is_loopback(name):
        return None
    if link_local_ip and is_link_local(name):
        return link_local_ip
    return control_ip
