"""Serve the simulator UI on the main origin, at ``/simulator/``.

The simulator runs as its own process with its own web server on a loopback
port, and for a long time the Programmer simply told the browser to open
``http://localhost:19500``. That works on exactly one machine: the server's
own. Open the IDE from a laptop -- which is how anyone actually commissions a
room -- and ``localhost`` is the laptop, so the button led nowhere. Over the
cloud tunnel it was worse: nothing was tunneling that port at all.

Which port it is, is now nobody's business but ours: it is the configured one
when that binds and the next one up when it does not (see
``core/simulation._choose_ui_port``), so this proxy asks
``active_simulator_ui_port()`` for the port the simulator actually got rather
than the one that was requested.

Binding 19500 to the LAN instead would have "fixed" it by putting an
**unauthenticated** control API on the network. That API can mutate device
state and shut the process down; it is written for loopback and says so
(``simulator/server.py``). So the simulator stays on 127.0.0.1 and the main
server proxies it here, which is the same shape plugins already use: one
origin, one credential, and the cloud tunnel carries it for free because it
is no longer a second port.

The UI cooperates by resolving its own mount prefix at runtime (``BASE`` in
``web/simulator/src/store/api.ts``) and by building with relative asset URLs,
so one build serves both here and directly on the simulator's own port for
driver development.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import websockets
from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import RedirectResponse, Response

from openavc.api.auth import (
    check_ws_auth,
    get_ws_auth_subprotocol,
    require_programmer_auth,
)
from openavc.core.simulation import active_simulator_ui_port
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# The auth split, and it is the whole design:
#
#   /simulator/api/*  and  /simulator/ws   -> credential required
#   everything else (the HTML, JS, CSS)    -> served open
#
# The control API is the thing worth protecting; the shell is inert markup that
# can do nothing until an authenticated call succeeds. Requiring a credential
# for the shell too sounds stricter and is actually worse: a new tab carries no
# credential, so the browser answers the 401 with its OWN sign-in dialog before
# our app ever runs. That is the popup this whole change exists to remove, and
# putting auth on the document is how you reintroduce it. `/programmer` has had
# this exact shape all along -- open shell, authenticated API -- which is why
# opening it in a fresh tab shows our sign-in card and not Chrome's.
#
# Registration order carries the split: the API rule is declared first so it
# matches before the open catch-all sees the path.
router = APIRouter(dependencies=[Depends(require_programmer_auth)])
open_router = APIRouter()
ws_router = APIRouter()

MOUNT = "/simulator"

# Headers that describe a single hop and must not be copied through a proxy.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}

# Headers that describe the BROWSER's context and must not be replayed at the
# simulator, because this hop is not that context.
#
# The simulator refuses any request carrying a non-loopback `Origin` -- its
# anti-rebinding guard, correct for a process meant to be reached only from its
# own machine. Forwarded verbatim, the browser's origin makes every asset 403,
# and the symptom is a blank page rather than an error: Chrome marks a module
# script `crossorigin`, so it sends `Origin` and the module fetch fails.
#
# Worth knowing when testing this: curl sends no `Origin` and sails through, so
# the proxy looks perfect from the shell and is broken in a browser.
#
# From the simulator's side this request genuinely IS local and same-origin --
# the main server on loopback is the client. The protection the guard provides
# has not been dropped, it has moved outward: a hostile page still cannot drive
# the control API here, because that half requires a credential it has no way
# to attach.
_BROWSER_CONTEXT = {"origin", "referer"}

_UNAVAILABLE = (
    "The simulator is not running. Start it from the Programmer sidebar "
    "(Simulation > Start), then reload this page."
)


def _target(path: str) -> str:
    """The loopback URL for a path below the mount point."""
    return f"http://127.0.0.1:{active_simulator_ui_port()}/{path.lstrip('/')}"


@open_router.get(MOUNT)
async def simulator_root() -> RedirectResponse:
    """Keep the UI's relative asset URLs resolving.

    Without the trailing slash the browser resolves ``assets/index.js``
    against ``/`` instead of ``/simulator/``, and every asset 404s.
    """
    return RedirectResponse(url=f"{MOUNT}/", status_code=307)


@router.api_route(
    MOUNT + "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def simulator_api_proxy(path: str, request: Request) -> Response:
    """The simulator's control API: start and stop devices, mutate state, stop
    the process. Credential required, and this rule is declared before the open
    one so it wins the match."""
    return await _forward(f"api/{path}", request)


@open_router.api_route(MOUNT + "/{path:path}", methods=["GET", "HEAD"])
async def simulator_shell_proxy(path: str, request: Request) -> Response:
    """The UI shell: index.html and the built assets.

    GET and HEAD only. Nothing reachable here changes anything -- every action
    the UI offers goes through the authenticated API above.
    """
    return await _forward(path, request)


# One client for the life of the process, rather than one per request.
#
# `async with httpx.AsyncClient()` reads as the careful thing to write and is
# the opposite here: a client owns a connection pool, so building one per
# request means no pool at all -- a fresh connection, and on Windows a fresh
# read of the system proxy configuration, for every forwarded call. Measured on
# a loopback simulator: 199.7 ms per request that way against 0.9 ms through a
# shared client, which is 229x. Nobody noticed because the shell is a handful
# of requests; the control API is not. Dragging one slider is a POST per pixel
# of travel, so the UI spent whole seconds queueing behind connection setup and
# read as a broken simulator.
#
# Keep-alive across a simulator restart is not a problem to solve here: the
# child process closes its sockets as it dies, and httpx discards a connection
# the far end closed and dials a new one (verified against a server restarted
# under the pool). Nothing stale is ever handed to a caller.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """The shared client, built on first use.

    Built lazily rather than at import so constructing it cannot depend on an
    event loop existing yet. There is no lock because there is no await between
    the check and the assignment: on one event loop this cannot interleave.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def aclose_client() -> None:
    """Release the shared client. Called from the app's shutdown."""
    global _client
    if _client is not None:
        client, _client = _client, None
        with contextlib.suppress(Exception):
            await client.aclose()


