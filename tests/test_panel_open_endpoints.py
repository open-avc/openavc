"""The room panel must never trigger authentication.

The panel (/panel) is unauthenticated by design — end users open it on a wall
tablet or phone and never see a login. It is a static page that, on load,
fetches a small set of read-only endpoints (its theme, plugin panel-element
metadata, assets). If any of those sit behind programmer auth, a *claimed*
instance answers a standalone panel with 401 ``WWW-Authenticate: Basic``, and
the browser pops its native HTTP Basic dialog — an unfillable username/password
prompt (setup only ever collected a password, so there is no username to type).

These tests pin the invariant: with a password configured, every endpoint the
panel loads on its render path resolves WITHOUT credentials, while sibling
management endpoints on the same prefix stay protected. Regression guard for the
panel-asks-for-a-password bug.
"""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.api.auth as auth_mod
from server.core.engine import Engine
from server.core.project_loader import load_project
from server.main import app
from server.api import rest, themes as themes_api, plugins as plugins_api


TEST_PROJECT = {
    "project": {"id": "panel_auth_test", "name": "Panel Auth Test Room"},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {"pages": [], "settings": {"theme_id": "dark-default"}},
}

_PASSWORD = "panel-secret-123"


@pytest.fixture
async def claimed_client(monkeypatch):
    """Real app + engine with a password configured (the instance is claimed)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT, f)
        tmp_path = f.name

    engine = Engine(tmp_path)
    engine.project = load_project(tmp_path)
    engine._running = True

    rest.set_engine(engine)
    themes_api.set_engine(engine)
    plugins_api.set_engine(engine)

    # Claim the instance: a password is set, so protected routes require auth.
    monkeypatch.setattr(auth_mod, "_get_password", lambda: _PASSWORD)
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")

    yield TestClient(app)

    rest.set_engine(None)
    themes_api.set_engine(None)
    plugins_api.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


# --- The panel's load-path endpoints are open even on a claimed instance ---


async def test_single_theme_is_open_without_auth(claimed_client):
    """GET /api/themes/{id} — the panel fetches this to apply the project theme."""
    resp = claimed_client.get("/api/themes/dark-default")
    assert resp.status_code == 200, (
        "panel theme fetch must not require auth (would pop the browser's "
        "native Basic dialog on a standalone panel)"
    )
    assert resp.json()["id"] == "dark-default"


async def test_plugin_extensions_is_open_without_auth(claimed_client):
    """GET /api/plugins/extensions — fetched on every panel load before render."""
    resp = claimed_client.get("/api/plugins/extensions")
    assert resp.status_code == 200, (
        "panel plugin-extensions fetch must not require auth"
    )
    # Shape check: always returns the panel_elements bucket (empty here).
    assert "panel_elements" in resp.json()


async def test_no_www_authenticate_on_panel_paths(claimed_client):
    """A panel-path 200 must not carry a Basic challenge that primes the dialog."""
    for path in ("/api/themes/dark-default", "/api/plugins/extensions"):
        resp = claimed_client.get(path)
        assert resp.status_code == 200
        assert "www-authenticate" not in {k.lower() for k in resp.headers}


async def test_ext_token_does_not_401_an_unauthenticated_panel(claimed_client):
    """A standalone panel fetching a plugin ext-token must get 200 (empty token),
    not 401 — a 401 here is the same browser-dialog trap."""
    resp = claimed_client.get("/api/plugins/audio_player/ext-token")
    assert resp.status_code == 200
    body = resp.json()
    # No privileged token for an unauthenticated caller, but the request itself
    # succeeds so the browser never prompts.
    assert body["token"] == ""
    assert body["auth_required"] is True
    assert "www-authenticate" not in {k.lower() for k in resp.headers}


async def test_ext_token_minted_for_authenticated_caller(claimed_client):
    """The Programmer (or a panel embedded in it) still gets a real token."""
    resp = claimed_client.get(
        "/api/plugins/audio_player/ext-token", auth=("admin", _PASSWORD)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]  # non-empty
    assert body["auth_required"] is True


# --- Control: management endpoints on the same prefix stay protected ---


async def test_theme_list_still_requires_auth(claimed_client):
    """The theme *list* is a programmer surface, not a panel one — stays closed."""
    resp = claimed_client.get("/api/themes")
    assert resp.status_code == 401


async def test_theme_mutations_still_require_auth(claimed_client):
    resp = claimed_client.put("/api/themes/dark-default", json={"id": "x"})
    assert resp.status_code == 401


async def test_plugin_management_still_requires_auth(claimed_client):
    """GET /api/plugins (management list) must still demand a credential."""
    resp = claimed_client.get("/api/plugins")
    assert resp.status_code == 401


async def test_protected_route_still_serves_with_auth(claimed_client):
    """Sanity: the protected routes do serve when the password is supplied."""
    auth = ("admin", _PASSWORD)
    assert claimed_client.get("/api/themes", auth=auth).status_code == 200
    assert claimed_client.get("/api/plugins", auth=auth).status_code == 200
