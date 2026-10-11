"""The poll loop's unread-replies check: replies arrive, no response rule
reads any of them.

The silence check (test_liveness.py) takes a device offline when its polls
draw nothing back. Something that answers every poll in its own language (a
telnet login prompt on the port a driver was pointed at, another product on
the address) is not silent, so that check never fires and the device reads
connected with every value blank. This check reports it, in the log and in
a declared ``last_error``, and never drops the connection: many drivers
leave some replies unread by design, so only "none at all" counts. These run
real sockets on loopback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class

_INTERVAL = 0.1
_SENTENCE = (
    "Something is answering on this connection, but this driver reads none "
    "of it: 3 polls in a row. Check the port, and that this driver matches "
    "the device."
)
_LOGGER = "openavc.drivers.base"


def _acme(last_error: bool = True, **extra: Any) -> dict[str, Any]:
    state_variables: dict[str, Any] = {"power": {"type": "string", "label": "Power"}}
    if last_error:
        state_variables["last_error"] = {"type": "string", "label": "Last Error"}
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {},
        "state_variables": state_variables,
        "commands": {},
        "responses": [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
        "polling": {"queries": ["PWR?\r"]},
    }
    definition.update(extra)
    return definition


def _build(definition: dict[str, Any], port: int) -> Any:
    cls = create_configurable_driver_class(definition)
    config = {
        "host": "127.0.0.1",
        "port": port,
        "poll_interval": _INTERVAL,
        "max_missed_polls": 3,
    }
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
async def talking_server():
    """TCP server that answers each ``PWR?`` line with ``state["reply"]``
    (bytes, possibly several lines), or nothing when it is empty."""
    state: dict[str, Any] = {"reply": b"PWR=ON\r", "queries": 0}

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
                    if state["reply"]:
                        writer.write(state["reply"])
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


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.WARNING
        and "no response rule reads" in r.getMessage()
    ]


@pytest.mark.asyncio
async def test_replies_no_rule_reads_are_reported_and_the_device_stays_up(
    talking_server, caplog,
) -> None:
    """The recorded case: a driver pointed at a telnet port gets a login
    prompt back every poll. Reported once in the log with what arrived, the
    sentence stays in last_error across the clean polls that follow (the
    stale-error clear leaves it alone), and the connection stays up."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    port, state = talking_server
    state["reply"] = b"Username and/or password incorrect.\r"
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("last_error") == _SENTENCE, 3.0)
        await asyncio.sleep(_INTERVAL * 6)
        assert drv.get_state("last_error") == _SENTENCE
        assert drv.get_state("connected") is True
        assert drv.last_fault is None
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert "'Username and/or password incorrect.'" in warnings[0]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_it_ends_on_the_first_cycle_a_reply_is_read(
    talking_server, caplog,
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    port, state = talking_server
    state["reply"] = b"Password: \r"
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("last_error") == _SENTENCE, 3.0)
        state["reply"] = b"PWR=ON\r"
        assert await _until(lambda: drv.get_state("power") == "ON", 3.0)
        assert await _until(lambda: not drv.get_state("last_error"), 3.0)
        assert any(
            "read again" in r.getMessage() for r in caplog.records
            if r.name == _LOGGER and r.levelno == logging.INFO
        )
        # A second episode is reported again.
        state["reply"] = b"Password: \r"
        assert await _until(lambda: len(_warnings(caplog)) == 2, 3.0)
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_hold_ends_with_its_connection(talking_server, caplog) -> None:
    """A reconnect starts the judgement again: the sentence a held check left
    behind clears after the new connection's first clean poll, even before
    anything is read (a silent reconnect is the silence check's to report)."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    port, state = talking_server
    state["reply"] = b"Password: \r"
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("last_error") == _SENTENCE, 3.0)
        await drv.disconnect()
        state["reply"] = b""
        await drv.connect()
        assert await _until(lambda: not drv.get_state("last_error"), 3.0)
        assert drv._unread_replies_hold is False
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_some_replies_read_is_not_a_finding(talking_server, caplog) -> None:
    """A driver that reads one reply in two (acknowledgements no rule needs,
    a dialect the unit does not speak) is working: only none counts."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    port, state = talking_server
    state["reply"] = b"ERR 002\rPWR=ON\r"
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        await asyncio.sleep(_INTERVAL * 8)
        assert state["queries"] >= 5
        assert drv.get_state("power") == "ON"
        assert not drv.get_state("last_error")
        assert _warnings(caplog) == []
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_driver_without_last_error_gets_the_log_line_only(
    talking_server, caplog,
) -> None:
    """The platform does not invent the property for a driver that never
    declared it."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    port, state = talking_server
    state["reply"] = b"Password: \r"
    drv = _build(_acme(last_error=False), port)
    await drv.connect()
    try:
        assert await _until(lambda: len(_warnings(caplog)) == 1, 3.0)
        assert drv.get_state("last_error") is None
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_it_runs_beside_a_liveness_probe(talking_server, caplog) -> None:
    """Unlike the silence check, nothing here disconnects, so a driver whose
    probe already decides when to drop is still told."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    port, state = talking_server
    state["reply"] = b"Password: \r"
    definition = _acme(
        liveness={"send": "PWR?\r", "interval": 30, "timeout": 5, "max_failures": 2},
    )
    drv = _build(definition, port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("last_error") == _SENTENCE, 3.0)
        assert drv.get_state("connected") is True
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_cycle_that_hears_nothing_is_the_silence_checks(
    talking_server, caplog,
) -> None:
    """Silence says nothing about reading: the silence check takes the
    device offline and this check stays quiet."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    port, state = talking_server
    state["reply"] = b""
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: drv.get_state("connected") is False, 3.0)
        assert drv.last_fault.code == "no_response"
        assert _warnings(caplog) == []
        assert not drv.get_state("last_error")
    finally:
        await drv.disconnect()


def test_a_python_driver_has_no_opinion() -> None:
    """A Python driver parses in its own code, so the platform cannot tell
    a reply it read from one it did not."""

    class _Acme(BaseDriver):
        DRIVER_INFO = {"id": "acme_widget_py", "name": "Acme", "transport": "tcp"}

        async def on_data_received(self, data: bytes) -> None:
            pass

        async def send_command(self, command: str, params: Any = None) -> Any:
            return None

    drv = _Acme(device_id="acme1", config={}, state=StateStore(), events=EventBus())
    assert drv._parsed_count() is None


def test_a_definition_with_no_rules_has_nothing_to_judge() -> None:
    drv = _build(_acme(responses=[]), 1)
    assert drv._parsed_count() is None
