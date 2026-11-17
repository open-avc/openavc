"""The tasks a driver spawns for one connection, and the cleanup a drop runs.

A Python driver hands the platform the work that belongs to the connection it
is on (a read-back after a write, a resubscribe loop) through ``spawn_task``,
and every close of that connection ends it: a drop, a disconnect, the clean
slate of the next connect. The cleanup a drop runs is the platform's own and
lives apart, so a driver that cancels every task it was given when its
session closes cannot cancel it: the drop still says ``device.disconnected``
and the reconnect still starts. Pinned on invented devices:

  - a driver whose session close cancels its whole task set (the shape a
    driver file written before ``spawn_task`` has) comes back after a drop;
  - a spawned task ends at a drop, at a disconnect and at the next connect;
  - a spawned task that disconnects the device itself is not cut short;
  - a spawned task's unhandled failure is logged against the device;
  - a task an older driver file put in ``_bg_tasks`` ends with the
    connection too.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY


class _OpenLink:
    """A transport that is up until closed."""

    connected = True

    async def close(self) -> None:
        self.connected = False


class AcmeWidgetDriver(BaseDriver):
    """BaseDriver's own connect, over a link that is always there."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
    }

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.connects = 0

    async def _create_transport(self, transport_type: str) -> None:
        self.connects += 1
        self.transport = _OpenLink()

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class AcmeCancelsItsSetDriver(AcmeWidgetDriver):
    """Cancels every task in ``_bg_tasks`` when its session closes, the way a
    driver file written before ``spawn_task`` does."""

    DRIVER_INFO: dict[str, Any] = {
        **AcmeWidgetDriver.DRIVER_INFO,
        "id": "acme_cancels_its_set",
        "name": "Acme Widget (cancels its set)",
    }

    async def _close_session(self) -> None:
        for task in list(self._bg_tasks):
            task.cancel()
        self._bg_tasks.clear()


def _bus() -> tuple[StateStore, EventBus]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


def _heard(events: EventBus, device_id: str) -> asyncio.Event:
    heard = asyncio.Event()

    async def on_disconnected(event: str, payload: Any = None) -> None:
        heard.set()

    events.on(f"device.disconnected.{device_id}", on_disconnected)
    return heard


async def _connected(cls: type[AcmeWidgetDriver] = AcmeWidgetDriver) -> tuple[AcmeWidgetDriver, asyncio.Event]:
    state, events = _bus()
    drv = cls("acme1", {"host": "192.0.2.30", "port": 23}, state, events)
    heard = _heard(events, "acme1")
    await drv.connect()
    assert drv.connected is True
    return drv, heard


async def _forever() -> None:
    await asyncio.Event().wait()


async def _until_done(task: asyncio.Task) -> None:
    await asyncio.wait({task}, timeout=1.0)


async def test_a_driver_that_cancels_its_task_set_at_close_comes_back_after_a_drop(monkeypatch):
    state, events = _bus()
    dm = DeviceManager(state, events)
    monkeypatch.setattr(dm, "_reconnect_delay", lambda device_id, attempt: 0.0)
    heard = _heard(events, "d1")
    _DRIVER_REGISTRY["acme_cancels_its_set"] = AcmeCancelsItsSetDriver
    try:
        await dm.add_device({
            "id": "d1", "driver": "acme_cancels_its_set", "name": "D1",
            "config": {"host": "192.0.2.30", "port": 23},
        })
        driver = dm._devices["d1"]
        assert driver.get_state("connected") is True

        driver._handle_transport_disconnect()  # what the transport calls on a drop

        await asyncio.wait_for(heard.wait(), timeout=1.0)
        for _ in range(100):
            if driver.connects >= 2 and driver.get_state("connected") is True:
                break
            await asyncio.sleep(0.01)
        assert driver.connects == 2
        assert driver.get_state("connected") is True
    finally:
        await dm._cancel_reconnect("d1")
        _DRIVER_REGISTRY.pop("acme_cancels_its_set", None)


async def test_a_drop_ends_the_tasks_spawned_for_the_connection():
    drv, heard = await _connected()
    task = drv.spawn_task(_forever())
    await asyncio.sleep(0)

    drv._handle_transport_disconnect()

    await asyncio.wait_for(heard.wait(), timeout=1.0)
    await _until_done(task)
    assert task.cancelled()


async def test_a_disconnect_ends_them():
    drv, _heard_it = await _connected()
    task = drv.spawn_task(_forever())
    await asyncio.sleep(0)

    await drv.disconnect()

    await _until_done(task)
    assert task.cancelled()


async def test_the_next_connect_ends_one_spawned_while_offline():
    drv, _heard_it = await _connected()
    await drv.disconnect()
    task = drv.spawn_task(_forever())
    await asyncio.sleep(0)

    await drv.connect()

    await _until_done(task)
    assert task.cancelled()
    assert drv.connected is True


async def test_a_spawned_task_that_disconnects_the_device_is_not_cut_short():
    drv, heard = await _connected()
    finished = asyncio.Event()

    async def hang_up() -> None:
        await drv.disconnect()
        await asyncio.sleep(0)
        finished.set()

    task = drv.spawn_task(hang_up())
    await _until_done(task)

    assert not task.cancelled()
    assert finished.is_set()
    assert heard.is_set()
    assert drv.get_state("connected") is False


async def test_a_spawned_task_that_fails_is_logged_against_the_device(caplog):
    drv, _heard_it = await _connected()

    async def fails() -> None:
        raise ValueError("acme reply out of range")

    with caplog.at_level(logging.WARNING, logger="openavc"):
        task = drv.spawn_task(fails())
        await _until_done(task)
        await asyncio.sleep(0)

    assert "[acme1]" in caplog.text
    assert "acme reply out of range" in caplog.text
    await drv.disconnect()


async def test_a_spawned_task_is_held_until_it_finishes():
    drv, _heard_it = await _connected()
    gate = asyncio.Event()

    async def waits() -> str:
        await gate.wait()
        return "done"

    task = drv.spawn_task(waits())
    assert task in drv._session_tasks
    gate.set()
    assert await task == "done"
    await asyncio.sleep(0)
    assert task not in drv._session_tasks
    await drv.disconnect()


@pytest.mark.parametrize("close", ["drop", "disconnect"])
async def test_a_task_an_older_driver_file_put_in_bg_tasks_ends_with_the_connection(close):
    drv, heard = await _connected()
    task = asyncio.ensure_future(_forever())
    drv._bg_tasks.add(task)
    task.add_done_callback(drv._bg_tasks.discard)
    await asyncio.sleep(0)

    if close == "drop":
        drv._handle_transport_disconnect()
        await asyncio.wait_for(heard.wait(), timeout=1.0)
    else:
        await drv.disconnect()

    await _until_done(task)
    assert task.cancelled()
