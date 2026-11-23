"""What a script and a macro's Set Variable step may write into the state store.

Any key but the platform's own (``system.*``, ``isc.*``), and a single value:
text, a number, true or false, or nothing. A refused write is not made. It is
reported, in the sentence the REST and WebSocket doors give, on the script's
row or the macro's run, and the run carries on: a refused write halfway through
a shutdown macro must not skip the power-offs after it. A ``device.*`` key stays
writable, as it is at the doors.

Every script, macro, device and variable here is invented.
"""

from __future__ import annotations

import asyncio
import textwrap
import types
from unittest.mock import AsyncMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.help_requests import KEY_STATE, UNREACHABLE, HelpRequests
from openavc.core.macro_engine import MacroEngine
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import (
    DOOR_READONLY_PREFIXES,
    StateStore,
    check_authored_write,
    check_state_write,
)


@pytest.fixture
def subsystems():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    return state, events, devices


@pytest.fixture
def scripts(subsystems, tmp_path):
    (tmp_path / "scripts").mkdir()
    state, events, devices = subsystems
    engine = ScriptEngine(state, events, devices, tmp_path)
    engine.install()
    yield engine
    engine.unload_all()


@pytest.fixture
def macros(subsystems):
    state, events, devices = subsystems
    return MacroEngine(state, events, devices)


def _load(engine: ScriptEngine, script_id: str, code: str) -> None:
    path = engine.scripts_dir / f"{script_id}.py"
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    engine.load_scripts([{"id": script_id, "file": f"{script_id}.py", "enabled": True}])
    assert engine.get_load_errors() == {}, engine.get_load_errors()


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


def _platform_sentence(key: str) -> str:
    prefix = next(p for p in DOOR_READONLY_PREFIXES if key.startswith(p))
    return DOOR_READONLY_PREFIXES[prefix].format(key=key)


# --- the rule ----------------------------------------------------------------


@pytest.mark.parametrize("key", ["system.help_state", "system.version", "isc.lobby.var.mode"])
def test_a_platform_key_is_refused_in_the_doors_words(key):
    assert check_authored_write(key, "x") == _platform_sentence(key)
    assert check_authored_write(key, "x") == check_state_write(key, "x")


@pytest.mark.parametrize("value", [[1, 2], {"a": 1}, (1,), {1}])
def test_a_value_that_is_not_a_single_value_is_refused_in_the_doors_words(value):
    refused = check_authored_write("var.rows", value)
    assert refused and refused == check_state_write("var.rows", value)


@pytest.mark.parametrize("key", [
    "var.room_mode", "ui.btn_power.label", "plugin.acme_bridge.mode",
    "device.acme_widget.power",
    # Off the namespaces: the doors refuse it, an authored writer is not held
    # to that (it is stored, as it always was).
    "room_mode",
])
@pytest.mark.parametrize("value", ["on", 3, 2.5, True, None])
def test_any_other_key_takes_a_single_value(key, value):
    assert check_authored_write(key, value) is None


# --- a script ------------------------------------------------------------------


async def test_a_script_cannot_stop_a_help_request_reaching_unreachable(scripts, subsystems):
    """The defect: a script that wrote ``system.help_state`` while a request
    was going out left it there, so a request nobody received never read
    "unreachable" on the panel."""
    state, events, _ = subsystems
    engine = types.SimpleNamespace(state=state, events=events, project=None, cloud_agent=None)
    help_requests = HelpRequests(engine)
    help_requests.declare_keys()
    _load(scripts, "front_desk", """\
        from openavc import on_event, state

        @on_event("help.requested")
        async def mark_seen(event):
            state.set("system.help_state", "acknowledged")
    """)

    result = await help_requests.raise_request("Projector will not turn on", cooldown=0)

    assert result["delivered"] is False
    assert state.get(KEY_STATE) == UNREACHABLE
    entry = scripts.get_runtime_errors()["front_desk"]
    assert entry["error"] == _platform_sentence(KEY_STATE)


