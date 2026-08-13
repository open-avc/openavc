"""A cloud-authorized session gets into the Programmer without the password.

Two callers arrive this way and neither can be handed the instance credential:
OpenAVC support working under a customer's grant (nobody here holds their
password), and the system's own owner, who turned on password-free remote
programming rather than typing a per-room password once per room. Before this
existed, both landed on the instance's own sign-in screen and stopped.

What is pinned here is the whole promise and its limits:

- the marker rides only on a tunnel the cloud authorized, never on an ordinary
  one, and it carries a secret this process minted rather than a name anyone
  could assert;
- whoever called the cloud cannot supply or suppress it;
- it is only believed from a loopback peer, so it is not a LAN credential;
- it dies with the tunnel, which is what makes withdrawing the authorization
  enough;
- it authenticates the Programmer and its WebSocket, and it does NOT become the
  device's own console, so host network configuration stays behind the password.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import openavc.api.auth as auth_mod
from openavc.api import cloud_session
from openavc.main import app
from openavc.system import network as netmod
from openavc.utils.request_origin import (
    CLOUD_SESSION_HEADER,
    TUNNEL_HEADER,
    cloud_session_secret,
)


@pytest.fixture(autouse=True)
def no_leftover_sessions():
    """Every test starts and ends with an empty registry."""
    cloud_session._sessions.clear()
    yield
    cloud_session._sessions.clear()


@pytest.fixture
def tunnel_handler():
    from openavc.cloud.tunnel import TunnelHandler

    agent = MagicMock()
    agent.send_message = AsyncMock()
    return TunnelHandler(agent)


def _conn(handler, *, authorized: bool, tunnel_id="t-sup"):
    from openavc.cloud.tunnel import TunnelConnection

    conn = TunnelConnection(
        tunnel_id=tunnel_id, target_port=8080, data_ws=AsyncMock()
    )
    if authorized:
        conn.session_secret = cloud_session.open_session(tunnel_id)
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
# The agent stamps it, and only on an authorized tunnel
# ===========================================================================


@pytest.mark.asyncio
async def test_ordinary_tunnel_carries_no_marker(tunnel_handler):
    """An unauthorized tunnel is unchanged: the instance asks for its password."""
    conn, client = _conn(tunnel_handler, authorized=False)

    await tunnel_handler._handle_http_request(
        conn, {"id": "r1", "method": "GET", "path": "/api/project", "headers": {}, "body": ""}
    )

    sent = client.request.call_args.kwargs["headers"]
    assert CLOUD_SESSION_HEADER not in {k.lower() for k in sent}
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_authorized_tunnel_stamps_the_minted_secret(tunnel_handler):
    conn, client = _conn(tunnel_handler, authorized=True)

    await tunnel_handler._handle_http_request(
        conn, {"id": "r1", "method": "GET", "path": "/api/project", "headers": {}, "body": ""}
    )

    sent = client.request.call_args.kwargs["headers"]
    assert sent[CLOUD_SESSION_HEADER] == conn.session_secret
    assert cloud_session.is_active(conn.session_secret)
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_upstream_cannot_forge_or_suppress_the_marker(tunnel_handler):
    """The cloud forwards its caller's headers verbatim. A value that arrived
    with the request must never reach the local server, in any casing."""
    conn, client = _conn(tunnel_handler, authorized=True)

    await tunnel_handler._handle_http_request(
        conn,
        {
            "id": "r2",
            "method": "POST",
            "path": "/api/project",
            "headers": {"X-OpenAVC-Cloud-Session": "attacker-supplied", "Accept": "*/*"},
            "body": "",
        },
    )

    sent = client.request.call_args.kwargs["headers"]
    keys = [k for k in sent if k.lower() == CLOUD_SESSION_HEADER]
    assert keys == [CLOUD_SESSION_HEADER], "exactly one marker, stamped by us"
    assert sent[CLOUD_SESSION_HEADER] == conn.session_secret
    assert sent["Accept"] == "*/*", "other headers untouched"
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_an_ordinary_tunnel_strips_a_forged_marker(tunnel_handler):
    """The dangerous case: no secret of its own to overwrite the forgery with,
    so the drop has to happen whether or not this tunnel is a support one."""
    conn, client = _conn(tunnel_handler, authorized=False)

    await tunnel_handler._handle_http_request(
        conn,
        {
            "id": "r3",
            "method": "GET",
            "path": "/api/project",
            "headers": {"X-OpenAVC-Cloud-Session": "attacker-supplied"},
            "body": "",
        },
    )

    sent = client.request.call_args.kwargs["headers"]
    assert CLOUD_SESSION_HEADER not in {k.lower() for k in sent}
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_support_websocket_open_is_marked(tunnel_handler):
    """The IDE loads over REST and then lives on its WebSocket. Authenticating
    one without the other gives a staff member an empty Programmer."""
    conn, _ = _conn(tunnel_handler, authorized=True, tunnel_id="t-ws")

    local_ws = AsyncMock()
    local_ws.__aiter__ = lambda self: self
    local_ws.__anext__ = AsyncMock(side_effect=StopAsyncIteration)

    with patch(
        "openavc.cloud.tunnel.websockets.connect", new=AsyncMock(return_value=local_ws)
    ) as connect:
        await tunnel_handler._handle_ws_open(
            conn,
            {"id": "ws-1", "path": "/ws?client=programmer",
             "headers": {"X-OpenAVC-Cloud-Session": "forged"}},
        )
        headers = connect.call_args.kwargs["additional_headers"]

    marker = [v for k, v in headers if k.lower() == CLOUD_SESSION_HEADER]
    assert marker == [conn.session_secret]
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_closing_the_tunnel_discards_the_secret(tunnel_handler):
    """Revoking a grant closes the tunnel cloud-side. That has to be enough:
    the secret must not outlive it here either."""
    conn, _ = _conn(tunnel_handler, authorized=True, tunnel_id="t-close")
    secret = conn.session_secret
    assert cloud_session.is_active(secret)

    await tunnel_handler._close_tunnel("t-close")

    assert not cloud_session.is_active(secret)
    assert cloud_session.active_count() == 0


@pytest.mark.asyncio
async def test_agent_shutdown_discards_every_secret(tunnel_handler):
    _conn(tunnel_handler, authorized=True, tunnel_id="t-a")
    _conn(tunnel_handler, authorized=True, tunnel_id="t-b")
    assert cloud_session.active_count() == 2

    await tunnel_handler.stop()

    assert cloud_session.active_count() == 0


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
    return {TUNNEL_HEADER: "1", CLOUD_SESSION_HEADER: secret}


@pytest.mark.asyncio
async def test_claimed_instance_refuses_a_tunnel_with_no_session(claimed):
    """The state this fixes: the grant arrives and the Programmer says no."""
    resp = await _get("/api/project", headers={TUNNEL_HEADER: "1"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_live_session_reaches_the_programmer_api(claimed):
    secret = cloud_session.open_session("t-live")
    resp = await _get("/api/project", headers=_session_headers(secret))
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_a_wrong_secret_is_still_refused(claimed):
    cloud_session.open_session("t-live")
    resp = await _get("/api/project", headers=_session_headers("not-the-secret"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_closed_session_is_refused(claimed):
    """Same request, same secret, after the grant ended."""
    secret = cloud_session.open_session("t-live")
    assert (await _get("/api/project", headers=_session_headers(secret))).status_code != 401

    cloud_session.close_session("t-live")

    assert (await _get("/api/project", headers=_session_headers(secret))).status_code == 401


@pytest.mark.asyncio
async def test_a_lan_client_cannot_use_a_leaked_secret(claimed):
    """The secret never leaves this host, but it is not treated as a bearer
    token even so: off loopback it is not read at all."""
    secret = cloud_session.open_session("t-live")
    resp = await _get("/api/project", headers=_session_headers(secret), peer="192.168.1.50")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_an_authorized_session_does_not_become_the_console(claimed):
    """Physical access to the appliance's own screen is a different trust
    anchor, and configuring the host's network rests on it with no password.
    A staff session is remote by definition and must not inherit it."""
    secret = cloud_session.open_session("t-live")
    resp = await _get("/api/system/network", headers=_session_headers(secret))
    # 404 = allowed through to a route with no backend; 401 = refused.
    # Either way it must not be the credential-free console grant, which is
    # what the plain-console call returns.
    assert resp.status_code == 404, "programmer-level access, not console access"

    from openavc.utils.request_origin import is_local_console_request

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {TUNNEL_HEADER: "1", CLOUD_SESSION_HEADER: secret}

    assert is_local_console_request(_Req()) is False


@pytest.mark.asyncio
async def test_auth_required_says_ok_on_an_authorized_session(claimed):
    """Otherwise the caller is shown a password box for a password they do not
    have, in front of an app they can already use."""
    secret = cloud_session.open_session("t-live")

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
            "headers": {TUNNEL_HEADER: "1", CLOUD_SESSION_HEADER: secret},
        },
    )()


def test_the_secret_is_only_read_from_a_loopback_peer():
    """The one place that reasons about the peer, so it is tested here rather
    than at each door."""
    secret = cloud_session.open_session("t-live")
    assert cloud_session_secret(_fake_conn(secret)) == secret
    assert cloud_session_secret(_fake_conn(secret, peer="192.168.1.50")) == ""
    assert cloud_session_secret(None) == ""


def test_ws_auth_accepts_a_live_session(claimed):
    secret = cloud_session.open_session("t-live")
    headers = {TUNNEL_HEADER: "1", CLOUD_SESSION_HEADER: secret}
    assert auth_mod.check_ws_auth({}, headers, secret) is True


def test_ws_auth_refuses_a_closed_session(claimed):
    secret = cloud_session.open_session("t-live")
    cloud_session.close_session("t-live")
    headers = {TUNNEL_HEADER: "1", CLOUD_SESSION_HEADER: secret}
    assert auth_mod.check_ws_auth({}, headers, secret) is False


def test_ws_auth_ignores_the_header_it_was_not_handed(claimed):
    """The default keeps every existing caller safe: the secret has to be
    resolved through request_origin and passed in, never read off the dict."""
    secret = cloud_session.open_session("t-live")
    headers = {TUNNEL_HEADER: "1", CLOUD_SESSION_HEADER: secret}
    assert auth_mod.check_ws_auth({}, headers) is False


# ===========================================================================
# The wire field
#
# Every test above sets session_secret by hand, so until now nothing read the
# field name off a real tunnel_open payload -- which is precisely what a rename
# breaks silently. The support session is the surface nobody exercises daily,
# so it is the one that would sit broken.
# ===========================================================================


async def _open(handler, payload):
    """Drive handle_tunnel_open with the data WS stubbed out."""
    with patch("openavc.cloud.tunnel.websockets.connect", new=AsyncMock()):
        await handler.handle_tunnel_open({"payload": payload})
    return handler._tunnels.get(payload["tunnel_id"])


def _payload(tunnel_id="t-wire", **extra):
    return {
        "tunnel_id": tunnel_id,
        "tunnel_token": "tok",
        "tunnel_data_url": "wss://cloud.example/tunnel-data/t-wire",
        **extra,
    }


@pytest.mark.asyncio
async def test_a_tunnel_with_no_authorization_mints_nothing(tunnel_handler):
    conn = await _open(tunnel_handler, _payload())
    assert conn.session_secret == ""
    assert cloud_session.active_count() == 0
    await tunnel_handler.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["support", "owner"])
async def test_either_authorization_reason_mints_a_secret(tunnel_handler, reason):
    """Both callers are trusted identically; the reason only picks the log
    sentence, so neither may be quietly stricter than the other."""
    conn = await _open(tunnel_handler, _payload(authorized_session=reason))
    assert conn.session_secret != ""
    assert cloud_session.is_active(conn.session_secret)
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_the_old_boolean_field_no_longer_authorizes_anything(tunnel_handler):
    """The 0.26.0 spelling was `support_session: true`, and dropping it is a
    deliberate break rather than an oversight: an instance that still honoured
    it would be trusting a field the cloud no longer means to send. Pinned so
    nobody restores the bool as a kindness."""
    conn = await _open(tunnel_handler, _payload(support_session=True))
    assert conn.session_secret == ""
    assert cloud_session.active_count() == 0
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_a_non_string_reason_authorizes_nothing(tunnel_handler):
    """`authorized_session: true` is the same mistake in a new field name."""
    conn = await _open(tunnel_handler, _payload(authorized_session=True))
    assert conn.session_secret == ""
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_an_unknown_reason_still_authorizes(tunnel_handler):
    """The reason is a label, not a permission. Refusing a working session to
    punish a typo in a field that grants nothing is the wrong trade."""
    conn = await _open(tunnel_handler, _payload(authorized_session="whatever"))
    assert cloud_session.is_active(conn.session_secret)
    await tunnel_handler.stop()
