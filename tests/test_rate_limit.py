"""Tests for per-IP rate limiting middleware."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from server.middleware.rate_limit import (
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
    monkeypatch.setattr("server.config.RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("server.config.RATE_LIMIT_OPEN_PER_MINUTE", 120)
    monkeypatch.setattr("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 60)
    monkeypatch.setattr("server.config.RATE_LIMIT_STRICT_PER_MINUTE", 10)


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
    assert _classify("GET", "/api/library") == "open"
    assert _classify("GET", "/api/library/abc123") == "open"


def test_classify_strict():
    assert _classify("POST", "/api/devices/proj1/command") == "strict"
    assert _classify("POST", "/api/devices/proj1/test") == "strict"
    assert _classify("POST", "/api/driver-definitions/d1/test-command") == "strict"
    assert _classify("POST", "/api/drivers/install") == "strict"
    assert _classify("POST", "/api/drivers/upload") == "strict"
    assert _classify("PUT", "/api/project") == "strict"
    assert _classify("POST", "/api/cloud/pair") == "strict"
    assert _classify("POST", "/api/cloud/unpair") == "strict"
    assert _classify("POST", "/api/discovery/scan") == "strict"
    assert _classify("POST", "/api/backups/backup1/restore") == "strict"


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

    with patch("server.config.RATE_LIMIT_OPEN_PER_MINUTE", 5):
        _reset_state()
        for i in range(5):
            r = client.get("/api/status")
            assert r.status_code == 200, f"Request {i+1} should pass"
        r = client.get("/api/status")
        assert r.status_code == 429


def test_standard_tier_limit():
    app = _make_app(standard_limit=3)
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 3):
        _reset_state()
        for _ in range(3):
            r = client.get("/api/devices")
            assert r.status_code == 200
        r = client.get("/api/devices")
        assert r.status_code == 429


def test_strict_tier_limit():
    app = _make_app(strict_limit=2)
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        for _ in range(2):
            r = client.post("/api/devices/d1/command")
            assert r.status_code == 200
        r = client.post("/api/devices/d1/command")
        assert r.status_code == 429


def test_strict_exceeded_blocks_all_tiers():
    """When strict tier is exceeded, ALL requests from that IP are blocked."""
    app = _make_app(strict_limit=2)
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        # Exhaust strict tier
        for _ in range(2):
            client.post("/api/devices/d1/command")
        # Standard tier should also be blocked
        r = client.get("/api/devices")
        assert r.status_code == 429
        # Open tier should also be blocked
        r = client.get("/api/status")
        assert r.status_code == 429


def test_auth_failure_counts_strict():
    """401 responses should count toward the strict tier."""
    app = _make_app(auth_status=401, strict_limit=2)
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        _reset_state()
        # Two auth failures
        for _ in range(2):
            r = client.get("/api/protected")
            assert r.status_code == 401
        # Now even a different endpoint should be blocked
        r = client.get("/api/devices")
        assert r.status_code == 429


def test_429_response_format():
    app = _make_app(standard_limit=1)
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
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
    monkeypatch.setattr("server.config.RATE_LIMIT_ENABLED", False)
    app = _make_app(standard_limit=1)
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        # Should not be rate-limited even though limit is 1
        client.get("/api/devices")
        r = client.get("/api/devices")
        assert r.status_code == 200


def test_options_not_limited():
    """CORS preflight should never be rate-limited."""
    app = _make_app(standard_limit=1)
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        # Exhaust the limit
        client.get("/api/devices")
        r = client.get("/api/devices")
        assert r.status_code == 429
        # OPTIONS should still work
        r = client.options("/api/devices")
        assert r.status_code != 429


def test_tiers_are_independent():
    """Hitting the open tier limit should not affect the standard tier."""
    app = _make_app()
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_OPEN_PER_MINUTE", 2):
        _reset_state()
        # Exhaust open tier
        for _ in range(2):
            client.get("/api/status")
        r = client.get("/api/status")
        assert r.status_code == 429
        # Standard tier should still work
        r = client.get("/api/devices")
        assert r.status_code == 200


def test_library_routes_are_open_tier():
    """Library routes should use the open tier (higher limit)."""
    app = _make_app()
    client = TestClient(app)

    with patch("server.config.RATE_LIMIT_OPEN_PER_MINUTE", 3), \
         patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        _reset_state()
        # Exhaust standard
        client.get("/api/devices")
        r = client.get("/api/devices")
        assert r.status_code == 429
        # Library should still work (open tier)
        r = client.get("/api/library")
        assert r.status_code == 200
        r = client.get("/api/library/abc")
        assert r.status_code == 200
