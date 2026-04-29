"""
OpenAVC optional authentication.

All auth is opt-in: if no password / API key is configured, everything is open.

- OPENAVC_PROGRAMMER_USERNAME — HTTP Basic username for /programmer
- OPENAVC_PROGRAMMER_PASSWORD — HTTP Basic password for /programmer and protected API routes
- OPENAVC_API_KEY — alternative token-based auth via X-API-Key header
- OPENAVC_PANEL_LOCK_CODE — reserved for future panel lock screen
"""

from __future__ import annotations

import base64
import binascii
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from server.system_config import get_system_config

_basic = HTTPBasic(auto_error=False)


def _get_username() -> str:
    return get_system_config().get("auth", "programmer_username", "")


def _get_password() -> str:
    return get_system_config().get("auth", "programmer_password", "")


def _get_api_key() -> str:
    return get_system_config().get("auth", "api_key", "")


def _check_password(provided: str) -> bool:
    """Timing-safe comparison against the configured programmer password."""
    return secrets.compare_digest(provided, _get_password())


def _check_username(provided: str) -> bool:
    """Timing-safe comparison against the configured programmer username.

    Returns True when no username is configured (legacy single-credential mode).
    Then the caller must still verify the password.
    """
    expected = _get_username()
    if not expected:
        return True
    return secrets.compare_digest(provided, expected)


def _check_api_key(provided: str) -> bool:
    """Timing-safe comparison against the configured API key."""
    return secrets.compare_digest(provided, _get_api_key())


def _check_credentials(provided_user: str, provided_pass: str) -> bool:
    """Verify a username/password pair against the configured credentials.

    Always evaluates both checks (no short-circuit) so the running time
    doesn't leak which field was wrong.
    """
    user_ok = _check_username(provided_user)
    pass_ok = _check_password(provided_pass)
    return user_ok and pass_ok


async def require_programmer_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    """FastAPI dependency: require programmer-level auth on protected routes.

    Checks (in order):
    1. X-API-Key header
    2. HTTP Basic credentials (username and password both checked when a
       username is configured; password-only when no username is set)

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
        if _check_credentials(credentials.username, credentials.password):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )


def _decode_basic_header(header_value: str) -> tuple[str, str] | None:
    """Decode an `Authorization: Basic <base64>` header value to (user, pass).

    Returns None if the header isn't Basic auth or is malformed. Used by
    `check_ws_auth` so a browser's cached HTTP Basic credentials authenticate
    the WebSocket handshake automatically.
    """
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(parts[1], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ":" not in decoded:
        return None
    user, password = decoded.split(":", 1)
    return user, password


def check_ws_auth(query_params: dict, headers: dict) -> bool:
    """Check WebSocket authentication from headers or subprotocol.

    Checks (in priority order):
    1. X-API-Key header (best for programmatic access)
    2. Authorization: Basic header (browsers send this when cached HTTP Basic
       credentials exist for the origin — keeps the Programmer IDE WebSocket
       authenticated without separate wiring)
    3. Sec-WebSocket-Protocol subprotocol prefixed with "auth." (browser-safe
       channel for clients that can't set arbitrary headers; treated as a
       password OR an API key)

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

    auth_header = headers.get("authorization", "")
    decoded = _decode_basic_header(auth_header)
    if decoded is not None and pw:
        user, password = decoded
        if _check_credentials(user, password):
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
