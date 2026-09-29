"""Reads ``X-OpenAVC-Client`` into ``core.client_origin.current_client_id``.

Pure ASGI rather than BaseHTTPMiddleware so the ContextVar is set in the same
task the route handler runs in; the value is reset when the request ends.
"""

from __future__ import annotations

from openavc.core.client_origin import (
    CLIENT_HEADER,
    current_client_id,
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
        token = current_client_id.set(client_id)
        try:
            await self.app(scope, receive, send)
        finally:
            current_client_id.reset(token)
