"""Tests for the driver push primitive (``push: {type: sse}``).

Platform-feature tests with an INVENTED device (acme_streamer) and synthetic
events per the test policy. Stream-level tests run against a real local
aiohttp server speaking text/event-stream, so connection, parsing,
reconnect/backoff, and teardown are exercised over actual sockets.
"""

import asyncio

import pytest
from aiohttp import web

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition
from server.transport.http_client import HTTPClientTransport


def _make_driver(definition: dict, config: dict | None = None, device_id: str = "dev1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, config or {}, state, events)


def _streamer_def(**overrides) -> dict:
    d = {
        "id": "acme_streamer",
        "name": "Acme Streamer",
        "manufacturer": "Acme",
        "category": "streaming",
        "version": "1.0.0",
        "author": "Test",
        "description": "Invented SSE-push device",
        "transport": "http",
        "source_url": "https://example.com",
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP"},
            "events_path": {"type": "string", "default": "/api/events"},
        },
        "default_config": {
            "host": "",
            "ssl": False,
            "events_path": "/api/events",
        },
        "push": {"type": "sse", "path": "{events_path}"},
        "commands": {
            "query_status": {
                "label": "Query Status",
                "method": "GET",
                "path": "/api/status",
            },
        },
        "state_variables": {
            "level": {"type": "integer", "label": "Level"},
            "muted": {"type": "boolean", "label": "Muted"},
        },
        "responses": [
            {"json": True, "set": {"level": "level", "muted": "muted"}},
        ],
    }
    d.update(overrides)
    return d


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.02)


# ===========================================================================
# Loader validation
# ===========================================================================


def test_loader_accepts_sse_with_template_path():
    assert validate_driver_definition(_streamer_def()) == []


def test_loader_accepts_sse_with_literal_path_list_and_idle_timeout():
    d = _streamer_def()
    d["push"] = {
        "type": "sse",
        "path": ["/api/events", "/api/status"],
        "idle_timeout": 200,
    }
    assert validate_driver_definition(d) == []


@pytest.mark.parametrize(
    "push, expect",
    [
        ({"type": "sse"}, "missing 'path'"),
        ({"type": "sse", "path": []}, "missing 'path'"),
        ({"type": "sse", "path": ""}, "must be a non-empty string"),
        ({"type": "sse", "path": 5}, "must be a string or a list"),
        ({"type": "sse", "path": [""]}, "must be a non-empty string"),
        ({"type": "sse", "path": "api/events"}, "must start with '/'"),
        ({"type": "sse", "path": "{undeclared}"}, "not declared"),
        ({"type": "sse", "path": "/e", "idle_timeout": 0}, "idle_timeout"),
        ({"type": "sse", "path": "/e", "idle_timeout": -5}, "idle_timeout"),
        ({"type": "sse", "path": "/e", "idle_timeout": True}, "idle_timeout"),
        ({"type": "sse", "path": "/e", "idle_timeout": "long"}, "idle_timeout"),
        ({"type": "sse", "path": "/e", "group": "239.0.0.1"}, "unknown key"),
        ({"type": "multicast", "group": "239.0.0.1", "port": 1, "path": "/e"}, "unknown key"),
    ],
)
def test_loader_rejects_bad_sse_blocks(push, expect):
    d = _streamer_def()
    d["push"] = push
    errors = validate_driver_definition(d)
    assert any(expect in e for e in errors), errors


def test_loader_rejects_sse_on_non_http_transport():
    d = _streamer_def(transport="tcp")
    d["push"] = {"type": "sse", "path": "/api/events"}
    errors = validate_driver_definition(d)
    assert any("requires the http transport" in e for e in errors), errors


def test_factory_copies_sse_push_into_driver_info():
    drv = _make_driver(_streamer_def(), {"host": "10.0.0.5"})
    assert drv.DRIVER_INFO["push"]["type"] == "sse"


# ===========================================================================
# Local SSE test server
# ===========================================================================


