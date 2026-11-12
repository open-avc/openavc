"""Secret command parameters saved on this system, for the Programmer's box.

The Macro editor and the UI Builder's action editor never put a typed stream
key or PIN in the project: their box sends it here on its own, and the step
holds the marker that comes back (core/saved_params.py). A saved value is
never read back, only whether one is saved under a marker's name.
"""

from typing import Any

from fastapi import APIRouter

from openavc.api._engine import _get_engine
from openavc.api.errors import api_error
from openavc.api.host_policy import ProgrammerRoute
from openavc.api.models import SavedParamRequest
from openavc.core import saved_params
from openavc.core.device_logins import LoginFileUnreadable

router = APIRouter(route_class=ProgrammerRoute)


@router.post("/saved-params")
async def save_param(body: SavedParamRequest) -> dict[str, Any]:
    """Save a secret command parameter; answers the marker the step holds."""
    if saved_params.is_any_marker(body.value):
        raise api_error(400, "Enter the value itself.")
    try:
        marker = _get_engine().save_param_value(body.value)
    except LoginFileUnreadable as exc:
        raise api_error(409, f"It could not be saved on this system. {exc}", exc) from exc
    return {"marker": marker}


@router.get("/saved-params/{name}")
async def saved_param_state(name: str) -> dict[str, Any]:
    """Whether a value is saved on this system under a marker's name."""
    if not saved_params.is_marker(saved_params.marker(name)):
        raise api_error(404, "No saved value has that name.")
    return {"saved": _get_engine().saved_param_value(name) is not None}
