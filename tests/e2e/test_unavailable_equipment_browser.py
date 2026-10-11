"""Explain an unavailable permanent integration through the ordinary device UI."""

import json

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize('mixed', [False, True])
def test_unavailable_equipment_keeps_programming_and_normal_reassignment(server_factory, page, tmp_path, mixed):
    from openavc.core.installed_equipment import model_driver_id

    devices = [{'id': 'amp', 'name': 'Lobby amplifier', 'driver': model_driver_id('acme_amplifier'),
                'config': {'integration': 'acme_amplifier'}}]
    if mixed:
        devices.append({'id': 'other', 'name': 'Unavailable ordinary device',
                        'driver': 'acme_absent_driver', 'config': {}})
    macros = [{'id': 'operate', 'name': 'Operate amplifier', 'steps': [
        {'action': 'device.command', 'device': 'amp', 'command': 'gain', 'params': {'value': -20}},
    ]}]
    handle = server_factory(project_overrides={'devices': devices, 'macros': macros})
    before = json.loads(handle.project_path.read_text())
    page.goto(handle.base_url + '/programmer/', wait_until='domcontentloaded')
    dialog = page.get_by_role('dialog', name='Missing drivers')
    expect(dialog).to_be_visible(timeout=30000)
    expect(dialog.get_by_text('Built-in equipment unavailable', exact=True)).to_be_visible()
    expect(dialog.get_by_text('Lobby amplifier', exact=True)).to_be_visible()
    expect(dialog.get_by_text('This equipment is built into another product and is unavailable on this server.', exact=True)).to_be_visible()
    if mixed:
        expect(dialog.get_by_text('acme_absent_driver', exact=True)).to_be_visible()
        expect(dialog.get_by_text('Reassign these devices to a different driver, or upload the driver file manually from the Drivers tab.', exact=True)).to_be_visible()
    else:
        expect(dialog.get_by_role('button', name='Install drivers', exact=True)).to_have_count(0)
        expect(dialog.get_by_text('upload the driver file', exact=False)).to_have_count(0)
    page.screenshot(path=str(tmp_path / 'missing-equipment.png'), full_page=True)
    dialog.get_by_role('button', name='Skip', exact=True).click()
    page.get_by_role('button', name='Devices', exact=True).click()
    page.get_by_role('button').filter(has_text='Lobby amplifier').click()
    expect(page.get_by_text('Built-in equipment unavailable', exact=True)).to_be_visible(timeout=15000)
    expect(page.get_by_text('This driver isn\'t in the community catalog.', exact=False)).to_have_count(0)
    expect(page.get_by_role('button', name='Browse Drivers', exact=True)).to_have_count(0)
    page.screenshot(path=str(tmp_path / 'equipment-detail.png'), full_page=True)
    page.get_by_role('button', name='Reassign Driver', exact=True).click()
    expect(page.get_by_role('dialog')).to_be_visible()
    expect(page.get_by_placeholder('Search drivers...')).to_be_visible()
    after = json.loads(handle.project_path.read_text())
    for field in ['devices', 'connections', 'ui', 'macros', 'variables']:
        assert after[field] == before[field]
