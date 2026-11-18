"""Engine bookkeeping persists — project writes the running system takes
without a reconcile (pending-settings clears, plugin ``save_config``) must
persist the file, bump the revision, and broadcast ``project.reloaded``
WITHOUT running a reconcile.

The hard constraint pinned here: the code that triggers such a write can be
running while the engine holds the reconcile lock. A device can connect
while a project change holds it, and the pending-settings clear its connect
makes is handed to the queue keeper from there — so a keeper that naively
called ``apply_project`` would deadlock the engine. The first test
reproduces exactly that call shape and guards it with a timeout.
"""

import asyncio
import json
from typing import Any

import pytest

from openavc.drivers.registry import register_driver, unregister_driver
from openavc.core.engine import Engine
from openavc.core.pending_settings import PendingClear
from openavc.core.project_loader import load_project
from openavc.drivers.base import BaseDriver
from tests.helpers import wait_for_condition


def _project_dict(*, devices=None, plugins=None, variables=None):
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": "P"},
        "variables": variables or [],
        "macros": [],
        "devices": devices or [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": plugins or {},
        "ui": {
            "settings": {},
            "pages": [
                {"id": "main", "name": "Main",
                 "grid": {"columns": 12, "rows": 8}, "elements": []},
            ],
        },
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


def _engine(tmp_path, **kwargs) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project_dict(**kwargs)), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    return eng


def _capture_broadcasts(eng) -> list[dict]:
    sent: list[dict] = []

    async def record(msg, namespaces=None):
        sent.append(msg)

    eng.broadcast_ws = record
    return sent


def _disk_project(eng) -> dict:
    return json.loads(eng.project_path.read_text(encoding="utf-8"))


