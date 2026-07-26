"""A tunneled request must never be mistaken for the device's own console.

The cloud remote-UI tunnel proxies remote traffic to ``localhost``, so before
this was closed a remote caller arrived indistinguishable from someone standing
at the appliance. Two credential-free grants keyed on that identity:

- ``/api/system/network/*`` reconfigures the host's IP, WiFi and hostname via
  nmcli, and allows loopback with **no password** so an appliance can be put
  onto a network it isn't on yet.
- The rate limiter exempts loopback entirely — including the 401 brute-force
  counter, which is what makes the tunnel an unmetered channel for guessing
  the password those routes would otherwise want.

The contract pinned here: the marker is stamped by the agent (not suppressible
from upstream), it is only believed from a loopback peer (so a LAN client can't
push traffic into the tunnel's rate-limit bucket), console access still works
without a credential, and a remote operator who *does* present the credential
keeps every capability they had.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import server.api.auth as auth_mod
from server.main import app
from server.middleware.rate_limit import (
    TUNNEL_BUCKET_KEY,
    RateLimitMiddleware,
    _ip_buckets,
    _warn_dedup,
)
from server.system import network as netmod
from server.utils.request_origin import TUNNEL_HEADER

TUNNEL_HEADERS = {TUNNEL_HEADER: "1"}


# ===========================================================================
# The agent stamps the marker — and owns it
# ===========================================================================


@pytest.fixture
def tunnel_handler():
    from server.cloud.tunnel import TunnelHandler

    agent = MagicMock()
    agent.send_message = AsyncMock()
    return TunnelHandler(agent)


def _http_conn(handler, tunnel_id="t-mark"):
    from server.cloud.tunnel import TunnelConnection

    conn = TunnelConnection(
        tunnel_id=tunnel_id, target_port=8080, data_ws=AsyncMock()
    )
    handler._tunnels[tunnel_id] = conn

    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.content = b"ok"
    client = AsyncMock()
    client.request = AsyncMock(return_value=response)
    handler._http_client = client
    return conn, client


@pytest.mark.asyncio
async def test_proxied_http_request_is_marked(tunnel_handler):
    conn, client = _http_conn(tunnel_handler)

    await tunnel_handler._handle_http_request(
        conn,
        {"id": "r1", "method": "GET", "path": "/api/system/network", "headers": {}, "body": ""},
    )

    sent_headers = client.request.call_args.kwargs["headers"]
    assert sent_headers[TUNNEL_HEADER] == "1"
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_upstream_cannot_suppress_or_forge_the_marker(tunnel_handler):
    """The cloud forwards its caller's headers verbatim, so the marker has to
    be the agent's word and not a value that arrived with the request."""
    conn, client = _http_conn(tunnel_handler)

    await tunnel_handler._handle_http_request(
        conn,
        {
            "id": "r2",
            "method": "POST",
            "path": "/api/system/network/ipv4",
            # Whoever called the cloud tries to hand us a "not tunneled" value,
            # in the casing a header-filter written against the lower-case name
            # would be most likely to miss.
            "headers": {"X-OpenAVC-Tunneled": "0", "Accept": "application/json"},
            "body": "",
        },
    )

    sent_headers = client.request.call_args.kwargs["headers"]
    marker_keys = [k for k in sent_headers if k.lower() == TUNNEL_HEADER]
    assert marker_keys == [TUNNEL_HEADER], "exactly one marker, stamped by us"
    assert sent_headers[TUNNEL_HEADER] == "1"
    assert sent_headers["Accept"] == "application/json", "other headers untouched"
    await tunnel_handler.stop()


@pytest.mark.asyncio
async def test_proxied_websocket_open_is_marked(tunnel_handler):
    """Nothing on the WS surface trusts loopback today; the marker is there so
    that stays a choice rather than an accident."""
    from server.cloud.tunnel import TunnelConnection

    conn = TunnelConnection(tunnel_id="t-ws", target_port=8080, data_ws=AsyncMock())
    tunnel_handler._tunnels["t-ws"] = conn

    local_ws = AsyncMock()
    local_ws.__aiter__ = lambda self: self
    local_ws.__anext__ = AsyncMock(side_effect=StopAsyncIteration)

    with patch(
        "server.cloud.tunnel.websockets.connect", new=AsyncMock(return_value=local_ws)
    ) as connect:
        await tunnel_handler._handle_ws_open(
            conn,
            {"id": "ws-1", "path": "/ws", "headers": {"X-OpenAVC-Tunneled": "0"}},
        )
        headers = connect.call_args.kwargs["additional_headers"]

    assert [k for k, _ in headers if k.lower() == TUNNEL_HEADER] == [TUNNEL_HEADER]
    assert dict(headers)[TUNNEL_HEADER] == "1"
    await tunnel_handler.stop()


