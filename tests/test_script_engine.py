"""Tests for ScriptEngine and script_api."""

import asyncio
import sys
import textwrap
import threading

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.core.device_manager import DeviceManager
from server.core import script_api
from server.core.script_engine import ScriptEngine


@pytest.fixture
def subsystems():
    """Wired StateStore + EventBus + DeviceManager."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    return state, events, devices


@pytest.fixture
def script_dir(tmp_path):
    """Temp directory with a scripts/ subdirectory."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    return tmp_path


@pytest.fixture
def engine(subsystems, script_dir):
    """ScriptEngine instance with script_api installed."""
    state, events, devices = subsystems
    se = ScriptEngine(state, events, devices, script_dir)
    se.install()
    yield se
    se.unload_all()
    # Clean up sys.modules injection
    sys.modules.pop("openavc", None)


def _write_script(script_dir, filename, code):
    """Helper to write a script file into the scripts/ subdirectory."""
    path = script_dir / "scripts" / filename
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return path


# --- Tests ---


async def test_load_state_change_handler(engine, subsystems, script_dir):
    """Script with @on_state_change gets called when state changes."""
    state, events, devices = subsystems

    _write_script(script_dir, "test1.py", """\
        from openavc import on_state_change, state

        @on_state_change("device.proj1.power")
        async def handle(key, old_value, new_value):
            state.set("var.result", f"got:{new_value}")
    """)

    count = engine.load_scripts([{"id": "test1", "file": "test1.py", "enabled": True}])
    assert count == 1

    state.set("device.proj1.power", "on", source="driver")
    # Allow async task to run
    await asyncio.sleep(0.05)

    assert state.get("var.result") == "got:on"


async def test_load_event_handler(engine, subsystems, script_dir):
    """Script with @on_event gets called when event is emitted."""
    state, events, devices = subsystems

    _write_script(script_dir, "test2.py", """\
        from openavc import on_event, state

        @on_event("custom.test")
        async def handle(event, payload):
            state.set("var.event_fired", payload.get("msg", ""))
    """)

    engine.load_scripts([{"id": "test2", "file": "test2.py", "enabled": True}])
    await events.emit("custom.test", {"msg": "hello"})
    assert state.get("var.event_fired") == "hello"


async def test_disabled_script_not_loaded(engine, script_dir):
    """Scripts with enabled=False are skipped."""
    _write_script(script_dir, "skip.py", """\
        from openavc import on_event
        @on_event("should.not.register")
        async def handle(event, payload):
            pass
    """)

    count = engine.load_scripts([{"id": "skip", "file": "skip.py", "enabled": False}])
    assert count == 0


async def test_missing_script_file(engine, script_dir):
    """Missing script file is logged, not raised."""
    count = engine.load_scripts([{"id": "missing", "file": "nope.py", "enabled": True}])
    assert count == 0


async def test_handler_error_is_caught(engine, subsystems, script_dir):
    """Handler exceptions are caught and logged, not propagated."""
    state, events, devices = subsystems

    _write_script(script_dir, "bad.py", """\
        from openavc import on_event

        @on_event("boom")
        async def handle(event, payload):
            raise RuntimeError("script error")
    """)

    engine.load_scripts([{"id": "bad", "file": "bad.py", "enabled": True}])
    # Should not raise
    await events.emit("boom")


async def test_state_handler_error_is_caught(engine, subsystems, script_dir):
    """State handler exceptions are caught and logged."""
    state, events, devices = subsystems

    _write_script(script_dir, "bad_state.py", """\
        from openavc import on_state_change

        @on_state_change("var.x")
        def handle(key, old_value, new_value):
            raise ValueError("bad state handler")
    """)

    engine.load_scripts([{"id": "bad_state", "file": "bad_state.py", "enabled": True}])
    # Should not raise
    state.set("var.x", "test")


async def test_unload_removes_handlers(engine, subsystems, script_dir):
    """unload_all removes all registered handlers."""
    state, events, devices = subsystems

    _write_script(script_dir, "unload.py", """\
        from openavc import on_event, on_state_change

        @on_event("test.unload")
        async def h1(event, payload):
            pass

        @on_state_change("var.unload")
        def h2(key, old, new):
            pass
    """)

    engine.load_scripts([{"id": "unload", "file": "unload.py", "enabled": True}])
    assert len(engine._event_handler_ids) == 1
    assert len(engine._state_sub_ids) == 1

    engine.unload_all()
    assert len(engine._event_handler_ids) == 0
    assert len(engine._state_sub_ids) == 0


