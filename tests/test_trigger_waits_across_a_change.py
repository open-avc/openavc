"""A fire waiting in a trigger, across a project change.

A trigger can hold a fire before it runs its macro: a startup trigger's delay,
a debounce, a delay that re-checks, a queue behind the macro's last run. Every
device, variable, connection, plugin or macro edit rebuilds the triggers, and
the rebuild used to cancel each of those fires and tell nobody: the card went
on saying "Delaying" until its timer ran out, and the macro never ran.

The rule now: a fire waiting on a trigger defined as it was goes on through the
rebuild and runs the macro as now saved, as a later fire would. One waiting on
a trigger the change altered or deleted is ended with ``trigger.skipped``
``project_changed``, which is also the trigger's last run. A shutdown still
cancels every waiting fire.

The engine tests drive the rebuild the reconcile runs: ``pause``, then
``load_triggers`` with the new definitions, then ``start``. The last ones run
a real change through ``Engine.apply_project``, and one that fails part-way.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import openavc.core.trigger_engine as te
from openavc.core.device_manager import DeviceManager
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_text import skip_reason
from openavc.core.project_changes import ProjectNotAppliedError
from openavc.core.project_diff import ProjectOrigin
from openavc.core.project_loader import ProjectConfig, load_project
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine
from tests.test_reconcile_stages import _Devices, _fail_right_after, _Mesh, _Plugins

PROJECT_CHANGED = skip_reason("project_changed")


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def macros(core):
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


@pytest.fixture
def triggers(core, macros):
    state, events = core
    return TriggerEngine(state, events, macros)


def _heard(events: EventBus, pattern: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def on(_event: str, payload: dict[str, Any]) -> None:
        seen.append(dict(payload or {}))

    events.on(pattern, on)
    return seen


def _macro(value: str, **trigger: Any) -> dict[str, Any]:
    """Macro ``m``, which writes ``value`` to ``var.ran``, with trigger ``t``."""
    return {
        "id": "m", "name": "M",
        "steps": [{"action": "state.set", "key": "var.ran", "value": value}],
        "triggers": [{"id": "t", "type": "event", "event_pattern": "go", "enabled": True,
                      **trigger}],
    }


async def _rebuild(triggers: TriggerEngine, macros: MacroEngine, *project: dict) -> None:
    """What the reconcile does to the triggers on a change: pause, the macros
    as now saved, the new definitions, start."""
    await triggers.pause()
    macros.load_macros(list(project))
    triggers.load_triggers(list(project))
    await triggers.start(fire_startup=False)


async def _load(triggers: TriggerEngine, macros: MacroEngine, *project: dict) -> None:
    macros.load_macros(list(project))
    triggers.load_triggers(list(project))
    await triggers.start(fire_startup=False)


# --- a trigger defined as it was keeps its waiting fire ---------------------------


@pytest.mark.parametrize("wait", [{"delay_seconds": 0.2}, {"debounce_seconds": 0.2}])
async def test_a_waiting_fire_goes_on_through_an_unrelated_change(
    triggers, macros, core, wait,
):
    state, events = core
    skipped = _heard(events, "trigger.skipped")
    await _load(triggers, macros, _macro("before", **wait))
    await events.emit("go", {})
    await asyncio.sleep(0.05)

    await _rebuild(triggers, macros, _macro("as now saved", **wait))
    await asyncio.sleep(0.35)

    assert state.get("var.ran") == "as now saved"
    assert skipped == []
    await triggers.stop()


async def test_a_queued_fire_goes_on_through_an_unrelated_change(
    triggers, macros, core, monkeypatch,
):
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    state, events = core
    running = {"v": True}
    macros.is_macro_running = lambda _mid: running["v"]
    await _load(triggers, macros, _macro("before", overlap="queue"))
    await events.emit("go", {})
    await asyncio.sleep(0.05)
    assert len(triggers._triggers["t"].pending_queue) == 1

    await _rebuild(triggers, macros, _macro("as now saved", overlap="queue"))
    running["v"] = False
    await asyncio.sleep(0.15)

    assert state.get("var.ran") == "as now saved"
    await triggers.stop()


async def test_a_startup_triggers_delay_goes_on_through_an_edit(triggers, macros, core):
    """An edit never fires a startup trigger again, so a delay it cancelled
    was the trigger's one fire until the next restart."""
    state, _ = core
    startup = {"id": "m", "name": "M",
               "steps": [{"action": "state.set", "key": "var.ran", "value": "yes"}],
               "triggers": [{"id": "t", "type": "startup", "delay_seconds": 0.2}]}
    macros.load_macros([startup])
    triggers.load_triggers([startup])
    await triggers.start(fire_startup=True)
    await asyncio.sleep(0.05)

    await _rebuild(triggers, macros, startup)
    await asyncio.sleep(0.3)

    assert state.get("var.ran") == "yes"
    await triggers.stop()


