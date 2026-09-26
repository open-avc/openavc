"""A driver's own httpx client, recorded the way the platform's HTTP transport records.

Most Python HTTP drivers open their own ``httpx.AsyncClient``. Nothing in the
driver asks for recording: a client created by a driver's code gets the
recording hooks attached for that driver's device, so each request is
recorded as it is sent and each response once its body has been read, in the
same form as the platform transport's entries (method, target, headers with
credentials masked, status, body up to the limit). A client created anywhere
else is left exactly as it was built.
"""

from __future__ import annotations

import gzip

import httpx
import pytest

from openavc.core.device_traffic import BODY_LIMIT, RX, TX, get_traffic_recorder
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport.http_client import HTTPClientTransport, traffic_event_hooks


@pytest.fixture(autouse=True)
def clean_recorder():
    yield
    get_traffic_recorder().clear()


class _Wire(httpx.AsyncByteStream):
    """A body that arrives in pieces, as it does off the network (a response
    built from bytes is read at construction, which no device's is)."""

    def __init__(self, data: bytes, piece: int = 4096) -> None:
        self._data, self._piece = data, piece

    async def __aiter__(self):
        for i in range(0, len(self._data), self._piece):
            yield self._data[i:i + self._piece]


BROTLI_ISH = bytes([0x8B, 0x00])


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/gz":
        return httpx.Response(
            200, headers={"content-encoding": "gzip"},
            stream=_Wire(gzip.compress(b'{"power": "on"}')),
        )
    if path == "/big":
        return httpx.Response(200, stream=_Wire(b"x" * (BODY_LIMIT + 5000)))
    if path == "/br":
        return httpx.Response(200, headers={"content-encoding": "br"}, stream=_Wire(BROTLI_ISH))
    if path == "/read":
        return httpx.Response(200, json={"power": "on"})
    return httpx.Response(
        200, headers={"x-acme": "1", "content-type": "application/json"},
        stream=_Wire(b'{"power":"on"}'),
    )


class _AcmeSession:
    """A client wrapper a driver might build, the way a vendor SDK would."""

    def __init__(self, base_url: str) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(_handler),
        )


class _AcmeRest(BaseDriver):
    DRIVER_INFO = {"id": "acme_rest_own", "name": "Acme REST", "transport": "http"}

    async def send_command(self, command, params=None):
        return None

    def make_client(self, **kwargs) -> httpx.AsyncClient:
        # What a driver's _create_transport does: nothing about recording.
        return httpx.AsyncClient(
            base_url="http://10.0.0.5", transport=httpx.MockTransport(_handler), **kwargs,
        )

    def make_session(self) -> _AcmeSession:
        return _AcmeSession("http://10.0.0.5")

    async def open_platform_transport(self) -> HTTPClientTransport:
        transport = HTTPClientTransport("http://10.0.0.5", name=self.device_id)
        await transport.open()
        return transport


def _driver(device_id: str) -> _AcmeRest:
    return _AcmeRest(device_id, {}, StateStore(), EventBus())


def _entries(name: str):
    return get_traffic_recorder().get_recent(name)


def _recording(client: httpx.AsyncClient) -> int:
    return sum(
        1 for fns in client.event_hooks.values() for fn in fns
        if getattr(fn, "records_traffic", False)
    )


# ---------------------------------------------------------------------------
# Which clients are recorded
# ---------------------------------------------------------------------------


async def test_a_client_a_driver_creates_is_recorded_with_nothing_in_the_driver():
    driver = _driver("acme-own-1")
    async with driver.make_client() as client:
        response = await client.post(
            "/api/status?verbose=1", json={"get": "power"},
            headers={"Authorization": "Basic YWRtaW46aHVudGVyMjI="},
        )
    assert response.json() == {"power": "on"}  # the driver's response is untouched
    sent, received = _entries("acme-own-1")
    assert (sent.direction, sent.channel) == (TX, "http")
    assert sent.meta["method"] == "POST" and sent.meta["target"] == "/api/status?verbose=1"
    assert sent.data == b'{"get":"power"}'
    # Basic auth's base64 hides the password from any literal match: masked at capture.
    assert ["authorization", "Basic ***"] in sent.meta["headers"]
    assert (received.direction, received.channel) == (RX, "http")
    assert received.meta["status"] == 200 and received.meta["reason"] == "OK"
    assert received.meta["target"] == "/api/status?verbose=1"
    assert ["x-acme", "1"] in received.meta["headers"]
    assert received.data == b'{"power":"on"}'


