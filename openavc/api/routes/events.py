"""The event bus's REST door for an outside system.

``POST /api/events`` emits one event. It exists for the integration that holds
no WebSocket -- a calendar service's webhook, a building system posting
occupancy -- and it answers the same policy as the socket's ``event.emit``
(``core/event_bus.check_event_emit``): ``custom.*`` only, and a JSON object for
a payload. Authenticated, like every REST mutation; the unauthenticated door is
the panel socket.

The emit is fire-and-forget and the reply is a receipt (202): a script handler
awaiting on the far side would otherwise hold the HTTP response open for as
long as it runs, the same head-of-line problem ``macro.execute`` solves the
same way.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Body

from openavc.api._engine import _get_engine
from openavc.api.errors import api_error as _api_error
from openavc.core.event_bus import check_event_emit
from openavc.utils.logger import get_logger

router = APIRouter()
log = get_logger(__name__)

# Strong refs for the fire-and-forget emits -- asyncio only weakly references
# tasks, so an unreferenced one can be GC'd before its handlers run.
_bg_tasks: set[asyncio.Task] = set()


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"Event emit failed: {exc}", exc_info=exc)


@router.post("/events", status_code=202)
async def emit_event(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Emit a ``custom.*`` event onto the bus.

    Body: ``{"event": "custom.<name>", "payload": {...}}``. The payload is
    handed to handlers verbatim -- an ``event`` trigger reads it as
    ``$trigger.<field>``, a script as ``event.payload``.
    """
    event = body.get("event")
    payload = body.get("payload")
    reason = check_event_emit(event, payload)
    if reason:
        raise _api_error(422, reason)
    engine = _get_engine()
    task = asyncio.create_task(engine.events.emit(event, payload or {}))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    task.add_done_callback(_log_task_exception)
    return {"status": "emitted", "event": event}
