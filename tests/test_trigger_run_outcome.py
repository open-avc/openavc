"""What a trigger says about the macro it just fired.

A trigger that errors on every fire looked exactly like a healthy one. The
only runtime tell `/api/triggers` carried was `last_fired`, and that is set
before the macro runs and never reads back what happened — so a trigger whose
macro fails every night shows a FRESH timestamp, which reads as working. The
`Fire Now` button answered `{"status": "fired"}` for a macro whose every step
failed, and the AI's `test_trigger` tool said the same.

`execute()` reports how a run ended, so a trigger has something to record.
These pin that it does, at the three doors a person can reach it through:

* `_fire_macro` — automation. It keeps its catch-all, because isolating a
  macro's failure from the trigger pipeline is the design; what it stops
  doing is throwing the answer away.
* `test_trigger` — `Fire Now` in the IDE and the cloud AI's tool. It stays
  side-effect-free on the stored record, deliberately: a manual test must not
  overwrite last night's real failure. The person who pressed the button gets
  the answer in the response instead.
* `list_triggers` — what `/api/triggers` and the AI's `list_triggers` show.

The rebuild in `load_triggers` calls itself loss-free and the reconcile runs
it on any device, connection, variable, plugin or macro edit, so the record
has to survive one — otherwise saving anything erases the evidence.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine


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


#: A step that genuinely fails with nothing stubbed: the macro it calls does
#: not exist. `device.command` would not do — the fixture answers every
#: device, so the step would succeed.
BAD_STEP = {"action": "macro", "macro": "ghost"}
GOOD_STEP = {"action": "state.set", "key": "var.ran", "value": True}


def _macro(macro_id: str, *steps, **extra) -> dict:
    return {"id": macro_id, "name": macro_id, "steps": list(steps), **extra}


def _with_trigger(macro: dict, trigger_id: str = "trg") -> dict:
    return {
        **macro,
        "triggers": [{"id": trigger_id, "type": "schedule", "enabled": True}],
    }


async def _fire(triggers, trigger_id: str = "trg") -> None:
    """Fire through the automation path, the way a schedule or state change
    reaches it — not through `test_trigger`, which is the operator door."""
    await triggers._fire_macro(triggers._triggers[trigger_id], {})


def _status(triggers, trigger_id: str = "trg") -> dict:
    return next(
        t for t in triggers.list_triggers() if t["id"] == trigger_id
    )


# ---------------------------------------------------------------------------
# The defect: every way a fire goes wrong used to read as a healthy trigger
# ---------------------------------------------------------------------------

async def test_a_trigger_whose_macro_failed_says_so(triggers, macros):
    """The finding, exactly: a trigger that errors on every fire showed as
    enabled and healthy."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    await _fire(triggers)

    assert _status(triggers)["last_outcome"] == "failed"


async def test_a_trigger_whose_macro_is_gone_says_so(triggers, macros):
    """`execute()` RAISES for a macro that does not exist — a trigger left
    pointing at a deleted macro. The catch-all logged it and moved on."""
    macros.load_macros([])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    await _fire(triggers)

    status = _status(triggers)
    assert status["last_outcome"] == "error"
    assert "not found" in (status["last_error"] or "")


async def test_a_trigger_whose_macro_was_cancelled_is_not_called_failed(
    triggers, macros, core
):
    """Cancelled is not the same as failed — a cancel_group preemption is
    ordinary operation, and calling it a failure would cry wolf."""
    state, _ = core
    macros.load_macros([_macro(
        "m",
        {"action": "wait_until",
         "condition": {"key": "var.never", "operator": "eq", "value": True},
         "timeout": None},
    )])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    fire = asyncio.create_task(_fire(triggers))
    await asyncio.sleep(0.05)
    await macros.cancel("m")
    await fire

    assert _status(triggers)["last_outcome"] == "cancelled"


