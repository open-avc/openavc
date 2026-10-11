"""Project controls survive persistence without changing the host hardware mapping."""

import json

import pytest

from openavc.core.installed_equipment import InstalledEquipment, InstalledEquipmentError
from openavc.api.models import DeviceUpdateRequest
from openavc.api.routes import devices as routes
from openavc.core.engine import Engine
from openavc.core.project_loader import DeviceConfig, ProjectConfig, load_project, save_project
from openavc.drivers import registry


DRIVER = 'openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232'


@pytest.fixture
def host(tmp_path, monkeypatch):
    manifest = {"device_id": "local", "identity": "product-one", "integration": "acme_amplifier", "name": "Built-in amplifier",
                "connection": {"private_connection": "host-owned"}}
    path = tmp_path / "host.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(path))
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    return InstalledEquipment.from_environment(), tmp_path / "project.avc"


def project(policy):
    return policy.compose(ProjectConfig(project={"id": "hall", "name": "Hall"}))


@pytest.mark.parametrize("enabled", [False, True])
def test_project_control_and_pending_settings_survive_save_reload(host, enabled):
    policy, path = host
    before = project(policy)
    local = before.devices[0]
    local.enabled = enabled
    local.name = "Room amplifier"
    local.pending_settings = {"output_level": -12}
    save_project(path, before)
    after = policy.compose(load_project(path))
    assert after.devices[0].enabled is enabled
    assert after.devices[0].pending_settings == {"output_level": -12}
    assert after.devices[0].name == "Room amplifier"
    assert after.devices[0].config == {"integration": "acme_amplifier"}
    assert policy.resolve(after.devices[0])["config"] == {
        "private_connection": "host-owned", "identity": "product-one", "integration": "acme_amplifier",
    }
    assert "host-owned" not in path.read_text()
    assert "private_connection" not in path.read_text()


async def test_normal_edit_route_disables_and_reenables_builtin_equipment(host, monkeypatch):
    policy, path = host
    engine = Engine(str(path))
    engine.project = project(policy)
    save_project(path, engine.project)
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    try:
        await routes.update_device("local", DeviceUpdateRequest(enabled=False))
        assert load_project(path).devices[0].enabled is False
        assert engine.project.devices[0].enabled is False
        assert engine.devices.get_device_info("local")["ownership"] == "manufacturer"
        assert next(d for d in engine.devices.list_devices() if d["id"] == "local")["ownership"] == "manufacturer"
        await routes.update_device("local", DeviceUpdateRequest(enabled=True))
        assert load_project(path).devices[0].enabled is True
        assert engine.project.devices[0].driver == DRIVER
        assert engine.devices.get_device_info("local")["ownership"] == "manufacturer"
    finally:
        await engine.stop()


@pytest.mark.parametrize("mutation", ["identity", "driver", "connection", "child_mapping", "duplicate"])
def test_project_controls_do_not_allow_hardware_replacement(host, mutation):
    policy, path = host
    value = project(policy)
    save_project(path, value)
    original = path.read_bytes()
    if mutation == "identity":
        value.devices[0].config["identity"] = "another-box"
    elif mutation == "driver":
        value.devices[0].driver = "generic_tcp"
    elif mutation == "connection":
        value.connections["local"] = {"host": "127.0.0.1", "port": 9}
    elif mutation == "child_mapping":
        value.devices[0] = DeviceConfig.model_validate({
            **value.devices[0].model_dump(),
            "child_entities": {"channel": {"1": {"config": {"address": 99}}}},
        })
    else:
        value.devices.append(DeviceConfig(id="copy", name="Copy", driver="generic_tcp", config={"identity": "product-one"}))
    with pytest.raises(InstalledEquipmentError):
        save_project(path, value)
    assert path.read_bytes() == original


def test_disabling_does_not_allow_removal_or_uninstall(host):
    policy, path = host
    value = project(policy)
    value.devices[0].enabled = False
    save_project(path, value)
    with pytest.raises(InstalledEquipmentError, match="cannot be uninstalled"):
        policy.refuse_removal("local")
    policy.register()
    with pytest.raises(InstalledEquipmentError, match="cannot be replaced or uninstalled"):
        registry.unregister_driver(DRIVER)
    value.devices.clear()
    save_project(path, value)
    assert load_project(path).devices[0].id == "local"


def test_hostless_projects_keep_ordinary_editable_device_controls(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAVC_EQUIPMENT_MANIFEST", raising=False)
    value = ProjectConfig(project={"id": "ordinary", "name": "Ordinary"}, devices=[
        DeviceConfig(id="display", name="Display", driver="generic_tcp", enabled=False, pending_settings={"power": "on"}),
    ])
    assert InstalledEquipment().compose(value) is value
    path = tmp_path / "project.avc"
    save_project(path, value)
    after = load_project(path)
    assert after.devices == value.devices
