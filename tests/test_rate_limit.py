"""Tests for per-IP rate limiting middleware."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from openavc.middleware.rate_limit import (
    RateLimitMiddleware,
    _classify,
    _ip_buckets,
    _warn_dedup,
)


def _reset_state():
    """Clear all rate-limit state between tests."""
    _ip_buckets.clear()
    _warn_dedup.clear()


def _make_app(
    *,
    auth_status: int = 200,
    rate_limit_enabled: bool = True,
    open_limit: int = 120,
    standard_limit: int = 60,
    strict_limit: int = 10,
) -> FastAPI:
    """Build a minimal FastAPI app with rate limiting for testing."""
    app = FastAPI()

    # Stub routes that return the requested status
    @app.get("/api/status")
    async def status():
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/devices")
    async def devices():
        return []

    @app.get("/api/devices/{device_id}")
    async def device(device_id: str):
        return {"id": device_id}

    @app.post("/api/devices/{device_id}/command")
    async def device_command(device_id: str):
        return {"sent": True}

    @app.post("/api/devices/{device_id}/test")
    async def device_test(device_id: str):
        return {"tested": True}

    @app.put("/api/project")
    async def save_project():
        return {"saved": True}

    @app.post("/api/cloud/pair")
    async def cloud_pair():
        return {"paired": True}

    @app.get("/api/library")
    async def library():
        return []

    @app.get("/api/library/{project_id}")
    async def library_item(project_id: str):
        return {"id": project_id}

    # Route that simulates auth failure
    @app.get("/api/protected")
    async def protected():
        if auth_status == 401:
            return JSONResponse(status_code=401, content={"detail": "Auth required"})
        return {"ok": True}

    # Middleware that can force 401 on any protected route (simulates auth layer)
    if auth_status == 401:

        class FakeAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if request.url.path == "/api/protected":
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Auth required"},
                    )
                return await call_next(request)

        app.add_middleware(FakeAuthMiddleware)

    app.add_middleware(RateLimitMiddleware)

    return app


@pytest.fixture(autouse=True)
def _clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    """Ensure consistent config for all tests."""
    monkeypatch.setattr("openavc.config.RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("openavc.config.RATE_LIMIT_OPEN_PER_MINUTE", 120)
    monkeypatch.setattr("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 60)
    monkeypatch.setattr("openavc.config.RATE_LIMIT_CONTROL_PER_MINUTE", 120)
    monkeypatch.setattr("openavc.config.RATE_LIMIT_STRICT_PER_MINUTE", 10)


# --- Path classification ---


def test_classify_skip_static():
    assert _classify("GET", "/panel/index.html") == "skip"
    assert _classify("GET", "/programmer/app.js") == "skip"


def test_classify_skip_websocket():
    assert _classify("GET", "/ws") == "skip"
    assert _classify("GET", "/isc/ws") == "skip"


def test_classify_skip_non_api():
    assert _classify("GET", "/favicon.ico") == "skip"


def test_classify_open():
    assert _classify("GET", "/api/status") == "open"
    assert _classify("GET", "/api/health") == "open"
    assert _classify("GET", "/api/cloud/status") == "open"
    assert _classify("GET", "/api/startup-status") == "open"
    assert _classify("GET", "/api/auth/required") == "open"
    # Library moved to protected (standard tier) by the 1821cba security fix.
    assert _classify("GET", "/api/library") == "standard"
    assert _classify("GET", "/api/library/abc123") == "standard"


def test_classify_strict():
    """Security-sensitive ops keep the low strict budget."""
    assert _classify("POST", "/api/auth/session") == "strict"
    assert _classify("POST", "/api/cloud/pair") == "strict"
    assert _classify("POST", "/api/cloud/unpair") == "strict"
    assert _classify("POST", "/api/backups/backup1/restore") == "strict"


def test_classify_control():
    """Authenticated commissioning ops get their own higher-budget tier."""
    assert _classify("POST", "/api/devices/proj1/command") == "control"
    assert _classify("POST", "/api/devices/proj1/test") == "control"
    assert _classify("POST", "/api/driver-definitions/d1/test-command") == "control"
    assert _classify("POST", "/api/drivers/install") == "control"
    assert _classify("POST", "/api/drivers/upload") == "control"
    assert _classify("PUT", "/api/project") == "control"
    assert _classify("POST", "/api/discovery/scan") == "control"


def test_classify_standard():
    assert _classify("GET", "/api/devices") == "standard"
    assert _classify("GET", "/api/devices/proj1") == "standard"
    assert _classify("GET", "/api/project") == "standard"
    assert _classify("GET", "/api/scripts/s1/source") == "standard"
    assert _classify("POST", "/api/macros/m1/execute") == "standard"


# --- Middleware behavior ---


def test_requests_within_limit_pass():
    app = _make_app()
    client = TestClient(app)
    for _ in range(5):
        r = client.get("/api/devices")
        assert r.status_code == 200


def test_open_tier_high_limit():
    """Open tier should allow many requests."""
    app = _make_app(open_limit=5)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_OPEN_PER_MINUTE", 5):
        _reset_state()
        for i in range(5):
            r = client.get("/api/status")
            assert r.status_code == 200, f"Request {i+1} should pass"
        r = client.get("/api/status")
        assert r.status_code == 429


def test_standard_tier_limit():
    app = _make_app(standard_limit=3)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 3):
        _reset_state()
        for _ in range(3):
            r = client.get("/api/devices")
            assert r.status_code == 200
        r = client.get("/api/devices")
        assert r.status_code == 429


def test_strict_tier_limit():
    app = _make_app(strict_limit=2)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        for _ in range(2):
            r = client.post("/api/cloud/pair")
            assert r.status_code == 200
        r = client.post("/api/cloud/pair")
        assert r.status_code == 429


def test_strict_exceeded_blocks_only_strict():
    """When strict tier is exceeded, only strict-tier requests are blocked."""
    app = _make_app(strict_limit=2)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        # Exhaust strict tier
        for _ in range(2):
            client.post("/api/cloud/pair")
        # Strict tier should be blocked
        r = client.post("/api/cloud/pair")
        assert r.status_code == 429
        # Standard tier should still work
        r = client.get("/api/devices")
        assert r.status_code == 200
        # Open tier should still work
        r = client.get("/api/status")
        assert r.status_code == 200


def test_auth_failure_throttles_every_tier_at_strict_rate():
    """401 responses feed a brute-force counter that throttles EVERY tier at
    the strict rate — not just strict-tier traffic. Credential probing against
    a standard-tier endpoint must be stopped too."""
    app = _make_app(auth_status=401, strict_limit=2)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        # Two auth failures (the strict/brute-force limit).
        for _ in range(2):
            r = client.get("/api/protected")
            assert r.status_code == 401
        # Strict-tier endpoint is blocked...
        assert client.post("/api/cloud/pair").status_code == 429
        # ...the control tier too...
        assert client.post("/api/devices/d1/command").status_code == 429
        # ...and so is the standard tier (brute-force protection
        # is no longer confined to strict-tier endpoints).
        assert client.get("/api/devices").status_code == 429


def test_brute_force_probing_throttled_at_strict_rate_not_standard():
    """Probing a STANDARD-tier protected endpoint is capped at the strict
    (brute-force) rate, far below the standard rate — the standard window alone
    would let ~60/min through."""
    app = _make_app(auth_status=401, standard_limit=60, strict_limit=3)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 60), \
         patch("openavc.config.RATE_LIMIT_STRICT_PER_MINUTE", 3):
        _reset_state()
        # /api/protected classifies as standard, but only 3 probes get through.
        for _ in range(3):
            assert client.get("/api/protected").status_code == 401
        # 4th probe is throttled — at the strict rate (3), not the standard 60.
        assert client.get("/api/protected").status_code == 429


def test_legit_strict_usage_does_not_trip_brute_force_counter():
    """A separate window: hammering a strict-tier endpoint with SUCCESSFUL
    calls fills the strict window but not the auth-failure counter, so it never
    leaks into the standard tier (guards against conflating the two limits)."""
    app = _make_app(strict_limit=2)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        for _ in range(2):
            assert client.post("/api/cloud/pair").status_code == 200
        # Strict endpoint now blocked, but standard/open still work — the 200s
        # went to the strict window, not the brute-force counter.
        assert client.post("/api/cloud/pair").status_code == 429
        assert client.get("/api/devices").status_code == 200
        assert client.get("/api/status").status_code == 200


def test_429_response_format():
    app = _make_app(standard_limit=1)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        client.get("/api/devices")
        r = client.get("/api/devices")
        assert r.status_code == 429
        body = r.json()
        assert "detail" in body
        assert "retry_after" in body
        assert isinstance(body["retry_after"], int)
        assert "Retry-After" in r.headers


def test_disabled_via_config(monkeypatch):
    monkeypatch.setattr("openavc.config.RATE_LIMIT_ENABLED", False)
    app = _make_app(standard_limit=1)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        # Should not be rate-limited even though limit is 1
        client.get("/api/devices")
        r = client.get("/api/devices")
        assert r.status_code == 200


def test_options_not_limited():
    """CORS preflight should never be rate-limited."""
    app = _make_app(standard_limit=1)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        # Exhaust the limit
        client.get("/api/devices")
        r = client.get("/api/devices")
        assert r.status_code == 429
        # OPTIONS should still work
        r = client.options("/api/devices")
        assert r.status_code != 429


def test_commissioning_traffic_does_not_share_the_strict_bucket():
    """Device commands are commissioning traffic, not security ops: exhausting
    the strict (security) window must not 429 device command/test calls, and
    command bursts must not drain the strict window for security ops."""
    app = _make_app()
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        # Exhaust the strict/security window
        for _ in range(2):
            assert client.post("/api/cloud/pair").status_code == 200
        assert client.post("/api/cloud/pair").status_code == 429
        # Commissioning ops still flow
        assert client.post("/api/devices/d1/command").status_code == 200
        assert client.post("/api/devices/d1/test").status_code == 200
        assert client.put("/api/project").status_code == 200


def test_control_tier_has_its_own_limit():
    """The control tier is still bounded — by its own (higher) window."""
    app = _make_app()
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_CONTROL_PER_MINUTE", 3):
        _reset_state()
        for _ in range(3):
            assert client.post("/api/devices/d1/command").status_code == 200
        assert client.post("/api/devices/d1/command").status_code == 429
        # Exceeding control does not block security or standard tiers
        assert client.post("/api/cloud/pair").status_code == 200
        assert client.get("/api/devices").status_code == 200


def test_tiers_are_independent():
    """Hitting the open tier limit should not affect the standard tier."""
    app = _make_app()
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_OPEN_PER_MINUTE", 2):
        _reset_state()
        # Exhaust open tier
        for _ in range(2):
            client.get("/api/status")
        r = client.get("/api/status")
        assert r.status_code == 429
        # Standard tier should still work
        r = client.get("/api/devices")
        assert r.status_code == 200


def test_library_routes_are_standard_tier():
    """Library routes share the standard tier with other authenticated /api/ routes
    (since the 1821cba security fix moved them off the open tier)."""
    app = _make_app()
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 2):
        _reset_state()
        # First two library hits succeed (standard limit = 2)
        assert client.get("/api/library").status_code == 200
        assert client.get("/api/library/abc").status_code == 200
        # Third hit on a sibling standard-tier path should be throttled
        r = client.get("/api/devices")
        assert r.status_code == 429


# --- X-Forwarded-For trust (client-IP spoofing) ---


def _req_with_xff(peer_host: str, xff: str | None) -> Request:
    """Build a minimal Starlette Request with a TCP peer and optional XFF."""
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/devices",
        "headers": headers,
        "client": (peer_host, 5555),
        "query_string": b"",
    }
    return Request(scope)


def _behind_proxy(monkeypatch, proxies=()):
    """network.trust_forwarded_for on, with these proxies on other machines."""
    monkeypatch.setattr("openavc.config.TRUST_FORWARDED_FOR", True)
    monkeypatch.setattr("openavc.config.TRUSTED_PROXIES", list(proxies), raising=False)


def _from_peer(app, host: str) -> TestClient:
    """A client whose socket peer is ``host`` (TestClient's own is "testclient")."""

    async def _app(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = (host, 50000)
        await app(scope, receive, send)

    return TestClient(_app)


def _second_hit(client: TestClient, xff: str | None) -> int:
    """Status of the second standard-tier request with the limit at one."""
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    assert client.get("/api/devices", headers=headers).status_code == 200
    return client.get("/api/devices", headers=headers).status_code


def test_xff_ignored_by_default(monkeypatch):
    """Default posture: X-Forwarded-For is NOT trusted, so the real TCP peer is
    used and a client can't spoof its source IP."""
    from openavc.middleware.rate_limit import _client_key

    monkeypatch.setattr("openavc.config.TRUST_FORWARDED_FOR", False)
    req = _req_with_xff("203.0.113.9", "127.0.0.1")
    assert _client_key(req) == ("203.0.113.9", False)


def test_xff_honored_from_a_listed_proxy(monkeypatch):
    """Behind a proxy on another machine that is listed, the client it names
    is the key, read from the right past the proxy's own entry."""
    from openavc.middleware.rate_limit import _client_key

    _behind_proxy(monkeypatch, ["10.0.0.1"])
    req = _req_with_xff("10.0.0.1", "198.51.100.7, 10.0.0.1")
    assert _client_key(req) == ("198.51.100.7", False)


def test_xff_ignored_from_an_unlisted_machine(monkeypatch):
    from openavc.middleware.rate_limit import _client_key

    _behind_proxy(monkeypatch)
    req = _req_with_xff("10.0.0.1", "198.51.100.7")
    assert _client_key(req) == ("10.0.0.1", False)


def test_the_machine_s_own_screen_is_exempt(monkeypatch):
    from openavc.middleware.rate_limit import _client_key

    _behind_proxy(monkeypatch)
    assert _client_key(_req_with_xff("127.0.0.1", None)) == ("127.0.0.1", True)


def test_spoofed_xff_localhost_does_not_exempt(monkeypatch):
    """Security regression: a client must not be able to spoof
    `X-Forwarded-For: 127.0.0.1` to claim the localhost rate-limit exemption
    (which would also defeat the 401 brute-force counter)."""
    monkeypatch.setattr("openavc.config.TRUST_FORWARDED_FOR", False)
    app = _make_app(standard_limit=1)
    client = TestClient(app)

    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        spoof = {"X-Forwarded-For": "127.0.0.1"}
        assert client.get("/api/devices", headers=spoof).status_code == 200
        # Second hit must still be throttled — the spoof did NOT exempt it.
        assert client.get("/api/devices", headers=spoof).status_code == 429


def test_spoofed_xff_localhost_does_not_exempt_behind_a_proxy_either(monkeypatch):
    """The same spoof with trust_forwarded_for ON, from a client that reached
    the port without going through the proxy. The header was believed from
    any peer, so the client was the machine itself and never throttled."""
    _behind_proxy(monkeypatch)
    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        client = _from_peer(_make_app(standard_limit=1), "192.168.4.20")
        assert _second_hit(client, "127.0.0.1") == 429


def test_an_appending_proxy_cannot_carry_a_spoof(monkeypatch):
    """nginx's $proxy_add_x_forwarded_for keeps what the client sent and
    appends the real address. Read from the left, a client that sent
    127.0.0.1 through it was exempt."""
    _behind_proxy(monkeypatch)
    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        client = _from_peer(_make_app(standard_limit=1), "127.0.0.1")
        assert _second_hit(client, "127.0.0.1, 198.51.100.9") == 429


def test_a_forwarded_loopback_client_is_metered(monkeypatch):
    """A proxy that says its client was 127.0.0.1 means its own machine, and a
    forwarded request is not the console anywhere else either."""
    _behind_proxy(monkeypatch)
    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        client = _from_peer(_make_app(standard_limit=1), "127.0.0.1")
        assert _second_hit(client, "127.0.0.1") == 429


def test_the_machine_s_own_screen_is_still_never_throttled(monkeypatch):
    _behind_proxy(monkeypatch)
    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        client = _from_peer(_make_app(standard_limit=1), "127.0.0.1")
        assert _second_hit(client, None) == 200


def test_clients_behind_a_listed_proxy_get_their_own_buckets(monkeypatch):
    _behind_proxy(monkeypatch, ["10.0.0.5"])
    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        client = _from_peer(_make_app(standard_limit=1), "10.0.0.5")
        assert _second_hit(client, "198.51.100.20") == 429
        assert client.get("/api/devices", headers={"X-Forwarded-For": "198.51.100.21"}).status_code == 200


def test_clients_behind_an_unlisted_proxy_share_its_bucket(monkeypatch):
    """The announced cost: a proxy on another machine has to be listed, or
    every client behind it is metered as the proxy."""
    _behind_proxy(monkeypatch)
    with patch("openavc.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        client = _from_peer(_make_app(standard_limit=1), "10.0.0.5")
        assert client.get("/api/devices", headers={"X-Forwarded-For": "198.51.100.20"}).status_code == 200
        assert client.get("/api/devices", headers={"X-Forwarded-For": "198.51.100.21"}).status_code == 429