class _AcmeSettingsPanel(BaseDriver):
    """Invented device whose settings apply instantly on connect, so the
    DeviceManager's real pending-settings machinery runs — and emits
    ``device.pending_settings_applied`` — during ``add_device``."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_settings_panel_bk_test",
        "name": "Acme Settings Panel (test)",
        "transport": "tcp",
        "default_config": {"port": 5000},
        "state_variables": {},
        "commands": {},
        "device_settings": {
            "input": {"type": "string", "label": "Input"},
        },
    }

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def set_device_setting(self, key: str, value: Any) -> bool:
        return True


@pytest.fixture
def acme_settings_panel():
    register_driver(_AcmeSettingsPanel)
    yield _AcmeSettingsPanel
    unregister_driver("acme_settings_panel_bk_test")


@pytest.mark.asyncio
async def test_pending_settings_persist_from_inside_reconcile_no_deadlock(
    tmp_path, acme_settings_panel,
):
    """The re-entrancy trap: pending settings are applied on device
    connect, which runs inside _sync_devices while apply_project holds the
    reconcile lock, and the event handler is awaited inline from there.

    The persist must not deadlock (timeout guard), must land the cleared
    pending_settings on disk, and must advance the revision so a stale
    IDE PUT gets a 409 instead of silently restoring the queue (BUG-4).
    """
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)

    from openavc.core.project_loader import DeviceConfig

    new_project = eng.project.model_copy(deep=True)
    new_project.devices.append(DeviceConfig(
        id="panel1",
        driver="acme_settings_panel_bk_test",
        name="Panel 1",
        config={},
        enabled=True,
        pending_settings={"input": "hdmi1"},
    ))

    # Deadlock guard: a naive apply_project call in the handler would hang
    # here forever (the handler is awaited inline under the reconcile lock).
    await asyncio.wait_for(eng.apply_project(new_project), timeout=10)
    saves_after_apply = eng._revision_counter
    assert saves_after_apply == 1

    # The bookkeeping persist runs outside the lock, right after.
    await wait_for_condition(
        lambda: eng._revision_counter == saves_after_apply + 1,
        message="pending-settings persist never bumped the revision",
    )
    on_disk = _disk_project(eng)
    dev = next(d for d in on_disk["devices"] if d["id"] == "panel1")
    assert dev.get("pending_settings") in ({}, None)
    # In-memory project matches what was persisted.
    assert next(
        d for d in eng.project.devices if d.id == "panel1"
    ).pending_settings == {}
    # The IDE learned the new revision.
    reloads = [m for m in sent if m.get("type") == "project.reloaded"]
    assert reloads and reloads[-1]["revision"] == eng._project_revision


@pytest.mark.asyncio
async def test_scheduled_bookkeeping_outside_lock_persists_and_bumps(tmp_path):
    eng = _engine(tmp_path, devices=[{
        "id": "panel1", "driver": "acme_settings_panel_bk_test",
        "name": "Panel 1", "config": {}, "enabled": True,
        "pending_settings": {"input": "hdmi1"},
    }])
    sent = _capture_broadcasts(eng)

    await eng.devices._queue_keeper.drop(PendingClear("panel1", {"input": "hdmi1"}))
    await wait_for_condition(
        lambda: eng._revision_counter == 1,
        message="bookkeeping persist never bumped the revision",
    )
    dev = next(d for d in _disk_project(eng)["devices"] if d["id"] == "panel1")
    assert dev.get("pending_settings") in ({}, None)
    assert [m["revision"] for m in sent if m.get("type") == "project.reloaded"] == [
        eng._project_revision
    ]


@pytest.mark.asyncio
async def test_bookkeeping_writes_coalesce_into_one_persist(tmp_path):
    """Two writes queued in the same tick flush as one save + one bump."""
    eng = _engine(tmp_path)
    _capture_broadcasts(eng)

    def rename(project):
        project.project.name = "Renamed"

    def flag(project):
        project.project.id = "p2"

    eng.schedule_bookkeeping_change(rename)
    eng.schedule_bookkeeping_change(flag)
    await wait_for_condition(
        lambda: eng._revision_counter >= 1,
        message="bookkeeping flush never ran",
    )
    # Let any (wrong) second flush land before asserting.
    await asyncio.sleep(0.05)
    assert eng._revision_counter == 1
    meta = _disk_project(eng)["project"]
    assert meta["name"] == "Renamed" and meta["id"] == "p2"


@pytest.mark.asyncio
async def test_bookkeeping_mutation_applies_to_swapped_project(tmp_path):
    """A write scheduled while the lock is held lands on whatever project is
    current at flush time — a swap in between must not resurrect the old
    object or lose the write."""
    eng = _engine(tmp_path, devices=[{
        "id": "panel1", "driver": "acme_settings_panel_bk_test",
        "name": "Panel 1", "config": {}, "enabled": True,
        "pending_settings": {"input": "hdmi1"},
    }])
    _capture_broadcasts(eng)

    async with eng._reload_lock:
        # Fires under the lock -> must defer, not deadlock.
        await asyncio.wait_for(
            eng.devices._queue_keeper.drop(PendingClear("panel1", {"input": "hdmi1"})),
            timeout=5,
        )
        # Swap the project object before the flush can run.
        eng.project = eng.project.model_copy(deep=True)

    await wait_for_condition(
        lambda: eng._revision_counter >= 1,
        message="deferred persist never ran",
    )
    assert next(
        d for d in eng.project.devices if d.id == "panel1"
    ).pending_settings == {}
    dev = next(d for d in _disk_project(eng)["devices"] if d["id"] == "panel1")
    assert dev.get("pending_settings") in ({}, None)


@pytest.mark.asyncio
async def test_plugin_save_config_uncontended_persists_before_return(tmp_path):
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    sent = _capture_broadcasts(eng)

    await eng._save_plugin_config("plug1", {"x": 2})

    # Awaited path: persisted + bumped by the time the call returns.
    assert eng._revision_counter == 1
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 2}
    assert eng.project.plugins["plug1"].config == {"x": 2}
    assert [m["revision"] for m in sent if m.get("type") == "project.reloaded"] == [
        eng._project_revision
    ]


@pytest.mark.asyncio
async def test_plugin_save_config_inside_a_change_defers_without_deadlock(tmp_path):
    """A plugin hook (on_start / on_config_changed / an event handler) can
    call api.save_config while _sync_plugins holds the reconcile lock."""
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)

    async with eng._reload_lock:
        with eng._running_hooks():
            await asyncio.wait_for(
                eng._save_plugin_config("plug1", {"x": 2}), timeout=5
            )
            # Queued: the running project, which the settings page shows,
            # waits for the file.
            assert eng.project.plugins["plug1"].config == {"x": 1}

    await wait_for_condition(
        lambda: eng._revision_counter == 1,
        message="deferred plugin-config persist never ran",
    )
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 2}
    assert eng.project.plugins["plug1"].config == {"x": 2}


@pytest.mark.asyncio
async def test_plugin_save_config_failure_leaves_the_project_as_saved(tmp_path, monkeypatch):
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)

    async def boom(path, project):
        raise OSError("disk full")

    monkeypatch.setattr("openavc.core.project_changes.save_project_async", boom)

    with pytest.raises(OSError):
        await eng._save_plugin_config("plug1", {"x": 2})

    # Never taken in, so the config can't break every later save.
    assert eng.project.plugins["plug1"].config == {"x": 1}
    assert eng._revision_counter == 0


@pytest.mark.asyncio
async def test_bookkeeping_scheduled_during_stop_teardown_persists(tmp_path):
    """A bookkeeping write scheduled while stop() holds the reload lock —
    a plugin ``on_stop`` calling ``save_config`` during ``_stop_inner``'s
    teardown — spawns a flush task that queues behind the held lock.
    ``stop()`` must drain it after teardown releases the lock, so the
    write persists before the process exits instead of being dropped."""
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {}},
    })
    _capture_broadcasts(eng)

    def mutate(project):
        project.plugins["plug1"].config["written_during_stop"] = True

    async def stop_all_scheduling_write():
        # Runs inside _stop_inner with the reload lock held — the same
        # call shape as a plugin on_stop hook persisting its config.
        assert eng._reload_lock.locked()
        eng.schedule_bookkeeping_change(mutate)

    eng.plugin_loader.stop_all = stop_all_scheduling_write

    await asyncio.wait_for(eng.stop(), timeout=5)

    # Persisted by the time stop() returns — no reliance on the event
    # loop surviving past shutdown.
    disk = _disk_project(eng)
    assert disk["plugins"]["plug1"]["config"].get("written_during_stop") is True
    assert eng._revision_counter == 1


# --- when a plugin's own save waits its turn, and when it is queued ---------


def _hold_the_first_write(monkeypatch, *, fail_the_rest: bool = False) -> asyncio.Event:
    """The first project write waits until the returned event is set, so the
    save making it holds the reload lock meanwhile. With ``fail_the_rest``
    every write after it raises, as a full disk does."""
    from openavc.core import project_changes

    real = project_changes.save_project_async
    released = asyncio.Event()
    writes = 0

    async def save(path, project):
        nonlocal writes
        writes += 1
        if writes == 1:
            await released.wait()
        elif fail_the_rest:
            raise OSError("Injected unwritable project")
        await real(path, project)

    monkeypatch.setattr(project_changes, "save_project_async", save)
    return released


def _rename(project) -> None:
    project.project.name = "Renamed"


async def _another_save_holding_the_lock(eng) -> asyncio.Task:
    other = asyncio.create_task(eng.persist_bookkeeping_change(_rename))
    await wait_for_condition(eng._reload_lock.locked, message="the other save never took the lock")
    return other


@pytest.mark.asyncio
async def test_a_plugin_save_that_waited_beside_another_raises_its_own_failure(tmp_path, monkeypatch):
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)
    released = _hold_the_first_write(monkeypatch, fail_the_rest=True)
    other = await _another_save_holding_the_lock(eng)

    saving = asyncio.create_task(eng._save_plugin_config("plug1", {"x": 2}))
    await asyncio.sleep(0.05)
    released.set()
    with pytest.raises(OSError):
        await asyncio.wait_for(saving, timeout=5)
    await other

    assert eng.project.plugins["plug1"].config == {"x": 1}
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 1}
    assert eng._revision_counter == 1


@pytest.mark.asyncio
async def test_a_plugin_save_made_while_a_change_waits_for_the_lock_is_queued_behind_it(tmp_path, monkeypatch):
    """A change that runs plugin code may wait on that plugin, so a save
    made while the change waits for the lock is queued behind it rather
    than waiting on the lock ahead of it."""
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)
    released = _hold_the_first_write(monkeypatch)
    other = await _another_save_holding_the_lock(eng)
    applied = eng.project.model_copy(deep=True)
    applied.project.name = "Applied"
    applying = asyncio.create_task(eng.apply_project(applied))
    await asyncio.sleep(0.05)
    assert not applying.done()

    await asyncio.wait_for(eng._save_plugin_config("plug1", {"x": 2}), timeout=1)
    assert eng.project.plugins["plug1"].config == {"x": 1}
    released.set()
    await other
    await applying
    await eng._bookkeeping_task

    assert _disk_project(eng)["project"]["name"] == "Applied"
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 2}


@pytest.mark.asyncio
async def test_a_plugin_save_from_a_project_reloaded_handler_lands_without_a_deadlock(tmp_path):
    """The bus event an applied change sends is awaited under its lock, so
    a plugin saving from its handler is queued behind the change."""
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)

    async def save_on_reload(event, payload):
        await eng._save_plugin_config("plug1", {"x": 2})

    eng.events.on("system.project.reloaded", save_on_reload)
    renamed = eng.project.model_copy(deep=True)
    _rename(renamed)
    await asyncio.wait_for(eng.apply_project(renamed), timeout=5)

    await wait_for_condition(
        lambda: eng._revision_counter == 2,
        message="the plugin's save from the handler never landed",
    )
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 2}
    assert _disk_project(eng)["project"]["name"] == "Renamed"


@pytest.mark.asyncio
async def test_a_plugin_save_made_as_the_engine_stops_lands_without_a_deadlock(tmp_path):
    """A plugin's stop runs under the stop's hold on the lock, so its save
    is queued and written before stop() returns."""
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)

    async def stop_all_saving():
        await eng._save_plugin_config("plug1", {"x": 2})

    eng.plugin_loader.stop_all = stop_all_saving

    problems = await asyncio.wait_for(eng.stop(), timeout=5)

    assert problems == []
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 2}
