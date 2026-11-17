"""A refused login on an HTTP device is one verdict, whichever request heard it.

The credential check (``drivers/http_credential.py``) judges start-up, poll and
follow-up answers. The event stream (``push: {type: sse}``) and the connect
check (``HEAD /``) hand it their answers too, and the connection-fault text
classifier reads its HTTP statuses from the same table. Run against a real
local HTTP server with an invented device (``acme_streamer``), so the stream's
reconnect loop and the driver's drop are the real ones.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest
from aiohttp import web

from openavc.core.connection_fault import AUTH_FAILED, classify_connection_fault
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY

EVENTS = "/api/events"


class _Device:
    """An HTTP device whose every path answers the status set for it.

    ``HEAD /`` is keyed as such; an event-stream path set to 200 streams
    until :meth:`drop_streams`."""

    def __init__(self, statuses: dict[str, int]) -> None:
        self.statuses = dict(statuses)
        self.hits: Counter[str] = Counter()
        self._streams: list[asyncio.Queue] = []
        self._runner: web.AppRunner | None = None
        self.port = 0

    @property
    def open_streams(self) -> int:
        return len(self._streams)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, handler_cancellation=True)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.drop_streams()
        if self._runner is not None:
            await self._runner.cleanup()

    def drop_streams(self) -> None:
        for queue in list(self._streams):
            queue.put_nowait(None)

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        key = "HEAD /" if request.method == "HEAD" else request.path
        self.hits[key] += 1
        status = self.statuses.get(key, 404)
        if status != 200:
            return web.Response(status=status)
        if key != EVENTS:
            return web.json_response({"level": 7})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self._streams.append(queue)
        try:
            await resp.write(b": hello\n\n")
            while await queue.get() is not None:
                pass
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._streams.remove(queue)
        return resp


async def _device(statuses: dict[str, int]) -> _Device:
    device = _Device(statuses)
    await device.start()
    return device


def _definition(*, poll: list[str] | None = None, streams: list[str] | None = None) -> dict:
    d = {
        "id": "acme_streamer",
        "name": "Acme Streamer",
        "manufacturer": "Acme",
        "category": "streaming",
        "version": "1.0.0",
        "author": "Test",
        "description": "Invented SSE-push device",
        "transport": "http",
        "config_schema": {"host": {"type": "string", "required": True, "label": "IP"}},
        "default_config": {"host": "", "ssl": False},
        "push": {"type": "sse", "path": streams or EVENTS},
        "commands": {},
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
        "responses": [{"json": True, "set": {"level": "level"}}],
    }
    if poll is not None:
        d["polling"] = {"interval": 60, "queries": poll}
    return d


def _driver(definition: dict, port: int):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    config = {"host": "127.0.0.1", "port": port, "ssl": False}
    if "polling" in definition:
        config["poll_interval"] = definition["polling"]["interval"]
    return cls("dev1", config, state, events)


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.02)


def _refused(driver) -> bool:
    fault = driver.last_fault
    return fault is not None and fault.code == AUTH_FAILED


# --- the event stream ----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_event_stream_alone_refused_stops_as_a_refused_login():
    """A device whose only traffic is its event stream, with a wrong password:
    the stream's refusals settle it after the credential check's count, the
    connection drops as auth_failed, and nothing more is sent."""
    device = await _device({"HEAD /": 401, EVENTS: 401})
    driver = _driver(_definition(), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: _refused(driver), timeout=8.0)
        assert driver.last_fault.message == (
            "The device refused the username and password. "
            "Check them in the device settings."
        )
        assert driver.get_state("connected") is False
        sent = device.hits[EVENTS]
        assert sent == 2  # with the connect check's HEAD /, the third refusal
        await asyncio.sleep(2.5)
        assert device.hits[EVENTS] == sent
    finally:
        await driver.disconnect()
        await device.stop()


@pytest.mark.asyncio
async def test_an_event_stream_that_answered_and_is_now_refused_stops_at_once():
    device = await _device({EVENTS: 200, "/api/status": 200})
    driver = _driver(_definition(poll=["/api/status"]), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: device.open_streams == 1)
        device.statuses[EVENTS] = 401
        device.drop_streams()
        await _wait_for(lambda: _refused(driver), timeout=5.0)
        assert driver.last_fault.message == (
            "The device stopped accepting the username and password. "
            "Check them in the device settings."
        )
        assert device.hits[EVENTS] == 2
    finally:
        await driver.disconnect()
        await device.stop()


@pytest.mark.asyncio
async def test_an_event_stream_the_account_may_not_read_keeps_retrying():
    """The poll answers with the same credential, so the stream's refusal is a
    page the account may not read: no verdict, and the stream keeps trying."""
    device = await _device({EVENTS: 401, "/api/status": 200})
    driver = _driver(_definition(poll=["/api/status"]), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: device.hits[EVENTS] >= 3, timeout=6.0)
        assert driver.last_fault is None
        assert driver.get_state("connected") is True
    finally:
        await driver.disconnect()
        await device.stop()


@pytest.mark.asyncio
async def test_a_stream_the_device_could_not_serve_then_refuses_is_not_a_changed_password():
    """A 503 while the device restarts does not open the stream, so the 401
    after it is not "stopped accepting": the poll answers, so it is a stream
    the account may not read, and the device stays connected."""
    device = await _device({EVENTS: 503, "/api/status": 200})
    driver = _driver(_definition(poll=["/api/status"]), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: device.hits[EVENTS] >= 1)
        device.statuses[EVENTS] = 401
        await _wait_for(lambda: device.hits[EVENTS] >= 3, timeout=6.0)
        assert driver.last_fault is None
        assert driver.get_state("connected") is True
    finally:
        await driver.disconnect()
        await device.stop()


@pytest.mark.asyncio
async def test_a_second_stream_refused_after_the_verdict_does_not_drop_again():
    """Two streams whose refusals land together: the first settles it and
    drops the connection, the second only ends its own stream."""
    driver = _driver(_definition(streams=[EVENTS, "/api/more"]), 80)
    driver._connected = True
    drops: list[int] = []
    driver._handle_transport_disconnect = lambda: (
        drops.append(1), setattr(driver, "_connected", False),
    )
    driver._judge_connect_check(401)

    assert driver._event_stream_answer(EVENTS, 401) is False
    assert driver._event_stream_answer("/api/more", 401) is True
    assert driver._event_stream_answer(EVENTS, 401) is True
    assert drops == [1]
    assert driver.last_fault.code == AUTH_FAILED


@pytest.mark.asyncio
async def test_an_event_stream_the_device_cannot_serve_keeps_retrying():
    device = await _device({"HEAD /": 401, EVENTS: 503})
    driver = _driver(_definition(), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: device.hits[EVENTS] >= 3, timeout=6.0)
        assert driver.last_fault is None
        assert driver.get_state("connected") is True
    finally:
        await driver.disconnect()
        await device.stop()


# --- the connect check -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_connect_check_s_refusal_counts_toward_the_verdict():
    """HEAD / refused is one refused login, so a wrong password is settled on
    the poll's second query rather than its third: one failed login fewer."""
    queries = ["/api/a", "/api/b", "/api/c"]
    device = await _device({"HEAD /": 401, EVENTS: 503, **{q: 401 for q in queries}})
    driver = _driver(_definition(poll=queries), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: _refused(driver), timeout=5.0)
        assert (device.hits["/api/a"], device.hits["/api/b"], device.hits["/api/c"]) == (1, 1, 0)
    finally:
        await driver.disconnect()
        await device.stop()


