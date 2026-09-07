"""What a macro run tells the person who asked for it.

Every diagnostic this needs already existed and every one of them stopped at
the log. A step that failed emitted `macro.step_error` with a genuinely good
sentence, the run then reached `log.info("… completed")`, and the operator
door answered `{"status": "executed"}` — the same answer a clean run gives.
A cancelled run did the same: `macro.cancelled` went out and `execute()` fell
through, so `POST /cancel` on a hung macro and a `cancel_group` preemption
both left the original request reporting success.

So the engine knew, the event bus knew, and the one caller holding a socket
open waiting for the answer was told nothing. These pin the outcome the
engine reports, at the two doors that report it:

* `execute_steps` counts failures even when it carries on — with the default
  `stop_on_error: false` a failed step is caught, logged and stepped over,
  which is the whole reason the run used to look clean.
* `execute` turns that into one word, and keeps swallowing. Isolation is the
  point of the outer catch — a trigger, a panel press or a script must not
  take a macro's failure as its own — so the outcome is REPORTED, not raised.
  Every existing caller that ignores the return value is unaffected.

`skipped` is here because a run that never started is an outcome somebody
asked for too, and it already had an event nobody could see either.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncio

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore


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


#: A step that genuinely fails, with nothing stubbed: the macro it calls does
#: not exist. `device.command` would not do -- the fixture mocks send_command,
#: so every device answers and the step succeeds.
BAD_STEP = {"action": "macro", "macro": "ghost"}


def _boom(macro_id: str = "m", **extra) -> dict:
    """A macro whose one step cannot run."""
    return {"id": macro_id, "name": macro_id, "steps": [BAD_STEP], **extra}


# ---------------------------------------------------------------------------
# The three ways a run ends badly, all of which used to read as success
# ---------------------------------------------------------------------------


async def test_a_clean_run_reports_completed(macros):
    macros.load_macros([{
        "id": "ok", "name": "Ok",
        "steps": [{"action": "state.set", "key": "var.x", "value": 1}],
    }])

    assert await macros.execute("ok") == "completed"


async def test_a_failed_step_reports_failed_even_though_the_run_carried_on(macros):
    # stop_on_error is false by default, so the step is caught and the macro
    # runs to the end. That is deliberate and unchanged -- the run really did
    # complete. What was missing is that it completed having failed.
    macros.load_macros([_boom()])

    assert await macros.execute("m") == "failed"


async def test_the_later_steps_still_run(macros, core):
    # Pinning the half that must NOT change: reporting the failure must not
    # turn stop_on_error false into stop_on_error true. Without this a build
    # that simply halted on the first failure would pass the test above.
    state, _ = core
    macros.load_macros([{
        "id": "m", "name": "m",
        "steps": [
            BAD_STEP,
            {"action": "state.set", "key": "var.reached", "value": True},
        ],
    }])

    assert await macros.execute("m") == "failed"
    assert state.get("var.reached") is True


async def test_stop_on_error_also_reports_failed(macros, core):
    state, _ = core
    macros.load_macros([{
        "id": "m", "name": "m", "stop_on_error": True,
        "steps": [
            BAD_STEP,
            {"action": "state.set", "key": "var.reached", "value": True},
        ],
    }])

    # Both error modes answered "executed"; the outer catch-all swallowed the
    # raise the same way it swallowed nothing at all.
    assert await macros.execute("m") == "failed"
    assert state.get("var.reached") is None


async def test_a_cancelled_run_reports_cancelled(macros):
    macros.load_macros([{
        "id": "slow", "name": "slow",
        "steps": [{"action": "delay", "seconds": 30}],
    }])

    task = asyncio.create_task(macros.execute("slow"))
    await asyncio.sleep(0.05)
    assert await macros.cancel("slow") is True

    # Cancelled is its own answer, not a kind of failure and certainly not a
    # kind of success: the steps after the cancel point never ran, so a caller
    # that treats it as completed is wrong about the room.
    assert await task == "cancelled"


async def test_a_run_that_never_started_reports_skipped(macros):
    # overlap=skip with one already running. It emitted macro.skipped and
    # returned, so the door could not tell it from a run that did everything.
    macros.load_macros([{
        "id": "once", "name": "once", "overlap": "skip",
        "steps": [{"action": "delay", "seconds": 30}],
    }])
    first = asyncio.create_task(macros.execute("once"))
    await asyncio.sleep(0.05)

    assert await macros.execute("once") == "skipped"

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


# ---------------------------------------------------------------------------
# The nesting cap, which is the same defect one level down
# ---------------------------------------------------------------------------


async def test_a_blocked_nested_call_fails_the_macro_that_called_it(macros):
    # The guard itself is good and its sentence is good -- "blocked --
    # circular/recursive call detected". What was wrong is that every macro up
    # the chain then reported completed, so hitting it was invisible outside
    # the raw log.
    macros.load_macros([{
        "id": "self", "name": "self",
        "steps": [{"action": "macro", "macro": "self"}],
    }])

    assert await macros.execute("self") == "failed"


async def test_the_depth_cap_is_reported_the_same_way(macros):
    # A chain longer than _max_depth. The innermost call raises, the parent
    # step catches it, and the outermost run has to say so.
    depth = macros._max_depth + 3
    macros.load_macros([
        {"id": f"c{i}", "name": f"c{i}",
         "steps": [{"action": "macro", "macro": f"c{i + 1}"}]}
        for i in range(depth)
    ] + [{"id": f"c{depth}", "name": "last",
          "steps": [{"action": "state.set", "key": "var.deep", "value": 1}]}])

    assert await macros.execute("c0") == "failed"


# ---------------------------------------------------------------------------
# The operator door carries it through
# ---------------------------------------------------------------------------


async def test_the_operator_door_reports_the_outcome(macros):
    macros.load_macros([_boom()])

    assert await macros.execute_detached("m") == "failed"


async def test_the_operator_door_still_says_running_for_a_long_macro(macros):
    # Unchanged, and pinned here because the outcome now comes off the task:
    # a macro still going when the wait expires is neither failed nor
    # completed, and reading its result would have blocked the caller for as
    # long as the macro runs -- the exact bug execute_detached exists to fix.
    macros.load_macros([{
        "id": "slow", "name": "slow",
        "steps": [{"action": "delay", "seconds": 30}],
    }])

    assert await macros.execute_detached("slow", wait_seconds=0.05) == "running"

    await macros.cancel("slow")


async def test_a_missing_macro_still_raises_rather_than_reporting(macros):
    # The 404 has to keep happening at the door: "there is no such macro" is
    # a refusal of the request, not an outcome of a run.
    with pytest.raises(ValueError, match="not found"):
        await macros.execute_detached("ghost")
