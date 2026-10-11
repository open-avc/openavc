"""A driver's required config field is refused blank where one device changes.

``PATCH /api/devices/{id}`` and the AI's add/update device tools refuse a
``required: true`` field the device would run without, naming it by its label,
the way the Add and Edit Device dialogs do. A whole-project save and a project
load never refuse over it: one older device with the gap must not block every
other edit or stop a project opening.

The device here is an invented camera: IP address and Model required (Model
with no default), a required password the driver gives no default, and a
driver that also connects by serial.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.engine import Engine
from openavc.core.project_loader import (
    DeviceConfig,
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
)
from openavc.core.required_config import (
    connection_kind,
    merged_device_config,
    missing_for_driver,
    missing_required_fields,
    required_sentence,
)
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.main import app

SCHEMA: dict[str, Any] = {
    "host": {"type": "string", "required": True, "label": "IP Address"},
    "port": {"type": "integer", "default": 5500, "label": "Control Port"},
    "model": {
        "type": "enum", "required": True, "label": "Model",
        "values": [{"value": "w12", "label": "W12"}, {"value": "w20", "label": "W20"}],
    },
    "password": {"type": "string", "secret": True, "required": True, "label": "Password"},
    "presets": {"type": "table", "required": True, "label": "Presets", "columns": {}},
}


class _AcmeCamera(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_camera",
        "name": "Acme Camera",
        "transport": "tcp",
        "transports": ["tcp", "serial"],
        "default_config": {"port": 5500},
        "config_schema": SCHEMA,
        "state_variables": {},
        "commands": {},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


# --- The rule ---


def test_a_blank_required_field_is_named_by_its_label():
    missing = missing_required_fields(
        SCHEMA, {"host": "10.0.0.5", "port": 5500, "model": "", "password": "x"}, "network",
    )
    assert missing == [("model", "Model")]
    assert required_sentence(["Model"]) == "Model is required."
    assert required_sentence(["IP Address", "Model"]) == "IP Address and Model are required."
    assert (
        required_sentence(["IP Address", "Password", "Model"])
        == "IP Address, Password and Model are required."
    )


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_none_and_whitespace_are_blank(blank):
    config = {"host": "10.0.0.5", "model": blank, "password": "x"}
    assert missing_required_fields(SCHEMA, config, "network") == [("model", "Model")]


def test_false_and_zero_are_values():
    schema = {
        "on": {"type": "boolean", "required": True, "label": "On"},
        "level": {"type": "integer", "required": True, "label": "Level"},
    }
    assert missing_required_fields(schema, {"on": False, "level": 0}, "network") == []


def test_a_table_field_is_not_asked():
    """It is set up on the device page, never in the dialog the rule refuses."""
    config = {"host": "10.0.0.5", "model": "w12", "password": "x"}
    assert missing_required_fields(SCHEMA, config, "network") == []


def test_the_ip_address_is_asked_only_of_a_network_connection():
    config = {"model": "w12", "password": "x"}
    assert missing_required_fields(SCHEMA, config, "network") == [("host", "IP Address")]
    assert missing_required_fields(SCHEMA, config, "serial") == []
    assert missing_required_fields(SCHEMA, config, "bridge") == []


def test_the_port_is_asked_of_network_and_serial_never_through_a_bridge():
    schema = {"port": {"type": "string", "required": True, "label": "Serial Port"}}
    assert missing_required_fields(schema, {}, "serial") == [("port", "Serial Port")]
    assert missing_required_fields(schema, {}, "network") == [("port", "Serial Port")]
    assert missing_required_fields(schema, {}, "bridge") == []


def test_the_pickers_other_fields_are_never_asked():
    schema = {
        "baudrate": {"type": "integer", "required": True, "label": "Baud Rate"},
        "bridge": {"type": "string", "required": True, "label": "Bridge"},
    }
    assert missing_required_fields(schema, {}, "serial") == []
    assert missing_required_fields(schema, {}, "bridge") == []


@pytest.mark.parametrize(
    "config, driver_transport, kind",
    [
        ({}, "tcp", "network"),
        ({"transport": "udp"}, "tcp", "network"),
        ({"transport": "serial"}, "tcp", "serial"),
        ({}, "serial", "serial"),
        ({"bridge": "itach", "bridge_port": "serial:1"}, "tcp", "bridge"),
        ({"bridge": "itach"}, "tcp", "network"),
        ({}, "bridge", "bridge"),
    ],
)
def test_connection_kind(config, driver_transport, kind):
    assert connection_kind(config, driver_transport) == kind


def test_a_driver_default_fills_a_required_field():
    register_driver(_AcmeCamera)
    try:
        merged = merged_device_config("acme_camera", {"model": "w12"}, {"host": "h", "password": "x"})
        assert merged["port"] == 5500
        schema_port_required = {**SCHEMA, "port": {**SCHEMA["port"], "required": True}}
        assert missing_required_fields(schema_port_required, merged, "network") == []
    finally:
        unregister_driver("acme_camera")


def test_a_driver_that_is_not_installed_has_nothing_to_refuse():
    assert missing_for_driver("not_installed_driver", {}) == []


# --- The doors ---


def _seed_project() -> ProjectConfig:
    """An older device with the gap: no Model."""
    return ProjectConfig(
        project=ProjectMeta(id="proj1", name="Test Project"),
        devices=[DeviceConfig(id="cam1", driver="acme_camera", name="Camera", config={})],
        connections={"cam1": {"host": "10.0.0.50", "port": 5500, "password": "stored"}},
    )


@pytest.fixture
async def camera_engine(tmp_path):
    register_driver(_AcmeCamera)
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
        unregister_driver("acme_camera")


def _saved_device(engine) -> DeviceConfig:
    return next(d for d in load_project(engine.project_path).devices if d.id == "cam1")


async def test_patch_refuses_a_blank_required_field_by_label(camera_engine):
    client, engine = camera_engine
    before = engine._project_revision
    resp = client.patch("/api/devices/cam1", json={"config": {"host": "10.0.0.50", "model": ""}})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Model is required."
    assert engine._project_revision == before
    assert _saved_device(engine).config == {}


async def test_patch_saves_once_the_field_is_filled(camera_engine):
    client, engine = camera_engine
    resp = client.patch("/api/devices/cam1", json={"config": {"host": "10.0.0.50", "model": "w12"}})
    assert resp.status_code == 200, resp.text
    assert _saved_device(engine).config == {"model": "w12"}


async def test_patch_names_every_blank_field(camera_engine):
    client, _engine = camera_engine
    resp = client.patch("/api/devices/cam1", json={"config": {"host": "", "model": ""}})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "IP Address and Model are required."
    resp = client.patch(
        "/api/devices/cam1", json={"config": {"host": None, "password": None, "model": ""}},
    )
    assert resp.json()["detail"] == "IP Address, Model and Password are required."


async def test_patch_counts_a_stored_password_it_was_not_sent(camera_engine):
    client, engine = camera_engine
    resp = client.patch("/api/devices/cam1", json={"config": {"model": "w20"}})
    assert resp.status_code == 200, resp.text
    assert load_project(engine.project_path).connections["cam1"]["password"] == "stored"


async def test_patch_does_not_ask_for_an_ip_address_of_a_serial_or_bridged_device(camera_engine):
    client, engine = camera_engine
    serial = {"transport": "serial", "port": "/dev/ttyUSB0", "host": None, "model": "w12"}
    assert client.patch("/api/devices/cam1", json={"config": serial}).status_code == 200
    bridged = {"port": None, "bridge": "itach1", "bridge_port": "serial:1", "model": "w12"}
    assert client.patch("/api/devices/cam1", json={"config": bridged}).status_code == 200
    assert "host" not in load_project(engine.project_path).connections["cam1"]


async def test_patch_renaming_or_disabling_an_older_device_is_not_refused(camera_engine):
    client, engine = camera_engine
    assert client.patch("/api/devices/cam1", json={"name": "Lectern Camera"}).status_code == 200
    assert client.patch("/api/devices/cam1", json={"enabled": False}).status_code == 200
    saved = _saved_device(engine)
    assert saved.name == "Lectern Camera"
    assert saved.enabled is False


async def test_ai_update_device_refuses_with_the_label_and_the_key(camera_engine):
    _client, engine = camera_engine
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    result = await handler._update_device({"device_id": "cam1", "config": {"port": 5501}})
    assert result == {"error": "Model is required.", "missing_config": ["model"]}
    assert _saved_device(engine).config == {}

    result = await handler._update_device({"device_id": "cam1", "config": {"model": "w12"}})
    assert result == {"status": "updated", "device_id": "cam1"}
    assert _saved_device(engine).config == {"model": "w12"}


async def test_ai_update_device_rename_is_not_refused(camera_engine):
    _client, engine = camera_engine
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    result = await handler._update_device({"device_id": "cam1", "name": "Lectern Camera"})
    assert result == {"status": "updated", "device_id": "cam1"}


async def test_ai_add_device_refuses_a_blank_required_field(camera_engine):
    _client, engine = camera_engine
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    result = await handler._add_device(
        {"id": "cam2", "driver": "acme_camera", "config": {"host": "10.0.0.51", "password": "pw"}},
    )
    assert result == {"error": "Model is required.", "missing_config": ["model"]}
    assert all(d.id != "cam2" for d in load_project(engine.project_path).devices)

    result = await handler._add_device(
        {
            "id": "cam2", "driver": "acme_camera",
            "config": {"host": "10.0.0.51", "password": "pw", "model": "w20"},
        },
    )
    assert result == {"status": "created", "id": "cam2"}


async def test_a_whole_project_save_never_refuses(camera_engine):
    """One older device with the gap must not block every other edit."""
    client, engine = camera_engine
    body = engine.project.model_dump(mode="json")
    body["project"]["name"] = "Renamed Project"
    body["devices"].append(
        {"id": "cam3", "driver": "acme_camera", "name": "Camera 3", "config": {}},
    )
    resp = client.put("/api/project", json=body)
    assert resp.status_code == 200, resp.text
    saved = load_project(engine.project_path)
    assert saved.project.name == "Renamed Project"
    assert {d.id for d in saved.devices} == {"cam1", "cam3"}


async def test_a_project_load_never_refuses(camera_engine):
    client, engine = camera_engine
    resp = client.post("/api/project/reload")
    assert resp.status_code == 200, resp.text
    assert [d.id for d in engine.project.devices] == ["cam1"]
    assert engine.devices.get_driver("cam1") is not None