class _SSEServer:
    """Minimal event-stream device: GET /api/events streams queued bytes."""

    def __init__(self):
        self.connections = 0
        self.reject_status: int | None = None
        self._queues: list[asyncio.Queue] = []
        self._runner: web.AppRunner | None = None
        self.port = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    async def start(self):
        app = web.Application()
        app.router.add_get("/api/events", self._handle)
        # Cancel handlers when the client disconnects (off by default since
        # aiohttp 3.9) so subscriber_count reflects live connections.
        self._runner = web.AppRunner(app, handler_cancellation=True)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        self.drop_all()
        if self._runner:
            await self._runner.cleanup()

    async def _handle(self, request):
        self.connections += 1
        if self.reject_status is not None:
            return web.Response(status=self.reject_status)
        assert "text/event-stream" in request.headers.get("Accept", "")
        resp = web.StreamResponse(
            headers={"Content-Type": "text/event-stream"}
        )
        await resp.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                await resp.write(item)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._queues.remove(queue)
        return resp

    def send_raw(self, data: bytes) -> None:
        for queue in list(self._queues):
            queue.put_nowait(data)

    def drop_all(self) -> None:
        """End every open stream from the server side."""
        for queue in list(self._queues):
            queue.put_nowait(None)


@pytest.fixture
async def sse_server():
    server = _SSEServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def http_transport(sse_server):
    transport = HTTPClientTransport(
        base_url=f"http://127.0.0.1:{sse_server.port}", timeout=2.0
    )
    await transport.open()
    yield transport
    await transport.close()


# ===========================================================================
# SSEEventStream — parsing, reconnect, teardown
# ===========================================================================


@pytest.mark.asyncio
async def test_stream_delivers_event_data(sse_server, http_transport):
    received: list[bytes] = []
    stream = http_transport.open_event_stream("/api/events", received.append)
    await _wait_for(lambda: sse_server.subscriber_count == 1)
    sse_server.send_raw(b'data: {"level": 42}\n\n')
    await _wait_for(lambda: len(received) == 1)
    assert received[0] == b'{"level": 42}'
    await stream.close()


@pytest.mark.asyncio
async def test_stream_assembles_multi_line_data_and_skips_noise(
    sse_server, http_transport
):
    received: list[bytes] = []
    stream = http_transport.open_event_stream("/api/events", received.append)
    await _wait_for(lambda: sse_server.subscriber_count == 1)
    # Keepalive comment, event/id fields, and two data lines in one event.
    sse_server.send_raw(
        b": keepalive\n\n"
        b"event: update\n"
        b"id: 7\n"
        b"data: line one\n"
        b"data: line two\n"
        b"\n"
    )
    await _wait_for(lambda: len(received) == 1)
    assert received[0] == b"line one\nline two"
    await stream.close()


@pytest.mark.asyncio
async def test_stream_reconnects_after_server_drop(sse_server, http_transport):
    received: list[bytes] = []
    stream = http_transport.open_event_stream("/api/events", received.append)
    await _wait_for(lambda: sse_server.subscriber_count == 1)
    sse_server.send_raw(b"data: first\n\n")
    await _wait_for(lambda: len(received) == 1)

    sse_server.drop_all()
    # Backoff after a stream that delivered data restarts at 1 s.
    await _wait_for(lambda: sse_server.connections == 2, timeout=5.0)
    await _wait_for(lambda: sse_server.subscriber_count == 1, timeout=5.0)
    sse_server.send_raw(b"data: second\n\n")
    await _wait_for(lambda: len(received) == 2)
    assert received[1] == b"second"
    await stream.close()


