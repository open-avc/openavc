"""A discovered peer that turns up somewhere else is dialed where it is now.

A peer is dialed at the address, port and scheme of the first beacon heard
from it. Its reconnect loop kept dialing that endpoint for as long as the
peer kept beaconing, and a fresh beacon only refreshed ``last_seen``, which
also kept the peer from ever being pruned and rediscovered. So a peer that
came back on a new address (a DHCP lease, a different adapter), a new port,
or with HTTPS turned on was never reached again. When that peer has the
larger instance id the pair stays apart for good: its own dial is turned away
as a duplicate, because the other side's loop is still running.

Platform test: no real peer, the outbound connect is stubbed.
"""

import asyncio
import json

import pytest

from openavc.core.isc import DISCOVERY_MAGIC, PeerInfo

PEER_ID = "ffff-2222"


def _beacon(port: int = 8080, **extra) -> bytes:
    body = {"instance_id": PEER_ID, "name": "Room B", "port": port,
            "version": "0.37.0", "protocol": "1", **extra}
    return DISCOVERY_MAGIC + json.dumps(body).encode()


class _UnreachablePeer:
    """Refuses every dial, and records where each one went."""

    def __init__(self, isc, during_first_dial=None):
        self.dials: list[tuple[str, int, str]] = []
        self.dialed = asyncio.Event()
        self._during_first_dial = during_first_dial
        isc._outbound_connect = self._connect

    async def _connect(self, peer_id, host, port, scheme):
        self.dials.append((host, port, scheme))
        if len(self.dials) == 1 and self._during_first_dial:
            self._during_first_dial()
        self.dialed.set()
        raise ConnectionRefusedError("connection refused")

    async def next_dial(self, timeout: float = 2.0) -> None:
        """Well inside the first backoff (5 s): a dial this soon was asked for."""
        self.dialed.clear()
        await asyncio.wait_for(self.dialed.wait(), timeout=timeout)


async def _until(condition, timeout: float = 2.0) -> None:
    """Well inside the first backoff (5 s), like ``next_dial``."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("not reached in time")
        await asyncio.sleep(0.01)


async def _stop(isc):
    isc._running = False
    isc._wake_reconnects()
    tasks = list(isc._connect_tasks.values())
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.0)


async def test_a_beacon_from_a_new_address_is_dialed_there_now(isc):
    peer = _UnreachablePeer(isc)
    try:
        isc._handle_beacon(_beacon(), ("192.0.2.10", 51000))
        await asyncio.wait_for(peer.dialed.wait(), timeout=2.0)

        isc._handle_beacon(_beacon(), ("192.0.2.20", 51000))
        await peer.next_dial()

        assert peer.dials == [("192.0.2.10", 8080, "http"), ("192.0.2.20", 8080, "http")]
        assert isc._peers[PEER_ID].host == "192.0.2.20"
    finally:
        await _stop(isc)


async def test_a_new_port_is_dialed_too(isc):
    peer = _UnreachablePeer(isc)
    try:
        isc._handle_beacon(_beacon(port=8080), ("192.0.2.10", 51000))
        await asyncio.wait_for(peer.dialed.wait(), timeout=2.0)

        isc._beacon_rate.clear()  # the same address again, inside its 5 s limit
        isc._handle_beacon(_beacon(port=8090), ("192.0.2.10", 51000))
        await peer.next_dial()

        assert peer.dials[-1] == ("192.0.2.10", 8090, "http")
    finally:
        await _stop(isc)


async def test_https_turned_on_is_dialed_over_its_tls_port(isc):
    peer = _UnreachablePeer(isc)
    try:
        isc._handle_beacon(_beacon(), ("192.0.2.10", 51000))
        await asyncio.wait_for(peer.dialed.wait(), timeout=2.0)

        isc._beacon_rate.clear()
        isc._handle_beacon(_beacon(scheme="https", tls_port=8443), ("192.0.2.10", 51000))
        await peer.next_dial()

        assert peer.dials[-1] == ("192.0.2.10", 8443, "https")
        assert isc._peers[PEER_ID].scheme == "https"
    finally:
        await _stop(isc)


async def test_a_move_heard_during_a_dial_is_not_missed(isc):
    """The beacon lands while the loop is dialing, not while it waits: the
    retry still goes to the new address without sitting out the backoff."""
    peer = _UnreachablePeer(
        isc, during_first_dial=lambda: isc._handle_beacon(_beacon(), ("192.0.2.20", 51000)),
    )
    try:
        isc._handle_beacon(_beacon(), ("192.0.2.10", 51000))
        await _until(lambda: len(peer.dials) >= 2)

        assert peer.dials == [("192.0.2.10", 8080, "http"), ("192.0.2.20", 8080, "http")]
    finally:
        await _stop(isc)


async def test_a_connected_peer_heard_from_another_address_is_left_alone(isc):
    """A peer with two adapters beacons from both; the working connection
    stays where it is."""
    isc._peers[PEER_ID] = PeerInfo(
        instance_id=PEER_ID, name="Room B", host="192.0.2.10", port=8080, connected=True,
    )

    isc._handle_beacon(_beacon(port=8090, scheme="https", tls_port=8443), ("192.0.2.20", 51000))

    peer = isc._peers[PEER_ID]
    assert (peer.host, peer.port, peer.scheme) == ("192.0.2.10", 8080, "http")
    assert isc._connect_tasks == {}


async def test_a_manual_peer_keeps_its_own_address(isc):
    """A manual peer's entry moves to its real id once it answers, and the
    loop keeps dialing the address that was typed in."""
    peer = _UnreachablePeer(isc)
    isc._running = True
    key = "manual:192.0.2.30:8080"
    isc._peers[key] = PeerInfo(instance_id=key, name="192.0.2.30:8080",
                               host="192.0.2.30", port=8080, source="manual")
    isc._schedule_connect(key, "192.0.2.30", 8080)
    try:
        await asyncio.wait_for(peer.dialed.wait(), timeout=2.0)
        del isc._peers[key]
        isc._wake_reconnects()
        await peer.next_dial()

        assert peer.dials == [("192.0.2.30", 8080, "http")] * 2
    finally:
        await _stop(isc)


@pytest.fixture
def isc(state, events):
    """A manager with no peers of its own, running, and with nothing behind
    the UDP or WebSocket sides."""
    from openavc.core.isc import ISCManager

    class _Devices:
        async def send_command(self, *a, **k):
            return None

        def list_devices(self):
            return []

    state.set_event_bus(events)
    manager = ISCManager(
        state=state,
        events=events,
        devices=_Devices(),
        shared_state_patterns=["var.*"],
        auth_key="testkey",
        instance_id="aaaa-1111",
        instance_name="Test Room A",
        http_port=8080,
        manual_peers=[],
    )
    manager._running = True
    return manager
