"""Tests for the HTTP -> HTTPS redirect listener helper in server.main.

Covers the catch-all redirect handler (Phase 3 of the HTTPS plan): status
codes, Location header construction, query-string preservation, Host
header fallback, and the certified-hostname rewrite when a cloud-issued
trusted certificate is active.
"""

from __future__ import annotations

import datetime as _dt
import ssl

import pytest
from starlette.testclient import TestClient

from server import tls
from server.main import _build_redirect_app
from tests.helpers import make_cloud_cert_pem

LABEL = "ab12cd34ef56ab78"
ZONE = "i.certtest.invalid"


@pytest.fixture(autouse=True)
def _clean_holder():
    """Cloud state lives in a module-level holder — isolate every test."""
    tls.cloud_cert_holder().clear()
    yield
    tls.cloud_cert_holder().clear()


def _client(port: int = 8443) -> TestClient:
    return TestClient(_build_redirect_app(port))


def _install_cloud_cert(tmp_path, **kwargs) -> None:
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE, **kwargs)
    tls.install_cloud_cert(tmp_path, cert_pem, key_pem)


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


# ---------------------------------------------------------------------------
# Certified-hostname rewrite (cloud-issued trusted certificate active)
# ---------------------------------------------------------------------------


def test_ipv4_host_rewritten_to_certified_name(tmp_path):
    _install_cloud_cert(tmp_path)
    resp = _client().get(
        "/present?room=3", headers={"host": "192.168.1.20:8080"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        f"https://192-168-1-20.{LABEL}.{ZONE}:8443/present?room=3"
    )
    assert resp.headers.get("cache-control") == "no-store"


def test_post_to_ipv4_host_keeps_307_with_certified_name(tmp_path):
    _install_cloud_cert(tmp_path)
    resp = _client().post(
        "/api/x", headers={"host": "10.0.0.5"}, follow_redirects=False
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == f"https://10-0-0-5.{LABEL}.{ZONE}:8443/api/x"


def test_hostname_host_not_rewritten(tmp_path):
    _install_cloud_cert(tmp_path)
    resp = _client().get(
        "/x", headers={"host": "openavc.local:8080"}, follow_redirects=False
    )
    assert resp.headers["location"] == "https://openavc.local:8443/x"


def test_ipv6_host_not_rewritten(tmp_path):
    _install_cloud_cert(tmp_path)
    resp = _client().get(
        "/x", headers={"host": "[::1]:8080"}, follow_redirects=False
    )
    assert resp.headers["location"] == "https://[::1]:8443/x"


def test_ipv4_host_without_cloud_cert_unchanged():
    resp = _client().get(
        "/x", headers={"host": "192.168.1.20:8080"}, follow_redirects=False
    )
    assert resp.headers["location"] == "https://192.168.1.20:8443/x"


def test_expired_cloud_cert_reverts_to_bare_ip(tmp_path):
    """An expired cert must not be redirected to — serve today's bare-IP
    behavior instead (the SNI layer likewise falls back to self-signed)."""
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE)
    state = tls.install_cloud_cert(tmp_path, cert_pem, key_pem)
    expired = tls.CloudCertState(
        context=state.context,
        exact_names=state.exact_names,
        wildcard_bases=state.wildcard_bases,
        hostname_suffix=state.hostname_suffix,
        expires_at=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1),
    )
    tls.cloud_cert_holder().set(expired)
    resp = _client().get(
        "/x", headers={"host": "192.168.1.20"}, follow_redirects=False
    )
    assert resp.headers["location"] == "https://192.168.1.20:8443/x"


def test_cert_without_wildcard_for_name_not_rewritten(tmp_path):
    """Defensive: if the cert wouldn't cover the encoded name, don't send
    the client there (SNI would serve self-signed and the browser would
    hard-fail the mismatched name)."""
    exact_only = tls.CloudCertState(
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER),
        exact_names=frozenset({f"{LABEL}.{ZONE}"}),
        wildcard_bases=frozenset(),
        hostname_suffix=f"{LABEL}.{ZONE}",
        expires_at=_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=30),
    )
    tls.cloud_cert_holder().set(exact_only)
    resp = _client().get(
        "/x", headers={"host": "192.168.1.20"}, follow_redirects=False
    )
    assert resp.headers["location"] == "https://192.168.1.20:8443/x"


def test_enrollment_while_running_flips_redirect(tmp_path):
    """The holder is read per request — installing a cert takes effect on
    the very next redirect, and removal reverts it (no listener restart)."""
    client = _client()
    before = client.get(
        "/x", headers={"host": "192.168.1.20"}, follow_redirects=False
    )
    assert before.headers["location"] == "https://192.168.1.20:8443/x"

    _install_cloud_cert(tmp_path)
    during = client.get(
        "/x", headers={"host": "192.168.1.20"}, follow_redirects=False
    )
    assert during.headers["location"] == (
        f"https://192-168-1-20.{LABEL}.{ZONE}:8443/x"
    )

    tls.remove_cloud_cert(tmp_path)
    after = client.get(
        "/x", headers={"host": "192.168.1.20"}, follow_redirects=False
    )
    assert after.headers["location"] == "https://192.168.1.20:8443/x"


# --- _certified_host_for (shared by the redirect app and the startup banner) ---


def test_certified_host_for_active_cert(tmp_path):
    from server.main import _certified_host_for

    _install_cloud_cert(tmp_path)
    assert _certified_host_for("192.168.4.45") == f"192-168-4-45.{LABEL}.{ZONE}"
    # Non-IPv4 hosts never get a certified name (localhost, hostnames, IPv6).
    assert _certified_host_for("localhost") is None
    assert _certified_host_for("openavc.local") is None
    assert _certified_host_for("::1") is None


def test_certified_host_for_without_cert():
    from server.main import _certified_host_for

    assert _certified_host_for("192.168.4.45") is None


# --- Plain-HTTP mode (the port-80 convenience listener with HTTPS off) ---


def test_http_scheme_targets_the_http_port():
    client = TestClient(_build_redirect_app(8080, scheme="http"))
    resp = client.get(
        "/present", headers={"host": "192.168.1.20"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://192.168.1.20:8080/present"
    assert resp.headers.get("cache-control") == "no-store"


def test_http_scheme_preserves_query_and_method_semantics():
    client = TestClient(_build_redirect_app(8080, scheme="http"))
    resp = client.post(
        "/api/x?y=1", headers={"host": "10.0.0.5"}, follow_redirects=False
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "http://10.0.0.5:8080/api/x?y=1"


def test_http_scheme_never_rewrites_to_certified_name(tmp_path):
    # An active cloud cert must not leak into plain-HTTP targets — there is
    # no certificate on the http:// side for the name to match.
    _install_cloud_cert(tmp_path)
    client = TestClient(_build_redirect_app(8080, scheme="http"))
    resp = client.get(
        "/panel", headers={"host": "192.168.1.20"}, follow_redirects=False
    )
    assert resp.headers["location"] == "http://192.168.1.20:8080/panel"
