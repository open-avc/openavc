"""Polling: the loop that runs a driver's poll() and judges what each cycle
says about the device.

Every cycle is judged three ways:

* **Failed polls.** A poll() that raises a transport-level error (a
  connection error, a timeout, an OS error, an httpx HTTP error) is a missed
  poll, and ``max_missed_polls`` in a row drop the connection. Anything else
  it raises is a device that is reachable and misbehaving: logged and
  emitted as ``device.error``, never counted. A typed ``auth_failed`` drops
  the connection on the first one, because asking again is what trips a
  device's lockout.
* **Silence** (_watches_for_silence). A fire-and-forget poll over a byte
  stream returns as soon as its bytes are written, so a peer that accepts
  the connection and never answers would read connected for as long as the
  socket stays open. ``max_missed_polls`` polls in a row that sent something
  and heard nothing back drop it as ``no_response``, and the automatic
  reconnect then holds the device offline until it answers
  (drivers/liveness.py).
* **Unread replies** (_parsed_count). Something that answers every poll in
  its own language is not silent: ``max_missed_polls`` cycles in a row that
  brought replies and had none read put a warning in the log and a sentence
  in ``last_error``, and nothing else. The connection stays up, since many
  drivers leave some replies unread by design.

The rule worth knowing before changing anything here: a count is judged on
the connection it was taken on (_heard_since). A cycle judged after a
reconnect replaced the transport counts for nothing either way.

``PollLoop`` is a mixin that ``BaseDriver`` inherits, so every name stays on
the driver object: drivers call ``start_polling`` and ``stop_polling`` and
override ``_silence_expected``, ``_poll_waits_on_device``, ``_silence_fault``,
``_parsed_count``, ``_framed_count`` and ``_unread_sample``, and read
``_io_counts()``. ``BaseDriver.__init__`` calls ``_init_polling()``. From its
host this needs ``DRIVER_INFO``, ``LAST_ERROR_PROPERTY``, ``device_id``,
``config``, ``events``, ``transport``, ``_connected``, ``_push_receives``,
``_last_error_writes``, ``_last_poll_started_at``, ``_connect_began_at``,
``poll()``, ``set_state()``, ``get_state()``, ``_effective_transport()``,
``_reads_replies()`` and ``_handle_transport_disconnect()`` (BaseDriver), and
``_held``, ``_last_fault``, ``_silence_verdict``, ``_dropped_for_silence``,
``_health_enabled()``, ``_announce_held()`` and ``_refused_credential()``
(drivers/liveness.py).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, NamedTuple

from openavc.core.connection_fault import (
    NO_RESPONSE,
    TRANSPORT_DISCONNECTED,
    ConnectionFault,
    classify_connection_fault,
)
from openavc.core.event_bus import detach_emit_chain
from openavc.drivers.compiled_protocol import state_var_default
from openavc.drivers.spec import SILENCE_CHECK_TRANSPORTS
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")

# The transports the poll loop watches for a peer that answers no poll
# (_watches_for_silence). The list and the reason for each one left out live
# in spec.py, where the driver validator reads them too.
_SILENCE_CHECK_TRANSPORTS = frozenset(SILENCE_CHECK_TRANSPORTS)

# How often a held connection looks for the device's first answer between
# polls (_wait_out_interval).
_HELD_CHECK_S = 0.5


class IoCounts(NamedTuple):
    """What the live connection has sent and received (_io_counts)."""

    transport: Any
    sent: int
    received: int


def _heard_since(now: IoCounts | None, transport: Any, heard_before: int) -> bool:
    """True when ``now`` is the same connection as ``transport`` and it has
    received something since its count stood at ``heard_before``."""
    return _on_connection(now, transport) and now.received > heard_before


def _on_connection(now: IoCounts | None, transport: Any) -> bool:
    """True when ``now`` was taken on ``transport``: a count from another
    connection (a reconnect replaced it) says nothing about this one."""
    return now is not None and now.transport is transport


def _httpx_errors() -> tuple[type[BaseException], ...]:
    """The errors a poll's HTTP failure raises: httpx's, when it imports."""
    try:
        import httpx
    except ImportError:
        return ()
    return (httpx.HTTPError,)


@dataclass
class _PollWatch:
    """What _poll_loop carries from one cycle to the next.

    ``pending`` is the last poll, for the silence check: its transport,
    whether it sent anything, and the connection's receive count when it
    began. ``parse_pending`` is the same poll for the unread-replies check:
    its transport, receive count, parsed count and framed-message count when
    it began. Each is judged when the next poll is due.
    """

    max_missed: int
    watching: bool  # the silence check runs (_watches_for_silence)
    reading: bool  # the unread-replies check runs (_parsed_count)
    dry_polls: int = 0
    last_exc: BaseException | None = None
    silent_polls: int = 0
    heard_cycles: int = 0
    pending: tuple[Any, bool, int] | None = None
    unread_cycles: int = 0
    parse_pending: tuple[Any, int, int, int | None] | None = None


class PollLoop:
    """The poll loop and the checks it runs (see the module docstring)."""

    def _init_polling(self) -> None:
        self._poll_task: asyncio.Task | None = None
        # True while the unread-replies check holds: polls in a row brought
        # replies and the driver's rules read none of them (_parsed_count).
        # The loop owns `last_error` meanwhile, so the stale-error clear
        # leaves its sentence alone.
        self._unread_replies_hold: bool = False
        # The sentence the check last wrote (it clears only its own), and the
        # framed-message count when the current run of unread cycles began.
        self._unread_sentence: str = ""
        self._unread_run_framed_from: int | None = None

    async def start_polling(self, interval: float) -> None:
        """Start a background polling loop at the given interval (seconds)."""
        if interval <= 0:
            return
        await self.stop_polling()
        self._poll_task = asyncio.create_task(self._poll_loop(interval))
        log.debug(f"[{self.device_id}] Polling started (every {interval}s)")

    async def stop_polling(self) -> None:
        """Cancel the polling background task."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            log.debug(f"[{self.device_id}] Polling stopped")

    def _clear_stale_last_error(self) -> None:
        """Drop a ``last_error`` the device has stopped reporting.

        Called after a poll that finished cleanly **and** wrote nothing to the
        property: a fault that is still happening rewrites it on the way past,
        so what survives to here is an error from before — and an error from
        before, sitting under a device that is answering normally, is the thing
        that made the field unreadable.

        Only for a driver that declares the property. The platform does not
        invent the convention for a driver that never opted into it, and it
        clears to the value the declaration implies rather than to ``None``:
        this is a device that just answered cleanly, so "no error" is something
        we know, not something nobody has said.
        """
        var_def = self.DRIVER_INFO.get("state_variables", {}).get(
            self.LAST_ERROR_PROPERTY
        )
        if var_def is None:
            return
        current = self.get_state(self.LAST_ERROR_PROPERTY)
        cleared = state_var_default(var_def)
        if current == cleared or current in (None, ""):
            return
        self.set_state(self.LAST_ERROR_PROPERTY, cleared)

    async def _poll_loop(self, interval: float) -> None:
        """Background loop that calls self.poll() periodically.

        Each cycle is judged the three ways the module docstring lists.
        Drivers MUST propagate transport errors from poll(): swallowing
        httpx.ConnectError and friends in a driver's poll() makes connected
        state lie. Untyped text that only reads like a refused credential
        ("401 Unauthorized") still counts as a missed poll, since an expired
        session looks the same and the classifier is guessing.

        The first poll runs at once, unless this connect has already polled
        (an _initial_sync that reads state): that was the first cycle, so the
        loop waits out the rest of its interval before polling again, and a
        device is not sent every read twice back to back on each connect.

        A poll that returned cleanly is judged again when the next one is
        due: for silence, when it sent something and nothing came back since
        it began while the driver did not expect silence (_silence_expected);
        for unread replies, when it brought replies and none was read
        (_judge_reading). The reconnect after a silence drop is held offline
        until a poll is answered (connect(), _announce_held), or, while the
        device's own state holds the poll back, until it has answered
        anything on the new connection (_poll_waits_on_device). Only cycles
        this loop ran are judged; a connect-time poll is not.
        """
        detach_emit_chain()  # a device-lifetime loop is a root, not a continuation
        httpx_errors = _httpx_errors()
        watch = _PollWatch(
            max_missed=self._max_missed_polls(),
            watching=self._watches_for_silence(),
            reading=self._parsed_count() is not None,
        )
        # A verdict lasts while the automatic reconnect holds the device
        # offline (the warning is logged once an episode, and the sentence
        # stays in last_error); any other connection starts again, and the
        # sentence a held check left behind clears after its first clean poll.
        if not self._held:
            self._unread_replies_hold = False
        try:
            await self._wait_after_connect_poll(interval)
            while True:
                self._judge_last_reading(watch)
                if await self._judge_last_silence(watch):
                    return
                if await self._poll_once(watch, httpx_errors):
                    return
                if watch.dry_polls >= watch.max_missed:
                    self._drop_for_failed_polls(watch)
                    return
                await self._wait_out_interval(interval, watch.pending, watch.heard_cycles >= 1)
        except asyncio.CancelledError:
            return

    def _max_missed_polls(self) -> int:
        """The device's ``max_missed_polls``, at least 1.

        A value of 0 (or negative, or non-numeric) would drop a healthy device
        after its first poll: the watchdog cannot be turned off that way, and
        the minimum is one missed poll.
        """
        try:
            return max(int(self.config.get("max_missed_polls", 3)), 1)
        except (TypeError, ValueError):
            return 3

    async def _wait_after_connect_poll(self, interval: float) -> None:
        """Wait out the rest of the interval when this connect has polled
        already: an _initial_sync that reads state was the first cycle."""
        polled_at = self._last_poll_started_at
        began_at = self._connect_began_at
        if polled_at is None or began_at is None:
            return
        if polled_at < began_at:
            return
        remaining = interval - (time.monotonic() - polled_at)
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _wait_out_interval(
        self, interval: float, pending: tuple[Any, bool, int] | None, heard_earlier: bool
    ) -> None:
        """Sleep until the next poll is due, looking for a held connection's
        answer on the way when one may come (_may_answer_mid_interval): a
        device that answers is announced within _HELD_CHECK_S rather than at
        the next poll."""
        if not self._may_answer_mid_interval(pending, heard_earlier):
            await asyncio.sleep(interval)
            return
        transport, _sent, heard_before = pending
        deadline = time.monotonic() + interval
        while (left := deadline - time.monotonic()) > 0:
            await asyncio.sleep(min(_HELD_CHECK_S, left))
            now = self._io_counts()
            if self._held and _heard_since(now, transport, heard_before):
                await self._announce_held()

    def _may_answer_mid_interval(
        self, pending: tuple[Any, bool, int] | None, heard_earlier: bool,
    ) -> bool:
        """True when a held connection's answer is looked for between polls:
        this cycle's poll went out and an earlier cycle heard something. One
        cycle that hears something is not enough: a banner sent to every new
        connection (a telnet prompt) lands in the first one, from a device
        that may answer nothing after it."""
        if pending is None or not heard_earlier:
            return False
        return self._held

    # --- What the device sent back ---

    def _io_counts(self) -> IoCounts | None:
        """``(transport, sent, received)`` for the live connection, or None
        when its transport keeps no counts (a driver-owned session, a test
        double, serial). Push deliveries count as receives."""
        transport = self.transport
        sent = getattr(transport, "send_count", None)
        received = getattr(transport, "receive_count", None)
        if type(sent) is not int or type(received) is not int:
            return None
        return IoCounts(transport, sent, received + self._push_receives)

    async def _poll_once(
        self, watch: _PollWatch, httpx_errors: tuple[type[BaseException], ...],
    ) -> bool:
        """Run one poll() and count what it says about the device. True when
        that dropped the connection: the device refused the credential.

        A transport-level failure (a connection error, a timeout, an OS error
        or an httpx HTTP error) is a missed poll. Anything else is a device
        that is reachable and misbehaving: logged and emitted as
        ``device.error``, never counted. The bookkeeping after a clean poll
        runs inside the same handlers, so a failure there is judged the way
        a failing poll is.
        """
        try:
            await self._poll_and_note(watch)
        except (ConnectionError, TimeoutError, OSError) as exc:
            if self._poll_refused_credential(exc):
                return True
            log.warning(
                f"[{self.device_id}] Poll failed (connection): {exc}"
            )
            watch.dry_polls += 1
            watch.last_exc = exc
        except httpx_errors as exc:
            log.warning(
                f"[{self.device_id}] Poll failed (HTTP): {exc}"
            )
            watch.dry_polls += 1
            watch.last_exc = exc
        except Exception as exc:
            log.exception(
                f"[{self.device_id}] Unexpected error during poll"
            )
            await self._emit_poll_error(exc)
        return False

    async def _poll_and_note(self, watch: _PollWatch) -> None:
        """poll(), then what a clean one leaves: the missed-poll count reset,
        a stale last_error cleared, and its traffic kept for the next cycle's
        silence and unread-replies checks."""
        errors_before = self._last_error_writes
        before = self._io_counts() if watch.watching or watch.reading else None
        read_before = (
            (self._parsed_count(), self._framed_count()) if watch.reading else (None, None)
        )
        await self.poll()
        watch.dry_polls = 0
        watch.last_exc = None
        if (
            self._last_error_writes == errors_before
            and not self._unread_replies_hold
        ):
            self._clear_stale_last_error()
        if before is not None:
            await self._note_poll_traffic(watch, before, read_before)

    async def _note_poll_traffic(
        self, watch: _PollWatch, before: IoCounts,
        read_before: tuple[int | None, int | None],
    ) -> None:
        """Keep a clean poll's traffic for the checks the next cycle runs,
        when it went out on the connection it began on."""
        after = self._io_counts()
        if not _on_connection(after, before.transport):
            return
        if self._answered_while_poll_held(before, after):
            # Nothing goes out to answer until the device's state changes,
            # and the device has answered on this connection already.
            await self._announce_held()
        if watch.watching:
            watch.pending = (before.transport, after.sent > before.sent, before.received)
        parsed_before, framed_before = read_before
        if parsed_before is not None:
            watch.parse_pending = (before.transport, before.received, parsed_before, framed_before)

    def _answered_while_poll_held(self, before: IoCounts, after: IoCounts) -> bool:
        """Whether a held connection's poll sent nothing because the device's
        own state holds polling back (_poll_waits_on_device), from a device
        that has answered on this connection already."""
        if not (self._held and self._connected):
            return False
        if after.sent != before.sent or after.received <= 0:
            return False
        return self._poll_waits_on_device()

    def _poll_refused_credential(self, exc: BaseException) -> bool:
        """Drop the connection when a poll failed by the device refusing the
        credential; True when it did."""
        refused = self._refused_credential(exc)
        if refused is None:
            return False
        log.warning(
            f"[{self.device_id}] Poll refused the credential; "
            f"dropping the connection: {exc}"
        )
        self._last_fault = refused
        self._handle_transport_disconnect()
        return True

    async def _emit_poll_error(self, exc: BaseException) -> None:
        """``device.error`` for a poll that failed with the device reachable."""
        try:
            await self.events.emit(
                f"device.error.{self.device_id}",
                {"device_id": self.device_id, "error": str(exc)},
            )
        except Exception:
            log.exception(
                f"[{self.device_id}] Failed to emit device.error"
            )

    # --- Failed polls ---

    def _drop_for_failed_polls(self, watch: _PollWatch) -> None:
        """Drop the connection after ``max_missed_polls`` failed polls in a row."""
        log.warning(
            f"[{self.device_id}] No response for "
            f"{watch.dry_polls} poll cycles — marking disconnected"
        )
        fault = self._failed_polls_fault(watch.dry_polls, watch.last_exc)
        self._last_fault = fault
        if fault.code == NO_RESPONSE:
            self._silence_verdict = fault
        self._handle_transport_disconnect()

    def _failed_polls_fault(
        self, count: int, last_exc: BaseException | None,
    ) -> ConnectionFault:
        """Why ``count`` failed polls in a row dropped the connection.

        Recorded for the fault classifier: the disconnect event carries no
        exception, and a silently dead peer leaves no transport error, so
        without this the card shows the generic "connection dropped". The last
        poll's error decides when it says more than the generic codes do (a
        typed fault poll() raised, or a plain ConnectionError that really means
        "connection refused"). Those generic codes, no_response and
        transport_disconnected, give way to a sentence saying the device WAS
        answering and then stopped, which reads better than either.
        """
        if last_exc is not None:
            classified = classify_connection_fault(
                last_error=None,
                exc=last_exc,
                host=self.config.get("host", ""),
                port=self.config.get("port"),
                transport=self.config.get("transport", ""),
            )
            if classified.code not in (NO_RESPONSE, TRANSPORT_DISCONNECTED):
                return classified
        return ConnectionFault(
            NO_RESPONSE,
            f"The device stopped answering: {count} polls "
            f"in a row failed.",
        )

    # --- Silence (a peer that takes every poll and answers none) ---

    def _watches_for_silence(self) -> bool:
        """Whether the poll loop drops a connection that answers no poll.

        On when all of these hold:

        - the transport is one of _SILENCE_CHECK_TRANSPORTS and keeps counts;
        - the driver supplies no liveness probe (that watchdog already
          decides when to drop, with the driver's own knowledge of the
          protocol; the reconnect after its drop is held the same way);
        - the driver reads replies (_reads_replies): a device that is only
          ever told things has nothing to answer with.
        """
        if self._effective_transport() not in _SILENCE_CHECK_TRANSPORTS:
            return False
        if self._health_enabled() or not self._reads_replies():
            return False
        return self._io_counts() is not None

    def _silence_expected(self) -> bool:
        """True while the device may answer nothing at all and still be fine.

        Asked each time the silence check judges a poll that drew no reply:
        while it is True, that poll does not count toward dropping the
        connection (nor does it reset the count). Override it in a driver
        that knows the device is in such a state, e.g. a display the driver
        knows is off whose manual only promises replies when it is on.
        Default: never.
        """
        return False

    def _poll_waits_on_device(self) -> bool:
        """True when the poll that just ran sent nothing because the device's
        own state holds polling back (a unit in standby that any command
        would wake).

        Asked while a connection is held after a drop for silence: no poll
        goes out until that state changes, so waiting for a poll to be
        answered would keep the device offline for good. It is announced
        instead once it has answered anything on this connection. Default:
        never.
        """
        return False

    def _silence_fault(self, cycles: int) -> ConnectionFault:
        """The no_response fault for ``cycles`` polls in a row that drew
        nothing back: one sentence for a connection that has never answered
        (the address or the driver is the likely cause), another for a device
        that answered and then stopped."""
        counts = self._io_counts()
        if counts is not None and counts.received == 0:
            return ConnectionFault(
                NO_RESPONSE,
                f"Nothing has answered on this connection: {cycles} polls in "
                f"a row got no reply. Check the address and port, and that "
                f"this driver matches the device.",
            )
        return ConnectionFault(
            NO_RESPONSE,
            f"The device stopped answering: {cycles} polls in a row got no "
            f"reply.",
        )

    async def _judge_last_silence(self, watch: _PollWatch) -> bool:
        """The silence check on the poll before this one. True when it
        dropped the connection: ``max_missed_polls`` polls in a row that
        sent something heard nothing back."""
        pending = watch.pending
        if pending is None:
            return False
        watch.pending = None
        await self._count_silence(watch, pending)
        if watch.silent_polls < watch.max_missed:
            return False
        self._drop_for_silence(watch.silent_polls)
        return True

    async def _count_silence(self, watch: _PollWatch, pending: tuple[Any, bool, int]) -> None:
        """Count one poll toward the silence check.

        An answer since it began resets the count, and the second cycle in a
        row that heard one announces a held connection. A poll that sent
        something and heard nothing adds one, unless the driver expects
        silence now (_silence_expected). A poll judged on another connection
        than the one it went out on counts for nothing.
        """
        transport, sent, heard_before = pending
        now = self._io_counts()
        if not _on_connection(now, transport):
            return
        if now.received > heard_before:
            watch.silent_polls = 0
            watch.heard_cycles += 1
            if watch.heard_cycles >= 2:
                await self._announce_held()
        elif sent and not self._silence_expected():
            watch.silent_polls += 1

    def _drop_for_silence(self, cycles: int) -> None:
        """Drop a connection whose polls drew no answer, as ``no_response``.
        The automatic reconnect then holds the device offline until it
        answers (connect(), _announce_held)."""
        if self._held:
            log.info(
                f"[{self.device_id}] Still nothing back from "
                f"{cycles} poll cycles; closing the "
                f"connection to try again"
            )
        else:
            log.warning(
                f"[{self.device_id}] Nothing came back from "
                f"{cycles} poll cycles in a row; marking "
                f"disconnected"
            )
        self._last_fault = self._silence_fault(cycles)
        self._silence_verdict = self._last_fault
        self._dropped_for_silence = True
        self._handle_transport_disconnect()

    # --- Unread replies (something answers that the driver does not read) ---

    def _parsed_count(self) -> int | None:
        """How many received messages this driver's own rules have read on
        this connection, or None when the platform cannot tell (the default:
        a Python driver parses in its own code).

        The poll loop compares it with the transport's receive count
        (_io_counts): polls in a row that brought replies and moved this
        count not at all mean something answers that the driver does not
        understand, the wrong port or the wrong driver. Never a disconnect:
        many drivers leave some replies unread by design (an acknowledgement,
        a reply a rule does not need), so only "none at all" is a finding,
        and it is reported, not acted on.
        """
        return None

    def _framed_count(self) -> int | None:
        """How many complete messages have reached this driver's response
        rules on this connection, read or not; None when the platform cannot
        tell. Bytes that arrive and never end the way the driver frames a
        message (the wrong line ending, another protocol) count as received
        but never as a message, which is how the unread-replies check tells
        its two findings apart."""
        return None

    def _unread_sample(self) -> str:
        """The last reply no rule read, for the log line (empty: none kept)."""
        return ""

    def _unread_replies_message(self, cycles: int, framed: bool = True) -> str:
        """The ``last_error`` sentence while the unread-replies check holds:
        ``framed`` False when no reply in the run ended the way this driver
        frames a message."""
        if not framed:
            return (
                f"Something is answering on this connection, but none of its "
                f"replies ends the way this driver expects: {cycles} polls in "
                f"a row. Check the port, and that this driver matches the "
                f"device."
            )
        return (
            f"Something is answering on this connection, but this driver reads "
            f"none of it: {cycles} polls in a row. Check the port, and that "
            f"this driver matches the device."
        )

    def _unread_replies_explain(self) -> str:
        """The check's sentence while it holds on a connection that did
        receive replies, else "". A device that stops answering a liveness
        probe then is answering in a language this driver does not read (the
        wrong port or the wrong driver), which the sentence names better than
        "stopped answering" does (_probe_fault_message)."""
        if not (self._unread_replies_hold and self._unread_sentence):
            return ""
        counts = self._io_counts()
        if counts is None or counts.received <= 0:
            return ""
        return self._unread_sentence

    def _judge_last_reading(self, watch: _PollWatch) -> None:
        """The unread-replies check on the poll before this one (_judge_reading)."""
        if watch.parse_pending is None:
            return
        watch.unread_cycles = self._judge_reading(
            watch.parse_pending, watch.unread_cycles, watch.max_missed,
        )
        watch.parse_pending = None

    def _judge_reading(
        self, parse_pending: tuple[Any, int, int, int | None], cycles: int,
        limit: int,
    ) -> int:
        """Judge one poll cycle for the unread-replies check and return the
        new count of cycles in a row that heard replies and read none.

        A cycle that heard nothing says nothing about reading (that is the
        silence check's business) and leaves the count as it was. At
        ``limit`` the check holds (_hold_unread_replies); the first cycle
        that reads anything ends it (_end_unread_replies_hold).
        """
        transport, heard_before, parsed_before, framed_before = parse_pending
        now = self._io_counts()
        parsed = self._parsed_count()
        if parsed is None or not _heard_since(now, transport, heard_before):
            return cycles
        if parsed > parsed_before:
            self._end_unread_replies_hold()
            return 0
        if cycles == 0:
            self._unread_run_framed_from = framed_before
        cycles += 1
        if cycles >= limit:
            self._hold_unread_replies(cycles, limit)
        return cycles

    def _end_unread_replies_hold(self) -> None:
        """A cycle read a reply: the hold ends, and its sentence clears from
        ``last_error`` unless something else was written there since."""
        if not self._unread_replies_hold:
            return
        self._unread_replies_hold = False
        log.info(f"[{self.device_id}] The device's replies are read again")
        if self.get_state(self.LAST_ERROR_PROPERTY) == self._unread_sentence:
            self._clear_stale_last_error()

    def _hold_unread_replies(self, cycles: int, limit: int) -> None:
        """``cycles`` in a row, at least ``limit``, heard replies and read
        none: a warning in the log once, the sentence in ``last_error`` every
        cycle while it holds (when the driver declares the property)."""
        framed = self._unread_run_framed()
        self._unread_sentence = self._unread_replies_message(limit, framed)
        if not self._unread_replies_hold:
            self._unread_replies_hold = True
            self._warn_unread_replies(cycles, framed)
        if self.LAST_ERROR_PROPERTY in self.DRIVER_INFO.get("state_variables", {}):
            self.set_state(self.LAST_ERROR_PROPERTY, self._unread_sentence)

    def _unread_run_framed(self) -> bool:
        """False when no reply in this run of unread cycles ended the way the
        driver frames a message: the framed count has not moved since the
        run began. True when it moved, or the platform cannot tell."""
        framed_now = self._framed_count()
        since = self._unread_run_framed_from
        return framed_now is None or since is None or framed_now > since

    def _warn_unread_replies(self, cycles: int, framed: bool) -> None:
        """The one log warning a hold gives, with the last unread reply when
        the replies were framed messages."""
        if framed:
            sample = self._unread_sample()
            finding = "no response rule reads any of them"
        else:
            sample = ""
            finding = (
                "none of them ends the way this driver expects, so no "
                "response rule can read them"
            )
        log.warning(
            f"[{self.device_id}] Replies arrive but {finding} "
            f"({cycles} polls in a row)"
            + (f"; the last one was {sample!r}" if sample else "")
            + ". Check the port, and that this driver matches the "
            "device."
        )
