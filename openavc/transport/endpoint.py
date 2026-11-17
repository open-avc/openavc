"""The parts of a device's endpoint a transport fills in for itself.

A device's resolved config names a host and usually a port. Three things about
the connection its driver opens are not in that config: the port a transport
falls back to when the config names none, the local address the socket is
bound to when Settings names a control interface, and, for an http device,
the address its requests go to. ``BaseDriver._create_transport``
(drivers/transport_ladder.py) asks here for all three, and so does anything
that has to report or test where a device really connects
(``core/device_config.connection_of``, ``core/device_reachability``), so the
answer cannot drift from the dial.

THE rule for an http device's address is :func:`http_endpoint`: the scheme
from ``ssl``, the port as set or the scheme's default, the host as a URL
writes it (an IPv6 address in brackets) and whether the certificate is
checked. The HTTP transport's builder, Test Connection, Open Web UI and the
Driver Builder's raw probe all ask it. ``base_url`` in a config is not read
here: the transport never reads it.

tcp, udp and osc have no fallback port: the driver must declare one
(``BaseDriver._required_port``), and a device without one is a config error.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
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


def implied_port(transport: str, config: Mapping[str, Any]) -> int | None:
    """The port ``transport`` dials when ``config`` names none, or None.

    None means the transport has no fallback: tcp, udp and osc need a port
    from the driver's ``default_config`` or the device, and serial's "port" is
    a device path.
    """
    if transport == "http":
        return 443 if _uses_tls(config) else 80
    return _IMPLIED_PORTS.get(transport)


def _uses_tls(config: Mapping[str, Any]) -> bool:
    """Whether an http device's ``ssl`` is on: by its truth, as every
    transport reads it."""
    return bool(config.get("ssl", False))


def checks_certificate(config: Mapping[str, Any]) -> bool:
    """Whether an http device's certificate is checked: on when
    ``verify_ssl`` is not set, null or blank (as a blank port is not set),
    otherwise by its truth, as the TCP and MQTT transports read it. Part of
    :func:`http_endpoint`'s answer; on its own for a caller with a URL of its
    own and no host."""
    value = config.get("verify_ssl")
    return True if value is None or value == "" else bool(value)


def url_host(host: Any) -> str:
    """``host`` as a URL writes it: an IPv6 address in brackets, its zone's
    ``%`` written ``%25``; anything else trimmed and as written. A host
    already in brackets is not bracketed again."""
    name = str(host or "").strip()
    bare = name[1:-1] if name.startswith("[") and name.endswith("]") else name
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        return name
    if address.version == 6:
        return "[" + bare.replace("%", "%25") + "]"
    return bare


class UnusablePort(ValueError):
    """A port setting no connection can be made to. ``port`` is the value as
    written, for the caller to name in its own sentence."""

    def __init__(self, port: Any) -> None:
        super().__init__(f"the port {port!r} is not a port number")
        self.port = port


def _port_number(port: Any) -> int:
    """A port setting as a number from 1 to 65535, or :class:`UnusablePort`.
    Read with ``int()``, as ``connection_of`` and the other transports'
    builders read one: a number written as text counts, a word does not."""
    try:
        number = int(port)
    except (TypeError, ValueError):
        raise UnusablePort(port) from None
    if not 0 < number <= 65535:
        raise UnusablePort(port)
    return number


@dataclass(frozen=True)
class HttpEndpoint:
    """Where an http device's requests go."""

    scheme: str  # "https" when ssl is on, else "http"
    host: str  # as a URL writes it (url_host)
    port: int
    verify: bool  # whether the device's certificate is checked

    @property
    def base_url(self) -> str:
        """What the HTTP transport is built with, the port always written."""
        return f"{self.scheme}://{self.host}:{self.port}"


def http_endpoint(host: Any, config: Mapping[str, Any]) -> HttpEndpoint:
    """Where an http device at ``host`` with ``config`` sends its requests.

    The port is ``config["port"]``, or the scheme's default when it is not
    set, null or blank; one that is set to anything but a port number raises
    :class:`UnusablePort`, never falling back to the default, since the
    device's own connection is refused for it. ``host`` is passed apart from
    ``config`` because a caller may already know better (a bridge binding
    that connects nowhere leaves no host).
    """
    port = config.get("port")
    if port is None or port == "":
        number = implied_port("http", config)
        assert number is not None  # http always has one
    else:
        number = _port_number(port)
    return HttpEndpoint(
        scheme="https" if _uses_tls(config) else "http",
        host=url_host(host),
        port=number,
        verify=checks_certificate(config),
    )


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
    never bound. An adapter holding only a 169.254 address (a network with no
    DHCP server) still reaches a 169.254 device from it, and sends a
    broadcast or a multicast from it.
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
            if unusable.broadcast_source and _is_broadcast_or_multicast(host):
                # Leaves from where send_udp and UDPTransport send one: the
                # adapter's 169.254 address while it holds no other.
                return unusable.broadcast_source
            from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError

            where = str(host or "").strip() or "the device"
            raise ConnectionFaultError(
                f"Can't reach {where}. "
                + reach_devices.no_address_sentence(unusable.adapter_name),
                code=UNREACHABLE,
            )
    link_local = reach_devices.link_local_address() if pinned and is_link_local(host) else ""
    return local_address_for(host, pinned, link_local)


def _is_broadcast_or_multicast(host: Any) -> bool:
    """True for 255.255.255.255 or an IPv4 multicast group (224.0.0.0/4)."""
    try:
        ip = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return False
    return ip.version == 4 and (ip.is_multicast or ip == ipaddress.IPv4Address("255.255.255.255"))


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
