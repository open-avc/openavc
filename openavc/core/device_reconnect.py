"""Retrying a device that dropped: the part of DeviceManager that reconnects it.

The rule worth knowing before changing it: a device that is only absent is
retried for as long as it is in the project, with no attempt ceiling, because
the address of equipment on a wall is a permanent fact about the room. The wait
is a short ramp, then the steady interval (the project's, else system.json's,
clamped), jittered. Backoff is for a device that connects and drops again
(flapping), never for absence; inside a commanded-restart window the retry is
faster, and a device asleep on purpose is never escalated. Only a fault a human
has to clear stops the retry: a refused login, a missing address or a missing
login at once (more logins can trip a lockout), the other permanent faults
after one more try.

Each attempt stops polling, checks cheaply first for the transports whose
connect is costly, then dials. A connection that drops during start-up is a
failed attempt, because the drop's own handler leaves the retry to the loop.

Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
from urllib.parse import urlsplit

from openavc.core.connection_fault import (
    AUTH_FAILED,
    INVALID_CONFIG,
    LOGIN_MISSING,
    NO_RESPONSE,
    TRANSPORT_DISCONNECTED,
    UNREACHABLE,
    ConnectionFaultError,
    classify_connection_fault,
    held_connection_fault,
    is_permanent_fault,
)
from openavc.core.device_away import connection_descriptor
from openavc.core.event_bus import detach_emit_chain
from openavc.drivers.base import ConnectEndedError
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.device_sleep import SleepBook
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver

# The device manager's logger: utils/log_buffer.py files a line under the
# device category by this name, and the device page shows that category.
log = get_logger("openavc.core.device_manager")


# How many reconnect attempts a permanent fault gets before the loop stops.
# One retry past the first classification: enough to shrug off a device that
# was mid-reboot when we read its certificate or host key, few enough that a
# genuinely misconfigured device stops churning almost immediately. auth_failed
# is stricter still (zero retries — see _pause_reconnect_for_auth).
_MAX_PERMANENT_FAULT_ATTEMPTS = 2

# --- Retry policy for a device that is simply ABSENT -----------------------
# A transient fault gets retried for as long as the device is in the project.
# There is deliberately no attempt ceiling: the address of a projector on a
# wall is a permanent fact about the room, so "we gave up an hour ago" is never
# the right answer to someone plugging the cable back in the next morning. Only
# the permanent set above ever stops, because only those need a human.
#
# The ramp is short and then flat. Its whole job is to catch a blip in a second
# or two; past that, the steady interval is what a returning device waits for,
# and slower does not help anyone.
_RECONNECT_RAMP_SECONDS = (1.0, 2.0)

# Steady-state seconds between attempts. Two things set this floor, and neither
# is CPU. Repeated SYNs to a non-responding address, continuing indefinitely,
# is the signature of a slow horizontal port scan, and docs/it-network-guide.md
# tells IT departments we don't probe their network — so this is the number
# that has to stay defensible to somebody's IDS. The unreachable path also ARPs,
# which is broadcast, and now runs around the clock. Overridable per-site via
# system.json ("devices" -> "reconnect_interval_seconds").
_RECONNECT_INTERVAL_DEFAULT = 5.0

# Spread simultaneous retries so a rack full of devices doesn't redial in
# lockstep after a switch reboot — the one moment when they are ALL offline.
_RECONNECT_JITTER = 0.2  # +/- 20%

# Transports where the real connect is expensive enough to be worth gating
# behind a cheap reachability check first. ssh spawns an `ssh` subprocess and
# negotiates keys; mqtt does a TLS handshake; http can carry an auth exchange.
# Everything else (tcp, serial, udp, osc) connects about as cheaply as any
# probe would, so probing first would just do the work twice — and on a
# single-session device, an extra socket is not free.
_PROBE_FIRST_TRANSPORTS = frozenset({"ssh", "mqtt", "http"})
# Long enough for Windows to report a refused port: it sends the SYN twice more
# after the first reset, so a refusal arrives about 2 s after the dial (2.00 s
# measured, on loopback and on this computer's LAN addresses alike). A shorter
# wait reads every closed port there as silence, and the card as unreachable.
_PROBE_TIMEOUT = 3.0

# A reconnect that succeeds and then drops again inside this window is
# flapping, not recovering. THIS is what backoff is for: an absent device costs
# one cheap syscall to check, but a device that connects and dies on a loop can
# genuinely hurt — so the escalating delay lives here instead of on absence,
# which is where it used to be and where it did nothing but hide recoveries.
_FLAP_WINDOW_SECONDS = 30.0
_MAX_FLAP_ESCALATIONS = 4  # 5s -> 10 -> 20 -> 40 -> 80, then holds

# Log the first few attempts, then go quiet apart from a periodic heartbeat.
# Retrying forever at INFO would be ~17k lines a day for ONE offline device,
# into both the ring buffer and the log file; the recovery is always logged.
_RECONNECT_LOG_FIRST = 3
_RECONNECT_LOG_EVERY = 60  # roughly every 5 minutes at the default interval

# How often to re-dial a device that is inside a commanded-restart window.
# Bounded by the site's own interval where that is already tighter (see
# _reconnect_delay for why this one case earns a shorter wait at all).
_RESTART_RECONNECT_INTERVAL = 2.0


@dataclass
class _ReconnectRun:
    """One reconnect loop's counts, kept from one attempt to the next."""

    immediate: bool = False  # the first attempt goes without waiting
    attempt: int = 0  # 0-indexed: the attempt now being made
    # Permanent faults in a row; see _MAX_PERMANENT_FAULT_ATTEMPTS.
    permanent_attempts: int = 0
    noisy: bool = True  # whether this attempt is said in the log


