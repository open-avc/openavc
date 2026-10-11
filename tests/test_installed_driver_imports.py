"""Installed-equipment imports refuse file mutations before writing anything."""
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import zipfile

import pytest

from openavc.api.models import CommunityDriverInstallRequest, PythonDriverCreateRequest
from openavc.api.routes import drivers, python_drivers
from openavc.core.installed_equipment import InstalledEquipmentError
from openavc.drivers import registry

PROTECTED = 'acme_installed'


def source(identity):
    return f'class AcmeDriver:\n    DRIVER_INFO = {{"id": "{identity}"}}\n'.encode()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, '_RESERVED_DRIVERS', {PROTECTED})
    monkeypatch.setattr(drivers, '_get_driver_repo_dir', lambda: tmp_path)
    monkeypatch.setattr('openavc.system_config.DRIVER_REPO_DIR', tmp_path)
    return tmp_path


def upload(filename, data):
    return SimpleNamespace(form=AsyncMock(return_value={
        'file': SimpleNamespace(filename=filename, read=AsyncMock(return_value=data))}))


def tree(repo):
    return {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize('bundle', [False, True])
@pytest.mark.parametrize('kind', ['python_id', 'yaml_id', 'filename', 'companion_id'])
async def test_import_refuses_before_touching_any_files(repo, bundle, kind):
    if kind == 'companion_id' and not bundle:
        pytest.skip('Companions arrive with their bundle')
    entries = {'acme_kept.py': source(PROTECTED)}
    if kind == 'yaml_id':
        entries = {'acme_kept.avcdriver': f'id: {PROTECTED}\n'.encode()}
    elif kind == 'filename':
        entries = {f'{PROTECTED}.py': source('acme_ordinary')}
    elif kind == 'companion_id':
        entries = {'acme_kept.py': source('acme_kept'), 'acme_kept_sim.py': source(PROTECTED)}
    for name in entries:
        (repo / name).write_bytes(b'# previous file\n')
    (repo / 'unrelated.py').write_bytes(b'# unrelated\n')
    before = tree(repo)
    if bundle:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        call = drivers.upload_driver_bundle(upload('driver.zip', buf.getvalue()))
    else:
        name, data = next(iter(entries.items()))
        call = drivers.upload_driver(upload(name, data))
    with pytest.raises(InstalledEquipmentError, match='manufacturer'):
        await call
    assert tree(repo) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['save', 'create', 'reload'])
async def test_python_source_alias_cannot_claim_installed_identity(repo, operation):
    path = repo / 'acme_kept.py'
    path.write_bytes(source(PROTECTED) if operation == 'reload' else b'# previous\n')
    before = tree(repo)
    if operation == 'save':
        call = python_drivers.save_python_driver_source('acme_kept', {'source': source(PROTECTED).decode()})
    elif operation == 'create':
        call = python_drivers.create_python_driver(PythonDriverCreateRequest(id='acme_new', source=source(PROTECTED).decode()))
    else:
        call = python_drivers.reload_python_driver_endpoint('acme_kept')
    with pytest.raises(InstalledEquipmentError, match='manufacturer'):
        await call
    assert tree(repo) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['delete', 'uninstall', 'reload', 'update', 'install', 'save', 'create'])
async def test_direct_protected_id_refused_before_io(repo, operation):
    (repo / f'{PROTECTED}.py').write_bytes(b'# sentinel\n')
    before = tree(repo)
    calls = {
        'delete': lambda: python_drivers.delete_python_driver(PROTECTED),
        'uninstall': lambda: drivers.uninstall_driver(PROTECTED),
        'reload': lambda: python_drivers.reload_python_driver_endpoint(PROTECTED),
        'update': lambda: drivers.update_driver(PROTECTED, None),
        'install': lambda: drivers.install_community_driver(CommunityDriverInstallRequest(driver_id=PROTECTED, file_url='https://example.com/unused.py')),
        'save': lambda: python_drivers.save_python_driver_source(PROTECTED, {'source': '# replacement'}),
        'create': lambda: python_drivers.create_python_driver(PythonDriverCreateRequest(id=PROTECTED, source='# replacement')),
    }
    with pytest.raises(InstalledEquipmentError, match='manufacturer'):
        await calls[operation]()
    assert tree(repo) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['install', 'update'])
async def test_downloaded_alias_refused_before_old_driver_changes(repo, monkeypatch, operation):
    path = repo / 'acme_kept.py'
    path.write_bytes(source('acme_kept'))
    before = tree(repo)
    content = source(PROTECTED)
    response = MagicMock(content=content, text=content.decode())
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    monkeypatch.setattr('httpx.AsyncClient', lambda **kw: client)
    monkeypatch.setattr(drivers, '_catalog_hashes', AsyncMock(return_value=None))
    monkeypatch.setattr(drivers, '_verify_against_catalog', lambda *args: None)
    url = 'https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/acme_kept.py'
    if operation == 'install':
        call = drivers.install_community_driver(CommunityDriverInstallRequest(driver_id='acme_kept', file_url=url))
    else:
        call = drivers.update_driver('acme_kept', SimpleNamespace(json=AsyncMock(return_value={'file_url': url})))
    with pytest.raises(InstalledEquipmentError, match='manufacturer'):
        await call
    assert tree(repo) == before


@pytest.mark.asyncio
async def test_ordinary_incomplete_source_remains_saveable(repo):
    path = repo / 'acme_kept.py'
    path.write_bytes(b'# original\n')
    result = await python_drivers.save_python_driver_source('acme_kept', {'source': 'def unfinished('})
    assert result['status'] == 'saved'
    assert result['syntax_error']
    assert path.read_text() == 'def unfinished('


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['filename', 'identity', 'companion'])
@pytest.mark.parametrize('door', ['import', 'install', 'saved_bundle'])
async def test_project_bundle_refusal_precedes_all_project_and_driver_writes(repo, monkeypatch, kind, door):
    import json
    from openavc.api.routes.project import import_library_project
    from openavc.core import project_library as library

    lib = repo / 'library'
    lib.mkdir()
    driver_dir = repo / 'drivers'
    driver_dir.mkdir()
    monkeypatch.setattr(library.config, 'SAVED_PROJECTS_DIR', lib)
    monkeypatch.setattr(library, '_DRIVER_REPO_DIR', driver_dir)
    ordinary = driver_dir / 'acme_kept.py'
    ordinary.write_bytes(b'# existing ordinary file\n')
    entries = {'project.avc': json.dumps({'openavc_version': '0.13.0',
               'project': {'id': 'acme_project', 'name': 'Acme project'}}).encode(),
               'scripts/acme_script.py': b'# must not be installed\n',
               'drivers/acme_new.py': source('acme_new')}
    if kind == 'filename':
        entries[f'drivers/{PROTECTED}.py'] = source('acme_alias')
    elif kind == 'identity':
        entries['drivers/acme_alias.py'] = source(PROTECTED)
    else:
        entries['drivers/acme_new_sim.py'] = source(PROTECTED)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    if door == 'saved_bundle':
        saved = lib / 'acme_project'
        saved.mkdir()
        (saved / 'bundle.zip').write_bytes(buf.getvalue())
    before = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    with pytest.raises(InstalledEquipmentError, match='manufacturer'):
        if door == 'import':
            await import_library_project(upload('project.zip', buf.getvalue()))
        elif door == 'install':
            with zipfile.ZipFile(buf) as archive:
                library._install_bundled_drivers(archive)
        else:
            library._install_bundled_from_library('acme_project')
    assert {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob('*') if p.is_file()} == before
    assert list(driver_dir.iterdir()) == [ordinary]
