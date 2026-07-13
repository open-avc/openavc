"""Tests for the driver push primitive (``push: {type: tcp_listener}``).

Platform-feature tests with an INVENTED device (acme_cam) and synthetic
frames per the test policy. The device shape under test: an HTTP-controlled
camera that dials OUT to a TCP port the driver registered (via a command
carrying the ``{listener_port}`` token) and pushes notifications wrapped in
a fixed-structure binary container (reserve + length + reserve + payload +
reserve).
"""

import asyncio
import socket

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition
from server.transport import tcp_listener as tl
from server.transport.frame_parsers import build_frame_parser


def _make_driver(definition: dict, config: dict | None = None, device_id: str = "cam1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, config or {}, state, events)


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


_FRAME_CFG = {
    "type": "struct_frame",
    "header_reserve": 6,
    "length_size": 2,
    "length_endian": "big",
    "length_adjust": -8,
    "mid_reserve": 4,
    "trailer_reserve": 10,
}


def _wrap(payload: bytes) -> bytes:
    """Build a synthetic dial-back container around a payload."""
    return (
        bytes(6)
        + (len(payload) + 8).to_bytes(2, "big")
        + bytes(4)
        + payload
        + bytes(10)
    )


def _cam_def(**overrides) -> dict:
    d = {
        "id": "acme_cam",
        "name": "Acme Camera",
        "manufacturer": "Acme",
        "category": "camera",
        "version": "1.0.0",
        "author": "Test",
        "description": "Invented dial-back push camera",
        "transport": "http",
        "source_url": "https://example.com",
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP"},
            "notify_port": {"type": "integer", "default": 0},
        },
        "default_config": {
            "host": "",
            "notify_port": 0,
        },
        "push": {
            "type": "tcp_listener",
            "port": "{notify_port}",
            "frame_parser": dict(_FRAME_CFG),
            "register": "start_notifications",
            "unregister": "stop_notifications",
        },
        "commands": {
            "start_notifications": {
                "label": "Start Notifications",
                "method": "GET",
                "path": "/api/event?connect=start&my_port={listener_port}",
            },
            "stop_notifications": {
                "label": "Stop Notifications",
                "method": "GET",
                "path": "/api/event?connect=stop&my_port={listener_port}",
            },
        },
        "state_variables": {
            "power": {"type": "integer", "label": "Power"},
            "preset": {"type": "integer", "label": "Preset"},
        },
        "responses": [
            {"match": r"NOTIFY POWER (\d)", "set": {"power": "$1"}},
            {"match": r"NOTIFY PRESET (\d+)", "set": {"preset": "$1"}},
        ],
    }
    d.update(overrides)
    return d


@pytest.fixture(autouse=True)
async def _clean_registry():
    yield
    await tl.close_all()


# ===========================================================================
# Loader validation
# ===========================================================================


def test_loader_accepts_valid_tcp_listener_block():
    assert validate_driver_definition(_cam_def()) == []


def test_loader_accepts_literal_port_and_bare_block():
    d = _cam_def()
    d["push"] = {"type": "tcp_listener", "port": 31004}
    assert validate_driver_definition(d) == []


def test_loader_accepts_port_zero_ephemeral():
    d = _cam_def()
    d["push"] = {"type": "tcp_listener", "port": 0}
    assert validate_driver_definition(d) == []


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (lambda p: p.pop("port"), "missing 'port'"),
        (lambda p: p.update(port=-1), "port must be"),
        (lambda p: p.update(port=65536), "port must be"),
        (lambda p: p.update(port=True), "port must be"),
        (lambda p: p.update(port="{undeclared_field}"), "not declared"),
        (lambda p: p.update(group="239.0.0.1"), "unknown key"),
        (lambda p: p.update(frame_parser="struct_frame"), "must be a mapping"),
        (
            lambda p: p.update(frame_parser={"type": "bogus"}),
            "must be struct_frame",
        ),
        (
            lambda p: p.update(
                frame_parser={"type": "struct_frame", "header_reserve": -1}
            ),
            "non-negative integer",
        ),
        (
            lambda p: p.update(
                frame_parser={"type": "struct_frame", "length_size": 3}
            ),
            "length_size must be",
        ),
        (
            lambda p: p.update(
                frame_parser={"type": "struct_frame", "length_adjust": "x"}
            ),
            "length_adjust must be",
        ),
        (
            lambda p: p.update(
                frame_parser={"type": "struct_frame", "length_endian": "middle"}
            ),
            "length_endian must be",
        ),
        (lambda p: p.update(register="no_such_command"), "not declared in commands"),
        (lambda p: p.update(unregister=7), "must be a command name"),
    ],
)
def test_loader_rejects_bad_tcp_listener_blocks(mutate, expect):
    d = _cam_def()
    mutate(d["push"])
    errors = validate_driver_definition(d)
    assert any(expect in e for e in errors), errors


