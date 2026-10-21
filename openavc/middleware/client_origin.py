"""Reads ``X-OpenAVC-Client`` into ``core.client_origin`` for the request.

Pure ASGI rather than BaseHTTPMiddleware so the id is set in the same task
the route handler runs in; it is emptied when the request ends, for any task
the request started too (core/client_origin.py).
"""

from __future__ import annotations

from openavc.core.client_origin import (
    CLIENT_HEADER,
    client_origin,
    parse_client_id,
)

_HEADER_KEY = CLIENT_HEADER.lower().encode("latin-1")


class ClientOriginMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = next(
            (value for key, value in scope.get("headers", []) if key == _HEADER_KEY),
            None,
        )
        client_id = parse_client_id(
            raw.decode("latin-1") if raw is not None else None,
        )
        with client_origin(client_id):
            await self.app(scope, receive, send)
