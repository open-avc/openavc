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
to borrow the short argument. ``X-OpenAVC-Cloud-Session`` says "the cloud
authorized this tunnel to act as a signed-in Programmer client", and the
Programmer accepts it in place of the admin password. Two callers need that,
and neither of them can be sent the instance's credential: OpenAVC support
working under a customer's grant (nobody here holds their password), and the
system's own owner, who turned on password-free remote programming for their
own rooms rather than typing a per-room password once per room.

Its value is a per-tunnel secret minted here on the box, never transmitted
anywhere but back across loopback, and only ever valid while that tunnel is
open; the registry that mints and checks it is
``openavc/api/cloud_session.py``. So the marker is not a header anyone can
assert: guessing it is guessing 256 bits, and the moment the authorization
ends the cloud closes the tunnel and the secret is discarded. A bare name
would have handed programmer access to any unprivileged local process that
could reach ``localhost:8080``, which is a worse door than the one this opens.

A reverse proxy also makes every caller arrive from the proxy, and two things
read the ``X-Forwarded-For`` header it adds. uvicorn reads it first: every
listener the server starts has uvicorn's ``proxy_headers`` on, and for a socket
peer in ``UVICORN_FORWARDED_ALLOW_IPS`` uvicorn replaces ``request.client``
with the rightmost address in the header it does not trust, before any code
here runs. That is why a proxy on the same machine that sends the header is
resolved to its real client even with ``trust_forwarded_for`` off. The value is
pinned on every listener in ``openavc/main.py`` rather than left to uvicorn's
own ``FORWARDED_ALLOW_IPS`` variable: set to ``*``, uvicorn takes the LEFTMOST
entry from any peer, so a LAN client that sent ``127.0.0.1`` became the console.