def test_loader_accepts_http_listener_and_rejects_tcp_keys_on_it():
    """http_listener ships alongside tcp_listener — it is a real type, but it
    takes none of the dial-back keys (the platform assigns its callback URL)."""
    d = _cam_def()
    d["push"] = {"type": "http_listener"}
    assert validate_driver_definition(d) == []

    d["push"] = {"type": "http_listener", "port": 31004}
    errors = validate_driver_definition(d)
    assert any("unknown key" in e for e in errors), errors


def test_factory_copies_push_into_driver_info():
    drv = _make_driver(_cam_def(), {"host": "10.0.0.5"})
    assert drv.DRIVER_INFO["push"]["type"] == "tcp_listener"


# ===========================================================================
# Listener registry — sharing, refcounting, demux, framing
# ===========================================================================


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


async def _dial(port: int, data: bytes, chunks: int = 1) -> None:
    """Mimic a device notification: connect, write, close."""
    _, writer = await asyncio.open_connection("127.0.0.1", port)
    if chunks <= 1:
        writer.write(data)
        await writer.drain()
    else:
        step = max(1, len(data) // chunks)
        for i in range(0, len(data), step):
            writer.write(data[i : i + step])
            await writer.drain()
            await asyncio.sleep(0.01)
    writer.close()


@pytest.mark.asyncio
async def test_registry_shares_listener_and_refcounts():
    port = _free_tcp_port()
    sub_a = await tl.subscribe(port, "127.0.0.1", lambda d, a: None, "a")
    sub_b = await tl.subscribe(port, "127.0.0.1", lambda d, a: None, "b")

    assert len(tl._registry._listeners) == 1
    assert sub_a.port == port and sub_b.port == port

    await sub_a.close()
    assert port in tl._registry._listeners  # b still holds it

    await sub_b.close()
    assert port not in tl._registry._listeners


@pytest.mark.asyncio
async def test_registry_ephemeral_port_zero_gets_own_listener():
    sub_a = await tl.subscribe(0, "127.0.0.1", lambda d, a: None, "a")
    sub_b = await tl.subscribe(0, "127.0.0.1", lambda d, a: None, "b")
    assert sub_a.port > 0 and sub_b.port > 0
    assert sub_a.port != sub_b.port
    assert len(tl._registry._listeners) == 2
    await sub_a.close()
    await sub_b.close()
    assert not tl._registry._listeners


@pytest.mark.asyncio
async def test_frames_delivered_from_matching_source():
    port = _free_tcp_port()
    got: list[bytes] = []
    sub = await tl.subscribe(port, "127.0.0.1", lambda d, a: got.append(d), "cam")
    await _dial(port, b"hello")
    await _wait_for(lambda: got == [b"hello"])
    await sub.close()


@pytest.mark.asyncio
async def test_connection_from_unmatched_source_is_closed_undelivered():
    port = _free_tcp_port()
    got: list[bytes] = []
    sub = await tl.subscribe(port, "203.0.113.9", lambda d, a: got.append(d), "cam")
    # Local connections don't match the remote-only source filter; the
    # listener closes them immediately (EOF, or a reset if data raced in).
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"intruder")
    try:
        eof = await asyncio.wait_for(reader.read(), timeout=2.0)
        assert eof == b""
    except (ConnectionResetError, BrokenPipeError):
        pass
    writer.close()
    await asyncio.sleep(0.05)
    assert got == []
    await sub.close()


