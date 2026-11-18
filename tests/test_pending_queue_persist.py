"""A device's queue of settings waiting for it, as the project keeps it.

Every write of the queue is a change through the project's change door: the
intake adds its entries, a clear removes only the entries the pass took, and
the running system's queue moves once the file holds the change. So the file,
the running project and the device manager never disagree about a queue: a
newer value queued before an older clear is saved survives it, a clear or an
intake whose save fails leaves all three as they were, and a queue that comes
in with a project is handed to the running device in place, never by
removing the device and adding it again.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from openavc.core import project_changes
from openavc.core.engine import Engine
from openavc.core.project_changes import ProjectNotAppliedError
from openavc.core.project_loader import ProjectSaveError, load_project
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from tests.helpers import wait_for_condition

DRIVER = "acme_queue_widget_test"


class _AcmeQueueWidget(BaseDriver):
    """An invented device with two settings and nothing it reads back, so a
    queued value clears the moment it is sent."""

    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Queue Widget (test)",
        "transport": "tcp",
        "default_config": {"port": 5000},
        "state_variables": {},
        "commands": {},
        "device_settings": {
            "input": {"type": "string", "label": "Input"},
            "level": {"type": "integer", "label": "Level", "min": 0, "max": 100},
        },
    }

    written: list[tuple[str, Any]] = []
    away = False

    async def connect(self) -> None:
        if type(self).away:
            raise ConnectionError("no route to host")
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def set_device_setting(self, key: str, value: Any) -> bool:
        type(self).written.append((key, value))
        return True


@pytest.fixture
def widget():
    _AcmeQueueWidget.written = []
    _AcmeQueueWidget.away = False
    register_driver(_AcmeQueueWidget)
    yield _AcmeQueueWidget
    unregister_driver(DRIVER)


def _project(queue: dict[str, Any] | None = None) -> dict[str, Any]:
    device: dict[str, Any] = {
        "id": "w1", "driver": DRIVER, "name": "Widget", "config": {}, "enabled": True,
    }
    if queue is not None:
        device["pending_settings"] = queue
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": "P"},
        "variables": [],
        "macros": [],
        "devices": [device],
        "device_groups": [],
        "connections": {"w1": {"host": "10.0.0.9"}},
        "scripts": [],
        "plugins": {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


async def _engine(tmp_path, queue: dict[str, Any] | None = None) -> Engine:
    """An engine running the project with ``w1`` registered and not dialed,
    its events wired as a start wires them."""
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project(queue)), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True

    async def no_broadcast(msg, namespaces=None):
        return None

    eng.broadcast_ws = no_broadcast
    eng._bridge_events_to_ws()
    await eng._sync_devices(defer_connect=True)
    return eng


def _queues(eng: Engine) -> tuple[Any, Any, Any]:
    """The device's queue as the file, the running project and the device
    manager each hold it, with no queue read as an empty one."""
    on_disk = json.loads(eng.project_path.read_text(encoding="utf-8"))
    disk = next(d for d in on_disk["devices"] if d["id"] == "w1").get("pending_settings") or {}
    running = next(d for d in eng.project.devices if d.id == "w1").pending_settings or {}
    manager = (eng.devices.get_device_config("w1") or {}).get("pending_settings") or {}
    return disk, running, manager


async def _settled(eng: Engine) -> None:
    """Wait for every queued bookkeeping change to be saved or refused."""
    await wait_for_condition(
        lambda: not eng._bookkeeping_queue
        and (eng._bookkeeping_task is None or eng._bookkeeping_task.done()),
        message="the queued change never ran",
    )


@pytest.mark.asyncio
async def test_a_newer_value_saved_before_an_older_clear_survives_it(tmp_path, widget):
    """A pass took ``hdmi1``; before its clear was saved, a newer ``hdmi2``
    was (the order forced, as the change lock can give it). The clear removes
    the entry it took, not the queue as it was when the pass ran."""
    eng = await _engine(tmp_path, {"input": "hdmi1"})

    async with eng._reload_lock:
        await eng.devices._clear_applied_pending("w1", ["input"], {"input": "hdmi1"})

        def newer(project) -> None:
            next(d for d in project.devices if d.id == "w1").pending_settings = {"input": "hdmi2"}

        await eng._change_locked(newer, reconcile=False)
        eng.devices.get_device_config("w1")["pending_settings"] = {"input": "hdmi2"}
    await _settled(eng)

    assert _queues(eng) == ({"input": "hdmi2"},) * 3


@pytest.mark.asyncio
async def test_a_clear_whose_save_fails_leaves_the_entry_queued_everywhere(tmp_path, widget, monkeypatch):
    """The clear's save failed, so the file still holds the entry: the
    running system holds it too, and sends it again on the next connect
    rather than only after a restart."""
    eng = await _engine(tmp_path, {"input": "hdmi1"})

    async def full_disk(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(project_changes, "save_project_async", full_disk)
    await eng.devices._clear_applied_pending("w1", ["input"], {"input": "hdmi1"})
    await _settled(eng)

    assert _queues(eng) == ({"input": "hdmi1"},) * 3


@pytest.mark.asyncio
async def test_a_value_queued_whose_save_fails_is_queued_nowhere(tmp_path, widget, monkeypatch):
    """The running system takes a queued value only once the file holds it."""
    eng = await _engine(tmp_path)

    async def full_disk(*_args, **_kwargs):
        raise ProjectSaveError(OSError("No space left on device"))

    monkeypatch.setattr(project_changes, "save_project_async", full_disk)
    with pytest.raises(ProjectNotAppliedError):
        await eng.devices.store_pending_settings("w1", {"level": 40})

    assert _queues(eng) == ({},) * 3


@pytest.mark.asyncio
async def test_a_queued_value_is_saved_and_taken_once_saved(tmp_path, widget):
    """The intake checks the value, saves it, and the running system holds
    it in its declared type."""
    eng = await _engine(tmp_path, {"input": "hdmi1"})

    await eng.devices.store_pending_settings("w1", {"level": "40"})
    await _settled(eng)

    assert _queues(eng) == ({"input": "hdmi1", "level": 40},) * 3


@pytest.mark.asyncio
async def test_a_value_queued_while_a_clear_waits_survives_it(tmp_path, widget):
    """A clear waiting for the lock and a new value queued meanwhile: the
    clear takes its own entry and leaves the new one."""
    eng = await _engine(tmp_path, {"input": "hdmi1"})

    async with eng._reload_lock:
        await eng.devices._clear_applied_pending("w1", ["input"], {"input": "hdmi1"})
        intake = asyncio.create_task(eng.devices.store_pending_settings("w1", {"level": 7}))
        await asyncio.sleep(0)
    await intake
    await _settled(eng)

    assert _queues(eng) == ({"level": 7},) * 3


@pytest.mark.asyncio
async def test_a_live_write_clears_only_the_entry_it_settled(tmp_path, widget):
    """A live write settles the queued entry for its setting as it stood;
    a newer value queued before that clear is saved stays."""
    eng = await _engine(tmp_path, {"input": "hdmi1"})
    await eng.devices.bring_up()
    await _settled(eng)
    eng.devices.get_device_config("w1")["pending_settings"] = {"input": "hdmi1"}
    next(d for d in eng.project.devices if d.id == "w1").pending_settings = {"input": "hdmi1"}

    async with eng._reload_lock:
        await eng.devices.set_device_setting("w1", "input", "hdmi3")

        def newer(project) -> None:
            next(d for d in project.devices if d.id == "w1").pending_settings = {"input": "hdmi2"}

        await eng._change_locked(newer, reconcile=False)
        eng.devices.get_device_config("w1")["pending_settings"] = {"input": "hdmi2"}
    await _settled(eng)

    assert _queues(eng) == ({"input": "hdmi2"},) * 3


@pytest.mark.asyncio
async def test_a_queue_that_comes_in_with_a_project_is_sent_without_a_reconnect(tmp_path, widget):
    """A project whose only change to a connected device is its queue (a
    restore, a reload of the file) hands the queue to the running device and
    sends it: the device is not removed and added again for it."""
    eng = await _engine(tmp_path)
    await eng.devices.bring_up()
    driver = eng.devices.get_driver("w1")

    changed = eng.project.model_copy(deep=True)
    changed.devices[0].pending_settings = {"level": 55}
    await eng.apply_project(changed)
    await eng.wait_for_device_bringup()
    await _settled(eng)

    assert eng.devices.get_driver("w1") is driver
    assert ("level", 55) in widget.written
    assert _queues(eng) == ({},) * 3


@pytest.mark.asyncio
async def test_a_queue_that_comes_in_for_an_away_device_waits_for_its_connect(tmp_path, widget):
    """The same queue for a device that is not connected is held for its
    next connect, and the device is not added again for it."""
    widget.away = True
    eng = await _engine(tmp_path)
    driver = eng.devices.get_driver("w1")

    changed = eng.project.model_copy(deep=True)
    changed.devices[0].pending_settings = {"level": 55}
    try:
        await eng.apply_project(changed)
        await eng.wait_for_device_bringup()

        assert eng.devices.get_driver("w1") is driver
        assert widget.written == []
        assert _queues(eng) == ({"level": 55},) * 3
    finally:
        await eng.devices.disconnect_all()
