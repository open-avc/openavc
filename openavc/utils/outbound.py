"""THE trust and proxy rules for connections OpenAVC makes out of the box.

Two rules, one home:

**OpenAVC's own internet requests** (the driver and plugin catalogs and their
downloads, update checks and downloads, PyPI wheels and native libraries for
plugins, the IR code database, cloud pairing and unpairing, the cloud
connection and its tunnels, the AI proxy, report uploads) verify with
:func:`internet_ssl_context` and take the proxy from the environment. Every one
goes through :func:`internet_client` (HTTP) or passes the context to the cloud's
WebSockets, so "which certificates does OpenAVC trust?" has one answer.

**Connections to equipment** (a driver's connection, Test Connection, the Driver
Builder's test, inter-system links, this computer's own server and simulator)
never go through a proxy when the destination is on the local network:
:func:`is_local_destination` and :func:`env_proxy_for`. Anything else a driver
reaches (a cloud service such as Dante Director) follows the environment's
proxy settings, ``NO_PROXY`` included, as it always did.

Trust: the operating system's certificate store, then certifi
---------------------------------------------------------------
A campus or company network that inspects encrypted traffic re-signs every site
with its own certificate authority and installs that authority in each
computer's certificate store (Group Policy, MDM, ``update-ca-certificates``).
httpx on its own trusts only certifi's bundle, so behind such a network every
download failed with a certificate error, while the cloud WebSocket, which used
the standard library's default context, read the Windows store and connected.
:func:`internet_ssl_context` verifies against the OS store first
(``truststore``: the Windows chain engine, the macOS Keychain, the OpenSSL
paths on Linux), then against certifi's roots, so nothing that verified before
stops verifying: a box whose OS store lacks a public root (a container image
without ``ca-certificates``, an old Pi image) still reaches GitHub. A bundle
named in ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` is trusted as well, because httpx
honoured those variables before this module existed. The OS store is asked at
every handshake, so a certificate installed after startup needs no restart.

``truststore`` is MIT and pure Python. If it cannot be imported (a damaged
install), the context falls back to the standard library's view of the OS
store plus certifi, which is what the cloud WebSocket trusted before.

Proxy: from the environment, never for equipment on the local network
---------------------------------------------------------------------
httpx and websockets both read ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``ALL_PROXY``
/ ``NO_PROXY`` (and, on Windows and macOS, the signed-in user's system proxy
setting) through ``urllib.request.getproxies()``. Measured behind a proxy:
with only ``HTTPS_PROXY`` set, both libraries sent an HTTPS or ``wss://``
device on a private address, and a plain ``ws://127.0.0.1`` connection, to the
proxy (websockets uses the HTTPS proxy for ``ws://`` too). ``NO_PROXY`` cannot
fix that for an AV network: neither library matches an address range there.
So a destination at a loopback, private or link-local address, ``localhost``,
a ``.local`` name or a single-label name is always reached directly. A device
addressed by a DNS name on the site's network goes in ``NO_PROXY``.

A service has no signed-in user, so on an installed system the proxy is
whatever the service's own environment carries. With no proxy in the
environment (nearly every install) nothing here changes a device connection.
"""

from __future__ import annotations

import functools
import ipaddress
import logging
import os
import ssl
import urllib.request
from typing import Any
from urllib.parse import urlsplit

import certifi
import httpx

log = logging.getLogger(__name__)


# --- trust -----------------------------------------------------------------


@functools.cache
def internet_ssl_context() -> ssl.SSLContext:
    """The verifying TLS context for every connection OpenAVC makes to the internet."""
    try:
        import truststore

        # The OS half needs nothing loaded here: Windows and macOS ask the OS
        # at each handshake, and the Linux leg sets the OpenSSL paths itself.
        ctx: ssl.SSLContext = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - an unusable truststore must not stop downloads
        log.warning(
            "OS certificate store unavailable; using the default store and certifi",
            exc_info=True,
        )
        ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=certifi.where())
    for var, kind in (("SSL_CERT_FILE", "cafile"), ("SSL_CERT_DIR", "capath")):
        path = os.environ.get(var)
        if not path:
            continue
        try:
            ctx.load_verify_locations(**{kind: path})
        except (OSError, ssl.SSLError) as e:
            log.warning("%s=%s could not be loaded as certificates: %s", var, path, e)
    return ctx


def internet_client(**kwargs: Any) -> httpx.AsyncClient:
    """An httpx client for one of OpenAVC's own internet requests.

    Verifies with :func:`internet_ssl_context` and takes the proxy from the
    environment. Every other argument goes to ``httpx.AsyncClient`` unchanged.
    A ``transport`` is refused: httpx ignores the client's ``verify`` and the
    environment's proxy once it is handed one, so both rules would be lost
    without a word. A test that needs a mock transport builds its own client.
    """
    if kwargs.get("transport") is not None or kwargs.get("mounts") is not None:
        raise TypeError("internet_client takes no transport or mounts")
    kwargs.pop("transport", None)
    kwargs.pop("mounts", None)
    kwargs.setdefault("verify", internet_ssl_context())
    kwargs.setdefault("trust_env", True)
    return httpx.AsyncClient(**kwargs)



