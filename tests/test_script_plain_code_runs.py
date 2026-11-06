"""A script's plain ``def`` code runs off the event loop, and still works.

A blocking line in a plain handler, timer callback or control-called function
used to stop every device command and panel until it returned. These pin the
two halves of moving it onto a worker thread: the loop stays free, and every
call the code makes still reaches the system the way it did inline (its writes
fire listeners and events on the loop, in order, visible to its next line).
"""

import asyncio
import sys
import textwrap
import threading
import time
import types

import pytest

from openavc.core import script_api, script_runs
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.script_engine import COULD_NOT_FINISH, ScriptCallError, ScriptEngine
from openavc.core.state_store import StateStore

LOOP_THREAD = threading.main_thread


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
def gate():
    """A switch a script can wait on, released at teardown so no thread is left."""
    module = types.ModuleType("_plain_runs_gate")
    module.release = threading.Event()
    sys.modules[module.__name__] = module
    yield module
    module.release.set()
    sys.modules.pop(module.__name__, None)


@pytest.fixture
def engine(subsystems, script_dir, gate):
    state, events, devices = subsystems
    se = ScriptEngine(state, events, devices, script_dir)
    se.install()
    yield se
    se.unload_all()


def _load(engine, script_dir, script_id, code):
    path = script_dir / "scripts" / f"{script_id}.py"
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    cfg = {"id": script_id, "file": f"{script_id}.py", "enabled": True}
    engine.load_scripts([cfg])
    return cfg


async def _ticks_until(task, period=0.01):
    """How many times the loop got round while ``task`` was running."""
    ticks = 0
    while not task.done():
        await asyncio.sleep(period)
        ticks += 1
    await task
    return ticks


async def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


# --- the loop stays free -----------------------------------------------------


async def test_a_blocking_plain_event_handler_leaves_the_loop_running(
    engine, subsystems, script_dir
):
    _, events, _ = subsystems
    _load(engine, script_dir, "blocker", """\
        import time
        from openavc import on_event

        @on_event("custom.block")
        def block(event):
            time.sleep(0.4)
    """)
    emit = asyncio.ensure_future(events.emit("custom.block", {}))
    assert await _ticks_until(emit) >= 10


async def test_a_blocking_plain_state_handler_leaves_the_loop_running(
    engine, subsystems, script_dir
):
    state, _, _ = subsystems
    _load(engine, script_dir, "blocker", """\
        import time
        from openavc import on_state_change, state

        @on_state_change("var.go")
        def block(key, old, new):
            time.sleep(0.4)
            state.set("var.done", new)
    """)
    state.set("var.go", 1)
    waiter = asyncio.ensure_future(_wait_for(lambda: state.get("var.done") == 1))
    assert await _ticks_until(waiter) >= 10


async def test_a_blocking_plain_timer_callback_leaves_the_loop_running(engine):
    done = threading.Event()

    def slow():
        time.sleep(0.4)
        done.set()

    script_api.after(0, slow)
    waiter = asyncio.ensure_future(_wait_for(done.is_set))
    assert await _ticks_until(waiter) >= 10


async def test_a_blocking_function_a_control_calls_leaves_the_loop_running(
    engine, subsystems, script_dir
):
    state, _, _ = subsystems
    _load(engine, script_dir, "funcs", """\
        import time
        from openavc import state

        def warm_up(seconds=0.4):
            time.sleep(seconds)
            state.set("var.warm", True)
    """)
    call = asyncio.ensure_future(engine.call_function("warm_up"))
    assert await _ticks_until(call) >= 10
    assert state.get("var.warm") is True


# --- and everything the code calls still reaches the system ------------------


async def test_a_plain_handlers_write_reaches_listeners_and_events_on_the_loop(
    engine, subsystems, script_dir
):
    state, events, _ = subsystems
    listener_threads, seen_events = [], []
    state.subscribe("var.out", lambda *a: listener_threads.append(threading.current_thread()))
    events.on("state.changed.var.out", lambda e, p: seen_events.append(p["new_value"]))

    _load(engine, script_dir, "writer", """\
        from openavc import on_event, state

        @on_event("custom.write")
        def write(event):
            state.set("var.out", "written")
    """)
    await events.emit("custom.write", {})
    await subsystems[0].flush_pending_events()

    assert state.get("var.out") == "written"
    assert listener_threads == [LOOP_THREAD()]
    assert seen_events == ["written"]


async def test_a_plain_handlers_error_still_names_its_own_line(
    engine, subsystems, script_dir
):
    """The Scripts editor marks the last frame in the script's own file, and the
    error now crosses a thread to be recorded, so that frame must survive it."""
    _, events, _ = subsystems
    _load(engine, script_dir, "thrower", """\
        from openavc import on_event

        @on_event("custom.throw")
        def throw(event):
            raise ValueError("thrown on the worker")
    """)
    await events.emit("custom.throw", {})
    entry = engine.get_runtime_errors()["thrower"]
    assert entry["error"] == "thrown on the worker"
    assert 'thrower.py", line 5, in throw' in entry["traceback"]
    assert entry["traceback"].rstrip().endswith("ValueError: thrown on the worker")