@pytest.mark.asyncio
async def test_struct_frames_parsed_per_connection():
    port = _free_tcp_port()
    got: list[bytes] = []
    sub = await tl.subscribe(
        port,
        "127.0.0.1",
        lambda d, a: got.append(d),
        "cam",
        frame_parser_factory=lambda: build_frame_parser(_FRAME_CFG),
    )
    # One frame split across writes, then two frames in one connection.
    await _dial(port, _wrap(b"\r\nNOTIFY POWER 1\r\n"), chunks=4)
    await _dial(port, _wrap(b"\r\nA\r\n") + _wrap(b"\r\nB\r\n"))
    await _wait_for(lambda: len(got) == 3)
    assert got == [b"\r\nNOTIFY POWER 1\r\n", b"\r\nA\r\n", b"\r\nB\r\n"]
    await sub.close()


@pytest.mark.asyncio
async def test_fresh_parser_per_connection():
    """A connection that dies mid-frame must not poison the next connection's
    framing (real dial-back devices open one connection per notification)."""
    port = _free_tcp_port()
    got: list[bytes] = []
    sub = await tl.subscribe(
        port,
        "127.0.0.1",
        lambda d, a: got.append(d),
        "cam",
        frame_parser_factory=lambda: build_frame_parser(_FRAME_CFG),
    )
    # Half a frame, then the connection drops.
    await _dial(port, _wrap(b"\r\nTRUNCATED\r\n")[:9])
    await asyncio.sleep(0.05)
    # A complete frame on a new connection parses cleanly.
    await _dial(port, _wrap(b"\r\nNOTIFY POWER 0\r\n"))
    await _wait_for(lambda: got == [b"\r\nNOTIFY POWER 0\r\n"])
    await sub.close()


@pytest.mark.asyncio
async def test_same_source_feeds_all_matching_subscriptions():
    port = _free_tcp_port()
    got_a: list[bytes] = []
    got_b: list[bytes] = []
    sub_a = await tl.subscribe(port, "127.0.0.1", lambda d, a: got_a.append(d), "a")
    sub_b = await tl.subscribe(port, "127.0.0.1", lambda d, a: got_b.append(d), "b")
    await _dial(port, b"frame")
    await _wait_for(lambda: got_a == [b"frame"] and got_b == [b"frame"])
    await sub_a.close()
    await sub_b.close()


@pytest.mark.asyncio
async def test_port_conflict_raises_oserror():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError):
            await tl.subscribe(port, "127.0.0.1", lambda d, a: None, "cam")
    finally:
        blocker.close()


# ===========================================================================
# Driver lifecycle — subscribe, {listener_port}, register/unregister
# ===========================================================================


class _FakeTransport:
    connected = True

    async def close(self) -> None:
        self.connected = False


@pytest.mark.asyncio
async def test_start_push_subscribes_and_runs_register_command():
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})
    sent: list[str] = []

    async def fake_send(command, params=None):
        sent.append(command)

    drv.send_command = fake_send
    await drv._start_push()
    try:
        assert drv._push_subscription is not None
        assert drv.config["listener_port"] == port
        assert sent == ["start_notifications"]
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_listener_port_token_resolves_in_command_path():
    """The injected listener_port config value reaches HTTP path substitution
    — the actual wire-level registration a device would receive."""
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})

    async def fake_send(command, params=None):
        pass

    drv.send_command = fake_send
    await drv._start_push()
    try:
        raw = drv.DRIVER_INFO["commands"]["start_notifications"]["path"]
        resolved = drv._safe_substitute(raw, drv.config)
        assert f"my_port={port}" in resolved
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_ephemeral_port_resolves_to_bound_port():
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": 0})

    async def fake_send(command, params=None):
        pass

    drv.send_command = fake_send
    await drv._start_push()
    try:
        assert drv._push_subscription.port > 0
        assert drv.config["listener_port"] == drv._push_subscription.port
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_register_failure_keeps_subscription():
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})

    async def failing_send(command, params=None):
        raise ConnectionError("camera busy")

    drv.send_command = failing_send
    await drv._start_push()
    try:
        assert drv._push_subscription is not None
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_stop_push_sends_unregister_when_connected():
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})
    sent: list[str] = []

    async def fake_send(command, params=None):
        sent.append(command)

    drv.send_command = fake_send
    await drv._start_push()
    drv._connected = True
    drv.transport = _FakeTransport()
    await drv._stop_push()
    assert sent == ["start_notifications", "stop_notifications"]
    assert drv._push_subscription is None
    assert not tl._registry._listeners


