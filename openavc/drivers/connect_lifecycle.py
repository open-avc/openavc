"""A driver's connection: connect, disconnect, and a connection that drops.

``connect()`` runs its stages in order, one method each (its docstring lists
them): a clean slate, the bridge-routed shortcut, the refusals, the driver's
``_pre_connect`` hook, building and checking the transport, the handshake
and the declare, push, the initial sync, then polling and the liveness
watchdog. ``disconnect()`` is the deliberate end, and
``_handle_transport_disconnect`` is what a transport calls when the
connection closes under the driver.

The rules worth knowing before changing anything here:

* One connect runs at a time, under ``_connect_lock``. A disconnect, or a
  newer connect, ends the one in flight (``_end_connect_in_flight``): it
  closes what it built and raises ``ConnectEndedError``, which its caller
  reports nothing for, since the door that ended it owns the device now.
* From the moment the transport exists until the handshake settles, a drop
  of the connection is the connect's to report: it resolves
  ``_connect_drop`` instead of starting the usual teardown, the handshake is
  cancelled, and the connect fails at once with the transport's own words.
  No disconnect is emitted for a connection that never came up.
* A held connection (the reconnect asks for one after the device stopped
  answering, drivers/liveness.py) is up but not announced: ``connected`` and
  its event wait for the device's first answer, and when it comes down
  nothing is emitted; the reconnect loop waiting on it is told instead.

``ConnectLifecycle`` is a mixin ``BaseDriver`` inherits, so every name stays
on the driver object: drivers override ``connect``, ``disconnect`` and
``_handle_transport_disconnect``. The hooks the stages call
(``_pre_connect``, ``_post_connect``, ``_initial_sync``, ``_close_session``,
``_connect_check_held``) stay on ``BaseDriver`` with the rest of a driver's
surface. From its host this needs the transport builder
(drivers/transport_ladder.py), the push channel (drivers/push.py), the poll
loop (drivers/polling.py) and the liveness watch (drivers/liveness.py).
``ConnectEndedError`` stays importable from ``openavc.drivers.base``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, NoReturn

from openavc.core.connection_fault import (
    INVALID_CONFIG,
    LOGIN_MISSING,
    NO_RESPONSE,
    TRANSPORT_DISCONNECTED,
    ConnectionFaultError,
    bridge_port_missing,
    bridge_port_missing_message,
    classify_connection_fault,
    default_fault_message,
    no_address_message,
    typed_fault_from_exc,
)
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")


class ConnectEndedError(ConnectionError):
    """A connect was ended before it finished, by a disconnect or a newer
    connect of the same driver (the Reconnect button, a pause, a resume).

    The door that ended it owns the device now, so its caller reports nothing
    for it and starts no retry (core/device_manager.py).
    """


# What a disconnect resolves a connect's drop future with (_run_handshake).
_CONNECT_ENDED = object()


def _raise_ended(exc: BaseException) -> NoReturn:
    """Raise what a connect another door ended raises for ``exc``, the
    failure it ended on: a ConnectEndedError as it is, anything else as the
    cause of one."""
    if isinstance(exc, ConnectEndedError):
        raise exc
    raise ConnectEndedError(default_fault_message(TRANSPORT_DISCONNECTED)) from exc


def _verify_failure_error(
    said: str, cause: BaseException | None, host: Any, port: Any, transport: str,
) -> ConnectionError:
    """What a connect raises when the transport's verify step failed.

    "Device at <host>:<port> is not responding", unless the transport said
    why in a line of its own (ssh's "Connection refused" for a closed port):
    then that line, so the log's "Failed to connect" names the reason the
    device card shows. The line has to say that reason by itself (an HTTP
    refusal lives in the exception's chain, not in httpx's words). When none
    does, or the card's reason is ``no_response`` or a plain drop, "is not
    responding" stays: that sentence is what makes the card say
    ``no_response``. ``said`` is the transport's ``last_error``.
    """
    not_responding = ConnectionError(f"Device at {host}:{port} is not responding")
    not_responding.__cause__ = cause
    lines = [line.strip() for line in (said or "").splitlines() if line.strip()]
    if not lines:
        return not_responding
    fault = classify_connection_fault(
        last_error=said, exc=not_responding, host=str(host), port=port, transport=transport,
    )
    if fault.code in (NO_RESPONSE, TRANSPORT_DISCONNECTED):
        return not_responding
    for line in reversed(lines):
        alone = classify_connection_fault(
            last_error=line, exc=None, host=str(host), port=port, transport=transport,
        )
        if alone.code == fault.code:
            return ConnectionError(line)
    return not_responding


def _typed_handshake_failure(
    handshake: asyncio.Future[None] | None,
) -> BaseException | None:
    """The failure a finished handshake ended on, when it is a typed fault (a
    refused login); None for a handshake that never ran, was cancelled,
    returned, or failed some other way."""
    if handshake is None or not handshake.done():
        return None
    if handshake.cancelled():
        return None
    failure = handshake.exception()
    if typed_fault_from_exc(failure) is None:
        return None
    return failure


async def _stop_handshake(handshake: asyncio.Future[None] | None) -> None:
    """Cancel a handshake the drop cut short and wait for it to stop. One
    that ended on a failure of its own instead has that failure read, so it
    is not logged as never retrieved."""
    if handshake is None or handshake.done():
        return
    handshake.cancel()
    await asyncio.wait((handshake,))
    if not handshake.cancelled():
        handshake.exception()


class ConnectLifecycle:
    """Connect, disconnect, and a drop of the connection, for ``BaseDriver``
    (see the module docstring)."""

    # Supplied by BaseDriver; declared here for the methods.
    device_id: str
    config: dict[str, Any]
    transport: Any

    def _init_connect_lifecycle(self) -> None:
        # Set while connect() builds its transport, verifies it and runs the
        # handshake. A drop in that window resolves it instead of starting the
        # usual teardown: the connect is still running, so its failure is the
        # one report of this connection (_handle_transport_disconnect).
        self._connect_drop: asyncio.Future[object] | None = None
        # The connect that owns this driver (a token), and the lock that runs
        # connects one at a time. A disconnect, or a newer connect, ends the
        # one in flight (_end_connect_in_flight); it then unwinds before the
        # next one builds anything, so it can only close what it built.
        self._connect_attempt: object | None = None
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """
        Establish connection to the device — the one place the connection
        lifecycle lives.

        Stages, in order:

        1. Clean slate: reset fault classification, drop stale push
           subscriptions / driver sessions / transports from a previous
           attempt.
        2. Resolve the transport type (config override, then DRIVER_INFO);
           bridge-routed devices mirror their bridge and return early.
        3. Establish: _pre_connect() hook, then _create_transport() builds
           ``self.transport`` (constructor kwargs pass through
           _transport_kwargs(); drivers that own their session override
           _create_transport() and leave ``self.transport`` None).
        4. Verify: connectionless transports (OSC/HTTP) probe reachability
           before the device may report connected.
        5. Handshake: _post_connect() runs logins/negotiation before
           `connected` is declared; then the canonical declare — internal
           flag, ``connected`` state key, the ``device.connected.<id>``
           event. A connection that drops anywhere in stages 3 to 5 ends the
           handshake and fails the connect at once (_run_handshake).
        6. Tail: push subscription, _initial_sync() hook (identity reads,
           rosters, keep-alive loops), then polling and the liveness
           watchdog.

        One connect runs at a time. A disconnect, or another connect, while
        this one is still running ends it at once (the Reconnect button
        pressed while a device stays silent): it closes what it built and
        raises :class:`ConnectEndedError`, which the caller reports nothing
        for, since the door that ended it owns the device now.

        Prefer overriding the hooks over overriding this method — a driver
        that re-implements connect() must keep every stage above by hand
        (clean-slate reset, the canonical event topic, watchdog restart),
        and history shows most copies drop some of them.
        """
        self._end_connect_in_flight()
        attempt = object()
        self._connect_attempt = attempt
        async with self._connect_lock:
            try:
                self._raise_if_ended(attempt)
                await self._connect_once(attempt)
            finally:
                if self._connect_attempt is attempt:
                    self._connect_attempt = None

    def _end_connect_in_flight(self) -> None:
        """End the connect running on this driver, or waiting to run: it
        fails with :class:`ConnectEndedError` instead of finishing."""
        self._connect_attempt = None
        dropped = self._connect_drop
        if dropped is not None and not dropped.done():
            dropped.set_result(_CONNECT_ENDED)

    def _raise_if_ended(self, attempt: object) -> None:
        if self._connect_attempt is not attempt:
            raise ConnectEndedError(default_fault_message(TRANSPORT_DISCONNECTED))

    async def _close_platform_transport(self) -> None:
        """Close the platform transport and forget it. A close that fails is
        the caller's failure (the quiet form below is not)."""
        if self.transport:
            await self.transport.close()
            self.transport = None

    async def _close_transport_quietly(self, failure_note: str = "") -> None:
        """Forget the platform transport, then close it. A close that fails
        is nobody's failure; ``failure_note`` is what the debug log says
        about it (nothing, by default)."""
        transport, self.transport = self.transport, None
        if transport is None:
            return
        try:
            await transport.close()
        except Exception:
            if failure_note:
                log.debug(f"[{self.device_id}] {failure_note}", exc_info=True)

    async def _close_session_quietly(
        self, failure_note: str = "Closing an ended connect's session",
    ) -> None:
        """Close a driver-owned session; a close that fails is nobody's
        failure, and ``failure_note`` is what the debug log says about it.

        The default is an ended connect's: one may have built a session after
        the disconnect that ended it had closed it, and the disconnect may
        still be closing it too, so a second close that fails is not this
        connect's failure."""
        try:
            await self._close_session()
        except Exception:
            log.debug(f"[{self.device_id}] {failure_note}", exc_info=True)

    async def _report_connection_down(self) -> None:
        """Say the connection came down. A held one was never announced, so
        nothing else heard it come up and nothing is emitted: the reconnect
        loop waiting on it is told, and tries again. Any other emits
        ``device.disconnected.<id>``; a bus that refuses it fails nothing."""
        if self._held:
            self._held = False
            self._settle_answer(False)
            return
        try:
            await self.events.emit(f"device.disconnected.{self.device_id}")
        except Exception:
            log.debug(
                f"[{self.device_id}] Failed to emit disconnect event",
                exc_info=True,
            )

    async def _unwind_ended_connect(self) -> None:
        """Take down what an ended connect started after the disconnect that
        ended it had already cleaned up."""
        self._stop_health_loop()
        await self._stop_push()
        await self.stop_polling()
        await self._close_transport_quietly()
        await self._close_session_quietly()
        self._connected = False

    async def _end_if_ended(self, attempt: object) -> None:
        """Past the declare: stop here if a disconnect ended this connect."""
        if self._connect_attempt is not attempt:
            await self._unwind_ended_connect()
            raise ConnectEndedError(default_fault_message(TRANSPORT_DISCONNECTED))

    async def _connect_once(self, attempt: object) -> None:
        """The body of :meth:`connect`, run under its lock: the stages its
        docstring lists, in order, one method each."""
        hold = await self._connect_clean_slate()
        transport_type = self._effective_transport()
        if transport_type == "bridge":
            await self._connect_bridge_routed()
            return
        self._connect_refusal()
        await self._pre_connect()
        self._raise_if_ended(attempt)
        await self._connect_establish(attempt, transport_type)
        await self._connect_handshake(attempt, hold, transport_type)
        # Push notifications (DRIVER_INFO["push"]): subscribe as soon as the
        # session is up — BEFORE any subclass connect() stage runs on_connect
        # arming commands, so the listener never misses the first frame a
        # freshly-armed device sends. Failure is non-fatal (logged inside);
        # polling still covers the device.
        await self._start_push()
        await self._end_if_ended(attempt)
        await self._connect_initial_sync(attempt)
        await self._end_if_ended(attempt)
        await self._connect_start_watching(attempt)

    async def _connect_clean_slate(self) -> bool:
        """Stage 1: nothing from a previous attempt carries into this one.
        Returns whether this attempt asks to be held."""
        # So a previous failure's cause can't be misattributed to this one by
        # the fault classifier.
        self._last_transport_error = ""
        self._last_fault = None
        # A poll from before this attempt says nothing about the state now.
        self._connect_began_at = time.monotonic()
        self._push_receives = 0
        # Only the automatic reconnect asks for a held connection, and only
        # for this attempt (every other door is a fresh one).
        hold = self._hold_next_connect
        self._hold_next_connect = False
        self._held = False
        self._settle_answer(False)
        # A reconnect attempt may arrive with a stale push subscription if the
        # async cleanup hasn't run yet; drop it so we never hold two. Same for
        # a driver-owned session (an httpx client, a secondary socket) left
        # behind by a previous attempt.
        await self._stop_push()
        await self._close_session()
        if self.transport:
            try:
                await self.transport.close()
            except Exception:
                pass
            self.transport = None
        return hold

    async def _connect_bridge_routed(self) -> None:
        """A bridge-routed device (e.g. an IR device bound to a bridge's
        emitter port) has no transport of its own — it emits through the live
        bridge instance (see emit_via_bridge). Its liveness mirrors the bound
        bridge: online iff the bridge is currently online. The DeviceManager
        keeps this in sync as the bridge connects/disconnects; here it is
        seeded from the bridge's current state, so a device added after its
        bridge is already up starts online (and one whose bridge is down
        starts offline, with a bridge_offline reason set by the DeviceManager).
        """
        self._bridge_routed = True
        bridge_id = self.config.get("bridge")
        bridge_port = self.config.get("bridge_port")
        online = bool(
            bridge_id
            and bridge_port
            and self.state.get(f"device.{bridge_id}.connected")
        )
        self._connected = online
        self.set_state("connected", online)
        if online:
            await self.events.emit(f"device.connected.{self.device_id}")
            log.info(
                f"[{self.device_id}] Connected (bridge-routed via {bridge_id})"
            )
        else:
            log.info(
                f"[{self.device_id}] Bridge-routed; bound bridge "
                f"{bridge_id or '(none)'} is offline or unbound"
            )

    def _connect_refusal(self) -> None:
        """Refuse a device that cannot connect as configured, before any
        transport or driver-owned session is built. Every driver passes
        through here; a bridge-routed device does not (it sends through its
        bridge's own connection and signs in to nothing)."""
        # A bridge with no port chosen leaves the binding unresolved, so the
        # device would dial its own transport with nothing to dial, and the
        # card asked for an IP address its Edit Device form does not show.
        if bridge_port_missing(self.config):
            raise ConnectionFaultError(bridge_port_missing_message(), code=INVALID_CONFIG)
        # An empty address dials this computer, and the card then blamed a
        # refused port on a device nobody had given an address.
        if self.address_missing():
            raise ConnectionFaultError(no_address_message(), code=INVALID_CONFIG)
        # A setting still holding the "saved on this system" marker had no
        # saved login to fill it (core/device_logins.py), so the device is
        # never sent the marker as its login.
        if self.login_missing():
            raise ConnectionFaultError(code=LOGIN_MISSING)

    async def _connect_establish(self, attempt: object, transport_type: str) -> None:
        """Stages 3 and 4: build the transport (or driver-owned session), then
        check the device is there.

        From the moment the transport exists until the handshake settles, a
        drop of this connection is this connect's to report (_run_handshake).
        Ended by another door while this ran, the disconnect that ended it
        found neither the transport nor the session to close, so they are
        closed here; a cancel is left to whoever cancelled.
        """
        dropped: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._connect_drop = dropped
        try:
            await self._create_transport(transport_type)
            self._raise_if_ended(attempt)
            await self._connect_verify(attempt, transport_type)
            self._raise_if_ended(attempt)
        except BaseException as exc:
            if self._connect_drop is dropped:
                self._connect_drop = None
            if (
                self._connect_attempt is not attempt
                and not isinstance(exc, asyncio.CancelledError)
            ):
                await self._close_transport_quietly()
                await self._close_session_quietly()
                _raise_ended(exc)
            raise

    async def _connect_verify(self, attempt: object, transport_type: str) -> None:
        """Stage 4: a connectionless transport (OSC, HTTP) checks the remote
        host is actually reachable before the device may report connected.

        TCP and serial validate during open/create. UDP is genuinely
        connectionless and has no transport-level probe: a UDP device is
        noticed going away by its polls drawing no replies (the poll loop's
        silence check, for a driver that reads replies; an awaited poll that
        raises does the same) or by a liveness probe (a YAML `liveness:` block
        / a Python override of _liveness_probe). A UDP driver with none of
        those reads connected against a dead host forever.

        ``verify_timeout: 0`` in config skips the check. It is skipped too
        while the device's state holds polling back (_connect_check_held).
        """
        verify_timeout = self.config.get("verify_timeout", 3.0)
        if not verify_timeout > 0:
            return
        if not hasattr(self.transport, "verify"):
            return
        if self._connect_check_held():
            return
        if await self.transport.verify(timeout=verify_timeout):
            return
        self._raise_if_ended(attempt)
        # Retain the transport's underlying cause (refused / timeout) before
        # tearing it down: on its own the raise below says "not responding",
        # which the classifier would read as no_response. A transport that
        # keeps the exception (HTTP) passes it on as this one's cause, since a
        # refusal can live in its chain alone.
        cause = getattr(self.transport, "verify_error", None)
        if not isinstance(cause, BaseException):
            cause = None
        self._stash_transport_error()
        await self._close_platform_transport()
        raise _verify_failure_error(
            self._last_transport_error, cause,
            self.config.get("host", "?"), self.config.get("port", "?"),
            transport_type,
        ) from cause

    async def _connect_handshake(
        self, attempt: object, hold: bool, transport_type: str,
    ) -> None:
        """Stage 5: the handshake, then the declare.

        Device-specific session setup (e.g. an SSH/CLI driver entering
        privileged mode and disabling output paging) runs before the device
        is reported connected, so the first poll sees a ready session. A raise
        here aborts the connection, and what the connect built is closed.
        """
        try:
            await self._run_handshake()
            self._raise_if_ended(attempt)
            await self._connect_declare(hold, transport_type)
        except Exception as exc:
            await self._connect_handshake_failed(attempt, exc)
            raise

    async def _connect_handshake_failed(self, attempt: object, exc: Exception) -> None:
        """Close what a failed handshake or declare left. An attempt another
        door ended raises its ConnectEndedError here and reports nothing else;
        any other failure returns, for its caller to raise."""
        if self._connect_attempt is not attempt:
            await self._close_transport_quietly()
            await self._close_session_quietly()
            self._connected = False
            _raise_ended(exc)
        # The transport's last error first (e.g. an SSH auth failure surfaces
        # in ssh's log on the transport, not in this exception).
        self._stash_transport_error()
        await self._close_platform_transport()
        await self._close_session()
        self._connected = False

    def _connect_is_held(self, hold: bool) -> bool:
        """Whether this connection comes up held: the reconnect asked for it,
        and a liveness probe or the poll loop's silence check can see the
        device answer."""
        if not hold:
            return False
        return self._health_enabled() or self._watches_for_silence()

    async def _connect_declare(self, hold: bool, transport_type: str) -> None:
        """The canonical declare: the internal flag, the ``connected`` state
        key, then the ``device.connected.<id>`` event.

        A held connection gets the flag alone. Sends, polling and the
        liveness probe run as usual; ``connected`` and the event wait for the
        first poll or probe the device answers (_announce_held).
        """
        self._connected = True
        if self._connect_is_held(hold):
            self._held = True
            self._answer_waiter = asyncio.get_running_loop().create_future()
            log.info(
                f"[{self.device_id}] Connected via {transport_type}; the "
                f"device stopped answering last time, so it stays offline "
                f"until it answers"
            )
            return
        self._dropped_for_silence = False
        self._silence_verdict = None
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")
        log.info(f"[{self.device_id}] Connected via {transport_type}")

    async def _connect_initial_sync(self, attempt: object) -> None:
        """Stage 6: the driver's initial sync (identity reads, roster
        registration, keep-alive loops), after ``connected`` is declared and
        push is armed but BEFORE polling starts, so the first poll cycle sees
        a fully-initialized device. A driver that prefers a
        degraded-but-connected outcome catches inside its override."""
        try:
            await self._initial_sync()
        except Exception as exc:
            await self._connect_sync_failed(attempt, exc)
            raise

    async def _connect_sync_failed(self, attempt: object, exc: Exception) -> None:
        """Tear the whole connection back down after a failed initial sync.
        The device was briefly connected, so the disconnect event is emitted
        for symmetry. An attempt another door ended raises its
        ConnectEndedError here, since the disconnect that ended it already
        said so; any other failure returns, for its caller to raise."""
        if self._connect_attempt is not attempt:
            await self._unwind_ended_connect()
            _raise_ended(exc)
        self._stash_transport_error()
        # The disconnect event emitted below reaches the device manager's
        # handler with no exception to read, so a typed cause (a refused
        # credential) is kept where it looks, or the handler would start a
        # reconnect that the caller of connect() is about to hold.
        typed = typed_fault_from_exc(
            exc, host=self.config.get("host", ""), port=self.config.get("port"),
        )
        if typed is not None:
            self._last_fault = typed
        await self._stop_push()
        await self._close_transport_quietly()
        await self._close_session()
        self._connected = False
        self.set_state("connected", False)
        await self._report_connection_down()

    async def _connect_start_watching(self, attempt: object) -> None:
        """The tail: polling if configured, then the liveness watchdog if the
        driver supplies a probe.

        The watchdog starts after ``connected`` is reported and sleeps a full
        interval before its first probe, so subclass connect() stages that
        run after super().connect() (logins, subscriptions) aren't raced. If
        such a stage fails and tears the transport down, the loop notices the
        dead transport and exits on its own.
        """
        poll_interval = self.config.get("poll_interval", 0)
        if poll_interval > 0:
            await self.start_polling(poll_interval)
            await self._end_if_ended(attempt)
        if self._health_enabled():
            self._start_health_loop()

    async def disconnect(self) -> None:
        """
        Gracefully close the connection.

        Stops polling, closes transport, and updates state. A connect still
        running is ended first (:meth:`connect`).
        Override for custom disconnect logic.
        """
        self._end_connect_in_flight()
        self._stop_health_loop()
        await self._stop_push()
        await self.stop_polling()
        await self._close_platform_transport()
        await self._close_session()
        self._connected = False
        # A deliberate disconnect ends any hold: whatever connects next is a
        # fresh attempt, announced as soon as it is up.
        self._held = False
        self._dropped_for_silence = False
        self._silence_verdict = None
        self._unread_replies_hold = False
        self._settle_answer(False)
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")
        log.info(f"[{self.device_id}] Disconnected")

    async def _run_handshake(self) -> None:
        """Run :meth:`_post_connect`, ending it the moment the connection drops.

        A handshake waits for the device (a banner, a prompt, a login
        verdict). When the connection closes under it, waiting on would only
        run out its own timeout and fail with its own complaint, the cause
        long gone with the transport, while the drop's handler started a
        retry beside a connect that was still running. So the drop wins: the
        handshake is cancelled and the connect fails at once with what the
        transport said (:meth:`_dropped_error`). A handshake that had already
        failed with a typed fault (a refused login) keeps it. A drop that
        lands as the handshake finishes still fails the connect, so a device
        is never declared connected on a connection already gone.

        ``_connect_drop`` is cleared before this returns or raises, so a drop
        after it (a held connection, a running session) takes the usual path.
        """
        dropped = self._connect_drop
        if dropped is None:
            await self._post_connect()
            return
        handshake: asyncio.Future[None] | None = None
        try:
            if not dropped.done():
                handshake = asyncio.ensure_future(self._post_connect())
                await asyncio.wait(
                    (handshake, dropped), return_when=asyncio.FIRST_COMPLETED,
                )
                if not dropped.done():
                    handshake.result()  # its own outcome: returns or raises
                    return
            raise self._handshake_cut_short(dropped, handshake)
        finally:
            # Only ever this connect's own: connects run one at a time.
            if self._connect_drop is dropped:
                self._connect_drop = None
            await _stop_handshake(handshake)

    def _handshake_cut_short(
        self, dropped: asyncio.Future[object], handshake: asyncio.Future[None] | None,
    ) -> BaseException:
        """What a connect raises when its connection dropped, or another door
        ended it, before the handshake finished: ended, a ConnectEndedError;
        a handshake that had already failed with a typed fault, that fault;
        else why the connection closed (:meth:`_dropped_error`)."""
        if dropped.result() is _CONNECT_ENDED:
            return ConnectEndedError(default_fault_message(TRANSPORT_DISCONNECTED))
        typed = _typed_handshake_failure(handshake)
        if typed is not None:
            return typed
        return self._dropped_error()

    def _dropped_error(self) -> ConnectionError:
        """Why the connection closed under a connect, in the transport's words.

        A fault the driver stashed as it dropped the link
        (:meth:`_force_disconnect`, a refused login read by its reader) is
        raised typed, so it stays a refused login and the retries pause. Else
        the transport's last line, or the taxonomy's own sentence when the
        device closed without a word (a plain goodbye leaves none).
        """
        fault = self._last_fault
        if fault is not None:
            return ConnectionFaultError(fault.message, code=fault.code)
        said = ""
        if self.transport is not None:
            said = getattr(self.transport, "last_error", "") or ""
        lines = [line.strip() for line in said.splitlines() if line.strip()]
        if lines:
            return ConnectionError(lines[-1])
        return ConnectionError(default_fault_message(TRANSPORT_DISCONNECTED))

    def _handle_transport_disconnect(self) -> None:
        """
        Standard disconnect handler for transport callbacks.

        Sets connected state to False, then schedules cleanup (stop polling,
        close the now-dead transport, emit the disconnect event). Override for
        custom disconnect behavior.

        A drop while connect() is still building or handshaking ends that
        connect instead (_run_handshake): it closes what it opened and its
        failure is the one report, so nothing is scheduled here and no
        disconnect is emitted for a connection that never came up.
        """
        self._connected = False
        self.set_state("connected", False)
        dropped = self._connect_drop
        if dropped is not None:
            if not dropped.done():
                dropped.set_result(None)
            return
        if self._held:
            log.info(f"[{self.device_id}] Connection closed before the device answered")
        else:
            log.warning(f"[{self.device_id}] Connection lost")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning(
                f"[{self.device_id}] No event loop during disconnect — "
                f"polling/transport may not be cleaned up"
            )
            return
        # Keep a strong reference until the task finishes — a bare create_task
        # is only weakly held and can be GC'd before it runs, orphaning the
        # poll loop or skipping the disconnect event.
        task = loop.create_task(self._on_disconnect_cleanup())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _on_disconnect_cleanup(self) -> None:
        """Stop polling, close the dead transport, and emit the disconnect event.

        Closing the transport here (not only on the graceful ``disconnect()``
        path) releases the socket / bound UDP port / pooled HTTP client
        immediately instead of holding it for the whole reconnect-backoff
        window (up to ~1h). The live ref is nulled so auto-reconnect rebuilds
        it; the disconnect event is emitted last so the reconnect's connect()
        doesn't race the close. Closing is safe in the watchdog path too — a
        transport's close() sets its own connected=False before tearing down,
        so it never re-enters this handler.
        """
        self._stop_health_loop()
        await self._stop_push()
        await self.stop_polling()
        # Capture the transport's last error before nulling it, so the
        # DeviceManager can classify the offline reason from the event handler
        # (which runs after this teardown).
        self._stash_transport_error()
        await self._close_transport_quietly("Error closing transport on disconnect")
        await self._close_session_quietly("Error closing driver session on disconnect")
        await self._report_connection_down()
