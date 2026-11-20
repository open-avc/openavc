"""Which driver copy a Discovery card adds a device with, and where it comes from.

Install & Add sends the device and the driver it chose; the server decides the
rest from its own copy of the community index (``api/driver_catalog.py``
``index_cache``): a driver already installed is added as it is, one the index
lists is installed from the address and with the floor the index gives, and one
neither installed nor listed is refused with nothing written. Adding a scanned
device with a driver that is not installed is refused too, so a scan never
saves a device that has no driver to run it.

The scan, the device page's missing-driver list and its Install Missing read
that same copy of the index, so one fetch answers them all.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from openavc.api import driver_catalog
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, ProjectMeta
from openavc.discovery.community_index import COMMUNITY_REPO_URL
from openavc.drivers import refused_files
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver

DRIVER_ID = "acme_scanned_widget"
ENTRY = {
    "id": DRIVER_ID,
    "name": "Acme Scanned Widget",
    "manufacturer": "Acme",
    "file": "utility/acme_scanned_widget.avcdriver",
    "version": "1.2.0",
    "min_platform_version": "0.30.0",
}


class _Index:
    """The server's copy of the index, answering what it was given and
    counting how often it is read."""

    def __init__(self, drivers: list[dict]):
        self.drivers = drivers
        self.reads = 0

    async def get_drivers(self, force: bool = False) -> list[dict]:
        self.reads += 1
        return self.drivers

    def cached(self) -> list[dict]:
        return self.drivers


@pytest.fixture
def index(monkeypatch):
    shared = _Index([ENTRY])
    monkeypatch.setattr(driver_catalog, "_index_cache", shared)
    return shared


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from openavc.api import discovery as discovery_api

    fake_discovery = MagicMock()
    fake_discovery.results = {}
    monkeypatch.setattr(discovery_api, "_engine", fake_discovery)
    eng = Engine(str(tmp_path / "test.avc"))
    eng.project = ProjectConfig(project=ProjectMeta(id="t", name="Test"), devices=[], connections={})
    monkeypatch.setattr("openavc.core.project_loader.save_project", lambda *a, **k: None)
    monkeypatch.setattr(discovery_api, "_app_engine", eng)
    return eng


@pytest.fixture
def installed_driver():
    """An invented driver that is installed, with nothing the scan cannot fill."""

    class _AcmeScanned(BaseDriver):
        DRIVER_INFO = {
            "id": DRIVER_ID,
            "name": "Acme Scanned Widget",
            "transport": "tcp",
            "default_config": {"host": "", "port": 23},
            "config_schema": {
                "host": {"type": "string", "label": "Host", "required": True},
                "port": {"type": "integer", "label": "Port", "default": 23},
            },
            "state_variables": {},
            "commands": {},
        }

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def send_command(self, command, params=None):
            return None

    register_driver(_AcmeScanned)
    yield _AcmeScanned
    unregister_driver(DRIVER_ID)


@pytest.fixture
def installs(monkeypatch):
    """Every catalog install Install & Add asks for, none of them run."""
    from openavc.api import rest

    asked: list = []

    async def _install(request):
        asked.append(request)
        return {"status": "installed", "driver_id": request.driver_id}

    monkeypatch.setattr(rest, "install_community_driver", _install)
    return asked


# --- adding a scanned device ---------------------------------------------------


async def test_a_scanned_device_is_not_added_with_a_driver_that_is_not_installed(engine):
    from openavc.api.discovery import AddDeviceRequest, add_device

    with pytest.raises(HTTPException) as caught:
        await add_device(AddDeviceRequest(ip="192.0.2.70", driver_id=DRIVER_ID))
    assert caught.value.status_code == 422
    assert caught.value.detail == (
        f"Driver '{DRIVER_ID}' is not installed. The device was not added. "
        "Install it first, or choose another driver for this device."
    )
    assert engine.project.devices == []
    assert engine.project.connections == {}


async def test_a_driver_installed_but_not_loaded_is_refused_with_why(engine, tmp_path, monkeypatch):
    from openavc.api.discovery import AddDeviceRequest, add_device

    broken = tmp_path / f"{DRIVER_ID}.avcdriver"
    broken.write_text("id: acme_scanned_widget\n")
    monkeypatch.setattr(refused_files, "_refused", {})
    refused_files.record(broken, DRIVER_ID, ["Its transport is not one this platform knows."])

    with pytest.raises(HTTPException) as caught:
        await add_device(AddDeviceRequest(ip="192.0.2.70", driver_id=DRIVER_ID))
    assert caught.value.status_code == 422
    assert caught.value.detail == (
        f"Driver '{DRIVER_ID}' is installed but did not load: "
        "Its transport is not one this platform knows. The device was not added. "
        "Fix its driver file, or choose another driver for this device."
    )
    assert engine.project.devices == []


# --- Install & Add ---------------------------------------------------------------


async def test_install_and_add_installs_the_copy_the_server_index_lists(
    engine, index, installs, monkeypatch,
):
    from openavc.api import discovery as discovery_api
    from openavc.api.discovery import InstallAndMatchRequest, install_and_match

    async def _added(_request):
        return {"status": "ok", "device_id": "added", "name": "Acme Scanned Widget"}

    monkeypatch.setattr(discovery_api, "add_device", _added)
    result = await install_and_match(InstallAndMatchRequest(ip="192.0.2.70", driver_id=DRIVER_ID))

    assert result["status"] == "ok"
    assert [(r.driver_id, r.file_url, r.min_platform_version) for r in installs] == [(
        DRIVER_ID, f"{COMMUNITY_REPO_URL}/utility/acme_scanned_widget.avcdriver", "0.30.0",
    )]


async def test_install_and_add_takes_no_address_from_the_caller(engine, index, installs):
    from openavc.api.discovery import InstallAndMatchRequest

    request = InstallAndMatchRequest(
        ip="192.0.2.70", driver_id=DRIVER_ID, file_url="https://example.invalid/x.avcdriver",
    )
    assert "file_url" not in request.model_dump()


async def test_install_and_add_refuses_a_driver_the_index_does_not_list(engine, index, installs):
    from openavc.api.discovery import InstallAndMatchRequest, install_and_match

    index.drivers = [{"id": "acme_other", "file": "utility/acme_other.avcdriver"}]
    with pytest.raises(HTTPException) as caught:
        await install_and_match(InstallAndMatchRequest(ip="192.0.2.70", driver_id=DRIVER_ID))
    assert caught.value.status_code == 404
    assert caught.value.detail == (
        f"Driver '{DRIVER_ID}' is not installed and is not in the community driver "
        "library. The device was not added. Import the driver's file first, or "
        "choose another driver for this device."
    )
    assert installs == []
    assert engine.project.devices == []


async def test_install_and_add_refuses_an_index_entry_that_names_no_file(engine, index, installs):
    from openavc.api.discovery import InstallAndMatchRequest, install_and_match

    index.drivers = [{"id": DRIVER_ID, "name": "Acme Scanned Widget"}]
    with pytest.raises(HTTPException) as caught:
        await install_and_match(InstallAndMatchRequest(ip="192.0.2.70", driver_id=DRIVER_ID))
    assert caught.value.status_code == 404
    assert installs == []
    assert engine.project.devices == []


async def test_install_and_add_of_an_installed_driver_downloads_nothing(
    engine, index, installs, installed_driver,
):
    from openavc.api.discovery import InstallAndMatchRequest, install_and_match

    result = await install_and_match(InstallAndMatchRequest(ip="192.0.2.70", driver_id=DRIVER_ID))

    assert result["status"] == "ok"
    assert installs == []
    assert index.reads == 0
    assert [d.driver for d in engine.project.devices] == [DRIVER_ID]


# --- one copy of the index ---------------------------------------------------------


def test_the_scan_reads_the_server_copy_of_the_index():
    from openavc import main

    assert main.discovery_engine.community_index is driver_catalog.index_cache()


def test_every_catalog_install_address_is_built_one_way():
    assert driver_catalog.catalog_file_url(ENTRY) == (
        f"{COMMUNITY_REPO_URL}/utility/acme_scanned_widget.avcdriver"
    )
    assert driver_catalog.catalog_file_url({"id": DRIVER_ID}) is None
    assert driver_catalog.catalog_file_url(None) is None


def test_the_first_entry_for_an_id_is_the_one_read():
    first = {"id": DRIVER_ID, "file": "utility/first.avcdriver"}
    second = {"id": DRIVER_ID, "file": "utility/second.avcdriver"}
    assert driver_catalog.entries_by_id([first, second, "not an entry", {"id": ""}]) == {
        DRIVER_ID: first,
    }


@pytest.fixture
def device_page(monkeypatch):
    """A project with one device whose driver is missing."""
    from openavc.api.routes import devices as device_routes

    eng = MagicMock()
    eng.devices.get_missing_drivers.return_value = [DRIVER_ID]
    eng.devices.list_devices.return_value = [
        {"id": "lobby_widget", "driver": DRIVER_ID, "orphaned": True},
    ]
    eng.installed_equipment.describe_missing_reference.return_value = None

    async def _none():
        return []

    eng.devices.retry_all_orphans = _none
    monkeypatch.setattr(device_routes, "_get_engine", lambda: eng)
    return eng


async def test_the_missing_driver_list_reads_the_server_copy(index, device_page):
    from openavc.api.routes.devices import list_missing_drivers

    answer = await list_missing_drivers()

    assert index.reads == 1
    assert answer["missing"][0]["community_match"]["file_url"] == (
        f"{COMMUNITY_REPO_URL}/utility/acme_scanned_widget.avcdriver"
    )


async def test_install_missing_reads_the_server_copy_once_a_press(index, device_page, monkeypatch):
    from openavc.api.models import InstallMissingDriversRequest
    from openavc.api.routes import drivers as driver_routes
    from openavc.api.routes.devices import install_missing_drivers

    async def _install(request):
        return {"status": "installed", "activated_devices": []}

    index.drivers = []
    monkeypatch.setattr(driver_routes, "install_community_driver", _install)
    answer = await install_missing_drivers(InstallMissingDriversRequest(
        driver_ids=[DRIVER_ID, "acme_other", "acme_third"],
    ))

    assert index.reads == 1
    assert [f["driver_id"] for f in answer["failed"]] == [DRIVER_ID, "acme_other", "acme_third"]


async def test_install_missing_reads_the_server_copy(index, device_page, monkeypatch):
    from openavc.api.models import InstallMissingDriversRequest
    from openavc.api.routes import drivers as driver_routes
    from openavc.api.routes.devices import install_missing_drivers

    asked: list = []

    async def _install(request):
        asked.append(request)
        return {"status": "installed", "activated_devices": ["lobby_widget"]}

    monkeypatch.setattr(driver_routes, "install_community_driver", _install)
    answer = await install_missing_drivers(InstallMissingDriversRequest(driver_ids=[DRIVER_ID]))

    assert index.reads == 1
    assert answer["installed"] == [DRIVER_ID]
    assert [(r.file_url, r.min_platform_version) for r in asked] == [(
        f"{COMMUNITY_REPO_URL}/utility/acme_scanned_widget.avcdriver", "0.30.0",
    )]

