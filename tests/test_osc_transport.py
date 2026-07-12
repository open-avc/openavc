"""Tests for OSC transport (dual-socket verify behavior)."""

import asyncio

import pytest

from server.transport.osc import OSCTransport, _OSCListenProtocol
from server.transport.osc_codec import osc_encode_message


class _StubParent:
    """Minimal stand-in for OSCTransport (the listen protocol reads _host and
    stamps _listen_last_data)."""

    def __init__(self, host):
        self._host = host
        self._listen_last_data = 0.0


def test_osc_listen_socket_drops_spoofed_feedback():
    """The dedicated OSC feedback socket only accepts datagrams from the
    configured device — a spoofed source is dropped (no callback, no liveness
    stamp) while the device's own feedback passes."""
    received: list[bytes] = []
    parent = _StubParent("10.0.0.7")
    proto = _OSCListenProtocol(received.append, "osc", parent=parent)

    proto.datagram_received(b"forged", ("10.0.0.99", 9000))
    assert received == []
    assert parent._listen_last_data == 0.0

    proto.datagram_received(b"real", ("10.0.0.7", 9000))
    assert received == [b"real"]
    assert parent._listen_last_data > 0.0


def test_osc_listen_socket_fails_open_for_hostname_target():
    """A hostname target can't be pinned to an IP without a blocking lookup, so
    the listen socket accepts any source (matches prior behaviour)."""
    received: list[bytes] = []
    parent = _StubParent("console.local")
    proto = _OSCListenProtocol(received.append, "osc", parent=parent)
    proto.datagram_received(b"x", ("10.9.9.9", 9000))
    assert received == [b"x"]


# --- Helpers ---


class _SilentUDPServer(asyncio.DatagramProtocol):
    """UDP server that receives but never replies."""

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.received.append(data)


@pytest.fixture
async def silent_remote():
    """A UDP target that ignores all incoming datagrams."""
    loop = asyncio.get_running_loop()
    server = _SilentUDPServer()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: server,
        local_addr=("127.0.0.1", 0),
    )
    _, port = transport.get_extra_info("sockname")
    yield "127.0.0.1", port, server
    transport.close()


