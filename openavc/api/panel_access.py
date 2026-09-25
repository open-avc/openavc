"""Panel access: who may open this system's panel, decided in one place.

The panel page and its WebSocket used to be open to anything that could reach
the port. This module is the gate. A panel-type connection is admitted when
any of these holds:

1. **Panel access is ``open``** (``panels.access`` in ``system.json``).
2. **A credential was presented and accepted**, or the instance allows
   anonymous access (a dev checkout, or an explicit ``allow_anonymous``). This
   covers the Programmer's own preview of a page, an integration with an API
   key, and every test that runs anonymous.
3. **The device's own screen**: loopback peer, not tunnelled, not forwarded
   when a proxy is trusted. Exactly ``is_local_console_request``.
4. **A cloud tunnel**: ``is_tunneled_request``. The cloud decided who may open
   it.
5. **An approved panel cookie**: ``openavc_panel_<instance id>`` holding
   ``<device id>.<secret>``, checked against ``core/panel_devices.py``.

The reason recorded on the socket is the most specific one (``admit`` says
why), so a later switch to approved-only closes exactly the sockets that were
in because the mode was open.

Otherwise the connection is refused: the socket is accepted and then closed
with 4010, and the page shows the waiting screen. The check-in route
(``GET /api/panel/access``) is what a panel page calls first; it answers with
the mode, the device's status and, for a new device, the code the Programmer
will show. It is a GET because a panel-scoped cloud tunnel refuses every other
method, and through a tunnel the answer is always approved and nothing is
created. Cookies are set only here and on the claim route, and never for a
tunnelled request, so nothing is ever set on the cloud's origin.

The Programmer's side is the protected ``/api/panel/devices`` family (list,
approve, deny, revoke, rename) and one push, ``panel.devices.changed``, sent
to programmer clients only: every server push otherwise reaches panels too,
and a pending request's details are not theirs to see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBasicCredentials
from starlette.requests import HTTPConnection

from openavc.api._engine import _get_engine, get_engine_optional
from openavc.api.auth import (
    _basic,
    _get_username,
    anonymous_access_allowed,
    is_claimed,
    programmer_auth_satisfied,
)
from openavc.api.models import (
    PanelClaimRequest,
    PanelDeviceApproveRequest,
    PanelDeviceRenameRequest,
)
from openavc.core.panel_devices import (
    ACCESS_OPEN,
    STATUS_APPROVED,
    STATUS_PENDING,
    CheckIn,
    PanelDevice,
    PanelDeviceStore,
    access_mode,
    cookie_value,
)
from openavc.utils.logger import get_logger
from openavc.utils.request_origin import (
    is_local_console_request,
    is_tunneled_request,
    peer_address,
)

log = get_logger(__name__)

router = APIRouter()
open_router = APIRouter()

COOKIE_PREFIX = "openavc_panel_"

PUSH_TYPE = "panel.devices.changed"

CLAIM_UNCLAIMED = (
    "This system has not been set up yet. Open the Programmer and create the "
    "admin password first."
)
CLAIM_WRONG_PASSWORD = "That password is not correct."
NO_SUCH_PANEL = "No panel with that id."


@dataclass(frozen=True)
class Admission:
    """How a panel connection got in, or that it did not."""

    admitted: bool
    kind: str = ""  # open | credential | console | tunnel | device
    device_id: str | None = None


REFUSED = Admission(False)


def cookie_name(instance_id: str) -> str:
    """One cookie per instance, because several instances can share a host on
    different ports and browsers scope cookies by host, not port."""
    return COOKIE_PREFIX + "".join(ch for ch in instance_id if ch.isalnum())


def _store(engine: Any) -> PanelDeviceStore | None:
    store = getattr(engine, "panel_devices", None)
    return store if isinstance(store, PanelDeviceStore) else None


def _admitted_without_a_record(conn: HTTPConnection, authenticated: bool) -> Admission | None:
    """Rules 2 to 4: the ways in that need neither the mode nor a record."""
    if authenticated:
        return Admission(True, "credential")
    if is_local_console_request(conn):
        return Admission(True, "console")
    if is_tunneled_request(conn):
        return Admission(True, "tunnel")
    return None


def _approved_device(conn: HTTPConnection, engine: Any) -> PanelDevice | None:
    """Rule 5: the approved device this connection's cookie names, if any."""
    engine = engine if engine is not None else get_engine_optional()
    if engine is None:
        return None
    store = _store(engine)
    if store is None:
        return None
    return store.verify(conn.cookies.get(cookie_name(engine.instance_id)))


