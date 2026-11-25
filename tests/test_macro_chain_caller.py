"""A script answering an event a running macro emitted is a caller, not a step.

Such a script runs inside that macro's call chain, so a loop back into the
macro is still refused. But it is not one of the macro's steps, and a macro it
runs that fails reports the failure the way it does to any other caller: on
``macro.error``, with the run returned rather than raised. Before, the chain
alone made the call a subroutine, so a macro set to Stop on Error raised its
failed step, worded for the Programmer, into the script's code: the handler
stopped there and the failure was said twice, once more as a script error.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import StateStore

MACROS = [
    {"id": "stops", "name": "Stops", "stop_on_error": True, "steps": [
        {"action": "device.command", "device": "acme_widget", "command": "power_on"}]},
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
    ended: list[dict] = []

    async def on_error(_event, payload):
        errors.append(payload)

    async def on_macro_error(_event, payload):
        ended.append(payload)

    events.on("script.error", on_error)
    events.on("macro.error.stops", on_macro_error)
    yield state, macros, scripts, tmp_path, errors, ended
    scripts.unload_all()


def _load(scripts: ScriptEngine, project_dir, code: str) -> None:
    (project_dir / "scripts" / "inside.py").write_text(textwrap.dedent(code), encoding="utf-8")
    scripts.load_scripts([{"id": "inside", "file": "inside.py", "enabled": True}])


async def test_a_failed_run_awaited_inside_a_running_macro_is_not_raised_into_the_script(room):
    state, macros, scripts, project_dir, errors, ended = room
    _load(scripts, project_dir, """\
        from openavc import on_event, macros, state

        @on_event("custom.inside")
        async def inside(event):
            await macros.execute("stops")
            state.set("var.went_on", True)
            try:
                await macros.execute("outer")
            except ValueError as e:
                state.set("var.loop_refused", str(e))
    """)

    assert await macros.execute("outer") == "completed"

    assert state.get("var.went_on") is True
    assert len(ended) == 1
    assert errors == []
    # Still inside the chain: a loop back into the running macro is refused.
    assert "circular" in state.get("var.loop_refused")


async def test_a_failed_run_started_inside_a_running_macro_is_said_once(room, caplog):
    state, macros, scripts, project_dir, errors, ended = room
    _load(scripts, project_dir, """\
        from openavc import on_event, macros

        @on_event("custom.inside")
        async def inside(event):
            macros.start("stops")
    """)

    with caplog.at_level(logging.ERROR):
        await macros.execute("outer")
        assert await _until(lambda: ended and not macros._detached)

    # Said by the run's own macro.error, not again as a task nobody awaited.
    assert not any("nobody waiting" in r.getMessage() for r in caplog.records)
    assert errors == []
