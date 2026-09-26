"""A driver's own httpx client, recorded the way the platform's HTTP transport records.

A driver that opens its own ``httpx.AsyncClient`` passes
``BaseDriver.http_traffic_hooks()``; each request is recorded as it is sent and
each response once its body has been read, in the same form as the platform
transport's entries (method, target, headers with credentials masked, status,
body up to the limit), so a device audit and the communication monitor read
both kinds of driver alike.
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


class _AcmeRest(BaseDriver):
    DRIVER_INFO = {"id": "acme_rest_own", "name": "Acme REST", "transport": "http"}

    async def send_command(self, command, params=None):
        return None


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


def _client(hooks) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://10.0.0.5", transport=httpx.MockTransport(_handler), event_hooks=hooks,
    )


def _entries(name: str):
    return get_traffic_recorder().get_recent(name)


async def test_a_driver_owned_client_records_both_directions():
    driver = _AcmeRest("acme-own-1", {}, StateStore(), EventBus())
    async with _client(driver.http_traffic_hooks()) as client:
        response = await client.post(
            "/api/status?verbose=1", json={"get": "power"},
            headers={"Authorization": "Basic YWRtaW46aHVudGVyMjI="},
        )
    assert response.json() == {"power": "on"}  # the caller's response is untouched
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


async def test_an_encoded_body_is_recorded_as_the_reader_gets_it():
    async with _client(traffic_event_hooks("acme-own-2")) as client:
        gz = await client.get("/gz")
        async with client.stream("GET", "/br") as br:
            raw = b"".join([chunk async for chunk in br.aiter_raw()])
    assert gz.json() == {"power": "on"}
    _, gz_rx, _, br_rx = _entries("acme-own-2")
    assert gz_rx.data == b'{"power": "on"}'
    # An encoding the standard library cannot decode is kept as received, and says so.
    assert raw == BROTLI_ISH
    assert br_rx.data == BROTLI_ISH and br_rx.meta["content_encoding"] == "br"


async def test_a_streamed_response_stays_streamed_and_a_long_body_is_cut():
    async with _client(traffic_event_hooks("acme-own-3")) as client:
        async with client.stream("GET", "/big") as response:
            got = b"".join([chunk async for chunk in response.aiter_bytes()])
    assert len(got) == BODY_LIMIT + 5000  # the caller reads every byte
    (_, rx) = _entries("acme-own-3")
    assert len(rx.data) == BODY_LIMIT and rx.meta["truncated"] is True


async def test_a_body_closed_unread_is_still_recorded():
    async with _client(traffic_event_hooks("acme-own-4")) as client:
        async with client.stream("GET", "/api/status"):
            pass
    (_, rx) = _entries("acme-own-4")
    assert rx.meta["status"] == 200 and rx.data == b""


async def test_a_response_already_read_is_recorded_from_its_content():
    async with _client(traffic_event_hooks("acme-own-5")) as client:
        await client.get("/read")
    (_, rx) = _entries("acme-own-5")
    assert rx.data == b'{"power":"on"}'


async def test_the_platform_transport_records_in_the_same_form():
    transport = HTTPClientTransport("http://10.0.0.5", name="acme-own-6")
    transport._client = httpx.AsyncClient(
        base_url="http://10.0.0.5", transport=httpx.MockTransport(_handler),
    )
    await transport.post("/api/status", body={"get": "power"})
    await transport._client.aclose()
    platform = [(e.direction, sorted(e.meta), e.data) for e in _entries("acme-own-6")]

    driver = _AcmeRest("acme-own-7", {}, StateStore(), EventBus())
    async with _client(driver.http_traffic_hooks()) as client:
        await client.post("/api/status", json={"get": "power"})
    own = [(e.direction, sorted(e.meta), e.data) for e in _entries("acme-own-7")]
    assert own == platform
