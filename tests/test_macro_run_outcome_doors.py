"""A script and a plugin that run a macro are told how it ended.

``await macros.execute(id)`` in a script and ``await api.macro_execute(id)`` in
a plugin return the word every other door reports: ``completed``, ``failed``,
``cancelled`` or ``skipped``. Before, both returned None, so neither could tell
a run whose every step failed, or one its own Overlap turned away, from a clean
one.

A script answering an event a running macro emitted calls inside that macro's
chain, so the loop guards still see it, but it is not one of that macro's
steps: it is told the outcome like any other caller, where it used to get the
failed step's exception, worded for the Programmer, raised into its code.
"""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.plugin_api import PluginAPI
from openavc.core.plugin_registry import PluginRegistry
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import StateStore

FAILS = {"action": "device.command", "device": "acme_widget", "command": "power_on"}

MACROS = [
    {"id": "clean", "name": "Clean", "steps": [
        {"action": "state.set", "key": "var.clean_ran", "value": True}]},
    {"id": "fails", "name": "Fails", "steps": [FAILS]},
    {"id": "stops", "name": "Stops", "stop_on_error": True, "steps": [FAILS]},
    {"id": "solo", "name": "Solo", "overlap": "skip", "steps": [
        {"action": "delay", "seconds": 0.5}]},
    {"id": "long", "name": "Long", "steps": [{"action": "delay", "seconds": 0.5}]},
    {"id": "outer", "name": "Outer", "steps": [
        {"action": "event.emit", "event": "custom.inside"}]},
]


async def _until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.fixture
def room(tmp_path):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    macros = MacroEngine(state, events, devices)
    macros.load_macros(MACROS)
    (tmp_path / "scripts").mkdir()
    scripts = ScriptEngine(state, events, devices, tmp_path, macro_engine=macros)
    scripts.install()
    errors: list[dict] = []

    async def on_error(_event, payload):
        errors.append(payload)

    events.on("script.error", on_error)
    yield state, events, macros, scripts, tmp_path, errors
    scripts.unload_all()


def _load(scripts: ScriptEngine, project_dir, code: str) -> None:
    (project_dir / "scripts" / "runs.py").write_text(textwrap.dedent(code), encoding="utf-8")
    scripts.load_scripts([{"id": "runs", "file": "runs.py", "enabled": True}])


# Each run's outcome, written where the test can read it.
RECORDS_OUTCOMES = """\
    from openavc import on_event, macros, state

    @on_event("custom.run")
    async def run(event):
        which = event.payload["macro"]
        state.set("var.outcome_" + which, await macros.execute(which))

    @on_event("custom.inside")
    async def inside(event):
        for which in ("fails", "stops"):
            state.set("var.inside_" + which, await macros.execute(which))
        try:
            await macros.execute("outer")
        except ValueError as e:
            state.set("var.loop_refused", str(e))
"""


async def test_a_script_is_told_how_each_run_ended(room):
    state, events, macros, scripts, project_dir, errors = room
    _load(scripts, project_dir, RECORDS_OUTCOMES)

    for which in ("clean", "fails", "stops"):
        await events.emit("custom.run", {"macro": which})
    held = asyncio.create_task(macros.execute("solo"))
    await _until(lambda: macros.is_macro_running("solo"))
    await events.emit("custom.run", {"macro": "solo"})
    await held

    assert state.get("var.outcome_clean") == "completed"
    assert state.get("var.outcome_fails") == "failed"
    assert state.get("var.outcome_stops") == "failed"
    assert state.get("var.outcome_solo") == "skipped"
    assert errors == []


async def test_a_script_is_told_a_run_somebody_cancelled(room):
    state, events, macros, scripts, project_dir, errors = room
    _load(scripts, project_dir, RECORDS_OUTCOMES)

    waiting = asyncio.create_task(events.emit("custom.run", {"macro": "long"}))
    await _until(lambda: macros.is_macro_running("long"))
    assert await macros.cancel("long") is True
    await waiting

    assert state.get("var.outcome_long") == "cancelled"
    assert errors == []


async def test_inside_a_running_macro_a_script_is_told_the_outcome_not_raised_at(room):
    state, events, macros, scripts, project_dir, errors = room
    _load(scripts, project_dir, RECORDS_OUTCOMES)

    assert await macros.execute("outer") == "completed"

    assert state.get("var.inside_fails") == "failed"
    assert state.get("var.inside_stops") == "failed"
    # Still inside the chain: a loop back into the macro that is running is refused.
    assert "circular" in state.get("var.loop_refused")
    assert errors == []


async def test_a_plugin_is_told_how_its_run_ended(room):
    state, events, macros, _scripts, _dir, _errors = room
    api = PluginAPI(
        plugin_id="acme_plugin",
        capabilities=["macro_execute"],
        config={},
        registry=PluginRegistry("acme_plugin"),
        state_store=state,
        event_bus=events,
        macro_engine=macros,
        device_manager=None,
        platform_id="test",
    )

    assert await api.macro_execute("clean") == "completed"
    assert await api.macro_execute("fails") == "failed"
    held = asyncio.create_task(macros.execute("solo"))
    await _until(lambda: macros.is_macro_running("solo"))
    assert await api.macro_execute("solo") == "skipped"
    await held
