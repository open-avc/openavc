"""
SSRF-guard helpers for server-side outbound requests.

A plugin's ``proxy_to()`` forwards a URL to an upstream server on behalf of an
(untrusted) browser iframe. Without validation that turns the server into an
SSRF pivot: an attacker-influenced URL could reach loopback, RFC1918, or the
cloud metadata endpoint (169.254.169.254). These helpers reject non-HTTP(S)
schemes and resolve the host to block internal/reserved address space. A caller
that legitimately reaches a localhost sidecar opts in explicitly with
``allow_internal=True``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


def ip_is_internal(ip: str) -> bool:
    """True if an IP is loopback / private / link-local / reserved (v4 or v6).

    An unparseable value is treated as unsafe (returns True) so a caller can
    fail closed.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def assert_safe_outbound_url(url: str, *, allow_internal: bool = False) -> None:
    """Raise ``ValueError`` if ``url`` is not a safe http(s) target.

    Rejects non-http(s) schemes and (unless ``allow_internal``) any host that
    resolves to internal/reserved address space. Resolution checks EVERY
    address the host maps to, so a public hostname pointing at an internal IP
    is still refused.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(
            f"unsupported URL scheme '{parts.scheme}' (only http/https allowed)"
        )
    host = parts.hostname
    if not host:
        raise ValueError("URL has no host")
    if allow_internal:
        return

    port = parts.port or (443 if parts.scheme == "https" else 80)
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"cannot resolve host '{host}': {e}")
    blocked = sorted({info[4][0] for info in infos if ip_is_internal(info[4][0])})
    if blocked:
        raise ValueError(
            f"refusing to reach internal/reserved address(es) {blocked} "
            f"for host '{host}'"
        )
