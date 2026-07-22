"""The root landing hub (GET /).

Hitting the bare host:port used to return FastAPI's default 404 JSON, which
reads like a broken install. The root now serves a small navigation hub
(links to /panel and /programmer) on a general/dev instance, and redirects
straight to /panel on a panel-only deployment (kiosk / appliance) where an
end user at a wall panel shouldn't be offered the Programmer.
"""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.core.engine import Engine
from server.core.project_loader import load_project
from server.main import app
from server.api import rest
from server.api.routes import root as root_mod
from server.updater.platform import DeploymentType


EMPTY_PROJECT = {
    "project": {"id": "root_route_test", "name": "Root Route Test"},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {"pages": [{"id": "main", "name": "Main", "elements": []}]},
}


class _CfgStub:
    def __init__(self, kiosk_enabled: bool):
        self._kiosk = kiosk_enabled

    def get(self, section, key, default=None):
        if section == "kiosk" and key == "enabled":
            return self._kiosk
        return default


@pytest.fixture
def engine_set():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(EMPTY_PROJECT, f)
        tmp_path = f.name
    engine = Engine(tmp_path)
    engine.project = load_project(tmp_path)
    engine._running = True
    rest.set_engine(engine)
    yield engine
    rest.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


@pytest.fixture
def client():
    return TestClient(app)


def _server_deployment(monkeypatch):
    """Configure a general (non-panel-only) deployment: kiosk off, not an appliance."""
    monkeypatch.setattr(root_mod, "get_system_config", lambda: _CfgStub(False))
    monkeypatch.setattr(root_mod, "detect_deployment_type", lambda: DeploymentType.GIT_DEV)


# --- General / dev deployment: the hub ---


def test_root_serves_hub(engine_set, client, monkeypatch):
    _server_deployment(monkeypatch)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # Navigation only: links to both entry points, no native auth prompt.
    assert 'href="/panel"' in resp.text
    assert 'href="/programmer"' in resp.text
    assert "www-authenticate" not in {k.lower() for k in resp.headers}


def test_root_hub_needs_no_auth_and_shows_project(engine_set, client, monkeypatch):
    _server_deployment(monkeypatch)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert "Root Route Test" in resp.text  # instance (project) name is disclosed


def test_root_hub_renders_without_engine(client, monkeypatch):
    """The hub must never 500 — if the engine is unavailable it still renders."""
    _server_deployment(monkeypatch)
    rest.set_engine(None)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert "OpenAVC" in resp.text


# --- Panel-only deployments: redirect to /panel ---


def test_root_redirects_to_panel_on_kiosk(engine_set, client, monkeypatch):
    monkeypatch.setattr(root_mod, "get_system_config", lambda: _CfgStub(True))
    monkeypatch.setattr(root_mod, "detect_deployment_type", lambda: DeploymentType.LINUX_PACKAGE)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/panel"


def test_root_redirects_to_panel_on_appliance(engine_set, client, monkeypatch):
    monkeypatch.setattr(root_mod, "get_system_config", lambda: _CfgStub(False))
    monkeypatch.setattr(root_mod, "detect_deployment_type", lambda: DeploymentType.ANDROID_APPLIANCE)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/panel"
