"""What a loaded script started ends with it.

A script's handlers, timers, worker thread and runs belong to one load of it.
Saving an edit, disabling the script or removing it ends that load. A handler
still running at that moment finishes, but nothing it asks for afterwards is
started: a timer it starts is not armed, and a handler queued by a change just
before is not run. So an old version never keeps ticking beside the new one,
and its late failure never lands on the new version's row.

``cancel_all_timers()`` is the calling script's: it stops that script's timers
and leaves every other script's running.
"""

import asyncio
import textwrap
import threading

import pytest

from openavc.core import script_api
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import StateStore


@pytest.fixture
def subsystems():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    return state, events, devices


@pytest.fixture
def script_dir(tmp_path):
    (tmp_path / "scripts").mkdir()
    return tmp_path


@pytest.fixture
def engine(subsystems, script_dir):
    state, events, devices = subsystems
    se = ScriptEngine(state, events, devices, script_dir)
    se.install()
    yield se
    se.unload_all()


def _write(script_dir, script_id: str, code: str) -> dict:
    path = script_dir / "scripts" / f"{script_id}.py"
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return {"id": script_id, "file": f"{script_id}.py", "enabled": True}


# The version an edit saves in place of the one running: it handles nothing.
_NEXT_VERSION = """\
    from openavc import state
"""


def _end_the_load(engine, script_dir, cfg: dict, how: str) -> None:
    """Unload the script, or save a new version over it."""
    if how == "unload":
        engine.unload_script(cfg["id"])
    else:
        _write(script_dir, cfg["id"], _NEXT_VERSION)
        assert engine.reload_script(cfg)["status"] == "reloaded"


def _ticker(name: str) -> str:
    return (
        f"def tick_{name}():\n"
        f'    state.set("var.ticks_{name}", (state.get("var.ticks_{name}") or 0) + 1)\n'
    )


_ARMS_LATE = {
    "async": """\
        from openavc import on_event, every, delay, state

        {ticker}
        @on_event("custom.arm")
        async def arm(event):
            await delay(0.3)
            every(0.01, tick_old)
            state.set("var.armed", True)
    """,
    "plain": """\
        import time
        from openavc import on_event, every, state

        {ticker}
        @on_event("custom.arm")
        def arm(event):
            time.sleep(0.3)
            every(0.01, tick_old)
            state.set("var.armed", True)
    """,
}


@pytest.mark.parametrize("how", ["unload", "reload"])
@pytest.mark.parametrize("kind", ["async", "plain"])
async def test_a_timer_asked_for_after_the_load_ended_is_not_started(
    engine, subsystems, script_dir, kind, how
):
    state, events, _ = subsystems
    source = textwrap.dedent(_ARMS_LATE[kind]).format(ticker=_ticker("old"))
    cfg = _write(script_dir, "late", source)
    engine.load_scripts([cfg])
    assert engine.get_load_errors() == {}

    emit = asyncio.create_task(events.emit("custom.arm", {}))
    await asyncio.sleep(0.01)  # the handler is in its wait, well short of its end
    _end_the_load(engine, script_dir, cfg, how)
    await emit
    await asyncio.sleep(0.15)

    assert state.get("var.armed") is True, "the run going at the unload did not finish"
    assert state.get("var.ticks_old") is None, "the old version's timer is ticking"
    assert script_api._active_timers == {}
    assert script_api._pending_timers == {}


_QUEUED = {
    "async": """\
        from openavc import on_state_change, state

        @on_state_change("var.input")
        async def seen(key, old, new):
            state.set("var.seen", new)
    """,
    "plain": """\
        from openavc import on_state_change, state

        @on_state_change("var.input")
        def seen(key, old, new):
            state.set("var.seen", new)
    """,
}


@pytest.mark.parametrize("how", ["unload", "reload"])
@pytest.mark.parametrize("kind", ["async", "plain"])
async def test_a_handler_queued_before_the_load_ended_is_not_run(
    engine, subsystems, script_dir, kind, how
):
    """A change queues the handler's run; the unload comes before it begins."""
    state, _, _ = subsystems
    cfg = _write(script_dir, "queued", _QUEUED[kind])
    engine.load_scripts([cfg])
    assert engine.get_load_errors() == {}

    state.set("var.input", 1)  # the handler's run is queued, not started
    _end_the_load(engine, script_dir, cfg, how)
    await asyncio.sleep(0.1)

    assert state.get("var.seen") is None, "the old version's handler ran after its unload"
    # Nothing of the old version's is left to run its plain code.
    assert not [t for t in threading.enumerate() if t.name == "script-queued"]