def admit(conn: HTTPConnection, *, authenticated: bool, engine: Any = None) -> Admission:
    """The whole rule, for a ``Request`` or a ``WebSocket``.

    The verdict is "any rule holds". The *kind* recorded is the most specific
    reason, with the open mode last: a socket that a credential, the console,
    a tunnel or an approved cookie would admit anyway is remembered by that
    reason, so switching the mode to approved closes only the sockets that
    were in because the mode was open. The cost of asking the cookie first in
    open mode is one dictionary lookup and one salted SHA-256.

    ``authenticated`` is the caller's own credential check (``check_ws_auth``
    for a socket, ``programmer_auth_satisfied`` for a request), because the
    two doors read a credential differently and this module does not repeat
    either.
    """
    early = _admitted_without_a_record(conn, authenticated)
    if early is not None:
        return early
    device = _approved_device(conn, engine)
    if device is not None:
        return Admission(True, "device", device.id)
    if access_mode() == ACCESS_OPEN:
        return Admission(True, "open")
    return REFUSED


def _set_cookie(
    response: Response, engine: Any, value: str, max_age: int, *, secure: bool
) -> None:
    response.set_cookie(
        cookie_name(engine.instance_id),
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )


async def push_change(engine: Any, reason: str, device: PanelDevice | None = None) -> None:
    """Tell every Programmer client, and no panel, that the list changed."""
    message: dict[str, Any] = {"type": PUSH_TYPE, "reason": reason}
    if device is not None:
        message["device"] = device.public()
    await engine.ws.broadcast(message, client_type="programmer")


async def _sweep(engine: Any, store: PanelDeviceStore) -> None:
    for device in store.expire():
        await push_change(engine, "expired", device)


def _space_name(engine: Any) -> str:
    project = getattr(engine, "project", None)
    try:
        return str(project.project.name) if project is not None else ""
    except AttributeError:
        return ""


def _approver(request: Request, credentials: HTTPBasicCredentials | None) -> str:
    if is_tunneled_request(request):
        return "cloud"
    if credentials is not None and credentials.username.strip():
        return credentials.username.strip()
    return _get_username() or "admin"


def _require_store(engine: Any) -> PanelDeviceStore:
    store = _store(engine)
    if store is None:
        raise HTTPException(status_code=503, detail="Panel devices are not available yet")
    return store


# --- The panel's own doors (open router) ---


