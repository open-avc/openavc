"""Tests for UDP transport."""

import asyncio

import pytest

from server.transport.udp import UDPTransport, _format_data


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
    t.close()


async def test_close_clears_is_open():
    t = UDPTransport(name="test")
    await t.open()
    t.close()
    assert not t.is_open


async def test_close_before_open_is_safe():
    t = UDPTransport(name="test")
    t.close()  # Should not raise
    assert not t.is_open


async def test_double_close_is_safe():
    t = UDPTransport(name="test")
    await t.open()
    t.close()
    t.close()  # Should not raise
    assert not t.is_open


# --- Send ---


async def test_send_to_host_port(udp_receiver):
    receiver, port = udp_receiver
    t = UDPTransport(name="test")
    await t.open()

    await t.send(b"hello", "127.0.0.1", port)
    await asyncio.sleep(0.05)

    assert receiver.packets == [b"hello"]
    t.close()


async def test_send_multiple_packets(udp_receiver):
    receiver, port = udp_receiver
    t = UDPTransport(name="test")
    await t.open()

    await t.send(b"one", "127.0.0.1", port)
    await t.send(b"two", "127.0.0.1", port)
    await asyncio.sleep(0.05)

    assert receiver.packets == [b"one", b"two"]
    t.close()


async def test_send_without_open_raises():
    t = UDPTransport(name="test")
    with pytest.raises(ConnectionError, match="UDP socket not open"):
        await t.send(b"data", "127.0.0.1", 9999)


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

    t.close()


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
