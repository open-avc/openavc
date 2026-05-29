"""Regression test for the device-edit data-loss bug (audit C1 + C2).

``PUT /devices/{id}`` and the cloud AI ``update_device`` tool both rebuild a
device's ``DeviceConfig`` from the edit payload. Before the fix they dropped
``child_entities`` (user labels / per-child config) on every edit, and the AI
tool additionally dropped ``pending_settings`` and skipped the
connection-table merge, so the hot-swapped runtime device came back with no
host (silently breaking control).

These tests create a child-capable TCP device that has both ``child_entities``
and ``pending_settings``, with its host/port in the connections table, rename
it via each path, and assert everything survives both on disk (the reloaded
project file) and in the live driver / runtime config.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from server.api import rest, ws
from server.api.models import DeviceUpdateRequest
from server.cloud.ai_tool_handler import AIToolHandler
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


class _ChildTCPController(BaseDriver):
    """Child-capable TCP controller used only by these tests.

    ``connect`` is a no-op (no real socket, no exception) so the
    DeviceManager neither opens a connection nor spawns a reconnect task.
    Because the base ``set_device_setting`` raises NotImplementedError, the
    queued ``pending_settings`` can't be applied (and therefore cleared) on
    the fake connect — which is exactly the state we want to prove survives
    an edit.
    """

    DRIVER_INFO: dict[str, Any] = {
        "id": "test_child_ctrl",
        "name": "Test Child Controller",
        "transport": "tcp",
        "default_config": {"port": 5000},
        "state_variables": {},
        "commands": {},
        "child_entity_types": {
            "encoder": {
                "label": "Encoder",
                "id_format": {
                    "type": "integer", "min": 1, "max": 762, "pad_width": 3,
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
                driver="test_child_ctrl",
                name="Controller 1",
                config={},
                pending_settings={"brightness": 75},
                child_entities={
                    "encoder": {
                        "005": ChildEntityConfig(
                            label="Lobby TX", config={"room": "Lobby"},
                        ),
                    },
                },
            ),
        ],
        # host/port live in the connections table, NOT device.config — this
        # is the field set the AI tool used to drop on update (C2).
        connections={"ctrl1": {"host": "10.0.0.50", "port": 5001}},
    )


@pytest.fixture
async def child_engine(tmp_path):
    """Real engine + real DeviceManager + a live child-capable device."""
    register_driver(_ChildTCPController)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = _seed_project()
    save_project(project_path, engine.project)

    # Bring up the live driver the same way a real start would.
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
        unregister_driver("test_child_ctrl")


def _assert_survived_on_disk(engine, *, expected_name: str) -> None:
    reloaded = load_project(engine.project_path)
    dev = next(d for d in reloaded.devices if d.id == "ctrl1")
    assert dev.name == expected_name
    # child_entities preserved — the C1 bug wiped this on every edit.
    assert "encoder" in dev.child_entities
    entry = dev.child_entities["encoder"]["005"]
    assert entry.label == "Lobby TX"
    assert entry.config == {"room": "Lobby"}
    # pending_settings preserved.
    assert dev.pending_settings == {"brightness": 75}


def _assert_survived_at_runtime(engine) -> dict:
    driver = engine.devices.get_driver("ctrl1")
    assert driver is not None
    # Live driver re-seeded with the project child map (drives label state).
    assert driver._project_child_entities["encoder"]["005"]["label"] == "Lobby TX"
    cfg = engine.devices._device_configs["ctrl1"]
    assert cfg["pending_settings"] == {"brightness": 75}
    return cfg


async def test_rest_put_preserves_children_and_pending(child_engine):
    client, engine = child_engine
    resp = client.put("/api/devices/ctrl1", json={"name": "Renamed via REST"})
    assert resp.status_code == 200

    _assert_survived_on_disk(engine, expected_name="Renamed via REST")
    cfg = _assert_survived_at_runtime(engine)
    # REST already merged the connection table; host stays present.
    assert cfg["config"]["host"] == "10.0.0.50"


async def test_ai_update_device_preserves_children_pending_and_host(child_engine):
    _client, engine = child_engine
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    result = await handler._update_device(
        {"device_id": "ctrl1", "name": "Renamed via AI"}
    )
    assert result == {"status": "updated", "device_id": "ctrl1"}

    _assert_survived_on_disk(engine, expected_name="Renamed via AI")
    cfg = _assert_survived_at_runtime(engine)
    # C2: host/port live in the connections table; the AI tool must merge them
    # back in via resolved_device_config, or the device returns with no host.
    assert cfg["config"]["host"] == "10.0.0.50"
    assert cfg["config"]["port"] == 5001


def test_device_update_request_round_trips_child_entities():
    """The request model must declare child_entities so an explicit edit
    isn't silently dropped by Pydantic's extra='ignore' (C1)."""
    body = DeviceUpdateRequest.model_validate(
        {
            "name": "X",
            "child_entities": {
                "encoder": {"005": {"label": "Lobby TX", "config": {}}},
            },
        }
    )
    assert body.child_entities == {
        "encoder": {"005": {"label": "Lobby TX", "config": {}}},
    }
