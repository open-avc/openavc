"""A configuration change ends the auth-reject backoff, as its log line says.

"Peer <id> auth rejected (server_auth_failed); backing off to 300s until
configuration changes" was a promise nothing kept. The wait is a sleep inside
the peer's own outbound loop: the force-disconnect on a rotated key iterates
the live connections and a backed-off peer holds none, and _schedule_connect
short-circuits on its still-live task, so reload() could not reach it. The
only way out was to drop the peer from the manual list and add it back — five
silent minutes at the one moment somebody is certainly standing there, having
just fixed the key on the other box.

Platform test: no real peer, the outbound connect is stubbed.
"""

import asyncio

import pytest

from openavc.core.isc import _ISCAuthRejected


class _RejectingPeer:
    """Stands in for a peer that refuses our key, and says when it was asked."""

    def __init__(self, isc):
        self.attempts: list[str] = []
        self.asked = asyncio.Event()
        isc._outbound_connect = self._connect

    async def _connect(self, peer_id, host, port, scheme):
        self.attempts.append(host)
        self.asked.set()
        raise _ISCAuthRejected("server_auth_failed")

    async def wait_for_next_attempt(self, timeout: float = 2.0) -> None:
        """Return once the loop has asked again AND parked on its backoff.

        The loop runs straight from the rejection to its wait with nothing to
        yield on, so by the time this coroutine is resumed the wait is in
        place — which is what makes waking it a fair test rather than a race.
        """
        self.asked.clear()
        await asyncio.wait_for(self.asked.wait(), timeout=timeout)


async def _run_until_first_rejection(isc, peer):
    isc._running = True
    task = asyncio.create_task(
        isc._outbound_loop("manual:192.0.2.9:8080", "192.0.2.9", 8080)
    )
    await asyncio.wait_for(peer.asked.wait(), timeout=2.0)
    assert peer.attempts == ["192.0.2.9"]
    return task


async def _shut_down(isc, task):
    isc._running = False
    isc._wake_reconnects()
    await asyncio.wait_for(task, timeout=2.0)


async def test_a_reload_retries_a_backed_off_peer_immediately(isc):
    """The behaviour the log line promises: a config change, and it tries
    again now rather than in five minutes."""
    peer = _RejectingPeer(isc)
    task = await _run_until_first_rejection(isc, peer)
    try:
        peer.asked.clear()
        await isc.reload(
            shared_state_patterns=["var.*"],
            auth_key="the-key-the-user-just-fixed",
            manual_peers=[],
        )
        await asyncio.wait_for(peer.asked.wait(), timeout=2.0)
        assert peer.attempts == ["192.0.2.9", "192.0.2.9"]
    finally:
        await _shut_down(isc, task)


async def test_a_backed_off_peer_is_registered_to_be_woken(isc):
    peer = _RejectingPeer(isc)
    task = await _run_until_first_rejection(isc, peer)
    try:
        assert "manual:192.0.2.9:8080" in isc._reconnect_wakeups
    finally:
        await _shut_down(isc, task)
    assert isc._reconnect_wakeups == {}


async def test_a_wake_restarts_the_backoff_schedule(isc):
    """A woken peer reports its next failure the way a first failure is
    reported — the person who just changed the key has to see whether it
    took, and only the first rejection is logged at WARNING."""
    peer = _RejectingPeer(isc)
    task = await _run_until_first_rejection(isc, peer)
    try:
        records = []
        for _ in range(3):
            isc._wake_reconnects()
            await peer.wait_for_next_attempt()
            records.append(len(peer.attempts))
        assert records == [2, 3, 4]
    finally:
        await _shut_down(isc, task)


async def test_reload_wakes_every_outbound_peer(isc):
    """Not only the backed-off ones: the schedule a config change interrupts
    is a delay, not a decision."""
    events = {"p1": asyncio.Event(), "p2": asyncio.Event()}
    isc._reconnect_wakeups = events

    await isc.reload(
        shared_state_patterns=["var.*"],
        auth_key=isc._auth_key,
        manual_peers=[],
    )

    assert all(e.is_set() for e in events.values())


async def test_a_peer_nobody_wakes_stays_put(isc):
    """The wait is still a wait. Nothing woke this one, so no amount of
    event-loop turns produces a retry — the backoff is not a spin with an
    escape hatch bolted on."""
    peer = _RejectingPeer(isc)
    task = await _run_until_first_rejection(isc, peer)
    try:
        for _ in range(20):
            await asyncio.sleep(0)
        assert peer.attempts == ["192.0.2.9"]
    finally:
        await _shut_down(isc, task)


@pytest.fixture
def isc(state, events):
    """A manager with no peers of its own, so only the loop under test runs."""
    from openavc.core.isc import ISCManager

    class _Devices:
        async def send_command(self, *a, **k):
            return None

        def list_devices(self):
            return []

    state.set_event_bus(events)
    return ISCManager(
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
