"""
OpenAVC programmer/admin authentication — secure by default with first-run claim.

The room panel (/panel) is always open — end users never see auth. The
Programmer IDE and the mutating/admin API require a credential. A fresh shipped
deployment ships with no credential and is *unclaimed*: the first visit to the
Programmer shows a "create admin password" screen (POST /api/auth/setup), after
which login is required. A git development checkout stays open on localhost for
frictionless dev (see `anonymous_access_allowed`). Code-writing endpoints
(python drivers, scripts) ALWAYS require a claimed credential — see
`require_claimed_auth`.

- OPENAVC_PROGRAMMER_USERNAME — HTTP Basic username for /programmer
- OPENAVC_PROGRAMMER_PASSWORD — HTTP Basic password for /programmer and protected API routes
- OPENAVC_API_KEY — alternative token-based auth via X-API-Key header
- OPENAVC_ALLOW_ANONYMOUS — force the no-credential posture: "true" serves the
  admin surface openly, "false" requires setup. Unset = auto (dev-only open).
- OPENAVC_PANEL_LOCK_CODE — reserved for future panel lock screen
"""

from __future__ import annotations

import base64
import binascii
import secrets
from functools import lru_cache

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


def is_claimed() -> bool:
    """Whether an admin credential (password or API key) has been set."""
    return bool(_get_password() or _get_api_key())


@lru_cache(maxsize=1)
def _deployment_is_dev() -> bool:
    """True only for a git development checkout. Cached — deployment type is
    immutable for the life of the process."""
    from server.updater.platform import DeploymentType, detect_deployment_type
    return detect_deployment_type() == DeploymentType.GIT_DEV


def anonymous_access_allowed() -> bool:
    """Whether an instance with NO credential serves the admin surface openly.

    - Explicit `auth.allow_anonymous` (true/false in system.json or
      OPENAVC_ALLOW_ANONYMOUS) always wins.
    - Otherwise "auto": only a git development checkout is open; every shipped
      deployment (Windows, Linux, Docker, Pi, unknown) is closed and must be
      claimed first. This is what makes shipped boxes secure by default.
    """
    raw = get_system_config().get("auth", "allow_anonymous", "auto")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
    return _deployment_is_dev()


def auth_state() -> str:
    """Resolve the instance's auth state for the SPA.

    - "required" — a credential is set; show the login screen.
    - "setup"    — no credential and anonymous not allowed (shipped, unclaimed);
                   show the first-run "create admin password" screen.
    - "ok"       — no credential and anonymous allowed (dev); skip straight in.
    """
    if is_claimed():
        return "required"
    return "ok" if anonymous_access_allowed() else "setup"


def claim_instance(password: str, username: str = "") -> None:
    """Set the initial admin credential on an unclaimed instance, and persist it.

    Raises ValueError("already_claimed") if a credential already exists, or
    ValueError("weak_password") if the password is shorter than 8 characters.
    """
    if is_claimed():
        raise ValueError("already_claimed")
    password = (password or "").strip()
    if len(password) < 8:
        raise ValueError("weak_password")
    cfg = get_system_config()
    if username and username.strip():
        cfg.set("auth", "programmer_username", username.strip())
    cfg.set("auth", "programmer_password", password)
    cfg.save()


def programmer_auth_satisfied(
    request: Request,
    credentials: HTTPBasicCredentials | None,
) -> bool:
    """Return True if the request carries valid programmer auth (or none is required).

    Checks (in order):
    1. No password / API key configured — defer to `anonymous_access_allowed()`
       (open on a dev checkout, requires setup on a shipped deployment).
    2. X-API-Key header.
    3. HTTP Basic credentials (username and password both checked when a
       username is configured; password-only when no username is set).

    Non-raising so callers that compose auth schemes (e.g. plugin routers that
    also accept a plugin token) can fall through to their own checks.
    """
    pw = _get_password()
    api_key = _get_api_key()

    if not pw and not api_key:
        return anonymous_access_allowed()

    provided_key = request.headers.get("x-api-key", "")
    if provided_key and api_key and _check_api_key(provided_key):
        return True

    if credentials and pw:
        if _check_credentials(credentials.username, credentials.password):
            return True

    return False


async def require_programmer_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    """FastAPI dependency: require programmer-level auth on protected routes.

    If no credential is configured, access follows `anonymous_access_allowed()`
    (open on a dev checkout, 401 on a shipped deployment that hasn't been
    claimed).
    """
    if programmer_auth_satisfied(request, credentials):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )


async def require_claimed_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    """FastAPI dependency for code-writing endpoints (python drivers, scripts).

    These execute uploaded code in-process, so they ALWAYS require a valid
    claimed credential — even on an instance that otherwise allows anonymous
    access. An unclaimed instance returns 403 telling the caller to set an
    admin password first; a claimed instance enforces the credential normally.
    """
    if not is_claimed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Set an admin password before creating or editing code (Settings > Security).",
        )
    if programmer_auth_satisfied(request, credentials):
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
        return anonymous_access_allowed()

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
            # Support `auth.b64.<urlsafe-b64>` for tokens with characters
            # that aren't valid in a WebSocket subprotocol token (RFC 6455
            # restricts subprotocol values to HTTP token chars).
            if token.startswith("b64."):
                try:
                    raw = token[4:]
                    raw += "=" * (-len(raw) % 4)
                    token = base64.urlsafe_b64decode(raw).decode("utf-8")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    continue
            if token:
                if pw and _check_password(token):
                    return True
                if api_key and _check_api_key(token):
                    return True

    return False


def get_ws_auth_subprotocol(headers: dict) -> str | None:
    """Extract the auth subprotocol from WebSocket headers, if present.

    Returns the exact subprotocol token the client sent (not a shortened
    form) so it can be echoed back via ws.accept(subprotocol=...). Per
    RFC 6455, the server MUST select a subprotocol from the client's list;
    returning anything else causes the browser to fail the handshake.
    """
    ws_protocols = headers.get("sec-websocket-protocol", "")
    for proto in ws_protocols.split(","):
        proto = proto.strip()
        if proto.startswith("auth."):
            return proto
    return None
