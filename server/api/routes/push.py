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
from server.utils.request_origin import is_tunneled_request

open_router = APIRouter(tags=["push"])

_PUSH_METHODS = ["POST", "NOTIFY"]


@open_router.api_route("/push/{device_id}", methods=_PUSH_METHODS)
@open_router.api_route("/push/{device_id}/{label}", methods=_PUSH_METHODS)
async def device_push(
    device_id: str, request: Request, label: str = ""
) -> Response:
    # A device on the AV VLAN reaches this listener directly; nothing legitimate
    # pushes device events in through the operator's remote-UI tunnel. Refuse
    # them, because a tunneled request arrives from loopback and the source_ip
    # below would be 127.0.0.1 — which is a real device address while a driver
    # is redirected to the simulator, and the whole gate downstream is that
    # comparison.
    if is_tunneled_request(request):
        return Response(status_code=403)

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