async def test_a_whole_new_projects_start_fires_a_kept_startup_trigger_once(
    triggers, macros, core,
):
    state, _ = core
    count = {"n": 0}
    state.subscribe("var.ran", lambda *_a: count.__setitem__("n", count["n"] + 1))
    startup = {"id": "m", "name": "M",
               "steps": [{"action": "state.set", "key": "var.ran", "value": "yes"}],
               "triggers": [{"id": "t", "type": "startup", "delay_seconds": 0.2}]}
    macros.load_macros([startup])
    triggers.load_triggers([startup])
    await triggers.start(fire_startup=True)
    await asyncio.sleep(0.05)

    await triggers.pause()
    triggers.load_triggers([startup])
    await triggers.start(fire_startup=True)
    await asyncio.sleep(0.3)

    assert count["n"] == 1
    await triggers.stop()


async def test_a_wait_that_ends_while_the_triggers_are_paused_waits_for_the_restart(
    triggers, macros, core,
):
    """The reconcile runs between the pause and the restart. A delay that
    ended in it used to find the triggers stopped and drop the fire; it now
    re-checks once the change has settled."""
    state, events = core
    await _load(triggers, macros, _macro("before", delay_seconds=0.05))
    await events.emit("go", {})
    await asyncio.sleep(0.01)

    await triggers.pause()
    await asyncio.sleep(0.15)
    assert state.get("var.ran") is None
    macros.load_macros([_macro("as now saved", delay_seconds=0.05)])
    triggers.load_triggers([_macro("as now saved", delay_seconds=0.05)])
    await triggers.start(fire_startup=False)
    await asyncio.sleep(0.05)

    assert state.get("var.ran") == "as now saved"
    await triggers.stop()


async def test_a_kept_debounce_is_still_reset_by_the_next_event(triggers, macros, core):
    state, events = core
    count = {"n": 0}
    state.subscribe("var.ran", lambda *_a: count.__setitem__("n", count["n"] + 1))
    await _load(triggers, macros, _macro("x", debounce_seconds=0.5))
    await events.emit("go", {})
    await asyncio.sleep(0.05)

    await _rebuild(triggers, macros, _macro("x", debounce_seconds=0.5))
    await events.emit("go", {})
    await asyncio.sleep(0.3)
    assert count["n"] == 0, "the kept debounce was not reset by the new event"
    await asyncio.sleep(0.5)

    assert count["n"] == 1
    await triggers.stop()


async def test_a_renamed_macro_keeps_its_triggers_waiting_fire(triggers, macros, core):
    state, events = core
    await _load(triggers, macros, _macro("x", delay_seconds=0.2))
    await events.emit("go", {})
    await asyncio.sleep(0.05)

    await _rebuild(triggers, macros, {**_macro("x", delay_seconds=0.2), "name": "Renamed"})
    await asyncio.sleep(0.3)

    assert state.get("var.ran") == "x"
    assert triggers.list_triggers()[0]["macro_name"] == "Renamed"
    await triggers.stop()


