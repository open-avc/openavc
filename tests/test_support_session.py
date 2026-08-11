"""A customer-granted OpenAVC support session gets into the Programmer.

Before this, a grant opened a Programmer-scoped tunnel that carried real
traffic and delivered the staff member to the instance's own sign-in screen.
Nobody at OpenAVC holds a customer's instance password, so the grant reached
the door and stopped, and the only way through was the customer typing their
password into a support thread.

What is pinned here is the whole promise and its limits:

- the marker rides only on a tunnel the cloud opened under a live grant, never
  on an ordinary one, and it carries a secret this process minted rather than a
  name anyone could assert;
- whoever called the cloud cannot supply or suppress it;
- it is only believed from a loopback peer, so it is not a LAN credential;
- it dies with the tunnel, which is what makes revoking the grant enough;
- it authenticates the Programmer and its WebSocket, and it does NOT become the
  device's own console, so host network configuration stays behind the password.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import openavc.api.auth as auth_mod
from openavc.api import support_session
from openavc.main import app
from openavc.system import network as netmod
from openavc.utils.request_origin import (
    SUPPORT_SESSION_HEADER,
    TUNNEL_HEADER,
    support_session_secret,
)


@pytest.fixture(autouse=True)
def no_leftover_sessions():
    """Every test starts and ends with an empty registry."""
    support_session._sessions.clear()
    yield
    support_session._sessions.clear()


@pytest.fixture
def tunnel_handler():
    from openavc.cloud.tunnel import TunnelHandler

    agent = MagicMock()
    agent.send_message = AsyncMock()
    return TunnelHandler(agent)


def _conn(handler, *, support: bool, tunnel_id="t-sup"):
    from openavc.cloud.tunnel import TunnelConnection

    conn = TunnelConnection(
        tunnel_id=tunnel_id, target_port=8080, data_ws=AsyncMock()
    )
    if support:
        conn.support_secret = support_session.open_session(tunnel_id)
    handler._tunnels[tunnel_id] = conn

    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.content = b"ok"
    client = AsyncMock()
    client.request = AsyncMock(return_value=response)
    handler._http_client = client
    return conn, client


# ===========================================================================
# The agent stamps it, and only on a support tunnel
# ===========================================================================


@pytest.mark.asyncio
async def test_ordinary_tunnel_carries_no_support_marker(tunnel_handler):
    """The customer's own remote session is unchanged: they have a password."""
    conn, client = _conn(tunnel_handler, support=False)

    await tunnel_handler._handle_http_request(
        conn, {"id": "r1", "method": "GET", "path": "/api/project", "headers": {}, "body": ""}
    )

    sent = client.request.call_args.kwargs["headers"]
    assert SUPPORT_SESSION_HEADER not in {k.lower() for k in sent}
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_support_tunnel_stamps_the_minted_secret(tunnel_handler):
    conn, client = _conn(tunnel_handler, support=True)

    await tunnel_handler._handle_http_request(
        conn, {"id": "r1", "method": "GET", "path": "/api/project", "headers": {}, "body": ""}
    )

    sent = client.request.call_args.kwargs["headers"]
    assert sent[SUPPORT_SESSION_HEADER] == conn.support_secret
    assert support_session.is_active(conn.support_secret)
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_upstream_cannot_forge_or_suppress_the_support_marker(tunnel_handler):
    """The cloud forwards its caller's headers verbatim. A value that arrived
    with the request must never reach the local server, in any casing."""
    conn, client = _conn(tunnel_handler, support=True)

    await tunnel_handler._handle_http_request(
        conn,
        {
            "id": "r2",
            "method": "POST",
            "path": "/api/project",
            "headers": {"X-OpenAVC-Support-Session": "attacker-supplied", "Accept": "*/*"},
            "body": "",
        },
    )

    sent = client.request.call_args.kwargs["headers"]
    keys = [k for k in sent if k.lower() == SUPPORT_SESSION_HEADER]
    assert keys == [SUPPORT_SESSION_HEADER], "exactly one marker, stamped by us"
    assert sent[SUPPORT_SESSION_HEADER] == conn.support_secret
    assert sent["Accept"] == "*/*", "other headers untouched"
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_an_ordinary_tunnel_strips_a_forged_marker(tunnel_handler):
    """The dangerous case: no secret of its own to overwrite the forgery with,
    so the drop has to happen whether or not this tunnel is a support one."""
    conn, client = _conn(tunnel_handler, support=False)

    await tunnel_handler._handle_http_request(
        conn,
        {
            "id": "r3",
            "method": "GET",
            "path": "/api/project",
            "headers": {"X-OpenAVC-Support-Session": "attacker-supplied"},
            "body": "",
        },
    )

    sent = client.request.call_args.kwargs["headers"]
    assert SUPPORT_SESSION_HEADER not in {k.lower() for k in sent}
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_support_websocket_open_is_marked(tunnel_handler):
    """The IDE loads over REST and then lives on its WebSocket. Authenticating
    one without the other gives a staff member an empty Programmer."""
    conn, _ = _conn(tunnel_handler, support=True, tunnel_id="t-ws")

    local_ws = AsyncMock()
    local_ws.__aiter__ = lambda self: self
    local_ws.__anext__ = AsyncMock(side_effect=StopAsyncIteration)

    with patch(
        "openavc.cloud.tunnel.websockets.connect", new=AsyncMock(return_value=local_ws)
    ) as connect:
        await tunnel_handler._handle_ws_open(
            conn,
            {"id": "ws-1", "path": "/ws?client=programmer",
             "headers": {"X-OpenAVC-Support-Session": "forged"}},
        )
        headers = connect.call_args.kwargs["additional_headers"]

    marker = [v for k, v in headers if k.lower() == SUPPORT_SESSION_HEADER]
    assert marker == [conn.support_secret]
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_closing_the_tunnel_discards_the_secret(tunnel_handler):
    """Revoking a grant closes the tunnel cloud-side. That has to be enough:
    the secret must not outlive it here either."""
    conn, _ = _conn(tunnel_handler, support=True, tunnel_id="t-close")
    secret = conn.support_secret
    assert support_session.is_active(secret)

    await tunnel_handler._close_tunnel("t-close")

    assert not support_session.is_active(secret)
    assert support_session.active_count() == 0


