"""The HTTP transport honours ``inter_command_delay``.

A device whose manual asks for a gap between commands gets it from the end of
one exchange to the start of the next, as the byte-stream transports wait after
each write. Without a delay, requests may overlap as they always have.

A loopback HTTP server records when each request arrives and when its response
went out; every assertion is about those times, with the invented device
``acme_widget``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.http_client import HTTPClientTransport

DELAY = 0.15


@pytest.fixture
async def device():
    """A loopback HTTP device. ``log`` holds ``(path, arrived, answered)``;
    ``state["slow"]`` names a path it takes 0.3 s to answer."""
    log: list[tuple[str, float, float]] = []
    state: dict[str, Any] = {"slow": None}
    writers: list[asyncio.StreamWriter] = []

    async def handle(reader, writer):
        writers.append(writer)
        try:
            # A client that gave up before sending anything (the failed-request
            # test) leaves this read open; the teardown closes it.
            head = await reader.readuntil(b"\r\n\r\n")
            arrived = time.monotonic()
            path = head.split(b" ", 2)[1].decode()
            if path == state["slow"]:
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.02)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\nOK"
            )
            await writer.drain()
            log.append((path, arrived, time.monotonic()))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, log, state
    server.close()
    for writer in writers:
        writer.close()
    await server.wait_closed()


def _gaps(log: list[tuple[str, float, float]], paths: list[str]) -> list[float]:
    """Seconds from each listed request's response to the next one's arrival."""
    rows = sorted((r for r in log if r[0] in paths), key=lambda r: r[1])
    return [b[1] - a[2] for a, b in zip(rows, rows[1:])]


async def _transport(port: int, delay: float) -> HTTPClientTransport:
    paced = {"inter_command_delay": delay} if delay else {}
    t = HTTPClientTransport(base_url=f"http://127.0.0.1:{port}", timeout=5.0, **paced)
    await t.open()
    return t


async def test_requests_wait_the_delay_after_the_previous_response(device) -> None:
    port, log, _ = device
    t = await _transport(port, DELAY)
    try:
        await asyncio.gather(*(t.get(f"/q{i}") for i in range(4)))
    finally:
        await t.close()
    gaps = _gaps(log, [f"/q{i}" for i in range(4)])
    assert len(gaps) == 3
    assert all(gap >= DELAY - 0.01 for gap in gaps), gaps


async def test_without_a_delay_requests_still_overlap(device) -> None:
    """Unchanged at 0: concurrent requests are in flight together."""
    port, log, _ = device
    t = await _transport(port, 0.0)
    try:
        await asyncio.gather(*(t.get(f"/q{i}") for i in range(4)))
    finally:
        await t.close()
    rows = sorted(log, key=lambda r: r[1])
    # The last request arrived before the first one was answered.
    assert rows[-1][1] < rows[0][2]


async def test_a_command_waits_behind_at_most_the_request_in_flight(device) -> None:
    port, log, state = device
    state["slow"] = "/slow"
    t = await _transport(port, DELAY)
    try:
        slow = asyncio.create_task(t.get("/slow"))
        await asyncio.sleep(0.05)
        await t.get("/press")
        await slow
    finally:
        await t.close()
    slow_row = next(r for r in log if r[0] == "/slow")
    press_row = next(r for r in log if r[0] == "/press")
    gap = press_row[1] - slow_row[2]
    assert DELAY - 0.01 <= gap < DELAY + 0.25, gap


async def test_a_failed_request_still_leaves_the_gap(device) -> None:
    port, log, _ = device
    t = await _transport(port, DELAY)
    try:
        # The device takes 20 ms to answer; this request gives up long before.
        with pytest.raises(httpx.TimeoutException):
            await t.request("GET", "/x", timeout=0.001)
        started = time.monotonic()
        await t.get("/after")
        waited = time.monotonic() - started
    finally:
        await t.close()
    assert waited >= DELAY - 0.01, waited


def _acme_http(**extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_widget_http",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "http",
        "default_config": {},
        "state_variables": {},
        "commands": {},
        "responses": [],
        "polling": {"queries": ["/p1", "/p2", "/p3"]},
    }
    definition.update(extra)
    return definition


async def _connected_driver(port: int, definition: dict[str, Any]) -> Any:
    cls = create_configurable_driver_class(definition)
    drv = cls(
        device_id="acme1",
        config={
            "host": "127.0.0.1", "port": port, "poll_interval": 0,
            "inter_command_delay": DELAY,
        },
        state=StateStore(),
        events=EventBus(),
    )
    await drv.connect()
    return drv


async def test_a_yaml_http_poll_is_spaced_by_the_delay(device) -> None:
    port, log, _ = device
    drv = await _connected_driver(port, _acme_http())
    try:
        await drv.poll()
    finally:
        await drv.disconnect()
    gaps = _gaps(log, ["/p1", "/p2", "/p3"])
    assert len(gaps) == 2
    assert all(gap >= DELAY - 0.01 for gap in gaps), gaps


async def test_yaml_http_start_up_does_not_wait_twice(device) -> None:
    """on_connect used to sleep the delay itself, because the transport did
    not; now the transport does, so start-up waits once, not twice."""
    port, log, _ = device
    drv = await _connected_driver(port, _acme_http(on_connect=["/s1", "/s2", "/s3"]))
    await drv.disconnect()
    gaps = _gaps(log, ["/s1", "/s2", "/s3"])
    assert len(gaps) == 2
    assert all(DELAY - 0.01 <= gap < 1.5 * DELAY for gap in gaps), gaps
