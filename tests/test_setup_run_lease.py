"""A setup run holds the device instance it started on, and says how it ended.

A setup action (a driver's provisioning wizard) runs in the background for as
long as its handler takes. While it runs, the device can be removed, edited
(a new instance under the same id), removed and added again, or the system
can stop. Each of those ends the run first: the run is cancelled, its wizard
is told why, and nothing it does afterwards reaches the device's next
instance: no settings written, no reconnect, no retry loop started.

The server keeps each device's last run result, so a wizard whose connection
dropped asks how its run ended: done or failed for a run of this process, and
"interrupted" for a run of an earlier one, which no longer exists.

Invented device (Acme), invented handlers.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.core import setup_actions
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.core.setup_actions import SetupActionRunner
from openavc.core.state_store import StateStore
from openavc.drivers.actions import resolve_device_actions
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.main import app


REMOVED = "The setup stopped because the device was removed."
REPLACED = "The setup stopped because the device's settings or driver changed. Run it again."
STOPPING = (
    "The setup stopped because the system is shutting down. "
    "Run it again once the system is back."
)
INTERRUPTED = "The setup was interrupted. Check the device and run it again."


class _AcmeSetup(BaseDriver):
    """A provisionable widget whose setup handler waits at a gate."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_setup_lease",
        "name": "Acme Setup Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "actions": [
            {"id": "provision", "kind": "setup", "label": "Provision", "availability": "offline"}
        ],
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.gate = asyncio.Event()
        self.at_gate = asyncio.Event()
        self.connects = 0

    async def connect(self) -> None:
        self.connects += 1
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def run_setup_action(self, action_id, params, progress) -> dict:
        await progress("Waiting for the device", 10)
        self.at_gate.set()
        await self.gate.wait()
        await self.request_config_update({"port": 9999})
        await self.request_reconnect()
        return {"message": "Provisioned."}


class _StubbornSetup(_AcmeSetup):
    """A handler that ignores being cancelled and goes on to write and
    reconnect: what the device's next instance must never receive."""

    async def run_setup_action(self, action_id, params, progress) -> dict:
        self.at_gate.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            pass
        await self.gate.wait()
        try:
            await self.request_config_update({"port": 9999})
        except Exception as exc:
            self.refused_write = exc
        try:
            await self.request_reconnect()
        except Exception as exc:
            self.refused_reconnect = exc
        return {"message": "Provisioned."}


class _QuickSetup(_AcmeSetup):
    async def run_setup_action(self, action_id, params, progress) -> dict:
        await progress("Talking to the device", 50)
        return {"message": "Found it."}


class _FailingSetup(_AcmeSetup):
    async def run_setup_action(self, action_id, params, progress) -> dict:
        return {"success": False, "message": "The password was refused."}


class _FakeEngine:
    def __init__(self, dm: DeviceManager) -> None:
        self.devices = dm
        self.project = ProjectConfig(
            project=ProjectMeta(id="t", name="Test"),
            devices=[
                DeviceConfig(id=d, driver="acme_setup_lease", name=d, config={})
                for d in ("dev1", "dev2")
            ],
            connections={
                "dev1": {"host": "10.0.0.1", "port": 23},
                "dev2": {"host": "10.0.0.2", "port": 23},
            },
        )
        self.sent: list[dict[str, Any]] = []

    async def persist_bookkeeping_change(self, mutate, *, on_error=None, runtime_write=None):
        proposed = self.project.model_copy(deep=True)
        mutate(proposed)
        self.project = proposed
        if runtime_write is not None:
            runtime_write()
        return "rev"

    def take_device_settings(self, device_id: str, delta: dict[str, Any]) -> None:
        self.devices.merge_live_config(device_id, delta)

    async def broadcast_ws(self, message: dict[str, Any], *, client_type: str | None = None) -> None:
        self.sent.append(message)


def _config(device_id: str, port: int = 23) -> dict[str, Any]:
    return {
        "id": device_id, "driver": "acme_setup_lease", "name": device_id,
        "config": {"host": "10.0.0.1", "port": port}, "enabled": True,
    }


@pytest.fixture
def env():
    """A device manager with dev1 and dev2 on whichever setup driver a test
    registers, and a setup runner wired to it as the engine wires them."""
    made: list[DeviceManager] = []

    async def build(driver_cls=_AcmeSetup):
        register_driver(driver_cls)
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        dm = DeviceManager(state, events)
        made.append(dm)
        for device_id in ("dev1", "dev2"):
            await dm.add_device(_config(device_id), defer_connect=True)
            dm.get_driver(device_id).set_state("connected", False)
        engine = _FakeEngine(dm)
        runner = SetupActionRunner(engine)
        dm.end_setup_run = getattr(runner, "end_run", None)
        return runner, engine, dm

    yield build
    for dm in made:
        for task in list(dm._reconnect_tasks.values()):
            task.cancel()
    unregister_driver("acme_setup_lease")


