"""Regression tests for the system-routes hardening.

These were one file's findings when the routes all lived in
``openavc/api/routes/system.py``; they now span the domain modules that file
was split into (``cloud``, ``tls``, ``simulation``, and ``system`` itself).
They are kept together because they are one campaign's regressions, and each
still drives its endpoint through the real app.

Covers the audit findings closed in the bug-fix campaign:
  - H-027  cloud_pair() partial/renamed cloud body -> clean 502 (not KeyError 500)
  - H-028  SSRF guard on cloud_api_url (link-local/loopback/bad-scheme)
  - M-048  save_cloud_config OSError -> clear 500, no silent success
  - M-049  agent-start failure surfaced as agent_started=false + warning
  - M-047/L-032  update channel mirrored to state on PATCH
  - M-052  log level applied live on PATCH
  - L-033  non-list device_ids -> 400 (not opaque 500)
  - L-034  malformed JSON body to simulation start -> 400 (not simulate-all)
  - L-035  tls-status reflects live config after PATCH
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import openavc.api.routes.cloud as cloud_routes
from openavc.api import rest, ws
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.main import app
from openavc.system_config import get_system_config, reset_system_config


# ── Mock engine + clients ──────────────────────────────────────────────────


def _mock_engine():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)

    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine.cloud_agent = None  # default: no existing agent
    engine.simulation = MagicMock()
    engine.simulation.start = AsyncMock(return_value={"status": "started"})
    engine.get_status.return_value = {"version": "0.0.0-test", "uptime_seconds": 1}
    engine.project_path = "/tmp/test_project.avc"
    engine.project_dir = Path("/tmp")
    return engine


@pytest.fixture
def client():
    engine = _mock_engine()
    rest.set_engine(engine)
    ws.set_engine(engine)
    yield TestClient(app), engine
    rest.set_engine(None)
    ws.set_engine(None)


@pytest.fixture
def isolated_config(tmp_path):
    """Point the SystemConfig singleton at a throwaway dir so PATCH saves don't
    touch the real system.json."""
    reset_system_config()
    cfg = get_system_config()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    cfg.load()
    yield cfg
    reset_system_config()


# ── H-028: SSRF guard on cloud_api_url ──────────────────────────────────────


def _validate(url):
    return asyncio.run(cloud_routes._validate_cloud_api_url(url))


def test_ssrf_blocks_link_local_metadata():
    with pytest.raises(HTTPException) as e:
        _validate("http://169.254.169.254/latest/meta-data/")
    assert e.value.status_code == 400
    assert "disallowed address" in e.value.detail


def test_ssrf_blocks_non_http_scheme():
    with pytest.raises(HTTPException) as e:
        _validate("ftp://cloud.example.com/pair")
    assert e.value.status_code == 400
    assert "http" in e.value.detail


def test_ssrf_blocks_loopback_on_shipped_deployment(monkeypatch):
    monkeypatch.setattr("openavc.api.auth._deployment_is_dev", lambda: False)
    with pytest.raises(HTTPException) as e:
        _validate("http://127.0.0.1:8080/api")
    assert e.value.status_code == 400


def test_ssrf_allows_loopback_in_dev(monkeypatch):
    monkeypatch.setattr("openavc.api.auth._deployment_is_dev", lambda: True)
    assert _validate("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"


def test_ssrf_allows_ipv6_loopback_in_dev(monkeypatch):
    """`::1` is loopback and also reserved (::/8) -- loopback has to win."""
    monkeypatch.setattr("openavc.api.auth._deployment_is_dev", lambda: True)
    assert _validate("http://[::1]:8000/") == "http://[::1]:8000"


def test_ssrf_allows_localhost_in_dev(monkeypatch):
    """The name a developer actually types, which resolves to ::1 first."""
    monkeypatch.setattr("openavc.api.auth._deployment_is_dev", lambda: True)
    assert _validate("http://localhost:8000/") == "http://localhost:8000"


def test_ssrf_blocks_ipv6_loopback_on_shipped_deployment(monkeypatch):
    monkeypatch.setattr("openavc.api.auth._deployment_is_dev", lambda: False)
    with pytest.raises(HTTPException) as e:
        _validate("http://[::1]:8000/")
    assert e.value.status_code == 400
    assert "points back at this machine" in e.value.detail


def test_ssrf_allows_public_and_private_hosts():
    # Public IP literal (no DNS) and an RFC1918 self-hosted-cloud address both pass.
    assert _validate("https://8.8.8.8:443/x/") == "https://8.8.8.8:443/x"
    assert _validate("https://10.20.30.40:8000") == "https://10.20.30.40:8000"


# ── cloud_pair: httpx fake + shared setup ───────────────────────────────────


class _FakeResponse:
    _RAISE = object()

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is _FakeResponse._RAISE:
            raise ValueError("not json")
        return self._json_data


def _fake_httpx(monkeypatch, response):
    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return response

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


def _pair_env(monkeypatch, *, save=None):
    """Bypass the (network-touching) URL validator and stub config persistence."""
    async def _passthrough(url):
        return url.rstrip("/")

    monkeypatch.setattr(cloud_routes, "_validate_cloud_api_url", _passthrough)
    monkeypatch.setattr(
        "openavc.cloud.config.save_cloud_config", save or (lambda cfg: None)
    )
    # Don't leak runtime config mutations across tests.
    import openavc.config as cfg
    for name in ("CLOUD_ENABLED", "CLOUD_ENDPOINT", "CLOUD_SYSTEM_KEY", "CLOUD_SYSTEM_ID"):
        monkeypatch.setattr(cfg, name, getattr(cfg, name), raising=False)


# ── H-027: partial/renamed cloud body -> 502 ────────────────────────────────


def test_cloud_pair_missing_field_is_502(client, monkeypatch):
    c, engine = client
    _pair_env(monkeypatch)
    _fake_httpx(monkeypatch, _FakeResponse(200, {"endpoint": "wss://x", "system_id": "s1"}))
    resp = c.post("/api/cloud/pair", json={"token": "t", "cloud_api_url": "https://cloud.test"})
    assert resp.status_code == 502
    assert "system_key" in resp.json()["detail"]


def test_cloud_pair_non_dict_body_is_502(client, monkeypatch):
    c, engine = client
    _pair_env(monkeypatch)
    _fake_httpx(monkeypatch, _FakeResponse(200, ["unexpected"]))
    resp = c.post("/api/cloud/pair", json={"token": "t", "cloud_api_url": "https://cloud.test"})
    assert resp.status_code == 502


def test_cloud_pair_non_json_body_is_502(client, monkeypatch):
    c, engine = client
    _pair_env(monkeypatch)
    _fake_httpx(monkeypatch, _FakeResponse(200, _FakeResponse._RAISE))
    resp = c.post("/api/cloud/pair", json={"token": "t", "cloud_api_url": "https://cloud.test"})
    assert resp.status_code == 502


# ── M-048: save failure -> clear 500, agent not started ─────────────────────


def test_cloud_pair_save_oserror_is_clear_500(client, monkeypatch):
    c, engine = client

    def _boom(cfg):
        raise OSError("disk full")

    _pair_env(monkeypatch, save=_boom)
    _fake_httpx(
        monkeypatch,
        _FakeResponse(200, {"endpoint": "wss://x", "system_key": "k", "system_id": "s1"}),
    )
    started = {"called": False}

    async def _start():
        started["called"] = True

    engine._start_cloud_agent = _start

    resp = c.post("/api/cloud/pair", json={"token": "t", "cloud_api_url": "https://cloud.test"})
    assert resp.status_code == 500
    assert "could not save credentials" in resp.json()["detail"].lower()
    assert started["called"] is False  # never proceeded to start the agent


# ── M-049: agent-start failure surfaced ─────────────────────────────────────


def test_cloud_pair_agent_start_failure_surfaced(client, monkeypatch):
    c, engine = client
    _pair_env(monkeypatch)
    _fake_httpx(
        monkeypatch,
        _FakeResponse(200, {"endpoint": "wss://x", "system_key": "k", "system_id": "s1"}),
    )

    async def _start():  # leaves cloud_agent None (mimics isolated failure)
        engine.cloud_agent = None

    engine._start_cloud_agent = _start

    resp = c.post("/api/cloud/pair", json={"token": "t", "cloud_api_url": "https://cloud.test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "paired"
    assert body["agent_started"] is False
    assert "warning" in body


def test_cloud_pair_agent_start_success(client, monkeypatch):
    c, engine = client
    _pair_env(monkeypatch)
    _fake_httpx(
        monkeypatch,
        _FakeResponse(200, {"endpoint": "wss://x", "system_key": "k", "system_id": "s1"}),
    )

    async def _start():
        engine.cloud_agent = object()

    engine._start_cloud_agent = _start

    resp = c.post("/api/cloud/pair", json={"token": "t", "cloud_api_url": "https://cloud.test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_started"] is True
    assert "warning" not in body


# ── L-033 / L-034: simulation start input validation ────────────────────────


def test_simulation_start_malformed_json_is_400(client):
    c, engine = client
    resp = c.post(
        "/api/simulation/start",
        content="{not valid",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    engine.simulation.start.assert_not_called()


def test_simulation_start_non_list_device_ids_is_400(client):
    c, engine = client
    resp = c.post("/api/simulation/start", json={"device_ids": "dev1"})
    assert resp.status_code == 400
    engine.simulation.start.assert_not_called()


def test_simulation_start_no_body_simulates_all(client):
    c, engine = client
    resp = c.post("/api/simulation/start")
    assert resp.status_code == 200
    engine.simulation.start.assert_awaited_once_with(None)


def test_simulation_start_valid_list_passes_through(client):
    c, engine = client
    resp = c.post("/api/simulation/start", json={"device_ids": ["a", "b"]})
    assert resp.status_code == 200
    engine.simulation.start.assert_awaited_once_with(["a", "b"])


# ── M-047 / L-032 / M-052 / L-035: PATCH config live-apply ──────────────────


def test_patch_update_channel_mirrors_state(client, isolated_config):
    c, engine = client
    resp = c.patch("/api/system/config", json={"updates": {"channel": "beta"}})
    assert resp.status_code == 200
    assert engine.state.get("system.update_channel") == "beta"


def test_patch_log_level_applies_live(client, isolated_config):
    c, engine = client
    from openavc.utils import logger as lg

    lg.get_logger("test")  # ensure the console handler exists
    resp = c.patch("/api/system/config", json={"logging": {"level": "warning"}})
    assert resp.status_code == 200
    assert lg._console_handler is not None
    assert lg._console_handler.level == logging.WARNING


def test_tls_status_reflects_live_config(client, isolated_config):
    c, engine = client
    off_body = c.get("/api/system/tls-status").json()
    assert off_body["enabled"] is False
    # cloud_cert rides along even with TLS off (Settings enrollment callout).
    assert off_body["cloud_cert"]["active"] is False

    resp = c.patch("/api/system/config", json={"tls": {"enabled": True, "auto_generate": True}})
    assert resp.status_code == 200

    body = c.get("/api/system/tls-status").json()
    assert body["enabled"] is True
    assert body["mode"] == "auto"
