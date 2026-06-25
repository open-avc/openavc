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


# --- Async on_data tasks are strong-reffed (not GC'd mid-flight) ---


async def test_async_on_data_task_held_until_done():
    """An async on_data handler's task is strong-reffed while in flight (so it
    can't be GC'd mid-await) and cleared by its done-callback when finished."""
    release = asyncio.Event()
    started = asyncio.Event()

    async def handler(data):
        started.set()
        await release.wait()

    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=handler,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    transport.sim_receive(b"hello\r")
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert len(transport._bg_tasks) == 1  # strong ref held while awaiting
    release.set()
    await asyncio.sleep(0.05)
    assert transport._bg_tasks == set()  # cleared by the done-callback
    await transport.close()


# --- send_and_wait fails fast on disconnect / close (no full-timeout hang) ---


async def test_send_and_wait_wakes_on_close():
    """Closing the transport while send_and_wait is parked fails it fast
    instead of blocking the full response timeout."""
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
    )
    loop = asyncio.get_running_loop()
    waiter = asyncio.create_task(transport.send_and_wait(b"query\r", timeout=5.0))
    await asyncio.sleep(0.1)  # let the waiter park on the response queue

    start = loop.time()
    await transport.close()
    with pytest.raises(ConnectionError):
        await waiter
    assert loop.time() - start < 2.0


async def test_send_and_wait_wakes_on_disconnect():
    """A link drop while send_and_wait is parked fails fast with ConnectionError,
    not after the full response timeout."""
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
    )
    loop = asyncio.get_running_loop()
    waiter = asyncio.create_task(transport.send_and_wait(b"query\r", timeout=5.0))
    await asyncio.sleep(0.1)  # let the waiter park on the response queue

    start = loop.time()
    await transport._handle_disconnect()
    with pytest.raises(ConnectionError):
        await waiter
    assert loop.time() - start < 2.0
    await transport.close()


# --- Response-queue overflow does not tear down the connection ---


async def test_response_queue_overflow_does_not_disconnect():
    """A burst of >100 framed responses while a send_and_wait waiter is active
    must not let QueueFull escape the reader loop and disconnect the device
    (the same overflow guard the TCP transport has).

    _deliver_message is the only writer into the response queue, so exercising
    it directly is faithful: pre-fix the 101st put_nowait raises QueueFull;
    post-fix the overflow frame is dropped and the earliest frames (likely the
    real response) are kept.
    """
    disconnected = []
    transport = await SerialTransport.create(
        "SIM:test", baudrate=9600,
        on_data=lambda d: None,
        on_disconnect=lambda: disconnected.append(True),
        delimiter=b"\r",
    )
    transport._waiting_for_response = True

    # maxsize is 100; deliver 150. Must not raise and must not disconnect.
    for i in range(150):
        transport._deliver_message(f"frame{i}".encode())

    assert transport.connected
    assert not disconnected
    # Drop-newest on overflow: the earliest frame is preserved for the waiter.
    assert transport._response_queue.get_nowait() == b"frame0"
    await transport.close()
