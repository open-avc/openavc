"""TLS-aware loopback helpers for the cloud tunnel (Phase 6).

The HTTP and WS URL builders inside ``TunnelHandler`` consult
``openavc.config`` at call time, so these tests monkeypatch the config module
attributes to exercise both schemes.
"""

from __future__ import annotations

from openavc import config
from openavc.cloud.tunnel import TunnelHandler


# ---------------------------------------------------------------------------
# _loopback_origin (HTTP)
# ---------------------------------------------------------------------------


def test_loopback_origin_tls_off_uses_http(monkeypatch):
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    assert TunnelHandler._loopback_origin(8080) == "http://localhost:8080"
    assert TunnelHandler._loopback_origin(8443) == "http://localhost:8443"
    assert TunnelHandler._loopback_origin(12345) == "http://localhost:12345"


def test_loopback_origin_tls_on_swaps_http_port(monkeypatch):
    """TLS on + target == HTTP_PORT switches scheme + port (main app moved)."""
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    assert TunnelHandler._loopback_origin(8080) == "https://localhost:8443"


def test_loopback_origin_tls_on_plugin_port_stays_http(monkeypatch):
    """Plugin/alt-service ports stay HTTP even when TLS is on for the main app."""
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    assert TunnelHandler._loopback_origin(12345) == "http://localhost:12345"


# ---------------------------------------------------------------------------
# _loopback_ws_url (WebSocket)
# ---------------------------------------------------------------------------


def test_loopback_ws_url_tls_off(monkeypatch):
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    url, ctx = TunnelHandler._loopback_ws_url(8080, "/api/ws")
    assert url == "ws://localhost:8080/api/ws"
    assert ctx is None


def test_loopback_ws_url_tls_on_main_port(monkeypatch):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    url, ctx = TunnelHandler._loopback_ws_url(8080, "/api/ws?token=abc")
    assert url == "wss://localhost:8443/api/ws?token=abc"
    assert ctx is not None
    # Self-signed loopback — the context must NOT verify hostnames or chain.
    import ssl
    assert ctx.verify_mode == ssl.CERT_NONE


def test_loopback_ws_url_tls_on_plugin_port(monkeypatch):
    """Plugin WS ports stay ws:// even when main TLS is on."""
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    url, ctx = TunnelHandler._loopback_ws_url(12345, "/x")
    assert url == "ws://localhost:12345/x"
    assert ctx is None
