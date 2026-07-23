"""
Web UI auto-detection.

Most networked AV gear ships a browser-reachable admin page, so the "Open Web
UI" button shouldn't need a per-driver opt-in. A driver that leaves ``web_ui``
unset gets auto-detection: we work out a reachable web URL for the device and
the platform adds the button on its own. A driver can still force the button on
(``web_ui: true`` / a URL string) or off (``web_ui: false``).

Detection has three sources, cheapest first (the caller picks whichever applies):

* HTTP-transport devices resolve their URL straight from config — the control
  endpoint *is* the web server — with no network probe (see
  ``web_ui_url_for_http_config``).
* A device that was just discovered already carries its open ports, so
  ``web_ui_url_from_open_ports`` turns that into a URL with no extra traffic.
* Anything else gets a light TCP probe of the usual web ports
  (``probe_web_ui``).

All three funnel through the same candidate order and URL formatting so a device
detected two different ways lands on the same URL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from server.utils.logger import get_logger

log = get_logger(__name__)

# Ports we treat as "a web interface lives here", in preference order: an HTTPS
# admin page wins over plain HTTP, and the standard ports win over the 8080
# alt-HTTP fallback. Each entry is (port, scheme).
WEB_UI_CANDIDATES: tuple[tuple[int, str], ...] = (
    (443, "https"),
    (80, "http"),
    (8080, "http"),
)

# The default port for each scheme, so a URL on the standard port stays clean
# (``https://host`` rather than ``https://host:443``).
_DEFAULT_PORT = {"http": 80, "https": 443}

# Per-port connect timeout for the probe. Short: an open port answers a TCP
# handshake almost immediately, and a closed/filtered one isn't worth waiting on.
_PROBE_TIMEOUT = 1.5


def _format_url(host: str, port: int, scheme: str) -> str:
    """Build a browser URL, omitting the port when it's the scheme default."""
    if port == _DEFAULT_PORT.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def web_ui_url_for_http_config(config: dict) -> str | None:
    """Derive an HTTP-transport device's web URL from its own connection config.

    The device's control endpoint is already a web server, so we know the URL
    outright — no probe needed. Mirrors the base_url assembly in
    ``BaseDriver.connect`` (host + optional port + ``ssl`` scheme).
    """
    host = config.get("host")
    if not host:
        return None
    use_ssl = bool(config.get("ssl", False))
    scheme = "https" if use_ssl else "http"
    port = config.get("port")
    if port is None:
        port = _DEFAULT_PORT[scheme]
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = _DEFAULT_PORT[scheme]
    return _format_url(str(host), port, scheme)


def web_ui_url_from_open_ports(host: str, open_ports: Iterable[int]) -> str | None:
    """Turn a known set of open ports into a web URL, or None if none qualify.

    Used with discovery results, which already carry the scanned open ports, so
    a just-discovered device gets its button with no extra network traffic.
    """
    if not host:
        return None
    ports = set(open_ports or ())
    for port, scheme in WEB_UI_CANDIDATES:
        if port in ports:
            return _format_url(host, port, scheme)
    return None


async def probe_web_ui(
    host: str,
    candidates: tuple[tuple[int, str], ...] = WEB_UI_CANDIDATES,
    timeout: float = _PROBE_TIMEOUT,
) -> str | None:
    """TCP-probe the candidate web ports on ``host``; return the best URL or None.

    All candidates are probed concurrently, then the highest-priority one that
    accepted a connection wins — so a device with both 443 and 80 open reports
    its HTTPS page. A bare TCP handshake is signal enough here: these ports are
    web ports by convention, and the cost of a rare false positive (a button to
    a page that doesn't load) is low. Never raises — a probe is best-effort.
    """
    if not host:
        return None

    async def _reachable(port: int) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass
        return True

    results = await asyncio.gather(*(_reachable(port) for port, _ in candidates))
    for (port, scheme), ok in zip(candidates, results):
        if ok:
            return _format_url(host, port, scheme)
    return None
