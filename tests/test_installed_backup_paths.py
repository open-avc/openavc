"""Host equipment protections survive backups targeting a named project file."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import zipfile

import pytest

from openavc.core.installed_equipment import InstalledEquipment, InstalledEquipmentError
from openavc.api.routes import project as routes
from openavc.core.backup_manager import restore_from_backup
from openavc.core.project_loader import ProjectConfig


DRIVER = 'openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232'


@pytest.fixture
def host(tmp_path, monkeypatch):
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"device_id": "local", "identity": "acme-native", "integration": "acme_amplifier",
                                    "name": "Local Amplifier"}))
    monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(manifest))
    target = tmp_path / "main-hall.avc"
    target.write_text(json.dumps({"project": {"id": "hall", "name": "Before restore"}}))
    (tmp_path / "project.avc").write_bytes(b"unrelated project sentinel")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "keep.txt").write_bytes(b"asset sentinel")
    return target


def backup(target, zipped, forbidden=False):
    data = {"openavc_version": "0.13.0", "project": {"id": "hall", "name": "Restored"},
            "devices": [{"id": "local", "name": "Changed hardware", "driver": DRIVER,
                         "config": {"integration": "acme_amplifier", "url": "https://invalid.example"}}]
            if forbidden else []}
    raw = json.dumps(data).encode()
    if zipped:
        path = target.parent / "restored.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project.avc", raw)
            archive.writestr("assets/from-backup.txt", b"restored asset")
    else:
        path = target.parent / "restored.avc.bak"
        path.write_bytes(raw)
    return path, data


@pytest.mark.parametrize("zipped", [False, True])
def test_allowed_restore_targets_configured_file_and_composes_local_equipment(host, zipped):
    path, expected = backup(host, zipped)
    restore_from_backup(path, host)
    assert json.loads(host.read_text()) == expected
    assert (host.parent / "project.avc").read_bytes() == b"unrelated project sentinel"
    composed = InstalledEquipment.from_environment().compose(ProjectConfig(**expected))
    local = next(device for device in composed.devices if device.id == "local")
    assert local.driver == DRIVER and local.config == {"integration": "acme_amplifier"}


@pytest.mark.parametrize("zipped", [False, True])
def test_rejected_restore_writes_neither_project_nor_assets(host, zipped):
    path, _ = backup(host, zipped, forbidden=True)
    before = {str(p.relative_to(host.parent)): p.read_bytes()
              for p in host.parent.rglob("*") if p.is_file()}
    with pytest.raises(InstalledEquipmentError, match="identity and mapping"):
        restore_from_backup(path, host)
    assert {str(p.relative_to(host.parent)): p.read_bytes()
            for p in host.parent.rglob("*") if p.is_file()} == before


@pytest.mark.asyncio
async def test_api_rejects_before_stopping_persistence_for_named_project(host, monkeypatch):
    path, _ = backup(host, True, forbidden=True)
    engine = SimpleNamespace(project_path=host, persister=SimpleNamespace(stop=Mock()),
                             reload_project=AsyncMock(), reload_persisted_state=Mock())
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    original = host.read_bytes()
    with pytest.raises(InstalledEquipmentError, match="identity and mapping"):
        await routes.restore_backup(path.name)
    engine.persister.stop.assert_not_called()
    engine.reload_project.assert_not_awaited()
    assert host.read_bytes() == original
    saved = list((host.parent / "backups").glob("*.zip"))
    assert len(saved) == 1
    with zipfile.ZipFile(saved[0]) as archive:
        assert archive.read("project.avc") == original