@pytest.mark.asyncio
async def test_stop_push_skips_unregister_when_not_connected():
    """The stale-subscription drop at reconnect (and transport-loss cleanup)
    must not fire commands at a device that isn't reachable."""
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})
    sent: list[str] = []

    async def fake_send(command, params=None):
        sent.append(command)

    drv.send_command = fake_send
    await drv._start_push()
    drv._connected = False
    drv.transport = None
    await drv._stop_push()
    assert sent == ["start_notifications"]  # no stop_notifications


@pytest.mark.asyncio
async def test_start_push_unresolved_template_is_nonfatal():
    drv = _make_driver(_cam_def(), {"host": "10.0.0.5"})
    drv.config.pop("notify_port", None)
    drv.DRIVER_INFO = dict(drv.DRIVER_INFO)
    drv.DRIVER_INFO["push"] = {
        "type": "tcp_listener",
        "port": "{missing_field}",
    }
    await drv._start_push()
    assert drv._push_subscription is None


@pytest.mark.asyncio
async def test_stop_push_is_idempotent():
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})

    async def fake_send(command, params=None):
        pass

    drv.send_command = fake_send
    await drv._start_push()
    await drv._stop_push()
    await drv._stop_push()
    assert drv._push_subscription is None


# ===========================================================================
# End to end: framed dial-back -> response dispatch -> state write
# ===========================================================================


@pytest.mark.asyncio
async def test_pushed_frame_reaches_driver_state():
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})

    async def fake_send(command, params=None):
        pass

    drv.send_command = fake_send
    await drv._start_push()
    try:
        await _dial(port, _wrap(b"\r\nNOTIFY POWER 1\r\n"))
        await _wait_for(lambda: drv.state.get("device.cam1.power") == 1)
        # A second notification on a fresh connection (the dial-back pattern).
        await _dial(port, _wrap(b"\r\nNOTIFY PRESET 42\r\n"), chunks=3)
        await _wait_for(lambda: drv.state.get("device.cam1.preset") == 42)
    finally:
        await drv._stop_push()


# ===========================================================================
# Simulator — registration watching, frame wrapping, dial-out emission
# ===========================================================================


def _sim_def() -> dict:
    d = _cam_def()
    d["simulator"] = {
        "initial_state": {"power": 1, "preset": 0},
        "notifications": {
            "power": {"*": "\r\nNOTIFY POWER {value}\r\n"},
            "preset": {"*": "\r\nNOTIFY PRESET {value}\r\n"},
        },
    }
    return d


def test_sim_resolves_tcp_listener_push_block():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="cam1", config={}, driver_def=_sim_def())
    assert sim._push_tcp is not None
    assert sim._push_tcp["frame"]["type"] == "struct_frame"
    assert sim._push_tcp["register"] is not None
    assert sim._push_tcp["unregister"] is not None

    d = _sim_def()
    d.pop("push")
    sim2 = YAMLAutoSimulator(device_id="cam2", config={}, driver_def=d)
    assert sim2._push_tcp is None


