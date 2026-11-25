"""Every script the Programmer writes for an integrator loads, and its handlers run.

The Scripts view's templates and the Macros view's Convert to Script both write
Python that is saved as a project script, and saving loads it through
``ScriptEngine.load_scripts``. A handler in a form the engine refuses, or one
listening for an event nothing emits, is a script that fails at its first save
or never runs. So each one is loaded here through the real engine, and the
handlers that need no device are fired with the events the platform emits.

The sources are the real ``scriptTemplates.ts``, ``macroHelpers.ts``,
``triggerHelpers.ts`` and ``firingContext.gen.ts``, bundled with the esbuild in
``openavc/web/programmer/node_modules`` (``tests/fixtures/generated_scripts_harness.cjs``).
The template, step and trigger lists come from the IDE's own ``SCRIPT_TEMPLATES``,
``STEP_TYPES`` and ``TRIGGER_TYPES``, and the last two are held to the engine's
lists, so a new one is loaded here without anyone adding it.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

from openavc.core import script_api
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_validation import BUILTIN_STEP_ACTIONS, VALID_TRIGGER_TYPES
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine
from openavc.core.value_resolver import FIRING_CONTEXTS

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "generated_scripts_harness.cjs"
COMPONENTS = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components"
SOURCES = (
    COMPONENTS / "scripts" / "scriptTemplates.ts",
    COMPONENTS / "macros" / "macroHelpers.ts",
    COMPONENTS / "macros" / "triggerHelpers.ts",
    COMPONENTS.parent / "api" / "firingContext.gen.ts",
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not (NODE_MODULES / "esbuild").is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    for path in (HARNESS, *SOURCES):
        if not path.is_file():
            return f"{path.name} missing"
    return None


@pytest.fixture(scope="module")
def generated() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), *map(str, SOURCES)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"generated scripts harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


@pytest.fixture
def subsystems():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    return state, events, devices


@pytest.fixture
def engine(subsystems, tmp_path):
    state, events, devices = subsystems
    (tmp_path / "scripts").mkdir()
    se = ScriptEngine(state, events, devices, tmp_path)
    se.install()
    yield se
    se.unload_all()


def _load(engine: ScriptEngine, script_id: str, source: str) -> int:
    """Save *source* as a script and load it the way a save does; the handler
    count, or AssertionError with the engine's refusal."""
    (engine.scripts_dir / f"{script_id}.py").write_text(source, encoding="utf-8")
    count = engine.load_scripts([{"id": script_id, "file": f"{script_id}.py", "enabled": True}])
    errors = engine.get_load_errors()
    if errors:
        raise AssertionError(f"refused at load: {errors[script_id]}\n--- source ---\n{source}")
    return count


async def _settle(check, timeout: float = 2.0) -> bool:
    """Wait for *check* to hold: a plain ``def`` handler runs on its script's
    own thread, so its writes land after the emit returns."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.01)
    return True


def _runtime_errors(engine: ScriptEngine) -> dict:
    return {sid: rec["error"] for sid, rec in engine.get_runtime_errors().items()}


# --- Templates ---------------------------------------------------------------


async def test_every_template_loads(generated, subsystems, tmp_path):
    """Each template in the Scripts view's Templates menu loads as written."""
    state, events, devices = subsystems
    (tmp_path / "scripts").mkdir()
    refused = []
    for i, template in enumerate(generated["templates"]):
        se = ScriptEngine(state, events, devices, tmp_path)
        se.install()
        try:
            count = _load(se, f"template_{i}", template["code"])
            timers = _timers_of(f"template_{i}")
            assert count >= 1 or timers, f"{template['name']} registers no handler and starts no timer"
        except AssertionError as exc:
            refused.append(f"{template['name']}: {exc}")
        finally:
            se.unload_all()
    assert generated["templates"], "the harness found no templates"
    assert not refused, "\n\n".join(refused)


