"""Finite old-browser setup handoff, using the canonical prepared-result owner.

A cookie routes an unstarted input only. Current authentication, the original
exact grant, browser origin/API path and final response admission remain required.
No setup effect or result retrieval is authorized by this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from openavc.api.auth import authenticated_programmer_grant
from openavc.api.host_policy import bind_response_availability, request_admission
from openavc.core.setup_actions import SetupContractRequired
from openavc.core.setup_results import SetupCapacityError, SetupConflict, SetupUnavailable
from openavc.drivers.base import CommandParamError
from openavc.utils.request_origin import is_tunneled_request

_PREFIX = "openavc_setup_"
_NAME = re.compile(r"openavc_setup_[0-9a-f]{32}\Z")
_VALUE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_PROGRAMMER_PATH = re.compile(r"(?:/[A-Za-z0-9_-]+){0,8}/programmer/?\Z")
_MAX_COOKIES = 8


@dataclass(frozen=True)
class BrowserLocation:
    origin: str
    api_path: str
    secure: bool


def browser_location(request: Request) -> BrowserLocation:
    """Referer chooses cookie routing, never authentication or a host grant."""
    try:
        origin = urlsplit(request.headers.get("origin", ""))
        referer = urlsplit(request.headers.get("referer", ""))
        if (origin.scheme not in ("http", "https") or not origin.hostname
                or origin.username or origin.password or origin.path or origin.query or origin.fragment
                or origin.port == 0 or len(origin.netloc) > 300
                or (referer.scheme, referer.netloc) != (origin.scheme, origin.netloc)
                or referer.fragment or not _PROGRAMMER_PATH.fullmatch(referer.path)):
            raise ValueError
        # A trusted tunnel rewrites Host to loopback but retains the browser's
        # Origin/Referer. Its independently authenticated grant still owns access.
        if not is_tunneled_request(request) and (
                request.url.scheme, request.url.netloc) != (origin.scheme, origin.netloc):
            raise ValueError
        prefix = referer.path.rstrip("/").removesuffix("/programmer")
        return BrowserLocation(f"{origin.scheme}://{origin.netloc}", prefix + "/api", origin.scheme == "https")
    except ValueError:
        raise HTTPException(409, "Refresh this page before starting setup. No action was started and this input was not retained.",
                            headers={"Cache-Control": "no-store"}) from None


def transfer_cookies(request: Request) -> list[tuple[str, str]]:
    candidates = []
    for header in request.headers.getlist("cookie"):
        for field in header.split(";"):
            name, separator, value = field.strip().partition("=")
            if name.startswith(_PREFIX):
                candidates.append((name, value if separator else ""))
    if (len(candidates) > _MAX_COOKIES or sum(len(k) + len(v) for k, v in candidates) > 2048
            or len({k for k, _ in candidates}) != len(candidates)
            or any(not _NAME.fullmatch(k) or not _VALUE.fullmatch(v) for k, v in candidates)):
        raise HTTPException(409, "The retained setup cookies are invalid. No action was started.",
                            headers={"Cache-Control": "no-store"})
    return candidates


def active_transfer(request, runner, location, current):
    """Return at most one authorized draft; never silently truncate candidates."""
    active, stale = [], []
    for name, ticket in transfer_cookies(request):
        try:
            item, inputs = runner.results.recover_transfer(ticket, origin=location.origin,
                api_path=location.api_path, current=current)
        except SetupUnavailable:
            stale.append(name)
            continue
        if name != _PREFIX + item.run_id:
            raise HTTPException(409, "The retained setup cookie does not match this attempt.")
        active.append((name, ticket, item, inputs))
    if len(active) > 1:
        raise HTTPException(409, "More than one setup input is waiting. No action was started.")
    return (active[0] if active else None), stale


def expire_cookie(response, name, location):
    # Only one exact name, never a newer run's cookie. One Set-Cookie header also
    # survives the existing tunnel's single-valued response header transport.
    response.delete_cookie(name, path=location.api_path, secure=location.secure,
                           httponly=True, samesite="strict")


async def retain_legacy_setup(request, runner, device_id, action_id, params):
    location = browser_location(request)
    grant = await authenticated_programmer_grant(request)
    lease = request_admission(request)
    provider, snapshot, category = lease.policy, lease.snapshot, lease.category

    def original():
        return grant() is True and snapshot.allows(category) and provider.snapshot() == snapshot

    active, stale = active_transfer(request, runner, location, lease.revalidate)
    if active is None and len(transfer_cookies(request)) == _MAX_COOKIES:
        response = JSONResponse({"detail": "Too many old setup attempts are retained. Refresh this page before trying again. This input was not retained."}, status_code=409)
        if stale:
            expire_cookie(response, stale[0], location)
        response.headers["Cache-Control"] = "no-store"
        return response
    try:
        prepared = runner.prepare_transfer(device_id, action_id, params, origin=location.origin,
            api_path=location.api_path, original_grant=original, current=lease.revalidate,
            ticket=active[1] if active else None)
    except (SetupConflict, SetupCapacityError, SetupContractRequired, CommandParamError, SetupUnavailable) as exc:
        if isinstance(exc, SetupContractRequired):
            detail = "This installed driver needs a qualified setup result contract. No action was started."
        elif isinstance(exc, CommandParamError):
            detail = "Check the setup inputs. No action was started and this input was not retained."
        else:
            detail = "A previous setup input is waiting or unavailable. Refresh this page before trying again. This input was not retained."
        raise HTTPException(409, detail, headers={"Cache-Control": "no-store"}) from None
    guard = runner.results.transfer_guard(prepared["ticket"], origin=location.origin, api_path=location.api_path)
    bind_response_availability(request, guard)
    response = JSONResponse({"code": "setup_input_retained", "detail": "Setup has not started. Refresh this page to continue with your retained inputs."}, status_code=409)
    response.headers["Cache-Control"] = "no-store"
    if active is None:
        response.set_cookie(_PREFIX + prepared["run_id"], prepared["ticket"], max_age=300,
                            path=location.api_path, secure=location.secure, httponly=True, samesite="strict")
    return response
