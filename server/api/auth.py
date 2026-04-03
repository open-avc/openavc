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

from server import config

_basic = HTTPBasic(auto_error=False)


def _check_password(provided: str) -> bool:
    """Timing-safe comparison against the configured programmer password."""
    return secrets.compare_digest(provided, config.PROGRAMMER_PASSWORD)


def _check_api_key(provided: str) -> bool:
    """Timing-safe comparison against the configured API key."""
    return secrets.compare_digest(provided, config.API_KEY)


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
    # No auth configured — fully open
    if not config.PROGRAMMER_PASSWORD and not config.API_KEY:
        return

    # Check X-API-Key header
    api_key = request.headers.get("x-api-key", "")
    if api_key and config.API_KEY and _check_api_key(api_key):
        return

    # Check HTTP Basic
    if credentials and config.PROGRAMMER_PASSWORD:
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
    # No auth configured — open
    if not config.PROGRAMMER_PASSWORD and not config.API_KEY:
        return True

    # Check X-API-Key header
    api_key = headers.get("x-api-key", "")
    if api_key:
        if config.API_KEY and _check_api_key(api_key):
            return True

    # Check Sec-WebSocket-Protocol header for "auth.<token>" subprotocol.
    # Usage: new WebSocket(url, ["auth.MY_PASSWORD"])
    ws_protocols = headers.get("sec-websocket-protocol", "")
    for proto in ws_protocols.split(","):
        proto = proto.strip()
        if proto.startswith("auth."):
            token = proto[5:]  # strip "auth." prefix
            if token:
                if config.PROGRAMMER_PASSWORD and _check_password(token):
                    return True
                if config.API_KEY and _check_api_key(token):
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
            return proto
    return None
