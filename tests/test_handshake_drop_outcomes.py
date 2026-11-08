"""What a handshake ends on when its connection drops under it.

``connect()`` races the driver's handshake (``_post_connect``) against the
connection closing, and ends the handshake the moment it does. These pin
each outcome of that race on an invented device, driving the race directly
with a link that says why it closed:

  - with no connect running, the handshake simply runs;
  - a connection already gone never starts the handshake;
  - another door ending the connect ends the handshake as ended, not dropped;
  - a typed failure the handshake ended on (a refused login) beats the drop,
    an untyped one gives way to it, and a handshake that finished as the
    link closed still fails;
  - the race clears only its own drop future.
"""

from __future__ import annotations

import asyncio

import pytest

from openavc.core.connection_fault import (
    AUTH_FAILED,
    TRANSPORT_DISCONNECTED,
    ConnectionFaultError,
    default_fault_message,
)
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectEndedError

CLOSED = "acme: connection closed by 192.0.2.10"


class _Link:
    """An invented transport that left its last words behind."""

    def __init__(self) -> None:
        self.connected = True
        self.last_error = f"acme: banner\n{CLOSED}\n"

    async def close(self) -> None:
        self.connected = False


class AcmeHandshakeDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_handshake",
        "name": "Acme Handshake",
        "transport": "tcp",
        "state_variables": {},
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.handshake = None
        self.ran = 0

    async def _post_connect(self) -> None:
        self.ran += 1
        if self.handshake is not None:
            await self.handshake(self)

    async def send_command(self, command, params=None):
        return None


def _driver() -> AcmeHandshakeDriver:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = AcmeHandshakeDriver(
        "acme1", {"host": "192.0.2.10", "port": 23}, state, events,
    )
    drv.transport = _Link()
    return drv


def _arm(drv: AcmeHandshakeDriver) -> asyncio.Future:
    """The drop future connect() sets around its handshake."""
    drv._connect_drop = asyncio.get_running_loop().create_future()
    return drv._connect_drop


async def test_with_no_connect_running_the_handshake_simply_runs():
    drv = _driver()
    await drv._run_handshake()
    assert drv.ran == 1
    assert drv._connect_drop is None


async def test_a_connection_already_gone_never_starts_the_handshake():
    drv = _driver()
    _arm(drv).set_result(None)
    with pytest.raises(ConnectionError) as failed:
        await drv._run_handshake()
    await asyncio.sleep(0)
    assert drv.ran == 0
    assert type(failed.value) is ConnectionError
    assert str(failed.value) == CLOSED
    assert drv._connect_drop is None


async def test_another_door_ending_the_connect_ends_the_handshake_as_ended():
    drv = _driver()
    _arm(drv)
    started = asyncio.Event()
    cancelled: list[bool] = []

    async def waits_for_a_prompt(d):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    drv.handshake = waits_for_a_prompt
    race = asyncio.ensure_future(drv._run_handshake())
    await started.wait()
    drv._end_connect_in_flight()
    with pytest.raises(ConnectEndedError) as failed:
        await race
    assert type(failed.value) is ConnectEndedError
    assert str(failed.value) == default_fault_message(TRANSPORT_DISCONNECTED)
    assert failed.value.__cause__ is None
    assert cancelled == [True], "the handshake is cancelled, and waited for"
    assert drv._connect_drop is None


async def test_a_typed_failure_the_handshake_ended_on_beats_the_drop():
    drv = _driver()
    _arm(drv)
    refusal = ConnectionFaultError("Login rejected", code=AUTH_FAILED)

    async def refused_as_the_link_closes(d):
        d._handle_transport_disconnect()
        raise refusal

    drv.handshake = refused_as_the_link_closes
    with pytest.raises(ConnectionFaultError) as failed:
        await drv._run_handshake()
    assert failed.value is refusal
    assert drv._connect_drop is None


async def test_an_untyped_failure_the_handshake_ended_on_gives_way_to_the_drop():
    drv = _driver()
    _arm(drv)

    async def garbled_as_the_link_closes(d):
        d._handle_transport_disconnect()
        raise RuntimeError("banner garbled")

    drv.handshake = garbled_as_the_link_closes
    with pytest.raises(ConnectionError) as failed:
        await drv._run_handshake()
    assert type(failed.value) is ConnectionError
    assert str(failed.value) == CLOSED


async def test_a_handshake_that_finished_as_the_link_closed_still_fails():
    drv = _driver()
    _arm(drv)

    async def finished_as_the_link_closes(d):
        d._handle_transport_disconnect()

    drv.handshake = finished_as_the_link_closes
    with pytest.raises(ConnectionError) as failed:
        await drv._run_handshake()
    assert type(failed.value) is ConnectionError
    assert str(failed.value) == CLOSED
    assert drv.connected is False


async def test_the_race_clears_only_its_own_drop_future():
    drv = _driver()
    _arm(drv)
    newer = asyncio.get_running_loop().create_future()

    async def replaced(d):
        d._connect_drop = newer

    drv.handshake = replaced
    await drv._run_handshake()
    assert drv._connect_drop is newer
