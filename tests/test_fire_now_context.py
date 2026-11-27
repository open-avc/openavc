"""Fire now hands the macro what its trigger can supply, and says what it used.

Fire now in the trigger list ran the macro with no firing context at all, so
a step reading `$trigger.new_value` read nothing and a Conditional on it took
the branch the real fire would not, while the toast said the macro completed.
It now hands what the trigger alone can supply:

* a startup or schedule trigger: the context a real fire hands, exactly;
* a State Change trigger on one key: `key`, and the key's current value as
  `new_value` when that value passes the trigger's own test; failing that, for
  Equals, the value the trigger fires on; failing that, no `new_value`, with
  the reason. `old_value` is never known;
* a pattern, and an event trigger: nothing ("Fired without event data").

The answer's `fired_with` says which, at the REST door and the AI's tool.

Every key here is invented.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import openavc.core.trigger_engine as te
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine
from openavc.core.value_resolver import FIRING_CONTEXTS

Captured = list[dict[str, Any] | None]


@pytest.fixture
def state() -> StateStore:
    store = StateStore()
    store.set_event_bus(EventBus())
    return store


def _engines(state: StateStore) -> tuple[MacroEngine, TriggerEngine, Captured]:
    """A trigger engine whose macro runs are captured rather than executed."""
    events = state._event_bus
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    macros = MacroEngine(state, events, devices)
    macros.load_macros([{"id": "m", "name": "M", "steps": []}])
    captured: Captured = []

    async def execute(_macro_id, context=None, **_kwargs):
        captured.append(context)
        return "completed"

    macros.execute = execute
    return macros, TriggerEngine(state, events, macros), captured


def _load(triggers: TriggerEngine, trigger: dict[str, Any]) -> None:
    triggers.load_triggers([{
        "id": "m", "name": "M",
        "triggers": [{"id": "t", "enabled": True, **trigger}],
    }])


async def _fire_now(state: StateStore, trigger: dict[str, Any]) -> tuple[dict | None, str]:
    """Fire now on ``trigger``: the context its macro was handed, and the
    sentence the answer says it with."""
    _macros, triggers, captured = _engines(state)
    _load(triggers, trigger)
    fired = await triggers.test_trigger("t")
    assert fired is not None
    assert fired.end.outcome == "completed"
    return captured[0], fired.fired_with


async def _real_fire(state: StateStore, trigger: dict[str, Any], monkeypatch) -> dict | None:
    """The context the same trigger hands its macro when it fires for real."""
    _macros, triggers, captured = _engines(state)
    _load(triggers, trigger)
    if trigger["type"] == "schedule":
        monkeypatch.setattr(te, "_CRON_POLL_INTERVAL", 0.005)
        monkeypatch.setattr(triggers, "_schedule_prev_due", lambda _cron, now, _last: (now, True))
    await triggers.start()
    try:
        for _ in range(2000):  # up to 10 s on a slow runner
            if captured:
                return captured[0]
            await asyncio.sleep(0.005)
        raise AssertionError("the trigger never ran the macro")
    finally:
        await triggers.stop()


# --- Startup and schedule: exactly what a real fire hands -----------------------

@pytest.mark.parametrize("trigger", [
    {"type": "startup", "delay_seconds": 0},
    {"type": "schedule", "cron": "0 18 * * 1-5"},
], ids=["startup", "schedule"])
async def test_a_startup_or_schedule_trigger_hands_what_its_real_fire_does(
    state, trigger, monkeypatch,
):
    context, _ = await _fire_now(state, trigger)
    assert context == await _real_fire(state, trigger, monkeypatch)


async def test_a_schedule_trigger_says_what_it_handed(state):
    _, fired_with = await _fire_now(state, {"type": "schedule", "cron": "0 18 * * 1-5"})
    assert fired_with == "Fired with trigger_type = schedule and cron = 0 18 * * 1-5."


async def test_a_startup_trigger_says_what_it_handed(state):
    _, fired_with = await _fire_now(state, {"type": "startup"})
    assert fired_with == "Fired with trigger_type = startup."


# --- A State Change trigger on one key ------------------------------------------

async def test_the_current_value_is_new_value_when_it_would_fire_the_trigger(state):
    state.set("var.mode", "on")
    context, fired_with = await _fire_now(state, {
        "type": "state_change", "state_key": "var.mode", "state_operator": "eq", "state_value": "on",
    })
    assert context == {"key": "var.mode", "new_value": "on"}
    assert fired_with == "Fired with new_value = on, the current value. old_value is unknown."


async def test_any_change_takes_the_current_value(state):
    """An operator left out is Changes (any value), whatever the key holds."""
    state.set("var.level", 7)
    context, _ = await _fire_now(state, {"type": "state_change", "state_key": "var.level"})
    assert context == {"key": "var.level", "new_value": 7}


async def test_equals_takes_its_own_value_when_the_current_one_would_not_fire_it(state):
    """Testing "the space becomes vacant" from an occupied space takes the
    branch the real fire takes."""
    state.set("var.occupancy", "occupied")
    context, fired_with = await _fire_now(state, {
        "type": "state_change", "state_key": "var.occupancy",
        "state_operator": "==", "state_value": "vacant",
    })
    assert context == {"key": "var.occupancy", "new_value": "vacant"}
    assert fired_with == (
        "The current value (occupied) would not fire this trigger; fired with "
        "new_value = vacant, the value it fires on. old_value is unknown."
    )


async def test_equals_reads_a_value_that_names_a_key(state):
    state.set("var.occupancy", "occupied")
    state.set("var.wanted", "vacant")
    context, _ = await _fire_now(state, {
        "type": "state_change", "state_key": "var.occupancy",
        "state_operator": "eq", "state_value": "$var.wanted",
    })
    assert context == {"key": "var.occupancy", "new_value": "vacant"}


async def test_equals_with_a_value_naming_an_empty_key_fires_without_new_value(state):
    state.set("var.occupancy", "occupied")
    context, fired_with = await _fire_now(state, {
        "type": "state_change", "state_key": "var.occupancy",
        "state_operator": "eq", "state_value": "$var.nothing_here",
    })
    assert context == {"key": "var.occupancy"}
    assert fired_with.endswith("fired without new_value. old_value is unknown.")


async def test_another_operator_fires_without_new_value_and_says_why(state):
    state.set("var.temperature", 20)
    context, fired_with = await _fire_now(state, {
        "type": "state_change", "state_key": "var.temperature",
        "state_operator": "gt", "state_value": 30,
    })
    assert context == {"key": "var.temperature"}
    assert fired_with == (
        "The current value (20) would not fire this trigger; fired without new_value. "
        "old_value is unknown."
    )


async def test_a_key_with_no_value_yet_says_so(state):
    context, fired_with = await _fire_now(state, {
        "type": "state_change", "state_key": "var.temperature",
        "state_operator": "gt", "state_value": 30,
    })
    assert context == {"key": "var.temperature"}
    assert fired_with == (
        "var.temperature has no value yet; fired without new_value. old_value is unknown."
    )


async def test_a_key_with_no_value_yet_still_takes_the_equals_value(state):
    context, _ = await _fire_now(state, {
        "type": "state_change", "state_key": "var.mode", "state_operator": "eq", "state_value": "on",
    })
    assert context == {"key": "var.mode", "new_value": "on"}


async def test_a_value_reads_as_a_sentence_writes_it(state):
    state.set("var.ready", True)
    _, fired_with = await _fire_now(state, {
        "type": "state_change", "state_key": "var.ready", "state_operator": "truthy",
    })
    assert fired_with.startswith("Fired with new_value = Yes, the current value.")


async def test_an_operator_nobody_knows_still_fires(state):
    """The real fire refuses it; Fire now bypasses the trigger's test, so it
    runs, with nothing the test could have passed."""
    state.set("var.level", 5)
    context, fired_with = await _fire_now(state, {
        "type": "state_change", "state_key": "var.level", "state_operator": "about", "state_value": 5,
    })
    assert context == {"key": "var.level"}
    assert fired_with.startswith("The current value (5) would not fire this trigger;")


async def test_old_value_is_never_handed(state):
    state.set("var.mode", "on")
    context, _ = await _fire_now(state, {"type": "state_change", "state_key": "var.mode"})
    assert "old_value" not in (context or {})


# --- Nothing to supply -----------------------------------------------------------

async def test_a_pattern_fires_without_event_data_and_says_key_is_unknown(state):
    state.set("var.zone1_level", 3)
    context, fired_with = await _fire_now(state, {"type": "state_change", "state_key": "var.zone*_level"})
    assert context == {}
    assert fired_with == (
        "Fired without event data. This trigger watches a pattern, so key, "
        "old_value and new_value are unknown."
    )


async def test_an_event_trigger_fires_without_event_data(state):
    context, fired_with = await _fire_now(state, {"type": "event", "event_pattern": "custom.source_changed"})
    assert context == {}
    assert fired_with == "Fired without event data."


@pytest.mark.parametrize("trigger", [
    {"type": "startup"},
    {"type": "schedule", "cron": "*/5 * * * *"},
    {"type": "state_change", "state_key": "var.mode"},
    {"type": "state_change", "state_key": "var.mode", "state_operator": "eq", "state_value": "off"},
    {"type": "state_change", "state_key": "var.mode", "state_operator": "ne", "state_value": "on"},
    {"type": "state_change", "state_key": "var.*"},
    {"type": "event", "event_pattern": "custom.*"},
], ids=["startup", "schedule", "any", "eq", "ne", "pattern", "event"])
async def test_fire_now_hands_only_fields_its_trigger_type_lists(state, trigger):
    """The picker and the docs list a type's fields from the table; Fire now
    never hands one the type does not."""
    state.set("var.mode", "on")
    context, _ = await _fire_now(state, trigger)
    listed = {field.name for field in FIRING_CONTEXTS[trigger["type"]].fields}
    assert set(context or {}) <= listed


# --- The macro takes the branch the real fire takes ------------------------------

async def test_a_conditional_on_new_value_takes_the_real_fires_branch(state):
    """The case the change is for: the macro branches on what fired it."""
    events = state._event_bus
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    macros = MacroEngine(state, events, devices)
    branch = {
        "action": "conditional",
        "condition": {"key": "trigger.new_value", "operator": "eq", "value": "vacant"},
        "then_steps": [{"action": "state.set", "key": "var.branch", "value": "vacant"}],
        "else_steps": [{"action": "state.set", "key": "var.branch", "value": "other"}],
    }
    macro = {"id": "m", "name": "M", "steps": [branch], "triggers": [{
        "id": "t", "type": "state_change", "enabled": True, "state_key": "var.occupancy",
        "state_operator": "eq", "state_value": "vacant",
    }]}
    macros.load_macros([macro])
    triggers = TriggerEngine(state, events, macros)
    triggers.load_triggers([macro])
    state.set("var.occupancy", "occupied")

    fired = await triggers.test_trigger("t")

    assert fired is not None and fired.end.outcome == "completed"
    assert state.get("var.branch") == "vacant"


# --- The doors that answer with it -----------------------------------------------

def test_the_rest_door_says_what_the_macro_was_handed(state, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openavc.api import _engine as engine_module
    from openavc.api.routes import macros as macros_routes

    _macros, triggers, _captured = _engines(state)
    _load(triggers, {"type": "event", "event_pattern": "custom.source_changed"})
    app_engine = MagicMock()
    app_engine.triggers = triggers
    monkeypatch.setattr(engine_module, "_engine", app_engine, raising=False)
    monkeypatch.setattr(macros_routes, "_rate_limit_test", lambda key: None)
    app = FastAPI()
    app.include_router(macros_routes.router, prefix="/api")

    resp = TestClient(app).post("/api/triggers/t/test")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "completed", "trigger_id": "t", "fired_with": "Fired without event data.",
    }


async def test_the_ai_is_told_what_the_macro_was_handed(state, monkeypatch):
    from openavc.api import _engine as engine_module
    from openavc.cloud.tools.macro_tools import MacroToolsMixin

    monkeypatch.setattr(engine_module, "_test_call_retry_after", lambda key: 0, raising=False)
    state.set("var.temperature", 20)
    _macros, triggers, _captured = _engines(state)
    _load(triggers, {
        "type": "state_change", "state_key": "var.temperature",
        "state_operator": "gt", "state_value": 30,
    })
    app_engine = MagicMock()
    app_engine.triggers = triggers
    tools = MacroToolsMixin.__new__(MacroToolsMixin)
    tools._get_engine = lambda: app_engine

    result = await tools._test_trigger({"trigger_id": "t"})

    assert result["status"] == "completed"
    assert result["fired_with"] == (
        "The current value (20) would not fire this trigger; fired without new_value. "
        "old_value is unknown."
    )