def internet_tls_kwargs(url: str) -> dict[str, Any]:
    """``websockets.connect`` keyword arguments for one of OpenAVC's own
    internet WebSockets: the internet context for ``wss://``, nothing for a
    plain ``ws://`` (a loopback development cloud), where passing ``ssl`` is an
    error."""
    if url.lower().startswith("wss://"):
        return {"ssl": internet_ssl_context()}
    return {}

# --- proxy -----------------------------------------------------------------


def is_local_destination(host: str | None) -> bool:
    """True for a host OpenAVC reaches directly whatever the proxy settings say.

    A loopback, private or link-local address, ``localhost``, a ``.local`` (mDNS)
    name, or a single-label name (no dot, the way Windows' own "bypass the
    proxy for local addresses" reads a name). Any other name or address is
    not known to be local, and follows the environment.
    """
    if not host:
        return False
    name = host.strip().strip("[]").rstrip(".").lower()
    if not name:
        return False
    try:
        addr = ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        return name == "localhost" or name.endswith(".local") or "." not in name
    return addr.is_loopback or addr.is_private or addr.is_link_local


def environment_has_proxy() -> bool:
    """True when the environment (or the user's system setting) names a proxy."""
    proxies = urllib.request.getproxies()
    return any(proxies.get(scheme) for scheme in ("http", "https", "all", "ws", "wss", "socks"))


def env_proxy_for(url: str) -> str | None:
    """The proxy the environment gives ``url``, or None to connect directly.

    None for a local destination (:func:`is_local_destination`), for a host
    ``NO_PROXY`` (or the system bypass list) excludes, and when no proxy is
    set. Read the way httpx and websockets read it, through
    ``urllib.request.getproxies()``.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.hostname
    if is_local_destination(host):
        return None
    proxies = urllib.request.getproxies()
    if not proxies:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    if urllib.request.proxy_bypass(f"{host}:{port}" if port else (host or "")):
        return None
    scheme = (parts.scheme or "http").lower()
    secure = scheme in ("https", "wss")
    order = ("https", "all") if secure else ("http", "all")
    for key in order:
        proxy = proxies.get(key)
        if proxy:
            return proxy if "://" in proxy else f"http://{proxy}"
    return None


def device_transport(url: str, **settings: Any) -> httpx.AsyncHTTPTransport:
    """The httpx transport for one connection to equipment at ``url``.

    Directly to a local destination, through the environment's proxy for
    anything else (:func:`env_proxy_for`). ``settings`` are the transport's
    own (``verify``, ``cert``, ``local_address``, ...). A client given a
    transport no longer reads proxies from the environment itself, and the
    transport keeps reading ``SSL_CERT_FILE`` for ``verify=True``, so a
    device's certificate is checked exactly as before.
    """
    return httpx.AsyncHTTPTransport(proxy=env_proxy_for(url), **settings)


# --- what to say when it fails ---------------------------------------------


def _causes(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen and len(seen) < 8:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def is_certificate_failure(exc: BaseException) -> bool:
    """True when ``exc`` is (or wraps) a refused server certificate."""
    for e in _causes(exc):
        if isinstance(e, ssl.SSLCertVerificationError):
            return True
        text = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in text or "not trusted by the trust provider" in text:
            return True
    return False


_CERTIFICATE_SENTENCE_START = "The connection to "
_CERTIFICATE_SENTENCE_MARK = "certificate this computer does not trust"
_PROXY_SENTENCE_START = "The proxy server refused the connection to "


def is_internet_failure_sentence(text: str | None) -> bool:
    """True when ``text`` is a sentence :func:`internet_failure` wrote."""
    if not text:
        return False
    return (
        text.startswith(_CERTIFICATE_SENTENCE_START) and _CERTIFICATE_SENTENCE_MARK in text
    ) or text.startswith(_PROXY_SENTENCE_START)


def internet_failure(exc: BaseException, host: str) -> str | None:
    """The sentence for a failed internet request whose cause is the network's
    security rather than the network, or None for any other failure.

    A refused certificate on a request that verifies against the OS store
    means something between this computer and ``host`` presented a
    certificate this computer does not trust: a network that inspects
    encrypted traffic, with its certificate authority not installed here.
    """
    if is_certificate_failure(exc):
        return (
            f"The connection to {host} was answered with a certificate this "
            "computer does not trust. If this network inspects encrypted "
            "traffic, install its certificate authority in this computer's "
            "certificate store, then try again."
        )
    for e in _causes(exc):
        if isinstance(e, httpx.ProxyError):
            return (
                f"The proxy server refused the connection to {host} ({e}). "
                "Check the proxy address in HTTPS_PROXY, and that the proxy "
                f"allows this computer to reach {host}."
            )
    return None
