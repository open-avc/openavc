"""Tests for UDP transport."""

import asyncio
import socket

import pytest

from openavc import reach_devices
from openavc.transport.udp import UDPTransport, _format_data


class _UDPReceiver(asyncio.DatagramProtocol):
    """Test helper that captures received datagrams."""

    def __init__(self):
        self.packets: list[bytes] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.packets.append(data)


@pytest.fixture
async def udp_receiver():
    """Listening UDP socket on localhost that captures received packets."""
    loop = asyncio.get_running_loop()
    receiver = _UDPReceiver()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: receiver,
        local_addr=("127.0.0.1", 0),
    )
    # Get the dynamically assigned port
    _, port = transport.get_extra_info("sockname")
    yield receiver, port
    transport.close()


# --- Lifecycle ---


async def test_open_sets_is_open():
    t = UDPTransport(name="test")
    assert not t.is_open
    await t.open()
    assert t.is_open
    await t.close()


async def test_close_clears_is_open():
    t = UDPTransport(name="test")
    await t.open()
    await t.close()
    assert not t.is_open


async def test_close_before_open_is_safe():
    t = UDPTransport(name="test")
    await t.close()  # Should not raise
    assert not t.is_open


async def test_double_close_is_safe():
    t = UDPTransport(name="test")
    await t.open()
    await t.close()
    await t.close()  # Should not raise
    assert not t.is_open


# --- Send ---


async def test_send_to_host_port(udp_receiver):
    receiver, port = udp_receiver
    t = UDPTransport(name="test")
    await t.open()

    await t.send_to(b"hello", "127.0.0.1", port)
    await asyncio.sleep(0.05)

    assert receiver.packets == [b"hello"]
    await t.close()


async def test_send_multiple_packets(udp_receiver):
    receiver, port = udp_receiver
    t = UDPTransport(name="test")
    await t.open()

    await t.send_to(b"one", "127.0.0.1", port)
    await t.send_to(b"two", "127.0.0.1", port)
    await asyncio.sleep(0.05)

    assert receiver.packets == [b"one", b"two"]
    await t.close()


async def test_send_without_open_raises():
    t = UDPTransport(name="test")
    with pytest.raises(ConnectionError, match="UDP socket not open"):
        await t.send_to(b"data", "127.0.0.1", 9999)


# --- Broadcast ---


async def test_broadcast_sends_to_255_255_255_255(udp_receiver):
    """Broadcast calls send with 255.255.255.255. We verify the method works
    by checking it delegates to send (which would raise if socket not open)."""
    t = UDPTransport(name="test")
    await t.open(allow_broadcast=True)

    # Broadcast to a port. We cannot easily receive broadcast on loopback,
    # but we can verify the call completes without error.
    # Use the receiver port just to have a valid target; whether the OS
    # delivers a broadcast datagram to a loopback listener is platform-dependent.
    _, port = udp_receiver
    await t.broadcast(b"\xff" * 6, port)

    await t.close()


async def test_broadcast_without_open_raises():
    t = UDPTransport(name="test")
    with pytest.raises(ConnectionError, match="UDP socket not open"):
        await t.broadcast(b"data", 9999)


# --- Custom name ---


async def test_custom_name():
    t = UDPTransport(name="wol")
    assert t._name == "wol"


async def test_default_name():
    t = UDPTransport()
    assert t._name == "udp"


# --- _format_data helper ---


def test_format_data_ascii():
    assert _format_data(b"hello") == "hello"


def test_format_data_ascii_with_whitespace():
    """Leading/trailing whitespace is stripped, but content is still printable."""
    assert _format_data(b"  hello  ") == "hello"


def test_format_data_binary():
    assert _format_data(b"\x00\x01\x02\xff") == "000102ff"


def test_format_data_mixed_nonprintable():
    """ASCII-decodable but non-printable characters fall through to hex."""
    assert _format_data(b"\x07\x08") == "0708"


def test_format_data_empty():
    assert _format_data(b"") == ""


# --- Async on_data tasks are strong-reffed (not GC'd mid-flight) ---


