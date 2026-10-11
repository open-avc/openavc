"""Use the actual library and backup paths to move programming between units."""

import io
import json
import zipfile

import pytest

from openavc.core.installed_equipment import InstalledEquipment, InstalledEquipmentError
from openavc.core import project_library as library
from openavc.core.backup_manager import restore_from_backup
from openavc.core.project_loader import DeviceConfig, ProjectConfig, load_project, save_project


DRIVER = 'openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", tmp_path / "saved")

    def set_host(identity, integration="acme_amplifier"):
        if identity is None:
            monkeypatch.delenv("OPENAVC_EQUIPMENT_MANIFEST", raising=False)
        else:
            manifest = tmp_path / "host.json"
            manifest.write_text(json.dumps({
                "device_id": "local", "identity": identity, "integration": integration,
                "name": "Built-in amplifier", "connection": {"token": identity + "-private-token"},
            }))
            monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(manifest))
        return InstalledEquipment.from_environment()

    policy = set_host("unit-a")
    value = policy.compose(ProjectConfig(project={"id": "hall", "name": "Hall"}, macros=[{
        "id": "mute", "name": "Mute", "steps": [{
            "action": "device.command", "device": "local", "command": "mute", "params": {},
        }],
    }]))
    value.devices[0].name = "House amplifier"
    value.devices[0].enabled = False
    source = tmp_path / "source.avc"
    save_project(source, value)
    return tmp_path, set_host, source.read_bytes()


def content(raw, zipped):
    if not zipped:
        return raw, "hall.avc"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.avc", raw)
        archive.writestr("assets/label.txt", b"project asset")
    return buffer.getvalue(), "hall.zip"


@pytest.mark.parametrize("zipped", [False, True])
def test_replacement_unit_uses_its_own_hardware_and_preserves_programming(workspace, zipped):
    root, set_host, raw = workspace
    assert b"unit-a" not in raw and b"private-token" not in raw
    policy = set_host("unit-b")
    data, name = content(raw, zipped)
    imported = library.import_project(data, name)
    target = root / "active/project.avc"
    target.parent.mkdir()
    project = library.open_from_library(imported["id"], target, target.parent / "scripts", "new-hall", "New hall")
    local = project.devices[0]
    assert local.id == "local" and local.name == "House amplifier" and local.enabled is False
    assert project.macros[0].steps[0].device == "local"
    resolved = policy.resolve(local)
    assert resolved["config"]["identity"] == "unit-b"
    assert resolved["config"]["token"] == "unit-b-private-token"
    assert resolved["config"]["integration"] == "acme_amplifier"
    assert b"unit-b" not in target.read_bytes()
    if zipped:
        assert (target.parent / "assets/label.txt").read_bytes() == b"project asset"


def test_ordinary_controller_opens_reference_for_normal_reassignment(workspace):
    root, set_host, raw = workspace
    set_host(None)
    imported = library.import_project(raw, "hall.avc")
    target = root / "active/project.avc"
    target.parent.mkdir()
    project = library.open_from_library(imported["id"], target, target.parent / "scripts", "new-hall", "New hall")
    assert project.devices[0].driver == DRIVER
    assert any(item["driver_id"] == DRIVER for item in imported["missing_drivers"])
    # Normal reassignment preserves the project-local ID used by automation.
    project.devices[0] = DeviceConfig(id="local", name="Remote amplifier", driver="generic_tcp", config={})
    project.connections["local"] = {"host": "192.0.2.30", "port": 4001}
    save_project(target, project)
    reopened = load_project(target)
    assert reopened.macros[0].steps[0].device == "local"
    assert reopened.connections["local"]["host"] == "192.0.2.30"


@pytest.mark.parametrize("zipped", [False, True])
def test_replacement_backup_uses_current_host(workspace, zipped):
    root, set_host, raw = workspace
    policy = set_host("unit-b")
    data, _ = content(raw, zipped)
    backup = root / ("saved.zip" if zipped else "saved.avc.bak")
    backup.write_bytes(data)
    target = root / "active/project.avc"
    target.parent.mkdir()
    target.write_text("before")
    restore_from_backup(backup, target)
    value = policy.compose(load_project(target))
    assert value.devices[0].enabled is False
    assert policy.resolve(value.devices[0])["config"]["identity"] == "unit-b"
    assert value.macros[0].steps[0].device == "local"


def test_different_model_preserves_foreign_programming_without_using_host_hardware(workspace):
    root, set_host, raw = workspace
    policy = set_host("other-unit", "acme_switcher")
    imported = library.import_project(raw, "hall.avc")
    target = root / "active/project.avc"
    target.parent.mkdir()
    target.write_bytes(b"active project sentinel")
    project = library.open_from_library(imported["id"], target, target.parent / "scripts", "new", "New")
    assert [device.id for device in project.devices] == ["local_2", "local"]
    local, foreign = project.devices
    assert policy.matches(local) and policy.resolve(local)["config"]["identity"] == "other-unit"
    assert foreign.driver == DRIVER and policy.resolve(foreign) is None
    assert foreign.name == "House amplifier" and foreign.enabled is False
    assert project.macros[0].steps[0].device == "local"
    assert load_project(target).devices == project.devices


def test_missing_manufacturer_model_is_not_guessed():
    with pytest.raises(InstalledEquipmentError, match="no built-in equipment model"):
        InstalledEquipment({"device_id": "local", "identity": "unit-a", "name": "Amplifier"})
