"""Inbound device push endpoint (``push: {type: http_listener}``).

Devices that deliver notifications by HTTP callback (webhook registrations,
UPnP GENA NOTIFY) POST here; the body is handed to the subscribed driver's
response dispatch. Unauthenticated by design — AV devices cannot carry
credentials for us, and the trust model is the AV VLAN, the same as UDP
device control (see the IT network guide). The source-IP gate and path
demux live in ``server/transport/http_listener.py``.

``NOTIFY`` is accepted alongside ``POST`` because UPnP GENA delivers event
messages with that method.
"""

from fastapi import APIRouter, Request, Response

from server.transport import http_listener

open_router = APIRouter(tags=["push"])

_PUSH_METHODS = ["POST", "NOTIFY"]


@open_router.api_route("/push/{device_id}", methods=_PUSH_METHODS)
@open_router.api_route("/push/{device_id}/{label}", methods=_PUSH_METHODS)
async def device_push(
    device_id: str, request: Request, label: str = ""
) -> Response:
    body = await request.body()
    status = await http_listener.dispatch(
        device_id,
        label,
        http_listener.HTTPPushRequest(
            body=body,
            method=request.method,
            headers={k.lower(): v for k, v in request.headers.items()},
            source_ip=request.client.host if request.client else "",
            label=label,
        ),
    )
    return Response(status_code=status)
