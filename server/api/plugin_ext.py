"""Plugin-registered HTTP routers and their authentication.

A plugin calls ``api.register_router(router)`` during ``start()``; the engine
mounts that router under ``/api/plugins/{plugin_id}/ext/*`` after the plugin
starts and removes it on stop.

Routes are guarded by :func:`require_plugin_access`, which passes when **any**
of these hold:

1. Auth is not configured (open instance) — matches the rest of the platform.
2. Normal programmer auth succeeds (HTTP Basic / X-API-Key) — used by the
   Programmer IDE and server-side callers.
3. A valid, plugin-scoped token is presented — used by the plugin's sandboxed
   panel iframe, which (confirmed cross-browser) cannot attach programmer
   credentials to its own ``fetch`` but can attach a token the platform injects
   into ``openavc:init``.

The token is a stateless HMAC over ``plugin_id`` + expiry, keyed by an
in-memory per-process secret (so tokens naturally invalidate on restart and
nothing sensitive lands on disk). It is scoped to one ``plugin_id`` so a token
minted for plugin A can't reach plugin B's routes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from server.api import auth as _auth
from server.api.auth import programmer_auth_satisfied
from server.utils.logger import get_logger

log = get_logger(__name__)

# Header (preferred) and query-param (fallback for contexts that can't set
# headers) the iframe uses to present its plugin token.
PLUGIN_TOKEN_HEADER = "x-openavc-plugin-token"
PLUGIN_TOKEN_QUERY = "_plugin_token"

_DEFAULT_TTL_SECONDS = 12 * 3600
# Per-process secret: regenerated each start, never written to disk. Panels
# re-fetch a token whenever they (re)init a plugin iframe, so rotation on
# restart is transparent.
_SECRET = secrets.token_bytes(32)

_basic = HTTPBasic(auto_error=False)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def auth_required() -> bool:
    """True when the instance has any auth configured (password or API key)."""
    return bool(_auth._get_password() or _auth._get_api_key())


def mint_plugin_token(plugin_id: str, ttl: int = _DEFAULT_TTL_SECONDS) -> tuple[str, int]:
    """Mint a plugin-scoped token. Returns (token, expires_at_unix)."""
    expires_at = int(time.time()) + ttl
    msg = f"{plugin_id}:{expires_at}".encode("utf-8")
    sig = hmac.new(_SECRET, msg, hashlib.sha256).digest()
    return f"{_b64(msg)}.{_b64(sig)}", expires_at


def verify_plugin_token(token: str, plugin_id: str) -> bool:
    """Validate a token's signature, scope (plugin_id), and expiry."""
    try:
        msg_b64, sig_b64 = token.split(".", 1)
        msg = _unb64(msg_b64)
        sig = _unb64(sig_b64)
    except (ValueError, TypeError):  # binascii.Error subclasses ValueError
        return False
    expected = hmac.new(_SECRET, msg, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        token_id, exp_str = msg.decode("utf-8").rsplit(":", 1)
        expires_at = int(exp_str)
    except (ValueError, UnicodeDecodeError):
        return False
    if token_id != plugin_id:
        return False
    return expires_at >= int(time.time())


def require_plugin_access(plugin_id: str):
    """Build a FastAPI dependency guarding one plugin's ``/ext/*`` routes."""

    async def _dependency(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(_basic),
    ) -> None:
        if programmer_auth_satisfied(request, credentials):
            return
        token = request.headers.get(PLUGIN_TOKEN_HEADER) or request.query_params.get(
            PLUGIN_TOKEN_QUERY, ""
        )
        if token and verify_plugin_token(token, plugin_id):
            return
        # No WWW-Authenticate challenge: the only browser-usable auth here is the
        # injected token, not HTTP Basic, so advertising a Basic challenge would
        # only make the browser pop its native dialog inside an unauthenticated
        # panel's plugin iframe. A plain 401 is handled in JS.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return _dependency


def _ext_prefix(plugin_id: str) -> str:
    return f"/api/plugins/{plugin_id}/ext"


def mount_plugin_router(app, plugin_id: str, router) -> None:
    """Mount a plugin's APIRouter under ``/api/plugins/{id}/ext/*`` (idempotent)."""
    prefix = _ext_prefix(plugin_id)
    # Drop any previously-mounted routes for this plugin first so a
    # stop→start (e.g. config change) doesn't leave stale duplicates.
    unmount_plugin_router(app, plugin_id)
    app.include_router(
        router,
        prefix=prefix,
        dependencies=[Depends(require_plugin_access(plugin_id))],
    )
    log.info("Mounted plugin '%s' HTTP router at %s/*", plugin_id, prefix)


def unmount_plugin_router(app, plugin_id: str) -> None:
    """Remove all routes previously mounted under this plugin's ``/ext`` prefix."""
    _unmount_prefix(app, plugin_id, _ext_prefix(plugin_id))


def _guest_prefix(plugin_id: str) -> str:
    return f"/api/plugins/{plugin_id}/guest"


def mount_plugin_guest_router(app, plugin_id: str, router) -> None:
    """Mount a plugin's guest APIRouter under ``/api/plugins/{id}/guest/*``.

    Idempotent, like :func:`mount_plugin_router`. Deliberately mounted with
    **no** auth dependency: guest routes serve devices that have no OpenAVC
    login (a guest laptop, an unattended receiver box), the same posture as
    the platform's own open routes (``/pair``, ``/setup``). The plugin gates
    these routes itself; registration requires the ``guest_endpoints``
    capability, checked in ``PluginAPI.register_guest_router``.
    """
    prefix = _guest_prefix(plugin_id)
    unmount_plugin_guest_router(app, plugin_id)
    app.include_router(router, prefix=prefix)
    log.info("Mounted plugin '%s' guest HTTP router at %s/*", plugin_id, prefix)


def unmount_plugin_guest_router(app, plugin_id: str) -> None:
    """Remove all routes previously mounted under this plugin's ``/guest`` prefix."""
    _unmount_prefix(app, plugin_id, _guest_prefix(plugin_id))


def _unmount_prefix(app, plugin_id: str, prefix: str) -> None:
    routes = app.router.routes
    keep = [r for r in routes if not getattr(r, "path", "").startswith(prefix)]
    removed = len(routes) - len(keep)
    if removed:
        routes[:] = keep
        log.info("Unmounted %d route(s) for plugin '%s' at %s/*", removed, plugin_id, prefix)
