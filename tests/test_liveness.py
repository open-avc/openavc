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
from openavc.drivers.base import BaseDriver
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
    """TCP server that accepts and holds connections without sending."""

    async def handle(reader, writer):
        try:
            await reader.read(4096)
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
