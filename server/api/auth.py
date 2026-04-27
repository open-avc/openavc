"""
OpenAVC optional authentication.

All auth is opt-in: if no password / API key is configured, everything is open.

- OPENAVC_PROGRAMMER_PASSWORD — HTTP Basic password for /programmer and protected API routes
- OPENAVC_API_KEY — alternative token-based auth via X-API-Key header
- OPENAVC_PANEL_LOCK_CODE — reserved for future panel lock screen
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from server.system_config import get_system_config

_basic = HTTPBasic(auto_error=False)


def _get_password() -> str:
    return get_system_config().get("auth", "programmer_password", "")


def _get_api_key() -> str:
    return get_system_config().get("auth", "api_key", "")


def _check_password(provided: str) -> bool:
    """Timing-safe comparison against the configured programmer password."""
    return secrets.compare_digest(provided, _get_password())


def _check_api_key(provided: str) -> bool:
    """Timing-safe comparison against the configured API key."""
    return secrets.compare_digest(provided, _get_api_key())


async def require_programmer_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    """FastAPI dependency: require programmer-level auth on protected routes.

    Checks (in order):
    1. X-API-Key header
    2. HTTP Basic credentials (username ignored, password checked)

    If neither PROGRAMMER_PASSWORD nor API_KEY is configured, access is open.
    """
    pw = _get_password()
    api_key = _get_api_key()

    if not pw and not api_key:
        return

    provided_key = request.headers.get("x-api-key", "")
    if provided_key and api_key and _check_api_key(provided_key):
        return

    if credentials and pw:
        if _check_password(credentials.password):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )


def check_ws_auth(query_params: dict, headers: dict) -> bool:
    """Check WebSocket authentication from headers or subprotocol.

    Checks (in priority order):
    1. X-API-Key header (best for programmatic access)
    2. Sec-WebSocket-Protocol subprotocol prefixed with "auth." (browser-safe)

    Returns True if auth passes or is not required.
    """
    pw = _get_password()
    api_key = _get_api_key()

    if not pw and not api_key:
        return True

    provided_key = headers.get("x-api-key", "")
    if provided_key:
        if api_key and _check_api_key(provided_key):
            return True

    ws_protocols = headers.get("sec-websocket-protocol", "")
    for proto in ws_protocols.split(","):
        proto = proto.strip()
        if proto.startswith("auth."):
            token = proto[5:]
            if token:
                if pw and _check_password(token):
                    return True
                if api_key and _check_api_key(token):
                    return True

    return False


def get_ws_auth_subprotocol(headers: dict) -> str | None:
    """Extract the auth subprotocol from WebSocket headers, if present.

    Must be echoed back in ws.accept(subprotocol=...) for the browser
    to keep the connection open.
    """
    ws_protocols = headers.get("sec-websocket-protocol", "")
    for proto in ws_protocols.split(","):
        proto = proto.strip()
        if proto.startswith("auth."):
            return "auth"
    return None
