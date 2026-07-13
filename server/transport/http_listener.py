"""
OpenAVC HTTP push-listener registry — inbound HTTP callbacks for driver push
notifications (``push: {type: http_listener}``).

Some devices deliver notifications by dialing OUT to an HTTP server the
controller registers with them (Cisco codec HttpFeedback webhooks, UPnP GENA
NOTIFY callbacks). The platform's existing web listener is that server: each
subscribed device gets a callback path under ``/api/push/`` routed here, and
the API layer hands every request body on that path to the matching
subscription's callback.

- **Path is the demux key**: one subscription per (device_id, label). The
  default path is ``/api/push/<device_id>``; a driver that needs several
  distinct callbacks (e.g. one per subscribed service) passes a ``label``
  and gets ``/api/push/<device_id>/<label>``.
- **Source-IP gate**: a request is delivered only when it comes from the
  subscribed device's host (loopback subscriptions accept any local address,
  same as the multicast listener — the simulator redirect points devices at
  127.0.0.1 while the simulator POSTs from a real interface). The gate is a
  sanity check, not authentication — the trust model is the AV VLAN, same
  as UDP device control (documented in the IT network guide).
- **No socket of its own**: requests arrive on the normal web port(s). When
  HTTPS is enabled with the HTTP->HTTPS redirect, the redirect listener
  passes ``/api/push/`` through to this registry instead of redirecting —
  devices POST plain HTTP and don't follow redirects or trust our
  self-signed certificate.

``callback_url()`` builds the URL a driver registers with its device: the
server address **as seen from that device** (honoring the
``network.control_interface`` pin), on the plain-HTTP web port whenever one
is reachable. All calls run on the event loop thread.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Any, Callable

from server.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class HTTPPushRequest:
    """One inbound push request, as handed to a subscription callback."""

    body: bytes
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    source_ip: str = ""
    label: str = ""


class HTTPListenerSubscription:
    """Handle returned by :func:`subscribe`; ``close()`` detaches it."""

    def __init__(
        self,
        device_id: str,
        label: str,
        source_ips: set[str] | None,
        callback: Callable[[HTTPPushRequest], Any],
        name: str,
    ) -> None:
        self.device_id = device_id
        self.label = label
        # None means "any local source" (loopback-host subscription where the
        # local interface set could not be determined); otherwise the exact
        # source addresses this subscription accepts.
        self._source_ips = source_ips
        self._callback = callback
        self.name = name
        self._closed = False

    @property
    def path(self) -> str:
        """URL path (no scheme/host) the device must deliver to."""
        base = f"/api/push/{self.device_id}"
        return f"{base}/{self.label}" if self.label else base

    def matches_source(self, src_ip: str) -> bool:
        if self._source_ips is None:
            return True
        if src_ip in self._source_ips:
            return True
        # A loopback-host subscription accepts the whole loopback net: the
        # exact source of a looped-back request varies by OS.
        return "127.0.0.1" in self._source_ips and src_ip.startswith("127.")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _registry.unsubscribe(self)


class _Registry:
    """Process-wide subscription registry (single event loop)."""

    def __init__(self) -> None:
        self._subs: dict[tuple[str, str], HTTPListenerSubscription] = {}

    def add(self, sub: HTTPListenerSubscription) -> None:
        key = (sub.device_id, sub.label)
        old = self._subs.get(key)
        if old is not None:
            # A reconnect can re-subscribe before the previous handle's async
            # close ran; last-in wins so a fresh connection never loses its
            # callback to stale teardown.
            old._closed = True
        self._subs[key] = sub

    def unsubscribe(self, sub: HTTPListenerSubscription) -> None:
        key = (sub.device_id, sub.label)
        if self._subs.get(key) is sub:
            del self._subs[key]

    def get(self, device_id: str, label: str) -> HTTPListenerSubscription | None:
        return self._subs.get((device_id, label))

    def close_all(self) -> None:
        """Drop every subscription (tests / shutdown)."""
        for sub in self._subs.values():
            sub._closed = True
        self._subs.clear()


_registry = _Registry()


async def subscribe(
    device_id: str,
    source_ip: str,
    callback: Callable[[HTTPPushRequest], Any],
    name: str,
    label: str = "",
) -> HTTPListenerSubscription:
    """Register an inbound push callback for ``device_id``.

    Returns a handle whose ``path`` is the URL path the device must deliver
    to; ``await handle.close()`` detaches it. Requests are delivered only
    from ``source_ip`` (the device's host; loopback accepts any local
    source). The callback receives an :class:`HTTPPushRequest` and may be
    sync or async. ``label`` distinguishes multiple callbacks for one device.
    """
    from server.transport.multicast_listener import resolve_source_ips

    source_ips = await resolve_source_ips(source_ip, name)
    sub = HTTPListenerSubscription(device_id, label, source_ips, callback, name)
    _registry.add(sub)
    log.debug("[%s] Push listener registered at %s", name, sub.path)
    return sub


async def dispatch(
    device_id: str,
    label: str,
    request: HTTPPushRequest,
) -> int:
    """Deliver one inbound request to its subscription.

    Returns the HTTP status the web layer should answer with: 200 delivered,
    404 no subscription on this path (device unknown / disconnected), 403
    source address does not match the subscribed device (kept distinct from
    404 so a misconfigured device points at itself in the server log).
    """
    sub = _registry.get(device_id, label)
    if sub is None:
        log.debug(
            "Push request for unknown path /api/push/%s%s from %s dropped "
            "(%d bytes)",
            device_id,
            f"/{label}" if label else "",
            request.source_ip,
            len(request.body),
        )
        return 404
    if not sub.matches_source(request.source_ip):
        log.warning(
            "[%s] Push request from unmatched source %s dropped — the "
            "subscription accepts only the device's own host",
            sub.name,
            request.source_ip,
        )
        return 403
    try:
        result = sub._callback(request)
        if hasattr(result, "__await__"):
            await result
    except Exception:
        log.exception("[%s] Error in push callback", sub.name)
    return 200


def callback_url(device_host: str, path: str) -> str:
    """Build the callback URL a driver registers with its device.

    The host part is the server address **as the device sees it**: the
    ``network.control_interface`` pin when configured, otherwise the local
    address the OS would route toward ``device_host`` (a loopback device —
    the simulator redirect — gets a loopback URL). Scheme and port follow
    where a device can actually deliver: the plain-HTTP web port whenever it
    is reachable (directly, or via the redirect listener's push
    pass-through); only an HTTPS-only server (TLS on, redirect listener off)
    yields an https URL — most devices then need their certificate checks
    relaxed, which the driver's help text must call out.
    """
    from server import config

    if config.TLS_ENABLED and not config.TLS_REDIRECT_HTTP:
        scheme, port = "https", config.TLS_PORT
    else:
        scheme, port = "http", config.HTTP_PORT
    return f"{scheme}://{_local_ip_for(device_host)}:{port}{path}"


def _local_ip_for(device_host: str) -> str:
    """The local address a device at ``device_host`` can reach us on."""
    from server.system_config import get_system_config

    pinned = str(
        get_system_config().get("network", "control_interface") or ""
    ).strip()
    if pinned:
        return pinned
    host = (device_host or "").strip()
    if not host or host == "localhost" or host.startswith("127."):
        return "127.0.0.1"
    # UDP connect assigns the outgoing interface without sending a packet —
    # the standard trick for "which of my addresses routes to this host".
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            return s.getsockname()[0]
    except OSError:
        pass
    # Unresolvable device host (e.g. a hostname while offline): fall back to
    # the address that routes toward the wider network.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def close_all() -> None:
    """Drop every subscription (used by tests and engine shutdown)."""
    _registry.close_all()
