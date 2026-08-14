"""A response the local server produces as it goes must reach the cloud that way.

The tunnel used to proxy every request by reading the whole response and
sending it as one message. For an event stream that is not a slow version of
the right answer -- it is the wrong shape: the stream only ends when the work
does, so the entire turn had to finish inside the cloud's request deadline or
it was answered with a 504. The AI assistant is the case that made this
visible (a few tool calls and a long answer routinely pass thirty seconds),
but it applies to every ``text/event-stream`` endpoint and to anything the
local server sends without a declared length.

What is pinned here: which responses stream and which still buffer, that the
pieces arrive in order and are terminated, that a cloud which never said it
could take them keeps getting the old shape, and that a cancel stops the agent
reading a response nobody is waiting for.
"""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tests.helpers import tunnel_stream_client

SSE_HEADERS = {"content-type": "text/event-stream", "cache-control": "no-cache"}


@pytest.fixture
def tunnel_handler():
    from openavc.cloud.tunnel import TunnelHandler

    agent = MagicMock()
    agent.send_message = AsyncMock()
    return TunnelHandler(agent)


def _conn(handler, *, stream_responses: bool, tunnel_id="t-stream"):
    from openavc.cloud.tunnel import TunnelConnection

    conn = TunnelConnection(
        tunnel_id=tunnel_id,
        target_port=8080,
        data_ws=AsyncMock(),
        stream_responses=stream_responses,
    )
    handler._tunnels[tunnel_id] = conn
    return conn


def _response(chunks, *, headers=None, status=200, fail_after=None):
    """A stand-in httpx response that yields ``chunks`` from aiter_bytes()."""
    response = MagicMock()
    response.status_code = status
    response.headers = SSE_HEADERS if headers is None else headers
    response.content = b"".join(chunks)
    response.aread = AsyncMock()

    async def _aiter_bytes():
        for index, chunk in enumerate(chunks):
            if fail_after is not None and index == fail_after:
                raise httpx.ReadError("connection dropped")
            yield chunk

    response.aiter_bytes = _aiter_bytes
    return response


def _sent(conn):
    return [json.loads(call[0][0]) for call in conn.data_ws.send.call_args_list]


async def _proxy(handler, conn, path="/api/ai/chat?stream=true", request_id="r1"):
    await handler._handle_http_request(conn, {
        "type": "http_request",
        "id": request_id,
        "method": "GET",
        "path": path,
        "headers": {},
        "body": "",
    })


# ===========================================================================
# An event stream goes up in pieces
# ===========================================================================


@pytest.mark.asyncio
async def test_an_event_stream_is_sent_as_it_arrives(tunnel_handler):
    conn = _conn(tunnel_handler, stream_responses=True)
    tunnel_handler._http_client = tunnel_stream_client(
        _response([b"event: status\n\n", b"event: done\n\n"])
    )

    await _proxy(tunnel_handler, conn)

    sent = _sent(conn)
    assert [m["type"] for m in sent] == [
        "http_response_start", "http_body", "http_body", "http_body_end",
    ]
    assert all(m["id"] == "r1" for m in sent)
    assert sent[0]["status"] == 200
    assert sent[0]["headers"]["content-type"] == "text/event-stream"
    body = b"".join(base64.b64decode(m["data"]) for m in sent[1:3])
    assert body == b"event: status\n\nevent: done\n\n"


@pytest.mark.asyncio
async def test_a_streamed_response_declares_neither_length_nor_encoding(tunnel_handler):
    """Both would describe a body that isn't the one being sent: the bytes are
    decoded on the way through and the cloud re-frames the response."""
    conn = _conn(tunnel_handler, stream_responses=True)
    tunnel_handler._http_client = tunnel_stream_client(
        _response(
            [b"event: ping\n\n"],
            headers={
                "content-type": "text/event-stream",
                "content-length": "13",
                "content-encoding": "gzip",
            },
        )
    )

    await _proxy(tunnel_handler, conn)

    headers = {k.lower() for k in _sent(conn)[0]["headers"]}
    assert "content-length" not in headers
    assert "content-encoding" not in headers


@pytest.mark.asyncio
async def test_a_response_with_no_declared_length_streams_too(tunnel_handler):
    """Not just SSE: no content-length means the server is still producing it."""
    conn = _conn(tunnel_handler, stream_responses=True)
    tunnel_handler._http_client = tunnel_stream_client(
        _response([b"chunk-one", b"chunk-two"], headers={"content-type": "application/json"})
    )

    await _proxy(tunnel_handler, conn)

    assert _sent(conn)[0]["type"] == "http_response_start"


# ===========================================================================
# Everything else is untouched
# ===========================================================================


@pytest.mark.asyncio
async def test_a_measured_response_is_still_buffered(tunnel_handler):
    """A declared length means the whole body already exists. Streaming it
    would only add a message per chunk for no gain."""
    conn = _conn(tunnel_handler, stream_responses=True)
    response = _response(
        [b"<html>hi</html>"],
        headers={"content-type": "text/html", "content-length": "15"},
    )
    tunnel_handler._http_client = tunnel_stream_client(response)

    await _proxy(tunnel_handler, conn, path="/panel")

    sent = _sent(conn)
    assert [m["type"] for m in sent] == ["http_response"]
    assert base64.b64decode(sent[0]["body"]) == b"<html>hi</html>"


