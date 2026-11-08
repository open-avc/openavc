"""What the platform does after a connection drops by itself.

A transport that closes under a connected device calls the driver's
``_handle_transport_disconnect``, which marks the device offline at once and
schedules the cleanup: stop watching the connection, keep the transport's
last words for the offline reason, close the transport and any session the
driver owns, then say so. These pin each part on an invented device:

  - the transport is closed and forgotten, its last error and typed fault
    kept, the session closed, and ``device.disconnected`` emitted once;
  - a transport or a session whose close fails, or an event bus that
    refuses the event, does not stop the rest;
  - a held connection (never announced) emits nothing: the reconnect loop
    waiting on it is told instead;
  - a connect another door ended closes its own session quietly.
"""

from __future__ import annotations

import asyncio

from openavc.core.connection_fault import NO_RESPONSE, ConnectionFault
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _Link:
    """An invented transport that left its last words behind."""

    def __init__(self, *, close_fails: bool = False) -> None:
        self.connected = True
        self.last_error = "acme: read timed out"
        self.last_fault = ConnectionFault(NO_RESPONSE, "Acme stopped answering")
        self.closed = 0
        self._close_fails = close_fails

    async def close(self) -> None:
        self.closed += 1
        self.connected = False
        if self._close_fails:
            raise OSError("socket already gone")


class AcmeSessionDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_drop_cleanup",
        "name": "Acme Drop Cleanup",
        "transport": "tcp",
        "state_variables": {},
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sessions_closed = 0
        self.session_close_fails = False

    async def _close_session(self) -> None:
        self.sessions_closed += 1
        if self.session_close_fails:
            raise RuntimeError("session already closed")

    async def send_command(self, command, params=None):
        return None


def _driver(link: _Link) -> tuple[AcmeSessionDriver, list[str]]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = AcmeSessionDriver("acme1", {"host": "192.0.2.20", "port": 23}, state, events)
    drv.transport = link
    drv._connected = True
    heard: list[str] = []

    async def on_disconnected(event, payload=None):
        heard.append(event)

    events.on("device.disconnected.acme1", on_disconnected)
    return drv, heard


async def _drop(drv: AcmeSessionDriver) -> None:
    drv._handle_transport_disconnect()
    await asyncio.gather(*drv._bg_tasks)


async def test_a_drop_closes_and_forgets_the_transport_and_keeps_its_last_words():
    link = _Link()
    drv, heard = _driver(link)
    await _drop(drv)
    assert drv.transport is None
    assert link.closed == 1
    assert drv.last_transport_error == "acme: read timed out"
    assert drv.last_fault == ConnectionFault(NO_RESPONSE, "Acme stopped answering")
    assert drv.sessions_closed == 1
    assert heard == ["device.disconnected.acme1"]
    assert drv.connected is False
    assert drv.get_state("connected") is False


async def test_a_transport_that_will_not_close_does_not_stop_the_rest():
    link = _Link(close_fails=True)
    drv, heard = _driver(link)
    await _drop(drv)
    assert drv.transport is None
    assert drv.sessions_closed == 1
    assert heard == ["device.disconnected.acme1"]


async def test_a_session_that_will_not_close_does_not_stop_the_event():
    drv, heard = _driver(_Link())
    drv.session_close_fails = True
    await _drop(drv)
    assert drv.sessions_closed == 1
    assert heard == ["device.disconnected.acme1"]


async def test_an_event_bus_that_refuses_the_event_does_not_fail_the_cleanup():
    drv, _ = _driver(_Link())

    async def refuses(event, payload=None):
        raise RuntimeError("bus closed")

    drv.events.emit = refuses
    await _drop(drv)
    assert drv.transport is None
    assert drv.sessions_closed == 1


async def test_a_held_connection_that_drops_tells_the_reconnect_and_emits_nothing():
    drv, heard = _driver(_Link())
    drv._held = True
    waiter = asyncio.get_running_loop().create_future()
    drv._answer_waiter = waiter
    await _drop(drv)
    assert heard == []
    assert drv._held is False
    assert waiter.done() and waiter.result() is False
    assert drv.transport is None
    assert drv.sessions_closed == 1


async def test_an_ended_connect_closes_its_session_quietly():
    drv, _ = _driver(_Link())
    drv.session_close_fails = True
    await drv._close_session_quietly()
    assert drv.sessions_closed == 1
