"""Tests for UDP transport."""

import asyncio
import logging
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


# --- Socket errors are logged once a run, not once a datagram ---


def _said(caplog) -> list:
    """Everything the transport logged at INFO or above (TX lines are DEBUG)."""
    return [
        r for r in caplog.records
        if r.name == "openavc.transport.udp" and r.levelno >= logging.INFO
    ]


async def test_a_stream_the_os_refuses_logs_its_first_error_and_says_when_it_goes_out_again(
    udp_receiver, monkeypatch, caplog,
):
    receiver, port = udp_receiver
    t = UDPTransport(host="127.0.0.1", port=port, name="sacn")
    await t.open()
    sendto = t._transport.sendto

    def refused(data, addr):
        # What asyncio does with a datagram the OS refuses: no raise, the
        # error handed to the protocol during the call.
        t._protocol.error_received(OSError(65, "No route to host"))

    try:
        with caplog.at_level("DEBUG", logger="openavc.transport.udp"):
            monkeypatch.setattr(t._transport, "sendto", refused)
            for _ in range(20):
                await t.send(b"DMX")
            gone = _said(caplog)
            assert len(gone) == 1, [r.message for r in gone]
            assert gone[0].levelname == "WARNING" and "No route to host" in gone[0].message
            assert t.last_error == "[Errno 65] No route to host", "every error still recorded"

            caplog.clear()
            monkeypatch.setattr(t._transport, "sendto", sendto)
            for _ in range(3):
                await t.send(b"DMX")
            back = _said(caplog)
            assert [r.levelname for r in back] == ["INFO"], [r.message for r in back]
            assert f"to 127.0.0.1:{port} go out again, after 20 errors" in back[0].message
            # Windows' event loop queues a datagram behind one still going
            # out, so they are waited for before the next outage starts.
            for _ in range(100):
                if len(receiver.packets) == 3:
                    break
                await asyncio.sleep(0.01)
            assert len(receiver.packets) == 3, "the datagrams sent between the outages arrived"

            caplog.clear()
            monkeypatch.setattr(t._transport, "sendto", refused)
            await t.send(b"DMX")
            await t.send(b"DMX")
            assert [r.levelname for r in _said(caplog)] == ["WARNING"], "a second outage is said again"
    finally:
        await t.close()


async def test_an_error_reported_after_each_send_stays_one_run(udp_receiver, caplog):
    # Some sockets hear of a closed port after the datagram went out, not
    # during the send: each send looks clean, then an error arrives.
    _, port = udp_receiver
    t = UDPTransport(host="127.0.0.1", port=port, name="t")
    await t.open()
    try:
        with caplog.at_level("DEBUG", logger="openavc.transport.udp"):
            for _ in range(10):
                await t.send(b"POLL")
                t._protocol.error_received(ConnectionResetError(10054, "Connection reset"))
            assert [r.levelname for r in _said(caplog)] == ["WARNING"], "one line, not one a datagram"

            caplog.clear()
            await t.send(b"POLL")
            assert _said(caplog) == [], "one clean send is not proof: the error may follow it"
            await t.send(b"POLL")
            back = _said(caplog)
            assert [r.levelname for r in back] == ["INFO"] and "after 10 errors" in back[0].message
    finally:
        await t.close()


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


async def _multicast_transport(
    monkeypatch, sources, local_addr=None, refuse=(), host=None, port=None, chosen=None,
):
    """An Acme lighting transport on a machine whose connected adapters are
    ``sources``, and ``chosen`` the adapter Reach devices through names after
    the socket opened (None: every adapter); returns it, its recording
    socket, and what went through asyncio's transport instead."""
    monkeypatch.setattr(reach_devices, "broadcast_sources", lambda: list(sources))
    monkeypatch.setattr(reach_devices, "is_chosen", lambda: chosen is not None)
    monkeypatch.setattr(reach_devices, "pinned", lambda: chosen)
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


ETHERNET_CHOSEN = reach_devices.Resolved(
    identity={"name": "Ethernet"}, address="10.20.30.5", adapter_name="Ethernet",
)
ETHERNET_GONE = reach_devices.Resolved(
    identity={"name": "Ethernet"}, adapter_name="Ethernet", connected=False,
)
# Unplugged, it keeps its last address for connections, which is nowhere to send from.
ETHERNET_UNPLUGGED = reach_devices.Resolved(
    identity={"name": "Ethernet"}, address="10.20.30.5", adapter_name="Ethernet", connected=False,
)


async def test_an_adapter_chosen_after_the_socket_opened_takes_every_copy(monkeypatch):
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], chosen=ETHERNET_CHOSEN,
    )
    try:
        await t.send_to(b"DMX", *GROUP)
    finally:
        await t.close()
    assert recording.calls == [
        ("interface", "10.20.30.5"), ("send", "10.20.30.5", GROUP), ("interface", "0.0.0.0"),
    ]
    assert direct == []


