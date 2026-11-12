"""Liveness: the probe that asks a connected device whether it is still there,
and the hold that keeps a device offline until it answers again.

Two checks decide that a device has stopped answering: the liveness probe
here, and the poll loop's silence check (drivers/polling.py). Either one
drops the connection as ``no_response`` and records the verdict
(``_dropped_for_silence``, ``_silence_verdict``). The automatic reconnect
(core/device_reconnect.py) then sets ``_hold_next_connect``, and connect()
brings the next connection up *held*: sends, polls and probes run as usual,
but ``connected`` and ``device.connected.<id>`` wait for the first poll or
probe the device answers (``_announce_held``). Without the hold, a device
that never answers reads connected for most of every reconnect and its card
flickers.

The rules worth knowing before changing anything here:

* Overriding ``_liveness_probe`` is the whole opt-in (``_health_enabled``).
  A driver that supplies one turns the poll loop's silence check off, so the
  probe alone decides.
* A probe the device answered with an error is an answer: only a raise is a
  miss, and ``HEALTH_MAX_FAILURES`` misses in a row drop the connection. A
  typed ``auth_failed`` drops it on the first one, because asking again is
  what trips a device's lockout.
* A held connection is probed at once, waits up to ``HEALTH_INTERVAL_S``
  for the answer, and logs its misses at debug: it is shown offline already.

``LivenessWatch`` is a mixin that ``BaseDriver`` inherits, so every name stays
on the driver object: drivers override ``_liveness_probe``, ``_liveness_held``
and the ``HEALTH_*`` attributes and call ``_force_disconnect``, and the
device manager reads ``_hold_next_connect``, ``_wait_for_answer``,
``_dropped_for_silence`` and ``_silence_verdict``. ``BaseDriver.__init__``
calls ``_init_liveness()``. From its host this needs ``device_id``,
``config``, ``events``, ``_last_fault``, ``set_state()``, ``get_state()``,
``_link_alive()``, ``_stash_fault()`` and ``_handle_transport_disconnect()``
(BaseDriver), and ``_poll_task``, ``_io_counts()`` and
``_unread_replies_explain()`` (drivers/polling.py).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from openavc.core.connection_fault import (
    AUTH_FAILED,
    NO_RESPONSE,
    ConnectionFault,
    typed_fault_from_exc,
)
from openavc.core.event_bus import detach_emit_chain
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")


def _probe_miss_reason(exc: BaseException, deadline: float) -> str:
    """Why a liveness probe missed, for the log. The probe's deadline raises
    a timeout with no text of its own."""
    return str(exc) or (
        f"no answer within {deadline:g} s"
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        else type(exc).__name__
    )


@dataclass
class _ProbeWatch:
    """What _health_loop carries from one probe to the next, and the probe
    in flight: whether it went out on a held connection, its deadline, and
    when it started."""

    interval: float
    timeout: float
    max_failures: int
    wait: float = 0.0  # seconds before the next probe
    paused: bool = False  # the device's state holds the probe back
    held: bool = False
    deadline: float = 0.0
    started: float = 0.0


class LivenessWatch:
    """The liveness probe and the held connection (see the module docstring)."""

    # Liveness watchdog knobs (see _liveness_probe). Class attributes so a
    # driver can tune them wholesale (`HEALTH_INTERVAL_S = 20.0`) or per
    # instance; the defaults suit most request/response protocols. INTERVAL is
    # the gap between probes, TIMEOUT the per-probe reply deadline (INTERVAL on
    # a reconnect held offline, see _health_loop), and after
    # MAX_FAILURES consecutive misses the transport is torn down with a typed
    # ``no_response`` fault (FAULT_MESSAGE) so the platform reconnects and the
    # device card shows the real cause.
    HEALTH_INTERVAL_S: float = 30.0
    HEALTH_TIMEOUT_S: float = 5.0
    HEALTH_MAX_FAILURES: int = 2
    HEALTH_FAULT_MESSAGE: str = (
        "The device stopped answering keep-alive probes."
    )

    def _init_liveness(self) -> None:
        # The watchdog task and its count of misses in a row. Started by
        # connect() when the driver supplies a probe; stopped on disconnect
        # and on a transport drop.
        self._health_task: asyncio.Task | None = None
        self._health_failures = 0
        # `_dropped_for_silence` is the verdict that the device stopped
        # answering, kept until an answer; the reconnect loop sets
        # `_hold_next_connect` from it; `_held` is a connection that is up
        # but not yet announced, and `_answer_waiter` tells the reconnect
        # loop how it ended (_settle_answer).
        self._dropped_for_silence = False
        # The sentence the drop gave ("The device stopped answering: 3 polls
        # in a row ..."), kept until the device answers. A reconnect that gets
        # no answer either reads only as a generic no_response (an HTTP
        # device's connect check fails "is not responding"), whose advice to
        # check the driver, transport and port is wrong for a device that was
        # working; the device manager shows this sentence instead.
        self._silence_verdict: ConnectionFault | None = None
        self._hold_next_connect = False
        self._held = False
        self._answer_waiter: asyncio.Future[bool] | None = None
        # Seconds left in a restart one of this device's own commands
        # declared (restarts_device_for), asked of the DeviceManager that
        # holds the window (core/device_away.py); None without a manager.
        self._restart_window: Callable[[], int | None] | None = None

    # --- The probe (opt-in) ---

    async def _liveness_probe(self) -> None:
        """Optional hook: send a cheap request and await the device's reply.

        Override in drivers whose link can die silently where polling cannot
        tell: push/receive-mostly TCP that is not polled (no FIN when the
        device vanishes), serial, a device that streams data on its own, or
        a driver-owned session. A polled driver that reads replies over the
        platform's tcp/udp/osc transport is already watched for silence by
        the poll loop (_watches_for_silence); overriding this turns that
        check off, so the probe alone decides. Return normally when the
        device answered; raise when it did not. _health_loop runs it (see
        the HEALTH_* attributes). Overriding this is the whole opt-in:
        connect() starts the loop, disconnect and the transport-drop cleanup
        stop it.

        - Ask something every unit answers however it is set up (a firmware
          or model query, a no-op, the protocol's keep-alive), never an
          address from the device's configuration. Report a wrong setting
          as a child's fault, ``last_error`` or ``invalid_config``.
        - Any reply means the link is up, an error included: catch the
          protocol's error reply (a NAK, an exception response, an error
          frame) and return.
        - When every message is addressed to something configured (a
          display ID on a shared bus), ask the address that answered most
          recently on this connection, then the rest; raise only when none
          answers.
        - Never log back in from here. Log in again on the connection you
          have when the refusal arrives; a rejected credential is
          ``auth_failed``.
        """
        raise NotImplementedError

    def _health_enabled(self) -> bool:
        """True when this driver supplies a liveness probe.

        Default: the subclass overrides _liveness_probe. ConfigurableDriver
        overrides this to key off the YAML ``liveness:`` block instead (it
        always overrides the probe, but only some definitions declare one).
        """
        return type(self)._liveness_probe is not LivenessWatch._liveness_probe

    async def _liveness_held(self) -> bool:
        """True when the device's own state holds the liveness probe back now
        (a unit in standby that any message would wake).

        Asked just before each probe goes out, the first on a held reconnect
        included; nothing yields between a False and the probe's start. A
        held probe sends nothing and is neither a miss nor an answer: the
        count of misses in a row is left as it was. On a held connection it
        is announced instead once the device has answered anything on it, as
        the poll loop does for a held poll (_poll_waits_on_device). Default:
        never.
        """
        return False

    def _start_health_loop(self) -> None:
        """Start the liveness watchdog (idempotent while one is running)."""
        if self._health_task is None or self._health_task.done():
            self._health_failures = 0
            self._health_task = asyncio.ensure_future(self._health_loop())

    def _stop_health_loop(self) -> None:
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        self._health_task = None

    async def _health_loop(self) -> None:
        """Probe the device every HEALTH_INTERVAL_S and force a reconnect when
        it stops answering.

        The probe is awaited under HEALTH_TIMEOUT_S so a hung implementation
        can't stall the loop. Any exception (timeout, transport failure,
        protocol error) counts as a miss; a clean return resets the counter.
        So a probe whose request the device answered with an error returns
        instead of raising (see _liveness_probe): the device answered.

        The exception is a typed ``auth_failed``: the device answered by
        refusing the credential, so the connection drops on the first one and
        keeps that code, which pauses reconnecting rather than starting it.

        A drop for HEALTH_MAX_FAILURES misses holds the automatic reconnect
        offline until a probe is answered (connect(), _announce_held), as the
        poll loop's silence check does. A held connection is probed at once
        rather than an interval later: every stage of connect() has run by
        the time this loop starts, and the hold exists to ask whether the
        device is back.

        A held connection's probe waits for its answer up to HEALTH_INTERVAL_S
        rather than HEALTH_TIMEOUT_S. The device is shown offline already, so
        nothing is lost by waiting, and a unit still starting up (one that
        answers its first queries seconds late, then everything) is announced
        when its answer arrives instead of at the next probe. The probes still
        go out every interval: a miss there is followed by the next probe at
        once. The probe stays the judge, so its own rules still decide what
        counts as an answer (a YAML ``expect`` keeps a banner or unrelated
        chatter from announcing a device that never answers the probe).

        While the device's own state holds the probe back (_liveness_held),
        nothing goes out and nothing is counted; the log says so once when
        the probe pauses and once when it resumes.
        """
        detach_emit_chain()  # a device-lifetime loop is a root, not a continuation
        probe = _ProbeWatch(
            interval=float(self.HEALTH_INTERVAL_S),
            timeout=float(self.HEALTH_TIMEOUT_S),
            max_failures=max(int(self.HEALTH_MAX_FAILURES), 1),
        )
        # Seconds before the next probe: none on a held connection, which
        # exists to ask whether the device is back.
        probe.wait = 0.0 if self._held else probe.interval
        try:
            while self._link_alive():
                # Even at 0 this yields, so a probe that never awaits cannot
                # hold the event loop.
                await asyncio.sleep(probe.wait)
                probe.wait = probe.interval
                if not self._link_alive():
                    return
                held = await self._probe_held_back()
                if not self._link_alive():
                    return
                self._note_probe_pause(probe, held)
                if held:
                    await self._announce_if_answered_while_probe_held()
                    continue
                if await self._probe_once(probe):
                    return
        except asyncio.CancelledError:
            return

    async def _probe_held_back(self) -> bool:
        """_liveness_held, read as "not held" when the hook fails: a driver's
        own hook failing must not end the watchdog."""
        try:
            return await self._liveness_held()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                f"[{self.device_id}] Could not tell whether the "
                f"device's state holds the liveness probe back; "
                f"probing"
            )
            return False

    def _note_probe_pause(self, probe: _ProbeWatch, held: bool) -> None:
        """Say once in the log when the probe pauses, and once when it resumes."""
        if held != probe.paused:
            probe.paused = held
            log.info(
                f"[{self.device_id}] Liveness probe paused: the "
                f"device's state holds it back"
                if held else f"[{self.device_id}] Liveness probe resumed"
            )

    async def _announce_if_answered_while_probe_held(self) -> None:
        """Announce a held connection whose probe the device's state holds
        back, once the device has answered anything on it: nothing goes out
        to answer until that state changes."""
        counts = self._io_counts()
        if not self._held or counts is None:
            return
        if counts.received > 0:
            await self._announce_held()

    async def _probe_once(self, probe: _ProbeWatch) -> bool:
        """Send one liveness probe and judge it. True when that dropped the
        connection."""
        probe.held = self._held
        probe.deadline = max(probe.interval, probe.timeout) if probe.held else probe.timeout
        probe.started = time.monotonic()
        try:
            # asyncio.timeout, not wait_for: on Python 3.11 wait_for
            # starts the probe as a task, which yields first, and a
            # command sent in that gap would go out between the
            # decision above and the probe.
            async with asyncio.timeout(probe.deadline):
                await self._liveness_probe()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._probe_missed(probe, exc)
        self._health_failures = 0
        await self._announce_held()
        return False

    def _probe_missed(self, probe: _ProbeWatch, exc: Exception) -> bool:
        """Count a probe that raised. True when that dropped the connection:
        a refused credential drops it at once, HEALTH_MAX_FAILURES misses in
        a row drop it as ``no_response``."""
        refused = self._refused_credential(exc)
        if refused is not None:
            log.warning(
                f"[{self.device_id}] Liveness probe refused the "
                f"credential; dropping the connection: {exc}"
            )
            self._force_disconnect(AUTH_FAILED, refused.message)
            return True
        self._health_failures += 1
        if probe.held:
            # The next probe is due an interval after this one
            # went out, however much of it the wait used.
            probe.wait = max(0.0, probe.interval - (time.monotonic() - probe.started))
        why = _probe_miss_reason(exc, probe.deadline)
        # A held device is already shown offline: its misses are
        # expected, and a device that stays down for hours would
        # otherwise warn twice on every retry.
        say, restarting = (log.debug, "") if self._held else self._silence_log()
        say(
            f"[{self.device_id}] Liveness probe failed "
            f"({self._health_failures}/{probe.max_failures}): {why}{restarting}"
        )
        if self._health_failures < probe.max_failures:
            return False
        self._drop_for_unanswered_probes()
        return True

    def _drop_for_unanswered_probes(self) -> None:
        """Drop a connection whose device stopped answering the liveness
        probe, as ``no_response``. The automatic reconnect then holds it
        offline until a probe is answered (connect(), _announce_held)."""
        if self._held:
            log.info(
                f"[{self.device_id}] Still no answer to the "
                f"liveness probe; closing the connection to "
                f"try again"
            )
        else:
            say, restarting = self._silence_log()
            say(
                f"[{self.device_id}] Device stopped answering "
                f"liveness probes; disconnecting{restarting}"
            )
        self._dropped_for_silence = True
        self._force_disconnect(
            NO_RESPONSE, self._probe_fault_message()
        )
        self._silence_verdict = self._last_fault

    def _probe_fault_message(self) -> str:
        """What the card says when the liveness probe takes the device
        offline: the unread-replies check's sentence when it explains the
        silence better than "stopped answering" does (_unread_replies_explain),
        else HEALTH_FAULT_MESSAGE."""
        return self._unread_replies_explain() or self.HEALTH_FAULT_MESSAGE

    # --- What a poll and a probe share ---

    def _silence_log(self) -> tuple[Callable[..., None], str]:
        """How a missed poll or probe, and the drop it leads to, is logged,
        and what the line adds: a warning, except inside a restart one of the
        device's own commands declared, where it is expected to go quiet and
        its card already says it is restarting. Then it is info, and says so."""
        left = self._restart_window() if self._restart_window is not None else None
        if left is None:
            return log.warning, ""
        return log.info, f"; the device is restarting ({left} s left)"

    def _refused_credential(self, exc: BaseException) -> ConnectionFault | None:
        """The typed ``auth_failed`` fault ``exc`` carries, or None.

        A poll or a liveness probe drops the connection on the first one: the
        device answered by refusing the credential, and asking again is what
        trips its lockout. Untyped text that only reads like a refusal ("401
        Unauthorized") is not one, since an expired session looks the same.
        """
        refused = typed_fault_from_exc(
            exc, host=self.config.get("host", ""), port=self.config.get("port"),
        )
        if refused is not None and refused.code == AUTH_FAILED:
            return refused
        return None

    def _force_disconnect(self, code: str = NO_RESPONSE, message: str = "") -> None:
        """Tear down a dead transport and fire the disconnect path so the
        DeviceManager auto-reconnects / classifies the device offline.

        Callable from inside the health loop, so the task ref is dropped
        first — the disconnect cleanup would otherwise cancel the still-running
        loop out from under us. The typed fault is stashed because this
        disconnect carries no exception: a silently-dead device leaves no
        transport error, so without the stash the device card would show the
        generic "connection dropped".
        """
        self._health_task = None
        self._stash_fault(code, message)
        self._handle_transport_disconnect()

    # --- The held connection ---

    def _settle_answer(self, answered: bool) -> None:
        """Tell a reconnect loop waiting on a held connection how it ended."""
        waiter = self._answer_waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(answered)

    async def _announce_held(self) -> None:
        """A held connection's device answered a poll or a liveness probe:
        announce it now, the way connect() announces any other connection."""
        if not self._held:
            return
        self._held = False
        self._dropped_for_silence = False
        self._silence_verdict = None
        self.set_state("connected", True)
        log.info(f"[{self.device_id}] The device answered; connected")
        self._settle_answer(True)
        await self.events.emit(f"device.connected.{self.device_id}")

    def _nothing_asks(self) -> bool:
        """True when no poll loop and no liveness probe is running: nothing
        will ask the device anything it could answer."""
        polling = self._poll_task is not None and not self._poll_task.done()
        probing = self._health_task is not None and not self._health_task.done()
        return not (polling or probing)

    async def _wait_for_answer(self) -> bool:
        """For the reconnect loop, after a connect() that was held: True once
        the device answers a poll or a liveness probe (it is announced by
        then), False when the connection closes first (a silent verdict or
        missed probes again, or the link failing).

        A device that nothing asks (no poll running, no liveness probe) has
        nothing to answer, so it is announced on the open connection, as any
        connection is.
        """
        waiter = self._answer_waiter
        if waiter is None:
            return self.get_state("connected") is True
        try:
            if not waiter.done() and self._nothing_asks():
                await self._announce_held()
            return await waiter
        finally:
            if self._answer_waiter is waiter:
                self._answer_waiter = None
