"""How a connect ends at each of its stages, when it does not finish.

``BaseDriver.connect()`` builds the transport, checks it, runs the handshake,
declares the device connected, subscribes push, runs the initial sync and
starts polling. A failure at each stage takes down what the connect built,
and an attempt ended by a disconnect or a newer connect reports nothing of
its own (``ConnectEndedError``). These tests pin what each failure leaves
behind: what is raised and from what, what was closed, what state was
written and which events went out.

The device is invented and nothing dials: the driver hands connect a
stand-in transport.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.connection_fault import AUTH_FAILED, ConnectionFaultError
from openavc.core.device_logins import SAVED_LOGIN
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectEndedError

CONNECTED = "device.connected.acme1"
DISCONNECTED = "device.disconnected.acme1"


class _Link:
    """A transport that is up until closed."""

    def __init__(self, *, close_raises: bool = False, verify: bool | None = None) -> None:
        self.connected = True
        self.closed = 0
        self.close_raises = close_raises
        self.last_error = ""
        if verify is not None:
            self.verify_answer = verify

    async def close(self) -> None:
        self.closed += 1
        self.connected = False
        if self.close_raises:
            raise OSError("the link would not close")


class _VerifiedLink(_Link):
    async def verify(self, timeout: float) -> bool:
        self.last_error = "Connection refused"
        return self.verify_answer


class _AcmeLink(BaseDriver):
    DRIVER_INFO = {"id": "acme_link", "transport": "tcp"}

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__("acme1", {"host": "192.0.2.5", "port": 7, **(config or {})},
                         StateStore(), EventBus())
        self.link: _Link = _Link()
        self.sessions_closed = 0
        self.emitted: list[str] = []
        self.on_create = None
        self.on_handshake = None
        self.on_sync = None
        self.events.emit = self._record_emit  # type: ignore[method-assign]
        self.emit_raises: set[str] = set()

    async def _record_emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.emitted.append(event)
        if event in self.emit_raises:
            raise RuntimeError(f"a handler of {event} failed")

    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        return None

    async def _create_transport(self, transport_type: str) -> None:
        self.transport = self.link
        if self.on_create:
            self.on_create()

    async def _post_connect(self) -> None:
        if self.on_handshake:
            self.on_handshake()

    async def _initial_sync(self) -> None:
        if self.on_sync:
            self.on_sync()

    async def _close_session(self) -> None:
        self.sessions_closed += 1


class _ProbedAcmeLink(_AcmeLink):
    """Supplies a liveness probe, so a reconnect after silence is held."""

    async def _liveness_probe(self) -> None:
        return None


def _raise(exc: BaseException):
    def hook() -> None:
        raise exc
    return hook


def _end_then_raise(driver: _AcmeLink, exc: BaseException):
    """A disconnect lands while the stage runs, then the stage fails."""
    def hook() -> None:
        driver._end_connect_in_flight()
        raise exc
    return hook


# --- the start of a connect ---------------------------------------------------


async def test_a_leftover_transport_that_will_not_close_is_dropped_anyway():
    driver = _AcmeLink()
    leftover = _Link(close_raises=True)
    driver.transport = leftover
    await driver.connect()
    assert leftover.closed == 1
    assert driver.transport is driver.link
    assert driver.get_state("connected") is True


# --- building the transport ---------------------------------------------------


async def test_ended_while_the_transport_was_built_and_then_it_failed():
    driver = _AcmeLink()
    refused = OSError("refused")
    driver.on_create = _end_then_raise(driver, refused)
    with pytest.raises(ConnectEndedError) as caught:
        await driver.connect()
    assert caught.value.__cause__ is refused
    assert driver.link.closed == 1
    assert driver.transport is None
    assert driver.sessions_closed == 2  # the clean slate, then the ended connect's
    assert driver._connect_drop is None
    assert driver.emitted == []


async def test_a_failed_check_names_what_the_transport_said():
    driver = _AcmeLink()
    driver.link = _VerifiedLink(verify=False)
    with pytest.raises(ConnectionError, match="^Connection refused$"):
        await driver.connect()
    assert driver.link.closed == 1
    assert driver.transport is None
    assert driver.last_transport_error == "Connection refused"
    assert driver.emitted == []


async def test_a_failed_check_whose_transport_is_already_gone():
    driver = _AcmeLink()

    class _Vanishing(_VerifiedLink):
        async def verify(self, timeout: float) -> bool:
            driver.transport = None
            return False

    driver.link = _Vanishing(verify=False)
    with pytest.raises(ConnectionError, match="^Device at 192.0.2.5:7 is not responding$"):
        await driver.connect()
    assert driver.link.closed == 0
    assert driver.transport is None


async def test_a_check_switched_off_is_never_asked():
    driver = _AcmeLink({"verify_timeout": 0})
    driver.link = _VerifiedLink(verify=False)
    await driver.connect()
    assert driver.get_state("connected") is True


# --- the handshake ------------------------------------------------------------


async def test_a_failed_handshake_closes_the_transport_and_the_session():
    driver = _AcmeLink()
    refused = ConnectionError("login refused")
    driver.on_handshake = _raise(refused)
    with pytest.raises(ConnectionError) as caught:
        await driver.connect()
    assert caught.value is refused
    assert driver.link.closed == 1
    assert driver.transport is None
    assert driver.sessions_closed == 2
    assert driver._connected is False
    assert driver.get_state("connected") is False
    assert driver.emitted == []


async def test_a_failed_handshake_whose_transport_the_driver_already_dropped():
    driver = _AcmeLink()

    def drop_then_fail() -> None:
        driver.transport = None
        raise ValueError("no banner")

    driver.on_handshake = drop_then_fail
    with pytest.raises(ValueError, match="no banner"):
        await driver.connect()
    assert driver.link.closed == 0
    assert driver.sessions_closed == 2
    assert driver._connected is False


async def test_ended_by_a_connected_handler_that_then_failed():
    driver = _AcmeLink()
    driver.emit_raises = {CONNECTED}
    original = driver._record_emit

    async def end_then_fail(event: str, payload: dict[str, Any] | None = None) -> None:
        if event == CONNECTED:
            driver._end_connect_in_flight()
        await original(event, payload)

    driver.events.emit = end_then_fail  # type: ignore[method-assign]
    with pytest.raises(ConnectEndedError) as caught:
        await driver.connect()
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert driver.link.closed == 1
    assert driver.transport is None
    assert driver.sessions_closed == 2
    assert driver._connected is False
    assert driver.emitted == [CONNECTED]


async def test_a_held_connection_waits_to_be_announced():
    driver = _ProbedAcmeLink()
    driver._hold_next_connect = True
    driver.config["poll_interval"] = 0
    await driver.connect()
    try:
        assert driver._held is True
        assert driver._connected is True
        assert driver.get_state("connected") is False
        assert driver.emitted == []
        assert driver._answer_waiter is not None and not driver._answer_waiter.done()
        assert driver._hold_next_connect is False
    finally:
        await driver.disconnect()


# --- the initial sync ---------------------------------------------------------


async def test_a_failed_sync_takes_the_connection_back_down():
    driver = _AcmeLink()
    broken = ValueError("roster unreadable")
    driver.on_sync = _raise(broken)
    with pytest.raises(ValueError) as caught:
        await driver.connect()
    assert caught.value is broken
    assert driver.link.closed == 1
    assert driver.transport is None
    assert driver.sessions_closed == 2
    assert driver._connected is False
    assert driver.get_state("connected") is False
    assert driver.emitted == [CONNECTED, DISCONNECTED]
    assert driver.last_fault is None


async def test_a_sync_refused_for_its_login_keeps_the_fault():
    driver = _AcmeLink()
    driver.on_sync = _raise(ConnectionFaultError("Wrong password", code=AUTH_FAILED))
    with pytest.raises(ConnectionFaultError):
        await driver.connect()
    assert driver.last_fault is not None
    assert driver.last_fault.code == AUTH_FAILED
    assert driver.emitted == [CONNECTED, DISCONNECTED]


async def test_a_failed_sync_whose_transport_will_not_close():
    driver = _AcmeLink()
    driver.link = _Link(close_raises=True)
    broken = ValueError("roster unreadable")
    driver.on_sync = _raise(broken)
    with pytest.raises(ValueError) as caught:
        await driver.connect()
    assert caught.value is broken
    assert driver.transport is None
    assert driver.emitted == [CONNECTED, DISCONNECTED]


async def test_a_failed_sync_whose_transport_is_already_gone():
    driver = _AcmeLink()

    def drop_then_fail() -> None:
        driver.transport = None
        raise ValueError("roster unreadable")

    driver.on_sync = drop_then_fail
    with pytest.raises(ValueError):
        await driver.connect()
    assert driver.link.closed == 0
    assert driver.sessions_closed == 2
    assert driver.get_state("connected") is False


async def test_a_failed_sync_whose_disconnect_handler_fails():
    driver = _AcmeLink()
    driver.emit_raises = {DISCONNECTED}
    broken = ValueError("roster unreadable")
    driver.on_sync = _raise(broken)
    with pytest.raises(ValueError) as caught:
        await driver.connect()
    assert caught.value is broken
    assert driver.emitted == [CONNECTED, DISCONNECTED]


async def test_a_failed_sync_on_a_held_connection_announces_nothing():
    driver = _ProbedAcmeLink()
    driver._hold_next_connect = True
    broken = ValueError("roster unreadable")
    driver.on_sync = _raise(broken)
    waiters = []
    original = driver._settle_answer

    def settle(answered: bool) -> None:
        waiters.append((driver._answer_waiter, answered))
        original(answered)

    driver._settle_answer = settle  # type: ignore[method-assign]
    with pytest.raises(ValueError) as caught:
        await driver.connect()
    assert caught.value is broken
    assert driver._held is False
    assert driver.get_state("connected") is False
    assert driver.emitted == []
    waiter, answered = waiters[-1]
    assert answered is False
    assert waiter is not None and waiter.done() and waiter.result() is False
    assert driver.link.closed == 1


async def test_ended_during_the_sync_unwinds_and_reports_nothing():
    driver = _AcmeLink()
    broken = ValueError("roster unreadable")
    driver.on_sync = _end_then_raise(driver, broken)
    with pytest.raises(ConnectEndedError) as caught:
        await driver.connect()
    assert caught.value.__cause__ is broken
    assert driver.link.closed == 1
    assert driver.transport is None
    assert driver.sessions_closed == 2
    assert driver._connected is False
    assert driver.get_state("connected") is True  # the disconnect that ended it writes it
    assert driver.emitted == [CONNECTED]


async def test_ended_during_the_sync_by_its_own_ended_error():
    driver = _AcmeLink()
    ended = ConnectEndedError("ended")
    driver.on_sync = _end_then_raise(driver, ended)
    with pytest.raises(ConnectEndedError) as caught:
        await driver.connect()
    assert caught.value is ended
    assert driver.emitted == [CONNECTED]


# --- after the sync -----------------------------------------------------------


async def test_ended_once_the_sync_finished():
    driver = _AcmeLink()
    driver.on_sync = driver._end_connect_in_flight
    with pytest.raises(ConnectEndedError):
        await driver.connect()
    assert driver.link.closed == 1
    assert driver._connected is False
    assert driver.emitted == [CONNECTED]


async def test_a_finished_connect_polls_and_resets_the_silence_verdict():
    driver = _AcmeLink({"poll_interval": 30})
    driver._dropped_for_silence = True
    driver._silence_verdict = "no replies"
    await driver.connect()
    try:
        assert driver._dropped_for_silence is False
        assert driver._silence_verdict is None
        assert driver._poll_task is not None
        assert driver.emitted == [CONNECTED]
        assert driver._connect_attempt is None
    finally:
        await driver.disconnect()


# --- what every attempt starts from -------------------------------------------


async def test_an_attempt_starts_from_a_clean_slate():

    driver = _AcmeLink()
    driver._push_receives = 5
    stale = asyncio.get_running_loop().create_future()
    driver._answer_waiter = stale
    await driver.connect()
    assert driver._push_receives == 0
    assert stale.done() and stale.result() is False


async def test_the_check_is_skipped_while_the_device_holds_polling_back():
    class _Standby(_AcmeLink):
        def _connect_check_held(self) -> bool:
            return True

    driver = _Standby()
    driver.link = _VerifiedLink(verify=False)
    await driver.connect()
    assert driver.get_state("connected") is True
    assert driver.link.closed == 0


async def test_ended_as_polling_started_unwinds():
    class _EndedAtPoll(_AcmeLink):
        async def start_polling(self, interval: float) -> None:
            await super().start_polling(interval)
            self._end_connect_in_flight()

    driver = _EndedAtPoll({"poll_interval": 30})
    with pytest.raises(ConnectEndedError):
        await driver.connect()
    assert driver._poll_task is None
    assert driver.link.closed == 1
    assert driver._connected is False


async def test_cancelled_while_the_transport_was_built_leaves_it_to_the_canceller():
    driver = _AcmeLink()
    driver.on_create = _end_then_raise(driver, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await driver.connect()
    assert driver.link.closed == 0
    assert driver.transport is driver.link
    assert driver._connect_drop is None


# --- refused before anything is built ---------------------------------------


class _AddressedAcmeLink(_AcmeLink):
    DRIVER_INFO = {
        "id": "acme_link",
        "transport": "tcp",
        "config_schema": {"host": {"type": "string", "required": True}},
    }


@pytest.mark.parametrize(
    ("config", "code", "says"),
    [
        ({"bridge": "acme_bridge", "host": "", "password": SAVED_LOGIN}, "invalid_config",
         "bridge"),
        ({"host": "", "password": SAVED_LOGIN}, "invalid_config", "address"),
        ({"password": SAVED_LOGIN}, "login_missing", ""),
    ],
)
async def test_refusals_are_asked_in_order(config, code, says):
    driver = _AddressedAcmeLink(config)
    with pytest.raises(ConnectionFaultError) as caught:
        await driver.connect()
    assert caught.value.fault_code == code
    assert says.lower() in str(caught.value).lower()
    assert driver.link.closed == 0
    assert driver.transport is None
    assert driver.emitted == []


# --- a device that sends through a bridge ------------------------------------


@pytest.mark.parametrize(
    ("bridge_port", "bridge_up", "online"),
    [("ir1", True, True), ("ir1", False, False), ("", True, False)],
)
async def test_a_bridge_routed_device_follows_its_bridge(bridge_port, bridge_up, online):
    driver = _AcmeLink({"transport": "bridge", "bridge": "acme_bridge",
                        "bridge_port": bridge_port})
    driver.state.set("device.acme_bridge.connected", bridge_up)
    await driver.connect()
    assert driver._bridge_routed is True
    assert driver._connected is online
    assert driver.get_state("connected") is online
    assert driver.emitted == ([CONNECTED] if online else [])
    assert driver.transport is None
    assert driver.sessions_closed == 1
