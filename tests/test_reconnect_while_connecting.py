"""A Reconnect pressed while a connect still waits replaces that connect.

A driver's handshake (``_post_connect``) can wait seconds for a device that
says nothing. Pressing Reconnect meanwhile (or anything else that disconnects
the driver or connects it again: a pause, a resume, a setup action's
reconnect) used to leave the first connect waiting on a connection already
closed under it, reading the new connection's replies, and when it failed its
cleanup closed whatever the driver held by then: the new connection. Its
failure also started a retry beside the connect that replaced it.

Now a disconnect, or a newer connect, ends a connect still in flight at once;
the ended connect closes only what it built, reports nothing and starts no
retry. The device is invented; the server accepts on a port of its own, and
its first connection never answers.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY

# How long the invented device's handshake waits for its prompt.
PROMPT_WAIT = 3.0


class AcmeConsoleDriver(BaseDriver):
    """An invented device that shows a prompt once its connection is up."""

    DRIVER_INFO = {
        "id": "acme_console_reconnect_test",
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
        self.handshakes = 0

    async def on_data_received(self, data: bytes) -> None:
        if b"PROMPT>" in data:
            self.prompt.set()

    async def _pre_connect(self) -> None:
        # Before the transport exists, so a prompt sent on accept is kept.
        self.prompt.clear()

    async def _post_connect(self) -> None:
        self.handshakes += 1
        try:
            await asyncio.wait_for(self.prompt.wait(), PROMPT_WAIT)
        except asyncio.TimeoutError:
            raise ConnectionError(f"[{self.device_id}] No prompt") from None

    async def send_command(self, command, params=None):
        return None


class _Console:
    """A server whose first connection never answers; each later one sends
    its prompt ``answer_after`` seconds after it is accepted."""

    def __init__(self, answer_after: float):
        self.answer_after = answer_after
        self.accepted: list[float] = []
        self.closed_by_client: dict[int, float] = {}
        self.first = asyncio.Event()
        self._writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.base_events.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _serve(self, reader, writer) -> None:
        index = len(self.accepted)
        self.accepted.append(time.monotonic())
        self._writers.append(writer)
        self.first.set()
        answer = None
        if index > 0:
            async def later():
                await asyncio.sleep(self.answer_after)
                writer.write(b"PROMPT>\r\n")
                await writer.drain()
            answer = asyncio.ensure_future(later())
        try:
            while await reader.read(1024):
                pass
        except (ConnectionError, OSError):
            pass
        self.closed_by_client[index] = time.monotonic()
        if answer is not None:
            answer.cancel()

    def timeline(self, since: float) -> str:
        rows = [
            f"connection {i}: accepted +{t - since:.2f}s, "
            + (f"closed by the client +{self.closed_by_client[i] - since:.2f}s"
               if i in self.closed_by_client else "still open")
            for i, t in enumerate(self.accepted)
        ]
        return "; ".join(rows)

    def open(self, index: int) -> bool:
        return index < len(self.accepted) and index not in self.closed_by_client

    async def stop(self) -> None:
        for writer in self._writers:
            writer.transport.abort()
        if self.server is not None:
            self.server.close()


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
async def manager(core, monkeypatch):
    state, events = core
    monkeypatch.setitem(
        _DRIVER_REGISTRY, AcmeConsoleDriver.DRIVER_INFO["id"], AcmeConsoleDriver,
    )
    dm = DeviceManager(state, events)
    # A retry started here waits instead of dialing again during the test.
    monkeypatch.setattr(dm, "_reconnect_delay", lambda device_id, attempt: 60.0)
    dm.retry_starts = []
    start = dm._start_reconnect

    def counted(device_id: str) -> None:
        dm.retry_starts.append(device_id)
        start(device_id)

    monkeypatch.setattr(dm, "_start_reconnect", counted)
    yield dm
    await dm.disconnect_all()


@pytest.fixture
async def console():
    servers: list[_Console] = []

    async def make(answer_after: float) -> tuple[_Console, int]:
        server = _Console(answer_after)
        servers.append(server)
        return server, await server.start()

    yield make
    for server in servers:
        await server.stop()


def _record_connects(events: EventBus) -> list[str]:
    seen: list[str] = []
    events.on("device.connected.*", lambda event, payload=None: seen.append(event))
    return seen


async def _bring_up_waiting(manager, server, port) -> asyncio.Task:
    """Add the device and return once its first handshake is waiting."""
    task = asyncio.ensure_future(manager.add_device({
        "id": "console",
        "driver": AcmeConsoleDriver.DRIVER_INFO["id"],
        "name": "Console",
        "config": {"host": "127.0.0.1", "port": port},
    }))
    await asyncio.wait_for(server.first.wait(), 5)
    await asyncio.sleep(0.2)
    return task


@pytest.mark.parametrize("answer_after", [0.0, PROMPT_WAIT - 0.5])
async def test_reconnect_while_the_bring_up_waits_keeps_the_new_connection(
    manager, core, console, answer_after,
):
    # answer_after = 0: the new connection answers at once.
    # PROMPT_WAIT - 0.5: it answers after the first handshake would have
    # given up (that one started a second earlier), inside its own wait.
    state, events = core
    server, port = await console(answer_after)
    connects = _record_connects(events)
    bring_up = await _bring_up_waiting(manager, server, port)
    await asyncio.sleep(0.8)

    await manager.reconnect_device("console")
    await asyncio.wait_for(bring_up, 5)
    # Long enough for anything left of the first connect to have ended.
    await asyncio.sleep(PROMPT_WAIT)

    driver = manager._devices["console"]
    seen = (
        f"{server.timeline(server.accepted[0])}; retries started "
        f"{manager.retry_starts}; connected events {len(connects)}; "
        f"connected {driver.connected}"
    )
    assert len(server.accepted) == 2, seen
    assert not server.open(0), seen
    assert server.open(1), seen
    assert driver.connected
    assert state.get("device.console.connected") is True
    assert connects == ["device.connected.console"]
    assert manager.retry_starts == []
    assert "console" not in manager._reconnect_tasks


async def test_a_pause_while_the_bring_up_waits_ends_it(manager, core, console):
    state, _ = core
    server, port = await console(0.0)
    bring_up = await _bring_up_waiting(manager, server, port)

    started = time.monotonic()
    await manager.pause_device("console")
    await asyncio.wait_for(bring_up, 5)
    seen = (
        f"bring-up returned +{time.monotonic() - started:.2f}s after the pause; "
        f"retries started {manager.retry_starts}; offline_reason "
        f"{state.get('device.console.offline_reason')}"
    )
    assert time.monotonic() - started < 1.0, seen

    await asyncio.sleep(0.2)
    assert not server.open(0)
    assert state.get("device.console.paused") is True
    assert state.get("device.console.offline_reason") is None
    assert manager.retry_starts == []
    assert "console" not in manager._reconnect_tasks


async def test_a_resume_while_a_connect_waits_replaces_it(manager, core, console):
    # Resume on a device that is not paused just connects: the connect
    # already waiting is ended, not left to race the new one.
    state, events = core
    server, port = await console(0.0)
    connects = _record_connects(events)
    bring_up = await _bring_up_waiting(manager, server, port)

    await manager.resume_device("console")
    await asyncio.wait_for(bring_up, 5)
    await asyncio.sleep(PROMPT_WAIT)

    driver = manager._devices["console"]
    seen = (
        f"{server.timeline(server.accepted[0])}; retries started "
        f"{manager.retry_starts}; connected events {len(connects)}; "
        f"connected {driver.connected}"
    )
    assert server.open(1), seen
    assert not server.open(0), seen
    assert driver.connected
    assert connects == ["device.connected.console"]
    assert manager.retry_starts == []


async def test_a_setup_actions_reconnect_while_the_bring_up_waits_keeps_its_connection(
    manager, core, console,
):
    state, events = core
    server, port = await console(0.0)
    connects = _record_connects(events)
    bring_up = await _bring_up_waiting(manager, server, port)

    # As the setup action runner does around the action.
    await manager.begin_setup("console")
    await manager.reconnect_in_place("console")
    await asyncio.wait_for(bring_up, 5)
    await asyncio.sleep(PROMPT_WAIT)
    await manager.end_setup("console")

    driver = manager._devices["console"]
    assert server.open(1)
    assert not server.open(0)
    assert driver.connected
    assert connects == ["device.connected.console"]
    assert manager.retry_starts == []


class AcmeSlowBuildDriver(AcmeConsoleDriver):
    """Takes a moment to build its transport."""

    async def _create_transport(self, transport_type: str) -> None:
        await asyncio.sleep(0.5)
        await super()._create_transport(transport_type)


async def test_a_disconnect_while_the_transport_is_built_closes_what_it_built(
    core, console,
):
    import openavc.drivers.base as base_module

    state, events = core
    server, port = await console(0.0)
    driver = AcmeSlowBuildDriver(
        "console", {"host": "127.0.0.1", "port": port}, state, events,
    )
    connect = asyncio.ensure_future(driver.connect())
    await asyncio.sleep(0.1)
    await driver.disconnect()
    started = time.monotonic()

    with pytest.raises(ConnectionError) as failed:
        await asyncio.wait_for(connect, 5)
    # Not the handshake waiting out its prompt on a driver already disconnected.
    assert time.monotonic() - started < 1.0
    assert type(failed.value) is base_module.ConnectEndedError
    await asyncio.sleep(0.2)
    assert driver.transport is None
    assert not driver.connected
    assert len(server.accepted) == 1 and not server.open(0)


class AcmeOwnSessionDriver(AcmeConsoleDriver):
    """Owns its session: builds its own connection instead of a platform
    transport, and closes it in _close_session."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session: asyncio.StreamWriter | None = None

    async def _create_transport(self, transport_type: str) -> None:
        await asyncio.sleep(0.5)
        _, self.session = await asyncio.open_connection(
            self.config["host"], self.config["port"],
        )

    async def _close_session(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            session.close()
            await session.wait_closed()


async def test_a_disconnect_while_a_driver_builds_its_own_session_closes_it(core, console):
    # A device that takes one control connection at a time: a session left
    # open by an ended connect would keep a test panel or an audit out of it
    # until the next connect.
    import openavc.drivers.base as base_module

    state, events = core
    server, port = await console(0.0)
    driver = AcmeOwnSessionDriver("console", {"host": "127.0.0.1", "port": port}, state, events)
    connect = asyncio.ensure_future(driver.connect())
    await asyncio.sleep(0.1)
    await driver.disconnect()

    with pytest.raises(ConnectionError) as failed:
        await asyncio.wait_for(connect, 5)
    assert type(failed.value) is base_module.ConnectEndedError
    await asyncio.sleep(0.2)
    assert driver.session is None
    assert len(server.accepted) == 1 and not server.open(0)


async def test_a_second_connect_while_a_yaml_device_dials_leaves_it_polling(
    core, console,
):
    """A YAML driver holds its poll interval back while it connects, so the
    poll starts after its start-up queries. A second connect started while the
    first still dialed (Reconnect pressed during a bring-up) read the held-back
    0 as the interval and, finishing last, put 0 back: that connection and
    every later one polled nothing until the device was edited."""
    from openavc.drivers.configurable import create_configurable_driver_class

    yaml_driver = create_configurable_driver_class({
        "id": "acme_widget_reconnect_poll_test",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "commands": {},
        "responses": [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
        "polling": {"queries": ["PWR?\r"]},
    })

    class SlowFirstDial(yaml_driver):
        dials = 0

        async def _create_transport(self, transport_type: str) -> None:
            SlowFirstDial.dials += 1
            if SlowFirstDial.dials == 1:
                await asyncio.sleep(0.5)
            await super()._create_transport(transport_type)

    state, events = core
    _, port = await console(0.0)
    driver = SlowFirstDial(
        "widget", {"host": "127.0.0.1", "port": port, "poll_interval": 30}, state, events,
    )
    first = asyncio.ensure_future(driver.connect())
    await asyncio.sleep(0.1)
    await driver.connect()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(first, 5)
    assert driver.connected
    assert driver.config["poll_interval"] == 30
    assert driver._poll_task is not None and not driver._poll_task.done()

    # A later reconnect, with nothing else going on, still polls.
    await driver.disconnect()
    await driver.connect()
    assert driver._poll_task is not None and not driver._poll_task.done()
    await driver.disconnect()
