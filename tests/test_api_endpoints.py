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
    engine._project_revision = 0

    # Routes mutate a model_copy of the project and hand it to
    # apply_project. Mirror that contract: the copy carries deep-copied
    # section values, and apply_project swaps it in and bumps the revision.
    import copy as _copylib

    def _wire_project_copy(p):
        def _copy(*, deep=False, update=None):
            cp = MagicMock()
            for attr in ("devices", "variables", "macros",
                         "scripts", "connections", "plugins"):
                setattr(cp, attr, _copylib.deepcopy(getattr(p, attr)))
            cp.ui = p.ui
            cp.project = p.project
            _wire_project_copy(cp)
            return cp
        p.model_copy = MagicMock(side_effect=_copy)

    _wire_project_copy(engine.project)

    async def _fake_apply(new_project, **kwargs):
        engine.project = new_project
        engine._project_revision += 1
        return engine._project_revision

    engine.apply_project = AsyncMock(side_effect=_fake_apply)

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


def test_device_update_preserves_pending_settings(client, tmp_path):
    """Regression for A4: PUT /devices/{id} must preserve pending_settings.

    Previously the route built a fresh DeviceConfig(...) without copying
    `pending_settings` from the existing record, so renaming or re-enabling
    a device silently dropped any queued settings — the next reconnect's
    _apply_pending_settings() would then have nothing to apply.
    """
    from unittest.mock import patch
    from server.core.project_loader import DeviceConfig

    c, engine = client
    existing = DeviceConfig(
        id="dev1",
        driver="generic_tcp",
        name="Original Name",
        config={"host": "10.0.0.1", "port": 23},
        enabled=True,
        pending_settings={"brightness": 75, "input": "hdmi1"},
    )
    engine.project.devices = [existing]
    engine.project.connections = {}
    engine.project_path = str(tmp_path / "test.avc")
    engine.resolved_device_config = MagicMock(
        return_value={"id": "dev1", "config": {"host": "10.0.0.1"}}
    )
    engine.devices.update_device = AsyncMock()

    with patch("server.core.project_loader.save_project"):
        resp = c.put("/api/devices/dev1", json={"name": "Renamed"})

    assert resp.status_code == 200
    updated = engine.project.devices[0]
    assert updated.name == "Renamed"
    assert updated.pending_settings == {"brightness": 75, "input": "hdmi1"}, (
        "pending_settings was dropped on edit — A4 regressed"
    )


def test_device_update_preserves_forward_compat_extra_fields(client, tmp_path):
    """M-160: PUT /devices/{id} must preserve forward-compat top-level extra
    fields. DeviceConfig is extra='allow', so an unknown top-level field a
    newer platform version wrote must round-trip through a routine edit, not be
    silently dropped by rebuilding a fresh DeviceConfig from known fields only.
    """
    from unittest.mock import patch
    from server.core.project_loader import DeviceConfig

    c, engine = client
    # extra='allow' parks an unknown top-level field in __pydantic_extra__.
    existing = DeviceConfig(
        id="dev1",
        driver="generic_tcp",
        name="Original",
        config={"host": "10.0.0.1"},
        enabled=True,
        future_field="keep-me",
    )
    assert existing.model_dump().get("future_field") == "keep-me"
    engine.project.devices = [existing]
    engine.project.connections = {}
    engine.project_path = str(tmp_path / "test.avc")
    engine.resolved_device_config = MagicMock(
        return_value={"id": "dev1", "config": {"host": "10.0.0.1"}}
    )
    engine.devices.update_device = AsyncMock()

    with patch("server.core.project_loader.save_project"):
        resp = c.put("/api/devices/dev1", json={"name": "Renamed"})

    assert resp.status_code == 200
    updated = engine.project.devices[0]
    assert updated.name == "Renamed"
    assert updated.model_dump().get("future_field") == "keep-me", (
        "forward-compat top-level field was dropped on edit — M-160 regressed"
    )


def test_serial_connection_test_runs_off_event_loop(client, monkeypatch):
    """H-109: the blocking pyserial open must be dispatched through
    asyncio.to_thread so a stuck/locked serial port can't freeze the event
    loop (and with it every other request, WS push, and device poll).
    """
    import asyncio
    from types import SimpleNamespace

    c, engine = client
    engine.project.devices = [
        SimpleNamespace(
            id="serdev",
            config={"transport": "serial", "port": "COM_TEST", "baudrate": 9600},
        )
    ]
    engine.project.connections = {}

    class _FakeSerial:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("serial.Serial", _FakeSerial)

    used: dict[str, bool] = {}
    real_to_thread = asyncio.to_thread

    async def _spy(fn, *a, **k):
        used["called"] = True
        return await real_to_thread(fn, *a, **k)

    monkeypatch.setattr("asyncio.to_thread", _spy)

    resp = c.post("/api/devices/serdev/test")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert used.get("called"), (
        "serial open did not go through asyncio.to_thread — H-109 regressed"
    )