async def _forward(path: str, request: Request) -> Response:
    """Forward one request to the simulator process and return its answer."""
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
        and k.lower() not in _BROWSER_CONTEXT
        and k.lower() != "host"
    }
    try:
        upstream = await _get_client().request(
            request.method,
            _target(path),
            content=body,
            headers=headers,
            params=request.query_params,
        )
    except httpx.ConnectError:
        # The simulator is stopped, or was never started. This is the ordinary
        # case (the UI is only useful while it runs), so answer it plainly
        # rather than as a server error.
        return Response(content=_UNAVAILABLE, status_code=503, media_type="text/plain")
    except httpx.HTTPError as e:
        log.warning(f"Simulator UI proxy failed for /{path}: {e}")
        return Response(content=f"Simulator UI unreachable: {e}", status_code=502,
                        media_type="text/plain")

    passthrough = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )


@ws_router.websocket(MOUNT + "/ws")
async def simulator_ws_proxy(ws: WebSocket) -> None:
    """Pump the simulator's live feed (state, protocol log) through this origin."""
    if not check_ws_auth(dict(ws.query_params), dict(ws.headers)):
        await ws.close(code=4401)
        return

    # The client carries its credential in a subprotocol, and RFC 6455 says a
    # server MUST select one the client offered. Accepting with none fails the
    # handshake in the browser -- the socket opens, closes 1006, and the UI just
    # reads "Disconnected" with no error anywhere. Echo it back, as `api/ws.py`
    # does for the platform's own socket.
    subprotocol = get_ws_auth_subprotocol(dict(ws.headers))

    upstream_url = f"ws://127.0.0.1:{active_simulator_ui_port()}/ws"
    try:
        upstream = await websockets.connect(upstream_url, open_timeout=5)
    except Exception as e:
        # Same reasoning as the HTTP side: a stopped simulator is expected.
        log.debug(f"Simulator WS proxy could not reach {upstream_url}: {e}")
        await ws.close(code=1013)
        return

    await ws.accept(subprotocol=subprotocol)

    async def client_to_sim() -> None:
        while True:
            await upstream.send(await ws.receive_text())

    async def sim_to_client() -> None:
        async for message in upstream:
            await ws.send_text(
                message if isinstance(message, str) else message.decode("utf-8", "replace")
            )

    tasks = [asyncio.create_task(client_to_sim()), asyncio.create_task(sim_to_client())]
    try:
        # Either direction ending means the socket is done; cancel its partner
        # so neither task is left pumping into a closed peer.
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # Retrieve the finished task's exception even though we do not act on
        # it. A closing socket raises here as a matter of course, and an
        # unretrieved task exception is logged by asyncio as if something had
        # gone wrong -- a full traceback in the journal every time a user
        # closes the simulator window.
        for task in done:
            with contextlib.suppress(Exception):
                task.exception()
    except Exception:  # noqa: BLE001 - a dropped socket is not an error here
        pass
    finally:
        await upstream.close()
        try:
            await ws.close()
        except RuntimeError:
            pass  # already closed by the peer


def simulator_ui_path() -> str:
    """The browser-facing location of the simulator UI, relative to this origin.

    Relative on purpose: the caller is a page already served by this server,
    so this works unchanged on localhost, over the LAN, and through the cloud
    tunnel -- none of which agree on what the absolute URL would be.
    """
    return f"{MOUNT}/"
