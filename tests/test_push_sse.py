"""Tests for the driver push primitive (``push: {type: sse}``).

Platform-feature tests with an INVENTED device (acme_streamer) and synthetic
events per the test policy. Stream-level tests run against a real local
aiohttp server speaking text/event-stream, so connection, parsing,
reconnect/backoff, and teardown are exercised over actual sockets.
"""

import asyncio

import pytest
from aiohttp import web

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.transport.http_client import HTTPClientTransport


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
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=_sim_def())
    assert sim.sse_paths == ["/api/events"]

    sim2 = YAMLAutoSimulator(
        device_id="s2",
        config={"events_path": "/custom/stream"},
        driver_def=_sim_def(),
    )
    assert sim2.sse_paths == ["/custom/stream"]


def test_sim_without_push_block_has_no_sse_paths():
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    d = _sim_def()
    d.pop("push")
    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=d)
    assert sim.sse_paths == []


@pytest.mark.asyncio
async def test_sim_serves_event_stream_and_normal_requests():
    import httpx

    from openavc.simulator.yaml_auto import YAMLAutoSimulator

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

    from openavc.simulator.yaml_auto import YAMLAutoSimulator

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
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

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


# ===========================================================================
# Session shape: the device names the stream, the driver arms it
# ===========================================================================


def _session_def(**overrides) -> dict:
    """An invented device whose event stream is a session it names: the
    reply carries Content-Location, the opening event repeats the id, a PUT
    of a resource list to the session arms it, and a close event ends it."""
    d = _streamer_def()
    d["config_schema"]["enable_meters"] = {
        "type": "boolean", "label": "Stream meters", "default": False,
    }
    d["default_config"]["enable_meters"] = False
    d["push"] = {
        "type": "sse",
        "path": "/api/subscriptions",
        "session": {
            "header": "Content-Location",
            "event": "open",
            "key": "sessionUUID",
            "pattern": "([0-9a-fA-F-]{36})$",
            "close_event": "close",
        },
        "register": [
            "subscribe_status",
            {"command": "subscribe_meters", "when": "enable_meters"},
        ],
        "unregister": "end_session",
    }
    d["commands"].update({
        "subscribe_status": {
            "label": "Subscribe status",
            "method": "PUT",
            "path": "/api/subscriptions/{push_session}",
            "body": '["/api/status"]',
        },
        "subscribe_meters": {
            "label": "Subscribe meters",
            "method": "PUT",
            "path": "/api/subscriptions/{push_session}/add",
            "body": '["/api/meters"]',
        },
        "end_session": {
            "label": "End session",
            "method": "DELETE",
            "path": "/api/subscriptions/{push_session}",
        },
    })
    d.update(overrides)
    return d


def test_loader_accepts_session_block_with_register_forms():
    assert validate_driver_definition(_session_def()) == []


def test_loader_accepts_session_from_header_only():
    d = _session_def()
    d["push"]["session"] = {"header": "Content-Location"}
    assert validate_driver_definition(d) == []


def test_loader_accepts_session_from_event_only():
    d = _session_def()
    d["push"]["session"] = {"event": "open", "key": "sessionUUID"}
    assert validate_driver_definition(d) == []


def test_loader_accepts_each_child_register_entry():
    d = _session_def()
    d["child_entity_types"] = {
        "channel": {
            "id_format": {"type": "integer", "min": 1, "max": 4},
            "state_variables": {"mute": {"type": "boolean"}},
            "instances": {"count": 2},
        }
    }
    d["commands"]["subscribe_channel"] = {
        "label": "Subscribe channel",
        "method": "PUT",
        "path": "/api/subscriptions/{push_session}/add",
        "body": '["/api/channel/{channel}"]',
        "params": {"channel": {"type": "child_id", "child_type": "channel"}},
    }
    d["push"]["register"] = [
        {"command": "subscribe_channel", "each_child": "channel"},
    ]
    assert validate_driver_definition(d) == []