@open_router.get("/panel/access")
async def panel_access_check_in(
    request: Request,
    response: Response,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict[str, Any]:
    """A panel page's first request: may this device connect, and if not, why.

    Answers ``{"access": "open"}`` when anyone may connect; otherwise the
    device's status. A device seen for the first time gets a pending record,
    a code and a pending cookie; an approved device gets its cookie re-issued
    when it is a day old; a device the programmer just approved collects its
    real cookie here. Open tier: a waiting panel polls this every 3 seconds.
    """
    engine = _get_engine()
    if access_mode() == ACCESS_OPEN:
        return {"access": "open"}
    authenticated = programmer_auth_satisfied(request, credentials)
    if _admitted_without_a_record(request, authenticated) is not None:
        return {"access": "approved", "status": STATUS_APPROVED, "name": ""}

    store = _require_store(engine)
    await _sweep(engine, store)
    result: CheckIn = store.check_in(
        request.cookies.get(cookie_name(engine.instance_id)),
        address=peer_address(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    if result.set_cookie and result.device is not None:
        _set_cookie(
            response, engine, result.set_cookie, result.cookie_max_age,
            secure=request.url.scheme == "https",
        )
    if result.created and result.device is not None:
        log.info(
            "Panel waiting for approval: code %s, %s at %s",
            result.device.code, result.device.platform, result.device.address,
        )
        await push_change(engine, "pending", result.device)

    body: dict[str, Any] = {"access": "approved", "status": result.status}
    if result.status == STATUS_APPROVED and result.device is not None:
        body["name"] = result.device.name
    elif result.status == STATUS_PENDING and result.device is not None:
        body["code"] = result.device.code
        body["space"] = _space_name(engine)
    return body


@open_router.post("/panel/access/claim")
async def panel_access_claim(
    request: Request,
    response: Response,
    body: PanelClaimRequest | None = None,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict[str, Any]:
    """Approve this device from the panel itself, with the admin password.

    The password travels once, as HTTP Basic. A wrong one is a 401 with no
    ``WWW-Authenticate`` header, so a browser shows the page's own message and
    never its sign-in dialog; the 401 feeds the brute-force counter and the
    route sits on the strict rate tier. The approved cookie is set on this
    very response, so the page connects at once.
    """
    engine = _get_engine()
    if not programmer_auth_satisfied(request, credentials):
        if not is_claimed() and not anonymous_access_allowed():
            raise HTTPException(status_code=409, detail=CLAIM_UNCLAIMED)
        raise HTTPException(status_code=401, detail=CLAIM_WRONG_PASSWORD)
    if access_mode() == ACCESS_OPEN:
        return {"access": "open"}

    store = _require_store(engine)
    await _sweep(engine, store)
    name = body.name if body is not None else None
    presented = request.cookies.get(cookie_name(engine.instance_id))
    result = store.check_in(
        presented,
        address=peer_address(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    if result.status == "unavailable":
        return {"access": "approved", "status": "unavailable"}
    device = result.device
    assert device is not None
    if device.status != STATUS_APPROVED:
        device, secret = store.approve(device.id, name, "admin password on the panel")
        store.mark_delivered(device.id)
        value = cookie_value(device.id, secret)
        log.info(
            "Panel approved with the admin password: %s (%s at %s)",
            device.name, device.platform, device.address,
        )
        await push_change(engine, "approved", device)
    elif result.set_cookie:
        value = result.set_cookie
    else:
        value = presented or ""
    if value:
        from openavc.core.panel_devices import COOKIE_MAX_AGE_SECONDS

        _set_cookie(
            response, engine, value, COOKIE_MAX_AGE_SECONDS,
            secure=request.url.scheme == "https",
        )
    return {"access": "approved", "status": STATUS_APPROVED, "name": device.name}


# --- The Programmer's doors (protected router) ---


@router.get("/panel/devices")
async def list_panel_devices() -> dict[str, Any]:
    """Every panel this system knows: waiting, approved and denied."""
    engine = _get_engine()
    store = _require_store(engine)
    await _sweep(engine, store)
    return {"access": access_mode(), **store.list_devices()}


@router.post("/panel/devices/{device_id}/approve")
async def approve_panel_device(
    device_id: str,
    request: Request,
    body: PanelDeviceApproveRequest | None = None,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict[str, Any]:
    engine = _get_engine()
    store = _require_store(engine)
    name = body.name if body is not None else None
    try:
        device, _secret = store.approve(device_id, name, _approver(request, credentials))
    except KeyError:
        raise HTTPException(status_code=404, detail=NO_SUCH_PANEL)
    log.info(
        "Panel approved: %s (%s at %s) by %s",
        device.name, device.platform, device.address, device.approved_by,
    )
    await push_change(engine, "approved", device)
    return device.public()


@router.post("/panel/devices/{device_id}/deny")
async def deny_panel_device(device_id: str) -> dict[str, Any]:
    engine = _get_engine()
    store = _require_store(engine)
    try:
        device = store.deny(device_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=NO_SUCH_PANEL)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    log.info("Panel denied: code %s (%s at %s)", device.code, device.platform, device.address)
    await push_change(engine, "denied", device)
    return device.public()


@router.delete("/panel/devices/{device_id}")
async def revoke_panel_device(device_id: str) -> dict[str, Any]:
    """Revoke: the record is gone and its live sockets close with 4010. The
    device's next check-in makes a fresh pending record with a new code."""
    engine = _get_engine()
    store = _require_store(engine)
    try:
        device = store.revoke(device_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=NO_SUCH_PANEL)
    closed = await engine.ws.close_panel_device(device.id)
    log.info(
        "Panel revoked: %s (%s at %s), %d socket(s) closed",
        device.name or device.code, device.platform, device.address, closed,
    )
    await push_change(engine, "revoked", device)
    return {"status": "revoked", "panel_id": device.id}


@router.patch("/panel/devices/{device_id}")
async def rename_panel_device(device_id: str, body: PanelDeviceRenameRequest) -> dict[str, Any]:
    engine = _get_engine()
    store = _require_store(engine)
    try:
        device = store.rename(device_id, body.name)
    except KeyError:
        raise HTTPException(status_code=404, detail=NO_SUCH_PANEL)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    log.info("Panel renamed: %s (%s at %s)", device.name, device.platform, device.address)
    await push_change(engine, "renamed", device)
    return device.public()
