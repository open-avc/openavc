"""Regression tests for BUG-3: every device/connection route that persists
the project must advance the project revision (and broadcast the new one),
or an open IDE's stale full-project PUT silently reverts the change.

Also pins two behaviors the seam routing must not regress:

- a child-entity label PATCH is applied live and must NOT tear down and
  re-add a connected device
- a device rename still hot-swaps the runtime device
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.api import rest, ws
from server.core.device_manager import register_driver, unregister_driver
from server.core.engine import Engine
from server.core.project_loader import (
    ChildEntityConfig,
    DeviceConfig,
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
)
from server.drivers.base import BaseDriver
from server.main import app


class _RevTCPController(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "test_rev_ctrl",
        "name": "Test Revision Controller",
        "transport": "tcp",
        "default_config": {"port": 5000},
        "state_variables": {},
        "commands": {},
        "device_settings": {
            "brightness": {"type": "number", "label": "Brightness"},
        },
        "child_entity_types": {
            "encoder": {
                "label": "Encoder",
                "id_format": {
                    "type": "integer", "min": 1, "max": 99, "pad_width": 3,
                },
                "state_variables": {"name": {"type": "string"}},
            },
        },
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _seed_project() -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="proj1", name="Test Project"),
        devices=[
            DeviceConfig(
                id="ctrl1",
                driver="test_rev_ctrl",
                name="Controller 1",
                config={},
                child_entities={
                    "encoder": {
                        "005": ChildEntityConfig(label="Lobby TX", config={}),
                    },
                },
            ),
        ],
        connections={"ctrl1": {"host": "10.0.0.50", "port": 5001}},
    )


@pytest.fixture
async def rev_engine(tmp_path):
    register_driver(_RevTCPController)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = _seed_project()
    save_project(project_path, engine.project)

    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))

    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), engine
    finally:
        await engine.devices.disconnect_all()
        rest.set_engine(None)
        ws.set_engine(None)
        unregister_driver("test_rev_ctrl")


async def test_device_update_bumps_revision(rev_engine):
    client, engine = rev_engine
    before = engine._project_revision
    resp = client.put("/api/devices/ctrl1", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert engine._project_revision > before
    assert load_project(engine.project_path).devices[0].name == "Renamed"


async def test_device_delete_bumps_revision_and_sweeps_state(rev_engine):
    client, engine = rev_engine
    engine.state.set("device.ctrl1.connected", True, source="test")
    before = engine._project_revision
    resp = client.delete("/api/devices/ctrl1")
    assert resp.status_code == 200
    assert engine._project_revision > before
    assert load_project(engine.project_path).devices == []
    assert engine.devices.get_device_config("ctrl1") is None
    # The seam's device sync also sweeps orphaned state keys — the old
    # direct remove_device call left them behind.
    assert engine.state.get("device.ctrl1.connected") is None


async def test_pending_settings_store_bumps_revision(rev_engine):
    client, engine = rev_engine
    before = engine._project_revision
    resp = client.post(
        "/api/devices/ctrl1/settings/pending", json={"settings": {"brightness": 50}}
    )
    assert resp.status_code == 200
    assert engine._project_revision > before
    dev = load_project(engine.project_path).devices[0]
    assert dev.pending_settings == {"brightness": 50}
    # Runtime queue and persisted queue stay identical (reconciler stays
    # convergent — no device bounce on the next device-section edit).
    assert engine.devices.get_device_config("ctrl1")["pending_settings"] == {
        "brightness": 50
    }


async def test_child_entity_patch_bumps_revision_without_device_bounce(rev_engine):
    client, engine = rev_engine
    driver_before = engine.devices.get_driver("ctrl1")
    before = engine._project_revision
    resp = client.patch(
        "/api/devices/ctrl1/children/encoder/5", json={"label": "Stage TX"}
    )
    assert resp.status_code == 200
    assert engine._project_revision > before
    dev = load_project(engine.project_path).devices[0]
    assert dev.child_entities["encoder"]["005"].label == "Stage TX"
    # Applied live — the same driver instance keeps running.
    assert engine.devices.get_driver("ctrl1") is driver_before


async def test_connection_update_bumps_revision(rev_engine):
    client, engine = rev_engine
    before = engine._project_revision
    resp = client.put(
        "/api/connections/ctrl1", json={"host": "10.0.0.60", "port": 5001}
    )
    assert resp.status_code == 200
    assert engine._project_revision > before
    assert load_project(engine.project_path).connections["ctrl1"]["host"] == "10.0.0.60"
    # The connections diff hot-swapped the device with the new host.
    assert engine.devices.get_device_config("ctrl1")["config"]["host"] == "10.0.0.60"


async def test_connection_bulk_update_bumps_revision(rev_engine):
    client, engine = rev_engine
    before = engine._project_revision
    resp = client.put(
        "/api/connections", json={"ctrl1": {"host": "10.0.0.61", "port": 5001}}
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert engine._project_revision > before
    assert engine.devices.get_device_config("ctrl1")["config"]["host"] == "10.0.0.61"


async def test_connection_delete_bumps_revision(rev_engine):
    client, engine = rev_engine
    before = engine._project_revision
    resp = client.delete("/api/connections/ctrl1")
    assert resp.status_code == 200
    assert engine._project_revision > before
    assert "ctrl1" not in load_project(engine.project_path).connections


async def test_connection_import_bumps_revision(rev_engine):
    client, engine = rev_engine
    before = engine._project_revision
    resp = client.post(
        "/api/connections/import",
        json={"ctrl1": {"host": "10.0.0.62", "port": 5001, "_device_name": "X"}},
    )
    assert resp.status_code == 200
    assert engine._project_revision > before
    conn = load_project(engine.project_path).connections["ctrl1"]
    assert conn == {"host": "10.0.0.62", "port": 5001}
