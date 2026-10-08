"""Tests for the liveness watchdog (BaseDriver awaited-probe hook), the
declarative YAML `liveness:` block, and the opt-in TCP keepalive.

The watchdog is the platform answer to silently-dead links: push-mostly TCP
(no FIN when the device vanishes), UDP (fire-and-forget polls never notice
silence), and OSC. A driver supplies a probe; after K consecutive misses the
transport is torn down with a typed ``no_response`` fault so the device card
shows the real cause and the platform reconnects.
"""

import asyncio
import socket
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectionFaultError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.transport.tcp import TCPTransport


class _FakeTransport:
    """Minimal transport double: connected flag + send recorder."""

    def __init__(self) -> None:
        self.connected = True
        self.sent: list[bytes] = []
        self.last_error = ""

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.connected = False


class _ProbeDriver(BaseDriver):
    """Test fixture: _liveness_probe behavior is configurable per instance."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "test_probe",
        "name": "Test Probe Driver",
        "category": "test",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
    }

    HEALTH_INTERVAL_S = 0.01
    HEALTH_TIMEOUT_S = 0.05
    HEALTH_MAX_FAILURES = 2

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.probe_count = 0
        self.probe_raises: BaseException | None = None
        self.probe_hangs = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        pass

    async def _liveness_probe(self) -> None:
        self.probe_count += 1
        if self.probe_hangs:
            await asyncio.sleep(10)
        if self.probe_raises is not None:
            raise self.probe_raises


class _PlainDriver(BaseDriver):
    """No probe override — the watchdog must stay disarmed."""

    DRIVER_INFO: dict[str, Any] = dict(_ProbeDriver.DRIVER_INFO)

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        pass


def _make_driver(cls: type[BaseDriver] = _ProbeDriver) -> Any:
    drv = cls(
        device_id="test_dev",
        config={},
        state=StateStore(),
        events=EventBus(),
    )
    drv.transport = _FakeTransport()
    drv._connected = True
    drv.set_state("connected", True)
    return drv


# --- BaseDriver hook ---


def test_health_enabled_requires_probe_override() -> None:
    assert _make_driver(_ProbeDriver)._health_enabled() is True
    assert _make_driver(_PlainDriver)._health_enabled() is False


@pytest.mark.asyncio
async def test_watchdog_flips_offline_with_typed_fault_after_misses() -> None:
    """K consecutive probe misses → connected False + no_response fault."""
    drv = _make_driver()
    drv.probe_raises = TimeoutError("no reply")

    drv._start_health_loop()
    await asyncio.sleep(0.3)

    assert drv.get_state("connected") is False
    assert drv._connected is False
    assert drv.probe_count >= 2
    assert drv.last_fault is not None
    assert drv.last_fault.code == "no_response"
    assert "keep-alive" in drv.last_fault.message
    # The loop exited on its own after forcing the disconnect
    assert drv._health_task is None or drv._health_task.done()


@pytest.mark.asyncio
async def test_a_refused_credential_drops_on_the_first_probe_and_keeps_its_code() -> None:
    """A probe the device answered by refusing the credential is not a miss:
    the device is there. It drops at once as auth_failed, so the platform
    pauses instead of reconnecting into the same refusal."""
    drv = _make_driver()
    drv.probe_raises = ConnectionFaultError("Login rejected", code="auth_failed")

    drv._start_health_loop()
    await asyncio.sleep(0.3)

    assert drv.get_state("connected") is False
    assert drv.probe_count == 1
    assert drv.last_fault is not None
    assert drv.last_fault.code == "auth_failed"
    assert drv.last_fault.message == "Login rejected"


@pytest.mark.asyncio
async def test_watchdog_success_resets_miss_counter() -> None:
    """A successful probe between misses prevents the disconnect."""
    drv = _make_driver()

    async def flaky_probe() -> None:
        drv.probe_count += 1
        # Alternate: odd probes miss, even probes answer
        if drv.probe_count % 2 == 1:
            raise TimeoutError("no reply")

    drv._liveness_probe = flaky_probe  # type: ignore[method-assign]
    drv._start_health_loop()
    await asyncio.sleep(0.3)
    try:
        assert drv.get_state("connected") is True
        assert drv.last_fault is None
        assert drv.probe_count >= 4
    finally:
        drv._stop_health_loop()


@pytest.mark.asyncio
async def test_watchdog_hung_probe_counts_as_miss() -> None:
    """A probe that never returns is bounded by HEALTH_TIMEOUT_S and counted."""
    drv = _make_driver()
    drv.probe_hangs = True

    drv._start_health_loop()
    await asyncio.sleep(0.5)

    assert drv.get_state("connected") is False
    assert drv.last_fault is not None
    assert drv.last_fault.code == "no_response"


@pytest.mark.asyncio
async def test_watchdog_stops_on_disconnect() -> None:
    drv = _make_driver()
    drv._start_health_loop()
    task = drv._health_task
    assert task is not None and not task.done()

    await drv.disconnect()
    await asyncio.sleep(0)

    assert drv._health_task is None
    assert task.done()


@pytest.mark.asyncio
async def test_watchdog_exits_when_transport_dies() -> None:
    """The loop self-terminates once the transport reports dead."""
    drv = _make_driver()
    drv._start_health_loop()
    drv.transport.connected = False
    await asyncio.sleep(0.1)
    assert drv._health_task is not None and drv._health_task.done()
    drv._stop_health_loop()


# --- Declarative `liveness:` block (ConfigurableDriver) ---


_LIVENESS_DEFINITION: dict[str, Any] = {
    "id": "test_udp_wall",
    "name": "Test UDP Wall",
    "manufacturer": "TestCo",
    "category": "video",
    "version": "1.0.0",
    "transport": "udp",
    "default_config": {"host": "", "port": 6000},
    "state_variables": {
        "brightness": {"type": "integer", "label": "Brightness"},
    },
    "commands": {},
    "responses": [
        {"match": r"BRT=(\d+)", "set": {"brightness": "$1"}},
    ],
    "liveness": {
        "send": "STATUS?\\r\\n",
        "interval": 0.01,
        "timeout": 0.05,
        "max_failures": 2,
    },
}


def _make_yaml_driver(definition: dict[str, Any]) -> Any:
    cls = create_configurable_driver_class(definition)
    drv = cls(
        device_id="test_dev",
        config=dict(definition.get("default_config", {})),
        state=StateStore(),
        events=EventBus(),
    )
    drv.transport = _FakeTransport()
    drv._connected = True
    drv.set_state("connected", True)
    return drv


def test_yaml_liveness_block_arms_the_watchdog() -> None:
    drv = _make_yaml_driver(_LIVENESS_DEFINITION)
    assert drv._health_enabled() is True
    assert drv.HEALTH_INTERVAL_S == 0.01
    assert drv.HEALTH_TIMEOUT_S == 0.05
    assert drv.HEALTH_MAX_FAILURES == 2


def test_yaml_without_liveness_block_stays_disarmed() -> None:
    definition = {
        k: v for k, v in _LIVENESS_DEFINITION.items() if k != "liveness"
    }
    drv = _make_yaml_driver(definition)
    assert drv._health_enabled() is False


@pytest.mark.asyncio
async def test_yaml_probe_sends_payload_and_reply_satisfies_it() -> None:
    """The probe transmits `send` (escapes processed) and any inbound frame
    resolves it — the frame still flows through normal response dispatch."""
    drv = _make_yaml_driver(_LIVENESS_DEFINITION)

    async def answer() -> None:
        await asyncio.sleep(0.01)
        await drv.on_data_received(b"BRT=42")

    answer_task = asyncio.create_task(answer())
    await asyncio.wait_for(drv._liveness_probe(), 1.0)
    await answer_task

    assert drv.transport.sent == [b"STATUS?\r\n"]
    assert drv.get_state("brightness") == 42


@pytest.mark.asyncio
async def test_yaml_probe_times_out_on_silence() -> None:
    drv = _make_yaml_driver(_LIVENESS_DEFINITION)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(drv._liveness_probe(), 0.05)
    # The waiter is cleaned up so the next probe can arm again
    assert drv._liveness_waiter is None


@pytest.mark.asyncio
async def test_yaml_probe_expect_filters_replies() -> None:
    """With `expect`, only a matching frame counts as alive."""
    definition = dict(_LIVENESS_DEFINITION)
    definition["liveness"] = dict(definition["liveness"], expect=r"^BRT=")
    drv = _make_yaml_driver(definition)

    async def chatter() -> None:
        await asyncio.sleep(0.005)
        await drv.on_data_received(b"HELLO")  # must NOT satisfy the probe

    task = asyncio.create_task(chatter())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(drv._liveness_probe(), 0.05)
    await task

    async def answer() -> None:
        await asyncio.sleep(0.005)
        await drv.on_data_received(b"BRT=7")

    task = asyncio.create_task(answer())
    await asyncio.wait_for(drv._liveness_probe(), 1.0)
    await task


@pytest.mark.asyncio
async def test_yaml_udp_silence_flips_device_offline_end_to_end() -> None:
    """The novastar shape: UDP + fire-and-forget polls used to stay online
    against a dead host forever. With a liveness block, silence now flips the
    device offline with a typed no_response fault."""
    drv = _make_yaml_driver(_LIVENESS_DEFINITION)
    transport = drv.transport  # nulled by the disconnect cleanup below

    drv._start_health_loop()
    await asyncio.sleep(0.5)

    assert drv.get_state("connected") is False
    assert drv.last_fault is not None
    assert drv.last_fault.code == "no_response"
    # Probes actually went out on the wire
    assert transport.sent


# --- Loader validation ---


def _definition_with_liveness(liveness: Any, transport: str = "udp") -> dict:
    return {
        "id": "x",
        "name": "X",
        "transport": transport,
        "liveness": liveness,
    }


def test_loader_accepts_valid_liveness_block() -> None:
    errors = validate_driver_definition(
        _definition_with_liveness(
            {"send": "PING\\r\\n", "interval": 10, "timeout": 3,
             "max_failures": 3, "expect": "PONG"}
        )
    )
    assert not [e for e in errors if e.startswith("liveness")]


def test_loader_rejects_liveness_on_http() -> None:
    errors = validate_driver_definition(
        _definition_with_liveness({"send": "PING"}, transport="http")
    )
    assert any("liveness" in e and "http" in e for e in errors)


def test_loader_rejects_liveness_without_send() -> None:
    errors = validate_driver_definition(_definition_with_liveness({}))
    assert any("liveness" in e and "send" in e for e in errors)


def test_loader_rejects_bad_liveness_values() -> None:
    errors = validate_driver_definition(
        _definition_with_liveness(
            {"send": "PING", "interval": 0, "max_failures": 0,
             "expect": "("}
        )
    )
    joined = "\n".join(errors)
    assert "interval" in joined
    assert "max_failures" in joined
    assert "liveness.expect" in joined


def test_loader_rejects_osc_args_on_other_transports() -> None:
    errors = validate_driver_definition(
        _definition_with_liveness({"send": "PING", "args": [1]})
    )
    assert any("args" in e for e in errors)


# --- TCP keepalive opt-in ---


@pytest.fixture
async def silent_server():
    """TCP server that accepts and holds connections without sending: it
    reads whatever arrives and never answers, until the client hangs up."""

    async def handle(reader, writer):
        try:
            while await reader.read(4096):
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield server, port
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_tcp_keepalive_opt_in_sets_socket_option(silent_server) -> None:
    _server, port = silent_server
    transport = await TCPTransport.create(
        host="127.0.0.1", port=port,
        on_data=lambda d: None, on_disconnect=lambda: None,
        keepalive=True,
    )
    try:
        sock = transport._writer.get_extra_info("socket")
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_tcp_keepalive_off_by_default(silent_server) -> None:
    _server, port = silent_server
    transport = await TCPTransport.create(
        host="127.0.0.1", port=port,
        on_data=lambda d: None, on_disconnect=lambda: None,
    )
    try:
        sock = transport._writer.get_extra_info("socket")
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 0
    finally:
        await transport.close()


# --- Silence check: a peer that takes every poll and answers none ---
#
# A fire-and-forget poll returns once its bytes are written, so before the
# check a device that accepted the connection and never answered (the wrong
# device at the address, something else on the port) read connected for as
# long as the socket stayed open. These run real sockets on loopback.

_SILENCE_INTERVAL = 0.1


def _acme_polled(transport: str = "tcp", **extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": f"acme_widget_{transport}",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": transport,
        "delimiter": "\r",
        "default_config": {},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "commands": {},
        "responses": [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
        "polling": {"queries": ["PWR?\r"]},
    }
    definition.update(extra)
    return definition


def _connect_config(port: int, **extra: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": port,
        "poll_interval": _SILENCE_INTERVAL,
        "max_missed_polls": 3,
    }
    config.update(extra)
    return config


def _build(definition: dict[str, Any], config: dict[str, Any]) -> Any:
    cls = create_configurable_driver_class(definition)
    return cls(device_id="acme1", config=config, state=StateStore(), events=EventBus())


async def _until(predicate: Any, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.fixture
async def answering_server():
    """TCP server that answers each ``PWR?`` line with ``PWR=ON``. Set
    ``state["answer_every"]`` to answer only every Nth query, and
    ``state["answers_left"]`` to stop answering after that many."""
    state: dict[str, Any] = {"answer_every": 1, "answers_left": None, "queries": 0}

    async def handle(reader, writer):
        buf = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    if line != b"PWR?":
                        continue
                    state["queries"] += 1
                    if state["queries"] % state["answer_every"]:
                        continue
                    if state["answers_left"] is not None:
                        if state["answers_left"] <= 0:
                            continue
                        state["answers_left"] -= 1
                    writer.write(b"PWR=ON\r")
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, state
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_a_polling_driver_with_rules_goes_no_response_on_a_silent_peer(
    silent_server,
) -> None:
    """The field case: the connection opens, every poll goes out, nothing
    ever comes back. Offline after max_missed_polls cycles, with the
    sentence that points at the address rather than at a device that was
    answering."""
    _server, port = silent_server
    drv = _build(_acme_polled(), _connect_config(port))
    await drv.connect()
    try:
        assert drv.get_state("connected") is True
        assert await _until(lambda: drv.get_state("connected") is False, 3.0)
        assert drv.last_fault is not None
        assert drv.last_fault.code == "no_response"
        assert drv.last_fault.message.startswith(
            "Nothing has answered on this connection: 3 polls in a row got no reply."
        )
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_driver_with_no_response_rules_stays_connected(silent_server) -> None:
    """A device that is only ever told things has nothing to answer with."""
    _server, port = silent_server
    drv = _build(_acme_polled(responses=[]), _connect_config(port))
    await drv.connect()
    try:
        await asyncio.sleep(_SILENCE_INTERVAL * 7)
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_driver_with_a_liveness_probe_is_left_to_its_probe(
    silent_server,
) -> None:
    """The probe already decides this; the poll loop does not decide it a
    second time on a different clock."""
    _server, port = silent_server
    definition = _acme_polled(
        liveness={"send": "PWR?\r", "interval": 30, "timeout": 5, "max_failures": 2},
    )
    drv = _build(definition, _connect_config(port))
    await drv.connect()
    try:
        await asyncio.sleep(_SILENCE_INTERVAL * 7)
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_device_that_answers_stays_connected(answering_server) -> None:
    port, _state = answering_server
    drv = _build(_acme_polled(), _connect_config(port))
    await drv.connect()
    try:
        await asyncio.sleep(_SILENCE_INTERVAL * 7)
        assert drv.get_state("connected") is True
        assert drv.get_state("power") == "ON"
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_an_answer_every_other_poll_never_drops(answering_server) -> None:
    """Silence has to be consecutive: one answered cycle resets the count."""
    port, state = answering_server
    state["answer_every"] = 2
    drv = _build(_acme_polled(), _connect_config(port, max_missed_polls=2))
    await drv.connect()
    try:
        await asyncio.sleep(_SILENCE_INTERVAL * 9)
        assert drv.get_state("connected") is True
        assert state["queries"] >= 6
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_device_that_stops_answering_says_so(answering_server) -> None:
    port, state = answering_server
    state["answers_left"] = 2
    drv = _build(_acme_polled(), _connect_config(port))
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("connected") is False, 3.0)
        assert drv.get_state("power") == "ON"
        assert drv.last_fault.code == "no_response"
        assert "stopped answering" in drv.last_fault.message
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_poll_that_sends_nothing_is_never_silent(silent_server) -> None:
    """A cycle that asked nothing proves nothing either way."""
    _server, port = silent_server
    drv = _build(_acme_polled(polling={"queries": []}), _connect_config(port))
    await drv.connect()
    try:
        await asyncio.sleep(_SILENCE_INTERVAL * 7)
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_silent_udp_peer_goes_no_response() -> None:
    """The same check on a datagram link: a bound socket that never replies."""
    loop = asyncio.get_running_loop()
    sink, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    port = sink.get_extra_info("sockname")[1]
    drv = _build(_acme_polled("udp"), _connect_config(port))
    try:
        await drv.connect()
        assert await _until(lambda: drv.get_state("connected") is False, 3.0)
        assert drv.last_fault.code == "no_response"
    finally:
        await drv.disconnect()
        sink.close()


@pytest.mark.asyncio
async def test_an_answering_udp_peer_stays_connected() -> None:
    loop = asyncio.get_running_loop()

    class _Answer(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            self.transport.sendto(b"PWR=ON\r", addr)

    answer, _ = await loop.create_datagram_endpoint(
        _Answer, local_addr=("127.0.0.1", 0)
    )
    port = answer.get_extra_info("sockname")[1]
    drv = _build(_acme_polled("udp"), _connect_config(port))
    try:
        await drv.connect()
        await asyncio.sleep(_SILENCE_INTERVAL * 7)
        assert drv.get_state("connected") is True
        assert drv.get_state("power") == "ON"
    finally:
        await drv.disconnect()
        answer.close()


@pytest.mark.asyncio
async def test_a_push_delivery_counts_as_an_answer(silent_server) -> None:
    """A device that answers on a push channel instead of the connection is
    answering."""
    _server, port = silent_server
    drv = _build(_acme_polled(), _connect_config(port, max_missed_polls=2))
    await drv.connect()
    try:
        handler = drv._counting_push(drv._handle_push_datagram)
        for _ in range(8):
            await handler(b"PWR=ON\r", ("127.0.0.1", 9))
            await asyncio.sleep(_SILENCE_INTERVAL)
        assert drv.get_state("connected") is True
        assert drv.get_state("power") == "ON"
    finally:
        await drv.disconnect()


class _AcmePython(BaseDriver):
    """A Python driver with a fire-and-forget poll over the platform's TCP."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_py",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "delimiter": "\r",
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        await self.transport.send(b"PWR?\r")

    async def on_data_received(self, data: bytes) -> None:
        pass


