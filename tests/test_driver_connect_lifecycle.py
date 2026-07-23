"""Tests for the BaseDriver connection-lifecycle seam.

BaseDriver.connect() is the one place the connection lifecycle lives:
clean-slate fault reset -> _pre_connect -> _create_transport (kwargs via
_transport_kwargs) -> verify -> _post_connect handshake -> connected
declared (state + canonical event) -> push -> _initial_sync -> polling +
liveness watchdog. Drivers customize through the hooks instead of copying
the sequence; these tests pin the hook order, the failure teardown paths,
and the driver-owned-session mode (_create_transport override +
_link_alive + _close_session).
"""

import asyncio
from typing import Any

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver


@pytest.fixture
async def echo_server():
    """Loopback TCP server that answers every line with OK."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                writer.write(b"OK\r")
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
    yield server, port
    server.close()
    await server.wait_closed()


class _HookDriver(BaseDriver):
    """Records every lifecycle hook invocation in order."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "test_lifecycle",
        "name": "Test Lifecycle Driver",
        "category": "test",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[str] = []
        self.initial_sync_raises: BaseException | None = None
        self.post_connect_raises: BaseException | None = None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        self.calls.append("poll")

    async def _pre_connect(self) -> None:
        self.calls.append("pre_connect")

    def _transport_kwargs(
        self, transport_type: str, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(f"transport_kwargs:{transport_type}")
        return kwargs

    async def _post_connect(self) -> None:
        self.calls.append("post_connect")
        assert self.transport is not None and self.transport.connected
        if self.post_connect_raises is not None:
            raise self.post_connect_raises

    async def _initial_sync(self) -> None:
        self.calls.append("initial_sync")
        # Declared and observable before the sync runs.
        assert self._connected is True
        assert self.get_state("connected") is True
        if self.initial_sync_raises is not None:
            raise self.initial_sync_raises

    async def _close_session(self) -> None:
        self.calls.append("close_session")


def _make_driver(cls: type[BaseDriver], port: int) -> Any:
    return cls(
        device_id="test_dev",
        config={"host": "127.0.0.1", "port": port},
        state=StateStore(),
        events=EventBus(),
    )


@pytest.mark.asyncio
async def test_hook_order_and_canonical_event(echo_server) -> None:
    """The full stage sequence runs in order and emits the canonical topic."""
    _server, port = echo_server
    drv = _make_driver(_HookDriver, port)
    events: list[str] = []
    drv.events.on("device.*", lambda t, p=None: events.append(t))

    await drv.connect()
    try:
        # close_session is part of the clean-slate head, so it leads.
        assert drv.calls == [
            "close_session",
            "pre_connect",
            "transport_kwargs:tcp",
            "post_connect",
            "initial_sync",
        ]
        assert drv.connected is True
        assert drv.get_state("connected") is True
        await asyncio.sleep(0)
        assert "device.connected.test_dev" in events
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_transport_kwargs_override_applied(echo_server) -> None:
    """A kwargs-hook mutation reaches the transport constructor."""
    _server, port = echo_server

    class _RawDriver(_HookDriver):
        def _transport_kwargs(
            self, transport_type: str, kwargs: dict[str, Any]
        ) -> dict[str, Any]:
            # Raw byte stream: no delimiter framing at all.
            kwargs["delimiter"] = None
            return kwargs

    drv = _make_driver(_RawDriver, port)
    await drv.connect()
    try:
        assert drv.transport._frame_parser is None
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_initial_sync_failure_tears_down(echo_server) -> None:
    """A raise in _initial_sync fails the attempt and undoes the declare."""
    _server, port = echo_server
    drv = _make_driver(_HookDriver, port)
    drv.initial_sync_raises = RuntimeError("sync failed")
    events: list[str] = []
    drv.events.on("device.*", lambda t, p=None: events.append(t))

    with pytest.raises(RuntimeError, match="sync failed"):
        await drv.connect()

    assert drv.connected is False
    assert drv.get_state("connected") is False
    assert drv.transport is None
    # Torn down after the sync raised (beyond the clean-slate call).
    assert drv.calls.count("close_session") == 2
    await asyncio.sleep(0)
    assert "device.disconnected.test_dev" in events


@pytest.mark.asyncio
async def test_post_connect_failure_closes_session(echo_server) -> None:
    """A raise in _post_connect closes the transport AND the driver session."""
    _server, port = echo_server
    drv = _make_driver(_HookDriver, port)
    drv.post_connect_raises = ConnectionError("login rejected")

    with pytest.raises(ConnectionError, match="login rejected"):
        await drv.connect()

    assert drv.connected is False
    assert drv.transport is None
    assert drv.calls.count("close_session") == 2


@pytest.mark.asyncio
async def test_disconnect_closes_session(echo_server) -> None:
    """Graceful disconnect runs _close_session symmetrically."""
    _server, port = echo_server
    drv = _make_driver(_HookDriver, port)
    await drv.connect()
    drv.calls.clear()

    await drv.disconnect()

    assert "close_session" in drv.calls
    assert drv.connected is False


class _SessionDriver(BaseDriver):
    """Driver-owned-session mode: no platform transport at all."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "test_session",
        "name": "Test Session Driver",
        "category": "test",
        "transport": "http",
        "state_variables": {},
        "commands": {},
    }

    HEALTH_INTERVAL_S = 0.01
    HEALTH_TIMEOUT_S = 0.05
    HEALTH_MAX_FAILURES = 2

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client: object | None = None
        self.probe_count = 0
        self.probe_raises: BaseException | None = None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        pass

    async def _create_transport(self, transport_type: str) -> None:
        # The driver owns its session (stand-in for an httpx.AsyncClient);
        # self.transport stays None.
        self.client = object()

    def _link_alive(self) -> bool:
        return self.client is not None

    async def _close_session(self) -> None:
        self.client = None

    async def _liveness_probe(self) -> None:
        self.probe_count += 1
        if self.probe_raises is not None:
            raise self.probe_raises


@pytest.mark.asyncio
async def test_session_driver_connects_without_transport() -> None:
    """connected is True with transport None when _link_alive says so."""
    drv = _SessionDriver(
        device_id="test_dev", config={}, state=StateStore(), events=EventBus()
    )
    await drv.connect()
    try:
        assert drv.transport is None
        assert drv.connected is True
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()
    assert drv.client is None
    assert drv.connected is False


@pytest.mark.asyncio
async def test_session_driver_health_loop_runs_and_tears_down() -> None:
    """The watchdog runs on _link_alive (not the transport) and a failing
    probe tears the session down through the standard cleanup."""
    drv = _SessionDriver(
        device_id="test_dev", config={}, state=StateStore(), events=EventBus()
    )
    await drv.connect()
    assert drv._health_task is not None and not drv._health_task.done()

    await asyncio.sleep(0.1)
    assert drv.probe_count >= 1

    drv.probe_raises = TimeoutError("silent")
    await asyncio.sleep(0.3)

    assert drv.get_state("connected") is False
    assert drv.client is None  # cleanup closed the driver session
    assert drv.last_fault is not None
    assert drv.last_fault.code == "no_response"


@pytest.mark.asyncio
async def test_reconnect_attempt_closes_stale_session() -> None:
    """A fresh connect() drops the previous attempt's session first."""
    drv = _SessionDriver(
        device_id="test_dev", config={}, state=StateStore(), events=EventBus()
    )
    await drv.connect()
    stale = drv.client
    assert stale is not None

    await drv.connect()
    try:
        assert drv.client is not None
        assert drv.client is not stale
    finally:
        await drv.disconnect()
