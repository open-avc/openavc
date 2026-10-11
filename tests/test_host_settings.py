"""Firmware-held settings retain upstream refusal and secret handling."""

import pytest

from openavc.updater.host import FirmwareHost
from tests.test_system_config_env_held import client as client


@pytest.fixture(params=[None, FirmwareHost("Fixture 1"), FirmwareHost(error="ValueError")])
def owner(request, monkeypatch):
    monkeypatch.setattr("openavc.updater.host.get_firmware_host", lambda: request.param)
    return request.param


def test_held_setting_owner_and_deployment_remain_distinct(client, owner):
    c, _ = client(OPENAVC_BIND="0.0.0.0")
    env = c.get("/api/system/config").json()["_environment"]
    assert env["deployment_type"] == "git_dev"
    assert env["overrides"]["network.bind_address"] == "OPENAVC_BIND"
    assert env.get("configuration_owner") == ("firmware" if owner else None)


def test_held_setting_refuses_before_any_write(client, owner, tmp_path):
    c, cfg = client(OPENAVC_BIND="0.0.0.0")
    before = cfg.to_dict()
    path = tmp_path / "system.json"
    before_file = path.read_bytes() if path.exists() else None
    response = c.patch("/api/system/config", json={
        "network": {"bind_address": "192.0.2.5"}, "logging": {"level": "debug"},
    })
    assert response.status_code == 409
    message = response.json()["detail"]
    if owner:
        assert "managed by the product's firmware" in message
        assert "Contact product support" in message
        assert "OPENAVC_" not in message and "server's environment" not in message
    else:
        assert "OPENAVC_BIND" in message
    assert cfg.to_dict() == before
    assert (path.read_bytes() if path.exists() else None) == before_file


def test_ordinary_editable_field_still_saves(client, owner):
    c, cfg = client(OPENAVC_BIND="0.0.0.0")
    response = c.patch("/api/system/config", json={"logging": {"level": "debug"}})
    assert response.status_code == 200
    assert cfg.get("logging", "level") == "debug"


def test_owner_cannot_be_set_by_a_config_patch(client, owner):
    c, _ = client(OPENAVC_BIND="0.0.0.0")
    assert c.patch("/api/system/config", json={
        "_environment": {"configuration_owner": "forged"},
    }).status_code == 200
    data = c.get("/api/system/config").json()
    assert data["_environment"].get("configuration_owner") == ("firmware" if owner else None)
