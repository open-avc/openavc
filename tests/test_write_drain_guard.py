"""A write the device never accepts must fail, not hang (openavc/transport/write_drain.py).

``StreamWriter.drain()`` waits on the peer taking the bytes, and asyncio gives
it no deadline of its own. A device that holds its socket open but stops
reading therefore used to park the send forever: the command never returned,
and nothing raised, so the device went on reporting ``connected`` while every
button aimed at it hung.

The end-to-end cases here run against a real socket whose peer never calls
``recv`` — the only way to produce genuine TCP backpressure. How many bytes
that takes is a kernel buffer question (send buffer plus the peer's receive
window, both auto-tuned), so they write in a loop until the guard trips rather
than asserting any particular size; the point under test is that it trips at
all, and quickly. Uses an invented device label and a loopback port; no real
product or address is named.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from openavc.core.connection_fault import (
    WRITE_STALLED,
    ConnectionFaultError,
    _DRIVER_FAULT_CODES,
    default_fault_message,
    is_permanent_fault,
)
from openavc.transport.tcp import TCPTransport
from openavc.transport.write_drain import (
    WRITE_DRAIN_TIMEOUT_S,
    close_or_abandon,
    drain_or_stalled,
)

DEVICE = "acme-widget"


# --- The taxonomy entry ----------------------------------------------------

def test_write_stalled_is_a_raisable_code_with_its_own_sentence():
    assert WRITE_STALLED in _DRIVER_FAULT_CODES
    ConnectionFaultError("", code=WRITE_STALLED)  # must not raise on the code
    msg = default_fault_message(WRITE_STALLED, "acme-widget at 10.0.0.9:4321")
    assert "acme-widget at 10.0.0.9:4321" in msg
    assert msg != default_fault_message("transport_disconnected")


def test_a_stalled_write_keeps_retrying():
    """A wedged device heals when somebody power-cycles it, so the reconnect
    loop must not give up the way it does for a bad password."""
    assert not is_permanent_fault(WRITE_STALLED)


# --- The guard itself ------------------------------------------------------

class _NeverDrains:
    async def drain(self) -> None:
        await asyncio.Event().wait()  # the peer never takes the bytes


class _DrainsFine:
    def __init__(self) -> None:
        self.calls = 0

    async def drain(self) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_guard_raises_a_typed_fault_when_the_peer_never_takes_the_bytes():
    with pytest.raises(ConnectionFaultError) as caught:
        await drain_or_stalled(_NeverDrains(), DEVICE, timeout=0.05)
    assert caught.value.fault_code == WRITE_STALLED
    # A ConnectionError subclass, so the API answers 503 rather than 500.
    assert isinstance(caught.value, ConnectionError)
    # The message lands on the device card as offline_detail, so it carries no
    # log furniture — no bracketed device label, no deadline, no module names.
    said = str(caught.value)
    assert DEVICE not in said
    assert "[" not in said and "drain" not in said.lower()
    assert "stopped accepting data" in said


@pytest.mark.asyncio
async def test_guard_is_invisible_when_the_device_is_reading():
    writer = _DrainsFine()
    await drain_or_stalled(writer, DEVICE, timeout=0.05)
    assert writer.calls == 1


@pytest.mark.asyncio
async def test_guard_reads_the_module_default_at_call_time(monkeypatch):
    """The transports call the guard with no timeout, so the default has to be
    live rather than bound when the function was defined — otherwise neither a
    test nor a future setting could move it."""
    monkeypatch.setattr(
        "openavc.transport.write_drain.WRITE_DRAIN_TIMEOUT_S", 0.05
    )
    with pytest.raises(ConnectionFaultError):
        await drain_or_stalled(_NeverDrains(), DEVICE)


def test_the_default_deadline_is_shorter_than_a_person_will_wait():
    assert 0 < WRITE_DRAIN_TIMEOUT_S <= 15


# --- End to end, against a socket nobody is reading ------------------------

class _SilentPeer:
    """Accepts one connection and never reads it, so the sender's buffer fills."""

    def __init__(self) -> None:
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._held: list[socket.socket] = []

    async def accept_one(self) -> None:
        loop = asyncio.get_running_loop()
        self._srv.setblocking(False)
        conn, _ = await loop.sock_accept(self._srv)
        self._held.append(conn)  # held open, deliberately never recv()'d

    def close(self) -> None:
        for c in self._held:
            c.close()
        self._srv.close()


