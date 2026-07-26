"""Variable and state REST API endpoints."""

from typing import Any

from fastapi import APIRouter

from server.api._engine import _get_engine
from server.api.errors import api_error as _api_error
from server.api.models import StateSetRequest
from server.core.state_store import check_state_write

router = APIRouter()


@router.get("/state")
async def get_state() -> dict[str, Any]:
    """Full state snapshot."""
    return {"state": _get_engine().state.snapshot()}


@router.get("/state/history")
async def get_state_history(count: int = 50) -> dict[str, Any]:
    """Recent state change history."""
    engine = _get_engine()
    return {"history": engine.state.get_history(min(count, 10000))}


@router.get("/state/{key:path}")
async def get_state_value(key: str) -> dict[str, Any]:
    """Single state value."""
    engine = _get_engine()
    value = engine.state.get(key)
    return {"key": key, "value": value}


@router.put("/state/{key:path}")
async def set_state_value(key: str, body: StateSetRequest) -> dict[str, Any]:
    """Set a state value."""
    # Shared write policy — same verdict the WebSocket and the cloud AI tools get.
    reason = check_state_write(key, body.value)
    if reason:
        raise _api_error(422, reason)
    engine = _get_engine()
    engine.state.set(key, body.value, source="api")
    return {"key": key, "value": body.value}