# ===========================================================================
# Host network config: the credential-free grant
# ===========================================================================


@pytest.fixture
def claimed_no_backend(monkeypatch):
    """A claimed instance with no network backend.

    With no backend the route itself 404s, which makes the two outcomes easy to
    tell apart without stubbing nmcli: 401 means the gate refused, 404 means it
    let the caller through to a route that has nothing to offer.
    """
    monkeypatch.setattr(netmod, "get_backend", lambda: None)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "secret-pw-123")
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")


async def _loopback_get(path, headers=None, auth=None):
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.get(path, headers=headers or {}, auth=auth)


@pytest.mark.asyncio
async def test_tunneled_request_cannot_configure_host_network(claimed_no_backend):
    """The finding: a remote caller reconfiguring the host's network with no
    password at all, because the tunnel delivered them over loopback."""
    resp = await _loopback_get("/api/system/network", headers=TUNNEL_HEADERS)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tunneled_write_route_also_refused(claimed_no_backend):
    """The reads are the harmless half — pin a mutating route too."""
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post(
            "/api/system/network/hostname",
            headers=TUNNEL_HEADERS,
            json={"hostname": "attacker-owned"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_console_still_configures_host_network_without_credentials(
    claimed_no_backend,
):
    """The bootstrap path this grant exists for. Breaking it would strand an
    appliance that has no network yet and therefore no way to be reached."""
    resp = await _loopback_get("/api/system/network")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_authenticated_tunneled_caller_keeps_the_capability(claimed_no_backend):
    """Remote host-network config is not removed, only made to cost a
    credential — the tunnel becomes equivalent to being on the LAN."""
    resp = await _loopback_get(
        "/api/system/network", headers=TUNNEL_HEADERS, auth=("admin", "secret-pw-123")
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_forwarded_header_cannot_buy_console_trust(claimed_no_backend, monkeypatch):
    """A reverse proxy also delivers everything from loopback. Console access is
    credential-free, so it must not rest on a header anyone can set."""
    monkeypatch.setattr("server.config.TRUST_FORWARDED_FOR", True)
    resp = await _loopback_get(
        "/api/system/network", headers={"X-Forwarded-For": "203.0.113.9"}
    )
    assert resp.status_code == 401


# /api/setup/status is the other consumer of this anchor; its cases live beside
# their siblings in tests/test_setup_route.py.


# ===========================================================================
# Rate limiting: the unmetered brute-force channel
# ===========================================================================


def _limited_app(status: int = 200) -> FastAPI:
    limited = FastAPI()

    @limited.get("/api/devices")
    async def devices():
        return []

    @limited.get("/api/needs-auth")
    async def needs_auth():
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "nope"}, status_code=status)

    limited.add_middleware(RateLimitMiddleware)
    return limited


def _loopback_client(limited_app) -> TestClient:
    return TestClient(limited_app, client=("127.0.0.1", 5555))


@pytest.fixture(autouse=True)
def _clean_buckets():
    _ip_buckets.clear()
    _warn_dedup.clear()
    yield
    _ip_buckets.clear()
    _warn_dedup.clear()


def test_tunneled_traffic_is_rate_limited():
    client = _loopback_client(_limited_app())
    with patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        assert client.get("/api/devices", headers=TUNNEL_HEADERS).status_code == 200
        assert client.get("/api/devices", headers=TUNNEL_HEADERS).status_code == 429


def test_console_traffic_is_still_exempt():
    """The exemption exists because the primary deployment is one person on the
    box. That must survive the fix."""
    client = _loopback_client(_limited_app())
    with patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 1):
        for _ in range(5):
            assert client.get("/api/devices").status_code == 200
    assert TUNNEL_BUCKET_KEY not in _ip_buckets


def test_tunneled_401s_feed_the_brute_force_counter():
    """The sharp end of the finding: password guessing through the tunnel was
    metered by nothing at all."""
    client = _loopback_client(_limited_app(status=401))
    with patch("server.config.RATE_LIMIT_STRICT_PER_MINUTE", 2):
        assert client.get("/api/needs-auth", headers=TUNNEL_HEADERS).status_code == 401
        assert client.get("/api/needs-auth", headers=TUNNEL_HEADERS).status_code == 401
        # Third attempt is refused by the counter, not by the endpoint.
        assert client.get("/api/needs-auth", headers=TUNNEL_HEADERS).status_code == 429


