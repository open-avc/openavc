"""Tests for REST API endpoints."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.core.device_manager import _DRIVER_REGISTRY
from server.core.engine import Engine
from server.main import app
from server.api import rest, ws
from tests.simulators.pjlink_simulator import PJLinkSimulator

needs_pjlink = pytest.mark.skipif(
    "pjlink_class1" not in _DRIVER_REGISTRY,
    reason="pjlink_class1 driver not installed",
)


# Test project with a single device pointing at the simulator.
# Never use the live projects/default/project.avc — it changes constantly.
TEST_PROJECT = {
    "project": {"id": "api_test", "name": "API Test Room"},
    "devices": [
        {
            "id": "projector1",
            "driver": "pjlink_class1",
            "name": "Test Projector",
            "config": {"host": "127.0.0.1", "port": 14355},
            "enabled": True,
        },
    ],
    "variables": [
        {"id": "room_active", "type": "bool", "default": False},
    ],
    "macros": [
        {
            "id": "system_on",
            "name": "System On",
            "steps": [
                {"action": "state.set", "key": "var.room_active", "value": True},
                {"action": "device.command", "device": "projector1", "command": "power_on"},
            ],
        },
    ],
    "ui": {"pages": []},
}


@pytest.fixture
async def running_app():
    """Start simulator + engine with a known test project, yield TestClient."""
    sim = PJLinkSimulator(port=0, warmup_time=0.3, cooldown_time=0.2)
    await sim.start()

    # Build project with the actual simulator port
    project = json.loads(json.dumps(TEST_PROJECT))
    project["devices"][0]["config"]["port"] = sim.port

    # Write test project to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(project, f)
        tmp_path = f.name

    engine = Engine(tmp_path)

    from server.core.project_loader import load_project
    engine.project = load_project(tmp_path)

    # Manual start sequence
    for var in engine.project.variables:
        engine.state.set(f"var.{var.id}", var.default, source="system")
    macros_data = [m.model_dump() for m in engine.project.macros]
    engine.macros.load_macros(macros_data)
    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))
    engine._running = True

    rest.set_engine(engine)
    ws.set_engine(engine)

    yield TestClient(app)

    await engine.devices.disconnect_all()
    await sim.stop()
    Path(tmp_path).unlink(missing_ok=True)

    # Reset globals
    rest.set_engine(None)
    ws.set_engine(None)


async def test_status(running_app):
    resp = running_app.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_count"] == 1


async def test_get_state(running_app):
    resp = running_app.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "var.room_active" in data


async def test_get_state_value(running_app):
    resp = running_app.get("/api/state/var.room_active")
    assert resp.status_code == 200
    assert resp.json()["key"] == "var.room_active"


async def test_set_state_value(running_app):
    resp = running_app.put(
        "/api/state/var.room_active",
        json={"value": True},
    )
    assert resp.status_code == 200
    assert resp.json()["value"] is True


async def test_list_devices(running_app):
    resp = running_app.get("/api/devices")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 1
    assert devices[0]["id"] == "projector1"


@needs_pjlink
async def test_get_device(running_app):
    resp = running_app.get("/api/devices/projector1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["driver"] == "pjlink_class1"
    assert "power_on" in data["commands"]


async def test_get_device_not_found(running_app):
    resp = running_app.get("/api/devices/nonexistent")
    assert resp.status_code == 404


@needs_pjlink
async def test_send_command(running_app):
    resp = running_app.post(
        "/api/devices/projector1/command",
        json={"command": "power_on"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


async def test_get_project(running_app):
    resp = running_app.get("/api/project")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"]["name"] == "API Test Room"


async def test_execute_macro(running_app):
    resp = running_app.post("/api/macros/system_on/execute")
    assert resp.status_code == 200
    assert resp.json()["status"] == "executed"
