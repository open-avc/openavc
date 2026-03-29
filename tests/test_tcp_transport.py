"""Tests for TCP transport."""

import asyncio

import pytest

from server.transport.tcp import TCPTransport


@pytest.fixture
async def echo_server():
    """Simple TCP echo server that sends back what it receives, with delimiter."""

    async def handle(reader, writer):
        try:
            buffer = b""
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                buffer += data
                while b"\r" in buffer:
                    msg, buffer = buffer.split(b"\r", 1)
                    writer.write(msg + b"\r")
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 14353)
    yield server, 14353
    server.close()
    await server.wait_closed()


async def test_connect_and_send(echo_server):
    server, port = echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    assert transport.connected
    await transport.send(b"hello\r")
    await asyncio.sleep(0.1)
    assert received == [b"hello"]

    await transport.close()
    assert not transport.connected


async def test_delimiter_framing(echo_server):
    server, port = echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    await transport.send(b"msg1\rmsg2\r")
    await asyncio.sleep(0.1)
    assert b"msg1" in received
    assert b"msg2" in received

    await transport.close()


async def test_send_and_wait(echo_server):
    server, port = echo_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    response = await transport.send_and_wait(b"query\r", timeout=2.0)
    assert response == b"query"

    await transport.close()


async def test_connection_refused():
    with pytest.raises(ConnectionError):
        await TCPTransport.create(
            "127.0.0.1", 19999,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            timeout=1.0,
        )
