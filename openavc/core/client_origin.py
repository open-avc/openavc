"""Which Programmer tab made the request that is running right now.

Every persisted edit ends in a ``project.reloaded`` broadcast to every
WebSocket client, the tab that made the edit included. That tab cannot tell
its own echo from an edit made in another session, and a device page save
while the UI Builder held unsaved work read as "Project modified externally".

The Programmer sends a per-tab id in the ``X-OpenAVC-Client`` header on every
request. ``ClientOriginMiddleware`` (openavc/middleware/client_origin.py)
stores it in this ContextVar for the life of the request, and the engine's
reload broadcasts carry it as ``origin_client`` through ``reload_origin()``.
A tab compares that field with its own id: a match is its own change.

The var is set only inside a request. A persist that runs elsewhere (the
bookkeeping worker, a plugin, a cloud push) sees ``None`` and the broadcast
carries no origin, which is the truthful answer: nobody's tab made it.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

CLIENT_HEADER = "X-OpenAVC-Client"

# Short and opaque: the Programmer sends a random id. Anything else is
# ignored rather than echoed to every client.
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

current_client_id: ContextVar[str | None] = ContextVar(
    "openavc_client_id", default=None,
)


def parse_client_id(value: str | None) -> str | None:
    """The header value as a client id, or None when absent or malformed."""
    if not value:
        return None
    value = value.strip()
    if not _CLIENT_ID.match(value):
        return None
    return value


def reload_origin() -> dict[str, str]:
    """The ``origin_client`` field for a ``project.reloaded`` broadcast."""
    client_id = current_client_id.get()
    return {"origin_client": client_id} if client_id else {}