# --- a trigger the change altered or deleted ends its waiting fire ----------------


async def test_a_waiting_fire_on_a_changed_trigger_is_ended_and_says_why(
    triggers, macros, core,
):
    state, events = core
    skipped = _heard(events, "trigger.skipped")
    completed = _heard(events, "trigger.completed")
    await _load(triggers, macros, _macro("x", delay_seconds=0.2))
    await events.emit("go", {})
    await asyncio.sleep(0.05)

    await _rebuild(triggers, macros, _macro("x", delay_seconds=0.2, event_pattern="other"))
    await asyncio.sleep(0.3)

    assert state.get("var.ran") is None
    assert skipped == [{"trigger_id": "t", "reason": "project_changed", **PROJECT_CHANGED}]
    assert completed == [{
        "trigger_id": "t", "macro_id": "m", "macro_name": "M", "trigger_type": "event",
        "outcome": "skipped", **PROJECT_CHANGED,
    }]
    status = triggers.list_triggers()[0]
    assert status["last_outcome"] == "skipped"
    assert status["last_reason"] == PROJECT_CHANGED
    assert status["has_pending_delay"] is False
    await triggers.stop()


async def test_each_queued_fire_on_a_changed_trigger_says_why(
    triggers, macros, core, monkeypatch,
):
    monkeypatch.setattr(te, "_QUEUE_POLL_INTERVAL", 0.02)
    state, events = core
    skipped = _heard(events, "trigger.skipped")
    macros.is_macro_running = lambda _mid: True
    await _load(triggers, macros, _macro("x", overlap="queue"))
    await events.emit("go", {})
    await events.emit("go", {})
    await asyncio.sleep(0.05)

    await _rebuild(triggers, macros, _macro("x", overlap="queue", cooldown_seconds=5))
    await asyncio.sleep(0.05)

    assert [s["code"] for s in skipped] == ["project_changed", "project_changed"]
    assert triggers._triggers["t"].pending_queue == []
    await triggers.stop()


async def test_a_deleted_triggers_waiting_fire_says_why_and_leaves_no_record(
    triggers, macros, core,
):
    state, events = core
    skipped = _heard(events, "trigger.skipped")
    await _load(triggers, macros, _macro("x", debounce_seconds=0.2))
    await events.emit("go", {})
    await asyncio.sleep(0.05)

    await _rebuild(triggers, macros, {**_macro("x"), "triggers": []})
    await asyncio.sleep(0.3)

    assert state.get("var.ran") is None
    assert [s["code"] for s in skipped] == ["project_changed"]
    assert "t" not in triggers._last_run


async def test_a_trigger_moved_to_another_macro_ends_its_waiting_fire(
    triggers, macros, core,
):
    state, events = core
    skipped = _heard(events, "trigger.skipped")
    other = {"id": "m2", "name": "M2", "steps": [], "triggers": []}
    await _load(triggers, macros, _macro("x", delay_seconds=0.2), other)
    await events.emit("go", {})
    await asyncio.sleep(0.05)

    moved = _macro("x", delay_seconds=0.2)
    await _rebuild(triggers, macros, {**moved, "triggers": []},
                   {**other, "triggers": moved["triggers"]})
    await asyncio.sleep(0.3)

    assert state.get("var.ran") is None
    assert [s["code"] for s in skipped] == ["project_changed"]
    await triggers.stop()


async def test_a_fire_running_its_macro_is_not_ended_when_its_trigger_changes(
    triggers, macros, core,
):
    """Past its wait the fire is a running macro, the macro engine's to end."""
    state, events = core
    skipped = _heard(events, "trigger.skipped")
    long_run = {**_macro("x", delay_seconds=0.01),
                "steps": [{"action": "delay", "seconds": 0.2},
                          {"action": "state.set", "key": "var.ran", "value": "done"}]}
    await _load(triggers, macros, long_run)
    await events.emit("go", {})
    await asyncio.sleep(0.08)
    assert macros.is_macro_running("m")

    await _rebuild(triggers, macros, {**long_run, "triggers": [
        {**long_run["triggers"][0], "event_pattern": "other"}]})
    await asyncio.sleep(0.3)

    assert state.get("var.ran") == "done"
    assert skipped == []
    await triggers.stop()


