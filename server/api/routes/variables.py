"""Variable and state REST API endpoints."""

from typing import Any

from fastapi import APIRouter

from server.api._engine import _get_engine
from server.api.errors import api_error as _api_error
from server.api.models import StateSetRequest

router = APIRouter()


def _is_flat_primitive(value: object) -> bool:
    """Check that a value is a flat primitive (str, int, float, bool, None)."""
    return value is None or isinstance(value, (str, int, float, bool))


@router.get("/state")
async def get_state() -> dict[str, Any]:
    """Full state snapshot."""
    return _get_engine().state.snapshot()


@router.get("/state/history")
async def get_state_history(count: int = 50) -> list[dict[str, Any]]:
    """Recent state change history."""
    engine = _get_engine()
    return engine.state.get_history(min(count, 10000))


@router.get("/state/{key:path}")
async def get_state_value(key: str) -> dict[str, Any]:
    """Single state value."""
    engine = _get_engine()
    value = engine.state.get(key)
    return {"key": key, "value": value}


@router.put("/state/{key:path}")
async def set_state_value(key: str, body: StateSetRequest) -> dict[str, Any]:
    """Set a state value."""
    if not _is_flat_primitive(body.value):
        raise _api_error(
            422,
            "Value must be a flat primitive (str, int, float, bool, or null)",
        )
    engine = _get_engine()
    engine.state.set(key, body.value, source="api")
    return {"key": key, "value": body.value}