@pytest.mark.parametrize(
    "push, expect",
    [
        ({"type": "sse", "path": "/e", "session": "sid"}, "session must be a mapping"),
        ({"type": "sse", "path": "/e", "session": {"pattern": "x"}}, "needs a source"),
        ({"type": "sse", "path": "/e", "session": {"key": "id"}}, "needs 'event'"),
        ({"type": "sse", "path": "/e", "session": {"header": "H", "cookie": 1}}, "unknown key"),
        ({"type": "sse", "path": "/e", "session": {"header": "H", "pattern": "("}}, "invalid regex"),
        ({"type": "sse", "path": ["/e", "/f"], "session": {"header": "H"}}, "single event-stream path"),
        ({"type": "sse", "path": "/e", "register": "arm"}, "not declared"),
        ({"type": "sse", "path": "/e", "register": []}, "must not be empty"),
        ({"type": "sse", "path": "/e", "register": [{"command": "query_status", "when": "nope"}]}, "when 'nope'"),
        ({"type": "sse", "path": "/e", "register": [{"command": "query_status", "each_child": "zone"}]}, "not a declared child"),
        ({"type": "sse", "path": "/e", "unregister": "bye"}, "not declared"),
    ],
)
def test_loader_rejects_bad_session_blocks(push, expect):
    d = _streamer_def()
    d["push"] = push
    errors = validate_driver_definition(d)
    assert any(expect in e for e in errors), errors


class _SessionServer(_SSEServer):
    """The invented session device: names each stream, records what the
    driver registers against it, and can close a session from its side."""

    def __init__(self):
        super().__init__()
        self.sessions: list[str] = []
        self.registrations: list[tuple[str, str, str, list]] = []  # method, sid, path, body
        self.header_on = True
        self.open_event_on = True
        self.header_value = None  # override the Content-Location value
        self._session_queues: dict[str, asyncio.Queue] = {}

    async def start(self):
        app = web.Application()
        app.router.add_get("/api/subscriptions", self._handle)
        app.router.add_put("/api/subscriptions/{sid}", self._register)
        app.router.add_put("/api/subscriptions/{sid}/add", self._register)
        app.router.add_delete("/api/subscriptions/{sid}", self._register)
        self._runner = web.AppRunner(app, handler_cancellation=True)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def _register(self, request):
        sid = request.match_info["sid"]
        body = await request.json() if request.can_read_body else []
        self.registrations.append((request.method, sid, request.path, body))
        if sid not in self.sessions:
            return web.Response(status=422)
        return web.Response(status=200)

    async def _handle(self, request):
        import uuid

        self.connections += 1
        if self.reject_status is not None:
            return web.Response(status=self.reject_status)
        sid = str(uuid.uuid4())
        self.sessions.append(sid)
        headers = {"Content-Type": "text/event-stream"}
        if self.header_on:
            headers["Content-Location"] = (
                self.header_value
                if self.header_value is not None
                else f"/api/subscriptions/{sid}"
            )
        resp = web.StreamResponse(headers=headers)
        await resp.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.append(queue)
        self._session_queues[sid] = queue
        try:
            if self.open_event_on:
                await resp.write(
                    f'event: open\ndata: {{"path": "/api/subscriptions/{sid}", '
                    f'"sessionUUID": "{sid}"}}\n\n'.encode()
                )
            while True:
                item = await queue.get()
                if item is None:
                    break
                await resp.write(item)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._queues.remove(queue)
            self._session_queues.pop(sid, None)
        return resp

    def close_session(self, sid: str) -> None:
        """Send the close event, then end the stream (what a device does
        before a reboot)."""
        queue = self._session_queues.get(sid)
        if queue is not None:
            queue.put_nowait(b"event: close\ndata: {}\n\n")
            queue.put_nowait(None)

    def close_event_only(self, sid: str) -> None:
        """Send the close event and keep the connection open — a device
        that ends the session without dropping the socket."""
        queue = self._session_queues.get(sid)
        if queue is not None:
            queue.put_nowait(b"event: close\ndata: {}\n\n")


@pytest.fixture
async def session_server():
    server = _SessionServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def session_transport(session_server):
    transport = HTTPClientTransport(
        base_url=f"http://127.0.0.1:{session_server.port}", timeout=2.0
    )
    await transport.open()
    yield transport
    await transport.close()


_SESSION = {
    "header": "Content-Location",
    "event": "open",
    "key": "sessionUUID",
    "pattern": "([0-9a-fA-F-]{36})$",
    "close_event": "close",
}


