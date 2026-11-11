"""A device paused on purpose: held disconnected for somebody who needs its
port, the part of DeviceManager behind ``pause_device`` and ``resume_device``.

Two callers pause a device: the Driver Builder's Live Test (through POST
/devices/{id}/pause), which opens its own session to a device that takes one
connection at a time, and a device audit. While paused the device is on the
intentional-disconnect list, so nothing reconnects it behind the holder's back,
and ``device.<id>.paused`` and ``device.<id>.paused_for`` (who holds it) say so
on the device page.

The rule worth knowing before changing it: a pause belongs to whoever took it,
and ends only when they resume it, a Reconnect overrides it, or its backstop
runs out (``PAUSE_TTL``, renewed while the holder stays open, so a closed tab
cannot leave a production device offline for good). Removing the device and
adding it again (an edit, a driver reload) is none of those, so the pause is
carried across with the time its backstop had left (``_pause_to_carry``, then
``_hold_carried_pause``).

Mixed into DeviceManager (core/device_manager.py), which re-exports the
constants. Logs under the device manager's name, which is how the log view
files a line as the device's.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from openavc.drivers.base import ConnectEndedError
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore

log = get_logger("openavc.core.device_manager")


# Backstop for a test-panel pause whose owner never resumes it (tab closed or
# crashed, request lost). The panel refreshes the pause while it stays open,
# so expiry only fires for genuinely abandoned pauses — without it a paused
# production device stays offline indefinitely with auto-reconnect suppressed.
PAUSE_TTL = 600.0

# Who holds a pause, published as ``device.<id>.paused_for`` so the device page
# can name it: the Driver Builder's Live Test (through POST /devices/{id}/pause)
# or a device audit. Set by the first pause and kept until the device resumes.
PAUSED_FOR_LIVE_TEST = "live_test"
PAUSED_FOR_AUDIT = "audit"


class DevicePauseMixin:
    """Pausing and resuming a device, the backstop that ends an abandoned
    pause, and the pause a device keeps across being re-added.

    Its state is set up by ``_init_pause``. From its host (DeviceManager) it
    reads ``state`` and ``_intentional_disconnect``, and asks ``_live_driver``,
    ``_connect_driver``, ``_cancel_reconnect``, ``_start_reconnect``,
    ``_paused_for_refused_login``, ``_set_offline_reason``,
    ``_clear_offline_reason``, ``_failure_text`` and ``_apply_pending_settings``.
    """

    state: StateStore
    _intentional_disconnect: set[str]

    def _init_pause(self) -> None:
        self._pause_expiry_tasks: dict[str, asyncio.Task] = {}  # pause TTL backstops
        # When each armed pause expires (monotonic). Kept beside the task
        # because a task can't be asked how much of its sleep is left, and a
        # pause carried across an instance replacement has to carry the time
        # it had remaining rather than restart its term.
        self._pause_deadlines: dict[str, float] = {}

    async def pause_device(
        self, device_id: str, ttl: float | None = None, purpose: str = PAUSED_FOR_LIVE_TEST,
    ) -> None:
        """Cleanly disconnect a device and suppress auto-reconnect.

        Used by the driver test panel before it opens a competing TCP session
        against the same host:port on single-session devices, and by a device
        audit. The device stays paused until ``resume_device`` is called — or
        until ``ttl`` seconds pass without a re-pause (the holder keeps the
        pause alive while it runs), at which point the device auto-resumes so a
        closed/crashed tab can't strand it offline forever.
        ``device.<id>.paused`` is set so the UI can surface the state, and
        ``device.<id>.paused_for`` names the holder (``purpose``). Re-pausing an
        already-paused device just resets the TTL; the first holder's name stays.
        """
        driver = self._live_driver(device_id)
        await self._cancel_reconnect(device_id)
        # Add to intentional_disconnect BEFORE disconnect so the disconnected
        # event handler doesn't kick off a reconnect_loop.
        self._intentional_disconnect.add(device_id)
        try:
            await driver.disconnect()
        except Exception as e:
            log.warning(f"pause_device: disconnect raised for {device_id}: {e}")
        if not self.state.get(f"device.{device_id}.paused_for"):
            self.state.set(f"device.{device_id}.paused_for", purpose, source="device_manager")
        self.state.set(f"device.{device_id}.paused", True, source="device_manager")
        self.state.set(f"device.{device_id}.connected", False, source="device_manager")
        self._schedule_pause_expiry(device_id, PAUSE_TTL if ttl is None else ttl)
        log.info(f"Paused device: {device_id}")

    def _hold_carried_pause(
        self, device_id: str, seconds_left: float, held_for: str | None,
    ) -> None:
        """Re-arm the pause a re-added device carried (``_pause_to_carry``): it
        stays off the wire, whoever held the pause still holds the session it
        was taken to protect, and the backstop gets the time it had left."""
        self._intentional_disconnect.add(device_id)
        self.state.set(f"device.{device_id}.paused", True, source="device_manager")
        self.state.set(f"device.{device_id}.paused_for", held_for, source="device_manager")
        self.state.set(f"device.{device_id}.connected", False, source="device_manager")
        self._schedule_pause_expiry(device_id, seconds_left)
        log.info(
            f"Device '{device_id}' was re-added while paused — staying "
            f"paused ({seconds_left:.0f}s left on the backstop)"
        )

    def _schedule_pause_expiry(self, device_id: str, ttl: float) -> None:
        """(Re)arm the auto-resume backstop for a paused device."""
        self._cancel_pause_expiry(device_id)
        task = asyncio.create_task(self._pause_expiry(device_id, ttl))
        self._pause_expiry_tasks[device_id] = task
        self._pause_deadlines[device_id] = time.monotonic() + ttl

    def _cancel_pause_expiry(self, device_id: str) -> None:
        self._pause_deadlines.pop(device_id, None)
        task = self._pause_expiry_tasks.pop(device_id, None)
        if task is not None:
            task.cancel()

    def _cancel_all_pause_expiries(self) -> None:
        """Cancel every backstop, so none fires a resume mid-shutdown."""
        for device_id in list(self._pause_expiry_tasks.keys()):
            self._cancel_pause_expiry(device_id)

    def is_paused(self, device_id: str) -> bool:
        """True while this device is held disconnected by a pause.

        One reader for the flag, so a caller deciding whether it may reconnect
        a device asks the same question the pause itself answers.
        """
        return bool(self.state.get(f"device.{device_id}.paused"))

    def _pause_to_carry(self, device_id: str) -> tuple[float | None, str | None]:
        """The pause a device keeps across being removed and added again (an
        edit, a driver reload): the seconds its backstop has left, and who
        holds it. Read before the removal, which deletes both.

        The pause belongs to whoever took it, still holding the competing
        session (the Driver Builder's Live Test, an audit), and ends when they
        release it or its backstop runs out. An edit or a driver reload is
        neither, so reconnecting there would put a production device back on a
        port under test without anyone asking. A driver reload is the likelier
        of the two: saving the driver from the Builder is what the Live Test's
        own user does next.
        """
        return self._pause_remaining(device_id), self.state.get(f"device.{device_id}.paused_for")

    def _end_pause(self, device_id: str) -> None:
        """The pause is over: its backstop cancelled and both flags cleared."""
        self._cancel_pause_expiry(device_id)
        self.state.set(f"device.{device_id}.paused", False, source="device_manager")
        self.state.set(f"device.{device_id}.paused_for", None, source="device_manager")

    def _pause_remaining(self, device_id: str) -> float | None:
        """Seconds left on this device's pause backstop, or None if unpaused.

        Never negative: a deadline already past means the backstop is mid-fire,
        and a carried pause of zero resumes immediately — which is what would
        have happened anyway.
        """
        deadline = self._pause_deadlines.get(device_id)
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    async def _pause_expiry(self, device_id: str, ttl: float) -> None:
        try:
            await asyncio.sleep(ttl)
            # Drop our own registration BEFORE resuming, so resume_device's
            # _cancel_pause_expiry doesn't cancel the very task running it.
            self._pause_expiry_tasks.pop(device_id, None)
            self._pause_deadlines.pop(device_id, None)
            log.warning(
                f"[{device_id}] Pause not renewed within {ttl:.0f} s; reconnecting"
            )
            await self.resume_device(device_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # resume_device handles connect failures itself; this guards the
            # device-removed race (ValueError) and anything unexpected.
            log.warning(f"[{device_id}] Pause-expiry auto-resume failed: {e}")
        finally:
            # current_task() raises if the loop is already gone (cancellation
            # during shutdown/teardown) — nothing to clean up in that case.
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if current is not None and self._pause_expiry_tasks.get(device_id) is current:
                self._pause_expiry_tasks.pop(device_id, None)
                self._pause_deadlines.pop(device_id, None)

    async def resume_device(self, device_id: str) -> None:
        """Resume a paused device — clear the pause flag and reconnect.

        Idempotent: resuming a device that isn't paused just runs reconnect.
        On connect failure the normal auto-reconnect loop takes over, unless
        the device refused the login: then it waits for a person.
        """
        driver = self._live_driver(device_id)
        self._intentional_disconnect.discard(device_id)
        self._end_pause(device_id)
        try:
            await self._connect_driver(device_id, driver)
            log.info(f"Resumed device: {device_id}")
            # Mirror the reconnect-loop success path: drop the stale offline
            # reason and flush anything queued while the device was away.
            self._clear_offline_reason(device_id)
            await self._apply_pending_settings(device_id)
        except ConnectEndedError:
            pass  # ended by a Reconnect, a pause or a removal meanwhile
        except Exception as e:
            self.state.set(f"device.{device_id}.connected", False, source="device_manager")
            code = self._set_offline_reason(device_id, driver, exc=e)
            log.warning(
                f"resume_device: connect failed for {device_id}: {self._failure_text(device_id, e)}"
            )
            # The resume was this login's one try, as a first connect and
            # Reconnect count theirs: a refused one waits for a person.
            if not self._paused_for_refused_login(device_id, code):
                self._start_reconnect(device_id)
