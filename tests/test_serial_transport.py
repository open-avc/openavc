"""Tests for serial transport (simulation mode — always runnable)."""

import asyncio

import pytest

from server.transport.frame_parsers import FixedLengthFrameParser
from server.transport.serial_transport import SerialTransport


async def test_sim_connect():
    """Simulated serial port connects successfully."""
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
    )
    assert transport.connected
    await transport.close()
    assert not transport.connected


async def test_sim_send():
    """Simulated serial port accepts sends without error."""
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
    )
    await transport.send(b"hello\r")
    await transport.close()


async def test_sim_receive_with_delimiter():
    """Simulated serial port delivers messages via frame parser."""
    received = []
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    transport.sim_receive(b"msg1\rmsg2\r")
    await asyncio.sleep(0.05)

    assert b"msg1" in received
    assert b"msg2" in received
    await transport.close()


async def test_sim_receive_raw_mode():
    """Simulated serial port in raw mode delivers data as-is."""
    received = []
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=None,
    )

    transport.sim_receive(b"raw data")
    await asyncio.sleep(0.05)

    assert received == [b"raw data"]
    await transport.close()


async def test_sim_send_and_wait():
    """send_and_wait works in simulation mode."""
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    # Inject a response shortly after sending
    async def inject_response():
        await asyncio.sleep(0.02)
        transport.sim_receive(b"OK\r")

    asyncio.create_task(inject_response())
    response = await transport.send_and_wait(b"query\r", timeout=1.0)
    assert response == b"OK"
    await transport.close()


async def test_sim_send_and_wait_timeout():
    """send_and_wait times out when no response arrives."""
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
    )

    with pytest.raises(asyncio.TimeoutError):
        await transport.send_and_wait(b"query\r", timeout=0.1)

    await transport.close()


async def test_sim_with_custom_frame_parser():
    """Simulated serial port works with a custom frame parser."""
    received = []
    parser = FixedLengthFrameParser(4)
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        frame_parser=parser,
    )

    transport.sim_receive(b"abcdef")
    await asyncio.sleep(0.05)

    assert received == [b"abcd"]
    # "ef" remains in buffer
    transport.sim_receive(b"gh")
    await asyncio.sleep(0.05)
    assert received == [b"abcd", b"efgh"]
    await transport.close()


async def test_sim_send_not_connected():
    """Sending on a closed port raises ConnectionError."""
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
    )
    await transport.close()

    with pytest.raises(ConnectionError):
        await transport.send(b"hello")


async def test_sim_async_callback():
    """Async on_data callbacks work correctly."""
    received = []

    async def on_data(data):
        received.append(data)

    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=on_data,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    transport.sim_receive(b"async_msg\r")
    await asyncio.sleep(0.1)

    assert b"async_msg" in received
    await transport.close()