async def test_a_refused_write_is_recorded_on_its_script_and_the_script_carries_on(
    scripts, subsystems,
):
    state, events, _ = subsystems
    state.set("system.version", "9.9.9", source="system")
    _load(scripts, "lectern", """\
        from openavc import on_event, state

        @on_event("custom.start")
        async def start(event):
            state.set("system.version", "1.0")
            state.set("var.after", True)
    """)

    await events.emit("custom.start")

    assert state.get("system.version") == "9.9.9"
    assert state.get("var.after") is True
    entry = scripts.get_runtime_errors()["lectern"]
    assert entry["count"] == 1
    assert entry["handler"] == "start"
    assert entry["event"] == "state.set"
    assert entry["error"] == _platform_sentence("system.version")
    # The trace ends at the script's own line, so the editor can mark it.
    assert entry["traceback"].rstrip().endswith('state.set("system.version", "1.0")')
    assert 'lectern.py", line 5, in start' in entry["traceback"]


async def test_a_plain_def_handlers_refused_write_names_its_own_line(scripts, subsystems):
    """A plain ``def`` runs on a worker thread and its call is handed to the
    loop; the line it refused at is still the one recorded."""
    state, events, _ = subsystems
    _load(scripts, "mirror", """\
        from openavc import on_event, state

        @on_event("custom.copy")
        def copy(event):
            state.set("isc.lobby.var.mode", "on")
            state.set("var.copied", True)
    """)

    await events.emit("custom.copy")
    await _wait_for(lambda: state.get("var.copied") is True)

    assert not state.has("isc.lobby.var.mode")
    entry = scripts.get_runtime_errors()["mirror"]
    assert entry["handler"] == "copy"
    assert entry["error"] == _platform_sentence("isc.lobby.var.mode")
    assert entry["traceback"].rstrip().endswith('state.set("isc.lobby.var.mode", "on")')


async def test_a_list_written_at_load_is_refused_and_recorded_not_dropped(scripts, subsystems):
    """It was dropped with a warning in the system log and nothing on the
    script's row; the script still loads and its next line still runs."""
    state, _, _ = subsystems
    _load(scripts, "setup", """\
        from openavc import state

        state.set("var.rows", [1, 2, 3])
        state.set("var.loaded", True)
    """)

    assert not state.has("var.rows")
    assert state.get("var.loaded") is True
    entry = scripts.get_runtime_errors()["setup"]
    assert entry["event"] == "state.set"
    assert entry["error"] == check_state_write("var.rows", [1, 2, 3])
    assert entry["traceback"].rstrip().endswith('state.set("var.rows", [1, 2, 3])')


async def test_a_script_cannot_delete_a_platform_key(scripts, subsystems):
    state, events, _ = subsystems
    state.set(KEY_STATE, "idle", source="system")
    _load(scripts, "tidy", """\
        from openavc import on_event, state

        @on_event("custom.tidy")
        async def tidy(event):
            state.delete("system.help_state")
            state.delete("var.scratch")
    """)
    state.set("var.scratch", 1)

    await events.emit("custom.tidy")

    assert state.get(KEY_STATE) == "idle"
    assert not state.has("var.scratch")
    entry = scripts.get_runtime_errors()["tidy"]
    assert entry["event"] == "state.delete"
    assert entry["error"] == _platform_sentence(KEY_STATE)


async def test_a_script_still_writes_a_device_key_and_its_own(scripts, subsystems):
    state, events, _ = subsystems
    _load(scripts, "bench", """\
        from openavc import on_event, state

        @on_event("custom.fake")
        async def fake(event):
            state.set("device.acme_widget.power", "on")
            state.set("var.room_mode", "lecture")
            state.set("room_mode", "lecture")
    """)

    await events.emit("custom.fake")

    assert state.get("device.acme_widget.power") == "on"
    assert state.get("var.room_mode") == "lecture"
    assert state.get("room_mode") == "lecture"
    assert "bench" not in scripts.get_runtime_errors()


# --- a macro's Set Variable step ---------------------------------------------------


def _step_errors(events: EventBus, macro_id: str) -> list[dict]:
    frames: list[dict] = []
    events.on(f"macro.step_error.{macro_id}", lambda _e, payload: frames.append(payload))
    return frames