class _AcmePythonDeaf(_AcmePython):
    """The same poll, with no reply handler of its own."""

    on_data_received = BaseDriver.on_data_received


@pytest.mark.asyncio
async def test_a_python_driver_that_reads_replies_is_watched(silent_server) -> None:
    _server, port = silent_server
    drv = _AcmePython("acme1", _connect_config(port), StateStore(), EventBus())
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("connected") is False, 3.0)
        assert drv.last_fault.code == "no_response"
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_python_driver_with_no_reply_handler_is_not(silent_server) -> None:
    _server, port = silent_server
    drv = _AcmePythonDeaf("acme1", _connect_config(port), StateStore(), EventBus())
    await drv.connect()
    try:
        await asyncio.sleep(_SILENCE_INTERVAL * 7)
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


def test_serial_and_countless_transports_are_not_watched() -> None:
    """Serial waits for a measurement on a serial unit; a transport that keeps
    no counts (a driver-owned session, a test double) cannot be watched."""

    class _Counted(_FakeTransport):
        send_count = 0
        receive_count = 0

    drv = _AcmePython("acme1", {"transport": "serial"}, StateStore(), EventBus())
    drv.transport = _Counted()
    assert drv._watches_for_silence() is False

    drv = _AcmePython("acme1", {}, StateStore(), EventBus())
    drv.transport = _Counted()
    assert drv._watches_for_silence() is True
    drv.transport = _FakeTransport()
    assert drv._watches_for_silence() is False
    drv.transport = None
    assert drv._watches_for_silence() is False