@pytest.mark.asyncio
async def test_stream_retries_after_rejected_status(sse_server, http_transport):
    sse_server.reject_status = 503
    received: list[bytes] = []
    stream = http_transport.open_event_stream("/api/events", received.append)
    # First attempt rejected; retry follows after ~1 s backoff.
    await _wait_for(lambda: sse_server.connections >= 2, timeout=5.0)
    assert received == []
    # Device recovers: the standing retry loop picks it up.
    sse_server.reject_status = None
    await _wait_for(lambda: sse_server.subscriber_count == 1, timeout=10.0)
    sse_server.send_raw(b"data: back\n\n")
    await _wait_for(lambda: len(received) == 1, timeout=5.0)
    await stream.close()


@pytest.mark.asyncio
async def test_stream_close_stops_reconnecting(sse_server, http_transport):
    stream = http_transport.open_event_stream("/api/events", lambda d: None)
    await _wait_for(lambda: sse_server.subscriber_count == 1)
    await stream.close()
    await _wait_for(lambda: sse_server.subscriber_count == 0)
    connections = sse_server.connections
    await asyncio.sleep(0.3)
    assert sse_server.connections == connections


@pytest.mark.asyncio
async def test_transport_close_closes_streams(sse_server, http_transport):
    http_transport.open_event_stream("/api/events", lambda d: None)
    await _wait_for(lambda: sse_server.subscriber_count == 1)
    await http_transport.close()
    await _wait_for(lambda: sse_server.subscriber_count == 0)


@pytest.mark.asyncio
async def test_stream_idle_timeout_forces_reconnect(sse_server, http_transport):
    stream = http_transport.open_event_stream(
        "/api/events", lambda d: None, idle_timeout=0.3
    )
    await _wait_for(lambda: sse_server.subscriber_count == 1)
    # Server stays silent past the idle window → the read times out and the
    # stream reconnects.
    await _wait_for(lambda: sse_server.connections >= 2, timeout=8.0)
    await stream.close()


@pytest.mark.asyncio
async def test_callback_error_does_not_kill_stream(sse_server, http_transport):
    received: list[bytes] = []

    def flaky(data: bytes) -> None:
        if data == b"bad":
            raise ValueError("boom")
        received.append(data)

    stream = http_transport.open_event_stream("/api/events", flaky)
    await _wait_for(lambda: sse_server.subscriber_count == 1)
    sse_server.send_raw(b"data: bad\n\ndata: good\n\n")
    await _wait_for(lambda: len(received) == 1)
    assert received[0] == b"good"
    assert sse_server.connections == 1
    await stream.close()


# ===========================================================================
# Driver lifecycle — subscribe on connect, dispatch, teardown
# ===========================================================================


@pytest.mark.asyncio
async def test_driver_connect_subscribes_and_dispatches_events(sse_server):
    drv = _make_driver(
        _streamer_def(),
        {"host": "127.0.0.1", "port": sse_server.port, "ssl": False,
         "events_path": "/api/events"},
    )
    await drv.connect()
    try:
        await _wait_for(lambda: sse_server.subscriber_count == 1)
        sse_server.send_raw(b'data: {"level": 42, "muted": true}\n\n')
        await _wait_for(lambda: drv.get_state("level") == 42)
        assert drv.get_state("muted") is True
    finally:
        await drv.disconnect()
    await _wait_for(lambda: sse_server.subscriber_count == 0)
    assert drv._push_subscription is None


@pytest.mark.asyncio
async def test_driver_with_multiple_stream_paths(sse_server):
    d = _streamer_def()
    d["push"] = {"type": "sse", "path": ["/api/events", "/api/events"]}
    drv = _make_driver(
        d, {"host": "127.0.0.1", "port": sse_server.port, "ssl": False}
    )
    await drv.connect()
    try:
        await _wait_for(lambda: sse_server.subscriber_count == 2)
        assert isinstance(drv._push_subscription, list)
        assert len(drv._push_subscription) == 2
    finally:
        await drv.disconnect()
    await _wait_for(lambda: sse_server.subscriber_count == 0)