async def test_a_plain_handler_reads_its_own_write_on_the_next_line(
    engine, subsystems, script_dir
):
    state, events, _ = subsystems
    _load(engine, script_dir, "rw", """\
        from openavc import on_event, state

        @on_event("custom.rw")
        def rw(event):
            state.set("var.a", 41)
            state.set("var.b", state.get("var.a") + 1)
    """)
    await events.emit("custom.rw", {})
    assert state.get("var.b") == 42


async def test_a_plain_state_handler_sees_changes_in_the_order_they_happened(
    engine, subsystems, script_dir
):
    state, _, _ = subsystems
    _load(engine, script_dir, "order", """\
        import time
        from openavc import on_state_change, state

        seen = []

        @on_state_change("var.level")
        def record(key, old, new):
            time.sleep(0.005)
            seen.append(new)
            state.set("var.seen", ",".join(str(v) for v in seen))
    """)
    for level in range(1, 21):
        state.set("var.level", level)
    expected = ",".join(str(v) for v in range(1, 21))
    await _wait_for(lambda: state.get("var.seen") == expected)


async def test_a_plain_handler_can_start_timers_owned_by_its_script(
    engine, subsystems, script_dir
):
    state, events, _ = subsystems
    cfg = _load(engine, script_dir, "timers", """\
        from openavc import on_event, after, state

        def later():
            state.set("var.later", True)

        @on_event("custom.arm")
        def arm(event):
            after(0.05, later)
            after(30, later)
    """)
    await events.emit("custom.arm", {})
    await _wait_for(lambda: state.get("var.later") is True)
    # The 30 s one is the script's, so a reload of that script cancels it.
    assert script_api.cancel_script_timers(cfg["id"]) == 1


async def test_a_timer_callback_that_cancels_its_own_timer_still_finishes(engine):
    state = engine.state
    timer = {}

    def tick():
        script_api.cancel_timer(timer["id"])
        state_proxy = script_api.state
        state_proxy.set("var.after_cancel", "written")

    timer["id"] = script_api.every(0.02, tick)
    await _wait_for(lambda: state.get("var.after_cancel") == "written")


async def test_a_thread_the_script_starts_still_reaches_the_loop(
    engine, subsystems, script_dir
):
    state, events, _ = subsystems
    seen = []
    events.on("state.changed.var.from_thread", lambda e, p: seen.append(p["new_value"]))
    _load(engine, script_dir, "threads", """\
        import asyncio
        from openavc import on_event, state

        def fetch():
            state.set("var.from_thread", "fetched")

        @on_event("custom.fetch")
        async def go(event):
            await asyncio.to_thread(fetch)
    """)
    await events.emit("custom.fetch", {})
    await state.flush_pending_events()
    assert seen == ["fetched"]


async def test_a_scripts_top_level_writes_do_not_wait_on_the_loop(
    engine, subsystems, script_dir
):
    """The loop is blocked on a load while it runs, so a load must never hand
    a call to it: a hand-off there waits out the whole load timeout."""
    state, _, _ = subsystems
    engine.SCRIPT_LOAD_TIMEOUT = 2
    started = time.monotonic()
    _load(engine, script_dir, "top", """\
        from openavc import state, log
        state.set("var.loaded", True)
        log.info("loaded")
    """)
    assert time.monotonic() - started < 1
    assert engine.get_load_errors() == {}
    assert state.get("var.loaded") is True


async def test_a_plain_method_of_a_plugin_runs_on_the_loop(engine, subsystems, script_dir):
    state, events, _ = subsystems
    calls = []
    script_api.plugins._register_method(
        "acme_widget", "ping", lambda: calls.append(threading.current_thread())
    )
    try:
        _load(engine, script_dir, "plug", """\
            from openavc import on_event, plugins

            @on_event("custom.ping")
            def ping(event):
                plugins.acme_widget.ping()
        """)
        await events.emit("custom.ping", {})
    finally:
        script_api.plugins._unregister_plugin("acme_widget")
    assert calls == [LOOP_THREAD()]


async def test_awaiting_the_api_on_a_second_loop_is_refused_with_what_to_write(
    engine, subsystems, script_dir
):
    _, events, _ = subsystems
    _load(engine, script_dir, "misuse", """\
        import asyncio
        from openavc import on_event, devices

        @on_event("custom.misuse")
        def misuse(event):
            asyncio.run(devices.send("acme_widget", "power_on"))
    """)
    await events.emit("custom.misuse", {})
    error = engine.get_runtime_errors()["misuse"]["error"]
    assert "devices.send() has to be awaited inside an async def" in error
    assert "after(0, devices.send, ...)" in error