async def _stall_a_real_socket(monkeypatch) -> tuple[TCPTransport, ConnectionFaultError]:
    monkeypatch.setattr(
        "openavc.transport.write_drain.WRITE_DRAIN_TIMEOUT_S", 0.25
    )
    peer = _SilentPeer()
    accepting = asyncio.create_task(peer.accept_one())
    try:
        transport = await TCPTransport.create(
            "127.0.0.1", peer.port, lambda _d: None, lambda: None,
            delimiter=b"\n", timeout=5.0, name=DEVICE,
        )
        await accepting
        chunk = b"A" * 65536
        # Write until backpressure bites. Bounded so a kernel with an enormous
        # buffer fails the test instead of running forever.
        for _ in range(400):
            try:
                await transport.send(chunk)
            except ConnectionFaultError as e:
                return transport, e
        pytest.fail("the peer absorbed 25 MB without stalling — guard never ran")
    finally:
        accepting.cancel()
        peer.close()


@pytest.mark.asyncio
async def test_a_real_unread_socket_fails_the_send_instead_of_hanging(monkeypatch):
    transport, err = await _stall_a_real_socket(monkeypatch)
    assert err.fault_code == WRITE_STALLED
    assert transport.last_fault is not None
    assert transport.last_fault.code == WRITE_STALLED


@pytest.mark.asyncio
async def test_a_stalled_device_stops_reporting_itself_connected(monkeypatch):
    """The half of this that a user sees: before the guard, the send blocked
    forever, so nothing ever ran the disconnect path and the device card kept
    its green 'connected' while every command aimed at it hung."""
    transport, _ = await _stall_a_real_socket(monkeypatch)
    assert transport.connected is False


@pytest.mark.asyncio
async def test_a_stalled_send_wakes_a_parked_send_and_wait(monkeypatch):
    """send_and_wait holds the lock across send+wait, so a send that never
    returned used to strand every later caller behind it too."""
    transport, _ = await _stall_a_real_socket(monkeypatch)
    with pytest.raises((ConnectionError, OSError)):
        await asyncio.wait_for(
            transport.send_and_wait(b"ping\n", timeout=0.25), timeout=5.0
        )


# --- Closing, which is what actually lets the device come back -------------

class _NeverCloses:
    def __init__(self) -> None:
        self.closed = False
        self.aborted = False
        self.transport = self

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        await asyncio.Event().wait()  # the peer never takes the queued bytes

    def abort(self) -> None:
        self.aborted = True


@pytest.mark.asyncio
async def test_close_gives_up_on_a_peer_that_never_takes_the_bytes():
    writer = _NeverCloses()
    await asyncio.wait_for(
        close_or_abandon(writer, DEVICE, timeout=0.05), timeout=5.0
    )
    assert writer.closed
    assert writer.aborted, "the socket must be dropped, not left holding the fd"


@pytest.mark.asyncio
async def test_close_never_raises():
    """Teardown runs on the failure path, and the disconnect event that starts
    the reconnect is emitted after it — so nothing here may escape."""

    class _Hostile:
        transport = None

        def close(self) -> None:
            raise OSError("already gone")

        async def wait_closed(self) -> None:
            raise AssertionError("unreachable")

    await close_or_abandon(_Hostile(), DEVICE, timeout=0.05)


@pytest.mark.asyncio
async def test_a_stalled_device_still_gets_torn_down_and_can_reconnect(monkeypatch):
    """The whole point of bounding close: the disconnect has to complete, or
    nothing downstream ever runs and the device is offline for good."""
    monkeypatch.setattr(
        "openavc.transport.write_drain.CLOSE_FLUSH_TIMEOUT_S", 0.25
    )
    transport, _ = await _stall_a_real_socket(monkeypatch)
    await asyncio.wait_for(transport.close(), timeout=5.0)
    assert transport.connected is False