async def test_a_failure_of_the_old_version_after_a_reload_is_not_put_on_the_new_one(
    engine, subsystems, script_dir
):
    _, events, _ = subsystems
    cfg = _write(script_dir, "fixed", """\
        from openavc import on_event, delay

        @on_event("custom.arm")
        async def arm(event):
            await delay(0.3)
            raise RuntimeError("the old version failed")
    """)
    engine.load_scripts([cfg])
    assert engine.get_load_errors() == {}

    emit = asyncio.create_task(events.emit("custom.arm", {}))
    await asyncio.sleep(0.01)
    _end_the_load(engine, script_dir, cfg, "reload")
    await emit

    assert engine.get_runtime_errors() == {}


_STOPS_ITS_TIMERS = {
    "async": """\
        from openavc import on_event, every, cancel_all_timers, state

        {ticker}
        every(0.01, tick_a)

        @on_event("custom.stop")
        async def stop(event):
            state.set("var.cancelled", cancel_all_timers())
    """,
    "plain": """\
        from openavc import on_event, every, cancel_all_timers, state

        {ticker}
        every(0.01, tick_a)

        @on_event("custom.stop")
        def stop(event):
            state.set("var.cancelled", cancel_all_timers())
    """,
}

_PEER = """\
    from openavc import every, state

    {ticker}
    every(0.01, tick_b)
"""


@pytest.mark.parametrize("kind", ["async", "plain"])
async def test_cancel_all_timers_stops_only_the_calling_scripts_timers(
    engine, subsystems, script_dir, kind
):
    state, events, _ = subsystems
    cfg_a = _write(
        script_dir, "a", textwrap.dedent(_STOPS_ITS_TIMERS[kind]).format(ticker=_ticker("a"))
    )
    cfg_b = _write(script_dir, "b", textwrap.dedent(_PEER).format(ticker=_ticker("b")))
    engine.load_scripts([cfg_a, cfg_b])
    assert engine.get_load_errors() == {}
    await asyncio.sleep(0.05)

    await events.emit("custom.stop", {})
    await asyncio.sleep(0.03)  # a tick already on its way may still land
    a_after, b_after = state.get("var.ticks_a"), state.get("var.ticks_b")
    await asyncio.sleep(0.1)

    assert state.get("var.cancelled") == 1
    assert state.get("var.ticks_a") == a_after, "the calling script's timer kept ticking"
    assert state.get("var.ticks_b") > b_after, "another script's timer was stopped"


@pytest.mark.parametrize("saved", ["loads", "fails"])
async def test_cancel_all_timers_at_the_top_of_a_new_version_leaves_what_is_running(
    engine, subsystems, script_dir, saved
):
    """Top-level code runs while the old version is still running, and the
    new version's import can still fail. Its cancel_all_timers() is its own:
    the running version's timer stops only by the swap, and a peer's never."""
    state, _, _ = subsystems
    cfg = _write(script_dir, "a", (
        "from openavc import every, state\n\n" + _ticker("v1") + "every(0.01, tick_v1)\n"
    ))
    cfg_b = _write(script_dir, "b", textwrap.dedent(_PEER).format(ticker=_ticker("b")))
    engine.load_scripts([cfg, cfg_b])
    assert engine.get_load_errors() == {}
    await asyncio.sleep(0.05)

    tail = 'raise RuntimeError("broken")\n' if saved == "fails" else ""
    _write(script_dir, "a", (
        "from openavc import every, cancel_all_timers, state\n\n"
        + _ticker("v2")
        + 'state.set("var.cancelled_at_load", cancel_all_timers())\n'
        + "every(0.01, tick_v2)\n"
        + tail
    ))
    result = engine.reload_script(cfg)
    assert result["status"] == ("reloaded" if saved == "loads" else "error")
    await asyncio.sleep(0.03)
    v1_after, b_after = state.get("var.ticks_v1"), state.get("var.ticks_b")
    await asyncio.sleep(0.1)

    assert state.get("var.cancelled_at_load") == 0
    assert state.get("var.ticks_b") > b_after, "another script's timer was stopped"
    if saved == "fails":
        assert state.get("var.ticks_v1") > v1_after, "the running version's timer was stopped"
        assert state.get("var.ticks_v2") is None
    else:
        assert state.get("var.ticks_v1") == v1_after
        assert state.get("var.ticks_v2"), "the new version's own timer did not start"
