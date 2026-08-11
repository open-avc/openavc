"""Where a request actually came from — the one authority for loopback trust.

Two surfaces hand a caller something on the strength of "this arrived from
127.0.0.1": ``require_local_or_programmer_auth`` (host network config with no
credential at all, so an appliance can be put onto a network from its own
screen before it has one) and the rate limiter (loopback is exempt, since the
primary deployment is a single user on the box). Both used to read the socket
peer and stop there.

The cloud remote-UI tunnel proxies every remote request to ``localhost``, so a
remote caller arrived looking exactly like the console and collected both
grants: host network reconfiguration with no password, and an unthrottled
channel on which to guess one (the 401 brute-force counter is keyed per IP and
loopback never reaches it).

So "the socket peer is loopback" is not the question. The tunnel stamps
``X-OpenAVC-Tunneled`` on everything it proxies (``openavc/cloud/tunnel.py``)
and the checks here read it.

Two properties make the marker safe to lean on:

- It is only believed when the socket peer is *also* loopback. A LAN client
  that sends it gains nothing — and, importantly, cannot push its traffic into
  the tunnel's shared rate-limit bucket, which is what believing it from any
  source would let it do.
- It cannot be suppressed from upstream. The tunnel sets it after copying the
  cloud's headers, so a value supplied by whoever called the cloud is
  overwritten rather than honored.

It only ever removes trust, never grants it, which is what keeps that argument
short: the failure mode of a spoofed marker is that the sender is treated as
remote, which is what every unknown caller is treated as anyway.

The tunnel stamps a **second** marker, and that one grants, so it does not get
to borrow the short argument. ``X-OpenAVC-Support-Session`` says "the customer
granted OpenAVC a support session and this request arrived on it", and the
Programmer accepts it in place of the admin password — which is the only way a
granted session can reach the Programmer at all, since nobody at OpenAVC has
the customer's instance credential and should not be sent it. Its value is a
per-tunnel secret minted here on the box, never transmitted anywhere but back
across loopback, and only ever valid while that tunnel is open; the registry
that mints and checks it is ``openavc/api/support_session.py``. So the marker
is not a header anyone can assert: guessing it is guessing 256 bits, and the
moment the customer revokes the grant the cloud closes the tunnel and the
secret is discarded. A bare name would have handed programmer access to any
unprivileged local process that could reach ``localhost:8080``, which is a
worse door than the one this opens.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from openavc import config

# Socket peers that mean "this process, or something on this host".
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Stamped by the agent's tunnel handler on every proxied request and WebSocket
# handshake. Lower-case because that is how Starlette normalizes header lookup.
TUNNEL_HEADER = "x-openavc-tunneled"

# Stamped only on a tunnel the cloud opened under a live support-access grant.
# Carries a per-tunnel secret; see the module docstring and support_session.py.
SUPPORT_SESSION_HEADER = "x-openavc-support-session"


def socket_peer_is_loopback(request: Request) -> bool:
    """Whether the TCP peer on the other end of this request is loopback.

    The un-spoofable half of the question: this is the real socket, not a
    header. It is not on its own evidence that a *person* is at the console —
    see ``is_local_console_request``.
    """
    client = request.client
    return client is not None and client.host in LOOPBACK_HOSTS


def support_session_secret(conn: Any) -> str:
    """The support-session secret this request carries, or "".

    Takes a ``Request`` or a ``WebSocket`` — both expose ``.client`` and
    ``.headers``, and the WebSocket handshake needs the same answer as the
    REST door, or a granted session would load the Programmer's pages and then
    fail to open its socket. ``None`` is accepted and answers "", because some
    auth callers have no request in hand.

    Returns "" unless the socket peer is loopback, so this stays the only place
    that has to reason about the peer.
    """
    if conn is None:
        return ""
    client = getattr(conn, "client", None)
    if client is None or client.host not in LOOPBACK_HOSTS:
        return ""
    value = conn.headers.get(SUPPORT_SESSION_HEADER, "")
    return value.strip() if isinstance(value, str) else ""


def is_tunneled_request(request: Request) -> bool:
    """Whether this request was proxied in over a cloud remote-UI tunnel."""
    return socket_peer_is_loopback(request) and TUNNEL_HEADER in request.headers


def is_local_console_request(request: Request) -> bool:
    """Whether this request came from the device's own screen.

    This is the credential-free trust anchor for host network configuration,
    so it rests only on things a remote caller cannot forge: the socket peer
    is loopback, and nothing in front of us says otherwise.

    A forwarded header can never satisfy it. When ``TRUST_FORWARDED_FOR`` is
    on, the operator has told us a reverse proxy sits in front, and everything
    it forwards arrives from loopback — including requests from the far side of
    the world. Rather than trust ``X-Forwarded-For`` to sort them out (a header
    anyone reaching the server directly can set), a forwarded request simply
    isn't console access. The cost is that a console user behind their own
    proxy signs in like anyone else; the alternative is a password-free route
    to the host's network settings gated on a spoofable string.
    """
    if not socket_peer_is_loopback(request):
        return False
    if TUNNEL_HEADER in request.headers:
        return False
    if config.TRUST_FORWARDED_FOR and request.headers.get("x-forwarded-for"):
        return False
    return True
