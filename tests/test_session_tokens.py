"""Tests for Programmer session-token auth (server/api/session_tokens.py).

The SPA exchanges the admin password for a short-lived token
(POST /api/auth/session) and authenticates with `Authorization: Bearer` and
the `auth.bearer.<token>` WebSocket subprotocol from then on. These tests
pin the whole contract: minting, sliding expiry, invalidation on credential
change and restart, explicit logout, both transports, and that Basic and
X-API-Key clients keep working untouched.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import server.api.auth as auth_mod
import server.api.session_tokens as st_mod
from server.api.auth import check_ws_auth, require_programmer_auth
from server.api.session_tokens import SessionTokenStore
from server.middleware.rate_limit import _classify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_auth(monkeypatch, password: str = "", api_key: str = "", username: str = ""):
    """Patch the live auth getters (same pattern as test_api_auth.py)."""
    monkeypatch.setattr(auth_mod, "_get_username", lambda: username)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: password)
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: api_key)


@pytest.fixture()
def fresh_store(monkeypatch) -> SessionTokenStore:
    """Give auth + the endpoints an isolated store for each test."""
    store = SessionTokenStore()
    monkeypatch.setattr(st_mod, "store", store)
    return store


class _Clock:
    """Controllable stand-in for time.time inside the store module."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(st_mod, "time", c)
    return c


def _basic_header(user: str, password: str) -> dict:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def _make_app() -> TestClient:
    """App with the real auth/session routes plus one protected route."""
    from server.api.routes.system import open_router

    app = FastAPI()
    app.include_router(open_router, prefix="/api")

    @app.get("/api/protected", dependencies=[Depends(require_programmer_auth)])
    async def protected():
        return {"ok": True}

    return TestClient(app)


def _mint(client: TestClient, user: str = "admin", password: str = "hunter22") -> str:
    res = client.post("/api/auth/session", headers=_basic_header(user, password))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token"] and body["expires_in"] > 0
    return body["token"]


# ---------------------------------------------------------------------------
# Store unit behavior
# ---------------------------------------------------------------------------

class TestStore:
    def test_issue_and_validate(self, clock):
        store = SessionTokenStore()
        token, expires_in = store.issue("fp")
        assert expires_in == st_mod.SESSION_TTL_SECONDS
        assert store.validate(token, "fp")

    def test_unknown_and_empty_tokens_rejected(self, clock):
        store = SessionTokenStore()
        assert not store.validate("", "fp")
        assert not store.validate("nope", "fp")

    def test_expiry(self, clock):
        store = SessionTokenStore(ttl=100)
        token, _ = store.issue("fp")
        clock.now += 101
        assert not store.validate(token, "fp")

    def test_sliding_expiry_extends_on_use(self, clock):
        store = SessionTokenStore(ttl=100)
        token, _ = store.issue("fp")
        # Touch the session just before each expiry; it must stay alive far
        # beyond the original TTL as long as it keeps being used.
        for _ in range(5):
            clock.now += 90
            assert store.validate(token, "fp")
        clock.now += 101
        assert not store.validate(token, "fp")

    def test_fingerprint_mismatch_kills_token(self, clock):
        store = SessionTokenStore()
        token, _ = store.issue("fp-old")
        assert not store.validate(token, "fp-new")
        # And the entry is gone — not resurrected by the old fingerprint.
        assert not store.validate(token, "fp-old")

    def test_revoke(self, clock):
        store = SessionTokenStore()
        token, _ = store.issue("fp")
        assert store.revoke(token)
        assert not store.validate(token, "fp")
        assert not store.revoke(token)

    def test_clear_models_restart(self, clock):
        # The table is process-memory only; a restart drops every session.
        store = SessionTokenStore()
        token, _ = store.issue("fp")
        store.clear()
        assert not store.validate(token, "fp")

    def test_capacity_evicts_soonest_expiry(self, clock):
        store = SessionTokenStore(ttl=100, max_sessions=3)
        first, _ = store.issue("fp")
        clock.now += 1
        keep = [store.issue("fp")[0] for _ in range(2)]
        clock.now += 1
        overflow, _ = store.issue("fp")  # evicts `first` (closest to expiry)
        assert not store.validate(first, "fp")
        for t in [*keep, overflow]:
            assert store.validate(t, "fp")


# ---------------------------------------------------------------------------
# Mint + logout endpoints
# ---------------------------------------------------------------------------

