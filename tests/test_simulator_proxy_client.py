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
"""

import asyncio
import contextlib

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
    monkeypatch.setattr(simulator_proxy, "simulator_ui_port", lambda: 9)

    for _ in range(5):
        assert client.get("/simulator/").status_code == 503

    assert len(seen) == 5
    assert len(set(seen)) == 1, f"{len(set(seen))} clients built for 5 requests"


@pytest.mark.asyncio
async def test_a_pooled_connection_survives_the_simulator_restarting_under_it():
    """Keep-alive plus a child process that stops and starts is the one risk a
    shared pool introduces, so pin the behaviour rather than trusting it: the
    simulator closes its sockets as it dies, and httpx discards a connection
    the far end closed and dials again."""

    async def start(port):
        async def handle(reader, writer):
            with contextlib.suppress(Exception):
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
        return await asyncio.start_server(handle, "127.0.0.1", port, reuse_address=True)

    port = 19833
    url = f"http://127.0.0.1:{port}/x"
    c = simulator_proxy._get_client()

    server = await start(port)
    assert (await c.get(url)).status_code == 200   # connection now pooled
    server.close()
    await server.wait_closed()
    await asyncio.sleep(0.2)

    server = await start(port)
    try:
        assert (await c.get(url)).status_code == 200, (
            "a stale pooled connection reached a caller"
        )
    finally:
        server.close()
        await server.wait_closed()