async def test_reload_scripts(engine, subsystems, script_dir):
    """reload_scripts unloads old handlers and loads new ones."""
    state, events, devices = subsystems

    _write_script(script_dir, "reload.py", """\
        from openavc import on_state_change, state

        @on_state_change("var.input")
        async def handle(key, old_value, new_value):
            state.set("var.output", f"v1:{new_value}")
    """)

    scripts = [{"id": "reload", "file": "reload.py", "enabled": True}]
    engine.load_scripts(scripts)

    state.set("var.input", "a")
    await asyncio.sleep(0.05)
    assert state.get("var.output") == "v1:a"

    # Rewrite script with different logic
    _write_script(script_dir, "reload.py", """\
        from openavc import on_state_change, state

        @on_state_change("var.input")
        async def handle(key, old_value, new_value):
            state.set("var.output", f"v2:{new_value}")
    """)

    engine.reload_scripts(scripts)
    state.set("var.input", "b")
    await asyncio.sleep(0.05)
    assert state.get("var.output") == "v2:b"


async def test_multiple_handlers_in_one_script(engine, subsystems, script_dir):
    """A script can register multiple handlers."""
    state, events, devices = subsystems

    _write_script(script_dir, "multi.py", """\
        from openavc import on_event, on_state_change, state

        @on_event("custom.a")
        async def h1(event, payload):
            state.set("var.a", "yes")

        @on_state_change("var.trigger")
        async def h2(key, old, new):
            state.set("var.b", "yes")
    """)

    count = engine.load_scripts([{"id": "multi", "file": "multi.py", "enabled": True}])
    assert count == 2


async def test_device_proxy(engine, subsystems, script_dir):
    """devices proxy can list devices (empty in test)."""
    result = script_api.devices.list()
    assert result == []


async def test_state_proxy(engine, subsystems, script_dir):
    """state proxy delegates to real StateStore."""
    state, events, devices = subsystems

    script_api.state.set("var.proxy_test", "hello")
    assert state.get("var.proxy_test") == "hello"
    assert script_api.state.get("var.proxy_test") == "hello"


async def test_openavc_importable(engine):
    """After install(), 'import openavc' works."""
    import openavc
    assert hasattr(openavc, "on_event")
    assert hasattr(openavc, "on_state_change")
    assert hasattr(openavc, "devices")
    assert hasattr(openavc, "state")
    assert hasattr(openavc, "log")
    assert hasattr(openavc, "delay")


# ===== get_callable_functions =====


def test_get_callable_functions(engine, script_dir):
    """Returns callable functions from loaded scripts."""
    _write_script(script_dir, "room.py", """
        def toggle_power():
            \"\"\"Toggle room power.\"\"\"
            pass

        def select_source(name):
            pass

        def _private_helper():
            pass
    """)
    engine.load_scripts([{"id": "room", "file": "room.py", "enabled": True}])

    functions = engine.get_callable_functions()
    names = [f["function"] for f in functions]
    assert "toggle_power" in names
    assert "select_source" in names
    assert "_private_helper" not in names

    # Check doc is included
    toggle = next(f for f in functions if f["function"] == "toggle_power")
    assert toggle["script"] == "room"
    assert "Toggle room power" in toggle["doc"]


def test_get_callable_functions_empty(engine):
    """No scripts loaded returns empty list."""
    assert engine.get_callable_functions() == []


# ===== H-069: scripts-dir path containment =====


async def test_script_path_traversal_is_refused(engine, subsystems, script_dir):
    """A scripts[].file that escapes the scripts dir is refused, not exec'd."""
    state, events, devices = subsystems

    # Plant a file OUTSIDE scripts/ that would set state if it were exec'd.
    evil = script_dir / "evil.py"
    evil.write_text(
        "from openavc import state\nstate.set('var.pwned', 'yes')\n", encoding="utf-8"
    )

    count = engine.load_scripts(
        [{"id": "evil", "file": "../evil.py", "enabled": True}]
    )
    assert count == 0
    assert state.get("var.pwned") is None  # never executed
    assert "evil" in engine.get_load_errors()
    assert "escape" in engine.get_load_errors()["evil"].lower()