@pytest.mark.asyncio
async def test_a_root_page_forbidden_is_no_refused_login():
    """A 403 on / is as often a server with no index page as a refused
    login, so it does not count: the poll's third query settles it."""
    queries = ["/api/a", "/api/b", "/api/c"]
    device = await _device({"HEAD /": 403, EVENTS: 503, **{q: 401 for q in queries}})
    driver = _driver(_definition(poll=queries), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: _refused(driver), timeout=5.0)
        assert (device.hits["/api/a"], device.hits["/api/b"], device.hits["/api/c"]) == (1, 1, 1)
    finally:
        await driver.disconnect()
        await device.stop()


@pytest.mark.asyncio
async def test_a_root_page_anyone_may_read_does_not_vouch_for_the_password():
    """Many devices serve / without a login. Its answer proves nothing about
    the credential, so refused queries still settle a wrong password."""
    queries = ["/api/a", "/api/b", "/api/c"]
    device = await _device({"HEAD /": 200, EVENTS: 503, **{q: 401 for q in queries}})
    driver = _driver(_definition(poll=queries), device.port)
    try:
        await driver.connect()
        await _wait_for(lambda: _refused(driver), timeout=5.0)
        assert driver.get_state("connected") is False
        assert (device.hits["/api/a"], device.hits["/api/b"], device.hits["/api/c"]) == (1, 1, 1)
    finally:
        await driver.disconnect()
        await device.stop()


# --- through the device manager ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_device_page_says_the_login_was_refused_and_nothing_retries_it():
    """The verdict reaches the device's offline reason, and the device
    manager holds the reconnect: no second connect check, no more stream."""
    device = await _device({"HEAD /": 401, EVENTS: 401})
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    _DRIVER_REGISTRY["acme_streamer"] = create_configurable_driver_class(_definition())
    try:
        await manager.add_device({
            "id": "d1", "driver": "acme_streamer", "name": "Streamer",
            "config": {"host": "127.0.0.1", "port": device.port, "ssl": False},
        })
        await _wait_for(lambda: state.get("device.d1.offline_reason") == AUTH_FAILED, timeout=8.0)
        sent = (device.hits["HEAD /"], device.hits[EVENTS])
        await asyncio.sleep(3.0)
        assert (device.hits["HEAD /"], device.hits[EVENTS]) == sent == (1, 2)
        assert "d1" not in manager._reconnect_tasks
    finally:
        _DRIVER_REGISTRY.pop("acme_streamer", None)
        await manager.remove_device("d1")
        await device.stop()


# --- the text classifier ---------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Client error '401 Unauthorized' for url 'http://10.0.0.5/api/a'",
        "Client error '403 Forbidden' for url 'http://10.0.0.5/api/a'",
        "Client error '407 Proxy Authentication Required' for url 'http://10.0.0.5/api/a'",
    ],
)
def test_the_text_classifier_reads_every_refusing_status_from_the_credential_table(said):
    fault = classify_connection_fault(
        last_error=said, exc=None, host="10.0.0.5", port=80, transport="http",
    )
    assert fault.code == AUTH_FAILED
