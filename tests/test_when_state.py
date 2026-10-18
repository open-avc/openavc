"""``when_state:``: a poll (or one query, or a start-up step) sent only while
the device's own state allows.

The device this exists for wakes from standby at any command it receives, so
a polled unit can never stay in standby. These run a real socket on loopback
against an invented device that does exactly that, and pin the poll loop's
side of it: a cycle that sends nothing is neither silent nor unread, so a
held poll leaves the device connected however long it is held.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import _poll_lines, validate_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class

_INTERVAL = 0.1


def _acme(**extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {},
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
            "standby": {"type": "boolean", "label": "Standby"},
            "mode": {"type": "integer", "label": "Mode"},
        },
        "commands": {
            "standby": {"send": "STBY 1\r"},
            "wake": {"send": "STBY 0\r"},
            "set_mode": {
                "send": "MODE {m}\r",
                "params": {"m": {"type": "integer", "label": "Mode"}},
            },
        },
        "responses": [
            {"match": r"PWR=(\w+)", "set": {"power": "$1"}},
            {"match": r"STBY=1", "set": {"standby": "true"}},
            {"match": r"STBY=0", "set": {"standby": "false"}},
            {"match": r"MODE=(\d)", "set": {"mode": "$1"}},
        ],
        "polling": {
            "queries": ["STBY?\r", "PWR?\r"],
            "when_state": {"standby": False},
        },
    }
    definition.update(extra)
    return definition


def _build(
    definition: dict[str, Any], port: int, state: StateStore | None = None,
    **config_extra: Any,
) -> Any:
    assert validate_driver_definition(definition) == []
    cls = create_configurable_driver_class(definition)
    config = {
        "host": "127.0.0.1",
        "port": port,
        "poll_interval": _INTERVAL,
        "max_missed_polls": 3,
        **config_extra,
    }
    return cls(
        device_id="acme1", config=config, state=state or StateStore(),
        events=EventBus(),
    )


async def _until(predicate: Any, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.fixture
async def sleepy_server():
    """A device that any line wakes from standby, except the line that puts
    it there and the standby query. Every line it receives is recorded in
    ``lines``."""
    dev: dict[str, Any] = {"standby": False, "mode": 0, "lines": [], "woken": 0}

    async def handle(reader, writer):
        buf = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\r" in buf:
                    raw, buf = buf.split(b"\r", 1)
                    line = raw.decode()
                    dev["lines"].append(line)
                    if line == "STBY 1":
                        dev["standby"] = True
                    elif dev["standby"] and line != "STBY?":
                        dev["standby"] = False
                        dev["woken"] += 1
                    if line.startswith("STBY"):
                        reply = f"STBY={int(dev['standby'])}"
                    elif line == "PWR?":
                        reply = "PWR=ON"
                    elif line.startswith("MODE "):
                        dev["mode"] = int(line[5:])
                        reply = f"MODE={dev['mode']}"
                    elif line == "MODE?":
                        reply = f"MODE={dev['mode']}"
                    else:
                        reply = ""
                    if reply:
                        writer.write(reply.encode() + b"\r")
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
    yield port, dev
    server.close()
    await server.wait_closed()


def _driver_log(caplog, *, level: int = logging.WARNING) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name.startswith("openavc.drivers") and r.levelno >= level
    ]


@pytest.mark.asyncio
async def test_a_held_poll_leaves_the_device_in_standby_and_connected(
    sleepy_server, caplog,
) -> None:
    """Held for eight intervals (more than twice max_missed_polls): nothing
    sent, nothing woken, still connected, no offline reason, no silence or
    unread-replies finding. Wake reopens the gate through its reply."""
    caplog.set_level(logging.INFO, logger="openavc.drivers")
    port, dev = sleepy_server
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("power") == "ON", 3.0)
        await drv.send_command("standby")
        assert await _until(lambda: drv.get_state("standby") is True, 3.0)
        sent_at_standby = len(dev["lines"])
        await asyncio.sleep(_INTERVAL * 8)
        assert dev["lines"][sent_at_standby:] == []
        assert dev["standby"] is True and dev["woken"] == 0
        assert drv.get_state("connected") is True
        assert drv.last_fault is None
        assert drv.get_state("offline_reason") is None
        assert drv._unread_replies_hold is False
        assert _driver_log(caplog) == []
        paused = [m for m in _driver_log(caplog, level=logging.INFO) if "Polling paused" in m]
        # Said in the words the Builder shows, with what the device reports.
        assert paused and "Standby is false" in paused[0] and "Standby true" in paused[0]
        assert "{" not in paused[0]

        await drv.send_command("wake")
        assert await _until(lambda: drv.get_state("standby") is False, 3.0)
        assert await _until(lambda: "PWR?" in dev["lines"][sent_at_standby:], 3.0)
        assert any("Polling resumed" in m for m in _driver_log(caplog, level=logging.INFO))
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_standby_sent_in_the_middle_of_a_poll_stops_that_poll(
    sleepy_server,
) -> None:
    """A poll of twelve lines takes over a second. Enter Standby sent while
    it runs is answered at once, and the lines left in that cycle must not go
    out: the first of them would wake the unit again. The transport waits
    ``inter_command_delay`` after every write, the command's included, which
    is what lets the answer arrive before the next poll line; both drivers
    that hold their poll this way set one."""
    port, dev = sleepy_server
    definition = _acme(
        polling={
            "queries": ["STBY?\r"] + ["PWR?\r"] * 11,
            "when_state": {"standby": False},
        },
    )
    drv = _build(definition, port, inter_command_delay=0.1)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("power") == "ON", 3.0)
        await asyncio.sleep(0.2)
        await drv.send_command("standby")
        assert await _until(lambda: drv.get_state("standby") is True, 3.0)
        await asyncio.sleep(_INTERVAL * 8)
        assert dev["standby"] is True
        assert dev["woken"] == 0
        assert dev["lines"][dev["lines"].index("STBY 1") + 1:] == []
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_an_unset_variable_lets_the_poll_run(sleepy_server) -> None:
    """Before the device has said whether it is in standby, the poll runs, so
    the first connect reads the state the gate needs."""
    port, dev = sleepy_server
    drv = _build(_acme(), port)
    assert drv.get_state("standby") is None
    await drv.connect()
    try:
        assert await _until(lambda: "STBY?" in dev["lines"], 3.0)
        assert await _until(lambda: drv.get_state("standby") is False, 3.0)
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_one_query_held_while_the_rest_of_the_poll_runs(sleepy_server) -> None:
    port, dev = sleepy_server
    definition = _acme(
        polling={
            "queries": [
                "STBY?\r",
                {"send": "PWR?\r", "when_state": {"standby": False}},
            ],
        },
    )
    drv = _build(definition, port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("power") == "ON", 3.0)
        # Put in standby from its front panel: the next STBY? reads it, and
        # from then on only STBY? goes out.
        dev["standby"] = True
        assert await _until(lambda: drv.get_state("standby") is True, 3.0)
        await asyncio.sleep(_INTERVAL)
        mark = len(dev["lines"])
        await asyncio.sleep(_INTERVAL * 5)
        later = dev["lines"][mark:]
        assert later.count("STBY?") >= 2
        assert "PWR?" not in later
        assert dev["woken"] == 0
        dev["standby"] = False
        assert await _until(lambda: "PWR?" in dev["lines"][mark:], 3.0)
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_list_of_values_and_a_typed_compare(sleepy_server) -> None:
    """``{mode: [0, 2]}`` on an integer variable: held at 1, open at 0 and 2."""
    port, dev = sleepy_server
    definition = _acme(
        polling={
            "queries": ["MODE?\r", "PWR?\r"],
            "when_state": {"mode": [0, 2]},
        },
    )
    drv = _build(definition, port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("mode") == 0, 3.0)
        await drv.send_command("set_mode", {"m": 1})
        assert await _until(lambda: drv.get_state("mode") == 1, 3.0)
        # A line of the cycle under way may still go out; the next is held.
        await asyncio.sleep(_INTERVAL)
        mark = len(dev["lines"])
        await asyncio.sleep(_INTERVAL * 5)
        assert dev["lines"][mark:] == []
        await drv.send_command("set_mode", {"m": 2})
        assert await _until(lambda: "PWR?" in dev["lines"][mark:], 3.0)
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_reconnect_to_a_unit_in_standby_leaves_it_there(sleepy_server) -> None:
    """A start-up step gated on the device's state is left out on a reconnect
    while the last reading says standby, and so is the poll."""
    port, dev = sleepy_server
    store = StateStore()
    definition = _acme(
        on_connect=[{"send": "PWR?\r", "when_state": {"standby": False}}],
    )
    drv = _build(definition, port, state=store)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("power") == "ON", 3.0)
        await drv.send_command("standby")
        assert await _until(lambda: drv.get_state("standby") is True, 3.0)
        await drv.disconnect()
        mark = len(dev["lines"])
        await drv.connect()
        await asyncio.sleep(_INTERVAL * 5)
        assert dev["lines"][mark:] == []
        assert dev["standby"] is True
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


# --- A device dropped for silence that comes back in standby ---
#
# After a drop for silence the automatic reconnect keeps the device offline
# until it answers a poll. A unit that comes back in standby holds its own
# poll back, so no poll ever goes out for it to answer.


@pytest.fixture
async def quiet_server():
    """A device the test changes as it runs: with ``answer`` False it records
    every line and replies to none (a unit that has hung); ``standby`` is what
    it says to ``STBY?``. ``kick()`` closes every open connection, as switching
    the unit off and on again does."""
    dev: dict[str, Any] = {
        "answer": True, "standby": False, "lines": [], "connections": 0,
    }
    writers: list[Any] = []

    async def handle(reader, writer):
        dev["connections"] += 1
        writers.append(writer)
        buf = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\r" in buf:
                    raw, buf = buf.split(b"\r", 1)
                    line = raw.decode()
                    dev["lines"].append(line)
                    if not dev["answer"]:
                        continue
                    if line == "STBY?":
                        reply = f"STBY={int(dev['standby'])}"
                    elif line == "PWR?":
                        reply = "PWR=ON"
                    else:
                        continue
                    writer.write(reply.encode() + b"\r")
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def kick() -> None:
        for writer in writers:
            writer.close()
        writers.clear()

    dev["kick"] = kick
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, dev
    server.close()
    await server.wait_closed()


@pytest.fixture
async def managed_quiet(monkeypatch, quiet_server):
    """The gated acme driver under a real DeviceManager, retries 50 ms apart.
    Start-up reads the standby state, so a reconnect learns it from the device
    before its first poll."""
    from openavc.core.device_manager import DeviceManager
    from openavc.drivers.registry import register_driver, unregister_driver

    monkeypatch.setattr(
        DeviceManager, "_reconnect_delay", lambda self, device_id, attempt: 0.05
    )
    port, dev = quiet_server
    definition = _acme(on_connect=[{"send": "STBY?\r"}])
    assert validate_driver_definition(definition) == []
    register_driver(create_configurable_driver_class(definition))
    state = StateStore()
    dm = DeviceManager(state, EventBus())
    await dm.add_device({
        "id": "acme1", "driver": definition["id"], "name": "Acme",
        "config": {
            "host": "127.0.0.1", "port": port, "poll_interval": _INTERVAL,
            "max_missed_polls": 5,
        },
    })
    try:
        yield dm, state, dev
    finally:
        await dm.disconnect_all()
        unregister_driver(definition["id"])


async def _drop_for_silence(dm: Any, state: StateStore, dev: dict[str, Any]) -> Any:
    """Connected and polling, then silent until dropped, and back on a held
    connection that has not answered. Returns the driver."""
    drv = dm._devices["acme1"]
    assert await _until(lambda: state.get("device.acme1.power") == "ON", 3.0)
    dev["answer"] = False
    assert await _until(
        lambda: state.get("device.acme1.offline_reason") == "no_response", 3.0,
    )
    assert await _until(lambda: drv._held, 3.0)
    return drv


@pytest.mark.asyncio
async def test_a_device_that_comes_back_in_standby_comes_online(managed_quiet) -> None:
    """Silent until dropped, then switched off and on again in standby: the
    reconnect reads standby from the device, the poll waits, and the device
    is online, still in standby, with nothing sent to wake it."""
    dm, state, dev = managed_quiet
    await _drop_for_silence(dm, state, dev)
    dev["answer"], dev["standby"] = True, True
    mark = len(dev["lines"])
    dev["kick"]()
    # The driver announces it; the reconnect loop then clears the reason.
    assert await _until(
        lambda: state.get("device.acme1.connected") is True
        and state.get("device.acme1.offline_reason") is None,
        3.0,
    )
    assert state.get("device.acme1.standby") is True
    assert state.get("device.acme1.offline_detail") is None
    await asyncio.sleep(_INTERVAL * 6)
    assert state.get("device.acme1.connected") is True
    assert set(dev["lines"][mark:]) <= {"STBY?"}


@pytest.mark.asyncio
async def test_an_open_held_connection_does_not_say_the_connection_dropped(
    managed_quiet,
) -> None:
    """Switched off and on while it is held, and still not answering: once
    the new connection is open, the card no longer says it dropped."""
    from openavc.core.connection_fault import (
        TRANSPORT_DISCONNECTED,
        default_fault_message,
    )

    dm, state, dev = managed_quiet
    drv = await _drop_for_silence(dm, state, dev)
    dropped = default_fault_message(TRANSPORT_DISCONNECTED)
    after_cycle = dev["connections"] + 1
    dev["kick"]()
    # The first connection after the power cycle, while it is open and held.
    assert await _until(
        lambda: dev["connections"] == after_cycle
        and drv._held
        and state.get("device.acme1.offline_reason") == "no_response",
        3.0,
    )
    assert state.get("device.acme1.offline_detail") != dropped
    assert state.get("device.acme1.connected") is False


@pytest.mark.asyncio
async def test_a_silent_device_last_seen_in_standby_stays_held(quiet_server) -> None:
    """The reading that holds the poll back is from before the drop, and the
    device answers nothing on the new connection: nothing has answered, so it
    stays offline."""
    port, dev = quiet_server
    drv = _build(_acme(on_connect=[{"send": "STBY?\r"}]), port)
    drv.set_state("standby", True)
    dev["answer"] = False
    drv._hold_next_connect = True
    await drv.connect()
    try:
        assert drv._held
        await asyncio.sleep(_INTERVAL * 6)
        assert drv._held
        assert drv.get_state("connected") is not True
    finally:
        await drv.disconnect()


def test_the_poll_length_counts_a_gated_entry_as_sent() -> None:
    """The validator's poll-length check does not know the device's state, so
    a gated entry counts as sent: the poll is that long whenever it runs."""
    queries = [
        "A\r",
        {"send": "B\r", "when_state": {"standby": False}},
        {"send": "C\r", "when": "meters"},
    ]
    assert _poll_lines(queries, {}, {"meters": False}) == 2
    assert _poll_lines(queries, {}, {"meters": True}) == 3


def test_the_gate_raises_the_computed_platform_floor() -> None:
    from openavc.drivers.spec import required_platform_version

    assert required_platform_version(_acme()) == "0.37.0"
    held_entry = _acme(
        polling={"queries": [{"send": "PWR?\r", "when_state": {"standby": False}}]},
    )
    assert required_platform_version(held_entry) == "0.37.0"