@pytest.mark.asyncio
async def test_stream_reads_session_from_header_and_swallows_open_event(
    session_server, session_transport
):
    received: list[bytes] = []
    sessions: list = []
    stream = session_transport.open_event_stream(
        "/api/subscriptions", received.append,
        session=_SESSION, on_session=sessions.append,
    )
    await _wait_for(lambda: len(sessions) == 1)
    assert sessions[0] == session_server.sessions[0]
    assert stream.session_id == session_server.sessions[0]
    # The opening event named the session again; it never reaches the
    # response callback, and it does not restart the session.
    session_server.send_raw(b'data: {"level": 3}\n\n')
    await _wait_for(lambda: len(received) == 1)
    assert received == [b'{"level": 3}']
    assert sessions == [session_server.sessions[0]]
    await stream.close()


@pytest.mark.asyncio
async def test_stream_falls_back_to_open_event_when_header_absent(
    session_server, session_transport
):
    session_server.header_on = False
    sessions: list = []
    stream = session_transport.open_event_stream(
        "/api/subscriptions", lambda d: None,
        session=_SESSION, on_session=sessions.append,
    )
    await _wait_for(lambda: len(sessions) == 1)
    assert sessions[0] == session_server.sessions[0]
    await stream.close()


@pytest.mark.asyncio
async def test_stream_pattern_reads_bare_and_path_forms(
    session_server, session_transport
):
    # The OpenAPI example header carries a bare UUID; the spec says a path.
    # The same pattern reads both.
    session_server.open_event_on = False
    session_server.header_value = "bare-value-31875a94-29e6-4fb0-ab4d-7f0bbd6e1bc8"
    sessions: list = []
    stream = session_transport.open_event_stream(
        "/api/subscriptions", lambda d: None,
        session=_SESSION, on_session=sessions.append,
    )
    await _wait_for(lambda: len(sessions) == 1)
    assert sessions[0] == "31875a94-29e6-4fb0-ab4d-7f0bbd6e1bc8"
    await stream.close()


@pytest.mark.asyncio
async def test_stream_without_pattern_takes_whole_value(
    session_server, session_transport
):
    session_server.open_event_on = False
    session_server.header_value = "session-42"
    sessions: list = []
    stream = session_transport.open_event_stream(
        "/api/subscriptions", lambda d: None,
        session={"header": "Content-Location"}, on_session=sessions.append,
    )
    await _wait_for(lambda: len(sessions) == 1)
    assert sessions[0] == "session-42"
    await stream.close()


@pytest.mark.asyncio
async def test_stream_close_event_reopens_and_renames_session(
    session_server, session_transport
):
    sessions: list = []
    stream = session_transport.open_event_stream(
        "/api/subscriptions", lambda d: None,
        session=_SESSION, on_session=sessions.append,
    )
    await _wait_for(lambda: len(sessions) == 1)
    first = sessions[0]
    # The device ends the session but keeps the socket: the stream must not
    # sit on the dead session until an idle timeout.
    session_server.close_event_only(first)
    await _wait_for(lambda: len(sessions) >= 3, timeout=5.0)
    assert sessions[1] is None
    assert sessions[2] == session_server.sessions[1]
    assert sessions[2] != first
    assert stream.session_id == sessions[2]
    await stream.close()


@pytest.mark.asyncio
async def test_stream_end_of_connection_ends_session(
    session_server, session_transport
):
    sessions: list = []
    stream = session_transport.open_event_stream(
        "/api/subscriptions", lambda d: None,
        session=_SESSION, on_session=sessions.append,
    )
    await _wait_for(lambda: len(sessions) == 1)
    session_server.drop_all()
    await _wait_for(lambda: len(sessions) >= 3, timeout=5.0)
    assert sessions[1] is None
    assert sessions[2] == session_server.sessions[1]
    await stream.close()


@pytest.mark.asyncio
async def test_stream_without_session_block_delivers_typed_events(
    session_server, session_transport
):
    # No session declared: every event's data is delivered, the opening
    # event included — the pre-session behaviour, unchanged.
    received: list[bytes] = []
    stream = session_transport.open_event_stream(
        "/api/subscriptions", received.append
    )
    await _wait_for(lambda: len(received) == 1)
    assert b"sessionUUID" in received[0]
    assert stream.session_id is None
    await stream.close()


