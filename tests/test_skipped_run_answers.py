"""A run the macro refused answers its door with why, in the reason table's words.

The operator doors (the IDE's Test button, a trigger's Fire now, and the AI's
``execute_macro`` and ``test_trigger``) answered ``skipped`` alone, and each
reader guessed the cause in its own words ("its own Overlap or Cooldown
setting"), which was wrong for a queue that timed out or a system stopping.
Each now answers with the ``code`` and ``message`` its ``macro.skipped`` event
carries, from ``macro_text.SKIP_REASONS``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_text import skip_reason
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine

COOLDOWN = 600
STEP = {"action": "state.set", "key": "var.ran", "value": True}
REFUSED = skip_reason("macro_cooldown", seconds=COOLDOWN)


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def macros(core):
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


@pytest.fixture
def triggers(core, macros):
    state, events = core
    return TriggerEngine(state, events, macros)


def _macro() -> dict:
    return {
        "id": "m",
        "name": "Lights",
        "cooldown_seconds": COOLDOWN,
        "steps": [STEP],
        "triggers": [{"id": "trg", "type": "schedule", "enabled": True}],
    }


async def _in_cooldown(macros, triggers) -> None:
    """Load the macro and start it once, so its next start is refused."""
    macros.load_macros([_macro()])
    triggers.load_triggers([_macro()])
    assert await macros.execute("m") == "completed"


@pytest.fixture
def heard(core):
    _state, events = core
    payloads: list[dict] = []
    events.on("macro.skipped.*", lambda _e, p: payloads.append(p))
    return payloads


# ── The engine ──────────────────────────────────────────────────────────────

async def test_a_detached_run_that_was_refused_says_why(macros, triggers, heard):
    await _in_cooldown(macros, triggers)

    end = await macros.execute_detached("m")

    assert end.outcome == "skipped"
    assert end.reason == REFUSED
    # The same words the event carried, not a second wording of them.
    assert {k: heard[-1][k] for k in ("code", "message")} == end.reason


async def test_a_run_that_started_carries_no_reason(macros, triggers):
    macros.load_macros([_macro()])

    end = await macros.execute_detached("m")

    assert end.outcome == "completed"
    assert end.reason is None


async def test_fire_now_says_why_the_macro_refused(macros, triggers):
    await _in_cooldown(macros, triggers)

    end = await triggers.test_trigger("trg")

    assert end.outcome == "skipped"
    assert end.reason == REFUSED


async def test_execute_still_reports_the_word_alone(macros, triggers):
    """A script's macros.execute and a plugin's macro_execute are documented
    to return the word."""
    await _in_cooldown(macros, triggers)

    assert await macros.execute("m") == "skipped"


# ── The REST doors ──────────────────────────────────────────────────────────

@pytest.fixture
def client(macros, triggers, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openavc.api import _engine as engine_module
    from openavc.api.routes import macros as macros_routes

    app_engine = MagicMock()
    app_engine.macros = macros
    app_engine.triggers = triggers
    monkeypatch.setattr(engine_module, "_engine", app_engine, raising=False)
    monkeypatch.setattr(macros_routes, "_rate_limit_test", lambda key: None)

    app = FastAPI()
    app.include_router(macros_routes.router, prefix="/api")
    return TestClient(app)


async def test_the_test_button_door_answers_the_reason(client, macros, triggers):
    await _in_cooldown(macros, triggers)

    resp = client.post("/api/macros/m/execute")

    assert resp.status_code == 200
    assert resp.json() == {"status": "skipped", "macro_id": "m", **REFUSED}


async def test_the_fire_now_door_answers_the_reason(client, macros, triggers):
    await _in_cooldown(macros, triggers)

    resp = client.post("/api/triggers/trg/test")

    assert resp.status_code == 200
    assert resp.json() == {"status": "skipped", "trigger_id": "trg", **REFUSED}


async def test_a_run_that_started_answers_no_reason(client, macros, triggers):
    """The negative control: a reason only on a run that did not start."""
    macros.load_macros([_macro()])

    resp = client.post("/api/macros/m/execute")

    assert resp.json() == {"status": "completed", "macro_id": "m"}


# ── The AI's doors ──────────────────────────────────────────────────────────

@pytest.fixture
def ai(macros, triggers, monkeypatch):
    from openavc.api import _engine as engine_module
    from openavc.cloud.tools.macro_tools import MacroToolsMixin

    monkeypatch.setattr(
        engine_module, "_test_call_retry_after", lambda key: 0, raising=False
    )
    app_engine = MagicMock()
    app_engine.macros = macros
    app_engine.triggers = triggers
    tools = MacroToolsMixin.__new__(MacroToolsMixin)
    tools._get_engine = lambda: app_engine
    return tools


async def test_the_ai_is_told_why_a_run_did_not_start(ai, macros, triggers):
    await _in_cooldown(macros, triggers)

    result = await ai._execute_macro({"macro_id": "m"})

    assert result["status"] == "skipped"
    assert result["code"] == "macro_cooldown"
    # The note names the table's reason, not a guess of its own.
    assert REFUSED["message"] in result["note"]
    assert "nothing was sent to any device" in result["note"]


async def test_the_ai_is_told_why_a_test_fire_did_not_start(ai, macros, triggers):
    await _in_cooldown(macros, triggers)

    result = await ai._test_trigger({"trigger_id": "trg"})

    assert result["status"] == "skipped"
    assert result["code"] == "macro_cooldown"
    assert REFUSED["message"] in result["note"]