def _timers_of(script_id: str) -> list[str]:
    """The live timers a script started (the timer table is the process's)."""
    return [
        t for t, owner in script_api._timer_owners.items()
        if getattr(owner, "script_id", None) == script_id
    ]


def _template(generated: dict, name: str) -> str:
    by_name = {t["name"]: t["code"] for t in generated["templates"]}
    assert name in by_name, f"no template named {name!r}; the tests below name it"
    return by_name[name]


async def test_device_monitor_hears_the_device_events(generated, engine, subsystems):
    state, events, _ = subsystems
    assert _load(engine, "monitor", _template(generated, "Device Monitor")) == 3

    await events.emit("device.connected.acme_widget")
    assert await _settle(lambda: state.get("var.acme_widget_online") is True)

    await events.emit("device.disconnected.acme_widget")
    assert await _settle(lambda: state.get("var.acme_widget_online") is False)

    await events.emit(
        "device.error.acme_widget", {"device_id": "acme_widget", "error": "bad reply"}
    )
    await asyncio.sleep(0.05)
    assert _runtime_errors(engine) == {}


async def test_custom_event_handler_reads_the_payload(generated, engine, subsystems):
    state, events, _ = subsystems
    _load(engine, "custom", _template(generated, "Custom Event Handler"))

    await events.emit("custom.room_mode_changed", {"mode": "presentation"})
    assert await _settle(lambda: state.get("var.room_mode") == "presentation")
    assert _runtime_errors(engine) == {}


async def test_button_handler_runs_on_a_press(generated, engine, subsystems):
    _, events, _ = subsystems
    _load(engine, "button", _template(generated, "Button Handler"))

    await events.emit("ui.press.btn_acme", {"element_id": "btn_acme"})
    await asyncio.sleep(0.05)
    assert _runtime_errors(engine) == {}


@pytest.mark.parametrize("name", ["Periodic Timer", "Scheduled Task"])
async def test_timer_templates_start_their_timer_at_load_and_again_after_an_edit(
    generated, engine, name
):
    """`system.started` comes once per server start, so a timer started in its
    handler would stop at the first edit; the templates start theirs at the top."""
    _load(engine, "timer", _template(generated, name))
    first = _timers_of("timer")
    assert len(first) == 1

    edited = _template(generated, name) + "\n# edited\n"
    (engine.scripts_dir / "timer.py").write_text(edited, encoding="utf-8")
    assert engine.reload_script({"id": "timer", "file": "timer.py", "enabled": True})["status"] == "reloaded"
    after_edit = _timers_of("timer")
    assert len(after_edit) == 1 and after_edit != first
    assert _runtime_errors(engine) == {}


async def test_variable_watcher_clamps_the_volume(generated, engine, subsystems):
    state, _, _ = subsystems
    _load(engine, "watcher", _template(generated, "Variable Watcher"))

    state.set("var.volume", 95)
    assert await _settle(lambda: state.get("var.volume") == 80)
    assert _runtime_errors(engine) == {}


# --- Convert to Script -------------------------------------------------------


def test_the_harness_covers_every_step_the_engine_runs(generated):
    assert set(generated["step_actions"]) == set(BUILTIN_STEP_ACTIONS)


def test_the_harness_covers_every_trigger_the_engine_runs(generated):
    assert set(generated["triggers"]) == set(VALID_TRIGGER_TYPES)


async def test_every_step_converts_to_a_script_that_loads(generated, subsystems, tmp_path):
    """One macro per step type, alone and in each block a step can be the only
    thing in, converted and loaded."""
    state, events, devices = subsystems
    (tmp_path / "scripts").mkdir()
    refused = []
    for i, (label, source) in enumerate(generated["steps"].items()):
        se = ScriptEngine(state, events, devices, tmp_path)
        se.install()
        try:
            _load(se, f"step_{i}", source)
        except AssertionError as exc:
            refused.append(f"{label}: {exc}")
        finally:
            se.unload_all()
    assert not refused, "\n\n".join(refused)


#: The payload the event trigger's event carries.
EVENT_PAYLOAD = {"source": "hdmi1"}