# --- Driver lifecycle: register against the session, re-arm, unregister ---


@pytest.mark.asyncio
async def test_driver_registers_against_session_and_ends_it(session_server):
    drv = _make_driver(
        _session_def(),
        {"host": "127.0.0.1", "port": session_server.port, "ssl": False,
         "enable_meters": False},
    )
    await drv.connect()
    try:
        await _wait_for(lambda: len(session_server.registrations) == 1)
        method, sid, path, body = session_server.registrations[0]
        assert (method, path, body) == (
            "PUT", f"/api/subscriptions/{sid}", ["/api/status"]
        )
        assert sid == session_server.sessions[0]
        assert drv.push_session == sid
        # The meter entry is gated off by config: never sent.
        await asyncio.sleep(0.2)
        assert len(session_server.registrations) == 1
        # Registered state arrives as an ordinary event.
        session_server.send_raw(b'data: {"level": 9, "muted": false}\n\n')
        await _wait_for(lambda: drv.get_state("level") == 9)
    finally:
        await drv.disconnect()
    # The unregister ran while the session id was still known.
    assert session_server.registrations[-1][0] == "DELETE"
    assert session_server.registrations[-1][1] == session_server.sessions[0]
    assert drv.push_session == ""


@pytest.mark.asyncio
async def test_driver_when_gated_register_entry_runs_when_enabled(session_server):
    drv = _make_driver(
        _session_def(),
        {"host": "127.0.0.1", "port": session_server.port, "ssl": False,
         "enable_meters": True},
    )
    await drv.connect()
    try:
        await _wait_for(lambda: len(session_server.registrations) == 2)
        paths = [r[2] for r in session_server.registrations]
        sid = session_server.sessions[0]
        assert paths == [
            f"/api/subscriptions/{sid}", f"/api/subscriptions/{sid}/add",
        ]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_driver_re_registers_on_every_reopen(session_server):
    drv = _make_driver(
        _session_def(),
        {"host": "127.0.0.1", "port": session_server.port, "ssl": False},
    )
    await drv.connect()
    try:
        await _wait_for(lambda: len(session_server.registrations) == 1)
        session_server.close_session(session_server.sessions[0])
        await _wait_for(lambda: len(session_server.registrations) == 2, timeout=5.0)
        assert session_server.registrations[1][1] == session_server.sessions[1]
        assert drv.push_session == session_server.sessions[1]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_driver_each_child_register_fans_out_per_child(session_server):
    d = _session_def()
    d["child_entity_types"] = {
        "channel": {
            "id_format": {"type": "integer", "min": 1, "max": 4},
            "state_variables": {"mute": {"type": "boolean"}},
            "instances": {"count_from": "channel_count"},
        }
    }
    d["config_schema"]["channel_count"] = {"type": "integer", "default": 2}
    d["default_config"]["channel_count"] = 2
    d["commands"]["subscribe_channel"] = {
        "label": "Subscribe channel",
        "method": "PUT",
        "path": "/api/subscriptions/{push_session}/add",
        "body": '["/api/channel/{channel}"]',
        "params": {
            "channel": {
                "type": "child_id", "child_type": "channel",
                "map": {"1": 0, "2": 1, "3": 2, "4": 3},
            }
        },
    }
    d["push"]["register"] = [
        "subscribe_status",
        {"command": "subscribe_channel", "each_child": "channel"},
    ]
    drv = _make_driver(
        d, {"host": "127.0.0.1", "port": session_server.port, "ssl": False,
            "channel_count": 2},
    )
    await drv.connect()
    try:
        await _wait_for(lambda: len(session_server.registrations) == 3)
        bodies = [r[3] for r in session_server.registrations]
        # One PUT per registered child, the local id mapped to the wire index.
        assert bodies == [["/api/status"], ["/api/channel/0"], ["/api/channel/1"]]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_driver_register_failure_is_logged_not_fatal(session_server, caplog):
    d = _session_def()
    d["commands"]["subscribe_status"]["path"] = "/api/nowhere/{push_session}"
    drv = _make_driver(
        d, {"host": "127.0.0.1", "port": session_server.port, "ssl": False},
    )
    await drv.connect()
    try:
        await _wait_for(lambda: len(session_server.sessions) == 1)
        await asyncio.sleep(0.3)
        assert drv.connected
        assert drv.push_session == session_server.sessions[0]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_driver_token_is_withdrawn_between_sessions(session_server):
    drv = _make_driver(
        _session_def(),
        {"host": "127.0.0.1", "port": session_server.port, "ssl": False},
    )
    await drv.connect()
    try:
        await _wait_for(lambda: drv.push_session != "")
        session_server.reject_status = 503
        session_server.drop_all()
        await _wait_for(lambda: drv.push_session == "", timeout=5.0)
        assert drv._push_params() == {}
    finally:
        await drv.disconnect()


