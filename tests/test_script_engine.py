"""Tests for ScriptEngine and script_api."""

import asyncio
import sys
import textwrap

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
