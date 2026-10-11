"""Explicit replacement retains programming without replacing physical ownership."""

import json
from unittest.mock import AsyncMock

import pytest

from openavc.api.models import DeviceUpdateRequest
from openavc.api.routes import devices as routes
from openavc.core.engine import Engine
from openavc.core.installed_equipment import InstalledEquipmentError, model_driver_id
from openavc.core.project_loader import ProjectConfig, load_project, save_project
from openavc.drivers import registry
from tests.test_ai_device_tools_hardening import _pure_handler


@pytest.fixture
def equipment(tmp_path, monkeypatch):
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"device_id": "local", "identity": "receiving-unit",
        "integration": "acme_amplifier_v2", "name": "Built-in amplifier", "connection": {"token": "host-owned"}}))
    monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(manifest))
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    path = tmp_path / "project.avc"
    engine = Engine(str(path))
    source = ProjectConfig.model_validate({"project": {"id": "hall", "name": "Hall"},
        "devices": [{"id": "local", "name": "House amplifier", "driver": model_driver_id("acme_amplifier"),
                     "config": {"integration": "acme_amplifier"}, "enabled": False,
                     "pending_settings": {"output_level": -12},
                     "child_entities": {"channel": {"1": {"label": "House"}}}}],
        "macros": [{"id": "mute", "name": "Mute", "steps": [
            {"action": "device.command", "device": "local", "command": "set_mute", "params": {"mute": True}}]}],
        "scripts": [{"id": "control", "file": "control.py"}],
    })
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/control.py").write_text('equipment = "local"\n')
    engine.project = engine.installed_equipment.compose(source, for_load=True)
    save_project(path, engine.project)
    monkeypatch.setattr(engine, "_reconcile", AsyncMock())
    information = {"connected": True, "driver_info": {
        "commands": {"set_mute": {"params": {"mute": {"type": "boolean", "required": True}}}},
        "child_entity_types": {"channel": {}},
        "device_settings": {"output_level": {"type": "number", "min": -80, "max": 12}},
    }}
    monkeypatch.setattr(engine.devices, "get_device_info", lambda _: information)
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    return engine, information, manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["rest", "ai"])
async def test_use_builtin_keeps_programming_and_protects_reassigned_id(equipment, monkeypatch, door):
    engine, _, manifest = equipment
    before = engine.project.model_dump()
    hardware = manifest.read_bytes()
    if door == "rest":
        await routes.update_device("local", DeviceUpdateRequest(driver=engine.installed_equipment.driver_id, config={}))
    else:
        handler = _pure_handler()
        monkeypatch.setattr(handler, "_get_engine", lambda: engine)
        result = await handler._update_device({"device_id": "local", "driver": engine.installed_equipment.driver_id})
        assert "error" not in result
    after = load_project(engine.project_path)
    assert [device.id for device in after.devices] == ["local"]
    assert after.devices[0].name == "House amplifier"
    assert after.devices[0].enabled is False
    assert after.devices[0].pending_settings == {"output_level": -12}
    assert after.devices[0].child_entities["channel"]["1"].label == "House"
    assert after.macros[0].steps[0].device == "local"
    assert after.model_dump()["macros"] == before["macros"]
    assert (engine.project_path.parent / "scripts/control.py").read_text() == 'equipment = "local"\n'
    assert manifest.read_bytes() == hardware
    assert b"host-owned" not in engine.project_path.read_bytes()
    assert engine.installed_equipment.owns("local")
    assert not engine.installed_equipment.owns("local_2")
    with pytest.raises(InstalledEquipmentError, match="cannot be uninstalled"):
        await routes.delete_device("local")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["command", "required", "parameter", "setting", "setting_value", "channel", "offline", "target_used"])
async def test_incompatible_or_used_equipment_preserves_everything(equipment, failure):
    engine, information, manifest = equipment
    if failure == "command":
        information["driver_info"]["commands"] = {}
    elif failure == "required":
        information["driver_info"]["commands"]["set_mute"]["params"]["channel"] = {"type": "integer", "required": True}
    elif failure == "setting":
        information["driver_info"]["device_settings"] = {}
    elif failure == "parameter":
        information["driver_info"]["commands"]["set_mute"]["params"]["mute"] = {"type": "integer", "min": 5, "required": True}
    elif failure == "setting_value":
        information["driver_info"]["device_settings"]["output_level"]["min"] = 0
    elif failure == "channel":
        information["driver_info"]["child_entity_types"] = {}
    elif failure == "offline":
        information["connected"] = False
    else:
        engine.project.macros[0].steps[0].device = "local_2"
    before = engine.project.model_dump()
    disk, host = engine.project_path.read_bytes(), manifest.read_bytes()
    with pytest.raises(InstalledEquipmentError):
        await routes.update_device("local", DeviceUpdateRequest(driver=engine.installed_equipment.driver_id))
    assert engine.project.model_dump() == before
    assert engine.project_path.read_bytes() == disk
    assert manifest.read_bytes() == host
    engine._reconcile.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"name": 7}, {"enabled": "perhaps"}])
async def test_reassignment_validates_device_fields_before_applying(equipment, changes):
    engine, _, _ = equipment
    before = engine.project.model_dump()
    with pytest.raises(ValueError):
        await engine.reassign_installed_equipment("local", **changes)
    assert engine.project.model_dump() == before
    engine._reconcile.assert_not_awaited()