@pytest.mark.asyncio
async def test_agent_shutdown_discards_every_secret(tunnel_handler):
    _conn(tunnel_handler, support=True, tunnel_id="t-a")
    _conn(tunnel_handler, support=True, tunnel_id="t-b")
    assert support_session.active_count() == 2

    await tunnel_handler.stop()

    assert support_session.active_count() == 0


# ===========================================================================
# The server accepts it — and only it
# ===========================================================================


@pytest.fixture
def claimed(monkeypatch):
    monkeypatch.setattr(netmod, "get_backend", lambda: None)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "secret-pw-123")
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")


async def _get(path, headers=None, peer="127.0.0.1"):
    transport = ASGITransport(app=app, client=(peer, 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.get(path, headers=headers or {})


def _session_headers(secret):
    return {TUNNEL_HEADER: "1", SUPPORT_SESSION_HEADER: secret}


@pytest.mark.asyncio
async def test_claimed_instance_refuses_a_tunnel_with_no_session(claimed):
    """The state this fixes: the grant arrives and the Programmer says no."""
    resp = await _get("/api/project", headers={TUNNEL_HEADER: "1"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_live_session_reaches_the_programmer_api(claimed):
    secret = support_session.open_session("t-live")
    resp = await _get("/api/project", headers=_session_headers(secret))
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_a_wrong_secret_is_still_refused(claimed):
    support_session.open_session("t-live")
    resp = await _get("/api/project", headers=_session_headers("not-the-secret"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_closed_session_is_refused(claimed):
    """Same request, same secret, after the grant ended."""
    secret = support_session.open_session("t-live")
    assert (await _get("/api/project", headers=_session_headers(secret))).status_code != 401

    support_session.close_session("t-live")

    assert (await _get("/api/project", headers=_session_headers(secret))).status_code == 401


@pytest.mark.asyncio
async def test_a_lan_client_cannot_use_a_leaked_secret(claimed):
    """The secret never leaves this host, but it is not treated as a bearer
    token even so: off loopback it is not read at all."""
    secret = support_session.open_session("t-live")
    resp = await _get("/api/project", headers=_session_headers(secret), peer="192.168.1.50")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_support_session_does_not_become_the_console(claimed):
    """Physical access to the appliance's own screen is a different trust
    anchor, and configuring the host's network rests on it with no password.
    A staff session is remote by definition and must not inherit it."""
    secret = support_session.open_session("t-live")
    resp = await _get("/api/system/network", headers=_session_headers(secret))
    # 404 = allowed through to a route with no backend; 401 = refused.
    # Either way it must not be the credential-free console grant, which is
    # what the plain-console call returns.
    assert resp.status_code == 404, "programmer-level access, not console access"

    from openavc.utils.request_origin import is_local_console_request

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {TUNNEL_HEADER: "1", SUPPORT_SESSION_HEADER: secret}

    assert is_local_console_request(_Req()) is False


@pytest.mark.asyncio
async def test_auth_required_says_ok_on_a_support_session(claimed):
    """Otherwise the staff member is shown a password box for a password only
    the customer has, in front of an app they can already use."""
    secret = support_session.open_session("t-live")

    plain = await _get("/api/auth/required", headers={TUNNEL_HEADER: "1"})
    assert plain.json()["state"] == "required"

    on_session = await _get("/api/auth/required", headers=_session_headers(secret))
    assert on_session.json() == {"required": False, "state": "ok"}


# ===========================================================================
# The WebSocket door
# ===========================================================================


def _fake_conn(secret, peer="127.0.0.1"):
    """Stands in for a Request or a WebSocket: both are read the same way."""
    return type(
        "_Conn", (),
        {
            "client": type("C", (), {"host": peer})(),
            "headers": {TUNNEL_HEADER: "1", SUPPORT_SESSION_HEADER: secret},
        },
    )()


def test_the_secret_is_only_read_from_a_loopback_peer():
    """The one place that reasons about the peer, so it is tested here rather
    than at each door."""
    secret = support_session.open_session("t-live")
    assert support_session_secret(_fake_conn(secret)) == secret
    assert support_session_secret(_fake_conn(secret, peer="192.168.1.50")) == ""
    assert support_session_secret(None) == ""


def test_ws_auth_accepts_a_live_session(claimed):
    secret = support_session.open_session("t-live")
    headers = {TUNNEL_HEADER: "1", SUPPORT_SESSION_HEADER: secret}
    assert auth_mod.check_ws_auth({}, headers, secret) is True


def test_ws_auth_refuses_a_closed_session(claimed):
    secret = support_session.open_session("t-live")
    support_session.close_session("t-live")
    headers = {TUNNEL_HEADER: "1", SUPPORT_SESSION_HEADER: secret}
    assert auth_mod.check_ws_auth({}, headers, secret) is False


def test_ws_auth_ignores_the_header_it_was_not_handed(claimed):
    """The default keeps every existing caller safe: the secret has to be
    resolved through request_origin and passed in, never read off the dict."""
    secret = support_session.open_session("t-live")
    headers = {TUNNEL_HEADER: "1", SUPPORT_SESSION_HEADER: secret}
    assert auth_mod.check_ws_auth({}, headers) is False
