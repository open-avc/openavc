"""A reconnect to a unit in standby sends it no connect check.

OSC and HTTP check the device is there before reporting it connected: an OSC
``/info`` query, an HTTP HEAD request. A unit that any message wakes from
standby was woken by that check on every reconnect, though ``polling.when_state``
held every poll back for exactly that reason. The check is now skipped while
that gate holds the poll back, and sent as before while it is open or while the
state it reads has not been reported yet (a first connect). Invented devices,
each reached on a real socket on this computer.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.osc_codec import osc_encode_message

SLEEPY_STATE = {"standby": {"type": "boolean", "label": "Standby"}}
SLEEPY_POLLING = {"queries": ["/acme/level"], "when_state": {"standby": False}}

ACME_OSC_SLEEPY = {
    "id": "acme_osc_sleepy_check",
    "name": "Acme OSC Sleepy",
    "manufacturer": "Acme",
    "category": "audio",
    "transport": "osc",
    "default_config": {},
    "state_variables": SLEEPY_STATE,
    "commands": {},
    "responses": [
        {"address": "/acme/standby", "mappings": [{"arg": 0, "state": "standby", "type": "boolean"}]},
    ],
    "on_connect": ["/acme/hello"],
    "polling": SLEEPY_POLLING,
}

ACME_HTTP_SLEEPY = {
    **ACME_OSC_SLEEPY,
    "id": "acme_http_sleepy_check",
    "name": "Acme HTTP Sleepy",
    "transport": "http",
    "responses": [],
    "on_connect": [],
    "polling": {"queries": ["/status"], "when_state": {"standby": False}},
}

INFO = osc_encode_message("/info")
HELLO = osc_encode_message("/acme/hello")


class _OscUnit(asyncio.DatagramProtocol):
    """Answers /info, as most OSC devices do, and keeps every datagram."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.received.append(data)
        if data == INFO and self.transport is not None:
            self.transport.sendto(osc_encode_message("/info", [("s", "acme")]), addr)


def _driver(definition: dict, port: int, standby: bool | None):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls(
        "dev1", {"host": "127.0.0.1", "port": port, "poll_interval": 0}, state, events,
    )
    if standby is not None:
        # Its last report, still in state when the connection opens again.
        driver.set_state("standby", standby)
    return driver


async def _connect_osc(standby: bool | None) -> list[bytes]:
    loop = asyncio.get_running_loop()
    endpoint, unit = await loop.create_datagram_endpoint(
        _OscUnit, local_addr=("127.0.0.1", 0),
    )
    port = endpoint.get_extra_info("sockname")[1]
    driver = _driver(ACME_OSC_SLEEPY, port, standby)
    try:
        await driver.connect()
        assert driver.get_state("connected") is True
        await asyncio.sleep(0.05)  # let the last datagram land
        return list(unit.received)
    finally:
        await driver.disconnect()
        endpoint.close()


async def test_a_reconnect_to_a_unit_in_standby_sends_no_info_query(caplog):
    with caplog.at_level(logging.INFO, logger="openavc"):
        received = await _connect_osc(True)
    assert received == [HELLO]
    assert (
        "Connect check skipped: it polls only while Standby is false, and the "
        "device reports Standby true"
    ) in caplog.text


@pytest.mark.parametrize("standby", [False, None], ids=["awake", "never_reported"])
async def test_an_awake_or_unknown_unit_is_checked_on_connect(standby):
    received = await _connect_osc(standby)
    assert received[0] == INFO


async def _connect_http(standby: bool) -> list[bytes]:
    request_lines: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        request_lines.append(head.split(b"\r\n", 1)[0])
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    driver = _driver(ACME_HTTP_SLEEPY, port, standby)
    try:
        await driver.connect()
        assert driver.get_state("connected") is True
        return list(request_lines)
    finally:
        await driver.disconnect()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    "standby, expected",
    [(True, []), (False, [b"HEAD / HTTP/1.1"])],
    ids=["standby", "awake"],
)
async def test_an_http_unit_in_standby_gets_no_head_request(standby, expected):
    assert await _connect_http(standby) == expected