def test_bulk_connections_drops_unknown_device_ids(client, tmp_path):
    """L-097: PUT /connections keeps entries for existing devices and reports
    unknown ids in `skipped` instead of persisting orphaned connection rows.
    """
    from unittest.mock import patch
    from types import SimpleNamespace

    c, engine = client
    engine.project.devices = [SimpleNamespace(id="real1"), SimpleNamespace(id="real2")]
    engine.project.connections = {}
    engine.project_path = str(tmp_path / "test.avc")
    engine.reload_project = AsyncMock()

    table = {
        "real1": {"host": "10.0.0.1"},
        "real2": {"host": "10.0.0.2"},
        "ghost": {"host": "10.0.0.9"},
    }
    with patch("server.core.project_loader.save_project"):
        resp = c.put("/api/connections", json=table)

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["skipped"] == ["ghost"]
    assert set(engine.project.connections.keys()) == {"real1", "real2"}


def test_import_connections_drops_unknown_device_ids(client, tmp_path):
    """L-097: POST /connections/import strips `_` metadata, keeps known device
    ids, and reports unknown ids in `skipped`.
    """
    from unittest.mock import patch
    from types import SimpleNamespace

    c, engine = client
    engine.project.devices = [SimpleNamespace(id="real1")]
    engine.project.connections = {}
    engine.project_path = str(tmp_path / "test.avc")
    engine.reload_project = AsyncMock()

    table = {
        "real1": {"host": "10.0.0.1", "_device_name": "Projector"},
        "stale": {"host": "10.0.0.9"},
    }
    with patch("server.core.project_loader.save_project"):
        resp = c.post("/api/connections/import", json=table)

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["skipped"] == ["stale"]
    assert engine.project.connections == {"real1": {"host": "10.0.0.1"}}


# ── Macro endpoints ──


def test_execute_macro_unknown_returns_404(client):
    """Macro execute returns 404 for unknown macros."""
    c, engine = client
    resp = c.post("/api/macros/nonexistent/execute")
    assert resp.status_code == 404


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


def test_execute_macro_is_rate_limited(client):
    """Rapid re-fire of the same macro is debounced, matching the guard on
    /triggers/{id}/test — the callers are the IDE's manual run buttons."""
    c, engine = client
    engine.macros.load_macros([{
        "id": "rl_macro",
        "name": "RL",
        "steps": [{"action": "state.set", "key": "var.flag", "value": True}],
    }])
    first = c.post("/api/macros/rl_macro/execute")
    assert first.status_code == 200
    second = c.post("/api/macros/rl_macro/execute")
    assert second.status_code == 429


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


def test_logs_recent_category_scans_whole_buffer(client):
    """category must filter the whole buffer before the count slice — a busy
    log otherwise returns zero matches once newer entries push the requested
    category out of the newest `count` window."""
    from server.utils.log_buffer import LogEntry, get_log_buffer

    c, engine = client
    buf = get_log_buffer()
    buf._entries.clear()
    try:
        for i in range(5):
            buf.append(LogEntry(
                timestamp=float(i), level="INFO", source="test",
                category="device", message=f"dev {i}",
            ))
        for i in range(200):
            buf.append(LogEntry(
                timestamp=float(100 + i), level="INFO", source="test",
                category="system", message=f"sys {i}",
            ))
        resp = c.get("/api/logs/recent?count=100&category=device")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5
        assert all(e["category"] == "device" for e in data)
    finally:
        buf._entries.clear()


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


# ── System restart ──


def test_system_restart_default_graceful(client):
    """POST /api/system/restart with no body defaults to graceful mode."""
    c, engine = client

    received: list[dict] = []

    async def _capture(_event: str, data: dict) -> None:
        received.append(data)

    engine.events.on("system.restart_requested", _capture)
    resp = c.post("/api/system/restart")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "restarting"
    assert body["mode"] == "graceful"
    assert body["delay_seconds"] == 2
    assert received == [{"mode": "graceful"}]


def test_system_restart_hard_mode(client):
    """Explicit hard mode is honored."""
    c, engine = client

    received: list[dict] = []

    async def _capture(_event: str, data: dict) -> None:
        received.append(data)

    engine.events.on("system.restart_requested", _capture)
    resp = c.post("/api/system/restart", json={"mode": "hard"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "hard"
    assert body["delay_seconds"] == 0
    assert received == [{"mode": "hard"}]


def test_system_restart_unknown_mode_falls_back_to_graceful(client):
    """Garbage mode value is ignored, defaults to graceful."""
    c, engine = client
    resp = c.post("/api/system/restart", json={"mode": "wild-west"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "graceful"


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