class TestSessionEndpoints:
    def test_mint_with_valid_basic(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        token = _mint(client)
        assert client.get(
            "/api/protected", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200

    def test_mint_wrong_password_401_without_basic_challenge(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        res = client.post("/api/auth/session", headers=_basic_header("admin", "wrong"))
        assert res.status_code == 401
        # No WWW-Authenticate: the SPA login form must never trigger the
        # browser's native Basic dialog.
        assert "www-authenticate" not in {k.lower() for k in res.headers}

    def test_mint_checks_username_when_configured(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22", username="aaron")
        client = _make_app()
        assert client.post(
            "/api/auth/session", headers=_basic_header("intruder", "hunter22")
        ).status_code == 401
        _mint(client, user="aaron")

    def test_mint_requires_configured_password(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch)  # open instance — nothing to mint against
        client = _make_app()
        res = client.post("/api/auth/session", headers=_basic_header("admin", "x"))
        assert res.status_code == 401

    def test_mint_without_header_401(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        assert client.post("/api/auth/session").status_code == 401

    def test_logout_revokes(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        token = _mint(client)
        res = client.delete(
            "/api/auth/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200 and res.json()["revoked"] is True
        assert client.get(
            "/api/protected", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401

    def test_logout_is_idempotent(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        res = client.delete(
            "/api/auth/session", headers={"Authorization": "Bearer unknown"}
        )
        assert res.status_code == 200 and res.json()["revoked"] is False


# ---------------------------------------------------------------------------
# Bearer on the HTTP hot path — and Basic / X-API-Key untouched
# ---------------------------------------------------------------------------

class TestHttpAuth:
    def test_garbage_bearer_rejected(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        assert client.get(
            "/api/protected", headers={"Authorization": "Bearer forged"}
        ).status_code == 401

    def test_basic_still_accepted(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        assert client.get(
            "/api/protected", headers=_basic_header("admin", "hunter22")
        ).status_code == 200

    def test_api_key_still_accepted(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22", api_key="k123")
        client = _make_app()
        assert client.get(
            "/api/protected", headers={"X-API-Key": "k123"}
        ).status_code == 200

    def test_password_change_invalidates_sessions(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        token = _mint(client)
        _set_auth(monkeypatch, password="new-password-9")
        assert client.get(
            "/api/protected", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401

    def test_username_change_invalidates_sessions(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22", username="aaron")
        client = _make_app()
        token = _mint(client, user="aaron")
        _set_auth(monkeypatch, password="hunter22", username="renamed")
        assert client.get(
            "/api/protected", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401

    def test_restart_invalidates_sessions(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        client = _make_app()
        token = _mint(client)
        fresh_store.clear()  # what a process restart does implicitly
        assert client.get(
            "/api/protected", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401


# ---------------------------------------------------------------------------
# WebSocket handshake
# ---------------------------------------------------------------------------

class TestWsAuth:
    def _mint_direct(self) -> str:
        token, _ = st_mod.store.issue(auth_mod.credential_fingerprint())
        return token

    def test_bearer_subprotocol_accepted(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        token = self._mint_direct()
        headers = {"sec-websocket-protocol": f"auth.bearer.{token}"}
        assert check_ws_auth({}, headers) is True

    def test_bearer_subprotocol_wrong_token_rejected(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        headers = {"sec-websocket-protocol": "auth.bearer.forged"}
        assert check_ws_auth({}, headers) is False

    def test_bearer_prefix_never_falls_back_to_password_check(self, monkeypatch, fresh_store):
        # `auth.bearer.<password>` must NOT authenticate as the password —
        # the bearer namespace is tokens only.
        _set_auth(monkeypatch, password="hunter22")
        headers = {"sec-websocket-protocol": "auth.bearer.hunter22"}
        assert check_ws_auth({}, headers) is False

    def test_bearer_authorization_header_accepted(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        token = self._mint_direct()
        headers = {"authorization": f"Bearer {token}"}
        assert check_ws_auth({}, headers) is True

    def test_password_subprotocol_still_accepted(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        headers = {"sec-websocket-protocol": "auth.hunter22"}
        assert check_ws_auth({}, headers) is True

    def test_password_change_kills_ws_token(self, monkeypatch, fresh_store):
        _set_auth(monkeypatch, password="hunter22")
        token = self._mint_direct()
        _set_auth(monkeypatch, password="rotated-pw-1")
        headers = {"sec-websocket-protocol": f"auth.bearer.{token}"}
        assert check_ws_auth({}, headers) is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_mint_endpoint_is_strict_tier():
    assert _classify("POST", "/api/auth/session") == "strict"
    # Logout is cheap and self-scoped; standard tier is fine.
    assert _classify("DELETE", "/api/auth/session") == "standard"
