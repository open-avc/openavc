"""Tests for the authentication module (server/api/auth.py)."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

from server.api.auth import (
    check_ws_auth,
    get_ws_auth_subprotocol,
    require_programmer_auth,
)
from server import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _HeaderDict(dict):
    """Dict subclass that also works as a Starlette-like headers object."""
    pass


def _make_request(headers: dict | None = None) -> MagicMock:
    """Build a mock Request with the given headers."""
    req = MagicMock()
    req.headers = _HeaderDict(headers or {})
    return req


def _make_credentials(password: str) -> HTTPBasicCredentials:
    return HTTPBasicCredentials(username="admin", password=password)


def _make_app_with_protected_route() -> FastAPI:
    """Minimal FastAPI app with a route protected by require_programmer_auth."""
    app = FastAPI()

    @app.get("/protected")
    async def protected(auth=None):
        return {"ok": True}

    # Wire up the dependency properly via a middleware-like approach
    from fastapi import Depends

    @app.get("/guarded")
    async def guarded(_=Depends(require_programmer_auth)):
        return {"ok": True}

    return app


# ---------------------------------------------------------------------------
# 1. No auth configured -- everything is open
# ---------------------------------------------------------------------------

class TestNoAuthConfigured:
    """When neither password nor API key is set, access is unrestricted."""

    @pytest.mark.asyncio
    async def test_require_programmer_auth_passes(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "")

        request = _make_request()
        # Should return without raising
        result = await require_programmer_auth(request, credentials=None)
        assert result is None

    def test_check_ws_auth_returns_true(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "")

        assert check_ws_auth({}, {}) is True

    def test_check_ws_auth_ignores_garbage_headers(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "")

        assert check_ws_auth(
            {"token": "anything"},
            {"x-api-key": "anything"},
        ) is True


# ---------------------------------------------------------------------------
# 2. Password auth (HTTP Basic)
# ---------------------------------------------------------------------------

class TestPasswordAuth:
    """HTTP Basic password authentication via require_programmer_auth."""

    @pytest.mark.asyncio
    async def test_valid_password_succeeds(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        request = _make_request()
        creds = _make_credentials("secret123")
        result = await require_programmer_auth(request, credentials=creds)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_password_raises_401(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        request = _make_request()
        creds = _make_credentials("wrong")
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=creds)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_credentials_raises_401(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=None)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 3. API key auth
# ---------------------------------------------------------------------------

class TestApiKeyAuth:
    """X-API-Key header authentication via require_programmer_auth."""

    @pytest.mark.asyncio
    async def test_valid_api_key_succeeds(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "my-api-key")

        request = _make_request({"x-api-key": "my-api-key"})
        result = await require_programmer_auth(request, credentials=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_api_key_raises_401(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "my-api-key")

        request = _make_request({"x-api-key": "bad-key"})
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_api_key_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "my-api-key")

        request = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=None)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 4. API key takes priority over bad password
# ---------------------------------------------------------------------------

class TestApiKeyPriority:
    """When both auth methods are configured, API key is checked first."""

    @pytest.mark.asyncio
    async def test_valid_api_key_with_bad_password(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "my-api-key")

        request = _make_request({"x-api-key": "my-api-key"})
        bad_creds = _make_credentials("wrong-password")
        # API key is valid, so auth passes despite bad password
        result = await require_programmer_auth(request, credentials=bad_creds)
        assert result is None

    @pytest.mark.asyncio
    async def test_bad_api_key_falls_through_to_valid_password(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "my-api-key")

        request = _make_request({"x-api-key": "wrong-key"})
        good_creds = _make_credentials("secret123")
        # API key fails, but password succeeds
        result = await require_programmer_auth(request, credentials=good_creds)
        assert result is None

    @pytest.mark.asyncio
    async def test_both_wrong_raises_401(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "my-api-key")

        request = _make_request({"x-api-key": "wrong-key"})
        bad_creds = _make_credentials("wrong-password")
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=bad_creds)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 5. WebSocket auth -- valid credentials
# ---------------------------------------------------------------------------

class TestWsAuthValid:
    """WebSocket auth via X-API-Key, auth.TOKEN subprotocol, and ?token= param."""

    def test_api_key_header(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "ws-key")

        assert check_ws_auth({}, {"x-api-key": "ws-key"}) is True

    def test_auth_subprotocol_with_password(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        headers = {"sec-websocket-protocol": "auth.secret123"}
        assert check_ws_auth({}, headers) is True

    def test_auth_subprotocol_with_api_key(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "ws-key")

        headers = {"sec-websocket-protocol": "auth.ws-key"}
        assert check_ws_auth({}, headers) is True

    def test_token_query_param_with_password(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        assert check_ws_auth({"token": "secret123"}, {}) is True

    def test_token_query_param_with_api_key(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "ws-key")

        assert check_ws_auth({"token": "ws-key"}, {}) is True


# ---------------------------------------------------------------------------
# 6. WebSocket auth -- wrong credentials return False
# ---------------------------------------------------------------------------

class TestWsAuthInvalid:
    """Wrong WebSocket credentials must return False."""

    def test_wrong_api_key_header(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "ws-key")

        assert check_ws_auth({}, {"x-api-key": "wrong"}) is False

    def test_wrong_subprotocol_token(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        headers = {"sec-websocket-protocol": "auth.wrong"}
        assert check_ws_auth({}, headers) is False

    def test_wrong_query_token(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        assert check_ws_auth({"token": "wrong"}, {}) is False

    def test_empty_subprotocol_token(self, monkeypatch):
        """auth. prefix with no token should not match."""
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        headers = {"sec-websocket-protocol": "auth."}
        assert check_ws_auth({}, headers) is False

    def test_no_credentials_at_all(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        assert check_ws_auth({}, {}) is False

    def test_empty_token_query_param(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        assert check_ws_auth({"token": ""}, {}) is False


# ---------------------------------------------------------------------------
# 7. get_ws_auth_subprotocol -- extraction
# ---------------------------------------------------------------------------

class TestGetWsAuthSubprotocol:
    """Extracts the auth.TOKEN subprotocol from headers."""

    def test_extracts_auth_subprotocol(self):
        headers = {"sec-websocket-protocol": "auth.mytoken123"}
        assert get_ws_auth_subprotocol(headers) == "auth.mytoken123"

    def test_returns_none_when_no_auth_subprotocol(self):
        headers = {"sec-websocket-protocol": "graphql-ws"}
        assert get_ws_auth_subprotocol(headers) is None

    def test_returns_none_when_no_header(self):
        assert get_ws_auth_subprotocol({}) is None

    def test_returns_none_for_empty_header(self):
        headers = {"sec-websocket-protocol": ""}
        assert get_ws_auth_subprotocol(headers) is None


# ---------------------------------------------------------------------------
# 8. Multiple subprotocols in header (comma-separated)
# ---------------------------------------------------------------------------

class TestMultipleSubprotocols:
    """Comma-separated subprotocol headers with auth.TOKEN mixed in."""

    def test_auth_subprotocol_among_others(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        headers = {"sec-websocket-protocol": "graphql-ws, auth.secret123, chat"}
        assert check_ws_auth({}, headers) is True

    def test_auth_subprotocol_first_in_list(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        headers = {"sec-websocket-protocol": "auth.secret123, graphql-ws"}
        assert check_ws_auth({}, headers) is True

    def test_auth_subprotocol_last_in_list(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        headers = {"sec-websocket-protocol": "graphql-ws, auth.secret123"}
        assert check_ws_auth({}, headers) is True

    def test_wrong_auth_among_multiple(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "secret123")
        monkeypatch.setattr(config, "API_KEY", "")

        headers = {"sec-websocket-protocol": "graphql-ws, auth.wrong, chat"}
        assert check_ws_auth({}, headers) is False

    def test_get_subprotocol_extracts_from_multiple(self):
        headers = {"sec-websocket-protocol": "graphql-ws, auth.mytoken, chat"}
        assert get_ws_auth_subprotocol(headers) == "auth.mytoken"

    def test_get_subprotocol_returns_first_auth(self):
        """If multiple auth. subprotocols exist, return the first one."""
        headers = {"sec-websocket-protocol": "auth.first, auth.second"}
        assert get_ws_auth_subprotocol(headers) == "auth.first"

    def test_get_subprotocol_none_when_no_auth_in_list(self):
        headers = {"sec-websocket-protocol": "graphql-ws, chat, v2"}
        assert get_ws_auth_subprotocol(headers) is None


# ---------------------------------------------------------------------------
# Integration: TestClient with Depends()
# ---------------------------------------------------------------------------

class TestFastAPIIntegration:
    """Verify require_programmer_auth works as a real FastAPI dependency."""

    def test_open_access_no_auth_configured(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_valid_basic_auth(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "letmein")
        monkeypatch.setattr(config, "API_KEY", "")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded", auth=("admin", "letmein"))
        assert resp.status_code == 200

    def test_bad_basic_auth_returns_401(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "letmein")
        monkeypatch.setattr(config, "API_KEY", "")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded", auth=("admin", "wrong"))
        assert resp.status_code == 401

    def test_valid_api_key_header(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
        monkeypatch.setattr(config, "API_KEY", "test-key-123")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded", headers={"x-api-key": "test-key-123"})
        assert resp.status_code == 200

    def test_no_credentials_returns_401(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "letmein")
        monkeypatch.setattr(config, "API_KEY", "")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded")
        assert resp.status_code == 401

    def test_401_includes_www_authenticate_header(self, monkeypatch):
        monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "letmein")
        monkeypatch.setattr(config, "API_KEY", "")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Basic"