async def test_async_on_data_task_held_until_done(udp_receiver):
    """An async on_data handler's task is strong-reffed while in flight (so it
    can't be GC'd mid-await) and cleared by its done-callback when finished."""
    _, port = udp_receiver
    release = asyncio.Event()
    started = asyncio.Event()

    async def handler(data):
        started.set()
        await release.wait()

    transport = UDPTransport(host="127.0.0.1", port=port, on_data=handler, name="t")
    await transport.open()
    # _deliver_message is the producer the real socket calls — exercise it directly.
    transport._deliver_message(b"hello", ("127.0.0.1", 5005))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert len(transport._bg_tasks) == 1  # strong ref held while awaiting
    release.set()
    await asyncio.sleep(0.05)
    assert transport._bg_tasks == set()  # cleared by the done-callback
    await transport.close()


# --- send_and_wait fails fast on close / socket loss (no full-timeout hang) ---


async def test_send_and_wait_wakes_on_close(udp_receiver):
    """Closing the socket while send_and_wait is parked fails it fast instead
    of blocking the full response timeout. The receiver never replies."""
    _, port = udp_receiver
    transport = UDPTransport(host="127.0.0.1", port=port, name="t")
    await transport.open()
    loop = asyncio.get_running_loop()
    waiter = asyncio.create_task(transport.send_and_wait(b"q", timeout=5.0))
    await asyncio.sleep(0.1)  # let the waiter park on the response queue

    start = loop.time()
    await transport.close()
    with pytest.raises(ConnectionError):
        await waiter
    assert loop.time() - start < 2.0


async def test_send_and_wait_wakes_on_socket_loss(udp_receiver):
    """A socket lost underneath a parked send_and_wait (connection_lost) fails
    it fast rather than blocking the full timeout."""
    _, port = udp_receiver
    transport = UDPTransport(host="127.0.0.1", port=port, name="t")
    await transport.open()
    loop = asyncio.get_running_loop()
    waiter = asyncio.create_task(transport.send_and_wait(b"q", timeout=5.0))
    await asyncio.sleep(0.1)  # let the waiter park on the response queue

    start = loop.time()
    transport._protocol.connection_lost(OSError("socket gone"))
    with pytest.raises(ConnectionError):
        await waiter
    assert loop.time() - start < 2.0
    await transport.close()


# --- Source filtering (spoofed datagrams rejected) ---


async def test_deliver_drops_datagram_from_wrong_source():
    """A datagram from a host other than the target is dropped: not queued as a
    response, not passed to on_data, and it doesn't bump the liveness stamp."""
    received: list[bytes] = []
    t = UDPTransport(host="10.0.0.5", port=4000, on_data=received.append, name="t")
    t._waiting_for_response = True
    t._deliver_message(b"forged", ("10.0.0.99", 4000))
    assert received == []
    assert t._response_queue.empty()
    assert t.last_data_received == 0.0


async def test_deliver_accepts_datagram_from_target_source():
    received: list[bytes] = []
    t = UDPTransport(host="10.0.0.5", port=4000, on_data=received.append, name="t")
    t._waiting_for_response = True
    t._deliver_message(b"real", ("10.0.0.5", 4000))
    assert received == [b"real"]
    assert t._response_queue.get_nowait() == b"real"
    assert t.last_data_received > 0.0


async def test_deliver_does_not_filter_adhoc_or_multicast_targets():
    """Fail open when the source can't be predicted: an ad-hoc transport (no
    host) and a multicast target both accept any source (matches prior
    behaviour so broadcast/multicast drivers keep working)."""
    adhoc: list[bytes] = []
    t = UDPTransport(on_data=adhoc.append, name="t")  # host=None
    t._deliver_message(b"x", ("1.2.3.4", 9))
    assert adhoc == [b"x"]

    mcast: list[bytes] = []
    t2 = UDPTransport(host="239.1.2.3", port=5000, on_data=mcast.append, name="t2")
    t2._deliver_message(b"y", ("10.9.9.9", 5000))
    assert mcast == [b"y"]


async def test_send_and_wait_rejects_spoofed_source(udp_receiver):
    """A response from the wrong source doesn't satisfy send_and_wait — it
    times out rather than returning the forged payload."""
    _, port = udp_receiver
    t = UDPTransport(host="127.0.0.1", port=port, name="t")
    await t.open()
    try:
        async def spoof():
            await asyncio.sleep(0.05)
            t._deliver_message(b"forged", ("10.0.0.99", port))

        asyncio.create_task(spoof())
        with pytest.raises(asyncio.TimeoutError):
            await t.send_and_wait(b"query", timeout=0.3)
    finally:
        await t.close()


