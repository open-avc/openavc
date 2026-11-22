"""Every door that runs a loaded script's code records its failures in one place.

The Scripts list's row, ``GET /api/scripts/errors`` and the AI's
``get_script_health`` all read ``ScriptEngine.get_runtime_errors()``. A failure
that is logged and emitted but not recorded there shows nowhere an integrator
looks: a dead button, a timer that never did its job, a chain of events that
stopped halfway, and a Scripts list with nothing wrong on it.

The doors covered here are the ones that reached the log and nothing else: a
timer's callback, a control's call that was refused before the function ran,
an event a script sent past the bus's chain limit, and an ``async def`` state
handler that had no loop to run on. Each one also logs an ERROR line in the
script category, the line the IDE refreshes its Scripts list on.

Every script, event and variable here is invented.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
import threading

import pytest

from openavc.core import script_api
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.script_engine import ScriptCallError, ScriptEngine
from openavc.core.state_store import StateStore
from openavc.utils.log_buffer import USER_SCRIPT_NAMESPACE


@pytest.fixture
def subsystems():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    return state, events, devices


@pytest.fixture
def engine(subsystems, tmp_path):
    (tmp_path / "scripts").mkdir()
    state, events, devices = subsystems
    se = ScriptEngine(state, events, devices, tmp_path)
    se.install()
    yield se
    se.unload_all()


def _load(engine: ScriptEngine, script_id: str, code: str) -> None:
    path = engine.scripts_dir / f"{script_id}.py"
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    engine.load_scripts([{"id": script_id, "file": f"{script_id}.py", "enabled": True}])
    assert engine.get_load_errors() == {}, engine.get_load_errors()


def _load_many(engine: ScriptEngine, scripts: dict[str, str]) -> None:
    for script_id, code in scripts.items():
        path = engine.scripts_dir / f"{script_id}.py"
        path.write_text(textwrap.dedent(code), encoding="utf-8")
    engine.load_scripts([
        {"id": script_id, "file": f"{script_id}.py", "enabled": True}
        for script_id in scripts
    ])
    assert engine.get_load_errors() == {}, engine.get_load_errors()


def _script_errors_logged(caplog) -> list[logging.LogRecord]:
    """ERROR lines the log buffer files under "script", the IDE's refresh cue."""
    return [
        r for r in caplog.records
        if r.levelno >= logging.ERROR
        and (r.name.startswith("openavc.core.script") or r.name.startswith(USER_SCRIPT_NAMESPACE))
    ]


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


# --- a timer's callback -------------------------------------------------------


async def test_a_timer_callback_that_raises_is_recorded_against_its_script(
    engine, subsystems, caplog,
):
    """The documented pattern: a timer started at the top of the script. Its
    failure was emitted with no event and never recorded."""
    _, events, _ = subsystems
    emitted: list[dict] = []
    events.on("script.error", lambda _e, payload: emitted.append(payload))

    _load(engine, "poller", """\
        from openavc import every

        def check_room():
            raise KeyError("lectern")

        every(0.02, check_room)
    """)
    await _wait_for(lambda: "poller" in engine.get_runtime_errors())

    entry = engine.get_runtime_errors()["poller"]
    assert entry["handler"] == "check_room"
    assert entry["event"] == "every() timer"
    assert entry["error"] == "'lectern'"
    assert "KeyError" in entry["traceback"]
    assert "poller.py" in entry["traceback"]

    # The frame keeps the timer id it always carried, and now says what the
    # callback was running for, the way every other door's frame does. It is
    # emitted just after the record, a turn of the loop later.
    await _wait_for(lambda: bool(emitted))
    assert emitted[0]["script_id"] == "poller"
    assert emitted[0]["timer_id"].startswith("timer_")
    assert emitted[0]["event"] == "every() timer"
    assert _script_errors_logged(caplog)


async def test_an_every_that_keeps_failing_keeps_counting(engine):
    """One failing tick never stops the interval, and every tick is counted."""
    _load(engine, "ticker", """\
        from openavc import every

        def tick():
            raise ValueError("no reply")

        every(0.02, tick)
    """)
    await _wait_for(lambda: engine.get_runtime_errors().get("ticker", {}).get("count", 0) >= 3)


