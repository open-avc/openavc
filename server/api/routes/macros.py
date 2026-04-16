"""Macro and trigger REST API endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException

from server.api._engine import _get_engine, _rate_limit_test
from server.api.errors import api_error as _api_error

router = APIRouter()


# --- Macros ---


@router.post("/macros/{macro_id}/execute")
async def execute_macro(macro_id: str) -> dict[str, Any]:
    """Execute a macro by ID."""
    engine = _get_engine()
    try:
        await engine.macros.execute(macro_id)
    except ValueError as e:
        raise _api_error(404, str(e))
    except Exception as e:
        raise _api_error(500, f"Macro execution failed: {e}", exc=e)
    return {"status": "executed", "macro_id": macro_id}


@router.post("/macros/{macro_id}/cancel")
async def cancel_macro(macro_id: str) -> dict[str, Any]:
    """Cancel a running macro by ID."""
    engine = _get_engine()
    cancelled = await engine.macros.cancel(macro_id)
    if cancelled:
        return {"cancelled": True}
    return {"cancelled": False, "reason": "not_running"}


# --- Triggers ---


@router.get("/triggers")
async def list_triggers() -> list[dict[str, Any]]:
    """List all triggers with status."""
    engine = _get_engine()
    return engine.triggers.list_triggers()


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(trigger_id: str) -> dict[str, Any]:
    """Fire a trigger's macro immediately, bypassing conditions."""
    _rate_limit_test(f"test_trigger:{trigger_id}")
    engine = _get_engine()
    ok = await engine.triggers.test_trigger(trigger_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    return {"status": "fired", "trigger_id": trigger_id}