@pytest.mark.asyncio
async def test_an_empty_body_status_is_not_streamed(tunnel_handler):
    """204 and 304 carry no body and no length. Streaming them would spend
    three messages saying nothing."""
    conn = _conn(tunnel_handler, stream_responses=True)
    tunnel_handler._http_client = tunnel_stream_client(
        _response([], headers={}, status=204)
    )

    await _proxy(tunnel_handler, conn, path="/api/devices/x")

    assert [m["type"] for m in _sent(conn)] == ["http_response"]


@pytest.mark.asyncio
async def test_a_cloud_that_never_offered_gets_the_old_shape(tunnel_handler):
    """The capability is the receiver's. An older cloud has no handler for
    these messages, so it would answer an event stream with nothing at all."""
    conn = _conn(tunnel_handler, stream_responses=False)
    tunnel_handler._http_client = tunnel_stream_client(
        _response([b"event: status\n\n", b"event: done\n\n"])
    )

    await _proxy(tunnel_handler, conn)

    assert [m["type"] for m in _sent(conn)] == ["http_response"]


@pytest.mark.asyncio
async def test_tunnel_open_records_what_the_cloud_offered(tunnel_handler):
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)

    from unittest.mock import patch
    payload = {
        "tunnel_id": "t-cap",
        "target_port": 8080,
        "tunnel_token": "tok",
        "tunnel_data_url": "ws://localhost:9999/tunnel-data/t-cap",
        "stream_responses": True,
    }
    with patch(
        "openavc.cloud.tunnel.websockets.connect",
        new_callable=AsyncMock, return_value=mock_ws,
    ):
        await tunnel_handler.handle_tunnel_open({"type": "tunnel_open", "payload": payload})
        assert tunnel_handler._tunnels["t-cap"].stream_responses is True

        payload = {**payload, "tunnel_id": "t-old", "tunnel_data_url": "ws://x/tunnel-data/t-old"}
        del payload["stream_responses"]
        await tunnel_handler.handle_tunnel_open({"type": "tunnel_open", "payload": payload})
        assert tunnel_handler._tunnels["t-old"].stream_responses is False

    await tunnel_handler.stop()


# ===========================================================================
# Failing part-way through
# ===========================================================================


@pytest.mark.asyncio
async def test_a_break_mid_stream_ends_the_body_instead_of_claiming_502(tunnel_handler):
    """The status went up with the first message. Answering 502 now would be
    a second response to a request that already has one."""
    conn = _conn(tunnel_handler, stream_responses=True)
    tunnel_handler._http_client = tunnel_stream_client(
        _response([b"event: status\n\n", b"never sent"], fail_after=1)
    )

    await _proxy(tunnel_handler, conn)

    sent = _sent(conn)
    assert [m["type"] for m in sent] == [
        "http_response_start", "http_body", "http_body_end",
    ]
    assert sent[-1]["error"]
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_a_break_before_the_headers_is_still_a_502(tunnel_handler):
    conn = _conn(tunnel_handler, stream_responses=True)
    client = MagicMock()
    client.stream = MagicMock(side_effect=httpx.ConnectError("refused"))
    tunnel_handler._http_client = client

    await _proxy(tunnel_handler, conn)

    sent = _sent(conn)
    assert [m["type"] for m in sent] == ["http_response"]
    assert sent[0]["status"] == 502


# ===========================================================================
# Nobody is reading it any more
# ===========================================================================


@pytest.mark.asyncio
async def test_a_cancel_stops_reading_a_response_nobody_wants(tunnel_handler):
    """The browser closed the page mid-answer. Without this the agent keeps
    pulling the local response to the end and sending it into a queue that
    was thrown away."""
    conn = _conn(tunnel_handler, stream_responses=True)
    first_chunk_sent = asyncio.Event()
    never = asyncio.Event()

    async def _stalls_after_one():
        yield b"event: status\n\n"
        first_chunk_sent.set()
        await never.wait()
        yield b"event: done\n\n"

    response = _response([])
    response.aiter_bytes = _stalls_after_one
    tunnel_handler._http_client = tunnel_stream_client(response)

    queued = [json.dumps({
        "type": "http_request", "id": "r1", "method": "GET",
        "path": "/api/ai/chat?stream=true", "headers": {}, "body": "",
    })]
    cancelled = False

    async def _recv():
        nonlocal cancelled
        if queued:
            return queued.pop(0)
        if not cancelled:
            # Only after the response is genuinely under way, so this proves
            # a running proxy was stopped rather than one that never started.
            await first_chunk_sent.wait()
            cancelled = True
            return json.dumps({"type": "http_cancel", "id": "r1"})
        await never.wait()

    conn.data_ws.recv = _recv
    loop_task = asyncio.create_task(tunnel_handler._data_receive_loop(conn))

    async def _proxy_finished():
        # Not before it is under way, or an empty task set reads as "done".
        await first_chunk_sent.wait()
        while conn._data_tasks:
            await asyncio.sleep(0.01)

    # It can only finish by being cancelled: its generator is parked forever.
    await asyncio.wait_for(_proxy_finished(), timeout=5)

    types = [m["type"] for m in _sent(conn)]
    assert types == ["http_response_start", "http_body"], (
        "a cancelled response must not go on to end its body -- the cloud has "
        "already torn its side down"
    )
    assert "r1" not in conn.http_tasks

    loop_task.cancel()
    await asyncio.gather(loop_task, return_exceptions=True)