async def _fire(trigger_type: str, state: StateStore, events: EventBus) -> None:
    if trigger_type == "state_change":
        state.set("var.acme_level", 75)
    elif trigger_type == "event":
        await events.emit("custom.acme_go", dict(EVENT_PAYLOAD))
    elif trigger_type == "startup":
        await events.emit("system.started")
    else:
        raise AssertionError(f"no way to fire a {trigger_type!r} trigger; add one here")


@pytest.mark.parametrize("trigger_type", sorted(VALID_TRIGGER_TYPES - {"schedule"}))
async def test_a_converted_trigger_runs_the_steps(generated, engine, subsystems, trigger_type):
    state, events, _ = subsystems
    assert _load(engine, "converted", generated["triggers"][trigger_type]) == 1

    await _fire(trigger_type, state, events)
    assert await _settle(lambda: state.get("var.acme_fired") == trigger_type)
    assert _runtime_errors(engine) == {}


def _uncomment_schedule_handler(source: str) -> str:
    """The three lines the schedule comment says to uncomment, uncommented."""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# @on_event("))
    for i in range(start, start + 3):
        assert lines[i].startswith("# "), lines[i]
        lines[i] = lines[i][2:]
    return "\n".join(lines) + "\n"


async def test_a_converted_schedule_is_a_comment_whose_handler_works(
    generated, engine, subsystems
):
    """A schedule trigger emits no event, so the script listens for nothing;
    the handler its comment offers runs once the macro emits that event."""
    state, events, _ = subsystems
    source = generated["triggers"]["schedule"]
    assert _load(engine, "scheduled", source) == 0
    assert "0 22 * * 1-5" in source

    assert _load(engine, "scheduled", _uncomment_schedule_handler(source)) == 1
    await events.emit("custom.macro_acme_schedule")
    assert await _settle(lambda: state.get("var.acme_fired") == "schedule")
    assert _runtime_errors(engine) == {}


# --- The firing context ------------------------------------------------------


def _context_keys(fields: list[str]) -> list[str]:
    return [f"var.acme_ctx_{field}" for field in fields]


async def _macro_reads(entry: dict, trigger_type: str) -> dict:
    """What the macro reads when its trigger fires through the trigger engine:
    the run the script's handler stands in for."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    macros = MacroEngine(state, events, devices)
    macros.load_macros([entry["macro"]])
    triggers = TriggerEngine(state, events, macros)
    triggers.load_triggers([entry["macro"]])
    keys = _context_keys(entry["fields"])
    await triggers.start()  # a startup trigger fires here
    try:
        if trigger_type != "startup":
            await _fire(trigger_type, state, events)
        assert await _settle(lambda: all(state.has(k) for k in keys)), trigger_type
        return {k: state.get(k) for k in keys}
    finally:
        await triggers.stop()


@pytest.mark.parametrize("trigger_type", sorted(VALID_TRIGGER_TYPES - {"schedule"}))
async def test_a_converted_trigger_hands_its_steps_what_the_macro_reads(
    generated, engine, subsystems, trigger_type
):
    """Every field the table lists for the trigger, and an event's payload,
    read through ``$trigger.<field>`` by the script's handler, holds what the
    macro reads when the trigger engine fires it."""
    entry = generated["context_reads"][trigger_type]
    row = FIRING_CONTEXTS[trigger_type]
    assert entry["fields"] == [f.name for f in row.fields] + (
        list(EVENT_PAYLOAD) if row.carries_payload else []
    )
    expected = await _macro_reads(entry, trigger_type)
    assert any(value is not None for value in expected.values()), expected

    state, events, _ = subsystems
    assert _load(engine, "context_reads", entry["script"]) == 1
    await _fire(trigger_type, state, events)
    keys = _context_keys(entry["fields"])
    assert await _settle(lambda: all(state.has(k) for k in keys))
    assert {k: state.get(k) for k in keys} == expected
    assert _runtime_errors(engine) == {}