@pytest.mark.asyncio
async def test_driver_unresolved_path_template_is_nonfatal(sse_server):
    d = _streamer_def()
    d["push"] = {"type": "sse", "path": "{missing_field}"}
    drv = _make_driver(
        d, {"host": "127.0.0.1", "port": sse_server.port, "ssl": False}
    )
    await drv.connect()
    try:
        assert drv.connected
        assert drv._push_subscription is None
    finally:
        await drv.disconnect()


# ===========================================================================
# YAMLAutoSimulator — event-stream serving + notification emission
# ===========================================================================


def _free_tcp_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sim_def() -> dict:
    d = _streamer_def()
    d["simulator"] = {
        "initial_state": {"level": 1, "muted": False},
        "notifications": {
            "level": '{"level": {value}}',
            # Booleans render Python's True/False through {value}; per-value
            # templates carry the JSON literals instead.
            "muted": {"true": '{"muted": true}', "false": '{"muted": false}'},
        },
    }
    return d


def test_sim_resolves_sse_paths_from_templates():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=_sim_def())
    assert sim._push_sse_paths == ["/api/events"]

    sim2 = YAMLAutoSimulator(
        device_id="s2",
        config={"events_path": "/custom/stream"},
        driver_def=_sim_def(),
    )
    assert sim2._push_sse_paths == ["/custom/stream"]


def test_sim_without_push_block_has_no_sse_paths():
    from simulator.yaml_auto import YAMLAutoSimulator

    d = _sim_def()
    d.pop("push")
    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=d)
    assert sim._push_sse_paths == []


@pytest.mark.asyncio
async def test_sim_serves_event_stream_and_normal_requests():
    import httpx

    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=_sim_def())
    port = _free_tcp_port()
    await sim.start(port)
    received: list[str] = []

    async def consume():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                f"http://127.0.0.1:{port}/api/events",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(5.0, read=None),
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        received.append(line[5:].strip())

    task = asyncio.create_task(consume())
    try:
        await _wait_for(lambda: len(sim._sse_clients) == 1)
        # Non-protocol state changes (simulator UI / API) render templates.
        sim.set_state("level", 55)
        sim.set_state("muted", True)
        await _wait_for(lambda: len(received) == 2)
        assert received == ['{"level": 55}', '{"muted": true}']

        # Without the Accept header, the same path routes through the normal
        # handler chain (404 — this invented driver declares no handler).
        async with httpx.AsyncClient() as client:
            plain = await client.get(f"http://127.0.0.1:{port}/api/events")
        assert plain.status_code == 404
    finally:
        task.cancel()
        await sim.stop()


@pytest.mark.asyncio
async def test_sim_stop_completes_with_open_subscription():
    import httpx

    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=_sim_def())
    port = _free_tcp_port()
    await sim.start(port)

    async def consume():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                f"http://127.0.0.1:{port}/api/events",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(5.0, read=None),
            ) as response:
                async for _ in response.aiter_lines():
                    pass

    task = asyncio.create_task(consume())
    try:
        await _wait_for(lambda: len(sim._sse_clients) == 1)
        # The None sentinel unblocks the held handler; stop() must not hang.
        await asyncio.wait_for(sim.stop(), timeout=5.0)
    finally:
        task.cancel()


# ===========================================================================
# End-to-end: real driver <-> auto-generated simulator over localhost
# ===========================================================================


@pytest.mark.asyncio
async def test_e2e_driver_state_follows_sim_changes():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=_sim_def())
    port = _free_tcp_port()
    await sim.start(port)
    drv = _make_driver(
        _sim_def(),
        {"host": "127.0.0.1", "port": port, "ssl": False,
         "events_path": "/api/events"},
    )
    try:
        await drv.connect()
        await _wait_for(lambda: len(sim._sse_clients) == 1)
        sim.set_state("level", 77)
        await _wait_for(lambda: drv.get_state("level") == 77)
        sim.set_state("muted", True)
        await _wait_for(lambda: drv.get_state("muted") is True)
    finally:
        await drv.disconnect()
        await sim.stop()
