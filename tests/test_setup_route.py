"""The device setup screen (/setup) and its status endpoint.

Appliance deployments (Pi kiosk, dedicated panels) show /setup on their own
display until the device is programmed. The page must render without auth on
a claimed instance (it is the device's own screen), while the network block
of /api/setup/status — IP, hostname, access URLs — follows the same
disclosure rule as /api/status: loopback and authenticated callers get it,
anonymous remote callers on a claimed instance do not.
"""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import server.api.auth as auth_mod
from server.core.engine import Engine
from server.core.project_loader import load_project
from server.main import app
from server.api import rest


EMPTY_PROJECT = {
    "project": {"id": "setup_route_test", "name": "Setup Route Test"},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {"pages": [{"id": "main", "name": "Main", "elements": []}]},
}

_PASSWORD = "setup-secret-123"


def _make_engine(project: dict) -> tuple[Engine, str]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(project, f)
        tmp_path = f.name
    engine = Engine(tmp_path)
    engine.project = load_project(tmp_path)
    engine._running = True
    return engine, tmp_path


@pytest.fixture
async def claimed(monkeypatch):
    """Real app + engine with a password configured (the instance is claimed)."""
    engine, tmp_path = _make_engine(EMPTY_PROJECT)
    rest.set_engine(engine)

    monkeypatch.setattr(auth_mod, "_get_password", lambda: _PASSWORD)
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")

    yield engine

    rest.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


@pytest.fixture
def remote_client():
    """Client whose requests arrive from a non-loopback address."""
    return TestClient(app)  # starlette's default client host is "testclient"


async def _loopback_get(path: str) -> dict:
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.get(path)
        assert resp.status_code == 200
        return resp.json()


# --- The page itself ---


async def test_setup_page_is_open_without_auth(claimed, remote_client):
    """GET /setup renders for anyone — it is the device's own screen."""
    resp = remote_client.get("/setup")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "www-authenticate" not in {k.lower() for k in resp.headers}
    assert "/api/setup/status" in resp.text  # the page polls the status endpoint


# --- Status endpoint: disclosure rules ---


async def test_status_open_but_redacted_for_anonymous_remote(claimed, remote_client):
    """A remote caller without credentials gets state but no network block."""
    resp = remote_client.get("/api/setup/status")
    assert resp.status_code == 200
    assert "www-authenticate" not in {k.lower() for k in resp.headers}
    body = resp.json()
    assert body["claimed"] is True
    assert body["state"] == "required"
    assert body["network"] is None


async def test_status_full_for_loopback(claimed):
    """The device's own kiosk browser (loopback) sees its network identity."""
    body = await _loopback_get("/api/setup/status")
    assert body["network"] is not None
    net = body["network"]
    assert net["port"] > 0
    assert net["protocol"] in ("http", "https")
    # Online or not, the URL fields exist (null only when truly unreachable).
    assert "programmer_url" in net
    assert "panel_url" in net
    assert "ssh" in net


async def test_status_full_for_authenticated_remote(claimed, remote_client):
    resp = remote_client.get("/api/setup/status", auth=("admin", _PASSWORD))
    assert resp.status_code == 200
    assert resp.json()["network"] is not None


# --- Status endpoint: content signals ---


async def test_empty_project_has_no_panel_content(claimed, remote_client):
    body = remote_client.get("/api/setup/status").json()
    assert body["panel_has_content"] is False
    assert body["project_name"] == "Setup Route Test"


async def test_page_element_counts_as_panel_content(claimed, remote_client):
    project = dict(EMPTY_PROJECT)
    project["ui"] = {
        "pages": [
            {
                "id": "main",
                "name": "Main",
                "elements": [{"id": "b1", "type": "button", "label": "On"}],
            }
        ]
    }
    engine, tmp_path = _make_engine(project)
    rest.set_engine(engine)
    try:
        body = remote_client.get("/api/setup/status").json()
        assert body["panel_has_content"] is True
    finally:
        rest.set_engine(claimed)
        Path(tmp_path).unlink(missing_ok=True)


async def test_master_element_counts_as_panel_content(claimed, remote_client):
    project = dict(EMPTY_PROJECT)
    project["ui"] = {
        "pages": [{"id": "main", "name": "Main", "elements": []}],
        "master_elements": [{"id": "m1", "type": "clock"}],
    }
    engine, tmp_path = _make_engine(project)
    rest.set_engine(engine)
    try:
        body = remote_client.get("/api/setup/status").json()
        assert body["panel_has_content"] is True
    finally:
        rest.set_engine(claimed)
        Path(tmp_path).unlink(missing_ok=True)


# --- Unclaimed instance ---


async def test_unclaimed_reports_setup_state(monkeypatch):
    """A shipped, unclaimed box tells the setup screen to show claim steps."""
    engine, tmp_path = _make_engine(EMPTY_PROJECT)
    rest.set_engine(engine)

    monkeypatch.setattr(auth_mod, "_get_password", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")
    monkeypatch.setattr(auth_mod, "anonymous_access_allowed", lambda: False)

    try:
        body = await _loopback_get("/api/setup/status")
        assert body["state"] == "setup"
        assert body["claimed"] is False
        # The device's own screen still shows network info pre-claim — that
        # is the whole point of the setup screen.
        assert body["network"] is not None
    finally:
        rest.set_engine(None)
        Path(tmp_path).unlink(missing_ok=True)