async def test_an_after_callback_that_times_out_is_recorded(engine, monkeypatch):
    monkeypatch.setattr(ScriptEngine, "HANDLER_TIMEOUT", 0.05)
    _load(engine, "slowpoke", """\
        import asyncio
        from openavc import after

        async def wait_forever():
            await asyncio.Event().wait()

        after(0.01, wait_forever)
    """)
    await _wait_for(lambda: "slowpoke" in engine.get_runtime_errors())

    entry = engine.get_runtime_errors()["slowpoke"]
    assert entry["handler"] == "wait_forever"
    assert entry["event"] == "after() timer"
    assert "timed out" in entry["error"]


async def test_a_timer_no_script_owns_leaves_no_row(engine):
    """A timer started outside any script's code has no script to show its
    failure on; it must not invent an empty-named one."""

    def orphan():
        raise RuntimeError("nobody's")

    script_api.after(0.01, orphan)
    await asyncio.sleep(0.1)

    assert engine.get_runtime_errors() == {}


# --- a control's call, refused before the function ran -----------------------


async def test_a_call_to_a_function_its_script_does_not_define_is_recorded(
    engine, subsystems, caplog,
):
    """The Builder records which script a control's function came from. When
    that script no longer defines it (renamed, deleted), the row for that
    script is where the integrator goes to look."""
    _, events, _ = subsystems
    emitted: list[dict] = []
    events.on("script.error", lambda _e, payload: emitted.append(payload))
    _load(engine, "room", """\
        def start_film():
            pass
    """)

    with pytest.raises(ScriptCallError, match="No script function named 'play_film'"):
        await engine.call_function("play_film", {}, "room")

    entry = engine.get_runtime_errors()["room"]
    assert entry["handler"] == "play_film"
    assert entry["event"] == "script.call.play_film"
    assert "No script function named 'play_film'" in entry["error"]
    assert entry["traceback"] == ""
    assert [p["script_id"] for p in emitted] == ["room"]
    assert _script_errors_logged(caplog)


async def test_a_call_with_parameters_the_function_does_not_take_is_recorded(engine):
    _load(engine, "room", """\
        def set_source(source):
            pass
    """)

    with pytest.raises(ScriptCallError):
        await engine.call_function("set_source", {"sauce": "hdmi1"})

    entry = engine.get_runtime_errors()["room"]
    assert entry["handler"] == "set_source"
    assert entry["event"] == "script.call.set_source"
    assert "It takes source; this control passed sauce" in entry["error"]


async def test_a_call_two_scripts_answer_is_recorded_on_both(engine):
    """Either script is where the fix can go (rename one), so both rows say so."""
    _load_many(engine, {
        "lobby": """\
            def all_off():
                pass
        """,
        "stage": """\
            def all_off():
                pass
        """,
    })

    with pytest.raises(ScriptCallError, match="More than one script defines 'all_off'"):
        await engine.call_function("all_off")

    errors = engine.get_runtime_errors()
    assert set(errors) == {"lobby", "stage"}
    assert "More than one script defines 'all_off'" in errors["lobby"]["error"]
    assert errors["stage"]["event"] == "script.call.all_off"


async def test_a_call_no_loaded_script_can_answer_leaves_no_row(engine):
    """A hand-written control naming no script, or a script that is not
    running, has no row to put the failure on. The control still says why."""
    _load(engine, "room", """\
        def start_film():
            pass
    """)

    with pytest.raises(ScriptCallError):
        await engine.call_function("play_film")
    with pytest.raises(ScriptCallError):
        await engine.call_function("play_film", {}, "not_loaded")

    assert engine.get_runtime_errors() == {}


async def test_a_panel_message_is_still_not_a_script_error(engine):
    """The author's own refusal in the room's words stays off the record."""
    _load(engine, "room", """\
        from openavc import PanelMessage

        def start_film():
            raise PanelMessage("Lamp needs replacing")
    """)

    with pytest.raises(ScriptCallError, match="Lamp needs replacing"):
        await engine.call_function("start_film")

    assert engine.get_runtime_errors() == {}


# --- an event sent past the bus's chain limit --------------------------------

_PING_PONG = """\
    from openavc import on_event, events, state

    @on_event("custom.ping")
    async def ping(event):
        await events.emit("custom.pong")
        state.set("var.pings", (state.get("var.pings") or 0) + 1)

    @on_event("custom.pong")
    async def pong(event):
        await events.emit("custom.ping")
"""


