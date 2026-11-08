"""What the redirect listeners answer: the HTTP port while HTTPS is on, and
the optional port-80 listener that lets a typed URL drop the port.

``build_redirect_app`` sends every request to the same path on the target
port (302 for GET/HEAD, 307 otherwise, never a permanent redirect or a cached
one), except device push, which it serves (``http_listener.push_routes``).
When a cloud-issued trusted certificate covers the address the client used,
the target is that certified name instead (``certified_host_for``), and a
browser navigation gets a probe page that tries the name before falling back
to the bare address. ``build_http_listener_app`` puts the real app in front of
the redirect for a client on this machine. The uvicorn listeners that serve
these apps are started in ``openavc.main``.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

# A Host header holding one of these is not a host, and is never put in a URL.
_BAD_HOST_CHARS = (" ", "/", "\\", "@", "<", ">", "\"", "'")


def certified_host_for(host: str) -> str | None:
    """The certified cloud-cert hostname for an IPv4 host, or None.

    None when no cloud cert is active (or it has expired), the host isn't a
    plain IPv4 address, or the encoded name wouldn't be covered by the cert's
    SANs. Shared by the redirect listeners and the startup banner.
    """
    import datetime as _dt
    import ipaddress as _ipaddress

    from openavc import tls as _tls

    state = _tls.cloud_cert_holder().get()
    if state is None:
        return None
    if state.expires_at <= _dt.datetime.now(_dt.timezone.utc):
        return None
    try:
        ip = _ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.version != 4:
        return None
    name = f"{str(ip).replace('.', '-')}.{state.hostname_suffix}"
    return name if state.matches(name) else None


# How long the probe page waits for the certified origin before falling back
# to the bare-IP HTTPS URL. LAN round-trips answer in milliseconds; the
# timeout only matters when the name is blackholed (rebind-protecting router)
# rather than failing fast (NXDOMAIN, no internet).
_PROBE_TIMEOUT_MS = 2500

# Whether the certified hostname resolves is only observable from the
# client's own device — its resolver, its router's rebind protection, its
# venue's internet — so the server can never pick the right redirect target
# on the client's behalf. This page probes the certified origin from the
# browser and picks the first target that actually works. Fully inline (no
# external assets: the client may be about to discover it has no working
# DNS). The probe endpoint is GET /api/health: open (no auth) and on the
# rate limiter's open tier. mode "no-cors" makes any completed response —
# opaque, any status — count as reachable; only network-level failure
# (resolution, TCP, TLS) or the timeout rejects. location.replace() keeps
# this page out of browser history so Back never lands on it.
_PROBE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Connecting&hellip;</title>
<style>
:root { color-scheme: light dark; }
body { font: 16px/1.5 system-ui, -apple-system, sans-serif; margin: 0;
       min-height: 100vh; display: grid; place-items: center; }
main { text-align: center; padding: 2rem; opacity: 0;
       animation: reveal 0.3s ease 0.4s forwards; }
@keyframes reveal { to { opacity: 1; } }
a { color: inherit; }
</style>
</head>
<body>
<main>
<p>Connecting&hellip;</p>
<noscript>
<p><a href="__CERT_URL__">Continue with a secure connection</a></p>
<p><a href="__BARE_URL__">Use the direct address</a> (your browser may warn
about the certificate)</p>
</noscript>
</main>
<script>
(function () {
  "use strict";
  var certOrigin = __CERT_ORIGIN_JS__;
  var bareOrigin = __BARE_ORIGIN_JS__;
  var suffix = location.pathname + location.search;
  function go(url) { location.replace(url); }
  if (!window.fetch || !window.AbortController) {
    go(certOrigin + suffix);
    return;
  }
  var ctrl = new AbortController();
  var timer = setTimeout(function () { ctrl.abort(); }, __TIMEOUT_MS__);
  fetch(certOrigin + "/api/health",
        { mode: "no-cors", cache: "no-store", signal: ctrl.signal })
    .then(function () { clearTimeout(timer); go(certOrigin + suffix); })
    .catch(function () { clearTimeout(timer); go(bareOrigin + suffix); });
})();
</script>
</body>
</html>
"""


def _path_and_query(request: Request) -> str:
    suffix = request.url.path
    if request.url.query:
        suffix += f"?{request.url.query}"
    return suffix


