"""A space typed around an address, a port or a login never reaches the dial.

``core/typed_fields.py`` trims the values a person types to say where and as
whom a device is reached, never a password. It runs where a project is
written, so every door that saves a device's settings stores the clean value,
and where a device's connection is resolved, so a project saved with a space
connects without an edit. The device here is an invented camera.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.connection_fault import UNREACHABLE, classify_connection_fault
from openavc.core.device_config import connection_of, resolve_device_config
from openavc.core.device_logins import SAVED_LOGIN
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import (
    DeviceConfig,
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
)
from openavc.core.state_store import StateStore
from openavc.core.typed_fields import trim_typed_fields, trims_value
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.main import app

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "typed_field_trim_cases.json").read_text()
)["cases"]

SCHEMA: dict[str, Any] = {
    "host": {"type": "string", "label": "IP Address"},
    "port": {"type": "integer", "label": "Control Port"},
    "username": {"type": "string", "label": "Username"},
    "password": {"type": "string", "secret": True, "label": "Password"},
    "mac_address": {"type": "string", "label": "MAC Address"},
    "notes": {"type": "text", "label": "Notes"},
}


class _AcmeCamera(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_trim_camera",
        "name": "Acme Camera",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"port": 5500},
        "config_schema": SCHEMA,
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _AcmeCameraOffline(_AcmeCamera):
    """The same camera with nothing to dial, for the doors' tests."""

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None


@pytest.fixture
def camera_driver():
    register_driver(_AcmeCamera)
    yield _AcmeCamera
    unregister_driver("acme_trim_camera")


# --- the rule ---


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c['key']}-{json.dumps(c['field'])}" for c in CASES],
)
def test_the_rule_answers_as_the_dialogs_do(case):
    """The same case file runs through trimsTypedValue in deviceConfigCoerce.test.ts."""
    assert trims_value(case["key"], case["field"]) is case["trims"]


def test_typed_values_are_trimmed_and_a_password_is_kept(camera_driver):
    padded = {
        "host": " 192.0.2.10 ",
        "port": " 5500",
        "username": "admin\t",
        "password": " pass word ",
        "mac_address": "00:11:22:33:44:55 ",
        "notes": " first line\n",
        "unknown_field": " kept ",
        "ssl": True,
    }
    assert trim_typed_fields("acme_trim_camera", padded) == {
        "host": "192.0.2.10",
        "port": "5500",
        "username": "admin",
        "password": " pass word ",
        "mac_address": "00:11:22:33:44:55",
        "notes": " first line\n",
        "unknown_field": " kept ",
        "ssl": True,
    }


def test_a_driver_that_is_not_installed_trims_only_its_connection_fields():
    padded = {"host": " 192.0.2.10", "password": " x ", "mac_address": " 00:11 "}
    assert trim_typed_fields("acme_not_installed", padded) == {
        "host": "192.0.2.10", "password": " x ", "mac_address": " 00:11 ",
    }


def test_nothing_to_trim_hands_back_the_same_dict(camera_driver):
    clean = {"host": "192.0.2.10", "port": 5500}
    assert trim_typed_fields("acme_trim_camera", clean) is clean


# --- where a connection is resolved ---


def _project(host: str, port: Any = 5500) -> SimpleNamespace:
    return SimpleNamespace(
        devices=[DeviceConfig(id="cam1", driver="acme_trim_camera", name="Camera", config={})],
        connections={"cam1": {"host": host, "port": port, "password": " p "}},
    )


def test_a_project_saved_with_a_space_resolves_to_the_clean_address(camera_driver):
    project = _project(" 192.0.2.10 ")
    resolved = resolve_device_config(project.devices[0], project)
    assert resolved["config"]["host"] == "192.0.2.10"
    assert resolved["config"]["password"] == " p "
    assert connection_of(resolved).host == "192.0.2.10"


async def test_a_device_saved_with_a_space_before_its_address_connects(camera_driver):
    """The Harmony case: a TCP device saved with " <address>" read unreachable."""
    accepted = asyncio.Event()

    async def handle(reader, writer):
        accepted.set()
        await reader.read()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    project = _project(" 127.0.0.1 ", port)
    resolved = resolve_device_config(project.devices[0], project)
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    driver = _AcmeCamera("cam1", resolved["config"], state, events)
    try:
        await driver.connect()
        await asyncio.wait_for(accepted.wait(), 2.0)
        assert driver.get_state("connected") is True
        assert driver.config["host"] == "127.0.0.1"
    finally:
        await driver.disconnect()
        server.close()
        await server.wait_closed()


def test_a_bridge_whose_address_was_saved_with_a_space_is_dialed_clean():
    from openavc.core.device_config import resolve_bridge_binding

    class _AcmeBridge(_AcmeCameraOffline):
        DRIVER_INFO = {
            **_AcmeCamera.DRIVER_INFO,
            "id": "acme_trim_bridge",
            "bridge": {"ports": [
                {"id": "serial_1", "kind": "serial", "label": "Serial 1", "passthrough_port": 4999},
            ]},
        }

    register_driver(_AcmeBridge)
    try:
        project = SimpleNamespace(
            devices=[DeviceConfig(id="itach", driver="acme_trim_bridge", name="Bridge", config={})],
            connections={"itach": {"host": " 192.0.2.40 "}},
        )
        resolved = resolve_bridge_binding({"bridge": "itach", "bridge_port": "serial_1"}, project)
        assert resolved["host"] == "192.0.2.40"
        assert resolved["port"] == 4999
    finally:
        unregister_driver("acme_trim_bridge")


