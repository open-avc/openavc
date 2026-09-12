"""Inter-System Communication (ISC) REST endpoints.

The request/response half of ISC: peer status, the discovered-instance list,
and the three outbound sends (event to one peer, broadcast to all, device
command with a reply). The peer-to-peer WebSocket that carries the traffic is
``openavc/api/isc_ws.py``; the mesh itself is ``openavc/core/isc.py``.

Every route returns/raises rather than assuming ISC is on — ``engine.isc`` is
None whenever the feature is disabled in system config.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from openavc.api._engine import _get_engine
from openavc.api.errors import api_error as _api_error
from openavc.api.models import (
    ISCBroadcastRequest, ISCCommandRequest, ISCSendRequest,
)
from openavc.core.isc import ISCRemoteError

router = APIRouter()


@router.get("/isc/status")
async def isc_status() -> dict[str, Any]:
    """ISC status: enabled, instance info, peer summary."""
    engine = _get_engine()
    if engine.isc is None:
        return {"status": "disabled", "enabled": False}
    return engine.isc.get_status()


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