``forwarded_client`` is the second reader, and only when the operator has
turned ``network.trust_forwarded_for`` on. It believes the header only from a
peer that is a proxy (loopback, or an address in ``network.trusted_proxies``)
and reads it the way uvicorn does, right to left, skipping proxies: the answer
is the address the last trusted proxy saw, never whatever the client wrote at
the front (a proxy that appends, nginx's ``$proxy_add_x_forwarded_for``, keeps
the client's own value there). The two readers compose because this one's
proxies include every peer uvicorn trusts: a peer uvicorn has already resolved
is either a proxy, whose header is walked again by the same rule, or a client,
whose header is ignored. ``tests/test_forwarded_client.py`` pins the inclusion.

A forwarded answer is a label and a rate-limit key, never a grant. A request a
proxy delivered is not the console (``is_local_console_request``), and it does
not get loopback's rate-limit exemption even when the proxy says its client was
``127.0.0.1``: that is the proxy's own machine, which need not be this one.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Any

from starlette.requests import HTTPConnection

from openavc import config
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Socket peers that mean "this process, or something on this host".
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# The socket peers uvicorn takes X-Forwarded-For from, passed explicitly to
# every listener in openavc/main.py so the FORWARDED_ALLOW_IPS environment
# variable cannot widen it (see the module docstring). Both loopback
# addresses, because LOOPBACK_HOSTS treats both as the console: a proxy that
# reaches the server over ::1 is resolved the same as one over 127.0.0.1,
# rather than being taken for the machine's own screen.
UVICORN_FORWARDED_ALLOW_IPS: tuple[str, ...] = ("127.0.0.1", "::1")

# Stamped by the agent's tunnel handler on every proxied request and WebSocket
# handshake. Lower-case because that is how Starlette normalizes header lookup.
TUNNEL_HEADER = "x-openavc-tunneled"

# Stamped only on a tunnel the cloud authorized to act as a signed-in
# Programmer client -- a support grant, or an owner who turned on password-free
# remote programming. Carries a per-tunnel secret; see the module docstring and
# cloud_session.py.
CLOUD_SESSION_HEADER = "x-openavc-cloud-session"


def socket_peer_is_loopback(request: HTTPConnection) -> bool:
    """Whether the TCP peer on the other end of this request is loopback.

    The un-spoofable half of the question: this is the real socket, not a
    header. It is not on its own evidence that a *person* is at the console —
    see ``is_local_console_request``.

    Takes a ``Request`` or a ``WebSocket`` (both are ``HTTPConnection``s with
    ``.client`` and ``.headers``): the panel gate asks the console question of
    a socket handshake, and it has to get the same answer the REST door gets.
    """
    client = request.client
    return client is not None and client.host in LOOPBACK_HOSTS


def scope_peer_is_loopback(scope: dict) -> bool:
    """The same question for a raw ASGI scope, before any app has parsed it.

    The HTTP listener that stands in front of the app while HTTPS is on has
    to answer it there — it decides whether to serve the request or redirect
    it, which happens before there is a ``Request`` to ask about. Reads the
    socket peer only; it grants nothing on its own (see ``_build_http_listener_app``
    in ``openavc/main.py`` for why the weaker question is the right one there).
    """
    client = scope.get("client")
    return bool(client) and client[0] in LOOPBACK_HOSTS


def cloud_session_secret(conn: Any) -> str:
    """The cloud-authorized-session secret this request carries, or "".

    Takes a ``Request`` or a ``WebSocket`` — both expose ``.client`` and
    ``.headers``, and the WebSocket handshake needs the same answer as the
    REST door, or an authorized session would load the Programmer's pages and
    then fail to open its socket. ``None`` is accepted and answers "", because
    some auth callers have no request in hand.

    Returns "" unless the socket peer is loopback, so this stays the only place
    that has to reason about the peer.
    """
    if conn is None:
        return ""
    client = getattr(conn, "client", None)
    if client is None or client.host not in LOOPBACK_HOSTS:
        return ""
    value = conn.headers.get(CLOUD_SESSION_HEADER, "")
    return value.strip() if isinstance(value, str) else ""


def is_tunneled_request(request: HTTPConnection) -> bool:
    """Whether this request was proxied in over a cloud remote-UI tunnel."""
    return socket_peer_is_loopback(request) and TUNNEL_HEADER in request.headers


def parse_trusted_proxies(value: Any) -> tuple[tuple, list[str]]:
    """``network.trusted_proxies`` as networks, plus the entries that are not one.

    Takes what ``system.json`` or the environment holds: a list of addresses
    or CIDR ranges, or one string of them separated by commas (the form the
    ``OPENAVC_TRUSTED_PROXIES`` variable arrives in). A bare address is its
    own one-address range. Returns ``(networks, bad)``; the config door refuses
    a value with anything in ``bad``, and the runtime drops it with a warning.
    """
    if value is None or value == "":
        entries: list[Any] = []
    elif isinstance(value, str):
        entries = value.split(",")
    elif isinstance(value, (list, tuple)):
        entries = list(value)
    else:
        entries = [value]
    networks = []
    bad: list[str] = []
    for entry in entries:
        # Text only: ipaddress reads a bare integer as an address, and 10 in a
        # JSON list is a typo, not 0.0.0.10.
        if not isinstance(entry, str):
            bad.append(str(entry))
            continue
        if not entry.strip():
            continue
        try:
            networks.append(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            bad.append(entry)
    return tuple(networks), bad


@lru_cache(maxsize=8)
def _proxy_networks_for(key: tuple) -> tuple:
    networks, bad = parse_trusted_proxies(list(key))
    if bad:
        log.warning(
            "network.trusted_proxies: ignoring %s (not an address or range)",
            ", ".join(bad),
        )
    return networks


def _proxy_networks() -> tuple:
    """The parsed ``network.trusted_proxies``, cached on its current value."""
    raw = config.TRUSTED_PROXIES
    if isinstance(raw, str):
        entries: list = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        entries = list(raw)
    else:
        entries = [] if raw is None else [raw]
    # A hand-edited file can hold anything, and the cache key must hash; a
    # non-text entry becomes its repr, which no address parses, so it is
    # dropped with the warning rather than raising on every request.
    return _proxy_networks_for(
        tuple(e if isinstance(e, str) else repr(e) for e in entries)
    )


def _is_proxy(host: str, networks: tuple) -> bool:
    """Whether an address is one the operator has said is a proxy of theirs."""
    if host in LOOPBACK_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in networks)


def forwarded_client(request: HTTPConnection) -> str | None:
    """The client a trusted proxy says it forwarded this request for, else None.

    None unless ``network.trust_forwarded_for`` is on, the peer is a proxy
    (loopback, or inside ``network.trusted_proxies``) and it sent
    ``X-Forwarded-For``. Then the header is read right to left and the first
    address that is not a proxy is the answer: the one the last trusted proxy
    saw, whatever the client wrote in front of it. When every entry is a
    proxy, the leftmost is, as uvicorn does.

    The peer here is ``request.client`` as uvicorn hands it over, which for a
    loopback peer has already been through the same walk (module docstring).
    """
    if not config.TRUST_FORWARDED_FOR:
        return None
    header = request.headers.get("x-forwarded-for", "")
    client = request.client
    if not header or client is None:
        return None
    networks = _proxy_networks()
    if not _is_proxy(client.host, networks):
        return None
    hops = [hop.strip() for hop in header.split(",") if hop.strip()]
    if not hops:
        return None
    for hop in reversed(hops):
        if not _is_proxy(hop, networks):
            return hop
    return hops[0]


def client_used_https(request: HTTPConnection) -> bool:
    """Whether the client reached this server over HTTPS, directly or via a proxy.

    For a loopback peer uvicorn has already turned a proxy's
    ``X-Forwarded-Proto`` into the request's scheme; a proxy on another
    machine is not one uvicorn trusts, so its word is read here, from a peer
    in ``network.trusted_proxies`` only and only as the single value uvicorn
    accepts. It decides the Secure flag on the panel approval cookie, so a
    false ``https`` can only withhold the cookie from a plain-HTTP client.
    """
    if request.url.scheme in ("https", "wss"):
        return True
    if not config.TRUST_FORWARDED_FOR:
        return False
    client = request.client
    if client is None or not _is_proxy(client.host, _proxy_networks()):
        return False
    return request.headers.get("x-forwarded-proto", "").strip() in ("https", "wss")


def peer_address(request: HTTPConnection) -> str:
    """The address to show a person for this connection, as data.

    The client a trusted proxy names (``forwarded_client``), else the socket
    peer. It names a waiting panel in the Programmer and bounds how many
    pending requests one address may hold, which is why the header is only
    read from a proxy: believed from anyone, a client could take a new
    address per request and walk past the bound. Kept here so the socket
    peer is still read in one module only.
    """
    forwarded = forwarded_client(request)
    if forwarded is not None:
        return forwarded
    client = request.client
    return client.host if client is not None else ""


def is_local_console_request(request: HTTPConnection) -> bool:
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