async def test_a_plain_state_handler_feedback_loop_is_capped(
    engine, subsystems, script_dir
):
    state, _, _ = subsystems
    _load(engine, script_dir, "loop", """\
        from openavc import on_state_change, state

        @on_state_change("var.counter")
        def bump(key, old, new):
            state.set("var.counter", (new or 0) + 1)
    """)
    state.set("var.counter", 0)
    await _wait_for(lambda: state.get("var.counter") == engine.MAX_STATE_HANDLER_DEPTH)
    await asyncio.sleep(0.1)
    assert state.get("var.counter") == engine.MAX_STATE_HANDLER_DEPTH


# --- the limit ---------------------------------------------------------------


async def test_a_plain_handler_past_the_limit_is_reported_and_its_later_calls_refused(
    engine, subsystems, script_dir, gate
):
    state, events, _ = subsystems
    engine.HANDLER_TIMEOUT = 0.1
    _load(engine, script_dir, "stuck", """\
        import _plain_runs_gate as gate
        from openavc import on_event, state

        @on_event("custom.stuck")
        def stuck(event):
            gate.release.wait(5)
            state.set("var.too_late", True)

        @on_event("custom.next")
        def next_one(event):
            state.set("var.next_ran", True)
    """)
    await events.emit("custom.stuck", {})
    entry = engine.get_runtime_errors()["stuck"]
    assert "timed out after 0.1s" in entry["error"]
    stuck_threads = [t for t in threading.enumerate() if t.name == "script-stuck"]
    assert len(stuck_threads) == 1

    # The script's next run is not queued behind the stuck one.
    await events.emit("custom.next", {})
    assert state.get("var.next_ran") is True

    # When the stuck run wakes, its write is refused and its thread ends.
    gate.release.set()
    for thread in stuck_threads:
        thread.join(2)
    assert not any(t.is_alive() for t in stuck_threads)
    assert state.get("var.too_late") is None


async def test_a_function_a_control_calls_past_the_limit_could_not_finish(
    engine, subsystems, script_dir
):
    engine.HANDLER_TIMEOUT = 0.1
    _load(engine, script_dir, "slowfn", """\
        import _plain_runs_gate as gate

        def slow():
            gate.release.wait(5)
    """)
    with pytest.raises(ScriptCallError) as raised:
        await engine.call_function("slow")
    assert str(raised.value) == COULD_NOT_FINISH
    assert "timed out" in engine.get_runtime_errors()["slowfn"]["error"]


async def test_too_many_stuck_runs_refuse_the_script_until_it_is_reloaded(
    engine, subsystems, script_dir
):
    state, events, _ = subsystems
    engine.HANDLER_TIMEOUT = 0.05
    cfg = _load(engine, script_dir, "spinner", """\
        import _plain_runs_gate as gate
        from openavc import on_event, state

        @on_event("custom.spin")
        def spin(event):
            gate.release.wait(5)

        @on_event("custom.mark")
        def mark(event):
            state.set("var.marked", event.get("n"))
    """)
    for _ in range(script_runs.MAX_STUCK_RUNS):
        await events.emit("custom.spin", {})
    await events.emit("custom.mark", {"n": 1})
    entry = engine.get_runtime_errors()["spinner"]
    assert entry["error"].startswith(f"Not run: {script_runs.MAX_STUCK_RUNS} earlier runs")
    assert state.get("var.marked") is None

    assert engine.reload_script(cfg)["status"] == "reloaded"
    await events.emit("custom.mark", {"n": 2})
    assert state.get("var.marked") == 2


async def test_a_run_going_when_its_script_reloads_finishes(
    engine, subsystems, script_dir, gate
):
    """An async handler's task runs on through a reload, and so does a plain one."""
    state, events, _ = subsystems
    cfg = _load(engine, script_dir, "reloading", """\
        import _plain_runs_gate as gate
        from openavc import on_event, state

        @on_event("custom.long")
        def long(event):
            gate.release.wait(5)
            state.set("var.finished", True)
    """)
    emit = asyncio.ensure_future(events.emit("custom.long", {}))
    await asyncio.sleep(0.05)
    assert engine.reload_script(cfg)["status"] == "reloaded"
    gate.release.set()
    await emit
    assert state.get("var.finished") is True


async def test_a_plain_handler_sends_a_device_command_through_after(
    engine, subsystems, script_dir, monkeypatch
):
    """The route the scripting guide gives a plain def for a device command."""
    _, events, _ = subsystems
    sent = []

    class _Manager:
        async def send_command(self, device_id, command, params=None):
            sent.append((device_id, command, threading.current_thread()))

    monkeypatch.setattr(script_api.devices, "_manager", _Manager())
    _load(engine, script_dir, "sender", """\
        from openavc import on_event, after, devices

        @on_event("custom.power")
        def power(event):
            after(0, devices.send, "acme_widget", "power_on")
    """)
    await events.emit("custom.power", {})
    await _wait_for(lambda: sent)
    assert sent == [("acme_widget", "power_on", LOOP_THREAD())]