def test_sim_tracks_subscribers_from_registration_commands():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="cam1", config={}, driver_def=_sim_def())
    # Registration with no explicit handler still succeeds (empty ack).
    resp = sim.handle_command(b"GET /api/event?connect=start&my_port=39999")
    assert resp == b""
    assert ("127.0.0.1", 39999) in sim._push_tcp_subscribers

    # Re-registration is idempotent.
    sim.handle_command(b"GET /api/event?connect=start&my_port=39999")
    assert len(sim._push_tcp_subscribers) == 1

    resp = sim.handle_command(b"GET /api/event?connect=stop&my_port=39999")
    assert resp == b""
    assert not sim._push_tcp_subscribers


def test_sim_wraps_payload_in_declared_struct_frame():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="cam1", config={}, driver_def=_sim_def())
    framed = sim._wrap_push_tcp_frame(b"\r\nNOTIFY POWER 0\r\n")
    assert framed == _wrap(b"\r\nNOTIFY POWER 0\r\n")
    # And the platform's parser round-trips it.
    parser = build_frame_parser(_FRAME_CFG)
    assert parser.feed(framed) == [b"\r\nNOTIFY POWER 0\r\n"]


def test_sim_without_frame_declaration_sends_raw():
    from simulator.yaml_auto import YAMLAutoSimulator

    d = _sim_def()
    d["push"] = {"type": "tcp_listener", "port": 0}
    sim = YAMLAutoSimulator(device_id="cam1", config={}, driver_def=d)
    assert sim._wrap_push_tcp_frame(b"raw") == b"raw"


@pytest.mark.asyncio
async def test_sim_dials_registered_subscriber_on_state_change():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="cam1", config={}, driver_def=_sim_def())
    received: list[bytes] = []

    async def on_conn(reader, writer):
        received.append(await reader.read())
        writer.close()

    server = await asyncio.start_server(on_conn, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        sim.handle_command(f"GET /api/event?connect=start&my_port={port}".encode())
        sim.set_state("power", 0)
        await _wait_for(lambda: len(received) == 1)
        parser = build_frame_parser(_FRAME_CFG)
        assert parser.feed(received[0]) == [b"\r\nNOTIFY POWER 0\r\n"]
    finally:
        server.close()
        await sim.stop()


@pytest.mark.asyncio
async def test_sim_prunes_unreachable_subscriber_after_three_failures():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="cam1", config={}, driver_def=_sim_def())
    dead_port = _free_tcp_port()  # nothing listening
    sim.handle_command(f"GET /api/event?connect=start&my_port={dead_port}".encode())
    target = ("127.0.0.1", dead_port)
    assert target in sim._push_tcp_subscribers
    for value in (0, 1, 0):
        sim._emit_push_tcp(b"x")
        await asyncio.gather(*sim._push_tcp_tasks, return_exceptions=True)
    assert target not in sim._push_tcp_subscribers


@pytest.mark.asyncio
async def test_sim_to_platform_end_to_end():
    """Full loop: driver opens the listener, registration is observed by the
    simulator, a simulator state change dials back a framed notification,
    and the driver's response rules write the state."""
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="cam1", config={}, driver_def=_sim_def())
    port = _free_tcp_port()
    drv = _make_driver(_cam_def(), {"host": "127.0.0.1", "notify_port": port})

    async def register_via_sim(command, params=None):
        # Stand-in for the HTTP hop: hand the resolved registration command
        # line to the simulator's dispatcher, as its HTTP server would.
        raw = drv.DRIVER_INFO["commands"][command]["path"]
        line = "GET " + drv._safe_substitute(raw, drv.config)
        sim.handle_command(line.encode())

    drv.send_command = register_via_sim
    # Mimic BaseDriver.connect(): the session is up before _start_push runs,
    # so the graceful _stop_push below takes the unregister path.
    drv._connected = True
    drv.transport = _FakeTransport()
    await drv._start_push()
    try:
        assert ("127.0.0.1", port) in sim._push_tcp_subscribers
        sim.set_state("preset", 7)
        await _wait_for(lambda: drv.state.get("device.cam1.preset") == 7)
    finally:
        await drv._stop_push()
        await sim.stop()
    # Graceful disconnect sent connect=stop -> subscriber slot freed.
    assert not sim._push_tcp_subscribers
