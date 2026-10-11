"""The real missing-driver API distinguishes portable built-in references."""

from pathlib import Path

import pytest

from tests.test_missing_drivers_api import running_app  # noqa: F401


@pytest.mark.parametrize('valid', [True, False])
async def test_missing_equipment_explanation_preserves_project(running_app, valid):  # noqa: F811
    from openavc.core import installed_equipment as installed
    import openavc
    from openavc.api._engine import _get_engine
    from openavc.core.project_loader import DeviceConfig

    assert Path(openavc.__file__).resolve().is_relative_to(Path.cwd())
    engine = _get_engine()
    model = 'acme_amplifier'
    driver_id = installed.model_driver_id(model)
    device = DeviceConfig(id='amp', name='Lobby amplifier', driver=driver_id,
                          config={'integration': model if valid else 'other_model'})
    engine.project.devices.append(device)
    await engine.devices.add_device(engine.resolved_device_config(device))
    before = engine.project.model_dump()
    response = running_app.get('/api/devices/missing-drivers')
    assert response.status_code == 200
    entries = {row['driver_id']: row for row in response.json()['missing']}
    row = entries[driver_id]
    if valid:
        assert row.get('built_in_equipment') == {
            'model': model, 'device_names': ['Lobby amplifier'],
        }
        assert row['community_match'] is None
    else:
        assert 'built_in_equipment' not in row
    assert 'built_in_equipment' not in entries['extron_sis_test']
    assert entries['samsung_mdc_test']['community_match'] is not None
    assert engine.project.model_dump() == before
    assert engine.devices.get_device_info('amp')['orphaned']
    assert not engine.installed_equipment.owns('amp')
