"""Tests for the secure-by-default auth posture (C11/C6).

Covers:
- anonymous_access_allowed() resolution (explicit flag + "auto" dev detection)
- auth_state() tri-state (ok / setup / required)
- programmer_auth_satisfied honoring the no-credential posture
- claim_instance() first-run claim (persist, reject re-claim, reject weak)
- require_claimed_auth on code endpoints (always needs a credential)
- /api/auth/required tri-state + /api/auth/setup claim endpoint
"""

import base64

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from server.api import auth
from server.system_config import get_system_config


@pytest.fixture(autouse=True)
def _isolate_auth():
    """Snapshot/restore the auth config section so tests don't leak state, and
    reset the cached deployment detection."""
    cfg = get_system_config()
    saved = cfg.section("auth")
    auth._deployment_is_dev.cache_clear()
    yield
    cfg._data["auth"] = dict(saved)
    auth._deployment_is_dev.cache_clear()
    cfg.save()


def _set_auth(**kwargs):
    cfg = get_system_config()
    for k, v in kwargs.items():
        cfg.set("auth", k, v)


# --- anonymous_access_allowed ------------------------------------------------


def test_explicit_true_allows_anonymous():
    _set_auth(allow_anonymous=True, programmer_password="", api_key="")
    assert auth.anonymous_access_allowed() is True


def test_explicit_false_blocks_anonymous():
    _set_auth(allow_anonymous=False)
    assert auth.anonymous_access_allowed() is False


def test_string_flags_parsed():
    _set_auth(allow_anonymous="false")
    assert auth.anonymous_access_allowed() is False
    _set_auth(allow_anonymous="true")
    assert auth.anonymous_access_allowed() is True


def test_auto_defers_to_deployment(monkeypatch):
    _set_auth(allow_anonymous="auto")
    monkeypatch.setattr(auth, "_deployment_is_dev", lambda: True)
    assert auth.anonymous_access_allowed() is True
    monkeypatch.setattr(auth, "_deployment_is_dev", lambda: False)
    assert auth.anonymous_access_allowed() is False


# --- auth_state --------------------------------------------------------------


def test_state_required_when_claimed():
    _set_auth(programmer_password="hunter2hunter2", allow_anonymous=False)
    assert auth.is_claimed() is True
    assert auth.auth_state() == "required"


def test_state_setup_when_shipped_unclaimed():
    _set_auth(programmer_password="", api_key="", allow_anonymous=False)
    assert auth.auth_state() == "setup"


def test_state_ok_when_dev_unclaimed():
    _set_auth(programmer_password="", api_key="", allow_anonymous=True)
    assert auth.auth_state() == "ok"


# --- programmer_auth_satisfied honors posture --------------------------------


def test_no_credential_shipped_is_not_satisfied():
    """A shipped, unclaimed instance must NOT treat 'no password' as open."""
    _set_auth(programmer_password="", api_key="", allow_anonymous=False)
    assert auth.programmer_auth_satisfied(None, None) is False


def test_no_credential_dev_is_open():
    _set_auth(programmer_password="", api_key="", allow_anonymous=True)
    assert auth.programmer_auth_satisfied(None, None) is True


# --- claim_instance ----------------------------------------------------------


def test_claim_sets_and_persists_password():
    _set_auth(programmer_password="", api_key="", allow_anonymous=False)
    auth.claim_instance("strongpass123")
    cfg = get_system_config()
    assert cfg.get("auth", "programmer_password") == "strongpass123"
    assert auth.is_claimed() is True


def test_claim_rejects_when_already_claimed():
    _set_auth(programmer_password="existingpass1")
    with pytest.raises(ValueError, match="already_claimed"):
        auth.claim_instance("anotherpass123")


def test_claim_rejects_weak_password():
    _set_auth(programmer_password="", api_key="")
    with pytest.raises(ValueError, match="weak_password"):
        auth.claim_instance("short")


# --- require_claimed_auth (code endpoints) -----------------------------------


def _claimed_auth_app() -> FastAPI:
    app = FastAPI()

    @app.post("/code", dependencies=[Depends(auth.require_claimed_auth)])
    async def code():
        return {"ok": True}

    return app


def _basic_header(user: str, pw: str) -> dict:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_code_endpoint_403_when_unclaimed_even_if_anonymous():
    """C6: code endpoints require a credential even on an open dev instance."""
    _set_auth(programmer_password="", api_key="", allow_anonymous=True)
    client = TestClient(_claimed_auth_app())
    r = client.post("/code")
    assert r.status_code == 403


def test_code_endpoint_401_when_claimed_without_creds():
    _set_auth(programmer_password="secretpass123", allow_anonymous=False)
    client = TestClient(_claimed_auth_app())
    r = client.post("/code")
    assert r.status_code == 401


def test_code_endpoint_200_with_valid_creds():
    _set_auth(programmer_password="secretpass123", programmer_username="", allow_anonymous=False)
    client = TestClient(_claimed_auth_app())
    r = client.post("/code", headers=_basic_header("admin", "secretpass123"))
    assert r.status_code == 200


# --- /api/auth/required + /api/auth/setup ------------------------------------


def _auth_api_app() -> FastAPI:
    from server.api.routes import system as system_routes

    app = FastAPI()
    app.include_router(system_routes.open_router, prefix="/api")
    return app


def test_auth_required_reports_setup_state():
    _set_auth(programmer_password="", api_key="", allow_anonymous=False)
    client = TestClient(_auth_api_app())
    body = client.get("/api/auth/required").json()
    assert body == {"required": False, "state": "setup"}


def test_auth_setup_claims_then_requires_login():
    _set_auth(programmer_password="", api_key="", allow_anonymous=False)
    client = TestClient(_auth_api_app())

    # Claim succeeds while unclaimed.
    r = client.post("/api/auth/setup", json={"password": "freshadmin123"})
    assert r.status_code == 200
    assert r.json()["state"] == "required"

    # Now the SPA is told to show login.
    assert client.get("/api/auth/required").json()["state"] == "required"

    # A second claim is refused.
    r2 = client.post("/api/auth/setup", json={"password": "anotheradmin1"})
    assert r2.status_code == 409


def test_auth_setup_rejects_weak_password():
    _set_auth(programmer_password="", api_key="", allow_anonymous=False)
    client = TestClient(_auth_api_app())
    r = client.post("/api/auth/setup", json={"password": "short"})
    assert r.status_code == 400


def test_auth_setup_stores_and_enforces_username():
    """First-run setup now sends a username (prefilled "admin"). It is persisted
    and the credential check enforces it, so the field a user sees at login is
    never a phantom."""
    _set_auth(
        programmer_password="", api_key="", programmer_username="",
        allow_anonymous=False,
    )
    client = TestClient(_auth_api_app())

    r = client.post(
        "/api/auth/setup",
        json={"username": "aaron", "password": "freshadmin123"},
    )
    assert r.status_code == 200

    cfg = get_system_config()
    assert cfg.get("auth", "programmer_username") == "aaron"
    assert cfg.get("auth", "programmer_password") == "freshadmin123"

    # The stored username is now enforced: right pair passes, wrong user fails.
    assert auth._check_credentials("aaron", "freshadmin123") is True
    assert auth._check_credentials("admin", "freshadmin123") is False
