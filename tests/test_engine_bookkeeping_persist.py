"""Engine bookkeeping persists — project writes whose runtime effect is
already applied (pending-settings clears, plugin ``save_config``) must
persist the file, bump the revision, and broadcast ``project.reloaded``
WITHOUT running a reconcile.

The hard constraint pinned here: the code that triggers such a write can be
running while the engine holds the reconcile lock. Device connect happens
inside ``_sync_devices`` (under ``apply_project``'s lock), and the
``device.pending_settings_applied`` event is awaited inline from there — so
a handler that naively called ``apply_project`` would deadlock the engine.
The first test reproduces exactly that call shape and guards it with a
timeout.
"""

import asyncio
import json
from typing import Any

import pytest

from server.drivers.registry import register_driver, unregister_driver
from server.core.engine import Engine
from server.core.project_loader import load_project
from server.drivers.base import BaseDriver
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
    """The §4e re-entrancy trap: pending settings are applied on device
    connect, which runs inside _sync_devices while apply_project holds the
    reconcile lock, and the event handler is awaited inline from there.

    The persist must not deadlock (timeout guard), must land the cleared
    pending_settings on disk, and must advance the revision so a stale
    IDE PUT gets a 409 instead of silently restoring the queue (BUG-4).
    """
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)
    # Wire the subscription Engine.start() would have made.
    eng.events.on(
        "device.pending_settings_applied", eng._on_pending_settings_applied
    )

    from server.core.project_loader import DeviceConfig

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
    rev_after_apply = eng._project_revision
    assert rev_after_apply == 1

    # The bookkeeping persist runs outside the lock, right after.
    await wait_for_condition(
        lambda: eng._project_revision == rev_after_apply + 1,
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

    await eng._on_pending_settings_applied(
        "device.pending_settings_applied",
        {"device_id": "panel1", "applied": ["input"], "remaining": {}},
    )
    await wait_for_condition(
        lambda: eng._project_revision == 1,
        message="bookkeeping persist never bumped the revision",
    )
    dev = next(d for d in _disk_project(eng)["devices"] if d["id"] == "panel1")
    assert dev.get("pending_settings") in ({}, None)
    assert [m["revision"] for m in sent if m.get("type") == "project.reloaded"] == [1]


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
        lambda: eng._project_revision >= 1,
        message="bookkeeping flush never ran",
    )
    # Let any (wrong) second flush land before asserting.
    await asyncio.sleep(0.05)
    assert eng._project_revision == 1
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
            eng._on_pending_settings_applied(
                "device.pending_settings_applied",
                {"device_id": "panel1", "remaining": {}},
            ),
            timeout=5,
        )
        # Swap the project object before the flush can run.
        eng.project = eng.project.model_copy(deep=True)

    await wait_for_condition(
        lambda: eng._project_revision >= 1,
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
    assert eng._project_revision == 1
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 2}
    assert eng.project.plugins["plug1"].config == {"x": 2}
    assert [m["revision"] for m in sent if m.get("type") == "project.reloaded"] == [1]


@pytest.mark.asyncio
async def test_plugin_save_config_under_lock_defers_without_deadlock(tmp_path):
    """A plugin hook (on_start / on_config_changed / an event handler) can
    call api.save_config while _sync_plugins holds the reconcile lock."""
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)

    async with eng._reload_lock:
        await asyncio.wait_for(
            eng._save_plugin_config("plug1", {"x": 2}), timeout=5
        )
        # In-memory config is already current for get_running_config parity.
        assert eng.project.plugins["plug1"].config == {"x": 2}

    await wait_for_condition(
        lambda: eng._project_revision == 1,
        message="deferred plugin-config persist never ran",
    )
    assert _disk_project(eng)["plugins"]["plug1"]["config"] == {"x": 2}


@pytest.mark.asyncio
async def test_plugin_save_config_failure_reverts_in_memory(tmp_path, monkeypatch):
    eng = _engine(tmp_path, plugins={
        "plug1": {"enabled": False, "config": {"x": 1}},
    })
    _capture_broadcasts(eng)

    async def boom(path, project):
        raise OSError("disk full")

    monkeypatch.setattr("server.core.engine.save_project_async", boom)

    with pytest.raises(OSError):
        await eng._save_plugin_config("plug1", {"x": 2})

    # Reverted so the poisoned config can't break every later save.
    assert eng.project.plugins["plug1"].config == {"x": 1}
    assert eng._project_revision == 0


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
    assert eng._project_revision == 1