def _probe_page_response(
    certified_host: str, bare_host: str, target_port: int, request: Request
) -> HTMLResponse:
    """The smart-redirect probe page for a browser navigation.

    Embeds both candidate targets: the certified origin (probed first, green
    lock) and the bare-IP HTTPS origin (fallback, self-signed interstitial —
    a working page instead of a dead browser error). The script rebuilds
    path + query from its own location; the noscript links carry them
    HTML-escaped (the path is request-controlled input).
    """
    import html as _html
    import json as _json

    suffix = _path_and_query(request)
    cert_origin = f"https://{certified_host}:{target_port}"
    bare_origin = f"https://{bare_host}:{target_port}"
    page = (
        _PROBE_PAGE
        .replace("__CERT_URL__", _html.escape(cert_origin + suffix, quote=True))
        .replace("__BARE_URL__", _html.escape(bare_origin + suffix, quote=True))
        .replace("__CERT_ORIGIN_JS__", _json.dumps(cert_origin))
        .replace("__BARE_ORIGIN_JS__", _json.dumps(bare_origin))
        .replace("__TIMEOUT_MS__", str(_PROBE_TIMEOUT_MS))
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


def _host_from_header(request: Request) -> str:
    """The host the client addressed, from its Host header with the port
    dropped. Pathological values fall back to the request URL's hostname,
    then localhost."""
    host = _host_part(request.headers.get("host", ""))
    if not host or any(c in host for c in _BAD_HOST_CHARS):
        host = request.url.hostname or "localhost"
    return host


def _host_part(host_header: str) -> str:
    if not host_header.startswith("["):
        return host_header.split(":", 1)[0]
    # Bracketed IPv6 ("[::1]:8080") — splitting on ":" would mangle
    # it; keep the brackets, URLs need them around IPv6 hosts.
    end = host_header.find("]")
    return host_header[: end + 1] if end != -1 else ""


def _is_page_navigation(request: Request) -> bool:
    return request.method == "GET" and "text/html" in request.headers.get("accept", "")


def _redirect(request: Request, origin: str) -> RedirectResponse:
    status = 302 if request.method in ("GET", "HEAD") else 307
    return RedirectResponse(
        origin + _path_and_query(request),
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def build_redirect_app(target_port: int, scheme: str = "https"):
    """Tiny Starlette app: catch-all that 302/307 redirects to {scheme}://...:target_port.

    Serves two listeners: the HTTP port when HTTPS is on (scheme "https"),
    and the optional port-80 convenience listener (scheme "https" when HTTPS
    is on, "http" otherwise) so typed URLs can drop the port entirely.

    Uses temporary redirects (302/307) rather than 301/308 because TLS can be
    toggled off at runtime — a permanent redirect cached by the browser would
    keep forcing HTTPS even after HTTPS is disabled, leaving users locked out
    until they manually clear the browser cache. Cache-Control: no-store
    belt-and-suspenders prevents any caching at all (including of the
    certified-hostname target below, which must not outlive the cert).

    The Host header drives the redirect target hostname so external clients
    (phones, other servers on the LAN) get redirected back to themselves, not
    to "localhost" (``_host_from_header``).

    When a cloud-issued trusted certificate is active and the client
    addressed us by plain IPv4, the target hostname becomes the certified
    name instead — the IP dash-encoded under the cert's wildcard
    (192.168.1.20 -> 192-168-1-20.<label>.<zone>) — so the client lands on
    HTTPS with no browser warning. The Host header is the reachability
    proof: the client just reached that IP over HTTP, so the encoded name
    resolves to an address known-good for this client. Everything else
    (real hostnames, IPv6, no/expired cloud cert) keeps the bare-host
    target and the self-signed interstitial. Certified names only make sense
    on the HTTPS target: on plain HTTP there is no certificate to match.

    Browser navigations (GET with text/html in Accept) don't get a blind
    302 to the certified name: whether that name resolves is only knowable
    on the client (its resolver, rebind-protecting router, venue internet),
    and a browser that can't resolve it shows a dead error page with no way
    back. They get the probe page instead, which tries the certified origin
    and falls back to bare-IP HTTPS. Non-browser clients (no text/html) and
    non-GET methods keep the plain 302/307; with no active cloud cert every
    response is a plain redirect.
    """
    from openavc.transport import http_listener

    async def _handler(request: Request) -> Response:
        host = _host_from_header(request)
        certified = certified_host_for(host) if scheme == "https" else None
        if certified is not None and _is_page_navigation(request):
            return _probe_page_response(certified, host, target_port, request)
        target_host = host if certified is None else certified
        return _redirect(request, f"{scheme}://{target_host}:{target_port}")

    # Device push is served here rather than redirected: see
    # http_listener.push_routes.
    return Starlette(routes=[
        *http_listener.push_routes(),
        Route(
            "/{path:path}",
            _handler,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
    ])


def build_http_listener_app(main_app, redirect_app):
    """The HTTP port while HTTPS is on: serve this machine, redirect the rest.

    The redirect exists to move *network* clients onto TLS. A client on this
    same machine has nothing to gain from it and quite a lot to lose, because
    the certificate is self-signed by default:

    - The appliance panel's own screen is a WebView on 127.0.0.1. Redirected
      to HTTPS it hits a certificate it does not trust, and its live-state
      WebSocket has no error hook to answer at all — so the panel draws and
      then sits there offline. Its supervisor is on the same loopback.
    - On any deployment, HTTPS with a broken certificate otherwise leaves no
      way back in except editing system.json by hand. Here the box itself
      always has one.

    Nothing is exposed by this. The socket peer really is loopback (a header
    cannot fake it), so anything reaching this branch is already on the
    machine, where it could read the certificate and the config off disk
    anyway. The weaker ``scope_peer_is_loopback`` is deliberately the right
    question: ``is_local_console_request`` subtracts tunneled traffic, which
    matters when loopback GRANTS something, and this grants nothing — the
    tunnel connects straight to the TLS port when HTTPS is on
    (``cloud/tunnel.py`` ``_loopback_origin``), so it never arrives here.

    Lifespan is answered by the redirect app, never forwarded: the main app's
    lifespan starts the engine, and a second listener running it would start
    everything twice.
    """
    from openavc.utils.request_origin import scope_peer_is_loopback

    async def listener(scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope_peer_is_loopback(scope):
            await main_app(scope, receive, send)
            return
        await redirect_app(scope, receive, send)

    return listener
