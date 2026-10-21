"""Which Programmer tab made the request that is running right now.

Every persisted edit ends in a ``project.reloaded`` broadcast to every
WebSocket client, the tab that made the edit included. That tab cannot tell
its own echo from an edit made in another session, and a device page save
while the UI Builder held unsaved work read as "Project modified externally".

The Programmer sends a per-tab id in the ``X-OpenAVC-Client`` header on every
request. ``ClientOriginMiddleware`` (openavc/middleware/client_origin.py)
holds it for the life of the request through :func:`client_origin`, and the
engine's reload broadcasts carry it as ``origin_client`` through
``reload_origin()``. A tab compares that field with its own id: a match is its
own change.

The id lasts as long as the request, not as long as the context. A task
started inside a request (``asyncio.create_task`` copies the context) shares
the request's holder, which is emptied when the request ends, so a task that
outlives it (the cloud agent's connection loop after pairing, a plugin's
background task after an enable) sees ``None`` from then on. Without that,
every later cloud-made save named the tab that paired, and that tab ignored
the assistant's edits as its own.

Outside a request (the bookkeeping worker, a plugin, a cloud push) the id is
``None`` and the broadcast carries no origin, which is the truthful answer:
nobody's tab made it. A run a tab started and is waiting on (a device's setup
wizard) says so by holding the id itself: :func:`request_client_id` inside
the request, then :func:`client_origin` around the run.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

CLIENT_HEADER = "X-OpenAVC-Client"

# Short and opaque: the Programmer sends a random id. Anything else is
# ignored rather than echoed to every client.
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class _Origin:
    """One request's (or run's) tab id; emptied when it ends."""

    __slots__ = ("client_id",)

    def __init__(self, client_id: str | None) -> None:
        self.client_id = client_id


_current: ContextVar[_Origin | None] = ContextVar(
    "openavc_client_origin", default=None,
)


def parse_client_id(value: str | None) -> str | None:
    """The header value as a client id, or None when absent or malformed."""
    if not value:
        return None
    value = value.strip()
    if not _CLIENT_ID.match(value):
        return None
    return value


@contextmanager
def client_origin(client_id: str | None) -> Iterator[None]:
    """Hold ``client_id`` as the tab making this change until the block ends,
    for this context and every task started from it."""
    origin = _Origin(client_id)
    token = _current.set(origin)
    try:
        yield
    finally:
        origin.client_id = None
        _current.reset(token)


def request_client_id() -> str | None:
    """The tab id of the request (or run) under way, or None."""
    origin = _current.get()
    return origin.client_id if origin is not None else None


def reload_origin() -> dict[str, str]:
    """The ``origin_client`` field for a ``project.reloaded`` broadcast."""
    client_id = request_client_id()
    return {"origin_client": client_id} if client_id else {}
