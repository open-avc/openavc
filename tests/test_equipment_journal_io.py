"""Durable equipment commands must not stop unrelated event-loop work."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
import threading
from unittest.mock import AsyncMock

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.equipment import AsyncRequestJournal, EquipmentDriver, RequestJournal


async def connected_driver(tmp_path, monkeypatch):
    monkeypatch.setattr('openavc.drivers.equipment.get_data_dir', lambda: tmp_path)
    driver = EquipmentDriver('local', {
        'token': 'fixture', 'url': 'https://127.0.0.1',
        'identity': 'unit', 'integration': 'acme_equipment',
    }, StateStore(), EventBus())
    info = dict(EquipmentDriver.DRIVER_INFO, id='acme_equipment', config_schema={},
                state_variables={}, commands={'increment': {}})
    driver._request = AsyncMock(return_value={
        'protocol': 'openavc-device-1', 'identity': 'unit',
        'session': 'session', 'capabilities': info,
    })
    driver._refresh = AsyncMock()
    await driver.connect()
    driver._request.reset_mock()
    driver._request.return_value = {'outcome': 'completed'}
    return driver


@pytest.mark.asyncio
async def test_slow_commit_allows_unrelated_loop_work_before_command(tmp_path, monkeypatch):
    driver = await connected_driver(tmp_path, monkeypatch)
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()
    reserve = RequestJournal.reserve
    loop = asyncio.get_running_loop()

    def slow_reserve(journal, session):
        entered.set()
        assert release.wait(5), 'storage probe was not released'
        return reserve(journal, session)

    def release_from_loop():
        if not driver._request.called:
            observed.set()
        release.set()

    def watchdog():
        assert entered.wait(5)
        loop.call_soon_threadsafe(release_from_loop)
        # A deadlocked old implementation must still leave the fixture clean.
        if not release.wait(3):
            release.set()

    monkeypatch.setattr(RequestJournal, 'reserve', slow_reserve)
    try:
        with ThreadPoolExecutor(max_workers=1) as helper:
            watchdog_result = helper.submit(watchdog)
            await driver.send_command('increment')
            watchdog_result.result(timeout=5)
        assert observed.is_set(), 'a command journal commit blocked unrelated loop work'
        assert driver._request.call_count == 1
        assert driver._request.call_args.args[1]['id'] == 1
    finally:
        release.set()
        await driver.disconnect()


@pytest.mark.asyncio
async def test_cancelled_reservation_consumes_id_without_sending_or_releasing_lease(tmp_path, monkeypatch):
    driver = await connected_driver(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    reserve = RequestJournal.reserve

    def slow_reserve(journal, session):
        entered.set()
        assert release.wait(5), 'storage probe was not released'
        return reserve(journal, session)

    monkeypatch.setattr(RequestJournal, 'reserve', slow_reserve)
    task = asyncio.create_task(driver.send_command('increment'))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        driver._request.assert_not_called()
        # The cancelled await cannot release the single-writer lease early.
        with pytest.raises(sqlite3.OperationalError):
            RequestJournal(tmp_path / 'equipment-requests', hashlib.sha256(b'unit').hexdigest())
        close = asyncio.create_task(driver.disconnect())
        await asyncio.sleep(0)
        assert not close.done()
        release.set()
        await close
        monkeypatch.setattr(RequestJournal, 'reserve', reserve)
        reopened = await connected_driver(tmp_path, monkeypatch)
        try:
            await reopened.send_command('increment')
            assert reopened._request.call_args.args[1]['id'] == 2
        finally:
            await reopened.disconnect()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await driver.disconnect()


@pytest.mark.asyncio
async def test_disconnect_during_commit_does_not_send_on_a_closed_connection(tmp_path, monkeypatch):
    driver = await connected_driver(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    reserve = RequestJournal.reserve

    def slow_reserve(journal, session):
        entered.set()
        assert release.wait(5)
        return reserve(journal, session)

    monkeypatch.setattr(RequestJournal, 'reserve', slow_reserve)
    task = asyncio.create_task(driver.send_command('increment'))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        close = asyncio.create_task(driver.disconnect())
        async def closing_started():
            while driver._sdk_journal is not None:
                await asyncio.sleep(0)
        await asyncio.wait_for(closing_started(), 3)
        release.set()
        await close
        with pytest.raises(ConnectionError, match='connection changed'):
            await task
        driver._request.assert_not_called()
    finally:
        release.set()
        await driver.disconnect()


@pytest.mark.asyncio
async def test_new_equipment_session_during_commit_does_not_send(tmp_path, monkeypatch):
    driver = await connected_driver(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    reserve = RequestJournal.reserve

    def slow_reserve(journal, session):
        entered.set()
        assert release.wait(5)
        return reserve(journal, session)

    monkeypatch.setattr(RequestJournal, 'reserve', slow_reserve)
    task = asyncio.create_task(driver.send_command('increment'))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        driver._request.return_value = {'identity': 'unit', 'session': 'replacement'}
        with pytest.raises(ConnectionError, match='equipment restarted'):
            await EquipmentDriver._refresh(driver)
        release.set()
        with pytest.raises(ConnectionError, match='connection changed'):
            await task
        driver._request.assert_awaited_once_with('/v1/snapshot')
    finally:
        release.set()
        await driver.disconnect()


@pytest.mark.asyncio
async def test_open_cancellation_closes_on_the_worker_and_releases_lease(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = RequestJournal.__init__

    def slow_open(journal, *args):
        original(journal, *args)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(RequestJournal, '__init__', slow_open)
    journal = AsyncRequestJournal()
    opening = asyncio.create_task(journal.open(tmp_path, 'unit'))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        closing = asyncio.create_task(journal.close())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        release.set()
        await journal.close()
        monkeypatch.setattr(RequestJournal, '__init__', original)
        replacement = AsyncRequestJournal()
        try:
            await replacement.open(tmp_path, 'unit')
            assert await replacement.reserve('session') == 1
        finally:
            await replacement.close()
    finally:
        release.set()
        await journal.close()


@pytest.mark.asyncio
async def test_failed_commit_never_sends_a_hardware_command(tmp_path, monkeypatch):
    driver = await connected_driver(tmp_path, monkeypatch)

    def failed_commit(journal, session):
        raise sqlite3.OperationalError('injected disk error')

    monkeypatch.setattr(RequestJournal, 'reserve', failed_commit)
    try:
        with pytest.raises(sqlite3.OperationalError, match='disk error'):
            await driver.send_command('increment')
        driver._request.assert_not_called()
    finally:
        await driver.disconnect()
