"""Persistence must retain the producing device across failures and replacement."""

import asyncio
import copy

import pytest

from openavc.core import engine as engine_module
from openavc.core.project_diff import ProjectOrigin
from tests.helpers import wait_for_condition
from tests.test_learned_config import (
    _add,
    _disk_config,
    _engine,
    acme_widget as acme_widget,
)


async def ready(tmp_path, acme_widget):
    acme_widget.REPORT = {}
    engine, _ = _engine(tmp_path)
    await _add(engine)
    return engine, engine.devices.get_driver('w1')


async def disk_full(path, project):
    raise OSError('Injected unwritable project')


@pytest.mark.asyncio
async def test_failed_save_rolls_back_and_retries_without_another_report(tmp_path, acme_widget, monkeypatch):
    engine, driver = await ready(tmp_path, acme_widget)
    before = engine.project_path.read_bytes()
    project_before = engine.project.model_dump()
    save = engine_module.save_project_async
    try:
        monkeypatch.setattr(engine_module, 'save_project_async', disk_full)
        driver.set_state('model', 'W100')
        await engine._bookkeeping_task
        assert engine.project_path.read_bytes() == before
        assert engine.project.model_dump() == project_before
        assert not driver.config.get('model')
        assert engine.devices.get_driver('w1') is driver
        monkeypatch.setattr(engine_module, 'save_project_async', save)
        await wait_for_condition(lambda: _disk_config(engine).get('model') == 'W100')
        assert engine.project.devices[0].config['model'] == 'W100'
        assert driver.config['model'] == 'W100'
        assert engine.devices.get_driver('w1') is driver
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_batched_reports_undo_in_reverse_order_and_stop_cancels_retry(tmp_path, acme_widget, monkeypatch):
    engine, driver = await ready(tmp_path, acme_widget)
    before = engine.project.model_dump()
    handles = []
    try:
        monkeypatch.setattr(engine_module, 'save_project_async', disk_full)
        async with engine._reload_lock:
            driver.set_state('model', 'W100')
            driver.set_state('model', 'W200')
            assert len(engine._bookkeeping_queue) == 2
        await engine._bookkeeping_task
        assert engine.project.model_dump() == before
        assert not driver.config.get('model')
        assert len(engine.learned_config._retry_handles) == 1
        handles = list(engine.learned_config._retry_handles.values())
    finally:
        await engine.stop()
    assert handles and all(handle.cancelled() for handle in handles)
    assert engine.learned_config._retry_handles == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('change_project_id', [False, True])
async def test_queued_report_cannot_modify_replacement(tmp_path, acme_widget, change_project_id):
    engine, driver = await ready(tmp_path, acme_widget)
    try:
        async with engine._reload_lock:
            driver.set_state('model', 'W100')
            assert engine._bookkeeping_queue
            replacement = engine.project.model_copy(deep=True)
            if change_project_id:
                replacement.project.id = 'different-project'
            replacement.devices[0].enabled = False
            replacement.connections['w1'] = {'host': 'different-equipment.invalid'}
            await engine._apply_project_locked(replacement, None, ProjectOrigin.LOAD, True)
        await engine._bookkeeping_task
        assert not _disk_config(engine).get('model')
        assert not engine.project.devices[0].config.get('model')
        assert engine.devices.get_driver('w1') is not driver
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_retry_cannot_modify_replacement(tmp_path, acme_widget, monkeypatch):
    engine, driver = await ready(tmp_path, acme_widget)
    save = engine_module.save_project_async
    try:
        monkeypatch.setattr(engine_module, 'save_project_async', disk_full)
        driver.set_state('model', 'W100')
        await engine._bookkeeping_task
        handles = list(engine.learned_config._retry_handles.values())
        assert len(handles) == 1
        monkeypatch.setattr(engine_module, 'save_project_async', save)
        replacement = engine.project.model_copy(deep=True)
        replacement.devices[0].enabled = False
        replacement.connections['w1'] = {'host': 'replacement.invalid'}
        await engine.apply_project(replacement, origin=ProjectOrigin.LOAD)
        before = copy.deepcopy(engine.project.model_dump())
        await wait_for_condition(lambda: not engine.learned_config._retry_handles)
        assert engine.project.model_dump() == before
        assert not _disk_config(engine).get('model')
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_batched_plugin_rollbacks_retain_original_config(tmp_path, monkeypatch):
    from tests.test_engine_bookkeeping_persist import _engine as plugin_engine

    engine = plugin_engine(tmp_path, plugins={'plug1': {'enabled': False, 'config': {'x': 1}}})
    monkeypatch.setattr(engine_module, 'save_project_async', disk_full)
    try:
        async with engine._reload_lock:
            await engine._save_plugin_config('plug1', {'x': 2})
            await engine._save_plugin_config('plug1', {'x': 3})
        await asyncio.wait_for(engine._bookkeeping_task, 5)
        assert engine.project.plugins['plug1'].config == {'x': 1}
    finally:
        await engine.stop()