# --- the shutdown still cancels everything ---------------------------------------


async def test_a_shutdown_still_cancels_every_waiting_fire(triggers, macros, core):
    state, events = core
    skipped = _heard(events, "trigger.skipped")
    await _load(triggers, macros, _macro("x", delay_seconds=0.1))
    await events.emit("go", {})
    await asyncio.sleep(0.02)
    task = triggers._triggers["t"].delay_task

    await triggers.stop()
    await asyncio.sleep(0.15)

    assert task.done() and state.get("var.ran") is None
    assert skipped == []


# --- through the engine's own change -----------------------------------------------


def _room() -> dict:
    return {
        "openavc_version": "0.17.0",
        "project": {"id": "p", "name": "Room"},
        "variables": [],
        "macros": [{
            "id": "m", "name": "M",
            "steps": [{"action": "state.set", "key": "var.ran", "value": "before"}],
            "triggers": [{"id": "t", "type": "event", "event_pattern": "custom.go",
                          "delay_seconds": 0.3}],
        }],
        "devices": [{"id": "d1", "driver": "generic_tcp", "name": "D1", "config": {}}],
        "connections": {"d1": {"host": "192.0.2.10", "port": 23}},
        "ui": {"settings": {}, "pages": []},
    }


async def _engine(tmp_path) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_room()), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    eng.devices = _Devices()
    eng.plugin_loader = _Plugins()
    eng.isc = _Mesh()
    eng.mdns_advertiser = MagicMock()
    eng._load_project_drivers = lambda: None
    eng._start_variables()
    await eng._sync_devices(defer_connect=True)
    eng._load_macro_definitions(macros=True, groups=True)
    await eng._restart_triggers(fire_startup=False)
    return eng


def _edited(**macro: Any) -> ProjectConfig:
    """The room with its device renamed, and the macro changed by ``macro``."""
    project = copy.deepcopy(_room())
    project["devices"][0]["name"] = "Lectern display"
    project["macros"][0].update(macro)
    return ProjectConfig(**project)


async def test_a_device_edit_keeps_a_delayed_fire(tmp_path):
    eng = await _engine(tmp_path)
    await eng.events.emit("custom.go", {})
    await asyncio.sleep(0.05)

    await eng.apply_project(_edited(), origin=ProjectOrigin.EDIT)
    await asyncio.sleep(0.4)

    assert eng.state.get("var.ran") == "before"
    await eng.triggers.stop()


async def test_a_macro_edit_keeps_the_fire_and_runs_the_macro_as_now_saved(tmp_path):
    eng = await _engine(tmp_path)
    await eng.events.emit("custom.go", {})
    await asyncio.sleep(0.05)

    steps = [{"action": "state.set", "key": "var.ran", "value": "as now saved"}]
    await eng.apply_project(_edited(steps=steps), origin=ProjectOrigin.EDIT)
    await asyncio.sleep(0.4)

    assert eng.state.get("var.ran") == "as now saved"
    await eng.triggers.stop()


@pytest.mark.parametrize("stage", ["devices", "trigger restart"])
async def test_a_change_that_fails_keeps_the_waiting_fire(tmp_path, stage):
    """Undone before or after the triggers' rebuild, the change leaves the
    fire waiting on the trigger it never altered."""
    eng = await _engine(tmp_path)
    await eng.events.emit("custom.go", {})
    await asyncio.sleep(0.05)
    _fail_right_after(eng, stage)

    with pytest.raises(ProjectNotAppliedError):
        await eng.apply_project(_edited(), origin=ProjectOrigin.EDIT)
    await asyncio.sleep(0.4)

    assert eng.state.get("var.ran") == "before"
    await eng.triggers.stop()
