"""Auth bootstrap and session REST endpoints.

Every route here is on ``open_router`` by design — this is the surface a
caller reaches *before* it has a credential: ask whether auth is required,
claim an unclaimed controller, exchange a password for a session token, and
log out. The credential checking itself lives in ``openavc/api/auth.py``;
this module is only the HTTP door onto it.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

open_router = APIRouter()


@open_router.get("/auth/required")
async def auth_required() -> dict[str, Any]:
    """Tells the SPA which auth screen to show, if any.

    The SPA can't rely on probing a protected endpoint because browsers
    auto-attach cached HTTP Basic credentials, masking whether auth is
    actually required. This explicit signal drives the SPA:

    - state "required" → show the login screen (a credential is set)
    - state "setup"    → show the first-run "create admin password" screen
                          (shipped, unclaimed)
    - state "ok"       → skip straight to the app (dev / anonymous allowed)
    """
    from openavc.api.auth import auth_state
    state = auth_state()
    return {"required": state == "required", "state": state}


@open_router.post("/auth/setup")
async def auth_setup(request: Request) -> dict[str, Any]:
    """First-run claim: set the initial admin password on an unclaimed instance.

    Open (no auth) so a fresh shipped controller can be claimed, but succeeds
    only while unclaimed — once a credential exists it returns 409 and the
    caller must log in and change it through the authenticated path.
    """
    from openavc.api.auth import auth_state, claim_instance
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        claim_instance(body.get("password", ""), body.get("username", ""))
    except ValueError as e:
        reason = str(e)
        if reason == "already_claimed":
            raise HTTPException(
                status_code=409,
                detail="This controller is already set up. Log in instead.",
            )
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters.",
        )
    return {"status": "claimed", "state": auth_state()}


@open_router.post("/auth/session")
async def create_auth_session(request: Request) -> dict[str, Any]:
    """Exchange the admin password for a short-lived session token.

    The Programmer SPA calls this at login and stores only the returned
    token — the raw password never persists in the browser. The token rides
    `Authorization: Bearer <token>` on API requests and the
    `auth.bearer.<token>` WebSocket subprotocol. Expiry is sliding
    (`expires_in` seconds since last authenticated use); tokens die on
    password change and server restart, and DELETE revokes one explicitly.

    Requires HTTP Basic against the configured password. The 401 carries no
    `WWW-Authenticate` header on purpose — this is a JSON exchange for the
    SPA's login form, and the browser's native Basic dialog must never pop.
    Open instances (no password) have no sessions to mint: 401.
    """
    from openavc.api.auth import (
        _check_credentials,
        _decode_basic_header,
        _get_password,
        credential_fingerprint,
    )
    from openavc.api.session_tokens import store

    decoded = _decode_basic_header(request.headers.get("authorization", ""))
    if _get_password() and decoded is not None:
        user, password = decoded
        if _check_credentials(user, password):
            token, expires_in = store.issue(credential_fingerprint())
            return {"token": token, "expires_in": expires_in}
    raise HTTPException(status_code=401, detail="Wrong username or password.")


@open_router.delete("/auth/session")
async def delete_auth_session(request: Request) -> dict[str, Any]:
    """Log out: revoke the session token presented as a Bearer credential.

    Idempotent — an unknown or already-expired token still returns 200, so a
    logout can't fail visibly after the server restarted or the token aged
    out. Open (no auth beyond the token itself): revoking is only ever
    destructive to the caller's own session.
    """
    from openavc.api.auth import _extract_bearer
    from openavc.api.session_tokens import store

    token = _extract_bearer(request.headers.get("authorization", ""))
    revoked = store.revoke(token) if token else False
    return {"status": "revoked", "revoked": revoked}
