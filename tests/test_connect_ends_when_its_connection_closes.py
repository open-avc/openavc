"""A connect fails as soon as its connection closes under it.

A driver's handshake (``_post_connect``) waits for the device's first words.
When the connection closed during that wait, the handshake used to wait out
its own timeout, while the drop's handler emitted the disconnect and the
device manager started a retry beside the connect that was still running.
The connect then failed seconds later with the handshake's own complaint and
no cause. Now the connect ends at once with the transport's own words, and
its failure is the one report: no disconnect event, one retry.

The device is invented; each server here accepts on a port of its own.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from openavc.core.connection_fault import (
    AUTH_FAILED,
    TRANSPORT_DISCONNECTED,
    default_fault_message,
)
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY

# How long the invented device's handshake waits for its prompt. A connect
# that waits it out takes this long; one that notices the drop does not.
PROMPT_WAIT = 3.0


class AcmeConsoleDriver(BaseDriver):
    """An invented device that shows a prompt once its connection is up."""

    DRIVER_INFO = {
        "id": "acme_console_drop_test",
        "name": "Acme Console",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"host": "", "port": 23},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompt = asyncio.Event()
        self.waiting = asyncio.Event()
        self.handshake_cancelled = False

    async def on_data_received(self, data: bytes) -> None:
        if b"LOGIN FAILED" in data:
            # The shape of a driver whose reader learns the credential was
            # refused: drop the connection, typed.
            self._force_disconnect(AUTH_FAILED, "Login rejected")

    async def _post_connect(self) -> None:
        self.waiting.set()
        try:
            await asyncio.wait_for(self.prompt.wait(), PROMPT_WAIT)
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"[{self.device_id}] No prompt from {self.config.get('host')}"
            ) from None
        except asyncio.CancelledError:
            self.handshake_cancelled = True
            raise

    async def send_command(self, command, params=None):
        return None


class _TalkativeLink:
    """An invented transport that says why it closed, the way the ssh client
    leaves its last words on stderr."""

    def __init__(self, on_disconnect) -> None:
        self.connected = True
        self.last_error = ""
        self._on_disconnect = on_disconnect

    def hang_up(self, said: str) -> None:
        self.last_error = said
        self.connected = False
        self._on_disconnect()

    async def close(self) -> None:
        self.connected = False


class AcmeShellDriver(AcmeConsoleDriver):
    """The same device behind a link whose client exits with a reason."""

    async def _create_transport(self, transport_type: str) -> None:
        self.transport = _TalkativeLink(self._handle_transport_disconnect)
        asyncio.get_running_loop().call_later(
            0.05,
            self.transport.hang_up,
            "Warning: Permanently added '192.0.2.10' to the list of known hosts.\n"
            "acme: connect to host 192.0.2.10 port 22: Connection refused\n",
        )


@pytest.fixture
async def device_server():
    """A server whose handler the test chooses: ``(reader, writer) -> None``."""
    servers: list[asyncio.Server] = []
    writers: list[asyncio.StreamWriter] = []

    async def start(behaviour) -> int:
        async def handle(reader, writer):
            writers.append(writer)
            await behaviour(reader, writer)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        servers.append(server)
        return server.sockets[0].getsockname()[1]

    yield start
    for writer in writers:
        writer.transport.abort()
    for server in servers:
        server.close()


async def _close_at_once(reader, writer) -> None:
    writer.close()


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


def _driver(core, port: int) -> AcmeConsoleDriver:
    state, events = core
    return AcmeConsoleDriver(
        "console", {"host": "127.0.0.1", "port": port}, state, events,
    )


def _record_disconnects(events: EventBus) -> list[str]:
    seen: list[str] = []
    events.on("device.disconnected.*", lambda event, payload=None: seen.append(event))
    return seen


async def test_a_connect_ends_when_its_connection_closes(core, device_server):
    _, events = core
    driver = _driver(core, await device_server(_close_at_once))
    disconnects = _record_disconnects(events)

    started = time.monotonic()
    with pytest.raises(ConnectionError) as failure:
        await driver.connect()
    elapsed = time.monotonic() - started
    await asyncio.sleep(0.1)  # room for a disconnect the drop might still emit

    assert elapsed < 1.0
    assert driver.handshake_cancelled
    # A goodbye carries no words of its own, so the taxonomy's sentence.
    assert str(failure.value) == default_fault_message(TRANSPORT_DISCONNECTED)
    assert disconnects == []
    assert driver.transport is None
    assert driver.get_state("connected") is False


async def test_the_connect_fails_with_what_the_transport_said(core):
    state, events = core
    driver = AcmeShellDriver(
        "shell", {"host": "192.0.2.10", "port": 22}, state, events,
    )

    started = time.monotonic()
    with pytest.raises(ConnectionError) as failure:
        await driver.connect()

    assert time.monotonic() - started < 1.0
    assert str(failure.value) == (
        "acme: connect to host 192.0.2.10 port 22: Connection refused"
    )
    # The whole of it stays for the classifier.
    assert "Connection refused" in driver.last_transport_error


async def test_a_drop_that_lands_with_a_finished_handshake_fails_the_connect(
    core, device_server,
):
    """The handshake finishes in the same moment the connection drops: the
    connect still fails, and the device is never declared connected."""
    _, events = core
    port = await device_server(_close_at_once)
    driver = _driver(core, port)
    disconnects = _record_disconnects(events)
    connected: list[str] = []
    events.on("device.connected.*", lambda event, payload=None: connected.append(event))
    drop = driver._handle_transport_disconnect

    def prompt_then_drop() -> None:
        driver.prompt.set()
        drop()

    driver._handle_transport_disconnect = prompt_then_drop

    with pytest.raises(ConnectionError):
        await driver.connect()
    await asyncio.sleep(0.1)

    assert driver.get_state("connected") is False
    assert driver.connected is False
    assert connected == []
    assert disconnects == []


async def test_a_cancelled_connect_cancels_its_handshake(core, device_server):
    async def stay_silent(reader, writer):
        await reader.read()

    driver = _driver(core, await device_server(stay_silent))

    connecting = asyncio.ensure_future(driver.connect())
    await asyncio.wait_for(driver.waiting.wait(), 2.0)
    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connecting

    assert driver.handshake_cancelled


@pytest.fixture
async def manager(core, monkeypatch):
    state, events = core
    monkeypatch.setitem(
        _DRIVER_REGISTRY, AcmeConsoleDriver.DRIVER_INFO["id"], AcmeConsoleDriver,
    )
    dm = DeviceManager(state, events)
    # A retry started here waits instead of dialing again during the test.
    monkeypatch.setattr(dm, "_reconnect_delay", lambda device_id, attempt: 60.0)
    yield dm
    await dm.disconnect_all()


async def _add(manager, port: int) -> None:
    await manager.add_device({
        "id": "console",
        "driver": AcmeConsoleDriver.DRIVER_INFO["id"],
        "name": "Console",
        "config": {"host": "127.0.0.1", "port": port},
    })


async def test_a_bring_up_whose_connection_closes_starts_one_retry(
    manager, core, device_server, monkeypatch,
):
    state, events = core
    port = await device_server(_close_at_once)
    disconnects = _record_disconnects(events)
    starts: list[str] = []
    start = manager._start_reconnect

    def counted(device_id: str) -> None:
        starts.append(device_id)
        start(device_id)

    monkeypatch.setattr(manager, "_start_reconnect", counted)

    started = time.monotonic()
    await _add(manager, port)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert disconnects == []
    assert starts == ["console"]
    assert list(manager._reconnect_tasks) == ["console"]
    assert state.get("device.console.offline_reason") is not None


async def test_a_refused_login_during_the_handshake_pauses_the_retries(
    manager, core, device_server,
):
    state, _ = core

    async def refuse(reader, writer):
        writer.write(b"LOGIN FAILED\r\n")
        await writer.drain()
        await reader.read()

    port = await device_server(refuse)

    started = time.monotonic()
    await _add(manager, port)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert state.get("device.console.offline_reason") == AUTH_FAILED
    assert state.get("device.console.offline_detail") == "Login rejected"
    assert state.get("device.console.reconnect_failed") is True
    assert "console" not in manager._reconnect_tasks
