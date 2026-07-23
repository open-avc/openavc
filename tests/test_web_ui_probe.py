"""Web UI auto-detection helpers (core/web_ui_probe.py).

Feature-level: URL formatting from known-open ports, from an HTTP device's
config, and a live TCP probe against throwaway local sockets. Uses invented
hosts and ephemeral ports — no real device, no fixtures.
"""

from __future__ import annotations

import asyncio

from server.core.web_ui_probe import (
    probe_web_ui,
    web_ui_url_for_http_config,
    web_ui_url_from_open_ports,
)


# --- web_ui_url_from_open_ports --------------------------------------------


def test_from_open_ports_prefers_https_over_http():
    assert web_ui_url_from_open_ports("host", [80, 443]) == "https://host"


def test_from_open_ports_plain_http():
    assert web_ui_url_from_open_ports("host", [80]) == "http://host"


def test_from_open_ports_alt_http_keeps_port():
    assert web_ui_url_from_open_ports("host", [8080]) == "http://host:8080"


def test_from_open_ports_none_when_no_web_port():
    assert web_ui_url_from_open_ports("host", [22, 23, 9000]) is None


def test_from_open_ports_none_without_host():
    assert web_ui_url_from_open_ports("", [443]) is None


# --- web_ui_url_for_http_config --------------------------------------------


def test_http_config_plain_http():
    assert web_ui_url_for_http_config({"host": "host"}) == "http://host"


def test_http_config_ssl_is_https():
    assert web_ui_url_for_http_config({"host": "host", "ssl": True}) == "https://host"


def test_http_config_nonstandard_port_kept():
    assert web_ui_url_for_http_config({"host": "host", "port": 8080}) == "http://host:8080"


def test_http_config_default_port_omitted():
    assert (
        web_ui_url_for_http_config({"host": "host", "ssl": True, "port": 443})
        == "https://host"
    )


def test_http_config_string_port_coerced():
    assert (
        web_ui_url_for_http_config({"host": "host", "ssl": True, "port": "8443"})
        == "https://host:8443"
    )


def test_http_config_no_host_returns_none():
    assert web_ui_url_for_http_config({"port": 80}) is None


# --- probe_web_ui (live TCP against throwaway local sockets) ----------------


async def _listener() -> asyncio.base_events.Server:
    return await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)


async def test_probe_finds_a_listening_port():
    server = await _listener()
    port = server.sockets[0].getsockname()[1]
    try:
        url = await probe_web_ui("127.0.0.1", candidates=((port, "http"),))
        assert url == f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


async def test_probe_prefers_higher_priority_open_port():
    s1 = await _listener()
    s2 = await _listener()
    p1 = s1.sockets[0].getsockname()[1]
    p2 = s2.sockets[0].getsockname()[1]
    try:
        # p1 is listed first (https); with both open it must win.
        url = await probe_web_ui("127.0.0.1", candidates=((p1, "https"), (p2, "http")))
        assert url == f"https://127.0.0.1:{p1}"
    finally:
        for s in (s1, s2):
            s.close()
            await s.wait_closed()


async def test_probe_returns_none_when_nothing_open():
    # Reserve a port then release it, so a connect is refused fast.
    tmp = await _listener()
    port = tmp.sockets[0].getsockname()[1]
    tmp.close()
    await tmp.wait_closed()
    url = await probe_web_ui("127.0.0.1", candidates=((port, "http"),), timeout=0.3)
    assert url is None


async def test_probe_none_without_host():
    assert await probe_web_ui("") is None
