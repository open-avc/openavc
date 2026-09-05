"""The operator doors answer; the macro keeps running.

``POST /api/macros/{id}/execute`` and the cloud AI's ``execute_macro`` ran the
macro in the CALLER'S task, so a `wait_until` with no timeout -- a legal,
documented shape meaning "wait for the projector" -- held the request open
until the socket died, and the caller was told a macro that was running
perfectly well had failed.

Pinned here:

- both doors answer while the macro is still waiting, and say `running`
- the macro is NOT cancelled by the door answering, and finishes normally
  when its condition is finally met
- an ordinary macro still answers `executed`, so the bound did not turn every
  run into a shrug
- a macro that does not exist is still a 404 / an error, which only works
  because the refusal happens at the door rather than inside the task
- the automation doors are deliberately unchanged: a trigger, a script, a
  plugin and a panel press all still await the run
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore


@pytest.fixture
def engine():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = MagicMock()
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


def _waiting_macro(macro_id="m_wait"):
    """A macro that stops on a condition nothing has satisfied, forever."""
    return {
        "id": macro_id,
        "name": "Wait For Projector",
        "steps": [
            {
                "action": "wait_until",
                "condition": {
                    "key": "device.p1.power_state",
                    "operator": "eq",
                    "value": "on",
                },
                "timeout": None,  # documented: wait indefinitely
            },
            {"action": "state.set", "key": "var.finished", "value": True},
        ],
    }


async def _drain(engine) -> None:
    for task in list(engine._detached):
        task.cancel()
        try:
            await task
        except BaseException:
            pass


# ── The engine's own door ───────────────────────────────────────────────────

async def test_a_macro_waiting_forever_answers_running(engine):
    engine.load_macros([_waiting_macro()])
    try:
        status = await asyncio.wait_for(
            engine.execute_detached("m_wait", wait_seconds=0.05), timeout=5
        )
        assert status == "running"
        assert engine.is_macro_running("m_wait")
    finally:
        await _drain(engine)


async def test_answering_does_not_cancel_the_macro(engine):
    """The point of the fix: the caller stops waiting, the room does not."""
    engine.load_macros([_waiting_macro()])
    try:
        assert await engine.execute_detached("m_wait", wait_seconds=0.05) == "running"
        assert engine.state.get("var.finished") is None

        # The projector finally comes on, long after the request answered.
        engine.state.set("device.p1.power_state", "on")
        for _ in range(200):
            if engine.state.get("var.finished") is True:
                break
            await asyncio.sleep(0.01)
        assert engine.state.get("var.finished") is True
        assert not engine.is_macro_running("m_wait")
    finally:
        await _drain(engine)


async def test_an_ordinary_macro_still_answers_executed(engine):
    """Guard the guard: a bound that answered "running" for everything would
    satisfy the tests above and tell nobody anything."""
    engine.load_macros([{
        "id": "m_quick",
        "name": "Quick",
        "steps": [{"action": "state.set", "key": "var.done", "value": True}],
    }])
    try:
        assert await engine.execute_detached("m_quick", wait_seconds=5) == "executed"
        assert engine.state.get("var.done") is True
    finally:
        await _drain(engine)


async def test_an_unknown_macro_still_refuses_at_the_door(engine):
    """The refusal has to happen before the task: raised inside one, nobody
    would ever see it and the door would answer success for a typo."""
    engine.load_macros([])
    with pytest.raises(ValueError, match="not found"):
        await engine.execute_detached("nope")


async def test_the_detached_task_is_held_and_released(engine):
    """A task nobody references can be collected out from under itself."""
    engine.load_macros([_waiting_macro()])
    try:
        await engine.execute_detached("m_wait", wait_seconds=0.05)
        assert len(engine._detached) == 1
        engine.state.set("device.p1.power_state", "on")
        for _ in range(200):
            if not engine._detached:
                break
            await asyncio.sleep(0.01)
        assert engine._detached == set()
    finally:
        await _drain(engine)


# ── The automation doors are deliberately unchanged ─────────────────────────

async def test_execute_still_runs_in_the_callers_task(engine):
    """A trigger, a script, a plugin and a panel press have no socket to lose,
    and one of them waiting for a device is the feature. If execute() itself
    were bounded, every one of those would silently stop waiting."""
    engine.load_macros([_waiting_macro()])
    runner = asyncio.ensure_future(engine.execute("m_wait"))
    try:
        await asyncio.sleep(0.05)
        assert not runner.done(), "execute() must still wait for the condition"
        engine.state.set("device.p1.power_state", "on")
        await asyncio.wait_for(runner, timeout=5)
        assert engine.state.get("var.finished") is True
    finally:
        if not runner.done():
            runner.cancel()
            try:
                await runner
            except BaseException:
                pass


# ── The REST door ───────────────────────────────────────────────────────────

@pytest.fixture
def client(engine, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openavc.api import _engine as engine_module
    from openavc.api.routes import macros as macros_routes

    app_engine = MagicMock()
    app_engine.macros = engine
    monkeypatch.setattr(engine_module, "_engine", app_engine, raising=False)
    monkeypatch.setattr(macros_routes, "_rate_limit_test", lambda key: None)

    app = FastAPI()
    app.include_router(macros_routes.router, prefix="/api")
    return TestClient(app)


def test_the_rest_door_answers_while_the_macro_waits(client, engine, monkeypatch):
    """The measured hang: the request sat pending until cancelled from another
    connection."""
    monkeypatch.setattr(
        "openavc.core.macro_engine.OPERATOR_RUN_WAIT_SECONDS", 0.05
    )
    engine.load_macros([_waiting_macro()])
    resp = client.post("/api/macros/m_wait/execute")
    assert resp.status_code == 200
    assert resp.json() == {"status": "running", "macro_id": "m_wait"}


def test_the_rest_door_still_says_executed_for_an_ordinary_macro(client, engine):
    engine.load_macros([{
        "id": "m_quick",
        "name": "Quick",
        "steps": [{"action": "state.set", "key": "var.done", "value": True}],
    }])
    resp = client.post("/api/macros/m_quick/execute")
    assert resp.status_code == 200
    assert resp.json() == {"status": "executed", "macro_id": "m_quick"}


def test_the_rest_door_still_404s_an_unknown_macro(client, engine):
    engine.load_macros([])
    resp = client.post("/api/macros/nope/execute")
    assert resp.status_code == 404


# ── The cloud AI's door ─────────────────────────────────────────────────────

@pytest.fixture
def macro_tools(engine):
    from openavc.cloud.tools.macro_tools import MacroToolsMixin

    app_engine = MagicMock()
    app_engine.macros = engine
    tools = MacroToolsMixin.__new__(MacroToolsMixin)
    tools._get_engine = lambda: app_engine
    return tools


async def test_the_ai_door_answers_while_the_macro_waits(
    macro_tools, engine, monkeypatch
):
    """An AI turn held open on a macro waiting for a device ends in a timeout
    the model reports as a failed macro."""
    monkeypatch.setattr(
        "openavc.core.macro_engine.OPERATOR_RUN_WAIT_SECONDS", 0.05
    )
    engine.load_macros([_waiting_macro()])
    try:
        result = await asyncio.wait_for(
            macro_tools._execute_macro({"macro_id": "m_wait"}), timeout=5
        )
        assert result["status"] == "running"
        # The model has to be told this is not a failure, or it reports one.
        assert "not a failure" in result["note"]
    finally:
        await _drain(engine)


async def test_the_ai_door_still_says_executed_and_carries_no_note(
    macro_tools, engine
):
    engine.load_macros([{
        "id": "m_quick",
        "name": "Quick",
        "steps": [{"action": "state.set", "key": "var.done", "value": True}],
    }])
    result = await macro_tools._execute_macro({"macro_id": "m_quick"})
    assert result == {"status": "executed", "macro_id": "m_quick"}


async def test_the_ai_door_still_reports_an_unknown_macro(macro_tools, engine):
    engine.load_macros([])
    result = await macro_tools._execute_macro({"macro_id": "nope"})
    assert "not found" in result["error"]