async def test_an_event_dropped_at_the_chain_limit_is_recorded_on_the_sender(
    engine, subsystems, caplog,
):
    """Two handlers that send each other's event. The bus stops the chain and
    the script carries on, which is right; what was wrong is that nothing but a
    warning in the system log said the event was never sent."""
    state, events, _ = subsystems
    _load(engine, "loop", _PING_PONG)

    await events.emit("custom.ping")

    entry = engine.get_runtime_errors()["loop"]
    assert entry["count"] == 1
    assert entry["handler"] == "pong"
    assert entry["event"] == "events.emit"
    assert "Event 'custom.ping' was not sent" in entry["error"]
    assert str(events.MAX_EMIT_DEPTH) in entry["error"]
    # The trace ends at the script's own line, so the editor can mark it, and
    # holds only the script's frames: it is shown to the author and the AI.
    assert entry["traceback"].rstrip().endswith('await events.emit("custom.ping")')
    assert 'loop.py", line 10, in pong' in entry["traceback"]
    files = [line for line in entry["traceback"].splitlines() if line.strip().startswith("File ")]
    assert files and all('loop.py"' in line for line in files), files
    # A dropped event is not an exception in the script: the line after the
    # emit still runs, as it always did.
    assert state.get("var.pings") == 2
    assert _script_errors_logged(caplog)


async def test_an_event_a_timer_sent_for_the_script_names_no_platform_function(engine, subsystems):
    """A plain def is told to send an event with ``after(0, events.emit, ...)``.
    Then no function of the script is on the stack when the bus drops it, and
    the record must not name whatever platform code happened to be there."""
    _, events, _ = subsystems
    _load(engine, "relay", """\
        from openavc import on_event, events, after

        @on_event("custom.tick")
        async def tick(event):
            after(0, events.emit, "custom.tock")

        @on_event("custom.tock")
        async def tock(event):
            after(0, events.emit, "custom.tick")
    """)

    await events.emit("custom.tick")
    await _wait_for(lambda: "relay" in engine.get_runtime_errors())

    entry = engine.get_runtime_errors()["relay"]
    assert entry["handler"] == "events.emit"
    assert entry["event"] == "events.emit"
    assert "was not sent" in entry["error"]
    # No line of the script sent it, so there is nothing to mark.
    assert entry["traceback"] == ""


async def test_the_bus_says_when_it_dropped_an_event(subsystems):
    """The script API asks the bus, which owns the limit, rather than keeping
    its own count of the chain."""
    _, events, _ = subsystems
    results: list[bool] = []

    async def again(_event, _payload):
        results.append(await events.emit("custom.again"))

    events.on("custom.again", again)
    assert await events.emit("custom.again") is True

    # The innermost emit, the one past the limit, finishes first.
    assert results[0] is False
    assert results.count(False) == 1


async def test_an_emit_outside_any_script_records_nothing(engine, subsystems):
    _, events, _ = subsystems

    async def again(_event, _payload):
        await events.emit("custom.again")

    events.on("custom.again", again)
    await events.emit("custom.again")

    assert engine.get_runtime_errors() == {}


# --- an async state handler with no loop to run on ---------------------------


async def test_an_async_state_handler_with_no_loop_is_recorded(engine, subsystems, caplog):
    """State set from a thread with no event loop runs a plain def handler
    right there. An async def cannot run there, and was closed with nothing
    said anywhere."""
    state, _, _ = subsystems
    _load(engine, "watcher", """\
        from openavc import on_state_change, state

        @on_state_change("var.occupied")
        async def on_occupied(key, old_value, new_value):
            state.set("var.saw", new_value)
    """)

    done = threading.Event()

    def writer():
        state.set("var.occupied", True)
        done.set()

    threading.Thread(target=writer, daemon=True).start()
    await asyncio.get_running_loop().run_in_executor(None, done.wait, 5)

    entry = engine.get_runtime_errors()["watcher"]
    assert entry["handler"] == "on_occupied"
    assert entry["event"] == "state_change:var.occupied"
    assert "did not run" in entry["error"]
    assert "plain def" in entry["error"]
    assert state.get("var.saw") is None
    assert _script_errors_logged(caplog)