async def test_a_clean_fire_reports_clean_and_carries_no_error(triggers, macros, core):
    """The negative control. A rule that only ever says `failed` would pass
    every test above and be worse than what it replaced."""
    state, _ = core
    macros.load_macros([_macro("m", GOOD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    await _fire(triggers)

    status = _status(triggers)
    assert status["last_outcome"] == "completed"
    assert status["last_error"] is None
    assert state.get("var.ran") is True


async def test_a_trigger_that_has_never_fired_claims_nothing(triggers, macros):
    """Never fired is not the same as fired and fine."""
    macros.load_macros([_macro("m", GOOD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    status = _status(triggers)
    assert status["last_outcome"] is None
    assert status["last_error"] is None
    assert status["last_fired"] is None


async def test_last_fired_still_moves_on_a_failed_fire(triggers, macros):
    """Pinning what must NOT change. `last_fired` is the cooldown baseline as
    well as a display value; withholding it on a failure would make a broken
    trigger fire straight through its own cooldown."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    await _fire(triggers)

    assert _status(triggers)["last_fired"] is not None


async def test_a_later_clean_fire_clears_the_error(triggers, macros):
    """It is the LAST run, not a sticky fault: a trigger somebody fixed has to
    be able to come back."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])
    await _fire(triggers)
    assert _status(triggers)["last_outcome"] == "failed"

    macros.load_macros([_macro("m", GOOD_STEP)])
    await _fire(triggers)

    status = _status(triggers)
    assert status["last_outcome"] == "completed"
    assert status["last_error"] is None


# ---------------------------------------------------------------------------
# The event, so the IDE learns without polling
# ---------------------------------------------------------------------------

async def test_the_fire_announces_how_it_went(triggers, macros, core):
    _, events = core
    seen: list[dict] = []
    events.on("trigger.completed", lambda e, p: seen.append(p))
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    await _fire(triggers)

    assert len(seen) == 1
    assert seen[0]["trigger_id"] == "trg"
    assert seen[0]["macro_id"] == "m"
    assert seen[0]["outcome"] == "failed"


async def test_a_clean_fire_announces_too(triggers, macros, core):
    """Not an error channel — the card has to be able to go back to healthy
    without a page reload."""
    _, events = core
    seen: list[dict] = []
    events.on("trigger.completed", lambda e, p: seen.append(p))
    macros.load_macros([_macro("m", GOOD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    await _fire(triggers)

    assert [p["outcome"] for p in seen] == ["completed"]


# ---------------------------------------------------------------------------
# Surviving the rebuild
# ---------------------------------------------------------------------------

async def test_the_record_survives_a_project_edit(triggers, macros):
    """`load_triggers` runs on any device, connection, variable, plugin or
    macro edit and calls itself loss-free. A record wiped by saving is a
    record nobody debugging can rely on."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])
    await _fire(triggers)

    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    assert _status(triggers)["last_outcome"] == "failed"


async def test_a_deleted_trigger_leaves_nothing_behind(triggers, macros):
    """The other half of surviving a rebuild: a record for an id nobody has
    any more is a leak, and a re-used id would inherit a stranger's failure."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])
    await _fire(triggers)

    triggers.load_triggers([_macro("m", BAD_STEP)])          # trigger removed
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])  # re-added

    assert _status(triggers)["last_outcome"] is None


# ---------------------------------------------------------------------------
# Fire Now — the operator door
# ---------------------------------------------------------------------------

async def test_fire_now_reports_the_outcome(triggers, macros):
    """It answered `fired` for a macro whose every step failed."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    assert await triggers.test_trigger("trg") == "failed"


async def test_fire_now_reports_a_clean_run_too(triggers, macros):
    macros.load_macros([_macro("m", GOOD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    assert await triggers.test_trigger("trg") == "completed"


async def test_fire_now_still_refuses_an_unknown_trigger(triggers, macros):
    """Pinning what must NOT change: the door answers 404 off this."""
    macros.load_macros([_macro("m", GOOD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    assert await triggers.test_trigger("nope") is None


async def test_fire_now_still_refuses_when_the_macro_is_gone(triggers, macros):
    """Also unchanged: a trigger pointing at a deleted macro is a 404 at this
    door, even though the same case is `error` down the automation path —
    there, nobody is holding a socket to be refused."""
    macros.load_macros([])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    assert await triggers.test_trigger("trg") is None


async def test_fire_now_does_not_overwrite_the_stored_record(triggers, macros):
    """The manual test stays side-effect-free, which is why the button reports
    in its own response. Otherwise testing a trigger in the morning would
    erase the failure you opened the IDE to look at."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])
    await _fire(triggers)
    assert _status(triggers)["last_outcome"] == "failed"

    macros.load_macros([_macro("m", GOOD_STEP)])
    assert await triggers.test_trigger("trg") == "completed"

    assert _status(triggers)["last_outcome"] == "failed"
    assert _status(triggers)["last_fired"] is not None


async def test_fire_now_announces_itself_as_a_test(triggers, macros, core):
    """`trigger.fired` already marks a manual fire `test`; its answer has to
    carry the same mark or the card would record it as a real run."""
    _, events = core
    seen: list[dict] = []
    events.on("trigger.completed", lambda e, p: seen.append(p))
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    await triggers.test_trigger("trg")

    assert [p["trigger_type"] for p in seen] == ["test"]
    assert seen[0]["outcome"] == "failed"


async def test_fire_now_answers_while_a_waiting_macro_runs(
    triggers, macros, monkeypatch
):
    """Fire Now is an operator door with a socket to lose, and it awaited the
    run inline: a `wait_until` with no timeout — documented as legal, and the
    projector-warmup case it exists for — held the request until the socket
    died."""
    monkeypatch.setattr(
        "openavc.core.macro_engine.OPERATOR_RUN_WAIT_SECONDS", 0.05
    )
    macros.load_macros([_macro(
        "m",
        {"action": "wait_until",
         "condition": {"key": "var.never", "operator": "eq", "value": True},
         "timeout": None},
    )])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    # Bounded so the door failing to answer is a FAILURE and not a hung test —
    # which is precisely what it did before: it awaited the run inline.
    assert await asyncio.wait_for(triggers.test_trigger("trg"), timeout=5) == "running"

    for task in list(macros._detached):
        task.cancel()
        try:
            await task
        except BaseException:
            pass


# ---------------------------------------------------------------------------
# The doors that report it
# ---------------------------------------------------------------------------

@pytest.fixture
def client(triggers, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openavc.api import _engine as engine_module
    from openavc.api.routes import macros as macros_routes

    app_engine = MagicMock()
    app_engine.triggers = triggers
    monkeypatch.setattr(engine_module, "_engine", app_engine, raising=False)
    monkeypatch.setattr(macros_routes, "_rate_limit_test", lambda key: None)

    app = FastAPI()
    app.include_router(macros_routes.router, prefix="/api")
    return TestClient(app)


def test_the_list_door_carries_the_record(client, triggers, macros):
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    resp = client.get("/api/triggers")

    assert resp.status_code == 200
    entry = resp.json()["triggers"][0]
    assert "last_outcome" in entry
    assert "last_error" in entry


def test_the_fire_now_door_answers_the_outcome(client, triggers, macros):
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    resp = client.post("/api/triggers/trg/test")

    assert resp.status_code == 200
    assert resp.json() == {"status": "failed", "trigger_id": "trg"}


def test_the_fire_now_door_still_404s_an_unknown_trigger(client, triggers, macros):
    macros.load_macros([])
    triggers.load_triggers([])

    resp = client.post("/api/triggers/nope/test")

    assert resp.status_code == 404


@pytest.fixture
def trigger_tools(triggers, monkeypatch):
    from openavc.api import _engine as engine_module
    from openavc.cloud.tools.macro_tools import MacroToolsMixin

    monkeypatch.setattr(
        engine_module, "_test_call_retry_after", lambda key: 0, raising=False
    )
    app_engine = MagicMock()
    app_engine.triggers = triggers
    tools = MacroToolsMixin.__new__(MacroToolsMixin)
    tools._get_engine = lambda: app_engine
    return tools


async def test_the_ai_is_told_a_test_fire_failed(trigger_tools, triggers, macros):
    """It was told `fired` and would report that to the integrator as a
    success."""
    macros.load_macros([_macro("m", BAD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", BAD_STEP))])

    result = await trigger_tools._test_trigger({"trigger_id": "trg"})

    assert result["status"] == "failed"
    assert "note" in result


async def test_the_ai_gets_no_note_for_a_clean_test_fire(
    trigger_tools, triggers, macros
):
    """The negative control: a note on every answer is a note nobody reads."""
    macros.load_macros([_macro("m", GOOD_STEP)])
    triggers.load_triggers([_with_trigger(_macro("m", GOOD_STEP))])

    result = await trigger_tools._test_trigger({"trigger_id": "trg"})

    assert result["status"] == "completed"
    assert "note" not in result


async def test_the_ai_still_reports_an_unknown_trigger(trigger_tools, triggers):
    triggers.load_triggers([])

    result = await trigger_tools._test_trigger({"trigger_id": "nope"})

    assert "error" in result
