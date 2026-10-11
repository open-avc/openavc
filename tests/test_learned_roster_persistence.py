"""A reported roster must not refresh from a setting whose save later fails."""

import asyncio

import pytest

from openavc.core import engine as engine_module
from openavc.core.project_loader import DeviceConfig
from openavc.drivers.registry import register_driver, unregister_driver
from tests.helpers import wait_for_condition
from tests.test_learned_config import _AcmeZoned, _ZONED_ID, _disk_config, _engine


@pytest.mark.asyncio
async def test_failed_report_save_does_not_publish_a_new_roster(tmp_path, monkeypatch):
    register_driver(_AcmeZoned)
    _AcmeZoned.REPORT = {}
    _AcmeZoned.refreshed = []
    engine, _ = _engine(tmp_path)
    entered, fail_now = asyncio.Event(), asyncio.Event()
    original_save = engine_module.save_project_async

    async def delayed_failure(path, project):
        entered.set()
        await fail_now.wait()
        raise OSError('Injected failed roster save')

    try:
        project = engine.project.model_copy(deep=True)
        project.devices.append(DeviceConfig(id='z1', driver=_ZONED_ID, name='Zones', config={'zones': 'A,B'}))
        await engine.apply_project(project)
        await wait_for_condition(lambda: engine.state.get('device.z1.connected') is True)
        driver = engine.devices.get_driver('z1')
        before = engine.project_path.read_bytes()
        monkeypatch.setattr(engine_module, 'save_project_async', delayed_failure)
        driver.set_state('zones_reported', 'A,B,C,D')
        await asyncio.wait_for(entered.wait(), 5)
        # Drain already scheduled roster work while the write is held open.
        await asyncio.gather(*tuple(engine.learned_config._tasks))
        fail_now.set()
        await asyncio.wait_for(engine._bookkeeping_task, 5)
        assert engine.project_path.read_bytes() == before
        assert _disk_config(engine, 'z1')['zones'] == driver.config['zones'] == 'A,B'
        assert _AcmeZoned.refreshed == [], 'A failed setting was used to refresh the roster'
        monkeypatch.setattr(engine_module, 'save_project_async', original_save)
        await wait_for_condition(lambda: _disk_config(engine, 'z1')['zones'] == 'A,B,C,D')
        await wait_for_condition(lambda: _AcmeZoned.refreshed == ['A,B,C,D'])
    finally:
        fail_now.set()
        await engine.stop()
        unregister_driver(_ZONED_ID)
