"""Discovery's add refuses a required setting the scan cannot supply.

Add Device and Edit Device refuse a driver's required field left blank; adding
a scanned device did not, so Install & Add saved a device that could never
connect (an API key, a product's password). Now the add answers with the
field's label, Install & Add reports which settings it needs after the driver
is installed, and the card asks for them.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, ProjectMeta
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


@pytest.fixture
def vault_driver():
    """An invented driver with a required API key and no default for it."""

    class _AcmeVault(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_vault_test",
            "name": "Acme Vault (test)",
            "transport": "tcp",
            "default_config": {"host": "", "port": 8443, "api_key": ""},
            "config_schema": {
                "host": {"type": "string", "label": "Host", "required": True},
                "port": {"type": "integer", "label": "Port", "default": 8443},
                "api_key": {"type": "string", "label": "API Key", "required": True, "secret": True},
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

    register_driver(_AcmeVault)
    yield _AcmeVault
    unregister_driver("acme_vault_test")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from openavc.api import discovery as discovery_api

    fake_discovery = MagicMock()
    fake_discovery.results = {}
    discovery_api.set_discovery_engine(fake_discovery)
    eng = Engine(str(tmp_path / "test.avc"))
    eng.project = ProjectConfig(project=ProjectMeta(id="t", name="Test"), devices=[], connections={})
    monkeypatch.setattr("openavc.core.project_loader.save_project", lambda *a, **k: None)
    discovery_api.set_app_engine(eng)
    return eng


async def test_a_blank_required_setting_is_refused_by_name(engine, vault_driver):
    from openavc.api.discovery import AddDeviceRequest, add_device

    with pytest.raises(HTTPException) as caught:
        await add_device(AddDeviceRequest(ip="192.0.2.60", driver_id="acme_vault_test"))
    assert caught.value.status_code == 422
    assert caught.value.detail == "API Key is required."
    assert engine.project.devices == []
    assert engine.project.connections == {}


async def test_the_setting_entered_on_the_card_adds_the_device(engine, vault_driver):
    from openavc.api.discovery import AddDeviceRequest, add_device

    result = await add_device(AddDeviceRequest(
        ip="192.0.2.60", driver_id="acme_vault_test", config={"api_key": "k-123"},
    ))
    assert result["status"] == "ok"
    saved = engine.project.devices[-1]
    assert saved.config["api_key"] == "k-123"
    assert engine.project.connections[saved.id]["host"] == "192.0.2.60"


async def test_install_and_add_names_the_settings_it_still_needs(engine, vault_driver, monkeypatch):
    from openavc.api import rest
    from openavc.api.discovery import InstallAndMatchRequest, install_and_match

    async def _installed(_req):
        return {"status": "installed"}

    monkeypatch.setattr(rest, "install_community_driver", _installed)
    result = await install_and_match(InstallAndMatchRequest(
        ip="192.0.2.60", driver_id="acme_vault_test",
        file_url="https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/acme_vault_test.py",
    ))
    assert result["status"] == "installed_not_added"
    assert result["missing"] == ["api_key"]
    assert result["error"] == "API Key is required."
    assert engine.project.devices == []