# --- YAMLAutoSimulator: a named session on the served stream ---


def _session_sim_def() -> dict:
    d = _session_def()
    d["simulator"] = {
        "initial_state": {"level": 1, "muted": False},
        "notifications": {
            "level": '{"level": {value}}',
            "muted": {"true": '{"muted": true}', "false": '{"muted": false}'},
        },
        "command_handlers": [
            # The device answers a subscription with the current value of
            # every listed resource, on the stream.
            {
                "match": r"PUT /api/subscriptions/[0-9a-f-]+\|(.*)",
                "handler": (
                    "paths = json.loads(match.group(1))\n"
                    "state['subscribed'] = ','.join(paths)\n"
                    "if '/api/status' in paths:\n"
                    "    notify(json.dumps({'level': state['level'], "
                    "'muted': state['muted']}))\n"
                    "respond('')\n"
                ),
            },
            {"match": r"DELETE /api/subscriptions/[0-9a-f-]+", "respond": ""},
        ],
    }
    return d


def test_sim_resolves_session_block():
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=_session_sim_def())
    assert sim.sse_paths == ["/api/subscriptions"]
    assert sim.sse_session == _SESSION
    plain = YAMLAutoSimulator(device_id="s2", config={}, driver_def=_sim_def())
    assert plain.sse_session is None


@pytest.mark.asyncio
async def test_sim_names_session_in_header_and_opening_event():
    import json as _json

    import httpx

    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=_session_sim_def())
    port = _free_tcp_port()
    await sim.start(port)
    seen: dict = {}

    async def consume():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", f"http://127.0.0.1:{port}/api/subscriptions",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(5.0, read=None),
            ) as response:
                seen["header"] = response.headers.get("content-location", "")
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        seen.setdefault("events", []).append((event, line[5:].strip()))
                        event = ""

    task = asyncio.create_task(consume())
    try:
        await _wait_for(lambda: seen.get("events"))
        sid = seen["header"].rsplit("/", 1)[-1]
        assert seen["header"] == f"/api/subscriptions/{sid}"
        assert len(sid) == 36
        event, data = seen["events"][0]
        assert event == "open"
        assert _json.loads(data)["sessionUUID"] == sid
        # A closing event ends the stream the way the device does.
        sim.close_sse_sessions()
        await _wait_for(lambda: len(seen["events"]) == 2)
        assert seen["events"][1][0] == "close"
    finally:
        task.cancel()
        await sim.stop()


@pytest.mark.asyncio
async def test_e2e_session_driver_registers_and_syncs_from_auto_sim():
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    d = _session_sim_def()
    sim = YAMLAutoSimulator(device_id="s1", config={}, driver_def=d)
    port = _free_tcp_port()
    await sim.start(port)
    drv = _make_driver(
        d, {"host": "127.0.0.1", "port": port, "ssl": False,
            "poll_interval": 0},
    )
    try:
        await drv.connect()
        # The registration ran against the session the simulator named, and
        # the simulator answered it with the current values on the stream.
        await _wait_for(lambda: sim.get_state("subscribed") == "/api/status")
        await _wait_for(lambda: drv.get_state("level") == 1)
        assert drv.push_session
        # A later change reaches the driver as a notification.
        sim.set_state("level", 42)
        await _wait_for(lambda: drv.get_state("level") == 42)
        # The device ends the session: a new one is named and registered.
        sim.close_sse_sessions()
        sim.set_state("level", 43)
        await _wait_for(lambda: drv.get_state("level") == 43, timeout=8.0)
    finally:
        await drv.disconnect()
        await sim.stop()