async def test_script_absolute_path_is_refused(engine, subsystems, script_dir, tmp_path):
    """An absolute scripts[].file path is refused."""
    state, events, devices = subsystems
    outside = tmp_path / "outside.py"
    outside.write_text(
        "from openavc import state\nstate.set('var.pwned2', 'yes')\n", encoding="utf-8"
    )
    count = engine.load_scripts(
        [{"id": "abs", "file": str(outside), "enabled": True}]
    )
    assert count == 0
    assert state.get("var.pwned2") is None
    assert "abs" in engine.get_load_errors()


# ===== H-068: bounded state-change cascade =====


async def test_state_cascade_depth_is_bounded(engine, subsystems, script_dir):
    """A self-feeding async @on_state_change loop is capped, not unbounded."""
    state, events, devices = subsystems

    _write_script(script_dir, "loop.py", """\
        from openavc import on_state_change, state

        @on_state_change("var.counter")
        async def bump(key, old_value, new_value):
            state.set("var.counter", (new_value or 0) + 1)
    """)
    engine.load_scripts([{"id": "loop", "file": "loop.py", "enabled": True}])

    state.set("var.counter", 0, source="test")
    # Let the cascade run to its bound.
    await asyncio.sleep(0.2)

    capped = state.get("var.counter")
    # Each of the MAX nested hops increments once; the loop then stops.
    assert capped == engine.MAX_STATE_HANDLER_DEPTH

    # Confirm it has truly stopped (not merely slow): value is stable.
    await asyncio.sleep(0.1)
    assert state.get("var.counter") == engine.MAX_STATE_HANDLER_DEPTH


# ===== M-118: async state handlers get a timeout + surface errors =====


async def test_async_state_handler_error_surfaces(engine, subsystems, script_dir):
    """An exception inside an async @on_state_change body emits script.error."""
    state, events, devices = subsystems

    seen: list[dict] = []
    events.on("script.error", lambda e, p: seen.append(p))

    _write_script(script_dir, "raiser.py", """\
        from openavc import on_state_change

        @on_state_change("var.trigger")
        async def boom(key, old_value, new_value):
            raise RuntimeError("kaboom")
    """)
    engine.load_scripts([{"id": "raiser", "file": "raiser.py", "enabled": True}])

    state.set("var.trigger", "go")
    await asyncio.sleep(0.05)

    assert any("kaboom" in (p.get("error") or "") for p in seen), seen
    assert any(p.get("script_id") == "raiser" for p in seen)


async def test_async_state_handler_timeout_surfaces(engine, subsystems, script_dir):
    """An async @on_state_change body that hangs times out and emits script.error."""
    state, events, devices = subsystems
    engine.HANDLER_TIMEOUT = 0.05  # keep the test fast

    seen: list[dict] = []
    events.on("script.error", lambda e, p: seen.append(p))

    _write_script(script_dir, "hang.py", """\
        from openavc import on_state_change, delay

        @on_state_change("var.trigger")
        async def slow(key, old_value, new_value):
            await delay(5)
    """)
    engine.load_scripts([{"id": "hang", "file": "hang.py", "enabled": True}])

    state.set("var.trigger", "go")
    await asyncio.sleep(0.2)

    assert any("timed out" in (p.get("error") or "") for p in seen), seen


# ===== M-119: timed-out load thread is a daemon (won't block shutdown) =====


async def test_load_timeout_thread_is_daemon(engine, subsystems, script_dir):
    """A script that blocks at import times out and leaves only a daemon thread."""
    engine.SCRIPT_LOAD_TIMEOUT = 0.1

    _write_script(script_dir, "slow_load.py", """\
        import time
        time.sleep(1)
    """)
    count = engine.load_scripts(
        [{"id": "slow_load", "file": "slow_load.py", "enabled": True}]
    )
    assert count == 0
    assert "slow_load" in engine.get_load_errors()
    assert "timed out" in engine.get_load_errors()["slow_load"]

    # The abandoned load thread must be a daemon so it can't block interpreter
    # shutdown (the bug: ThreadPoolExecutor workers are non-daemon and joined
    # at exit).
    load_threads = [
        t for t in threading.enumerate() if t.name.startswith("script-load-slow_load")
    ]
    assert load_threads, "expected an abandoned load thread"
    assert all(t.daemon for t in load_threads)


