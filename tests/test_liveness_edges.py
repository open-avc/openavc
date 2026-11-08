"""The edges of the checks that decide whether a connected device still answers.

test_liveness.py, test_unread_replies.py and test_base_driver_watchdog.py
drive these checks end to end over real sockets. These call each judgement
directly with an invented device and a link that only keeps counts, so the
paths those runs never reach are pinned too: a count judged on another
connection, a held connection that hears nothing mid-interval, the
unread-replies hold ending while ``last_error`` holds somebody else's
sentence, a reconnect loop waiting on a device nothing asks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest

from openavc.core.connection_fault import NO_RESPONSE
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.liveness import _ProbeWatch
from openavc.drivers.polling import _PollWatch

_LOGGER = "openavc.drivers.base"
_READ_SENTENCE = (
    "Something is answering on this connection, but this driver reads none "
    "of it: 3 polls in a row. Check the port, and that this driver matches "
    "the device."
)
_UNENDED_SENTENCE = (
    "Something is answering on this connection, but none of its replies ends "
    "the way this driver expects: 3 polls in a row. Check the port, and that "
    "this driver matches the device."
)


class _Link:
    """A transport that only keeps counts."""

    def __init__(self, sent: int = 0, received: int = 0) -> None:
        self.send_count = sent
        self.receive_count = received
        self.connected = True

    async def close(self) -> None:
        self.connected = False


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "state_variables": {"last_error": {"type": "string", "label": "Last Error"}},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.parsed: int | None = 0
        self.framed: int | None = 0
        self.sample = "PWR=?"
        self.samples_taken = 0
        self.expect_silence = False

    async def send_command(self, command: str, params: dict | None = None) -> None:
        return None

    async def on_data_received(self, data: bytes) -> None:
        pass

    def _parsed_count(self) -> int | None:
        return self.parsed

    def _framed_count(self) -> int | None:
        return self.framed

    def _unread_sample(self) -> str:
        self.samples_taken += 1
        return self.sample

    def _silence_expected(self) -> bool:
        return self.expect_silence


class _AcmeQuiet(_AcmeWidget):
    """Declares no last_error."""

    DRIVER_INFO: dict[str, Any] = {"id": "acme_quiet", "state_variables": {}}


def _widget(cls: type[_AcmeWidget] = _AcmeWidget) -> _AcmeWidget:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls("acme1", {"host": "127.0.0.1", "port": 5000}, state, events)
    drv.transport = _Link()
    drv._connected = True
    return drv


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _LOGGER and r.levelno == level
    ]


# --- The unread-replies check (_judge_reading) ---


def test_a_cycle_judged_on_another_connection_leaves_the_count() -> None:
    drv = _widget()
    drv.transport.receive_count = 5
    assert drv._judge_reading((_Link(), 0, 0, 0), 2, 3) == 2
    assert drv._unread_replies_hold is False


def test_a_driver_that_reports_no_parsed_count_is_not_judged() -> None:
    drv = _widget()
    drv.parsed = None
    drv.transport.receive_count = 5
    assert drv._judge_reading((drv.transport, 0, 0, 0), 2, 3) == 2
    assert drv._unread_replies_hold is False


def test_a_transport_that_keeps_no_counts_is_not_judged() -> None:
    drv = _widget()
    link = drv.transport
    drv.transport = object()
    assert drv._judge_reading((link, 0, 0, 0), 2, 3) == 2


def test_a_cycle_that_heard_nothing_leaves_the_count() -> None:
    drv = _widget()
    drv.transport.receive_count = 4
    assert drv._judge_reading((drv.transport, 4, 0, 0), 2, 3) == 2
    assert drv._unread_replies_hold is False


def test_a_cycle_that_read_a_reply_starts_the_count_again_quietly(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget()
    drv.transport.receive_count = 1
    drv.parsed = 1
    assert drv._judge_reading((drv.transport, 0, 0, 0), 2, 3) == 0
    assert _messages(caplog, logging.INFO) == []


def test_the_first_unread_cycle_marks_where_the_run_began() -> None:
    drv = _widget()
    drv.transport.receive_count = 1
    assert drv._judge_reading((drv.transport, 0, 0, 7), 0, 3) == 1
    assert drv._unread_run_framed_from == 7
    drv.transport.receive_count = 2
    assert drv._judge_reading((drv.transport, 1, 0, 9), 1, 3) == 2
    assert drv._unread_run_framed_from == 7


def test_at_the_limit_the_check_holds_and_says_so_once(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget()
    drv._unread_run_framed_from = 0
    drv.framed = 2
    drv.transport.receive_count = 3
    assert drv._judge_reading((drv.transport, 2, 0, 1), 2, 3) == 3
    assert drv._unread_replies_hold is True
    assert drv._unread_sentence == _READ_SENTENCE
    assert drv.get_state("last_error") == _READ_SENTENCE
    assert _messages(caplog, logging.WARNING) == [
        "[acme1] Replies arrive but no response rule reads any of them "
        "(3 polls in a row); the last one was 'PWR=?'. Check the port, and "
        "that this driver matches the device."
    ]
    # The next unread cycle writes the sentence again and warns no more.
    drv.set_state("last_error", "")
    drv.transport.receive_count = 4
    assert drv._judge_reading((drv.transport, 3, 0, 2), 3, 3) == 4
    assert drv.get_state("last_error") == _READ_SENTENCE
    assert len(_messages(caplog, logging.WARNING)) == 1
    assert drv.samples_taken == 1


def test_the_held_sentence_names_the_limit_not_the_run() -> None:
    drv = _widget()
    drv.transport.receive_count = 9
    assert drv._judge_reading((drv.transport, 8, 0, None), 7, 3) == 8
    assert "3 polls in a row" in drv._unread_sentence


def test_a_run_whose_replies_never_end_as_framed_says_that(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget()
    drv._unread_run_framed_from = 4
    drv.framed = 4
    drv.transport.receive_count = 3
    assert drv._judge_reading((drv.transport, 2, 0, 4), 2, 3) == 3
    assert drv._unread_sentence == _UNENDED_SENTENCE
    assert drv.get_state("last_error") == _UNENDED_SENTENCE
    assert _messages(caplog, logging.WARNING) == [
        "[acme1] Replies arrive but none of them ends the way this driver "
        "expects, so no response rule can read them (3 polls in a row). "
        "Check the port, and that this driver matches the device."
    ]
    assert drv.samples_taken == 0


@pytest.mark.parametrize(("framed_from", "framed_now"), [(None, 4), (4, None), (4, 5)])
def test_a_framed_count_that_moved_or_is_unknown_reads_as_framed(
    framed_from: int | None, framed_now: int | None,
) -> None:
    drv = _widget()
    drv._unread_run_framed_from = framed_from
    drv.framed = framed_now
    drv.transport.receive_count = 3
    drv._judge_reading((drv.transport, 2, 0, framed_from), 2, 3)
    assert drv._unread_sentence == _READ_SENTENCE


def test_a_driver_that_declares_no_last_error_gets_none_written(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget(_AcmeQuiet)
    drv.transport.receive_count = 3
    assert drv._judge_reading((drv.transport, 2, 0, None), 2, 3) == 3
    assert drv._unread_replies_hold is True
    assert drv.get_state("last_error") is None
    assert len(_messages(caplog, logging.WARNING)) == 1


def test_reading_again_ends_the_hold_and_clears_its_own_sentence(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget()
    drv._unread_replies_hold = True
    drv._unread_sentence = _READ_SENTENCE
    drv.set_state("last_error", _READ_SENTENCE)
    drv.transport.receive_count = 1
    drv.parsed = 1
    assert drv._judge_reading((drv.transport, 0, 0, 0), 5, 3) == 0
    assert drv._unread_replies_hold is False
    assert drv.get_state("last_error") == ""
    assert _messages(caplog, logging.INFO) == ["[acme1] The device's replies are read again"]


def test_reading_again_leaves_a_last_error_the_driver_wrote_since() -> None:
    drv = _widget()
    drv._unread_replies_hold = True
    drv._unread_sentence = _READ_SENTENCE
    drv.set_state("last_error", "Lamp fault")
    drv.transport.receive_count = 1
    drv.parsed = 1
    assert drv._judge_reading((drv.transport, 0, 0, 0), 5, 3) == 0
    assert drv._unread_replies_hold is False
    assert drv.get_state("last_error") == "Lamp fault"


# --- The liveness probe's fault sentence ---


@pytest.mark.parametrize(
    ("hold", "sentence", "received", "unread"),
    [
        (True, _READ_SENTENCE, 1, True),
        (True, _READ_SENTENCE, 0, False),
        (False, _READ_SENTENCE, 1, False),
        (True, "", 1, False),
    ],
)
def test_a_probe_drop_names_unread_replies_only_on_a_connection_that_received(
    hold: bool, sentence: str, received: int, unread: bool,
) -> None:
    drv = _widget()
    drv._unread_replies_hold = hold
    drv._unread_sentence = sentence
    drv.transport.receive_count = received
    expected = _READ_SENTENCE if unread else BaseDriver.HEALTH_FAULT_MESSAGE
    assert drv._probe_fault_message() == expected


def test_a_probe_drop_on_a_link_with_no_counts_says_it_stopped_answering() -> None:
    drv = _widget()
    drv._unread_replies_hold = True
    drv._unread_sentence = _READ_SENTENCE
    drv.transport = object()
    assert drv._probe_fault_message() == BaseDriver.HEALTH_FAULT_MESSAGE


def test_a_missed_probe_on_a_held_connection_keeps_the_interval_from_its_start() -> None:
    drv = _widget()
    probe = _ProbeWatch(interval=10.0, timeout=5.0, max_failures=3, wait=10.0)
    probe.held = True
    probe.started = time.monotonic() - 4.0
    drv._held = True
    assert drv._probe_missed(probe, TimeoutError()) is False
    assert 5.5 < probe.wait <= 6.0
    probe.started = time.monotonic() - 30.0
    drv._probe_missed(probe, TimeoutError())
    assert probe.wait == 0.0


@pytest.mark.parametrize(("held", "level"), [(True, logging.DEBUG), (False, logging.WARNING)])
def test_a_held_connections_missed_probes_log_at_debug(caplog, held: bool, level: int) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    drv = _widget()
    drv._held = held
    probe = _ProbeWatch(interval=10.0, timeout=5.0, max_failures=3)
    probe.deadline = 5.0
    drv._probe_missed(probe, TimeoutError())
    misses = [
        r.levelno for r in caplog.records
        if r.name == _LOGGER and "Liveness probe failed" in r.getMessage()
    ]
    assert misses == [level]
    assert _messages(caplog, level)[-1] == (
        "[acme1] Liveness probe failed (1/3): no answer within 5 s"
    )


async def test_unanswered_probes_drop_the_connection_and_keep_the_verdict() -> None:
    drv = _widget()
    drv._health_task = asyncio.ensure_future(asyncio.sleep(10))
    task = drv._health_task
    try:
        drv._drop_for_unanswered_probes()
        assert drv._dropped_for_silence is True
        assert drv._silence_verdict is not None
        assert drv._silence_verdict.code == NO_RESPONSE
        assert drv._silence_verdict.message == BaseDriver.HEALTH_FAULT_MESSAGE
        assert drv._silence_verdict is drv._last_fault
        assert drv._health_task is None
        assert drv._connected is False
    finally:
        task.cancel()
        await asyncio.sleep(0)


def test_the_probe_pause_is_logged_once_each_way(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget()
    probe = _ProbeWatch(interval=10.0, timeout=5.0, max_failures=3)
    for held in (False, True, True, False, False):
        drv._note_probe_pause(probe, held)
    assert _messages(caplog, logging.INFO) == [
        "[acme1] Liveness probe paused: the device's state holds it back",
        "[acme1] Liveness probe resumed",
    ]
    assert probe.paused is False


@pytest.mark.parametrize(
    ("configured", "limit"), [(None, 3), (5, 5), ("2", 2), (0, 1), (-4, 1), ("x", 3)],
)
def test_max_missed_polls_is_at_least_one(configured: Any, limit: int) -> None:
    drv = _widget()
    if configured is not None:
        drv.config["max_missed_polls"] = configured
    assert drv._max_missed_polls() == limit


def test_a_missed_probe_on_an_announced_connection_leaves_the_wait() -> None:
    drv = _widget()
    probe = _ProbeWatch(interval=10.0, timeout=5.0, max_failures=3, wait=10.0)
    probe.started = time.monotonic() - 4.0
    assert drv._probe_missed(probe, TimeoutError()) is False
    assert probe.wait == 10.0
    assert drv._health_failures == 1


# --- The silence check's count (_count_silence) ---


def _watch() -> _PollWatch:
    return _PollWatch(max_missed=3, watching=True, reading=False)


async def test_a_poll_judged_on_another_connection_counts_for_nothing() -> None:
    drv = _widget()
    watch = _watch()
    watch.silent_polls = 1
    await drv._count_silence(watch, (_Link(), True, 0))
    assert (watch.silent_polls, watch.heard_cycles) == (1, 0)


async def test_an_answer_resets_the_count_and_the_second_announces_a_held_connection() -> None:
    drv = _widget()
    drv._held = True
    watch = _watch()
    watch.silent_polls = 2
    drv.transport.receive_count = 1
    await drv._count_silence(watch, (drv.transport, True, 0))
    assert (watch.silent_polls, watch.heard_cycles) == (0, 1)
    assert drv._held is True
    drv.transport.receive_count = 2
    await drv._count_silence(watch, (drv.transport, True, 1))
    assert watch.heard_cycles == 2
    assert drv._held is False
    assert drv.get_state("connected") is True


@pytest.mark.parametrize(
    ("sent", "expected", "silent"), [(True, False, 1), (True, True, 0), (False, False, 0)],
)
async def test_only_a_poll_that_sent_and_expected_an_answer_counts_as_silent(
    sent: bool, expected: bool, silent: int,
) -> None:
    drv = _widget()
    drv.expect_silence = expected
    watch = _watch()
    await drv._count_silence(watch, (drv.transport, sent, 0))
    assert (watch.silent_polls, watch.heard_cycles) == (silent, 0)


# --- What a clean poll leaves for the next cycle (_note_poll_traffic) ---


class _AcmeStandby(_AcmeWidget):
    """Its state holds polling back: a poll sends nothing while it holds."""

    def _poll_waits_on_device(self) -> bool:
        return True


async def test_a_poll_whose_connection_was_replaced_leaves_nothing_to_judge() -> None:
    drv = _widget(_AcmeStandby)
    drv._held = True
    before = drv._io_counts()
    drv.transport = _Link(sent=0, received=5)
    watch = _PollWatch(max_missed=3, watching=True, reading=True)
    await drv._note_poll_traffic(watch, before, (0, 0))
    assert (watch.pending, watch.parse_pending) == (None, None)
    assert drv._held is True


async def test_a_clean_poll_keeps_its_traffic_for_both_checks() -> None:
    drv = _widget()
    drv.transport.receive_count = 2
    before = drv._io_counts()
    drv.transport.send_count = 1
    watch = _PollWatch(max_missed=3, watching=True, reading=True)
    await drv._note_poll_traffic(watch, before, (4, 6))
    assert watch.pending == (drv.transport, True, 2)
    assert watch.parse_pending == (drv.transport, 2, 4, 6)


async def test_a_held_poll_that_sent_nothing_announces_a_device_that_answered_before() -> None:
    drv = _widget(_AcmeStandby)
    drv._held = True
    drv.transport.receive_count = 1
    before = drv._io_counts()
    await drv._note_poll_traffic(_watch(), before, (None, None))
    assert drv._held is False
    assert drv.get_state("connected") is True


@pytest.mark.parametrize("case", ["sent", "never answered", "driver says no", "not connected"])
async def test_a_held_poll_announces_only_a_standby_device_that_answered(case: str) -> None:
    drv = _widget(_AcmeWidget if case == "driver says no" else _AcmeStandby)
    drv._held = True
    drv.transport.receive_count = 0 if case == "never answered" else 1
    before = drv._io_counts()
    if case == "sent":
        drv.transport.send_count = 1
    if case == "not connected":
        drv._connected = False
    await drv._note_poll_traffic(_watch(), before, (None, None))
    assert drv._held is True


# --- Waiting out the interval on a held connection ---


async def test_a_held_connection_that_answers_mid_interval_is_announced() -> None:
    drv = _widget()
    drv._held = True
    drv.transport.receive_count = 3
    await drv._wait_out_interval(0.05, (drv.transport, True, 2), True)
    assert drv._held is False
    assert drv.get_state("connected") is True


@pytest.mark.parametrize("case", ["first cycle", "no poll", "silent", "other link"])
async def test_a_held_connection_stays_held_unless_this_link_answered(case: str) -> None:
    drv = _widget()
    drv._held = True
    drv.transport.receive_count = 3
    pending: Any = (drv.transport, True, 2)
    heard_earlier = True
    if case == "first cycle":
        heard_earlier = False
    elif case == "no poll":
        pending = None
    elif case == "silent":
        pending = (drv.transport, True, 3)
    else:
        pending = (_Link(), True, 2)
    await drv._wait_out_interval(0.05, pending, heard_earlier)
    assert drv._held is True
    assert drv.get_state("connected") is not True


async def test_an_announced_connection_just_waits() -> None:
    drv = _widget()
    started = time.monotonic()
    await drv._wait_out_interval(0.05, (drv.transport, True, 0), True)
    assert time.monotonic() - started >= 0.04


# --- Announcing a held connection ---


async def test_an_answer_on_a_connection_that_is_not_held_announces_nothing(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget()
    announced: list[str] = []

    async def heard(event: str, payload: Any = None) -> None:
        announced.append(event)

    drv.events.on("device.connected.*", heard)
    drv._dropped_for_silence = True
    await drv._announce_held()
    await asyncio.sleep(0)
    assert announced == []
    assert drv.get_state("connected") is not True
    assert drv._dropped_for_silence is True
    assert _messages(caplog, logging.INFO) == []


async def test_announcing_a_held_connection_clears_the_verdict_and_says_so(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    drv = _widget()
    announced: list[str] = []

    async def heard(event: str, payload: Any = None) -> None:
        announced.append(event)

    drv.events.on("device.connected.*", heard)
    drv._held = True
    drv._dropped_for_silence = True
    drv._silence_verdict = drv._silence_fault(3)
    waiter = asyncio.get_running_loop().create_future()
    drv._answer_waiter = waiter
    await drv._announce_held()
    await asyncio.sleep(0)
    assert announced == ["device.connected.acme1"]
    assert drv.get_state("connected") is True
    assert (drv._held, drv._dropped_for_silence, drv._silence_verdict) == (False, False, None)
    assert waiter.result() is True
    assert _messages(caplog, logging.INFO) == ["[acme1] The device answered; connected"]


# --- The reconnect loop waiting on a held connection ---


async def test_with_no_hold_waiting_reads_whether_the_device_is_connected() -> None:
    drv = _widget()
    assert await drv._wait_for_answer() is False
    drv.set_state("connected", True)
    assert await drv._wait_for_answer() is True


async def test_a_held_connection_nothing_asks_is_announced_at_once() -> None:
    drv = _widget()
    drv._held = True
    drv._answer_waiter = asyncio.get_running_loop().create_future()
    assert await drv._wait_for_answer() is True
    assert drv.get_state("connected") is True
    assert drv._answer_waiter is None


async def test_a_held_connection_being_polled_waits_for_the_verdict() -> None:
    drv = _widget()
    drv._held = True
    waiter = asyncio.get_running_loop().create_future()
    drv._answer_waiter = waiter
    drv._poll_task = asyncio.ensure_future(asyncio.sleep(10))
    try:
        waiting = asyncio.ensure_future(drv._wait_for_answer())
        await asyncio.sleep(0.01)
        assert not waiting.done()
        drv._settle_answer(False)
        assert await waiting is False
        assert drv._held is True
    finally:
        drv._poll_task.cancel()


# --- Counts, polling's start and the failed-polls sentence ---


def test_push_deliveries_count_as_receives() -> None:
    drv = _widget()
    drv.transport.send_count = 2
    drv.transport.receive_count = 3
    drv._push_receives = 4
    assert drv._io_counts() == (drv.transport, 2, 7)


@pytest.mark.parametrize("bad", [None, 1.0, True, "3"])
def test_a_transport_whose_counts_are_not_integers_keeps_none(bad: Any) -> None:
    drv = _widget()
    drv.transport.receive_count = bad
    assert drv._io_counts() is None


async def test_a_poll_interval_of_zero_or_less_starts_no_loop() -> None:
    drv = _widget()
    await drv.start_polling(0)
    await drv.start_polling(-1)
    assert drv._poll_task is None


@pytest.mark.parametrize(
    ("polled", "waits"),
    [(None, False), (-0.1, False), (0.0, True), (-100.0, False)],
)
async def test_the_loop_waits_only_after_a_poll_this_connect_ran(
    polled: float | None, waits: bool,
) -> None:
    drv = _widget()
    began = time.monotonic()
    drv._connect_began_at = began
    drv._last_poll_started_at = None if polled is None else began + polled
    started = time.monotonic()
    await drv._wait_after_connect_poll(0.3)
    elapsed = time.monotonic() - started
    assert (elapsed >= 0.2) is waits


async def test_a_connect_with_no_start_time_polls_at_once() -> None:
    drv = _widget()
    drv._connect_began_at = None
    drv._last_poll_started_at = time.monotonic()
    started = time.monotonic()
    await drv._wait_after_connect_poll(5.0)
    assert time.monotonic() - started < 0.2


async def test_a_start_up_poll_older_than_the_interval_leaves_nothing_to_wait() -> None:
    drv = _widget()
    drv._connect_began_at = time.monotonic() - 10
    drv._last_poll_started_at = time.monotonic() - 6
    started = time.monotonic()
    await drv._wait_after_connect_poll(5.0)
    assert time.monotonic() - started < 0.2


def test_failed_polls_with_no_error_say_the_device_stopped_answering() -> None:
    drv = _widget()
    fault = drv._failed_polls_fault(4, None)
    assert fault.code == NO_RESPONSE
    assert fault.message == "The device stopped answering: 4 polls in a row failed."
