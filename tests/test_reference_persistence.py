"""A host reference must survive file consumers before the first user save."""

import io
import json
from pathlib import Path
from unittest.mock import AsyncMock
import zipfile

import pytest

from openavc.core import project_library as library
from openavc.core import project_loader
from openavc.core.backup_manager import create_backup
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig
from openavc.core.project_migration import CURRENT_VERSION
from openavc.drivers import registry


DRIVER = 'openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232'


class ProjectLoaded(Exception):
    """Stop the focused startup test before device and programming startup."""


@pytest.fixture
def host(tmp_path, monkeypatch):
    path = tmp_path / 'host.json'
    path.write_text(json.dumps({'device_id': 'local', 'identity': 'unit-b', 'integration': 'acme_amplifier',
                               'name': 'Built-in amplifier', 'connection': {'token': 'nonfunctional-host-marker'}}))
    monkeypatch.setenv('OPENAVC_EQUIPMENT_MANIFEST', str(path))
    monkeypatch.setattr(registry, '_DRIVER_REGISTRY', dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, '_RESERVED_DRIVERS', set(registry._RESERVED_DRIVERS))
    monkeypatch.setattr(library.config, 'SAVED_PROJECTS_DIR', tmp_path / 'library')
    return path


def document(collision=False, present=False):
    devices = []
    if collision:
        devices.append({'id': 'local', 'name': 'Existing ordinary device', 'driver': 'generic_tcp', 'enabled': False})
    if present:
        devices.append({'id': 'amplifier', 'name': 'My amplifier', 'driver': DRIVER, 'enabled': False,
                        'config': {'integration': 'acme_amplifier'}})
    return {'openavc_version': CURRENT_VERSION, 'project': {'id': 'hall', 'name': 'Hall'}, 'devices': devices,
            'macros': [{'id': 'operate', 'name': 'Operate', 'steps': [{'action': 'device.command', 'device': 'local', 'command': 'mute'}]}],
            'isc': {'enabled': False}}


async def load_before_hardware(engine, monkeypatch):
    def loaded():
        raise ProjectLoaded()
    monkeypatch.setattr(engine, '_apply_project_settings', loaded)
    with pytest.raises(ProjectLoaded):
        await engine.start()


@pytest.mark.parametrize('collision', [False, True])
async def test_first_start_materializes_reference_for_export_backup_and_save_as(host, tmp_path, monkeypatch, collision):
    path = tmp_path / 'project.avc'
    initial = document(collision=collision)
    path.write_text(json.dumps(initial))
    engine = Engine(str(path))
    try:
        await load_before_hardware(engine, monkeypatch)
        expected_id = 'local_2' if collision else 'local'
        exported, _, _ = library.export_active_project(path)
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            saved = json.loads(archive.read('project.avc'))
        assert [d['id'] for d in saved['devices'] if d['driver'] == DRIVER] == [expected_id]
        assert saved['macros'][0]['steps'][0]['device'] == 'local'
        assert 'nonfunctional-host-marker' not in json.dumps(saved)
        assert json.loads(host.read_text())['identity'] == 'unit-b'
        backup = create_backup(path, 'Reference persistence')
        assert backup is not None
        with zipfile.ZipFile(backup) as archive:
            backed_up = json.loads(archive.read('project.avc'))
        assert backed_up['devices'] == saved['devices']
        # Save As already uses the running project, unlike Export and backups.
        library.save_to_library('retained', engine.project, path.parent / 'scripts', 'Retained', '')
        retained, _ = library.get_project('retained')
        assert retained['devices'] == saved['devices']
    finally:
        await engine.stop()


@pytest.mark.parametrize('embedded', [False, True])
async def test_unchanged_project_is_not_rewritten_on_start(host, tmp_path, monkeypatch, embedded):
    if not embedded:
        monkeypatch.delenv('OPENAVC_EQUIPMENT_MANIFEST')
    path = tmp_path / 'project.avc'
    path.write_text(json.dumps(document(present=embedded), separators=(',', ':')))
    before = path.read_bytes()
    engine = Engine(str(path))
    try:
        await load_before_hardware(engine, monkeypatch)
        assert path.read_bytes() == before
        assert not path.with_suffix('.avc.bak').exists()
    finally:
        await engine.stop()


async def test_reload_materializes_new_reference_and_preserves_colliding_device(host, tmp_path, monkeypatch):
    path = tmp_path / 'project.avc'
    path.write_text(json.dumps(document(collision=True)))
    engine = Engine(str(path))
    engine.project = ProjectConfig(**document(present=True))
    monkeypatch.setattr(engine, '_reconcile', AsyncMock())
    try:
        await engine.reload_project()
        saved = json.loads(path.read_text())
        assert [(d['id'], d['driver']) for d in saved['devices']] == [('local_2', DRIVER), ('local', 'generic_tcp')]
        assert saved['macros'][0]['steps'][0]['device'] == 'local'
        assert engine.installed_equipment.owns('local_2')
        assert 'nonfunctional-host-marker' not in path.read_text()
        before = path.read_bytes()
        await engine.reload_project()
        assert path.read_bytes() == before
    finally:
        await engine.stop()


async def test_failed_materialization_preserves_file_and_stops_before_device_start(host, tmp_path, monkeypatch):
    path = tmp_path / 'project.avc'
    path.write_text(json.dumps(document()))
    before = path.read_bytes()
    original_copy = project_loader.shutil.copy2

    def unavailable(source, destination, *args, **kwargs):
        if Path(source) == path:
            raise PermissionError('Invented project backup write failure')
        return original_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(project_loader.shutil, 'copy2', unavailable)
    engine = Engine(str(path))
    try:
        with pytest.raises(OSError):
            await engine.start()
        assert path.read_bytes() == before
        assert engine.devices.get_device_configs() == {}
        assert not engine.state.get('system.started')
    finally:
        await engine.stop()