async def test_send_and_wait_accepts_response_from_target(udp_receiver):
    _, port = udp_receiver
    t = UDPTransport(host="127.0.0.1", port=port, name="t")
    await t.open()
    try:
        async def reply():
            await asyncio.sleep(0.05)
            t._deliver_message(b"pong", ("127.0.0.1", port))

        asyncio.create_task(reply())
        assert await t.send_and_wait(b"ping", timeout=0.5) == b"pong"
    finally:
        await t.close()


# --- A multicast send with nothing chosen in Reach devices through ---


class _RecordingSocket:
    """Stands in for the transport's own socket: records which multicast
    interface each copy was sent under instead of sending."""

    def __init__(self, refuse=()):
        self.calls: list[tuple] = []
        self.refuse = set(refuse)
        self._interface = "0.0.0.0"

    def setsockopt(self, level, option, value):
        if option == socket.IP_MULTICAST_IF:
            self._interface = socket.inet_ntoa(value)
            self.calls.append(("interface", self._interface))

    def sendto(self, data, addr):
        if self._interface in self.refuse:
            raise OSError("the adapter cannot send")
        self.calls.append(("send", self._interface, addr))


async def _multicast_transport(monkeypatch, sources, local_addr=None, refuse=(), host=None, port=None):
    """An Acme lighting transport on a machine whose connected adapters are
    ``sources``; returns it, its recording socket, and what went through
    asyncio's transport instead."""
    monkeypatch.setattr(reach_devices, "broadcast_sources", lambda: list(sources))
    t = UDPTransport(host=host, port=port, name="acme_lights")
    await t.open(local_addr=local_addr)
    recording = _RecordingSocket(refuse)
    t._sock = recording
    direct: list[tuple[str, int]] = []
    monkeypatch.setattr(t._transport, "sendto", lambda data, addr: direct.append(addr))
    return t, recording, direct


GROUP = ("239.255.0.1", 5568)


async def test_a_multicast_send_goes_once_through_every_adapter(monkeypatch):
    t, recording, direct = await _multicast_transport(monkeypatch, ["10.20.30.5", "192.168.1.40"])
    try:
        await t.send_to(b"DMX", *GROUP)
    finally:
        await t.close()
    assert recording.calls == [
        ("interface", "10.20.30.5"), ("send", "10.20.30.5", GROUP),
        ("interface", "192.168.1.40"), ("send", "192.168.1.40", GROUP),
        ("interface", "0.0.0.0"),
    ]
    assert direct == []
    assert t.send_count == 1


@pytest.mark.parametrize(
    "local_addr, host",
    [("127.0.0.1", GROUP[0]), (None, "10.0.0.5")],
    ids=["an adapter chosen", "a device's own address"],
)
async def test_it_goes_once_as_before(monkeypatch, local_addr, host):
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], local_addr=local_addr
    )
    try:
        await t.send_to(b"DMX", host, GROUP[1])
    finally:
        await t.close()
    assert recording.calls == []
    assert direct == [(host, GROUP[1])]


async def test_an_adapter_that_cannot_send_is_skipped_for_multicast(monkeypatch):
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], refuse={"10.20.30.5"}
    )
    try:
        await t.send_to(b"DMX", *GROUP)
    finally:
        await t.close()
    assert [call for call in recording.calls if call[0] == "send"] == [("send", "192.168.1.40", GROUP)]
    assert direct == []


@pytest.mark.parametrize("sources", [[], ["10.20.30.5"]], ids=["no adapter", "none can send"])
async def test_with_no_copy_sent_it_goes_once_as_the_os_picks(monkeypatch, sources):
    t, recording, direct = await _multicast_transport(monkeypatch, sources, refuse=set(sources))
    try:
        await t.send_to(b"DMX", *GROUP)
    finally:
        await t.close()
    assert [call for call in recording.calls if call[0] == "send"] == []
    assert direct == [GROUP]


async def test_a_multicast_query_goes_once_through_every_adapter(monkeypatch):
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], host=GROUP[0], port=GROUP[1]
    )

    async def answer():
        await asyncio.sleep(0.05)
        t._deliver_message(b"OK", ("10.20.30.9", GROUP[1]))

    reply = asyncio.create_task(answer())
    try:
        assert await t.send_and_wait(b"STATUS?", timeout=2.0) == b"OK"
    finally:
        await reply
        await t.close()
    assert [call for call in recording.calls if call[0] == "send"] == [
        ("send", "10.20.30.5", GROUP), ("send", "192.168.1.40", GROUP),
    ]
    assert direct == []
