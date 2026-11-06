"""Inter-System Communication (ISC) REST endpoints.

The request/response half of ISC: peer status, the discovered-instance list,
and the three outbound sends (event to one peer, broadcast to all, device
command with a reply). The peer-to-peer WebSocket that carries the traffic is
``openavc/api/isc_ws.py``; the mesh itself is ``openavc/core/isc.py``.

Every route returns/raises rather than assuming ISC is on — ``engine.isc`` is
None whenever the feature is disabled in system config.
"""

from typing import Any

from openavc.api.host_policy import ProgrammerRoute
from fastapi import APIRouter, HTTPException

from openavc.api._engine import _get_engine
from openavc.api.errors import api_error as _api_error
from openavc.api.models import (
    ISCAuthKeyRequest, ISCBroadcastRequest, ISCCommandRequest, ISCSendRequest,
)
from openavc.core.device_logins import SAVED_LOGIN, isc_key, saved_isc_key
from openavc.core.isc import ISCRemoteError

router = APIRouter(route_class=ProgrammerRoute)


def _why_isc_is_off(engine) -> dict[str, Any]:
    """Which of the two switches is holding ISC down, and the sentence for it.

    ISC runs behind a system-config gate and a per-project one, and the mesh
    tearing down is CORRECT whenever either is off — opening a template project
    with ISC off does it, and logs it cleanly. What was missing is any surface
    saying *where the switch is*: the page read "enabled in the project but not
    running yet. Save and reload the project", which is wrong advice when the
    system gate is the one down, and following it costs an hour.
    """
    system_enabled, project_enabled = engine.isc_gates()
    if engine.project is None:
        return {
            "disabled_by": "no_project",
            "reason": "No project is loaded, so there is nothing for ISC to share yet.",
        }
    if not system_enabled and not project_enabled:
        return {
            "disabled_by": "both",
            "reason": (
                "ISC is switched off in this project and for the whole system "
                "(system.json, isc.enabled). Both have to be on for the mesh to run."
            ),
        }
    if not system_enabled:
        return {
            "disabled_by": "system",
            "reason": (
                "ISC is switched off for this whole system in system.json "
                "(isc.enabled), so it will not start whatever a project asks for. "
                "Turn it back on there."
            ),
        }
    if not project_enabled:
        return {
            "disabled_by": "project",
            "reason": "ISC is switched off in this project.",
        }
    return {
        "disabled_by": "error",
        "reason": (
            "ISC is switched on everywhere but did not start. The server log "
            "records why."
        ),
    }


@router.get("/isc/status")
async def isc_status() -> dict[str, Any]:
    """ISC status: enabled, instance info, peer summary.

    When it is off, this also says which of the two gates is holding it down —
    see ``_why_isc_is_off``.
    """
    engine = _get_engine()
    if engine.isc is None:
        return {
            "status": "disabled", "enabled": False, **_why_isc_is_off(engine),
            "auth_key": _auth_key_state(engine),
        }
    return {**engine.isc.get_status(), "auth_key": _auth_key_state(engine)}


def _auth_key_state(engine) -> str:
    """Where the project's mesh key is, for the ISC page: ``saved`` (on this
    system, shown on request), ``needs_login`` (the project marks one this
    system does not have, as after a restore onto another system) or
    ``none``. The key itself is never in this answer."""
    key = isc_key(engine.project) if engine.project is not None else None
    if key == SAVED_LOGIN:
        return "saved" if saved_isc_key(engine.project, engine.device_logins) else "needs_login"
    return "saved" if isinstance(key, str) and key else "none"


@router.get("/isc/auth-key")
async def isc_auth_key() -> dict[str, Any]:
    """The mesh key, for the ISC page's Show, so it can be typed on the next
    system. Asked for only when somebody presses Show; the project and every
    copy of it hold the marker instead (core/device_logins)."""
    engine = _get_engine()
    if engine.project is None:
        raise HTTPException(status_code=503, detail="No project is loaded.")
    return {"auth_key": saved_isc_key(engine.project, engine.device_logins)}


@router.put("/isc/auth-key")
async def set_isc_auth_key(data: ISCAuthKeyRequest) -> dict[str, Any]:
    """Save a new mesh key. It goes into this system's login file through the
    project save, the project keeps the marker, and the mesh reloads with it."""
    engine = _get_engine()
    if engine.project is None:
        raise HTTPException(status_code=503, detail="No project is loaded.")
    key = data.auth_key
    if not key.strip():
        raise HTTPException(status_code=400, detail="Enter the new key.")
    if key == SAVED_LOGIN:
        raise HTTPException(status_code=400, detail="Enter the new key itself.")

    def mutate(project) -> None:
        project.isc.auth_key = key

    await engine.apply_project_edit(mutate)
    return {"status": "saved"}


@router.get("/isc/instances")
async def isc_instances() -> dict[str, Any]:
    """List all discovered/connected ISC peer instances."""
    engine = _get_engine()
    if engine.isc is None:
        return {"instances": []}
    return {"instances": engine.isc.get_instances()}


@router.post("/isc/send")
async def isc_send(data: ISCSendRequest) -> dict[str, Any]:
    """Send an event to a remote ISC peer."""
    engine = _get_engine()
    if engine.isc is None:
        raise HTTPException(status_code=503, detail="ISC not enabled")
    try:
        await engine.isc.send_to(data.instance_id, data.event, data.payload)
        return {"status": "sent"}
    except ConnectionError as e:
        raise _api_error(503, f"ISC peer '{data.instance_id}' is not connected", e)


@router.post("/isc/broadcast")
async def isc_broadcast(data: ISCBroadcastRequest) -> dict[str, Any]:
    """Broadcast an event to all connected ISC peers."""
    engine = _get_engine()
    if engine.isc is None:
        raise HTTPException(status_code=503, detail="ISC not enabled")
    if not data.event:
        raise HTTPException(status_code=422, detail="Missing 'event' field")
    await engine.isc.broadcast(data.event, data.payload)
    return {"status": "broadcast"}


@router.post("/isc/command")
async def isc_command(data: ISCCommandRequest) -> dict[str, Any]:
    """Send a device command to a remote ISC peer.

    Three different outcomes, because they have three different fixes and
    they are all somewhere else:

    * the peer answered "no" — its allowlist refused the command, it is at
      its concurrent-command cap, or its device reported a fault. It computed
      that sentence; this returns it verbatim as
      ``{"success": false, "error": ...}``, which is the shape the API
      reference has always documented, and the reason ``success`` is in the
      success body at all. 200: the call reached the peer and got an answer.
    * we never reached the peer, or it went away mid-command — 503.
    * the peer took too long — 504.

    Collapsing all three into one 500 with our own sentence was what made an
    allowlist you have to edit on the OTHER box indistinguishable from a
    network fault.
    """
    engine = _get_engine()
    if engine.isc is None:
        raise HTTPException(status_code=503, detail="ISC not enabled")
    try:
        result = await engine.isc.send_command(
            data.instance_id, data.device_id, data.command, data.params,
        )
        return {"success": True, "result": result}
    except ISCRemoteError as e:
        return {"success": False, "error": str(e)}
    except ConnectionError as e:
        raise _api_error(503, f"ISC peer '{data.instance_id}' is not connected", e)
    except TimeoutError as e:
        raise _api_error(504, f"Command timed out on ISC peer '{data.instance_id}'", e)
    except Exception as e:
        raise _api_error(500, f"Failed to send command to ISC peer '{data.instance_id}'", e)
