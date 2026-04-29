"""Tests for the authentication module (server/api/auth.py)."""

import base64
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
import server.api.auth as auth_mod


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


def _set_auth(monkeypatch, password: str = "", api_key: str = "", username: str = ""):
    """Patch the live auth getters to return the given values."""
    monkeypatch.setattr(auth_mod, "_get_username", lambda: username)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: password)
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: api_key)


# ---------------------------------------------------------------------------
# 1. No auth configured -- everything is open
# ---------------------------------------------------------------------------

class TestNoAuthConfigured:
    """When neither password nor API key is set, access is unrestricted."""

    @pytest.mark.asyncio
    async def test_require_programmer_auth_passes(self, monkeypatch):
        _set_auth(monkeypatch)

        request = _make_request()
        # Should return without raising
        result = await require_programmer_auth(request, credentials=None)
        assert result is None

    def test_check_ws_auth_returns_true(self, monkeypatch):
        _set_auth(monkeypatch)

        assert check_ws_auth({}, {}) is True

    def test_check_ws_auth_ignores_garbage_headers(self, monkeypatch):
        _set_auth(monkeypatch)

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
        _set_auth(monkeypatch, password="secret123")

        request = _make_request()
        creds = _make_credentials("secret123")
        result = await require_programmer_auth(request, credentials=creds)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_password_raises_401(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        request = _make_request()
        creds = _make_credentials("wrong")
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=creds)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_credentials_raises_401(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

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
        _set_auth(monkeypatch, api_key="my-api-key")

        request = _make_request({"x-api-key": "my-api-key"})
        result = await require_programmer_auth(request, credentials=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_api_key_raises_401(self, monkeypatch):
        _set_auth(monkeypatch, api_key="my-api-key")

        request = _make_request({"x-api-key": "bad-key"})
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_api_key_header_raises_401(self, monkeypatch):
        _set_auth(monkeypatch, api_key="my-api-key")

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
        _set_auth(monkeypatch, password="secret123", api_key="my-api-key")

        request = _make_request({"x-api-key": "my-api-key"})
        bad_creds = _make_credentials("wrong-password")
        # API key is valid, so auth passes despite bad password
        result = await require_programmer_auth(request, credentials=bad_creds)
        assert result is None

    @pytest.mark.asyncio
    async def test_bad_api_key_falls_through_to_valid_password(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123", api_key="my-api-key")

        request = _make_request({"x-api-key": "wrong-key"})
        good_creds = _make_credentials("secret123")
        # API key fails, but password succeeds
        result = await require_programmer_auth(request, credentials=good_creds)
        assert result is None

    @pytest.mark.asyncio
    async def test_both_wrong_raises_401(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123", api_key="my-api-key")

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
        _set_auth(monkeypatch, api_key="ws-key")

        assert check_ws_auth({}, {"x-api-key": "ws-key"}) is True

    def test_auth_subprotocol_with_password(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        headers = {"sec-websocket-protocol": "auth.secret123"}
        assert check_ws_auth({}, headers) is True

    def test_auth_subprotocol_with_api_key(self, monkeypatch):
        _set_auth(monkeypatch, api_key="ws-key")

        headers = {"sec-websocket-protocol": "auth.ws-key"}
        assert check_ws_auth({}, headers) is True

    def test_token_query_param_not_accepted(self, monkeypatch):
        """Token-in-URL was removed — must not authenticate."""
        _set_auth(monkeypatch, password="secret123")

        assert check_ws_auth({"token": "secret123"}, {}) is False


# ---------------------------------------------------------------------------
# 6. WebSocket auth -- wrong credentials return False
# ---------------------------------------------------------------------------

class TestWsAuthInvalid:
    """Wrong WebSocket credentials must return False."""

    def test_wrong_api_key_header(self, monkeypatch):
        _set_auth(monkeypatch, api_key="ws-key")

        assert check_ws_auth({}, {"x-api-key": "wrong"}) is False

    def test_wrong_subprotocol_token(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        headers = {"sec-websocket-protocol": "auth.wrong"}
        assert check_ws_auth({}, headers) is False

    def test_wrong_query_token(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        assert check_ws_auth({"token": "wrong"}, {}) is False

    def test_empty_subprotocol_token(self, monkeypatch):
        """auth. prefix with no token should not match."""
        _set_auth(monkeypatch, password="secret123")

        headers = {"sec-websocket-protocol": "auth."}
        assert check_ws_auth({}, headers) is False

    def test_no_credentials_at_all(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        assert check_ws_auth({}, {}) is False

    def test_empty_token_query_param(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        assert check_ws_auth({"token": ""}, {}) is False


# ---------------------------------------------------------------------------
# 7. get_ws_auth_subprotocol -- extraction
# ---------------------------------------------------------------------------

class TestGetWsAuthSubprotocol:
    """Extracts the auth.TOKEN subprotocol from headers."""

    def test_extracts_auth_subprotocol(self):
        headers = {"sec-websocket-protocol": "auth.mytoken123"}
        assert get_ws_auth_subprotocol(headers) == "auth"

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
        _set_auth(monkeypatch, password="secret123")

        headers = {"sec-websocket-protocol": "graphql-ws, auth.secret123, chat"}
        assert check_ws_auth({}, headers) is True

    def test_auth_subprotocol_first_in_list(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        headers = {"sec-websocket-protocol": "auth.secret123, graphql-ws"}
        assert check_ws_auth({}, headers) is True

    def test_auth_subprotocol_last_in_list(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        headers = {"sec-websocket-protocol": "graphql-ws, auth.secret123"}
        assert check_ws_auth({}, headers) is True

    def test_wrong_auth_among_multiple(self, monkeypatch):
        _set_auth(monkeypatch, password="secret123")

        headers = {"sec-websocket-protocol": "graphql-ws, auth.wrong, chat"}
        assert check_ws_auth({}, headers) is False

    def test_get_subprotocol_extracts_from_multiple(self):
        headers = {"sec-websocket-protocol": "graphql-ws, auth.mytoken, chat"}
        assert get_ws_auth_subprotocol(headers) == "auth"

    def test_get_subprotocol_returns_auth_for_first_match(self):
        """If multiple auth. subprotocols exist, return generic 'auth'."""
        headers = {"sec-websocket-protocol": "auth.first, auth.second"}
        assert get_ws_auth_subprotocol(headers) == "auth"

    def test_get_subprotocol_none_when_no_auth_in_list(self):
        headers = {"sec-websocket-protocol": "graphql-ws, chat, v2"}
        assert get_ws_auth_subprotocol(headers) is None


# ---------------------------------------------------------------------------
# Integration: TestClient with Depends()
# ---------------------------------------------------------------------------

class TestFastAPIIntegration:
    """Verify require_programmer_auth works as a real FastAPI dependency."""

    def test_open_access_no_auth_configured(self, monkeypatch):
        _set_auth(monkeypatch)

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_valid_basic_auth(self, monkeypatch):
        _set_auth(monkeypatch, password="letmein")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded", auth=("admin", "letmein"))
        assert resp.status_code == 200

    def test_bad_basic_auth_returns_401(self, monkeypatch):
        _set_auth(monkeypatch, password="letmein")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded", auth=("admin", "wrong"))
        assert resp.status_code == 401

    def test_valid_api_key_header(self, monkeypatch):
        _set_auth(monkeypatch, api_key="test-key-123")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded", headers={"x-api-key": "test-key-123"})
        assert resp.status_code == 200

    def test_no_credentials_returns_401(self, monkeypatch):
        _set_auth(monkeypatch, password="letmein")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded")
        assert resp.status_code == 401

    def test_401_includes_www_authenticate_header(self, monkeypatch):
        _set_auth(monkeypatch, password="letmein")

        app = _make_app_with_protected_route()
        client = TestClient(app)
        resp = client.get("/guarded")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Basic"


# ---------------------------------------------------------------------------
# 9. Username + password combined check
# ---------------------------------------------------------------------------

class TestUsernameAndPassword:
    """When a username is configured, both username and password must match."""

    @pytest.mark.asyncio
    async def test_correct_user_and_pass_succeeds(self, monkeypatch):
        _set_auth(monkeypatch, username="aaron", password="secret")

        request = _make_request()
        creds = HTTPBasicCredentials(username="aaron", password="secret")
        result = await require_programmer_auth(request, credentials=creds)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_username_raises_401(self, monkeypatch):
        _set_auth(monkeypatch, username="aaron", password="secret")

        request = _make_request()
        creds = HTTPBasicCredentials(username="someone-else", password="secret")
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=creds)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_password_with_correct_username_raises_401(self, monkeypatch):
        _set_auth(monkeypatch, username="aaron", password="secret")

        request = _make_request()
        creds = HTTPBasicCredentials(username="aaron", password="wrong")
        with pytest.raises(HTTPException) as exc_info:
            await require_programmer_auth(request, credentials=creds)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_username_config_accepts_any_username(self, monkeypatch):
        """Legacy mode: password set, no username — any username works."""
        _set_auth(monkeypatch, username="", password="secret")

        request = _make_request()
        creds = HTTPBasicCredentials(username="anything", password="secret")
        result = await require_programmer_auth(request, credentials=creds)
        assert result is None

    def test_integration_user_and_pass(self, monkeypatch):
        _set_auth(monkeypatch, username="aaron", password="secret")

        app = _make_app_with_protected_route()
        client = TestClient(app)

        ok = client.get("/guarded", auth=("aaron", "secret"))
        assert ok.status_code == 200

        wrong_user = client.get("/guarded", auth=("nope", "secret"))
        assert wrong_user.status_code == 401

        wrong_pass = client.get("/guarded", auth=("aaron", "nope"))
        assert wrong_pass.status_code == 401


# ---------------------------------------------------------------------------
# 10. WebSocket Authorization: Basic header (browser-cached HTTP Basic creds)
# ---------------------------------------------------------------------------

class TestWsAuthBasicHeader:
    """Browsers send Authorization: Basic on WS handshake when creds are cached."""

    def test_basic_header_with_correct_user_and_pass(self, monkeypatch):
        _set_auth(monkeypatch, username="aaron", password="secret")

        encoded = base64.b64encode(b"aaron:secret").decode("ascii")
        headers = {"authorization": f"Basic {encoded}"}
        assert check_ws_auth({}, headers) is True

    def test_basic_header_with_wrong_password(self, monkeypatch):
        _set_auth(monkeypatch, username="aaron", password="secret")

        encoded = base64.b64encode(b"aaron:wrong").decode("ascii")
        headers = {"authorization": f"Basic {encoded}"}
        assert check_ws_auth({}, headers) is False

    def test_basic_header_with_wrong_username(self, monkeypatch):
        _set_auth(monkeypatch, username="aaron", password="secret")

        encoded = base64.b64encode(b"someone:secret").decode("ascii")
        headers = {"authorization": f"Basic {encoded}"}
        assert check_ws_auth({}, headers) is False

    def test_basic_header_with_password_only_legacy(self, monkeypatch):
        """No username configured: any username in the header is accepted."""
        _set_auth(monkeypatch, password="secret")

        encoded = base64.b64encode(b"anything:secret").decode("ascii")
        headers = {"authorization": f"Basic {encoded}"}
        assert check_ws_auth({}, headers) is True

    def test_malformed_basic_header_falls_through(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")

        # Garbage base64 — should not authenticate, must not raise
        headers = {"authorization": "Basic !!!notbase64!!!"}
        assert check_ws_auth({}, headers) is False

    def test_non_basic_authorization_header_ignored(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")

        headers = {"authorization": "Bearer some-token"}
        assert check_ws_auth({}, headers) is False

    def test_basic_header_without_colon_ignored(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")

        encoded = base64.b64encode(b"justastring").decode("ascii")
        headers = {"authorization": f"Basic {encoded}"}
        assert check_ws_auth({}, headers) is False