# ===== Top-level timers (defer-and-drain): documented pattern works =====


async def test_top_level_timer_materializes(engine, subsystems, script_dir):
    """A top-level after() call (run off-loop during import) still fires."""
    state, events, devices = subsystems

    _write_script(script_dir, "timed.py", """\
        from openavc import after, state

        def fire():
            state.set("var.fired", "yes")

        after(0.05, fire)
    """)
    engine.load_scripts([{"id": "timed", "file": "timed.py", "enabled": True}])
    assert engine.get_load_errors() == {}  # no RuntimeError at load

    await asyncio.sleep(0.2)
    assert state.get("var.fired") == "yes"


# ===== M-120: per-script reload isolation + preserve-on-failure =====


async def test_reload_script_leaves_peers_running(engine, subsystems, script_dir):
    """Reloading one script doesn't tear down another script's handlers."""
    state, events, devices = subsystems

    _write_script(script_dir, "a.py", """\
        from openavc import on_state_change, state

        @on_state_change("var.a")
        async def ha(key, old, new):
            state.set("var.a_out", f"v1:{new}")
    """)
    _write_script(script_dir, "b.py", """\
        from openavc import on_state_change, state

        @on_state_change("var.b")
        async def hb(key, old, new):
            state.set("var.b_out", f"b:{new}")
    """)
    cfg_a = {"id": "a", "file": "a.py", "enabled": True}
    cfg_b = {"id": "b", "file": "b.py", "enabled": True}
    engine.load_scripts([cfg_a, cfg_b])

    state.set("var.b", "1")
    await asyncio.sleep(0.05)
    assert state.get("var.b_out") == "b:1"

    # Rewrite + reload ONLY script a.
    _write_script(script_dir, "a.py", """\
        from openavc import on_state_change, state

        @on_state_change("var.a")
        async def ha(key, old, new):
            state.set("var.a_out", f"v2:{new}")
    """)
    result = engine.reload_script(cfg_a)
    assert result["status"] == "reloaded"

    # a's new behavior is live...
    state.set("var.a", "x")
    await asyncio.sleep(0.05)
    assert state.get("var.a_out") == "v2:x"

    # ...and b's handler still fires (it was never torn down).
    state.set("var.b", "2")
    await asyncio.sleep(0.05)
    assert state.get("var.b_out") == "b:2"
    # b kept its single subscription; a was swapped, not duplicated.
    assert len(engine._state_sub_ids["b"]) == 1
    assert len(engine._state_sub_ids["a"]) == 1


async def test_reload_script_preserves_old_on_failure(engine, subsystems, script_dir):
    """If the new version fails to import, the old version stays active."""
    state, events, devices = subsystems

    _write_script(script_dir, "p.py", """\
        from openavc import on_state_change, state

        @on_state_change("var.in")
        async def h(key, old, new):
            state.set("var.out", f"good:{new}")
    """)
    cfg = {"id": "p", "file": "p.py", "enabled": True}
    engine.load_scripts([cfg])

    state.set("var.in", "1")
    await asyncio.sleep(0.05)
    assert state.get("var.out") == "good:1"

    # Break the source and reload — import must fail.
    _write_script(script_dir, "p.py", """\
        from openavc import on_state_change, state
        raise RuntimeError("broken on import")
    """)
    result = engine.reload_script(cfg)
    assert result["status"] == "error"
    assert result["old_script_preserved"] is True
    assert "broken on import" in result["error"]

    # The previously loaded handler is still serving.
    state.set("var.in", "2")
    await asyncio.sleep(0.05)
    assert state.get("var.out") == "good:2"


# ===== M-120: per-script timer ownership =====


async def test_cancel_script_timers_is_scoped():
    """cancel_script_timers only cancels the owning script's timers."""
    script_api.cancel_all_timers()  # clean slate

    with script_api.current_script_context("alpha"):
        t_alpha = script_api.every(100, lambda: None)
    with script_api.current_script_context("beta"):
        t_beta = script_api.every(100, lambda: None)

    assert script_api._timer_owners.get(t_alpha) == "alpha"
    assert script_api._timer_owners.get(t_beta) == "beta"

    cancelled = script_api.cancel_script_timers("alpha")
    assert cancelled == 1
    assert t_alpha not in script_api._active_timers
    assert t_beta in script_api._active_timers  # beta untouched

    script_api.cancel_all_timers()