def test_lan_client_cannot_claim_the_tunnel_bucket():
    """Believing the marker from any source would let any LAN client exhaust
    the tunnel's shared budget and take remote access down."""
    client = TestClient(_limited_app(), client=("203.0.113.9", 5555))
    with patch("server.config.RATE_LIMIT_STANDARD_PER_MINUTE", 2):
        assert client.get("/api/devices", headers=TUNNEL_HEADERS).status_code == 200
    assert TUNNEL_BUCKET_KEY not in _ip_buckets
    assert "203.0.113.9" in _ip_buckets


# ===========================================================================
# Device push — source_ip is a comparison, so it must not be a lie
# ===========================================================================


@pytest.mark.asyncio
async def test_device_push_refuses_tunneled_requests():
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post(
            "/api/push/some-device", headers=TUNNEL_HEADERS, content=b"{}"
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_device_push_from_a_real_device_is_unaffected():
    """No subscription exists for this device, so 404 is the listener's own
    answer — the point is that the request reached it."""
    transport = ASGITransport(app=app, client=("192.0.2.10", 40000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post("/api/push/some-device", content=b"{}")
    assert resp.status_code != 403


# ===========================================================================
# The marker is a header, so make sure it survives a real proxy hop unchanged
# ===========================================================================


@pytest.mark.asyncio
async def test_marker_reaches_the_local_server_as_the_agent_sent_it(tunnel_handler):
    """End-to-end on the agent half: what _handle_http_request hands httpx is
    what a local route would read back out of request.headers."""
    from starlette.requests import Request

    conn, client = _http_conn(tunnel_handler, "t-e2e")
    await tunnel_handler._handle_http_request(
        conn,
        {"id": "r3", "method": "GET", "path": "/api/setup/status", "headers": {}, "body": ""},
    )
    sent = client.request.call_args.kwargs["headers"]

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/setup/status",
        "headers": [(k.lower().encode(), v.encode()) for k, v in sent.items()],
        "client": ("127.0.0.1", 50000),
        "query_string": b"",
    }
    from server.utils.request_origin import is_local_console_request, is_tunneled_request

    assert is_tunneled_request(Request(scope)) is True
    assert is_local_console_request(Request(scope)) is False
    await tunnel_handler.stop()


# ===========================================================================
# Keeping it one authority
# ===========================================================================


# Reading the socket peer is fine when the answer is *data*. It is not fine
# when the answer is *trust*, and the two look identical at the callsite —
# which is how this got shipped. So the peer is off limits by default and the
# exceptions are named here, each with the reason it isn't a trust decision.
_MAY_READ_THE_SOCKET_PEER = {
    # The rate-limit bucket key. Never gates anything; a wrong answer costs a
    # caller a shared budget, not access.
    ("server/middleware/rate_limit.py", "_get_client_ip"),
    # Handed to the push listener as the value it compares against the
    # device's own addresses. The trust decision happens there, and this route
    # refuses tunneled requests before reaching it.
    ("server/api/routes/push.py", "device_push"),
}


def _peer_reads(tree, rel_path):
    """Yield (rel_path, enclosing_function) for every `request.client` read."""
    import ast

    scopes = []

    class Walker(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            scopes.append(node.name)
            self.generic_visit(node)
            scopes.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Attribute(self, node):
            if (
                node.attr == "client"
                and isinstance(node.value, ast.Name)
                and node.value.id in {"request", "websocket", "ws"}
            ):
                found.append((rel_path, scopes[-1] if scopes else "<module>"))
            self.generic_visit(node)

    found = []
    Walker().visit(tree)
    return found


def test_loopback_trust_has_exactly_one_authority():
    """A new route that writes `request.client.host == "127.0.0.1"` reintroduces
    this finding in a place nobody thinks to look. request_origin is the door."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for area in ("server/api", "server/middleware"):
        for path in sorted((root / area).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for site in _peer_reads(tree, rel):
                if site not in _MAY_READ_THE_SOCKET_PEER:
                    offenders.append(site)

    assert not offenders, (
        "These read the request's socket peer directly: "
        + ", ".join(f"{f}:{fn}()" for f, fn in offenders)
        + ". A tunneled request arrives from 127.0.0.1, so the peer does not "
        "answer 'is this local'. Use server.utils.request_origin "
        "(is_local_console_request / is_tunneled_request). If the peer really "
        "is just data here, add the site to _MAY_READ_THE_SOCKET_PEER with the "
        "reason it is not a trust decision."
    )
