"""A project cannot send the platform's own events.

`system.started` runs every Startup trigger, `system.stopping` every stopping
one, and `system.project.reloaded` has the cloud's alert monitor read its rules
again. A macro's Emit Event step, a control's event action and a script's
`events.emit` used to send any name, so a project could tell all of them
something that did not happen (and, before the restart door was its own, a
startup trigger that sent the restart event restarted the server forever).
Every `system.` name is now refused at those three doors, with one sentence
that names the event to use instead; the macro check marks the step before it
runs, and the AI's macro tools refuse it.

The REST and WebSocket doors are narrower still (`custom.` only) and a
plugin's emits carry its own prefix; both are tested where they live.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.engine import Engine
from openavc.core.event_bus import (
    PLATFORM_EVENTS,
    EventBus,
    check_project_emit,
    silent_pattern_sentence,
    silent_platform_pattern,
)
from openavc.core.event_references import PLATFORM_EVENT, script_issues
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_validation import macro_issues, validate_macro
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.project_loader import ProjectConfig, ProjectMeta, ScriptConfig, load_project
from openavc.core.script_api import _EventProxy
from openavc.core.state_store import StateStore

PACKAGE = Path(__file__).resolve().parent.parent / "openavc"


def _bus_hearing(pattern: str) -> tuple[EventBus, list[str]]:
    bus = EventBus()
    heard: list[str] = []
    bus.on(pattern, lambda name, _payload: heard.append(name))
    return bus, heard


# --- The rule ----------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "system.started", "system.stopping", "system.project.reloaded",
    "system.restart_requested", "system.panic", "System.Started", "  system.started",
])
def test_every_name_in_the_platform_namespace_is_refused(name):
    """The whole namespace, as the bus matches it: `fnmatch` folds case on
    Windows, so `System.Started` reaches a `system.started` trigger there."""
    reason = check_project_emit(name)
    assert reason is not None
    assert reason.startswith(f"Event '{name}' is not sent")


def test_the_sentence_names_the_event_to_use_instead():
    assert check_project_emit("system.room_occupied") == (
        "Event 'system.room_occupied' is not sent: names starting with 'system.' are "
        "OpenAVC's own. Name this event 'custom.room_occupied'."
    )


@pytest.mark.parametrize("name", [
    "custom.room_occupied", "room.shutdown_complete", "schedule.morning_on",
    "systemic.alarm", "my.system.thing", "", None, 3,
])
def test_a_project_s_own_names_are_sent(name):
    assert check_project_emit(name) is None


def test_the_platform_s_list_is_every_system_event_the_code_sends():
    """`PLATFORM_EVENTS` is what a listener may hear under `system.`; the
    checks mark any other `system.` pattern as hearing nothing. Read from the
    code so a new platform event cannot be left off it, nor a removed one
    left on."""
    sent: set[str] = set()
    pattern = re.compile(r"""emit(?:\(|,)\s*f?["'](system\.[^"'{]*)(["'{])""")
    for path in PACKAGE.rglob("*.py"):
        for name, end in pattern.findall(path.read_text(encoding="utf-8")):
            assert end != "{", f"{path}: a system. event named at run time: {name}..."
            sent.add(name)
    assert sent == set(PLATFORM_EVENTS)


@pytest.mark.parametrize("pattern,silent", [
    ("system.panic", True),
    ("system.room_*", True),
    ("system.started", False),
    ("system.*", False),
    ("system.project.*", False),
    ("custom.panic", False),
    ("*", False),
])
def test_a_listener_hears_nothing_under_system_but_the_platform_s_names(pattern, silent):
    assert silent_platform_pattern(pattern) is silent


# --- The three doors ---------------------------------------------------------

@pytest.fixture
def macros() -> tuple[MacroEngine, EventBus]:
    state = StateStore()
    bus = EventBus()
    state.set_event_bus(bus)
    devices = DeviceManager(state, bus)
    devices.send_command = AsyncMock()
    return MacroEngine(state, bus, devices), bus


@pytest.mark.asyncio
async def test_a_macro_step_is_refused_and_says_so(macros):
    engine, bus = macros
    started: list[str] = []
    bus.on("system.started", lambda name, _p: started.append(name))
    errors: list[dict] = []
    bus.on("macro.step_error.*", lambda _n, payload: errors.append(payload))
    engine.load_macros([{
        "id": "fake_start", "name": "Fake start",
        "steps": [{"action": "event.emit", "event": "system.started"}],
    }])

    await engine.execute("fake_start")

    assert started == []
    assert len(errors) == 1
    assert errors[0]["error"] == check_project_emit("system.started")


@pytest.mark.asyncio
async def test_a_macro_step_still_sends_a_custom_event(macros):
    engine, bus = macros
    heard: list[str] = []
    bus.on("custom.*", lambda name, _p: heard.append(name))
    engine.load_macros([{
        "id": "ready", "name": "Ready",
        "steps": [{"action": "event.emit", "event": "custom.room_ready"}],
    }])

    await engine.execute("ready")

    assert heard == ["custom.room_ready"]


def _engine_with_button(tmp_path: Path, event: str) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        # The current format: an older one renames a project's own system.
        # event on load (tests/test_event_migration.py).
        "openavc_version": CURRENT_VERSION,
        "project": {"id": "acme", "name": "Acme", "description": ""},
        "devices": [], "macros": [], "variables": [],
        "ui": {"settings": {}, "pages": [{
            "id": "main", "name": "Main", "layouts": [],
            "elements": [{
                "id": "btn", "type": "button", "label": "Go",
                "bindings": {"do": {"press": [{"action": "event.emit", "event": event}]}},
            }],
        }]},
    }), encoding="utf-8")
    engine = Engine(path)
    engine.project = load_project(path)
    return engine


@pytest.mark.asyncio
async def test_a_control_s_event_action_is_refused_and_recorded(tmp_path):
    """Recorded for the AI's simulated press, with no `error` field: the WS
    door turns one into a message on the panel, and the room cannot act on it."""
    engine = _engine_with_button(tmp_path, "system.stopping")
    heard: list[str] = []
    engine.events.on("system.*", lambda name, _p: heard.append(name))

    dispatched = await engine.handle_ui_event("press", "btn")

    assert heard == []
    assert dispatched == [{
        "action": "event.emit", "event": "system.stopping", "payload": {},
        "emitted": False, "refused": check_project_emit("system.stopping"),
    }]


@pytest.mark.asyncio
async def test_a_control_s_dry_run_says_it_would_be_refused(tmp_path):
    engine = _engine_with_button(tmp_path, "system.panic")

    dispatched = await engine.handle_ui_event("press", "btn", dry_run=True)

    assert dispatched[0]["refused"] == check_project_emit("system.panic")
    assert dispatched[0]["emitted"] is False


@pytest.mark.asyncio
async def test_a_script_s_emit_is_refused():
    bus, heard = _bus_hearing("system.*")
    proxy = _EventProxy()
    proxy._bind(bus)

    with pytest.raises(ValueError) as refused:
        await proxy.emit("system.project.reloaded")

    assert str(refused.value) == check_project_emit("system.project.reloaded")
    assert heard == []


@pytest.mark.asyncio
async def test_a_script_still_sends_a_custom_event():
    bus, heard = _bus_hearing("custom.*")
    proxy = _EventProxy()
    proxy._bind(bus)

    await proxy.emit("custom.room_ready", {"n": 1})

    assert heard == ["custom.room_ready"]


# --- The macro check, and the AI's macro tools -------------------------------

def test_the_macro_check_marks_the_step_with_the_runtime_s_sentence():
    issues = macro_issues(
        [{"action": "conditional", "condition": {"key": "var.x"},
          "then_steps": [{"action": "event.emit", "event": "system.panic"}]}],
        [],
    )
    assert issues == [{
        "scope": "step", "index": 0, "path": "steps[0].then_steps[0]",
        "message": check_project_emit("system.panic"),
        "action": "event.emit", "refused": "platform_event",
    }]


def test_the_macro_check_marks_a_trigger_that_hears_nothing():
    issues = macro_issues([], [
        {"type": "event", "event_pattern": "system.panic"},
        {"type": "event", "event_pattern": "system.started"},
        {"type": "event", "event_pattern": "system.*"},
        {"type": "event", "event_pattern": "custom.panic"},
    ])
    assert [(i["index"], i["message"]) for i in issues] == [(0, (
        "Nothing sends 'system.panic': under 'system.' OpenAVC sends only "
        "system.project.reloaded, system.started and system.stopping. "
        "A project's own event is named 'custom.panic'."
    ))]


def test_the_ai_s_macro_tools_refuse_the_step():
    refusal = validate_macro([{"action": "event.emit", "event": "system.started"}], [])
    assert refusal == (
        "Macro validation failed: steps[0]: " + check_project_emit("system.started")
    )


# --- The script check --------------------------------------------------------

_SCRIPT = '''\
from openavc import events, on_event


@on_event("system.room_occupied")
async def occupied(event, payload):
    await events.emit("system.panic")


@on_event("system.started")
async def boot(event, payload):
    await events.emit("custom.ready")


@on_event("system.*")
async def platform(event, payload):
    await events.emit(f"custom.{payload}")
'''


def test_the_script_check_marks_each_system_name_at_its_line():
    """A script is code and is not renamed when an older project opens, so
    the script editor is where it hears of the change."""
    issues = script_issues({"room": _SCRIPT}).get("room", [])
    assert [(i["kind"], i["line"], i["event"], i["message"]) for i in issues] == [
        (PLATFORM_EVENT, 4, "system.room_occupied", silent_pattern_sentence("system.room_occupied")),
        (PLATFORM_EVENT, 6, "system.panic", check_project_emit("system.panic")),
    ]


async def test_the_ai_is_told_when_it_writes_one(tmp_path):
    from openavc.api import rest
    from openavc.cloud.ai_tool_handler import AIToolHandler

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "room.py").write_text("x = 1\n", encoding="utf-8")
    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="acme", name="Acme"),
        scripts=[ScriptConfig(id="room", file="room.py")],
    )
    engine.project_path = tmp_path / "project.avc"
    engine.scripts = None
    rest.set_engine(engine)
    try:
        handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
        result = await handler._update_script_source({"script_id": "room", "source": _SCRIPT})
    finally:
        rest.set_engine(None)

    assert result["status"] == "saved"
    assert check_project_emit("system.panic") in result["warnings"]
    assert "Names starting with system. are OpenAVC's own" in result["warning_note"]