# --- Macro call chain survives the script boundary ---


async def test_macro_chain_survives_script_boundary(subsystems):
    """A macro that drives a handler which re-enters the same macro via the
    script proxy must hit the engine's circular guard, not restart the chain."""
    from server.core.macro_engine import MacroEngine

    state, events, devices = subsystems
    macro_engine = MacroEngine(state, events, devices)
    macro_engine.load_macros([{
        "id": "loop_macro",
        "name": "Loop",
        "steps": [{"action": "event.emit", "event": "loop.go"}],
    }])

    proxy = script_api._MacroProxy()
    proxy._bind(macro_engine)

    runs: list[int] = []
    reentry_errors: list[str] = []

    async def handler(event, payload):
        runs.append(1)
        if len(runs) > 15:
            return  # safety brake against pre-fix runaway re-entry
        try:
            await proxy.execute("loop_macro")
        except ValueError as e:
            reentry_errors.append(str(e))

    events.on("loop.go", handler)
    await macro_engine.execute("loop_macro")
    await asyncio.sleep(0.05)

    assert len(runs) == 1, f"macro re-entered {len(runs)} times across the script boundary"
    assert reentry_errors and "circular" in reentry_errors[0]


async def test_macro_proxy_outside_macro_context_unaffected(subsystems):
    """macros.execute from a plain handler (no active macro) still works."""
    from server.core.macro_engine import MacroEngine

    state, events, devices = subsystems
    macro_engine = MacroEngine(state, events, devices)
    macro_engine.load_macros([{
        "id": "plain",
        "name": "Plain",
        "steps": [{"action": "state.set", "key": "var.ran", "value": True}],
    }])
    proxy = script_api._MacroProxy()
    proxy._bind(macro_engine)

    await proxy.execute("plain")
    assert state.get("var.ran") is True


# --- Timer callback protections (script.error parity with event handlers) ---


class _EmitRecorder:
    def __init__(self):
        self.emitted = []

    async def emit(self, name, payload=None):
        self.emitted.append((name, payload))


async def test_after_async_callback_error_emits_script_error(monkeypatch):
    recorder = _EmitRecorder()
    monkeypatch.setattr(script_api, "events", recorder)

    async def boom():
        raise RuntimeError("kaput")

    script_api.after(0.01, boom)
    await asyncio.sleep(0.15)

    errors = [p for n, p in recorder.emitted if n == "script.error"]
    assert len(errors) == 1
    assert errors[0]["handler"] == "boom"
    assert "kaput" in errors[0]["error"]
    assert "RuntimeError" in errors[0]["traceback"]


async def test_after_async_callback_timeout_emits_script_error(monkeypatch):
    """A never-finishing async timer body is bounded by the handler timeout."""
    recorder = _EmitRecorder()
    monkeypatch.setattr(script_api, "events", recorder)
    monkeypatch.setattr(ScriptEngine, "HANDLER_TIMEOUT", 0.05)

    async def hang():
        await asyncio.Event().wait()

    script_api.after(0.01, hang)
    await asyncio.sleep(0.3)

    errors = [p for n, p in recorder.emitted if n == "script.error"]
    assert len(errors) == 1
    assert "timed out" in errors[0]["error"]


async def test_every_sync_callback_error_emits_and_keeps_ticking(monkeypatch):
    """A sync callback that raises surfaces script.error each tick and never
    kills the interval loop."""
    recorder = _EmitRecorder()
    monkeypatch.setattr(script_api, "events", recorder)

    count = 0

    def tick():
        nonlocal count
        count += 1
        raise ValueError("tick failed")

    timer_id = script_api.every(0.02, tick)
    try:
        await asyncio.sleep(0.15)
    finally:
        script_api.cancel_timer(timer_id)

    assert count >= 2, "interval loop died after a callback error"
    errors = [p for n, p in recorder.emitted if n == "script.error"]
    assert len(errors) >= 2
    assert errors[0]["timer_id"] == timer_id
    assert "tick failed" in errors[0]["error"]
