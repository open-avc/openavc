"""The proxy keeps one HTTP client, not one per request.

``async with httpx.AsyncClient()`` inside the request handler reads as the
careful thing to write and is the opposite: a client owns the connection pool,
so building one per call means no pooling at all. Measured against a loopback
simulator it cost 199.7 ms per request where a shared client cost 0.9 -- 229x.
Nobody saw it on the shell (a few requests) and everybody saw it on the control
API, where dragging one slider is a write per pixel of travel. The simulator UI
read as broken: controls that did not move, then jumped seconds later.

The regression this guards is subtle, because reintroducing the bug leaves
every behavioural test passing -- it is only slow. So assert the object
identity directly.

The one risk a shared pool introduces is a connection kept alive across a
simulator restart. It is not tested here, deliberately. The behaviour belongs
to httpx rather than to us -- it discards a connection the far end closed and
dials a new one -- and pinning it needs a real socket that is torn down
mid-test, which turned out to hang on Linux for the same reason the case is
interesting: the pooled connection is the one the teardown waits on. It was
verified by hand instead (a server restarted under a live pool answers the
next request normally), and in the field the simulator is a child process
whose sockets close as it exits.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api import simulator_proxy


@pytest.fixture(autouse=True)
async def _fresh_client():
    """Each test starts and ends with no shared client."""
    await simulator_proxy.aclose_client()
    yield
    await simulator_proxy.aclose_client()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(simulator_proxy, "require_programmer_auth", lambda: None)
    app = FastAPI()
    app.include_router(simulator_proxy.router)
    app.include_router(simulator_proxy.open_router)
    return TestClient(app)


@pytest.mark.asyncio
async def test_the_client_is_built_once_and_reused():
    first = simulator_proxy._get_client()
    assert simulator_proxy._get_client() is first, (
        "a new client per call means a new connection pool per call, which is "
        "the same as having no pool"
    )


@pytest.mark.asyncio
async def test_closing_releases_it_and_the_next_call_builds_a_fresh_one():
    first = simulator_proxy._get_client()
    await simulator_proxy.aclose_client()
    assert first.is_closed
    assert simulator_proxy._get_client() is not first


@pytest.mark.asyncio
async def test_closing_twice_is_harmless():
    simulator_proxy._get_client()
    await simulator_proxy.aclose_client()
    await simulator_proxy.aclose_client()


def test_forwarding_many_requests_uses_one_client(client, monkeypatch):
    """The proof through the actual route, not just the accessor."""
    seen = []

    real_request = httpx.AsyncClient.request

    async def spy(self, *args, **kwargs):
        seen.append(id(self))
        return await real_request(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", spy)
    # Port 9 is closed, so each call fails fast and answers 503 -- which is
    # fine: the client is chosen before the connection is attempted.
    monkeypatch.setattr(simulator_proxy, "active_simulator_ui_port", lambda: 9)

    for _ in range(5):
        assert client.get("/simulator/").status_code == 503

    assert len(seen) == 5
    assert len(set(seen)) == 1, f"{len(set(seen))} clients built for 5 requests"


# -- idle connections ------------------------------------------------------
#
# A pooled connection is only safe to reuse while the far end still holds it.
# The simulator's web server closes a connection idle for its keep-alive time,
# and the pool hands one out until it has been idle for its own expiry. With
# both at 5 s (each library's default) and the Simulator UI polling every 5 s,
# the proxy reused a connection at the instant the simulator closed it, now and
# then, and the poll came back 502 with an empty ReadError. So the pool lets go
# well before the simulator does.

KEEP_ALIVE_MARGIN_S = 10.0


def test_the_simulator_keeps_an_idle_connection_for_its_declared_time():
    from openavc.simulator import _runtime
    from openavc.simulator.__main__ import server_config

    config = server_config("127.0.0.1", 19500)
    assert config.timeout_keep_alive == _runtime.HTTP_KEEP_ALIVE_S


def test_the_pool_drops_an_idle_connection_well_before_the_simulator_does(monkeypatch):
    from openavc.simulator import _runtime

    built = []
    real_init = httpx.AsyncClient.__init__

    def spy(self, *args, **kwargs):
        built.append(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy)
    simulator_proxy._get_client()

    limits = built[0]["limits"]
    assert limits.keepalive_expiry is not None
    assert limits.keepalive_expiry + KEEP_ALIVE_MARGIN_S <= _runtime.HTTP_KEEP_ALIVE_S
    # The pool's other limits stay what httpx would have used.
    assert limits.max_connections == 100
    assert limits.max_keepalive_connections == 20


# -- a failure with no text ------------------------------------------------


class _Failing:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def request(self, *args, **kwargs):
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (httpx.ReadTimeout(""), "no answer within 30 s"),
        (httpx.ReadError(""), "ReadError"),
        (httpx.RemoteProtocolError("Server disconnected"), "Server disconnected"),
    ],
)
def test_a_failed_request_says_why_even_when_the_error_has_no_text(
    client, monkeypatch, caplog, exc, reason,
):
    monkeypatch.setattr(simulator_proxy, "_get_client", lambda: _Failing(exc))
    with caplog.at_level("WARNING", logger="openavc.api.simulator_proxy"):
        response = client.get("/simulator/api/devices")

    assert response.status_code == 502
    assert response.text == f"Simulator UI unreachable: {reason}"
    assert f"Simulator UI proxy failed for /api/devices: {reason}" in caplog.text
