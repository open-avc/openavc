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


def control_bind_address(host: Any) -> str | None:
    """The local IP a connection to ``host`` is bound to, or None.

    Settings' control interface pins device traffic to one adapter. A device
    on this machine (a simulator, a local service) is reached over loopback,
    which a socket bound to an adapter's address cannot do (Windows refuses
    the connect with WinError 1214), so a loopback host is never bound.
    """
    from openavc.system_config import get_system_config

    control_ip = get_system_config().get("network", "control_interface")
    if not control_ip:
        return None
    name = str(host or "").strip().strip("[]").lower()
    try:
        loopback = name == "localhost" or ipaddress.ip_address(name).is_loopback
    except ValueError:
        loopback = False
    return None if loopback else control_ip
