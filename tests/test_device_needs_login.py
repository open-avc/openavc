"""Needs login: a device whose login is not saved on this system waits for one.

What is pinned here:

- Which devices wait (``Engine.logins_needed``): each enabled device on an
  installed driver whose project marks a login saved on this system that this
  system does not have. Not a disabled device, not one whose driver is not
  installed, not one whose login is saved.
- The device page's list: a device's info names the settings that wait
  (``needs_login``), names only, and only those still missing.
- The doors a project comes in through from somewhere else name the devices
  that wait: a backup restored, a Project Library copy opened, a project
  imported.
- Entering a login through the device's own settings save
  (``PATCH /api/devices/{id}``, the save the device page's Needs login form
  sends): the login goes into the login file, the project keeps the marker,
  and the device is added again with the login. Saving one of two leaves the
  other waiting. The driver's default is saved the same way.

An invented device throughout.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from openavc.api import rest
from openavc.core import project_library as library
from openavc.core.backup_manager import backup_id, create_backup
from openavc.core.device_logins import SAVED_LOGIN, DeviceLogins
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver
from openavc.main import app

DRIVER = "acme_needs_login"
DEFAULT_PASSWORD = "acme-factory"


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5500, "password": DEFAULT_PASSWORD},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "username": {"type": "string", "label": "Username"},
            "password": {"type": "string", "secret": True, "label": "Password"},
            "api_key": {"type": "string", "secret": True, "label": "API Key"},
            "mode": {"type": "string", "label": "Mode"},
        },
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture(autouse=True)
def _driver(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    registry.register_driver(_AcmeWidget)


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", folder / "saved_projects")
    return folder


@pytest.fixture
async def engine(data, tmp_path):
    from openavc.core.engine import Engine

    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    rest.set_engine(eng)
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            yield eng
    finally:
        rest.set_engine(None)
        await eng.devices.disconnect_all()


@pytest.fixture
async def client(engine):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        yield http


def _device(device_id: str, name: str, *, driver: str = DRIVER, enabled: bool = True,
            **config: Any) -> DeviceConfig:
    return DeviceConfig(id=device_id, driver=driver, name=name, enabled=enabled,
                        config={"mode": "eco", **config})


def _project(*devices: DeviceConfig, passwords: dict[str, str] | None = None) -> ProjectConfig:
    passwords = passwords or {}
    return ProjectConfig(
        project=ProjectMeta(id="p1", name="Hall"),
        devices=list(devices),
        connections={
            d.id: {"host": "192.0.2.10", "port": 5500, "username": "admin",
                   "password": passwords.get(d.id, SAVED_LOGIN)}
            for d in devices
        },
    )


def _write(path, project: ProjectConfig) -> None:
    path.write_text(json.dumps(project.model_dump(mode="json"), indent=4), encoding="utf-8")


def _on_disk(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# --- which devices wait --------------------------------------------------------------


async def test_the_devices_that_wait_are_the_enabled_ones_with_a_login_not_saved_here(engine):
    engine.device_logins.save("saved1", DRIVER, {"password": "Hunter2-Acme"})
    engine.project = _project(
        _device("proj1", "Projector"),
        _device("saved1", "Display"),
        _device("off1", "Spare", enabled=False),
        _device("gone1", "Old Amp", driver="acme_not_installed"),
        _device("plain1", "Switcher"),
        passwords={"plain1": "typed-in-the-project"},
    )
    assert engine.logins_needed() == [{"id": "proj1", "name": "Projector"}]


async def test_a_project_with_nothing_marked_waits_for_nothing(engine, data):
    engine.project = _project(_device("plain1", "Switcher"), passwords={"plain1": "plain-old"})
    assert engine.logins_needed() == []
    assert not (data / "device_logins.json").exists()


async def test_the_device_page_is_told_which_settings_wait_and_never_a_value(engine):
    engine.device_logins.save("proj1", DRIVER, {"password": "Hunter2-Acme"})
    engine.project = _project(_device("proj1", "Projector", api_key=SAVED_LOGIN))
    await engine._sync_devices(defer_connect=True)

    info = engine.devices.get_device_info("proj1")
    assert info["needs_login"] == ["api_key"]
    assert "Hunter2-Acme" not in json.dumps(info["needs_login"])


# --- the doors that bring a project in name them ---------------------------------------


async def test_a_restored_backup_names_the_devices_that_need_their_login(engine, client):
    _write(engine.project_path, _project(_device("proj1", "Projector")))
    filename = backup_id(engine.project_path.parent, create_backup(engine.project_path, "Earlier"))

    answer = await client.post(f"/api/backups/{filename}/restore")

    assert answer.status_code == 200, answer.text
    assert answer.json()["needs_login"] == [{"id": "proj1", "name": "Projector"}]


async def test_an_opened_library_copy_names_the_devices_that_need_their_login(engine, client):
    _write(engine.project_path, _project())
    await engine.reload_project()
    content = json.dumps(_project(_device("proj1", "Projector")).model_dump(mode="json")).encode()
    library.import_project(content, "other_room.avc", "other_room")

    answer = await client.post(
        "/api/project/open-from-library",
        json={"library_id": "other_room", "project_name": "Other Room", "project_id": "other_room"},
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["needs_login"] == [{"id": "proj1", "name": "Projector"}]


async def test_an_imported_project_names_the_devices_that_need_their_login(engine, client):
    _write(engine.project_path, _project())
    await engine.reload_project()
    document = _project(_device("proj1", "Projector")).model_dump(mode="json")

    answer = await client.put("/api/project?mode=import", json=document)

    assert answer.status_code == 200, answer.text
    assert answer.json()["needs_login"] == [{"id": "proj1", "name": "Projector"}]


async def test_an_ordinary_save_answers_as_it_did(engine, client):
    _write(engine.project_path, _project(_device("proj1", "Projector")))
    await engine.reload_project()
    document = engine.project.model_dump(mode="json")

    answer = await client.put("/api/project", json=document)

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"status": "saved"}


# --- entering the login ------------------------------------------------------------


def _settings_with(engine, device_id: str, **logins: str) -> dict[str, Any]:
    """What the device page's form sends: the device's saved settings as the
    project holds them, markers and all, with the logins typed in."""
    device = next(d for d in engine.project.devices if d.id == device_id)
    return {**device.config, **logins}


async def test_a_login_entered_is_saved_here_and_the_device_is_added_again_with_it(engine, client, data):
    _write(engine.project_path, _project(_device("proj1", "Projector")))
    await engine.reload_project()
    before = engine.devices.get_driver("proj1")
    assert engine.devices.get_device_info("proj1")["needs_login"] == ["password"]

    answer = await client.patch(
        "/api/devices/proj1", json={"config": _settings_with(engine, "proj1", password="Typed-Here")},
    )

    assert answer.status_code == 200, answer.text
    assert DeviceLogins(data).login("proj1", DRIVER, "password") == "Typed-Here"
    on_disk = _on_disk(engine.project_path)
    assert on_disk["connections"]["proj1"]["password"] == SAVED_LOGIN
    assert "Typed-Here" not in engine.project_path.read_text(encoding="utf-8")
    assert on_disk["devices"][0]["config"]["mode"] == "eco"
    assert engine.devices.get_driver("proj1") is not before
    assert engine.devices.get_device_config("proj1")["config"]["password"] == "Typed-Here"
    assert engine.devices.get_device_info("proj1")["needs_login"] == []
    assert engine.logins_needed() == []


async def test_saving_one_of_two_leaves_the_other_waiting(engine, client, data):
    _write(engine.project_path, _project(_device("proj1", "Projector", api_key=SAVED_LOGIN)))
    await engine.reload_project()
    assert engine.devices.get_device_info("proj1")["needs_login"] == ["api_key", "password"]

    await client.patch(
        "/api/devices/proj1", json={"config": _settings_with(engine, "proj1", api_key="k-123456")},
    )

    assert DeviceLogins(data).login("proj1", DRIVER, "api_key") == "k-123456"
    assert engine.devices.get_device_info("proj1")["needs_login"] == ["password"]
    assert engine.logins_needed() == [{"id": "proj1", "name": "Projector"}]


async def test_the_drivers_default_is_saved_like_a_login_typed_in(engine, client, data):
    _write(engine.project_path, _project(_device("proj1", "Projector")))
    await engine.reload_project()

    await client.patch(
        "/api/devices/proj1",
        json={"config": _settings_with(engine, "proj1", password=DEFAULT_PASSWORD)},
    )

    assert DeviceLogins(data).login("proj1", DRIVER, "password") == DEFAULT_PASSWORD
    assert _on_disk(engine.project_path)["connections"]["proj1"]["password"] == SAVED_LOGIN
    assert engine.devices.get_device_config("proj1")["config"]["password"] == DEFAULT_PASSWORD
    assert engine.logins_needed() == []
