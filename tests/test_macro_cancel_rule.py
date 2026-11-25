"""Whose cancel a macro run reports, and whose it passes on.

A run executes in its caller's task, so two kinds of cancel reach it. One is
the macro engine's own: ``cancel`` (a person's Cancel, ``POST /cancel``),
``cancel_all`` (a shutdown, a project change) or a ``cancel_group``
preemption. That cancel is the run's outcome: the run ends on
``macro.cancelled`` and ``execute`` returns ``"cancelled"`` to whoever awaited
it, which carries on. The other is a cancel of the caller itself: a script
handler cut off at its time limit, the handler of an event a macro emitted when
that macro is cancelled, a script being unloaded. The run still ends on
``macro.cancelled``, and the cancel goes on up, because what asked for the run
is being stopped and must not run its next line.

Before, every cancel was swallowed as the run's outcome, so a script handler
cut off at its limit while it waited on a macro carried on past the await with
no limit at all, and a cancel the engine consumed was left counted against the
task, where a later time limit read it as a cancel and not as a timeout.
"""

from __future__ import annotations

import asyncio
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import StateStore

LONG = {"id": "long", "name": "Long", "steps": [{"action": "delay", "seconds": 0.5}]}


def _engine(macros: list[dict]) -> tuple[MacroEngine, EventBus, StateStore]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = MagicMock()
    devices.send_command = AsyncMock()
    engine = MacroEngine(state, events, devices)
    engine.load_macros(macros)
    return engine, events, state


def _ends(events: EventBus) -> list[str]:
    seen: list[str] = []

    async def _on(event, _payload):
        seen.append(event)

    for ending in ("completed", "cancelled", "error"):
        events.on(f"macro.{ending}.*", _on)
    return seen


async def _until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# --- the engine's own cancel is the run's outcome -----------------------------------


async def test_a_cancel_the_engine_made_is_returned_and_leaves_the_caller_clean():
    engine, events, _state = _engine([LONG])
    ends = _ends(events)
    seen: dict = {}

    async def caller():
        seen["outcome"] = await engine.execute("long")
        # Consumed, so nothing is left counted against this task: a time limit
        # it meets later reads as a timeout, not as this cancel.
        seen["cancelling"] = asyncio.current_task().cancelling()
        seen["next_line"] = True

    task = asyncio.create_task(caller())
    await _until(lambda: engine.is_macro_running("long"))
    assert await engine.cancel("long") is True
    await task

    assert seen == {"outcome": "cancelled", "cancelling": 0, "next_line": True}
    assert ends == ["macro.cancelled.long"]


async def test_a_preempted_run_returns_cancelled_to_its_caller():
    engine, events, _state = _engine([
        {**LONG, "id": "on", "name": "On", "cancel_group": "power"},
        {**LONG, "id": "off", "name": "Off", "cancel_group": "power",
         "steps": [{"action": "state.set", "key": "var.off", "value": True}]},
    ])
    first = asyncio.create_task(engine.execute("on"))
    await _until(lambda: engine.is_macro_running("on"))

    assert await engine.execute("off") == "completed"
    assert await first == "cancelled"


async def test_a_run_preempting_its_own_macro_starts_after_the_old_one_ends():
    # The IDE keeps one progress record per macro: the preempted run's end has
    # to come first, or it would clear the new run's progress.
    engine, events, _state = _engine([{**LONG, "id": "on", "name": "On", "cancel_group": "power"}])
    seen: list[str] = []

    async def _on(event, _payload):
        seen.append(event)

    events.on("macro.*", _on)
    first = asyncio.create_task(engine.execute("on"))
    await _until(lambda: engine.is_macro_running("on"))
    second = asyncio.create_task(engine.execute("on"))

    assert await first == "cancelled"
    assert await second == "completed"
    lifecycle = [e for e in seen if e.split(".")[1] in ("started", "cancelled", "completed")]
    assert lifecycle == [
        "macro.started.on", "macro.cancelled.on", "macro.started.on", "macro.completed.on",
    ]


# --- anyone else's cancel stops the caller ---------------------------------------


