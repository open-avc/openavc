"""Tests for REST API endpoints."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openavc.core.engine import Engine
from openavc.main import app
from openavc.api import rest, ws
from tests.simulators.acme_display_simulator import AcmeDisplaySimulator


# Test project with a single device pointing at the simulator.
# Never use the live projects/default/project.avc — it changes constantly.
TEST_PROJECT = {
    "project": {"id": "api_test", "name": "API Test Room"},
    "devices": [
        {
            "id": "projector1",
            "driver": "acme_display",
            "name": "Test Display",
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
    sim = AcmeDisplaySimulator(port=0, warmup_time=0.3, cooldown_time=0.2)
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

    from openavc.core.project_loader import load_project
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


async def test_status_carries_a_stable_instance_id(running_app):
    """The panel app pins a server's cert under this id, so it has to hold still.

    get_or_create_instance_id() silently falls back to a fresh UUID when it
    cannot write .instance_id, so an uncached read would hand out a different
    id on every request and a panel would accumulate a pin per poll.
    """
    first = running_app.get("/api/status").json()["instance_id"]
    second = running_app.get("/api/status").json()["instance_id"]

    assert first, "instance_id must not be empty"
    assert first == second


async def test_status_instance_id_reaches_an_anonymous_caller(running_app):
    """It rides in the non-sensitive subset, not with the host identifiers.

    A panel on a claimed instance is an anonymous caller. If this field were
    gated the way hostname/local_ip are, the QR and manual-entry pairing paths
    would never see it and cert pins would stay keyed to host:port -- orphaned
    by the next DHCP lease change.
    """
    engine = rest._engine

    anonymous = engine.get_status(include_sensitive=False)
    authenticated = engine.get_status(include_sensitive=True)

    assert anonymous["instance_id"] == authenticated["instance_id"]
    # The gated fields really are gated, so the assertion above means something.
    assert "hostname" not in anonymous
    assert "hostname" in authenticated


async def test_status_instance_id_is_what_mdns_advertises(running_app):
    """One id, one source. A panel must get the same value by QR as by mDNS."""
    from openavc.core.isc import get_or_create_instance_id

    engine = rest._engine
    served = running_app.get("/api/status").json()["instance_id"]

    assert served == get_or_create_instance_id(engine.project_path)


async def test_get_state(running_app):
    resp = running_app.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "var.room_active" in data["state"]


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
    devices = resp.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["id"] == "projector1"


async def test_get_device(running_app):
    resp = running_app.get("/api/devices/projector1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["driver"] == "acme_display"
    assert "power_on" in data["commands"]


async def test_get_device_not_found(running_app):
    resp = running_app.get("/api/devices/nonexistent")
    assert resp.status_code == 404


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
    assert resp.json()["status"] == "completed"