def _dial_target(host: Any, port: Any) -> tuple[Any, Any]:
    """The name and port the reconnect check dials for a device's host.

    A host field may hold a URL (some drivers ask for a service's address
    that way): the check dials its name, on its own port or the scheme's,
    as the client will. Any other host is dialed as it is.
    """
    text = str(host or "").strip()
    if "://" not in text:
        return host, port
    parts = urlsplit(text)
    try:
        own_port = parts.port
    except ValueError:
        own_port = None
    scheme_port = {"https": 443, "http": 80}.get(parts.scheme.lower())
    return parts.hostname or "", own_port or scheme_port or port


def _probe_fault(
    exc: BaseException, host: str, port: Any, transport: str,
) -> ConnectionFaultError:
    """Why the reconnect check's dial failed, as the device card says it.

    Worded by the classifier from this dial alone: the device's stashed error
    is an earlier attempt's, and a port refused minutes ago says nothing about
    a device that is now unplugged. The prefix is the one TCPTransport puts on
    a failed connect, so a timeout reads as a connect timeout; the cause chain
    keeps a refusal, which Windows words in the system's own language. A dial
    the classifier has nothing specific for stays unreachable, as before.
    """
    dial = ConnectionError(f"Failed to connect to {host}:{port}: {exc}")
    dial.__cause__ = exc
    fault = classify_connection_fault(
        last_error=None, exc=dial, host=host, port=port, transport=transport,
    )
    if fault.code in (NO_RESPONSE, TRANSPORT_DISCONNECTED):
        return ConnectionFaultError("", code=UNREACHABLE)
    return ConnectionFaultError(fault.message, code=fault.code)


