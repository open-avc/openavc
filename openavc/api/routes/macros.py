"""Macro and trigger REST API endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException

from openavc.api._engine import _get_engine, _rate_limit_test
from openavc.api.errors import api_error as _api_error

router = APIRouter()


# --- Macros ---


@router.post("/macros/{macro_id}/execute")
async def execute_macro(macro_id: str) -> dict[str, Any]:
    """Execute a macro by ID."""
    # Same runaway guard as /triggers/{id}/test: the only callers are the
    # IDE's manual "run this macro" buttons, so debounce rapid re-firing of
    # the same macro (runtime automation never uses this endpoint).
    #
    # The cloud AI's run_macro tool shares this bucket by key — it's the same
    # "a person asked for this macro, twice" case. The WebSocket
    # `macro.execute` deliberately does NOT: that one carries panel button
    # presses, where a throttle would make a real room stop responding. So the
    # asymmetry is the policy, not drift — this guard belongs on the operator
    # doors, not on the macro engine where it would catch panels and triggers.
    _rate_limit_test(f"macro_execute:{macro_id}")
    engine = _get_engine()
    try:
        await engine.macros.execute(macro_id)
    except ValueError as e:
        raise _api_error(404, str(e))
    except Exception as e:
        # The traceback goes to the log (which the IDE streams live) rather than
        # into the toast — a failing step's exception is rarely a sentence.
        raise _api_error(500, f"Macro '{macro_id}' failed to run — see the log for details.", exc=e)
    return {"status": "executed", "macro_id": macro_id}


@router.post("/macros/{macro_id}/cancel")
async def cancel_macro(macro_id: str) -> dict[str, Any]:
    """Cancel a running macro by ID."""
    engine = _get_engine()
    cancelled = await engine.macros.cancel(macro_id)
    if cancelled:
        return {"status": "cancelled", "macro_id": macro_id}
    return {"status": "not_running", "macro_id": macro_id}


# --- Triggers ---


@router.get("/triggers")
async def list_triggers() -> dict[str, Any]:
    """List all triggers with status."""
    engine = _get_engine()
    return {"triggers": engine.triggers.list_triggers()}


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(trigger_id: str) -> dict[str, Any]:
    """Fire a trigger's macro immediately, bypassing conditions."""
    _rate_limit_test(f"test_trigger:{trigger_id}")
    engine = _get_engine()
    ok = await engine.triggers.test_trigger(trigger_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    return {"status": "fired", "trigger_id": trigger_id}
