"""The poll loop's first cycle when the connect has already polled.

A driver's ``_initial_sync`` may read state with ``await self.poll()`` so the
device card fills at connect. The loop used to poll again the moment it
started, so every connect sent every read twice back to back. Now a poll that
returned during this connect counts as the loop's first cycle, and the next
one waits out the rest of the interval. A driver whose start-up reads nothing
is unchanged: its loop polls at once.

Driven through the real ``connect()`` with an invented device and a link that
opens nothing. ``asyncio.sleep`` inside the driver module is recorded, and any
pause of a second or more parks, so each test reads where the loop stopped.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import base as base_mod
from openavc.drivers.base import BaseDriver

INTERVAL = 10.0
_real_sleep = asyncio.sleep


class _RecordingAsyncio:
    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self._log = log
        self._never = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    async def sleep(self, seconds: float, result: Any = None) -> Any:
        self._log.append(("pause", seconds))
        if seconds >= 1:
            await self._never.wait()
        await _real_sleep(0)
        return result


class _Link:
    connected = True

    async def close(self) -> None:
        self.connected = False


class _AcmeWidget(BaseDriver):
    """Reads one value per poll; ``sync_polls`` makes start-up read it too."""

    DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}

    def __init__(self, *args: Any, log: list[tuple[Any, ...]], sync_polls: bool) -> None:
        super().__init__(*args)
        self.log = log
        self.sync_polls = sync_polls
        self.fail_polls = False

    async def _create_transport(self, transport_type: str) -> None:
        self.transport = _Link()

    async def send_command(self, command: str, params: dict | None = None) -> None:
        return None

    async def poll(self) -> None:
        """Read the widget's one value."""
        self.log.append(("poll",))
        if self.fail_polls:
            raise ConnectionError("no reply")

    async def _initial_sync(self) -> None:
        if self.sync_polls:
            try:
                await self.poll()
            except ConnectionError:
                pass


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    entries: list[tuple[Any, ...]] = []
    monkeypatch.setattr(base_mod, "asyncio", _RecordingAsyncio(entries))
    return entries


async def _connect(driver: _AcmeWidget) -> None:
    await driver.connect()
    for _ in range(5):  # let the loop run to its first long pause
        await _real_sleep(0)


def _widget(log: list[tuple[Any, ...]], *, sync_polls: bool) -> _AcmeWidget:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return _AcmeWidget(
        "acme1", {"host": "127.0.0.1", "port": 5000, "poll_interval": INTERVAL},
        state, events, log=log, sync_polls=sync_polls,
    )


async def test_a_start_up_poll_is_the_first_cycle(log):
    driver = _widget(log, sync_polls=True)
    await _connect(driver)
    try:
        assert [entry[0] for entry in log] == ["poll", "pause"]
        assert INTERVAL - 1 < log[1][1] <= INTERVAL
    finally:
        await driver.stop_polling()


async def test_without_a_start_up_poll_the_loop_polls_at_once(log):
    driver = _widget(log, sync_polls=False)
    await _connect(driver)
    try:
        assert log == [("poll",), ("pause", INTERVAL)]
    finally:
        await driver.stop_polling()


async def test_a_poll_from_before_this_connect_does_not_count(log):
    driver = _widget(log, sync_polls=False)
    driver._last_poll_started_at = time.monotonic() - 5
    await _connect(driver)
    try:
        assert log == [("poll",), ("pause", INTERVAL)]
    finally:
        await driver.stop_polling()


async def test_a_start_up_poll_that_failed_does_not_count(log):
    driver = _widget(log, sync_polls=True)
    driver.fail_polls = True
    await _connect(driver)
    try:
        # The start-up read failed, so the loop asks again at once.
        assert [entry[0] for entry in log[:3]] == ["poll", "poll", "pause"]
    finally:
        await driver.stop_polling()


def test_the_noted_poll_is_still_the_drivers_own_method():
    assert _AcmeWidget.poll.__name__ == "poll"
    assert _AcmeWidget.poll.__doc__ == "Read the widget's one value."
    assert inspect.iscoroutinefunction(_AcmeWidget.poll)


async def test_a_subclass_that_calls_super_poll_runs_both_and_is_noted(log):
    class _AcmeWidgetPro(_AcmeWidget):
        async def poll(self) -> None:
            await super().poll()
            self.log.append(("pro",))

    driver = _AcmeWidgetPro(
        "acme2", {}, StateStore(), EventBus(), log=log, sync_polls=False,
    )
    await driver.poll()
    assert log == [("poll",), ("pro",)]
    assert driver._last_poll_started_at is not None