# --- where a project is written: every door ---


@pytest.fixture
async def camera_engine(tmp_path):
    register_driver(_AcmeCameraOffline)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig(
        project=ProjectMeta(id="proj1", name="Test Project"),
        devices=[DeviceConfig(id="cam1", driver="acme_trim_camera", name="Camera", config={})],
        connections={"cam1": {"host": "192.0.2.10", "port": 5500}},
    )
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
        unregister_driver("acme_trim_camera")


def _saved(engine) -> tuple[DeviceConfig, dict]:
    project = load_project(engine.project_path)
    device = next(d for d in project.devices if d.id == "cam1")
    return device, project.connections.get("cam1", {})


async def test_edit_device_stores_and_dials_the_clean_address(camera_engine):
    client, engine = camera_engine
    resp = client.patch("/api/devices/cam1", json={"config": {
        "host": " 192.0.2.20 ", "port": 5500, "username": " admin ",
        "password": " pass word ", "mac_address": " 00:11:22:33:44:55",
    }})
    assert resp.status_code == 200, resp.text
    device, connection = _saved(engine)
    # The password is saved on this system as typed, spaces and all.
    assert connection == {
        "host": "192.0.2.20", "port": 5500, "username": "admin", "password": SAVED_LOGIN,
    }
    assert engine.device_logins.login("cam1", "acme_trim_camera", "password") == " pass word "
    assert device.config == {"mac_address": "00:11:22:33:44:55"}
    assert engine.devices.get_driver("cam1").config["host"] == "192.0.2.20"
    assert engine.devices.get_driver("cam1").config["password"] == " pass word "


async def test_a_connection_row_is_stored_clean(camera_engine):
    client, engine = camera_engine
    resp = client.put("/api/connections/cam1", json={"host": "192.0.2.30\n", "port": " 5501 "})
    assert resp.status_code == 200, resp.text
    assert _saved(engine)[1] == {"host": "192.0.2.30", "port": "5501"}

    resp = client.put("/api/connections", json={"cam1": {"host": " 192.0.2.31"}})
    assert resp.status_code == 200, resp.text
    assert _saved(engine)[1] == {"host": "192.0.2.31"}

    resp = client.post("/api/connections/import", json={"cam1": {"host": " 192.0.2.32"}})
    assert resp.status_code == 200, resp.text
    assert _saved(engine)[1] == {"host": "192.0.2.32"}


async def test_add_device_through_the_whole_project_save_stores_the_clean_address(camera_engine):
    """The Add Device dialog saves the whole project."""
    client, engine = camera_engine
    body = engine.project.model_dump(mode="json")
    body["devices"].append(
        {"id": "cam2", "driver": "acme_trim_camera", "name": "Camera 2", "config": {}},
    )
    body["connections"]["cam2"] = {"host": " 192.0.2.40", "password": " p "}
    resp = client.put("/api/project", json=body)
    assert resp.status_code == 200, resp.text
    saved = load_project(engine.project_path)
    assert saved.connections["cam2"] == {"host": "192.0.2.40", "password": SAVED_LOGIN}
    assert engine.device_logins.login("cam2", "acme_trim_camera", "password") == " p "


async def test_the_ai_device_tools_store_the_clean_address(camera_engine):
    _client, engine = camera_engine
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    result = await handler._update_device({"device_id": "cam1", "config": {"host": " 192.0.2.50"}})
    assert result == {"status": "updated", "device_id": "cam1"}
    assert _saved(engine)[1]["host"] == "192.0.2.50"

    result = await handler._add_device({
        "id": "cam3", "driver": "acme_trim_camera",
        "config": {"host": "192.0.2.51 ", "password": " p "},
    })
    assert result == {"status": "created", "id": "cam3"}
    saved = load_project(engine.project_path).connections["cam3"]
    assert saved["host"] == "192.0.2.51"
    assert saved["password"] == SAVED_LOGIN
    assert engine.device_logins.login("cam3", "acme_trim_camera", "password") == " p "


# --- the message names what was dialed ---


def test_an_unreachable_address_with_a_space_shows_the_space():
    fault = classify_connection_fault(
        last_error="Failed to connect to  192.0.2.10:5000: timed out",
        exc=TimeoutError(), host=" 192.0.2.10", port=5000, transport="tcp",
    )
    assert fault.code == UNREACHABLE
    assert fault.message == (
        "Can't reach \" 192.0.2.10\":5000. Check the IP address and network."
    )


def test_a_clean_address_reads_as_before():
    fault = classify_connection_fault(
        last_error="Failed to connect to 192.0.2.10:5000: timed out",
        exc=TimeoutError(), host="192.0.2.10", port=5000, transport="tcp",
    )
    assert fault.message == "Can't reach 192.0.2.10:5000. Check the IP address and network."