# --- The reconnect after a silent drop holds until the device answers ---
#
# Without the hold, a device that never answers reads connected for N polls of
# every reconnect (measured on hardware: 30 s connected, ~1 s offline, again),
# and its Connected / Disconnected trigger events fire every cycle.


@pytest.fixture
async def switchable_server():
    """TCP server whose behaviour a test changes as it runs: ``mode`` is
    "silent" or "answer" (``PWR=ON`` for each ``PWR?``), ``greet`` sends a
    banner on every new connection, ``connections`` counts accepts."""
    state: dict[str, Any] = {"mode": "silent", "greet": False, "connections": 0}

    async def handle(reader, writer):
        state["connections"] += 1
        buf = b""
        try:
            if state["greet"]:
                writer.write(b"Welcome\r")
                await writer.drain()
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    if line == b"PWR?" and state["mode"] == "answer":
                        writer.write(b"PWR=ON\r")
                        await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, state
    server.close()
    await server.wait_closed()


@pytest.fixture
async def managed_acme(monkeypatch, switchable_server):
    """The acme driver under a real DeviceManager, retries 50 ms apart, and a
    record of every connected / disconnected event it emits."""
    from openavc.core.device_manager import DeviceManager
    from openavc.drivers.registry import register_driver, unregister_driver

    monkeypatch.setattr(
        DeviceManager, "_reconnect_delay", lambda self, device_id, attempt: 0.05
    )
    port, server = switchable_server
    definition = _acme_polled()
    register_driver(create_configurable_driver_class(definition))
    state, events = StateStore(), EventBus()
    dm = DeviceManager(state, events)
    seen: list[str] = []
    events.on("device.connected.*", lambda event, payload=None: seen.append("connected"))
    events.on("device.disconnected.*", lambda event, payload=None: seen.append("disconnected"))
    await dm.add_device({
        "id": "acme1", "driver": definition["id"], "name": "Acme",
        "config": _connect_config(port),
    })
    try:
        yield dm, state, server, seen
    finally:
        await dm.disconnect_all()
        unregister_driver(definition["id"])


