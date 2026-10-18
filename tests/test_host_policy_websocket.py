"""Observe actual ASGI handshake, cancellation and connection cleanup order."""
import asyncio

from fastapi import FastAPI, WebSocket
import pytest
from starlette.routing import websocket_session

from openavc.api.host_policy import EndpointPolicy, WebSocketPolicy, configure_policy
from openavc.core.host_policy import HostCategory, HostPolicy, PolicySnapshot


class MutableHost(HostPolicy):
    def __init__(self):
        super().__init__()
        self.current = PolicySnapshot(1, True, True)

    def snapshot(self):
        return self.current


def scope(app, headers=()):
    return {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "app": app, "path": "/ws", "raw_path": b"/ws", "root_path": "",
            "query_string": b"", "scheme": "ws", "headers": list(headers),
            "server": ("test", 80), "client": ("127.0.0.1", 12345),
            "http_version": "1.1", "subprotocols": []}


def application(endpoint, category=HostCategory.PANEL):
    app, host = FastAPI(), MutableHost()
    configure_policy(app, host)
    wrapped = WebSocketPolicy(websocket_session(endpoint), EndpointPolicy(category))
    return app, host, wrapped


@pytest.mark.asyncio
async def test_disabled_socket_receives_connect_then_closes_without_entering_endpoint():
    async def endpoint(ws):
        raise AssertionError("Disabled connection reached the endpoint")

    app, host, wrapped = application(endpoint)
    host.current = PolicySnapshot(2, True, False)
    received, sent = [], []

    async def receive():
        assert not received  # Never consume an application frame on refusal.
        received.append(True)
        return {"type": "websocket.connect"}

    async def send(message):
        assert received
        sent.append(message)

    await wrapped(scope(app), receive, send)
    assert [item["type"] for item in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == 4013
    assert (await host.apply_current(timeout=0)).observed


@pytest.mark.asyncio
async def test_revocation_waits_for_endpoint_cleanup_transport_close_and_owned_commit():
    entered, cleaning, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    closing, release_close = asyncio.Event(), asyncio.Event()
    commits, sent = [], []

    async def endpoint(ws: WebSocket):
        await ws.accept()
        commits.append(ws.scope["openavc.host_admission"].begin_commit())
        entered.set()
        try:
            await ws.receive_text()
        finally:
            cleaning.set()
            await release_cleanup.wait()

    app, host, wrapped = application(endpoint)
    queue = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})

    async def send(message):
        sent.append(message)
        if message["type"] == "websocket.close":
            closing.set()
            await release_close.wait()

    task = asyncio.create_task(wrapped(scope(app), queue.get, send))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        host.current = PolicySnapshot(2, True, False)
        pending = await host.apply_current(timeout=0)
        assert pending.pending_connections == pending.pending_commits == 1
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not (await host.apply_current(timeout=0)).observed
        release_cleanup.set()
        await asyncio.wait_for(closing.wait(), 1)
        assert (await host.apply_current(timeout=0)).pending_connections == 1
        release_close.set()
        await asyncio.wait_for(task, 1)
        pending = await host.apply_current(timeout=0)
        assert pending.pending_connections == 0 and pending.pending_commits == 1
        commits[0].finish("committed")
        assert (await host.apply_current(timeout=0)).observed
        assert sent[-1]["code"] == 4013
    finally:
        release_cleanup.set()
        release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_runtime_socket_keeps_control_when_optional_interfaces_are_disabled():
    entered, messages, sent = asyncio.Event(), [], []

    async def endpoint(ws: WebSocket):
        await ws.accept()
        entered.set()
        messages.append(await ws.receive_text())
        await ws.send_text("acknowledged")
        await ws.close(code=1000)

    app, host, wrapped = application(endpoint, HostCategory.RUNTIME)
    queue = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(wrapped(scope(app), queue.get, send))
    await asyncio.wait_for(entered.wait(), 1)
    host.current = PolicySnapshot(2, False, False)
    assert (await host.apply_current(timeout=0)).observed
    await queue.put({"type": "websocket.receive", "text": "control"})
    await asyncio.wait_for(task, 1)
    assert messages == ["control"]
    assert sent[-2] == {"type": "websocket.send", "text": "acknowledged"}
    assert sent[-1]["code"] == 1000


@pytest.mark.asyncio
async def test_external_cancellation_propagates_without_finishing_owned_commit():
    entered, commits = asyncio.Event(), []

    async def endpoint(ws: WebSocket):
        await ws.accept()
        commits.append(ws.scope["openavc.host_admission"].begin_commit())
        entered.set()
        await ws.receive_text()

    app, host, wrapped = application(endpoint)
    queue = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})

    async def send(message):
        pass

    task = asyncio.create_task(wrapped(scope(app), queue.get, send))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    host.current = PolicySnapshot(2, False, False)
    pending = await host.apply_current(timeout=0)
    assert pending.pending_connections == 0 and pending.pending_commits == 1
    commits[0].finish("cancelled before publication")


@pytest.mark.asyncio
async def test_invalid_registered_resolution_cannot_become_runtime():
    async def endpoint(ws):
        raise AssertionError("Invalid category reached the endpoint")

    app, host, wrapped = application(endpoint, lambda scope: "unregistered")
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    await wrapped(scope(app), receive, send)
    assert sent[-1]["code"] == 4013
    assert (await host.apply_current(timeout=0)).observed