def _log_task_exception(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in background task: %s", exc, exc_info=exc)


class DeviceReconnectMixin:
    """Retrying a device that dropped, and deciding when to stop for a human.

    Its state is set up by ``_init_reconnect``. From its host (DeviceManager)
    it reads ``state``, ``_devices``, ``installed_equipment`` and
    ``project_reconnect_interval``, and asks ``_simulation_gap``,
    ``_connect_driver`` and ``_apply_pending_settings``. From the away status
    (core/device_away.py) it asks ``_set_offline_reason``,
    ``_clear_offline_reason``, ``_failure_text``, ``restart_seconds_left`` and
    the sleep book.
    """

    state: StateStore
    _devices: dict[str, BaseDriver]
    _sleep: SleepBook
    installed_equipment: Any
    project_reconnect_interval: float | None

    def _init_reconnect(self) -> None:
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        # Flapping bookkeeping — see _FLAP_WINDOW_SECONDS. Lives here rather
        # than in the reconnect loop because the loop EXITS on success: a
        # device that connects and drops again starts a brand new loop, so a
        # counter inside one could never see the pattern it exists to catch.
        self._last_connect_at: dict[str, float] = {}
        self._flap_counts: dict[str, int] = {}

    def _forget_flap_history(self, device_id: str) -> None:
        """Drop a removed device's flap count and when it last connected."""
        self._last_connect_at.pop(device_id, None)
        self._flap_counts.pop(device_id, None)

    async def _cancel_all_reconnects(self) -> None:
        """Cancel every running reconnect, waiting for each loop to finish."""
        for device_id in list(self._reconnect_tasks.keys()):
            await self._cancel_reconnect(device_id)

    def _pause_reconnect_for_auth(self, device_id: str) -> None:
        """Hold auto-reconnect after a credential rejection.

        A wrong password can't heal by retrying — the same login just fails
        again, and devices with brute-force lockouts (Crestron and others
        block the offending source IP after a handful of failures) punish
        every extra attempt, locking the legitimate user out too. Policy:
        one attempt per user action. The initial connect (which may carry
        driver-default credentials worth trying) counts as that attempt;
        after an auth_failed classification we stop and wait. A connected
        device whose credential is refused mid-session has had its attempt
        too (the driver's login on the live connection), so its drop pauses
        rather than reconnecting into the same refusal. Editing the
        device re-adds it (fresh attempt), and the Reconnect button forces
        one more try. ``reconnect_failed`` is set so the UI shows the
        not-retrying state.
        """
        if self.installed_equipment and self.installed_equipment.owns(device_id):
            log.warning(
                f"[{device_id}] Product hardware authentication failed. "
                "Automatic reconnect is paused. Check this product's OpenAVC "
                "status, then press Reconnect."
            )
        else:
            log.warning(
                f"[{device_id}] Authentication failed — auto-reconnect paused so "
                f"repeated logins can't trip the device's lockout. Update the "
                f"device's credentials, or press Reconnect to try again."
            )
        self.state.set(
            f"device.{device_id}.reconnect_failed", True, source="device_manager"
        )

    def _paused_for_refused_login(self, device_id: str, code: str) -> bool:
        """Pause the retry when ``code`` says the device refused the login.

        The one place that rule lives, for every path that has just
        classified a failure (a first connect, a drop, a reconnect attempt,
        the Reconnect button, a resume after a pause): see :meth:`_pause_reconnect_for_auth` for why a
        refused login is never retried on its own. True when it paused.
        """
        if code != AUTH_FAILED:
            return False
        self._pause_reconnect_for_auth(device_id)
        return True

    def _stop_reconnect_for_permanent_fault(self, device_id: str, code: str) -> None:
        """Stop auto-reconnect after a fault only a human can clear.

        A rejected host key, an untrusted certificate, bad connection
        settings, or a missing client binary all fail identically on every
        retry — the device card already names the cause and the fix, so
        continuing just churns. The reason and
        detail keys are left as classified (they carry the actionable
        wording); ``reconnect_failed`` tells the UI we've stopped.
        """
        log.warning(
            f"[{device_id}] Stopped reconnecting: '{code}' can't resolve by "
            f"retrying. Fix the cause shown on the device card, then press "
            f"Reconnect (editing the device also retries)."
        )
        self.state.set(
            f"device.{device_id}.reconnect_failed", True, source="device_manager"
        )

    def _start_reconnect(self, device_id: str) -> None:
        """Start a background reconnect loop for a device.

        A device with no address to dial gets none, and neither does one whose
        login is not saved on this system: retrying cannot supply either, so it
        stops at once, the way a rejected login does, rather than after the
        attempts a device-reported fault is allowed. The card already says what
        to set; editing the device or Reconnect retries.
        """
        if self._simulation_gap(device_id) is not None:
            return
        if device_id in self._reconnect_tasks:
            return  # Already reconnecting
        driver = self._devices.get(device_id)
        if driver is not None and driver.address_missing():
            self._stop_reconnect_for_permanent_fault(device_id, INVALID_CONFIG)
            return
        if driver is not None and driver.login_missing():
            self._stop_reconnect_for_permanent_fault(device_id, LOGIN_MISSING)
            return
        self._note_flap(device_id)
        task = asyncio.create_task(self._reconnect_loop(device_id))
        task.add_done_callback(_log_task_exception)
        self._reconnect_tasks[device_id] = task

    def _note_flap(self, device_id: str) -> None:
        """Count this drop as a flap if the device only just came up.

        Called as a reconnect loop starts, which is the one place that sees
        both halves of the pattern: how long ago we last got this device
        online, and the fact that it has fallen over again.

        A drop inside a commanded-restart window is the restart we asked for,
        however soon after connecting it comes, so it is not counted or logged
        as a flap (``_reconnect_delay`` skips the escalation for the same
        reason). The count is left as it stood: the drop says nothing either
        way about whether the device stays up, and a count it added would
        outlive the window and double the wait for a device still booting.
        """
        if self.restart_seconds_left(device_id) is not None:
            return
        # Same for a device we put to sleep: the drop is the sleep.
        if self._sleep.get(device_id) is not None:
            return
        last = self._last_connect_at.get(device_id)
        if last is not None and (time.monotonic() - last) < _FLAP_WINDOW_SECONDS:
            self._flap_counts[device_id] = self._flap_counts.get(device_id, 0) + 1
            log.warning(
                "[%s] Dropped again %.1fs after connecting — backing off "
                "(flap %d)", device_id, time.monotonic() - last,
                self._flap_counts[device_id],
            )
        else:
            self._flap_counts.pop(device_id, None)

    def _reconnect_delay(self, device_id: str, attempt: int) -> float:
        """Seconds to wait before ``attempt`` (0-indexed), jittered.

        Short ramp to catch a blip, then the steady interval forever. The only
        thing that stretches it is flapping, never absence.

        Inside a commanded-restart window it is tighter, and that is the one
        case where a tighter cadence is defensible rather than just keener: the
        steady interval is what it is because continuous SYNs to an address
        that is not answering look like a port scan to somebody's IDS, and a
        device we just told to reboot is one we have positive reason to expect
        back within a known handful of seconds. It is also the moment a person
        is standing at a panel watching for it. Flap escalation is skipped for
        the same reason -- a device that went away because it was asked to has
        not flapped, and doubling the wait for it would punish the room for
        using the button.
        """
        if self.restart_seconds_left(device_id) is not None:
            base = min(_RESTART_RECONNECT_INTERVAL, self._reconnect_interval())
            return base * (1.0 + random.uniform(-_RECONNECT_JITTER, _RECONNECT_JITTER))
        if attempt < len(_RECONNECT_RAMP_SECONDS):
            base = _RECONNECT_RAMP_SECONDS[attempt]
        else:
            base = self._reconnect_interval()
        # A device asleep on purpose is retried at the steady interval, never
        # escalated: it has not flapped (a display woken by its own remote is
        # found again within one interval).
        sleep = self._sleep.get(device_id)
        if sleep is not None and sleep.asleep:
            return base * (1.0 + random.uniform(-_RECONNECT_JITTER, _RECONNECT_JITTER))
        flaps = min(self._flap_counts.get(device_id, 0), _MAX_FLAP_ESCALATIONS)
        base *= 2 ** flaps
        return base * (1.0 + random.uniform(-_RECONNECT_JITTER, _RECONNECT_JITTER))

    def _reconnect_interval(self) -> float:
        """The configured steady-state retry interval, in seconds.

        The PROJECT wins where it says anything, because the rate of
        connection attempts an IT department sees is a property of the site
        rather than of one box -- so a customer sets it once and deploys it to
        every panel. A project that says nothing (the normal case) defers to
        this instance's own system.json, which is where the setting lived
        before and still works for a box configured by hand.
        """
        if self.project_reconnect_interval is not None:
            try:
                return min(max(float(self.project_reconnect_interval), 1.0), 300.0)
            except (TypeError, ValueError):
                pass
        try:
            from openavc.system_config import get_system_config

            value = float(
                get_system_config().get(
                    "devices", "reconnect_interval_seconds",
                    _RECONNECT_INTERVAL_DEFAULT,
                )
            )
        except Exception:
            return _RECONNECT_INTERVAL_DEFAULT
        # A zero or negative interval would spin; an absurdly large one would
        # silently recreate the problem this whole loop exists to fix.
        return min(max(value, 1.0), 300.0)

    async def _probe_failure(
        self, device_id: str, driver: BaseDriver,
    ) -> ConnectionFaultError | None:
        """Cheap "is it back?" check, for transports whose connect is costly.

        Returns None when the expensive connect is worth attempting — which
        includes every case where we can't cheaply tell, so a probe failure
        never becomes the reason a device stays offline. Otherwise returns
        why the port did not answer, typed, for ``_set_offline_reason``.
        """
        host, port, transport = connection_descriptor(driver)
        if (transport or "").lower() not in _PROBE_FIRST_TRANSPORTS:
            return None
        host, port = _dial_target(host, port)
        if not host or port in (None, "", 0):
            return None
        # From the adapter the connect itself will use (Reach devices through).
        from openavc.transport.endpoint import control_bind_address

        try:
            bind = control_bind_address(host)
        except ConnectionFaultError:
            # The chosen adapter has no address. The connect decides: every
            # connection the platform builds refuses with the same sentence
            # before it dials, and a driver may keep a connection off the
            # adapter (a cloud service is not equipment on site).
            return None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host, int(port), local_addr=(bind, 0) if bind else None,
                ),
                timeout=_PROBE_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError, ValueError) as exc:
            return _probe_fault(exc, host, port, transport)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return None

    def kick_reconnect(self, device_id: str) -> bool:
        """Retry this device now instead of waiting out the interval.

        The customer standing at the panel pressing a button on a dead device
        is the strongest "I want this back" signal the system gets, and it used
        to be thrown away. Cancels the sleeping loop and starts a fresh one, so
        the next attempt is immediate. Does nothing for a device that isn't
        retrying (connected, disabled, or deliberately stopped) — a permanent
        fault still needs its human. Returns True if an attempt was triggered.
        """
        if device_id not in self._devices:
            return False
        if device_id not in self._reconnect_tasks:
            return False
        task = self._reconnect_tasks.pop(device_id)
        task.cancel()
        fresh = asyncio.create_task(self._reconnect_loop(device_id, immediate=True))
        fresh.add_done_callback(_log_task_exception)
        self._reconnect_tasks[device_id] = fresh
        return True

    async def _cancel_reconnect(self, device_id: str) -> None:
        """Cancel a running reconnect task and wait for it to finish."""
        task = self._reconnect_tasks.pop(device_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _reconnect_loop(self, device_id: str, immediate: bool = False) -> None:
        """Reconnect a disconnected device, for as long as it takes.

        A short ramp (1s, 2s) then the steady interval, jittered, with **no
        attempt ceiling** — see the retry-policy constants above for why the
        old ~1 hour cap was the wrong answer for fixed AV installations. The
        only exits are success, the device going away, a permanent fault, and
        cancellation. Success is a driver still connected once its start-up
        has run: a connection that drops during start-up is a failed attempt,
        because the drop's own handler leaves the retry to this loop.

        ``immediate`` skips the first wait: used by ``kick_reconnect`` when a
        human has just asked for this device by pressing something.
        """
        # This loop is spawned from inside the disconnect emit chain but outlives
        # it, so it must not keep being charged to it — see detach_emit_chain.
        detach_emit_chain()
        run = _ReconnectRun(immediate=immediate)
        try:
            while not await self._reconnect_attempt(device_id, run):
                run.attempt += 1
        except asyncio.CancelledError:
            log.debug(f"[{device_id}] Reconnect cancelled")
        finally:
            # kick_reconnect replaces the entry before cancelling this task, so
            # only clear it if it is still ours — otherwise the cancelled loop
            # would delete the fresh one on its way out and leave the device
            # with no retry at all.
            if self._reconnect_tasks.get(device_id) is asyncio.current_task():
                self._reconnect_tasks.pop(device_id, None)

    async def _reconnect_attempt(self, device_id: str, run: _ReconnectRun) -> bool:
        """One turn of the reconnect loop: wait, check, then dial. True when
        the loop is finished (connected, gone, or stopped for a human)."""
        # Check device still exists before each attempt
        driver = self._devices.get(device_id)
        if driver is None:
            log.debug(f"[{device_id}] Device removed, stopping reconnect")
            return True
        if self._stopped_for_simulation(device_id, driver):
            return True
        self._begin_attempt(device_id, run)
        await self._wait_for_attempt(device_id, run)
        # Re-check after sleep — device may have been removed
        if device_id not in self._devices:
            log.debug(f"[{device_id}] Device removed during wait, stopping reconnect")
            return True
        await self._stop_polling_for_attempt(device_id, driver)
        if self._stopped_for_simulation(device_id, driver):
            return True
        # Cheap reachability first for the transports whose connect is
        # expensive. A device that is still absent costs one short socket here
        # instead of an ssh subprocess every few seconds.
        failure = await self._probe_failure(device_id, driver)
        if failure is not None:
            # The control port didn't answer, so the expensive connect would
            # only fail slower. Still classify — skipping the attempt must not
            # leave the device card showing a stale reason for a device that is
            # now simply not there — and say what the dial found: a refused
            # port is the service, not the address.
            self._set_offline_reason(device_id, driver, exc=failure)
            return False
        try:
            return await self._dial_attempt(device_id, driver, run)
        except ConnectEndedError:
            # A Reconnect, pause or resume ended this attempt and owns the
            # device now.
            return True
        except Exception as e:
            return self._attempt_failed(device_id, driver, e, run)

    def _stopped_for_simulation(self, device_id: str, driver: BaseDriver) -> bool:
        """A device a running simulation has no simulator for is not dialed:
        its card says why, and the loop ends. True when it ended."""
        if self._simulation_gap(device_id) is None:
            return False
        self._set_offline_reason(device_id, driver)
        return True

    def _begin_attempt(self, device_id: str, run: _ReconnectRun) -> None:
        """Decide whether this attempt is said, and publish its number if so.

        Quiet by construction: the first few attempts are the interesting
        ones, then a heartbeat. This loop can run for days, so a line (and a
        state write, which fans out over the WebSocket to every panel) per
        attempt would be pure noise for a number nothing counts down from any
        more. A device asleep on purpose is silent: nothing is wrong, and it
        may stay asleep all night (one line said it fell asleep).
        """
        attempt = run.attempt
        run.noisy = attempt < _RECONNECT_LOG_FIRST or attempt % _RECONNECT_LOG_EVERY == 0
        sleep = self._sleep.get(device_id)
        if sleep is not None and sleep.asleep:
            run.noisy = False
        if run.noisy:
            self.state.set(
                f"device.{device_id}.reconnect_attempt", attempt + 1,
                source="device_manager",
            )

    async def _wait_for_attempt(self, device_id: str, run: _ReconnectRun) -> None:
        """Wait out the delay before this attempt; an immediate first attempt
        does not wait at all."""
        if run.attempt == 0 and run.immediate:
            return
        delay = self._reconnect_delay(device_id, run.attempt)
        if run.noisy:
            log.info(
                "[%s] Reconnect attempt %d in %.1fs...",
                device_id, run.attempt + 1, delay,
            )
        await asyncio.sleep(delay)

    @staticmethod
    async def _stop_polling_for_attempt(device_id: str, driver: BaseDriver) -> None:
        """Stop polling before anything else touches the transport.

        This comes ahead of the probe on purpose: the probe can skip the rest
        of the attempt, and a poll loop left running against a dead transport
        would then never be stopped at all — which, now that the loop has no
        ceiling, means never.
        """
        try:
            await driver.stop_polling()
        except Exception:
            log.debug(f"[{device_id}] stop_polling failed", exc_info=True)

    async def _dial_attempt(
        self, device_id: str, driver: BaseDriver, run: _ReconnectRun,
    ) -> bool:
        """Dial once. True when the loop is finished: connected and still up
        once its start-up and queued settings have run, or paused for a
        refused login. A connection that drops on the way is a failed attempt.
        """
        self._refresh_usb_serial_port(device_id, driver)
        # A device dropped because it stopped answering (its polls or its
        # liveness probe) is not announced again until it answers:
        # re-announcing it at every reconnect made its card read connected for
        # most of each retry and fired its Connected/Disconnected triggers
        # every cycle. The link still opens, polls and probes as usual.
        if getattr(driver, "_dropped_for_silence", False) is True:
            driver._hold_next_connect = True
        await self._connect_driver(device_id, driver)
        held = getattr(driver, "_held", False) is True
        if held and not await self._held_connection_answered(device_id, driver, run):
            # Closed again before answering. The driver kept why (another
            # silent verdict, or the link failing).
            return self._paused_for_refused_login(
                device_id, self._set_offline_reason(device_id, driver),
            )
        if not driver.get_state("connected"):
            # The connection dropped while the driver was still starting up (a
            # device that accepts one as it boots, then resets it). connect()
            # logs its start-up steps' failures rather than raising, and the
            # drop's own handler saw this loop running and left the retry to
            # it, so returning here would leave the device down with nothing
            # retrying.
            if self._paused_for_refused_login(
                device_id, self._set_offline_reason(device_id, driver),
            ):
                return True
            log.info(f"[{device_id}] The connection dropped while it was starting up; retrying")
            return False
        log.info(f"[{device_id}] Reconnected successfully")
        self._last_connect_at[device_id] = time.monotonic()
        self._clear_offline_reason(device_id)
        self.state.set(f"device.{device_id}.reconnect_attempt", None, source="device_manager")
        await self._apply_pending_settings(device_id)
        if not driver.get_state("connected"):
            # Dropped again while its settings were applied: the same hand-off
            # as above, one await later.
            return self._paused_for_refused_login(
                device_id, self._set_offline_reason(device_id, driver),
            )
        return True

    async def _held_connection_answered(
        self, device_id: str, driver: BaseDriver, run: _ReconnectRun,
    ) -> bool:
        """Wait on a held connection: True once the device answers, False when
        the connection closes first."""
        if run.noisy:
            log.info(
                "[%s] Connected; it stays offline until the device answers",
                device_id,
            )
        # A reason about a connection that has since closed (dropped, refused)
        # no longer holds while this one is open; a silence verdict still does.
        if self.state.get(f"device.{device_id}.offline_reason") != NO_RESPONSE:
            self._set_offline_reason(
                device_id, driver, fault=held_connection_fault(),
            )
        return await driver._wait_for_answer()

    def _attempt_failed(
        self, device_id: str, driver: BaseDriver, exc: Exception, run: _ReconnectRun,
    ) -> bool:
        """After an attempt that raised: classify it, say it, and decide. True
        when the loop stops."""
        # Refine the offline reason from this attempt's failure — the cause
        # can change between attempts (auth vs unreachable).
        code = self._set_offline_reason(device_id, driver, exc=exc)
        if run.noisy:
            log.warning(
                f"[{device_id}] Reconnect failed: {self._failure_text(device_id, exc)}"
            )
        if self._paused_for_refused_login(device_id, code):
            # The device is reachable but rejecting the login. More attempts
            # can only trip its lockout — stop here and wait for new
            # credentials (an unreachable device that comes back with bad
            # creds lands here on the attempt that discovers it).
            return True
        if not is_permanent_fault(code):
            run.permanent_attempts = 0
            return False
        # Host key, TLS trust, connection settings, missing client: a human
        # has to change something. Allow a couple of tries first — a device
        # rebooting mid-scan can briefly present one of these — then stop,
        # since nothing about waiting longer will clear them.
        run.permanent_attempts += 1
        if run.permanent_attempts < _MAX_PERMANENT_FAULT_ATTEMPTS:
            return False
        self._stop_reconnect_for_permanent_fault(device_id, code)
        return True

    def _refresh_usb_serial_port(self, device_id: str, driver) -> None:
        """Re-resolve a usb_serial-bound adapter to its current OS path.

        A replug can move the adapter (ttyUSB0 -> ttyUSB1) while the stored
        path goes stale; the stable USB serial number is the truth, so each
        reconnect attempt follows the adapter instead of redialing the old
        path. No-op for anything but a local usb_serial-bound serial device.
        """
        try:
            from openavc.transport.serial_transport import resolve_usb_binding

            driver_transport = driver.DRIVER_INFO.get("transport", "tcp")
            refreshed = resolve_usb_binding(driver.config, driver_transport)
            if refreshed is not driver.config:
                log.info(
                    f"[{device_id}] Serial adapter moved: port "
                    f"{driver.config.get('port')!r} -> {refreshed.get('port')!r}"
                )
                driver.config = refreshed
        except Exception:
            log.debug(
                f"[{device_id}] USB serial re-resolution failed", exc_info=True
            )