async def _send_one_datagram(target_host: str, target_port: int, data: bytes) -> None:
    """Send a single UDP datagram to (host, port) from an ephemeral socket."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: asyncio.DatagramProtocol(),
        local_addr=("127.0.0.1", 0),
    )
    try:
        transport.sendto(data, (target_host, target_port))
        # Yield so the receiver's datagram_received fires before we close.
        await asyncio.sleep(0.05)
    finally:
        transport.close()


# --- verify(): single-socket (listen_port == 0) ---


async def test_verify_single_socket_timeout(silent_remote):
    """No listener: verify times out and returns False."""
    host, port, _ = silent_remote
    osc = OSCTransport(host=host, port=port, listen_port=0, name="single-test")
    await osc.open(local_addr="127.0.0.1")
    try:
        assert await osc.verify(timeout=0.3) is False
    finally:
        await osc.close()


async def test_verify_single_socket_no_host():
    """OSCTransport with no host returns False without sending."""
    osc = OSCTransport(host=None, port=None, listen_port=0, name="no-host")
    await osc.open(local_addr="127.0.0.1")
    try:
        assert await osc.verify(timeout=0.2) is False
    finally:
        await osc.close()


# --- verify(): dual-socket (listen_port > 0) — the A67 regression ---


async def test_verify_dual_socket_succeeds_via_listen_port(silent_remote):
    """Reply on the listen socket alone is enough for verify to succeed."""
    host, port, _ = silent_remote

    # Bind an ephemeral local listen port first to discover what's free.
    loop = asyncio.get_running_loop()
    probe_transport, _ = await loop.create_datagram_endpoint(
        lambda: asyncio.DatagramProtocol(),
        local_addr=("127.0.0.1", 0),
    )
    _, listen_port = probe_transport.get_extra_info("sockname")
    probe_transport.close()
    # Give the kernel a tick to release the port.
    await asyncio.sleep(0.01)

    osc = OSCTransport(
        host=host,
        port=port,
        listen_port=listen_port,
        name="dual-test",
    )
    await osc.open(local_addr="127.0.0.1")
    try:

        async def deliver_reply() -> None:
            # Wait briefly so verify() has snapshotted baseline and is
            # polling the listen socket.
            await asyncio.sleep(0.1)
            await _send_one_datagram(
                "127.0.0.1",
                listen_port,
                osc_encode_message("/eos/out/active/cue", [("s", "1/1")]),
            )

        reply_task = asyncio.create_task(deliver_reply())
        try:
            assert await osc.verify(timeout=2.0) is True
        finally:
            await reply_task
    finally:
        await osc.close()


async def test_verify_dual_socket_times_out_when_silent(silent_remote):
    """Dual-socket: nothing arrives on either path → False."""
    host, port, _ = silent_remote

    loop = asyncio.get_running_loop()
    probe_transport, _ = await loop.create_datagram_endpoint(
        lambda: asyncio.DatagramProtocol(),
        local_addr=("127.0.0.1", 0),
    )
    _, listen_port = probe_transport.get_extra_info("sockname")
    probe_transport.close()
    await asyncio.sleep(0.01)

    osc = OSCTransport(
        host=host,
        port=port,
        listen_port=listen_port,
        name="dual-silent",
    )
    await osc.open(local_addr="127.0.0.1")
    try:
        assert await osc.verify(timeout=0.3) is False
    finally:
        await osc.close()


async def test_verify_dual_socket_ignores_baseline_traffic(silent_remote):
    """Earlier traffic on the listen socket must not retroactively satisfy verify."""
    host, port, _ = silent_remote

    loop = asyncio.get_running_loop()
    probe_transport, _ = await loop.create_datagram_endpoint(
        lambda: asyncio.DatagramProtocol(),
        local_addr=("127.0.0.1", 0),
    )
    _, listen_port = probe_transport.get_extra_info("sockname")
    probe_transport.close()
    await asyncio.sleep(0.01)

    osc = OSCTransport(
        host=host,
        port=port,
        listen_port=listen_port,
        name="dual-baseline",
    )
    await osc.open(local_addr="127.0.0.1")
    try:
        # Deliver a packet BEFORE verify() runs. This bumps _listen_last_data
        # so verify's baseline snapshot must exclude it.
        await _send_one_datagram(
            "127.0.0.1", listen_port, osc_encode_message("/heartbeat")
        )
        # Give a moment for the listen socket to record it.
        await asyncio.sleep(0.1)
        # Now verify with a short timeout and no new traffic — must time out.
        assert await osc.verify(timeout=0.3) is False
    finally:
        await osc.close()


# --- Listen-socket async on_data tasks are strong-reffed (not GC'd mid-flight) ---
#
# OSCTransport has no send_and_wait of its own — verify() delegates to the
# underlying UDP (or TCP+SLIP) transport, so the disconnect-wake fix is covered
# by the UDP/TCP transport tests. The OSC-specific gap is the dedicated listen
# socket spawning fire-and-forget on_data tasks; that is exercised here.


async def test_listen_async_on_data_task_held_until_done():
    """The dedicated OSC listen socket strong-refs an async on_data task while
    it is in flight (so it can't be GC'd mid-await) and clears it when done."""
    from server.transport.osc import _OSCListenProtocol

    release = asyncio.Event()
    started = asyncio.Event()

    async def handler(data):
        started.set()
        await release.wait()

    proto = _OSCListenProtocol(on_data=handler, name="t")
    proto.datagram_received(osc_encode_message("/ping"), ("127.0.0.1", 9000))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert len(proto._bg_tasks) == 1  # strong ref held while awaiting
    release.set()
    await asyncio.sleep(0.05)
    assert proto._bg_tasks == set()  # cleared by the done-callback
