"""Imported IDs survive without weakening active equipment protections."""

import io
import json
from unittest.mock import AsyncMock
import zipfile

import pytest

from openavc.core.installed_equipment import InstalledEquipmentError
from openavc.api.models import DeviceUpdateRequest
from openavc.api.routes import devices as routes
from openavc.core import project_library as library
from openavc.core.backup_manager import restore_from_backup
from openavc.core.engine import Engine
from openavc.core.project_diff import ProjectOrigin
from openavc.core.project_loader import DeviceConfig, ProjectConfig, load_project, save_project
from openavc.drivers import registry


DRIVER = 'openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232'


@pytest.fixture
def host(tmp_path, monkeypatch):
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"device_id": "local", "identity": "unit-b", "integration": "acme_amplifier",
                                    "name": "Built-in amplifier", "connection": {"token": "host-private"}}))
    monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(manifest))
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", tmp_path / "library")
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    return tmp_path


def content(device_id="local", *, builtin=False, zipped=False):
    data = {"openavc_version": "0.13.0", "project": {"id": "hall", "name": "Hall"}, "devices": [{
        "id": device_id, "name": "Imported equipment", "driver": DRIVER if builtin else "generic_tcp",
        "config": {"integration": "acme_amplifier"} if builtin else {}, "enabled": False}],
        "connections": {} if builtin else {device_id: {"host": "192.0.2.30", "port": 4001}},
        "macros": [{"id": "operate", "name": "Operate", "steps": [{"action": "device.command",
                    "device": device_id, "command": "mute", "params": {}}]}]}
    raw = json.dumps(data).encode()
    if not zipped:
        return raw, "hall.avc"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.avc", raw)
        archive.writestr("assets/imported.txt", b"imported asset")
    return buffer.getvalue(), "hall.zip"


def open_project(root, *, device_id="local", builtin=False, zipped=False):
    payload, name = content(device_id, builtin=builtin, zipped=zipped)
    imported = library.import_project(payload, name)
    path = root / "active/project.avc"
    path.parent.mkdir()
    project = library.open_from_library(imported["id"], path, path.parent / "scripts", "new", "New")
    return path, project


@pytest.mark.parametrize("zipped", [False, True])
@pytest.mark.parametrize("builtin,device_id,expected_local", [(False, "local", "local_2"), (True, "amplifier", "amplifier")])
def test_library_open_preserves_project_ids_connections_and_programming(host, zipped, builtin, device_id, expected_local):
    path, project = open_project(host, device_id=device_id, builtin=builtin, zipped=zipped)
    reopened = load_project(path)
    assert reopened.macros[0].steps[0].device == device_id
    existing = next(d for d in reopened.devices if d.id == device_id)
    assert existing.name == "Imported equipment" and not existing.enabled
    assert existing.driver == (DRIVER if builtin else "generic_tcp")
    locals_ = [d for d in reopened.devices if d.driver == DRIVER]
    assert len(locals_) == 1 and locals_[0].id == expected_local
    engine = Engine(str(path))
    engine.project = project
    assert engine.installed_equipment.owns(expected_local)
    if not builtin:
        assert not engine.installed_equipment.owns("local")
        assert reopened.connections["local"] == {"host": "192.0.2.30", "port": 4001}
    resolved = engine.installed_equipment.resolve(locals_[0])
    assert resolved["config"]["identity"] == "unit-b" and resolved["config"]["token"] == "host-private"
    assert b"host-private" not in path.read_bytes()
    save_project(path, reopened)
    assert [d.id for d in load_project(path).devices] == [d.id for d in reopened.devices]
    if zipped:
        assert (path.parent / "assets/imported.txt").read_bytes() == b"imported asset"


@pytest.mark.parametrize("zipped", [False, True])
def test_backup_with_ordinary_local_id_reopens_without_replacing_that_device(host, zipped):
    raw, _ = content(zipped=zipped)
    backup = host / ("backup.zip" if zipped else "backup.avc.bak")
    backup.write_bytes(raw)
    path = host / "active.avc"
    path.write_text('{"project":{"id":"before","name":"Before"}}')
    restore_from_backup(backup, path)
    engine = Engine(str(path))
    # Startup uses this same load/composition path.
    engine.project = engine.installed_equipment.compose(engine._load_project_safe(), for_load=True)
    assert [(d.id, d.driver) for d in engine.project.devices] == [("local_2", DRIVER), ("local", "generic_tcp")]
    assert engine.project.macros[0].steps[0].device == "local"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["delete", "replace", "rename-id", "connection", "mapping"])
async def test_active_project_edits_cannot_substitute_or_remove_relocated_equipment(host, monkeypatch, mutation):
    path, project = open_project(host)
    engine = Engine(str(path))
    engine.project = project
    monkeypatch.setattr(engine, "_reconcile", AsyncMock())
    proposed = project.model_copy(deep=True)
    local = next(d for d in proposed.devices if d.id == "local_2")
    if mutation == "delete":
        proposed.devices.remove(local)
    elif mutation == "replace":
        local.driver = "generic_tcp"
        local.config = {}
    elif mutation == "rename-id":
        local.id = "other"
    elif mutation == "connection":
        proposed.connections["local_2"] = {"host": "192.0.2.31", "port": 4001}
    else:
        local.config["identity"] = "another-unit"
    before = path.read_bytes()
    with pytest.raises(InstalledEquipmentError):
        await engine.apply_project(proposed, origin=ProjectOrigin.EDIT)
    assert path.read_bytes() == before and engine.project is project
    assert engine.installed_equipment.owns("local_2") and not engine.installed_equipment.owns("local")
    engine._reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_delete_and_disable_routes_use_the_relocated_owner(host, monkeypatch):
    path, project = open_project(host)
    engine = Engine(str(path))
    engine.project = project
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    try:
        with pytest.raises(InstalledEquipmentError):
            await routes.delete_device("local_2")
        await routes.update_device("local_2", DeviceUpdateRequest(enabled=False))
        assert engine.devices.get_device_info("local_2")["ownership"] == "manufacturer"
        assert next(d for d in load_project(path).devices if d.id == "local_2").enabled is False
        await routes.delete_device("local")
        assert all(d.id != "local" for d in engine.project.devices)
        assert engine.installed_equipment.owns("local_2")
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_failed_reconcile_keeps_ownership_on_the_runtime_project(host, monkeypatch):
    path, project = open_project(host, device_id="amplifier", builtin=True)
    engine = Engine(str(path))
    engine.project = project
    proposed = ProjectConfig(project={"id": "next", "name": "Next"}, devices=[
        DeviceConfig(id="local", name="Other equipment", driver="generic_tcp", enabled=False)])
    async def fail(diff, origin):
        assert engine.installed_equipment.owns("local_2")
        assert not engine.installed_equipment.owns("amplifier")
        raise RuntimeError("injected reconcile failure")
    monkeypatch.setattr(engine, "_reconcile", fail)
    monkeypatch.setattr(engine, "_rollback_reconcile", AsyncMock())
    with pytest.raises(RuntimeError, match="injected reconcile failure"):
        await engine.apply_project(proposed, origin=ProjectOrigin.LOAD)
    assert engine.project is project and engine.installed_equipment.owns("amplifier")
    assert not engine.installed_equipment.owns("local_2")
    # Ordinary core preserves the new file while rolling back runtime state.
    assert any(d.id == "local_2" for d in load_project(path).devices)
    engine._rollback_reconcile.assert_awaited_once()