def _action(dm: DeviceManager, device_id: str = "dev1") -> dict:
    driver = dm.get_driver(device_id)
    return next(a for a in resolve_device_actions(driver.DRIVER_INFO) if a["kind"] == "setup")


async def _start_at_gate(runner, dm, device_id: str = "dev1"):
    driver = dm.get_driver(device_id)
    started = await runner.start(device_id, _action(dm, device_id), {})
    await asyncio.wait_for(driver.at_gate.wait(), 2)
    task = next(iter(runner._tasks))
    return started["run_id"], driver, task


def _last_progress(engine: _FakeEngine, device_id: str = "dev1") -> dict:
    return [
        m for m in engine.sent
        if m["type"] == "action.progress" and m["device_id"] == device_id
    ][-1]


async def _turns(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# --- each way a device's instance is retired ends its run first ------------------


async def test_removing_a_device_ends_its_setup_run_and_the_wizard_is_told(env):
    runner, engine, dm = await env()
    _run_id, _driver, task = await _start_at_gate(runner, dm)

    await dm.remove_device("dev1")

    assert task.done()
    last = _last_progress(engine)
    assert (last["status"], last["error"]) == ("error", REMOVED)
    assert not runner.is_running("dev1")
    assert "dev1" not in dm._reconnect_tasks
    assert "dev1" not in dm._intentional_disconnect


async def test_editing_a_device_ends_its_setup_run_before_the_new_instance_starts(env):
    runner, engine, dm = await env()
    _run_id, old, task = await _start_at_gate(runner, dm)

    await dm.update_device("dev1", _config("dev1", port=24), defer_connect=True)

    assert task.done()
    last = _last_progress(engine)
    assert (last["status"], last["error"]) == ("error", REPLACED)
    new = dm.get_driver("dev1")
    assert new is not old
    assert new.config["port"] == 24
    assert new.connects == 0
    assert engine.project.connections["dev1"]["port"] == 23


async def test_stopping_ends_a_setup_run_and_starts_no_retry_after_it(env):
    runner, engine, dm = await env()
    _run_id, driver, task = await _start_at_gate(runner, dm)

    await dm.disconnect_all()
    driver.gate.set()
    await _turns()

    assert task.done()
    last = _last_progress(engine)
    assert (last["status"], last["error"]) == ("error", STOPPING)
    # The stop's mark stays, and nothing dials the device behind the stop.
    assert "dev1" in dm._intentional_disconnect
    assert "dev1" not in dm._reconnect_tasks
    assert driver.connects == 0


async def test_the_runner_s_own_stop_ends_every_run(env):
    runner, engine, dm = await env()
    _run_id, _driver, task = await _start_at_gate(runner, dm)

    await runner.stop()

    assert task.done()
    assert _last_progress(engine)["error"] == STOPPING
    assert "dev1" not in dm._reconnect_tasks


async def test_a_run_ended_on_another_device_leaves_this_one_running(env):
    runner, _engine, dm = await env()
    _run_id, _driver, task = await _start_at_gate(runner, dm, "dev1")

    await dm.remove_device("dev2")

    assert not task.done()
    assert runner.is_running("dev1")
    await dm.remove_device("dev1")


# --- a handler that ignores the cancel never reaches the next instance ------------


async def test_a_stubborn_run_writes_nothing_to_the_device_s_next_instance(env, monkeypatch):
    monkeypatch.setattr(setup_actions, "RUN_END_SECONDS", 0.05, raising=False)
    runner, engine, dm = await env(_StubbornSetup)
    _run_id, old, task = await _start_at_gate(runner, dm)

    await dm.update_device("dev1", _config("dev1", port=24), defer_connect=True)
    new = dm.get_driver("dev1")
    # Its entry is its own until it ends: no second run starts beside it.
    assert runner.is_running("dev1")

    old.gate.set()
    await asyncio.wait_for(task, 2)

    assert new.config["port"] == 24
    assert dm.get_device_config("dev1")["config"]["port"] == 24
    assert engine.project.connections["dev1"]["port"] == 23
    assert new.connects == 0
    assert old.connects == 0
    assert old.refused_write is not None
    assert old.refused_reconnect is not None
    assert _last_progress(engine)["error"] == REPLACED
    assert not runner.is_running("dev1")
    # Its end went to the instance it started on, not the new one's retry.
    assert "dev1" not in dm._reconnect_tasks


async def test_a_stubborn_run_reaches_nothing_once_its_device_is_removed_and_added_again(
    env, monkeypatch,
):
    monkeypatch.setattr(setup_actions, "RUN_END_SECONDS", 0.05, raising=False)
    runner, engine, dm = await env(_StubbornSetup)
    _run_id, old, task = await _start_at_gate(runner, dm)

    await dm.remove_device("dev1")
    await dm.add_device(_config("dev1", port=25), defer_connect=True)
    again = dm.get_driver("dev1")
    old.gate.set()
    await asyncio.wait_for(task, 2)

    assert again.config["port"] == 25
    assert again.connects == 0
    assert engine.project.connections["dev1"]["port"] == 23
    assert _last_progress(engine)["error"] == REMOVED


# --- how a run ended, asked again ---------------------------------------------------


async def test_a_reconnecting_wizard_hears_a_finished_run_was_done(env):
    runner, _engine, dm = await env(_QuickSetup)
    started = await runner.start("dev1", _action(dm), {})
    await asyncio.gather(*runner._tasks)

    run = runner.outcome("dev1", started["run_id"])

    assert run["status"] == "done"
    assert run["result"] == {"message": "Found it."}
    assert run["run_id"] == started["run_id"]


async def test_a_reconnecting_wizard_hears_a_failed_run_failed(env):
    runner, _engine, dm = await env(_FailingSetup)
    started = await runner.start("dev1", _action(dm), {})
    await asyncio.gather(*runner._tasks)

    run = runner.outcome("dev1", started["run_id"])

    assert (run["status"], run["error"]) == ("error", "The password was refused.")


async def test_a_run_still_going_answers_running(env):
    runner, _engine, dm = await env()
    run_id, _driver, _task = await _start_at_gate(runner, dm)

    assert runner.outcome("dev1", run_id)["status"] == "running"
    await dm.remove_device("dev1")


async def test_a_removed_device_s_run_still_answers_how_it_ended(env):
    runner, _engine, dm = await env()
    run_id, _driver, _task = await _start_at_gate(runner, dm)
    await dm.remove_device("dev1")

    run = runner.outcome("dev1", run_id)

    assert (run["status"], run["error"]) == ("error", REMOVED)


async def test_a_run_from_an_earlier_process_reads_as_interrupted(env):
    runner, engine, dm = await env(_QuickSetup)
    started = await runner.start("dev1", _action(dm), {})
    await asyncio.gather(*runner._tasks)

    after_restart = SetupActionRunner(engine)
    run = after_restart.outcome("dev1", started["run_id"])

    assert (run["status"], run["error"]) == ("error", INTERRUPTED)
    # A run id from before run ids named their process reads the same.
    old_style = after_restart.outcome("dev1", "0123456789abcdef0123456789abcdef")
    assert old_style["error"] == INTERRUPTED


async def test_a_run_of_this_process_no_longer_kept_is_not_found(env, monkeypatch):
    monkeypatch.setattr(setup_actions, "KEPT_RESULTS", 1, raising=False)
    runner, _engine, dm = await env(_QuickSetup)
    first = await runner.start("dev1", _action(dm), {})
    await asyncio.gather(*runner._tasks)
    second = await runner.start("dev2", _action(dm, "dev2"), {})
    await asyncio.gather(*runner._tasks)

    assert runner.outcome("dev1", first["run_id"]) is None
    assert runner.outcome("dev2", second["run_id"])["status"] == "done"
    # A device's newer run replaces the result of its earlier one.
    third = await runner.start("dev2", _action(dm, "dev2"), {})
    await asyncio.gather(*runner._tasks)
    assert runner.outcome("dev2", second["run_id"]) is None
    assert runner.outcome("dev2", third["run_id"])["status"] == "done"


# --- the REST door --------------------------------------------------------------------


@pytest.fixture
async def client(env):
    runner, engine, dm = await env(_QuickSetup)
    engine.setup_actions = runner
    engine.state = dm.state
    engine.events = dm.events
    engine._running = True
    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), runner, dm
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


async def test_the_api_answers_how_a_run_ended(client):
    c, runner, dm = client
    started = await runner.start("dev1", _action(dm), {})
    await asyncio.gather(*runner._tasks)

    resp = c.get(f"/api/devices/dev1/setup-runs/{started['run_id']}")

    assert resp.status_code == 200
    assert resp.json()["run"]["status"] == "done"


async def test_the_api_answers_an_earlier_process_s_run_without_a_live_device(client):
    c, _runner, _dm = client

    resp = c.get("/api/devices/gone/setup-runs/0123abcd-ffff")

    assert resp.status_code == 200
    assert resp.json()["run"]["error"] == INTERRUPTED


async def test_the_api_says_a_run_it_no_longer_keeps_is_not_found(client):
    c, runner, _dm = client

    resp = c.get(f"/api/devices/dev1/setup-runs/{runner._start_id}-unknown")

    assert resp.status_code == 404
    assert resp.json()["detail"] == (
        "That setup run has ended and is no longer kept. Check the device."
    )