async def test_a_cancel_of_the_caller_itself_goes_on_up():
    engine, events, _state = _engine([LONG])
    ends = _ends(events)
    seen: dict = {}

    async def caller():
        seen["outcome"] = await engine.execute("long")
        seen["next_line"] = True

    task = asyncio.create_task(caller())
    await _until(lambda: engine.is_macro_running("long"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen == {}
    # The run still says how it ended, and lets go of its slot.
    assert ends == ["macro.cancelled.long"]
    assert not engine.is_macro_running("long")


async def test_both_cancels_at_once_still_stop_the_caller():
    engine, _events, _state = _engine([LONG])
    seen: dict = {}

    async def caller():
        seen["outcome"] = await engine.execute("long")

    task = asyncio.create_task(caller())
    await _until(lambda: engine.is_macro_running("long"))
    task.cancel()
    await engine.cancel("long")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == {}


# --- a cancel before the steps begin ------------------------------------------------


async def test_a_cancel_while_the_start_is_announced_ends_the_run_and_frees_its_slot():
    engine, events, state = _engine([
        {"id": "m", "name": "M", "overlap": "skip",
         "steps": [{"action": "state.set", "key": "var.ran", "value": True}]},
    ])
    ends = _ends(events)
    announced = asyncio.Event()

    async def slow_listener(_event, _payload):
        announced.set()
        await asyncio.sleep(0.5)

    events.on("macro.started.m", slow_listener)
    task = asyncio.create_task(engine.execute("m"))
    await announced.wait()
    assert await engine.cancel("m") is True

    assert await task == "cancelled"
    assert state.get("var.ran") is None
    assert ends == ["macro.cancelled.m"]
    # Before, the cancel escaped before the run's slot was given back, and
    # overlap: skip turned every later start away for good.
    assert not engine.is_macro_running("m")


async def test_a_cancel_while_waiting_for_a_preempted_run_is_not_lost():
    engine, events, state = _engine([
        {"id": "stubborn", "name": "Stubborn", "cancel_group": "power",
         "steps": [{"action": "delay", "seconds": 5}]},
        {"id": "next", "name": "Next", "cancel_group": "power",
         "steps": [{"action": "state.set", "key": "var.next_ran", "value": True}]},
    ])
    ends = _ends(events)
    unwinding = asyncio.Event()

    # A preempted run that takes its time to unwind: its cancelled event's
    # listener holds it, so the next run waits in its drain.
    async def hold(_event, _payload):
        unwinding.set()
        await asyncio.sleep(0.5)

    events.on("macro.cancelled.stubborn", hold)
    first = asyncio.create_task(engine.execute("stubborn"))
    await _until(lambda: engine.is_macro_running("stubborn"))
    second = asyncio.create_task(engine.execute("next"))
    await unwinding.wait()
    await _until(lambda: engine.is_macro_running("next"))
    assert await engine.cancel("next") is True

    assert await second == "cancelled"
    # Before, the wait swallowed the cancel and the run went ahead.
    assert state.get("var.next_ran") is None
    assert "macro.cancelled.next" in ends
    assert await first == "cancelled"


# --- the script handler, end to end -------------------------------------------------


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.setattr(ScriptEngine, "HANDLER_TIMEOUT", 0.2)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    macros = MacroEngine(state, events, devices)
    macros.load_macros([
        LONG,
        {"id": "outer", "name": "Outer", "steps": [
            {"action": "event.emit", "event": "custom.go"},
        ]},
    ])
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
    (project_dir / "scripts" / "waits.py").write_text(textwrap.dedent(code), encoding="utf-8")
    scripts.load_scripts([{"id": "waits", "file": "waits.py", "enabled": True}])


async def test_a_handler_cut_off_at_its_limit_stops_at_the_macro_it_waited_on(room):
    state, events, macros, scripts, project_dir, errors = room
    _load(scripts, project_dir, """\
        from openavc import on_event, macros, state

        @on_event("custom.wait")
        async def wait(event):
            await macros.execute("long")
            state.set("var.after_the_limit", True)
    """)
    await events.emit("custom.wait", {})
    await _until(lambda: errors)

    assert state.get("var.after_the_limit") is None
    assert [e.get("error", "") for e in errors] == [
        "Script 'waits' handler 'wait' timed out after 0.2s for event 'custom.wait'"
    ]
    assert not macros.is_macro_running("long")


async def test_a_handler_of_a_cancelled_macro_stops_at_the_macro_it_waited_on(room):
    state, events, macros, scripts, project_dir, errors = room
    _load(scripts, project_dir, """\
        from openavc import on_event, macros, state

        @on_event("custom.go")
        async def go(event):
            await macros.execute("long")
            state.set("var.after_the_cancel", True)
    """)
    outer = asyncio.create_task(macros.execute("outer"))
    await _until(lambda: macros.is_macro_running("long"))
    assert await macros.cancel("outer") is True

    assert await outer == "cancelled"
    assert state.get("var.after_the_cancel") is None
    assert not macros.is_macro_running("long")
