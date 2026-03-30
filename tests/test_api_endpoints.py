"""
Tests for REST API endpoints (server/api/rest.py).

These tests use a lightweight engine mock to test endpoint logic
without needing a running device simulator. They complement the
integration tests in test_api.py.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.api import rest, ws
from server.core.state_store import StateStore
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine


def _make_mock_engine():
    """Create a mock engine with the minimum needed for REST tests."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)

    engine = MagicMock()
    engine.state = state
    engine.events = events
    mock_devices = MagicMock()
    engine.macros = MacroEngine(state, events, mock_devices)
    engine.devices = MagicMock()
    engine.devices.list_devices.return_value = []
    engine.devices.get_device_info.return_value = None
    engine.triggers = MagicMock()
    engine.triggers.list_triggers.return_value = []
    engine.scripts = MagicMock()
    engine.plugin_loader = MagicMock()
    engine.isc = None
    engine._running = True
    engine._ws_clients = []
    engine.get_status.return_value = {
        "version": "0.0.0-test",
        "uptime_seconds": 123,
        "device_count": 0,
        "project": {"id": "test", "name": "Test Room"},
        "devices": {"total": 0, "connected": 0, "error": 0},
        "cloud_connected": False,
    }
    engine.project = MagicMock()
    engine.project.devices = []
    engine.project.variables = []
    engine.project.macros = []
    engine.project.scripts = []
    engine.project.ui = MagicMock()
    engine.project.ui.model_dump.return_value = {"pages": []}
    engine.project.connections = {}
    engine.project.plugins = {}
    engine.project_path = "/tmp/test_project.avc"
    engine.project_dir = Path("/tmp")

    return engine


@pytest.fixture
def client():
    """TestClient with a mock engine wired in."""
    engine = _make_mock_engine()
    rest.set_engine(engine)
    ws.set_engine(engine)
    yield TestClient(app), engine
    rest.set_engine(None)
    ws.set_engine(None)


# ── Status endpoint ──


def test_status_returns_system_info(client):
    c, engine = client
    engine.state.set("system.started", True, source="system")
    resp = c.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "device_count" in data
    assert "uptime_seconds" in data


# ── State endpoints ──


def test_get_state_snapshot(client):
    c, engine = client
    engine.state.set("var.test", 42, source="test")
    resp = c.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["var.test"] == 42


def test_get_state_value(client):
    c, engine = client
    engine.state.set("var.level", 75, source="test")
    resp = c.get("/api/state/var.level")
    assert resp.status_code == 200
    assert resp.json()["value"] == 75


def test_get_state_value_not_found(client):
    c, engine = client
    resp = c.get("/api/state/nonexistent.key")
    assert resp.status_code == 200
    assert resp.json()["value"] is None


def test_set_state_value(client):
    c, engine = client
    resp = c.put("/api/state/var.foo", json={"value": "bar"})
    assert resp.status_code == 200
    assert engine.state.get("var.foo") == "bar"


def test_set_state_accepts_any_json_value(client):
    """REST API accepts any JSON value for state (validation is at WS layer)."""
    c, engine = client
    resp = c.put("/api/state/var.test", json={"value": "hello"})
    assert resp.status_code == 200
    assert engine.state.get("var.test") == "hello"


# ── Device endpoints (mock) ──


def test_list_devices_empty(client):
    c, engine = client
    resp = c.get("/api/devices")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_device_not_found(client):
    c, engine = client
    engine.devices.get_device_info.side_effect = ValueError("not found")
    resp = c.get("/api/devices/nonexistent")
    assert resp.status_code == 404


def test_send_command_device_not_found(client):
    c, engine = client
    engine.devices.send_command = AsyncMock(side_effect=KeyError("not found"))
    resp = c.post("/api/devices/bad_id/command", json={"command": "power_on"})
    assert resp.status_code in (404, 500)


# ── Macro endpoints ──


def test_execute_macro_unknown_still_returns_200(client):
    """Macro execute returns 200 even for unknown macros (logs error internally)."""
    c, engine = client
    resp = c.post("/api/macros/nonexistent/execute")
    assert resp.status_code == 200
    assert resp.json()["status"] == "executed"


def test_execute_macro_success(client):
    c, engine = client
    engine.macros.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [{"action": "state.set", "key": "var.flag", "value": True}],
    }])
    resp = c.post("/api/macros/test_macro/execute")
    assert resp.status_code == 200
    assert resp.json()["status"] == "executed"


# ── Trigger endpoints ──


def test_list_triggers(client):
    c, engine = client
    resp = c.get("/api/triggers")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Project endpoints ──


def test_get_project(client):
    c, engine = client
    engine.project.model_dump.return_value = {
        "project": {"id": "test", "name": "Test Room"},
        "devices": [],
        "variables": [],
        "macros": [],
        "scripts": [],
        "ui": {"pages": []},
        "connections": {},
        "plugins": {},
    }
    resp = c.get("/api/project")
    assert resp.status_code == 200
    assert "project" in resp.json()


# ── Logs endpoint ──


def test_logs_recent(client):
    c, engine = client
    resp = c.get("/api/logs/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


# ── System version ──


def test_system_version(client):
    c, engine = client
    resp = c.get("/api/system/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data


# ── System config ──


def test_system_config(client):
    c, engine = client
    resp = c.get("/api/system/config")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)


# ── ISC endpoints (no ISC configured) ──


def test_isc_status_when_disabled(client):
    c, engine = client
    engine.isc = None
    resp = c.get("/api/isc/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False


# ── Connection endpoints ──


def test_list_connections(client):
    c, engine = client
    resp = c.get("/api/connections")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


# ── Driver list ──


def test_list_drivers(client):
    c, engine = client
    resp = c.get("/api/drivers")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