async def test_a_refused_set_variable_is_reported_and_the_macro_carries_on(macros, subsystems):
    """Even under stop_on_error: the step is refused, not failed, so the
    power-off after it still goes out."""
    state, events, devices = subsystems
    state.set(KEY_STATE, "idle", source="system")
    frames = _step_errors(events, "shutdown")
    macros.load_macros([{
        "id": "shutdown",
        "name": "Shutdown",
        "stop_on_error": True,
        "steps": [
            {"action": "state.set", "key": "system.help_state", "value": "acknowledged"},
            {"action": "device.command", "device": "acme_widget", "command": "power_off"},
        ],
    }])

    outcome = await macros.execute("shutdown")

    assert state.get(KEY_STATE) == "idle"
    devices.send_command.assert_awaited_once()
    assert outcome == "failed"
    assert len(frames) == 1
    assert frames[0]["step_index"] == 0
    assert frames[0]["error"] == _platform_sentence(KEY_STATE)


async def test_a_refused_set_variable_in_a_called_macro_does_not_stop_the_caller(
    macros, subsystems,
):
    state, events, devices = subsystems
    macros.load_macros([
        {
            "id": "all_off",
            "name": "All Off",
            "stop_on_error": True,
            "steps": [
                {"action": "macro", "macro": "flag_room"},
                {"action": "device.command", "device": "acme_widget", "command": "power_off"},
            ],
        },
        {
            "id": "flag_room",
            "name": "Flag Room",
            "stop_on_error": True,
            "steps": [
                {"action": "state.set", "key": "isc.lobby.var.mode", "value": "off"},
                {"action": "state.set", "key": "var.flagged", "value": True},
            ],
        },
    ])

    await macros.execute("all_off")

    assert state.get("var.flagged") is True
    assert not state.has("isc.lobby.var.mode")
    devices.send_command.assert_awaited_once()


async def test_a_value_that_resolves_to_a_list_is_refused_and_reported(macros, subsystems):
    """A literal is caught while writing; a ``$trigger`` value is only known
    when the step runs."""
    state, events, _ = subsystems
    frames = _step_errors(events, "keep_payload")
    macros.load_macros([{
        "id": "keep_payload",
        "name": "Keep Payload",
        "steps": [
            {"action": "state.set", "key": "var.payload", "value": "$trigger.data"},
            {"action": "state.set", "key": "var.after", "value": True},
        ],
    }])

    await macros.execute("keep_payload", {"data": {"zone": 2}})

    assert not state.has("var.payload")
    assert state.get("var.after") is True
    assert frames and frames[0]["error"] == check_state_write("var.payload", {"zone": 2})


async def test_a_set_variable_still_writes_a_device_key(macros, subsystems):
    state, _, _ = subsystems
    macros.load_macros([{
        "id": "fake_feedback",
        "name": "Fake Feedback",
        "steps": [{"action": "state.set", "key": "device.acme_widget.power", "value": "on"}],
    }])

    assert await macros.execute("fake_feedback") == "completed"
    assert state.get("device.acme_widget.power") == "on"


async def test_a_refusal_no_script_owns_is_still_logged(scripts, subsystems, caplog):
    """A thread the script starts itself carries no running script, so there
    is no row to record on; the refusal still reaches the log, as the dropped
    list it replaces did."""
    state, events, _ = subsystems
    _load(scripts, "spawner", """\
        import threading
        from openavc import on_event, state

        def work():
            state.set("system.version", "1.0")
            state.set("var.worked", True)

        @on_event("custom.spawn")
        async def spawn(event):
            threading.Thread(target=work).start()
    """)
    state.set("system.version", "9.9.9", source="system")

    with caplog.at_level("ERROR"):
        await events.emit("custom.spawn")
        await _wait_for(lambda: state.get("var.worked") is True)

    assert state.get("system.version") == "9.9.9"
    assert any(
        _platform_sentence("system.version") in record.getMessage()
        and "work" in record.getMessage()
        for record in caplog.records
    ), [r.getMessage() for r in caplog.records]
