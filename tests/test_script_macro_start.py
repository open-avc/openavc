"""macros.start: a script starts a macro without waiting for it.

``await macros.execute(id)`` runs the steps inside the handler or timer that
awaited it, so they count against ``ScriptEngine.HANDLER_TIMEOUT`` and a long
macro is cancelled partway through. ``macros.start(id)`` hands the run to the
macro engine and returns at once. The limit is patched down to 50 ms here, with
a macro that takes 300 ms, so the real 30 s case runs in under a second.
"""

import asyncio
import textwrap

import pytest

from openavc.core import script_api
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import StateStore

LIMIT = 0.05
LONG_MACRO = {
    "id": "long_off",
    "name": "Long Off",
    "steps": [
        {"action": "state.set", "key": "var.first_step", "value": True},
        {"action": "delay", "seconds": 0.3},
        {"action": "state.set", "key": "var.last_step", "value": True},
    ],
}


@pytest.fixture
def room(tmp_path, monkeypatch):
    """A script engine wired to a real macro engine, with the limit turned down."""
    monkeypatch.setattr(ScriptEngine, "HANDLER_TIMEOUT", LIMIT)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    macros = MacroEngine(state, events, devices)
    macros.load_macros([LONG_MACRO])
    (tmp_path / "scripts").mkdir()
    scripts = ScriptEngine(state, events, devices, tmp_path, macro_engine=macros)
    scripts.install()

    errors: list[dict] = []

    async def on_error(event, payload):
        errors.append(payload)

    events.on("script.error", on_error)
    yield state, macros, scripts, tmp_path, errors
    scripts.unload_all()


def _load(scripts: ScriptEngine, project_dir, code: str) -> None:
    (project_dir / "scripts" / "auto_off.py").write_text(
        textwrap.dedent(code), encoding="utf-8"
    )
    scripts.load_scripts([{"id": "auto_off", "file": "auto_off.py", "enabled": True}])
    # A timer-only script registers no handlers, so its timer is the proof it loaded.
    assert "auto_off" in script_api._timer_owners.values()


async def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def test_a_macro_started_from_every_runs_past_the_handler_limit(room):
    """The fix: the timer returns at once and the macro reaches its last step."""
    state, macros, scripts, project_dir, errors = room
    _load(scripts, project_dir, """\
        from openavc import every, macros

        fired = {"done": False}

        def check():
            if fired["done"]:
                return
            fired["done"] = True
            macros.start("long_off")

        every(0.02, check)
    """)

    assert await _wait_for(lambda: state.get("var.first_step") is True)
    # Six times the limit later the run is still going, and the timer was not
    # stopped: the callback returned the moment the macro started.
    assert await _wait_for(lambda: state.get("var.last_step") is True)
    assert errors == []
    assert not macros.is_macro_running("long_off")
    assert macros._detached == set()


async def test_execute_from_every_is_still_cut_off_at_the_handler_limit(room):
    """A script timer that awaits a long macro: the limit cancels the run
    partway, and the timer reports it. The macro swallows the cancellation (it
    ends as "cancelled"), so before the limit checked for that the cut-off left
    no error at all, only the macro's own "was cancelled" log line."""
    state, macros, scripts, project_dir, errors = room
    _load(scripts, project_dir, """\
        from openavc import every, macros

        fired = {"done": False}

        async def check():
            if fired["done"]:
                return
            fired["done"] = True
            await macros.execute("long_off")

        every(0.02, check)
    """)

    assert await _wait_for(lambda: errors)
    await asyncio.sleep(0.4)  # past the macro's own length
    assert state.get("var.first_step") is True
    assert state.get("var.last_step") is None
    assert len(errors) == 1
    assert "timed out" in errors[0]["error"]


async def test_execute_waits_for_the_last_step_and_start_does_not(room):
    state, macros, _, _, _ = room
    proxy = script_api._MacroProxy()
    proxy._bind(macros)
    macros.load_macros([{
        "id": "short",
        "name": "Short",
        "steps": [
            {"action": "delay", "seconds": 0.05},
            {"action": "state.set", "key": "var.short_done", "value": True},
        ],
    }])

    await proxy.execute("short")
    assert state.get("var.short_done") is True

    state.set("var.short_done", False)
    assert proxy.start("short") is None
    assert state.get("var.short_done") is False
    assert len(macros._detached) == 1
    assert await _wait_for(lambda: state.get("var.short_done") is True)


async def test_start_refuses_a_missing_macro_at_the_call(room):
    _, macros, _, _, _ = room
    proxy = script_api._MacroProxy()
    proxy._bind(macros)

    with pytest.raises(ValueError, match="not found"):
        proxy.start("nope")
    assert macros._detached == set()


async def test_start_from_a_scripts_top_level_says_how(room):
    """Top-level code runs off the event loop; the refusal names the way."""
    _, macros, _, _, _ = room
    proxy = script_api._MacroProxy()
    proxy._bind(macros)

    with pytest.raises(RuntimeError, match=r"after\(0, macros\.start, 'long_off'\)"):
        await asyncio.to_thread(proxy.start, "long_off")
    assert macros._detached == set()


async def test_start_keeps_the_call_chain_across_the_script_boundary():
    """A macro that drives a handler which starts the same macro again is
    refused at the call, as execute is, rather than looping in the background."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    macros = MacroEngine(state, events, DeviceManager(state, events))
    macros.load_macros([{
        "id": "loop_macro",
        "name": "Loop",
        "steps": [{"action": "event.emit", "event": "loop.go"}],
    }])
    proxy = script_api._MacroProxy()
    proxy._bind(macros)

    runs: list[int] = []
    refusals: list[str] = []

    async def handler(event, payload):
        runs.append(1)
        if len(runs) > 15:
            return  # safety brake should the guard ever stop holding
        try:
            proxy.start("loop_macro")
        except ValueError as e:
            refusals.append(str(e))

    events.on("loop.go", handler)
    await macros.execute("loop_macro")
    await asyncio.sleep(0.05)

    assert len(runs) == 1
    assert refusals and "circular" in refusals[0]
    assert macros._detached == set()


async def test_a_body_that_swallows_the_limits_cancel_is_still_timed_out():
    """The limit, not the body, decides whether the body was stopped."""
    from openavc.core.script_engine import HandlerTimedOut, run_within_handler_limit

    async def swallows():
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    with pytest.raises(HandlerTimedOut):
        await run_within_handler_limit(swallows(), LIMIT)