@pytest.mark.parametrize("local_addr", [None, "127.0.0.1"], ids=["opened unbound", "opened bound"])
async def test_sends_through_a_chosen_adapter_go_out_in_the_order_they_were_built(monkeypatch, local_addr):
    # A lighting stream numbers each datagram as it builds it and counts the
    # number once the datagram is sent (sACN's sequence byte), and a receiver
    # drops one whose number is not newer than the last it took. So a send
    # started while another is under way must not be built with the same
    # number: send_to may not stop to look the adapter up before it sends.
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], local_addr=local_addr, chosen=ETHERNET_CHOSEN,
    )
    sequence = 0
    built: list[int] = []

    async def send() -> None:
        nonlocal sequence
        built.append(sequence)
        await t.send_to(bytes([sequence]), *GROUP)
        sequence += 1

    try:
        await asyncio.gather(send(), send(), send())
    finally:
        await t.close()
    assert built == [0, 1, 2]


async def test_a_chosen_adapter_holding_only_a_169_254_address_takes_the_copy(monkeypatch):
    no_dhcp = reach_devices.Resolved(
        identity={"name": "Ethernet"}, adapter_name="Ethernet", connected=False,
        link_local_address="169.254.7.7",
    )
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["192.168.1.40", "169.254.7.7"], chosen=no_dhcp,
    )
    try:
        await t.send_to(b"DMX", *GROUP)
    finally:
        await t.close()
    assert [call for call in recording.calls if call[0] == "send"] == [("send", "169.254.7.7", GROUP)]
    assert direct == []


@pytest.mark.parametrize(
    "gone", [ETHERNET_GONE, ETHERNET_UNPLUGGED], ids=["no address", "its last address kept"],
)
async def test_a_chosen_adapter_with_no_address_sends_nothing_anywhere(monkeypatch, caplog, gone):
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], chosen=gone,
    )
    try:
        with caplog.at_level("WARNING", logger="openavc.transport.udp"):
            await t.send_to(b"DMX", *GROUP)
            await t.send_to(b"DMX", *GROUP)
        with pytest.raises(reach_devices.AdapterUnavailable, match="Ethernet isn't connected"):
            t.host, t.port = GROUP
            await t.send_and_wait(b"STATUS?", timeout=0.1)
    finally:
        await t.close()
    assert recording.calls == [] and direct == [], "never through the other adapters"
    assert t.send_count == 0
    said = [r.message for r in caplog.records if "not sent" in r.message]
    assert len(said) == 1 and "Ethernet isn't connected" in said[0], "once, not per datagram"


async def test_a_socket_bound_to_the_chosen_adapter_sends_nothing_while_it_is_unplugged(
    monkeypatch, caplog,
):
    # Opened after the choice, bound to the adapter's address, which it keeps when unplugged.
    unplugged = reach_devices.Resolved(
        identity={"name": "Ethernet"}, address="127.0.0.1", adapter_name="Ethernet", connected=False,
    )
    plugged = reach_devices.Resolved(
        identity={"name": "Ethernet"}, address="127.0.0.1", adapter_name="Ethernet",
    )
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], local_addr="127.0.0.1", chosen=unplugged,
    )
    try:
        with caplog.at_level("WARNING", logger="openavc.transport.udp"):
            await t.send_to(b"DMX", *GROUP)
            await t.send_to(b"DMX", *GROUP)
        assert recording.calls == [] and direct == [], "the OS would refuse every one"
        assert t.send_count == 0
        said = [r.message for r in caplog.records if "not sent" in r.message]
        assert len(said) == 1 and "Ethernet isn't connected" in said[0], "once, not per datagram"

        monkeypatch.setattr(reach_devices, "pinned", lambda: plugged)
        await t.send_to(b"DMX", *GROUP)
        assert direct == [GROUP], "plugged back in: from the bound address, as before"

        caplog.clear()
        monkeypatch.setattr(reach_devices, "pinned", lambda: unplugged)
        with caplog.at_level("WARNING", logger="openavc.transport.udp"):
            await t.send_to(b"DMX", *GROUP)
        assert [r for r in caplog.records if "not sent" in r.message], "a second outage is said again"
    finally:
        await t.close()


async def test_a_socket_bound_to_another_address_is_left_to_the_os(monkeypatch):
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], local_addr="127.0.0.1", chosen=ETHERNET_UNPLUGGED,
    )
    try:
        await t.send_to(b"DMX", *GROUP)
    finally:
        await t.close()
    assert recording.calls == [] and direct == [GROUP]


async def test_a_copy_the_chosen_adapter_cannot_send_is_not_sent_another_way(monkeypatch):
    t, recording, direct = await _multicast_transport(
        monkeypatch, ["10.20.30.5", "192.168.1.40"], refuse={"10.20.30.5"}, chosen=ETHERNET_CHOSEN,
    )
    try:
        with pytest.raises(ConnectionError, match="through 10.20.30.5 failed"):
            await t.send_to(b"DMX", *GROUP)
    finally:
        await t.close()
    assert [call for call in recording.calls if call[0] == "send"] == []
    assert direct == []


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