@pytest.mark.asyncio
async def test_a_device_that_never_answers_stays_offline_across_reconnects(managed_acme) -> None:
    dm, state, server, seen = managed_acme
    assert await _until(lambda: state.get("device.acme1.connected") is False, 3.0)
    first_drop = server["connections"]
    # Three more held connections, each closed after its own silent polls.
    assert await _until(lambda: server["connections"] >= first_drop + 3, 6.0)
    assert state.get("device.acme1.connected") is False
    assert state.get("device.acme1.offline_reason") == "no_response"
    assert state.get("device.acme1.offline_detail").startswith("Nothing has answered")
    assert seen == ["connected", "disconnected"]


@pytest.mark.asyncio
async def test_a_held_device_that_starts_answering_comes_online(managed_acme) -> None:
    dm, state, server, seen = managed_acme
    assert await _until(lambda: state.get("device.acme1.connected") is False, 3.0)
    assert await _until(lambda: server["connections"] >= 2, 3.0)
    server["mode"] = "answer"
    # The driver announces it; the reconnect loop then clears the reason.
    assert await _until(
        lambda: state.get("device.acme1.connected") is True
        and state.get("device.acme1.offline_reason") is None,
        2.0,
    )
    assert state.get("device.acme1.power") == "ON"
    assert seen == ["connected", "disconnected", "connected"]
    # Announced, it is an ordinary connection again: it stays up.
    await asyncio.sleep(_SILENCE_INTERVAL * 6)
    assert state.get("device.acme1.connected") is True


@pytest.mark.asyncio
async def test_a_banner_on_connect_is_not_an_answer(managed_acme) -> None:
    """Something else on the port that greets every connection (a telnet
    prompt) must not be announced on each reconnect: only a reply after a poll
    went out counts."""
    dm, state, server, seen = managed_acme
    server["greet"] = True
    assert await _until(lambda: state.get("device.acme1.connected") is False, 3.0)
    first_drop = server["connections"]
    assert await _until(lambda: server["connections"] >= first_drop + 3, 6.0)
    assert state.get("device.acme1.connected") is False
    assert seen == ["connected", "disconnected"]


@pytest.mark.asyncio
async def test_pressing_reconnect_on_a_held_device_is_a_fresh_attempt(managed_acme) -> None:
    dm, state, server, seen = managed_acme
    assert await _until(lambda: state.get("device.acme1.connected") is False, 3.0)
    assert await _until(lambda: server["connections"] >= 2, 3.0)
    await dm.reconnect_device("acme1")
    assert state.get("device.acme1.connected") is True