async def test_a_client_built_by_a_helper_the_driver_calls_is_the_drivers():
    driver = _driver("acme-own-2")
    session = driver.make_session()
    async with session.client as client:
        await client.get("/api/status")
    assert [e.direction for e in _entries("acme-own-2")] == [TX, RX]


async def test_a_client_created_outside_any_driver_is_left_alone():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        await client.get("http://10.0.0.5/api/status")
        assert _recording(client) == 0


async def test_the_platform_transport_is_not_recorded_twice():
    driver = _driver("acme-own-3")
    transport = await driver.open_platform_transport()
    try:
        # The transport records its own traffic; its client gets nothing more.
        assert _recording(transport._client) == 0
    finally:
        await transport.close()


async def test_a_drivers_own_hooks_are_kept_and_recording_is_added_once():
    seen: list[str] = []

    async def mine(request: httpx.Request) -> None:
        seen.append(request.url.path)

    driver = _driver("acme-own-4")
    async with driver.make_client(event_hooks={"request": [mine]}) as client:
        await client.get("/api/status")
        assert client.event_hooks["request"][0] is mine
        assert _recording(client) == 2  # one request hook, one response hook
    assert seen == ["/api/status"]
    assert len(_entries("acme-own-4")) == 2

    # A client handed hooks that already record is not given a second set.
    async with driver.make_client(event_hooks=traffic_event_hooks("acme-own-4")) as client:
        assert _recording(client) == 2


# ---------------------------------------------------------------------------
# How a response is recorded
# ---------------------------------------------------------------------------


async def test_an_encoded_body_is_recorded_as_the_reader_gets_it():
    driver = _driver("acme-own-5")
    async with driver.make_client() as client:
        gz = await client.get("/gz")
        async with client.stream("GET", "/br") as br:
            raw = b"".join([chunk async for chunk in br.aiter_raw()])
    assert gz.json() == {"power": "on"}
    _, gz_rx, _, br_rx = _entries("acme-own-5")
    assert gz_rx.data == b'{"power": "on"}'
    # An encoding the standard library cannot decode is kept as received, and says so.
    assert raw == BROTLI_ISH
    assert br_rx.data == BROTLI_ISH and br_rx.meta["content_encoding"] == "br"


async def test_a_streamed_response_stays_streamed_and_a_long_body_is_cut():
    driver = _driver("acme-own-6")
    async with driver.make_client() as client:
        async with client.stream("GET", "/big") as response:
            got = b"".join([chunk async for chunk in response.aiter_bytes()])
    assert len(got) == BODY_LIMIT + 5000  # the driver reads every byte
    (_, rx) = _entries("acme-own-6")
    assert len(rx.data) == BODY_LIMIT and rx.meta["truncated"] is True


async def test_a_body_closed_unread_is_still_recorded():
    driver = _driver("acme-own-7")
    async with driver.make_client() as client:
        async with client.stream("GET", "/api/status"):
            pass
    (_, rx) = _entries("acme-own-7")
    assert rx.meta["status"] == 200 and rx.data == b""


async def test_a_response_already_read_is_recorded_from_its_content():
    driver = _driver("acme-own-8")
    async with driver.make_client() as client:
        await client.get("/read")
    (_, rx) = _entries("acme-own-8")
    assert rx.data == b'{"power":"on"}'


async def test_the_platform_transport_records_in_the_same_form():
    transport = HTTPClientTransport("http://10.0.0.5", name="acme-own-9")
    transport._client = httpx.AsyncClient(
        base_url="http://10.0.0.5", transport=httpx.MockTransport(_handler),
    )
    await transport.post("/api/status", body={"get": "power"})
    await transport._client.aclose()
    platform = [(e.direction, sorted(e.meta), e.data) for e in _entries("acme-own-9")]

    driver = _driver("acme-own-10")
    async with driver.make_client() as client:
        await client.post("/api/status", json={"get": "power"})
    own = [(e.direction, sorted(e.meta), e.data) for e in _entries("acme-own-10")]
    assert own == platform
