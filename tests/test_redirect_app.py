"""Tests for the HTTP -> HTTPS redirect listener helper in server.main.

Covers the catch-all redirect handler (Phase 3 of the HTTPS plan): status
codes, Location header construction, query-string preservation, and Host
header fallback.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from server.main import _build_redirect_app


def _client(port: int = 8443) -> TestClient:
    return TestClient(_build_redirect_app(port))


def test_get_returns_302_with_https_url():
    resp = _client().get(
        "/programmer", headers={"host": "myserver:8080"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://myserver:8443/programmer"


def test_head_returns_302():
    resp = _client().head(
        "/api/health", headers={"host": "h:8080"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://h:8443/api/health"


def test_post_returns_307_to_preserve_method():
    resp = _client().post(
        "/api/devices/x/command",
        json={"action": "on"},
        headers={"host": "host1"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://host1:8443/api/devices/x/command"


def test_other_methods_use_307():
    for method, fn in (
        ("PUT", _client().put),
        ("PATCH", _client().patch),
        ("DELETE", _client().delete),
        ("OPTIONS", _client().options),
    ):
        resp = fn("/x", headers={"host": "h"}, follow_redirects=False)
        assert resp.status_code == 307, f"{method} should use 307"


def test_query_string_preserved():
    resp = _client().get(
        "/api/devices?foo=bar&baz=1",
        headers={"host": "h:8080"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://h:8443/api/devices?foo=bar&baz=1"


def test_root_path():
    resp = _client().get(
        "/", headers={"host": "h:8080"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://h:8443/"


def test_host_header_with_no_port_used_as_is():
    resp = _client().get(
        "/x", headers={"host": "openavc.local"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://openavc.local:8443/x"


def test_pathological_host_falls_back_to_url_hostname():
    """A Host header with whitespace or special chars is rejected; fall back."""
    resp = _client().get(
        "/x", headers={"host": "evil host with spaces"}, follow_redirects=False
    )
    assert resp.status_code == 302
    # Falls back to whatever TestClient resolves as the hostname.
    location = resp.headers["location"]
    assert location.startswith("https://") and location.endswith(":8443/x")


def test_redirect_is_not_cacheable():
    """Redirects must not be cached — TLS can be toggled off at runtime, and
    a cached permanent redirect would lock users out until they manually clear
    their browser cache."""
    resp = _client().get(
        "/x", headers={"host": "h:8080"}, follow_redirects=False
    )
    assert resp.headers.get("cache-control") == "no-store"


def test_custom_tls_port_in_redirect():
    resp = _build_redirect_app(9443)  # noqa: F841 - reused inline
    client = TestClient(_build_redirect_app(9443))
    resp = client.get("/x", headers={"host": "h:8080"}, follow_redirects=False)
    assert resp.headers["location"] == "https://h:9443/x"
